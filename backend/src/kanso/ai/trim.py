"""Cut an email body down to what its writer wrote, before any of it is sent to Claude: no quoted
replies, no signature, whitespace collapsed, and a length cap."""

import re

# A line that starts the quoted original in a reply ("On Mon, … wrote:", Outlook's separator above
# its header block, and the Chinese and Japanese Gmail forms). Everything from it on is dropped.
# Forwarded messages are kept: there the forwarded text is the content.
_QUOTE_START = re.compile(
    r"^(On .{0,300}wrote:"
    r"|-{2,} ?Original Message ?-{2,}"
    r"|_{10,}"
    r"|.{0,200}(写道|寫道)[:：]"
    r"|.{0,200}(が書きました|のメッセージ)[:：]?)\s*$",
    re.IGNORECASE,
)
# The start of a signature: the standard "-- " delimiter (or a bare "--") and mobile footers.
_SIGNATURE = re.compile(r"^(--\s*|Sent from my .{1,40}|Get Outlook for .{1,40})$", re.IGNORECASE)


def trim(text: str, limit: int) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _QUOTE_START.match(stripped) or _SIGNATURE.match(line.rstrip()):
            break
        if stripped.startswith(">"):
            continue
        lines.append(stripped)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    body = re.sub(r"[ \t]+", " ", body)
    if len(body) <= limit:
        return body
    cut = body[:limit]
    # End at a word boundary when there is one nearby (CJK text has no spaces: cut as is).
    space = cut.rfind(" ", limit - 40)
    return (cut[:space] if space > 0 else cut) + " …"
