import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import nh3

from kanso.db.models import Message as Original
from kanso.gmail import mime

# {"name": ..., "email": ...}, as the mirror stores them.
Addr = dict[str, Any]

# What the composer writes, Markdown shortcuts included. Blockquote also stays for drafts written
# in Gmail, which keep their quote in the body.
BODY_TAGS = {
    *("p", "div", "br", "b", "strong", "i", "em", "a", "ul", "ol", "li", "blockquote"),
    *("code", "pre", "h1", "h2", "h3", "img"),
}


def _body_urls(tag: str, attribute: str, value: str) -> str | None:
    """Images are the draft's own inline ones (cid:), never remote; links are never cid:."""
    is_cid = value.strip().lower().startswith("cid:")
    if tag == "img" and attribute == "src":
        return value if is_cid else None
    if tag == "a" and attribute == "href" and is_cid:
        return None
    return value


def sanitize_body(body: str) -> str:
    clean = nh3.clean(
        body,
        tags=BODY_TAGS,
        attributes={"a": {"href"}, "img": {"src", "alt"}},
        url_schemes={"http", "https", "mailto", "cid"},
        attribute_filter=_body_urls,
    )
    return re.sub(r"<img(?![^>]*\ssrc=)[^>]*>", "", clean)  # a remote image left without its src


# Subject prefixes, so a reply to "Re: x" stays "Re: x" (also German, Nordic, Dutch, Chinese).
_RE = re.compile(r"^\s*(re|aw|sv|antw|回复|回覆|答复)\s*[:：]", re.IGNORECASE)
_FWD = re.compile(r"^\s*(fwd?|wg|tr|转发|轉寄|轉發)\s*[:：]", re.IGNORECASE)


def reply_subject(subject: str) -> str:
    return subject if _RE.match(subject) else f"Re: {subject}".strip()


def forward_subject(subject: str) -> str:
    return subject if _FWD.match(subject) else f"Fwd: {subject}".strip()


def _unique(addresses: Iterable[Addr], skip: set[str]) -> list[Addr]:
    out = []
    for address in addresses:
        key = address["email"].lower()
        if key and key not in skip:
            skip.add(key)
            out.append({"name": address.get("name", ""), "email": address["email"]})
    return out


def reply_recipients(original: Original, mine: set[str], reply_all: bool) -> tuple[list, list]:
    """`mine` (the replying account, lowercase) is never copied in. Replying to your own
    message writes to its recipients again."""
    from_me = original.from_email.lower() in mine
    if from_me:
        to = original.to_addrs
        cc = original.cc_addrs if reply_all else []
    else:
        to = original.reply_to or [{"name": original.from_name, "email": original.from_email}]
        cc = [*original.to_addrs, *original.cc_addrs] if reply_all else []
    to_out = _unique(to, set(mine)) or _unique(to, set())  # a note to yourself stays one
    cc_out = _unique(cc, set(mine) | {a["email"].lower() for a in to_out})
    return to_out, cc_out


def references(original: Original) -> str | None:
    chain = (original.references or original.in_reply_to or "").split()
    if original.message_id_header:
        chain.append(original.message_id_header)
    return " ".join(chain[-20:]) or None


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def when(moment: datetime, tz: str | None) -> str:
    """Like Gmail's quote line: "Sun, Nov 1, 2026 at 9:41 AM"."""
    d = moment.astimezone(_zone(tz))
    hour = d.hour % 12 or 12
    return f"{d:%a}, {d:%b} {d.day}, {d.year} at {hour}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def _who(name: str, email: str) -> str:
    return f"{name} <{email}>" if name and name != email else email


def _original_html(original: Original, keep_cid: bool) -> str:
    """No <style> that would restyle the whole reply, and in replies no cid: images (not attached
    there)."""
    if original.body_html is None:
        return html.escape(original.body_text).replace("\n", "<br>")

    def drop_cid(tag: str, attribute: str, value: str) -> str | None:
        if attribute == "src" and value.strip().lower().startswith("cid:") and not keep_cid:
            return None
        return value

    return nh3.clean(
        original.body_html,
        tags=mime._TAGS - {"style"},
        attributes=mime._ATTRIBUTES,
        url_schemes=mime._URL_SCHEMES,
        clean_content_tags={"script", "style", "title"},
        attribute_filter=drop_cid,
    )


def reply_quote(original: Original, tz: str | None) -> tuple[str, str]:
    """Gmail's markup, so Gmail collapses the quote."""
    line = f"On {when(original.internal_date, tz)} {_who(original.from_name, original.from_email)}"
    quote_html = (
        f'<div class="gmail_quote"><div dir="ltr" class="gmail_attr">{html.escape(line)} wrote:'
        '<br></div><blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;'
        'border-left:1px solid rgb(204,204,204);padding-left:1ex">'
        f"{_original_html(original, keep_cid=False)}</blockquote></div>"
    )
    quoted = "\n".join(f"> {x}" if x else ">" for x in original.body_text.splitlines())
    return quote_html, f"{line} wrote:\n{quoted}"


def forward_quote(original: Original, tz: str | None) -> tuple[str, str]:
    def people(addresses: list[Addr]) -> str:
        return ", ".join(_who(a.get("name", ""), a["email"]) for a in addresses)

    fields = [
        ("From", _who(original.from_name, original.from_email)),
        ("Date", when(original.internal_date, tz)),
        ("Subject", original.subject),
        ("To", people(original.to_addrs)),
    ]
    if original.cc_addrs:
        fields.append(("Cc", people(original.cc_addrs)))
    head = "---------- Forwarded message ---------"
    header_html = "".join(f"{k}: {html.escape(v)}<br>" for k, v in fields)
    quote_html = (
        f'<div class="gmail_quote"><div dir="ltr" class="gmail_attr">{head}<br>{header_html}'
        f"</div><br>{_original_html(original, keep_cid=True)}</div>"
    )
    header_text = "\n".join(f"{k}: {v}" for k, v in fields)
    return quote_html, f"{head}\n{header_text}\n\n{original.body_text}"


class _Text(HTMLParser):
    """Plain text with "- " and "1. " list items, links as "text <url>" and code in backticks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.lists: list[int | None] = []  # None: bulleted; n: numbered, n items so far
        self.link: tuple[str, int] | None = None  # (href, where its text starts)
        self.pre = 0  # inside <pre>: whitespace stays

    def _break(self) -> None:
        if self.out and not self.out[-1].endswith("\n"):
            self.out.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.out.append("\n")
        elif tag in ("p", "div", "blockquote", "pre", "h1", "h2", "h3"):
            self._break()
            if tag == "pre":
                self.pre += 1
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag in ("ul", "ol"):
            self._break()
            self.lists.append(0 if tag == "ol" else None)
        elif tag == "li":
            self._break()
            count = self.lists[-1] if self.lists else None
            indent = "   " * max(len(self.lists) - 1, 0)
            if count is None:
                self.out.append(f"{indent}- ")
            else:
                self.lists[-1] = count + 1
                self.out.append(f"{indent}{count + 1}. ")
        elif tag == "a":
            href = dict(attrs).get("href")
            self.link = (href, len(self.out)) if href else None

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self._break()
            self.out.append("\n")
        elif tag in ("div", "li", "blockquote", "pre", "h1", "h2", "h3"):
            self._break()
            if tag == "pre":
                self.pre = max(self.pre - 1, 0)
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._break()
        elif tag == "a" and self.link:
            href, start = self.link
            text = "".join(self.out[start:]).strip()
            if href not in (text, f"mailto:{text}"):
                self.out.append(f" <{href.removeprefix('mailto:')}>")
            self.link = None

    def handle_data(self, data: str) -> None:
        if not self.pre:
            data = re.sub(r"[ \t\r\n]+", " ", data)
        self.out.append(data.replace("\xa0", " "))


def html_to_text(body: str) -> str:
    parser = _Text()
    parser.feed(body)
    parser.close()
    lines = [line.rstrip() for line in "".join(parser.out).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@dataclass
class Part:
    """With a content_id it is an inline image, related to the HTML part. A body_only one is left
    out when the HTML doesn't show it (an image inserted, then deleted)."""

    filename: str
    mime_type: str
    content: bytes
    content_id: str | None = None
    body_only: bool = False


def _addresses(addresses: list[Addr]) -> list[Address]:
    return [Address(display_name=a.get("name") or "", addr_spec=a["email"]) for a in addresses]


def _types(mime_type: str) -> tuple[str, str]:
    maintype, _, subtype = mime_type.lower().partition("/")
    # An attached message or a broken type goes out as plain bytes.
    if not subtype or maintype in ("message", "multipart"):
        return "application", "octet-stream"
    return maintype, subtype


def build_message(
    *,
    sender: Addr | None,
    to: list[Addr],
    cc: list[Addr],
    bcc: list[Addr],
    subject: str,
    body_html: str,
    quote_html: str | None,
    quote_text: str | None,
    message_id: str,
    in_reply_to: str | None,
    references: str | None,
    attachments: list[Part],
    date: datetime | None = None,
) -> bytes:
    """Without a sender Gmail fills in the account's own From. Bcc stays in: Gmail delivers to it
    and removes the header when sending."""
    msg = EmailMessage(policy=policy.default)
    if sender:
        msg["From"] = _addresses([sender])
    for name, addresses in (("To", to), ("Cc", cc), ("Bcc", bcc)):
        if addresses:
            msg[name] = _addresses(addresses)
    msg["Subject"] = subject
    msg["Date"] = format_datetime(date or datetime.now(UTC))
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg["MIME-Version"] = "1.0"

    text = html_to_text(body_html)
    if quote_text:
        text = f"{text}\n\n{quote_text}" if text else quote_text
    document = f'<div dir="ltr">{body_html}</div>'
    if quote_html:
        document += f"<br>{quote_html}"
    document = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{document}</body></html>'
    )
    # 7-bit safe on the wire: base64 for anything that isn't ASCII (compact for CJK).
    cte = None if (text + document).isascii() else "base64"
    msg.set_content(text + "\n", charset="utf-8", cte=cte)
    msg.add_alternative(document, subtype="html", charset="utf-8", cte=cte)
    html_part = msg.get_body(("html",))
    assert isinstance(html_part, EmailMessage)
    for part in attachments:
        if part.content_id and f"cid:{part.content_id}" in document:
            maintype, subtype = _types(part.mime_type)
            html_part.add_related(
                part.content,
                maintype,
                subtype,
                cid=f"<{part.content_id}>",
                filename=part.filename,
                disposition="inline",
            )
    for part in attachments:
        if not (part.content_id and f"cid:{part.content_id}" in document) and not part.body_only:
            maintype, subtype = _types(part.mime_type)
            msg.add_attachment(part.content, maintype, subtype, filename=part.filename)
    return msg.as_bytes()
