"""Decodes headers and bodies itself: real mail mislabels charsets all the time (GB2312 that is
really GBK, unencoded 8-bit headers…)."""

import email
import email.errors
import email.header
import email.utils
import logging
import mimetypes
import re
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from email.message import Message
from html.parser import HTMLParser

import nh3

log = logging.getLogger(__name__)

# Declared charset -> the superset codec that decodes what mailers actually send under that name.
_CHARSET_SUPERSETS = {
    "us-ascii": "utf-8",
    "ascii": "utf-8",
    "iso-8859-1": "cp1252",
    "latin1": "cp1252",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "cp936": "gb18030",
    "euc-cn": "gb18030",
    "big5": "big5hkscs",
    "x-big5": "big5hkscs",
    "cp950": "big5hkscs",
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "sjis": "cp932",
    "x-sjis": "cp932",
    "windows-31j": "cp932",
    "euc-kr": "cp949",
    "ks_c_5601-1987": "cp949",
}

_CHARSET_LANGS = {
    **dict.fromkeys(("gb2312", "gbk", "x-gbk", "gb18030", "cp936", "euc-cn"), "zh-Hans"),
    **dict.fromkeys(("hz-gb-2312",), "zh-Hans"),
    **dict.fromkeys(("big5", "x-big5", "big5-hkscs", "cp950"), "zh-Hant"),
    **dict.fromkeys(("iso-2022-jp", "shift_jis", "shift-jis", "sjis", "x-sjis"), "ja"),
    **dict.fromkeys(("windows-31j", "cp932", "euc-jp"), "ja"),
    **dict.fromkeys(("euc-kr", "ks_c_5601-1987", "iso-2022-kr", "cp949"), "ko"),
}

_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")


def decode_bytes(data: bytes, charset: str | None) -> str:
    name = (charset or "").strip().strip("\"'").lower()
    attempts = [(data, _CHARSET_SUPERSETS.get(name, name)), (data, name)]
    if name == "iso-2022-jp":
        # Windows mailers put NEC extras (①…) in ISO-2022-JP. JIS X 0213 (ESC $ ( Q) is a
        # superset of JIS X 0208 (ESC $ B) that holds them at the same code points.
        attempts.insert(0, (data.replace(b"\x1b$B", b"\x1b$(Q"), "iso2022_jp_2004"))
        attempts.insert(1, (data, "iso2022_jp_ext"))  # half-width katakana
    if not name.startswith("iso-2022"):  # 7-bit escapes would "decode" as UTF-8
        attempts.append((data, "utf-8"))
    for errors in ("strict", "replace"):
        for raw, codec in attempts:
            try:
                return raw.decode(codec, errors) if codec else raw.decode("utf-8", errors)
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("utf-8", errors="replace")


def _unfold(value: str) -> str:
    return re.sub(r"\r?\n(?=[ \t])", "", value)


def decode_header(value: str | None, fallback_charset: str | None = None) -> str:
    if not value:
        return ""
    value = _unfold(value)
    try:
        raw = value.encode("ascii", "surrogateescape")  # the parser keeps 8-bit bytes as surrogates
    except UnicodeEncodeError:
        return value.strip()  # already decoded, e.g. an RFC 2231 filename
    if not raw.isascii():
        return decode_bytes(raw, fallback_charset).strip()
    try:
        parts = email.header.decode_header(value)
    except email.errors.HeaderParseError:
        return value.strip()
    return "".join(
        decode_bytes(text, charset) if isinstance(text, bytes) else text for text, charset in parts
    ).strip()


@dataclass
class ParsedAttachment:
    part_index: int
    filename: str
    mime_type: str
    size: int
    content_id: str | None
    inline: bool


@dataclass
class ParsedMessage:
    subject: str = ""
    from_name: str = ""
    from_email: str = ""
    to: list[dict[str, str]] = field(default_factory=list)
    cc: list[dict[str, str]] = field(default_factory=list)
    bcc: list[dict[str, str]] = field(default_factory=list)
    reply_to: list[dict[str, str]] = field(default_factory=list)
    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    list_id: str = ""
    body_text: str = ""
    body_html: str | None = None
    lang: str | None = None
    attachments: list[ParsedAttachment] = field(default_factory=list)


def _leaves(part: Message) -> Iterator[Message]:
    """Attached messages (message/rfc822) count as one leaf."""
    payload = part.get_payload()
    if part.get_content_maintype() == "multipart" and isinstance(payload, list):
        for sub in payload:
            if isinstance(sub, Message):
                yield from _leaves(sub)
    else:
        yield part


def _leaf_bytes(part: Message) -> bytes:
    payload = part.get_payload()
    if isinstance(payload, list):  # message/rfc822: the attached message itself
        return b"".join(p.as_bytes() for p in payload if isinstance(p, Message))
    data = part.get_payload(decode=True)
    return data if isinstance(data, bytes) else b""


def _raw_header(msg: Message, name: str) -> str | None:
    # raw_items() skips compat32's Header wrapping, so 8-bit values stay recoverable.
    for key, value in msg.raw_items():
        if key.lower() == name.lower():
            return value
    return None


def _addresses(msg: Message, name: str, charset: str | None) -> list[dict[str, str]]:
    raw = _raw_header(msg, name)
    if not raw:
        return []
    return [
        {"name": decode_header(display, charset), "email": addr.strip()}
        for display, addr in email.utils.getaddresses([_unfold(raw)])
        if addr
    ]


def _is_body(part: Message) -> bool:
    return (
        part.get_content_type() in ("text/plain", "text/html")
        and part.get_content_disposition() != "attachment"
        and not part.get_filename()
    )


def _filename(part: Message, index: int, charset: str | None) -> str:
    name = decode_header(part.get_filename(), charset)
    if name:
        return name.replace("/", "_").replace("\\", "_")
    if part.get_content_type() == "message/rfc822":
        return "message.eml"
    return f"attachment-{index}{mimetypes.guess_extension(part.get_content_type()) or ''}"


def parse(raw: bytes) -> ParsedMessage:
    try:
        return _parse(raw)
    except Exception:
        # The stdlib parser is lenient, so this is a last resort: keep the message, lose the body.
        log.exception("could not parse a message; storing it without body")
        return ParsedMessage(subject="(could not read this message)")


def _parse(raw: bytes) -> ParsedMessage:
    msg = email.message_from_bytes(raw)
    leaves = list(_leaves(msg))
    # The charset of the body is the best guess for unencoded 8-bit headers.
    charsets = [c for p in leaves if (c := p.get_content_charset())]
    charset = charsets[0] if charsets else None

    text = html_body = None
    text_charset = html_charset = None
    attachments = []
    for index, part in enumerate(leaves):
        if _is_body(part):
            content = decode_bytes(_leaf_bytes(part), part.get_content_charset())
            if part.get_content_type() == "text/html" and html_body is None:
                html_body, html_charset = content, part.get_content_charset()
            elif part.get_content_type() == "text/plain" and text is None:
                text, text_charset = content, part.get_content_charset()
            continue
        content_id = part.get("Content-ID")
        content_id = content_id.strip().strip("<>") if content_id else None
        attachments.append(
            ParsedAttachment(
                part_index=index,
                filename=_filename(part, index, charset),
                mime_type=part.get_content_type(),
                size=len(_leaf_bytes(part)),
                content_id=content_id,
                inline=content_id is not None and part.get_content_disposition() != "attachment",
            )
        )

    # A multipart message with a broken boundary arrives as one text blob: show it as text.
    if text is None and html_body is None and msg.get_content_maintype() == "multipart":
        if not isinstance(msg.get_payload(), list):
            text = decode_bytes(_leaf_bytes(msg), charset)

    sender = _addresses(msg, "From", charset)
    subject = decode_header(_raw_header(msg, "Subject"), charset)
    body_text = text if text is not None else html_to_text(html_body or "")
    return ParsedMessage(
        subject=subject,
        from_name=sender[0]["name"] if sender else "",
        from_email=sender[0]["email"] if sender else "",
        to=_addresses(msg, "To", charset),
        cc=_addresses(msg, "Cc", charset),
        bcc=_addresses(msg, "Bcc", charset),
        reply_to=_addresses(msg, "Reply-To", charset),
        message_id=_header_or_none(msg, "Message-ID"),
        in_reply_to=_header_or_none(msg, "In-Reply-To"),
        references=_header_or_none(msg, "References"),
        list_id=" ".join(decode_header(_raw_header(msg, "List-Id"), charset).split()),
        body_text=body_text.strip(),
        body_html=sanitize_html(html_body) if html_body is not None else None,
        lang=detect_lang(
            f"{subject}\n{body_text[:2000]}",
            _raw_header(msg, "Content-Language"),
            html_charset or text_charset or charset,
        ),
        attachments=attachments,
    )


def _header_or_none(msg: Message, name: str) -> str | None:
    value = _raw_header(msg, name)
    if not value:
        return None
    return " ".join(_unfold(value).split()) or None


def attachment_content(raw: bytes, part_index: int) -> bytes | None:
    """`part_index` as parse() numbered it."""
    leaves = list(_leaves(email.message_from_bytes(raw)))
    return _leaf_bytes(leaves[part_index]) if 0 <= part_index < len(leaves) else None


def detect_lang(text: str, content_language: str | None, charset: str | None) -> str | None:
    """A BCP 47 hint so the UI picks Chinese or Japanese fonts (`:lang()`)."""
    declared = (content_language or "").split(",")[0].strip() or None
    by_charset = _CHARSET_LANGS.get((charset or "").lower())
    if _KANA.search(text):
        return "ja"
    if _HAN.search(text):
        for hint in (by_charset, declared):
            if hint and hint.lower().startswith(("zh", "ja")):
                return hint
        return "zh"
    if _HANGUL.search(text):
        return "ko"
    return declared


# Email HTML relies on inline styles, <style> blocks and old presentational attributes.
_TAGS = nh3.ALLOWED_TAGS | {"font", "style", "tfoot"}
_ATTRIBUTES = deepcopy(nh3.ALLOWED_ATTRIBUTES)
_ATTRIBUTES["*"] = {
    "style",
    "class",
    "id",
    "dir",
    "lang",
    "title",
    "align",
    "valign",
    "width",
    "height",
    "bgcolor",
    "background",
    "border",
    "cellpadding",
    "cellspacing",
    "color",
}
_ATTRIBUTES["font"] = {"face", "size", "color"}
# cid: keeps inline images resolvable through the attachment proxy.
_URL_SCHEMES = nh3.ALLOWED_URL_SCHEMES | {"cid"}


def sanitize_html(body: str) -> str:
    return nh3.clean(
        body,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        url_schemes=_URL_SCHEMES,
        clean_content_tags={"script", "title"},
    )


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"style", "script", "head", "title"})
    _BREAK = frozenset({"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self.skipping += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def html_to_text(body: str) -> str:
    parser = _TextExtractor()
    parser.feed(body)
    parser.close()
    text = "".join(parser.parts)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
