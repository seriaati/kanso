"""Full-text search over the mirror: the indexed text and the query language.

Postgres's parsers don't split Chinese or Japanese into words (a whole run of Han characters
becomes one token), so CJK runs are indexed as overlapping bigrams, as Lucene's CJK analyzer
does: "明天开会" -> "明天 天开 开会 会". A CJK query becomes the phrase of its bigrams, which finds
it anywhere inside a run. Everything else uses the `simple` configuration (no stemming, any
language), with prefix matching on each word.

Operators: from: to: label: is:unread is:starred has:attachment before: after: (dates as
YYYY/MM/DD or YYYY-MM-DD, in UTC), and "quoted phrases". from: and to: match the start of a word in
a name, or of an address or any of its parts, as in Gmail: from:ana finds Ana Lopes, not Mika
Tanaka; from:reyes finds daniel.reyes@example.com. A CJK name has no word breaks, so a CJK value
matches anywhere in it. Both use pg_trgm indexes (migrations 0003 and 0010).
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import (
    ColumnElement,
    Text,
    and_,
    any_,
    cast,
    exists,
    func,
    literal_column,
    select,
    true,
)

from kanso.db.models import Label, Message, Thread

# Kana, Han (with extension A and the compatibility block) and Hangul syllables.
_CJK_RUN = re.compile(
    r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿가-힯"
    r"\U00020000-\U0002fa1f]+"
)
# A tsvector holds at most 1 MB; long bodies are indexed up to this many characters.
MAX_INDEXED_CHARS = 200_000


def cjk_bigrams(text: str, query: bool = False) -> str:
    """Split each CJK run into bigrams. Indexed text also ends each run with its last character,
    so a one-character query (a prefix match) finds every character."""

    def split(match: re.Match[str]) -> str:
        run = match.group()
        grams = [run[i : i + 2] for i in range(len(run) - 1)]
        if not query or len(run) == 1:
            grams.append(run[-1])
        return f" {' '.join(grams)} "

    return _CJK_RUN.sub(split, text)


def index_text(*parts: str) -> str:
    return cjk_bigrams(" ".join(parts)[:MAX_INDEXED_CHARS])


OPERATORS = {"from", "to", "label", "is", "has", "before", "after"}
_TOKEN = re.compile(r'(?:(\w+):)?(?:"([^"]*)"?|(\S+))')


@dataclass
class Query:
    terms: list[str] = field(default_factory=list)  # words and "phrases"
    senders: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    unread: bool = False
    starred: bool = False
    has_attachment: bool = False
    before: datetime | None = None
    after: datetime | None = None

    @property
    def targets_trash_or_spam(self) -> bool:
        return any(name.lower() in ("trash", "spam") for name in self.labels)


def _date(value: str) -> datetime | None:
    try:
        day = date.fromisoformat(value.replace("/", "-"))
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def parse(q: str) -> Query:
    query = Query()
    for match in _TOKEN.finditer(q):
        key, quoted, bare = match.group(1), match.group(2), match.group(3)
        value = quoted if quoted is not None else bare
        if key is None or key.lower() not in OPERATORS:
            _add_term(query, match.group(0))
            continue
        key, lowered = key.lower(), value.lower()
        if key == "from" and value:
            query.senders.append(value)
        elif key == "to" and value:
            query.recipients.append(value)
        elif key == "label" and value:
            query.labels.append(value)
        elif key == "is" and lowered in ("unread", "starred"):
            setattr(query, lowered, True)
        elif key == "has" and lowered == "attachment":
            query.has_attachment = True
        elif key in ("before", "after") and (when := _date(value)):
            setattr(query, key, when)
        else:  # not an operator after all: search for the text
            _add_term(query, match.group(0))
    return query


def _add_term(query: Query, text: str) -> None:
    text = text.replace('"', "").strip()
    if re.search(r"\w", text):  # punctuation alone matches nothing
        query.terms.append(text)


def _tsquery(term: str) -> ColumnElement[object]:
    """One word or phrase as a tsquery: words match as prefixes, CJK as bigram phrases."""
    words = term.split()
    if len(words) == 1 and not _CJK_RUN.search(term):
        return func.to_tsquery("simple", "'" + term.replace("\\", "").replace("'", "''") + "':*")
    if len(term) == 1:  # a single CJK character: every bigram starting with it
        return func.to_tsquery("simple", f"'{term}':*")
    return func.phraseto_tsquery("simple", cjk_bigrams(term, query=True))


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _matches(column: ColumnElement[str], value: str) -> ColumnElement[bool]:
    """`value` at the start of a word in `column` (case-insensitive); anywhere for CJK."""
    if _CJK_RUN.search(value):
        return column.ilike(_like(value), escape="\\")
    # A Postgres regular expression: \m is the start of a word; a backslash makes any other
    # character literal.
    pattern = "".join(c if c.isalnum() else "\\" + c for c in value)
    return column.op("~*")(("\\m" if value[0].isalnum() else "") + pattern)


# The names and addresses of a message's To and Cc, as indexed (messages_recipients_trgm_idx).
RECIPIENTS = cast(
    func.jsonb_path_query_array(
        Message.to_addrs.op("||")(Message.cc_addrs), literal_column("'$[*].*'::jsonpath")
    ),
    Text,
)


def message_conditions(query: Query) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        Message.search.op("@@")(_tsquery(term)) for term in query.terms
    ]
    sender = Message.from_name + " " + Message.from_email
    conditions += [_matches(sender, v) for v in query.senders]
    conditions += [_matches(RECIPIENTS, v) for v in query.recipients]
    if query.has_attachment:
        conditions.append(Message.has_attachments.is_(True))
    if query.before:
        conditions.append(Message.internal_date < query.before)
    if query.after:
        conditions.append(Message.internal_date >= query.after)
    return conditions


def thread_conditions(query: Query) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    for name in query.labels:
        # Gmail writes spaces and nesting slashes in label: searches as dashes.
        dashed = func.replace(func.replace(func.lower(Label.name), " ", "-"), "/", "-")
        conditions.append(
            exists(
                select(1).where(
                    Label.id == any_(Thread.label_ids),
                    (func.lower(Label.name) == name.lower()) | (dashed == name.lower()),
                )
            )
        )
    if query.unread:
        conditions.append(Thread.unread.is_(True))
    if query.starred:
        conditions.append(Thread.starred.is_(True))
    return conditions


def conditions(q: str) -> tuple[list[ColumnElement[bool]], bool]:
    """WHERE conditions on threads for a search, and whether it asks for Trash or Spam."""
    query = parse(q)
    result = thread_conditions(query)
    if by_message := message_conditions(query):
        result.append(Thread.id.in_(select(Message.thread_id).where(and_(true(), *by_message))))
    return result, query.targets_trash_or_spam
