"""Postgres doesn't split CJK into words, so CJK runs are indexed as overlapping bigrams, like
Lucene's CJK analyzer: "明天开会" -> "明天 天开 开会 会". A CJK query is the phrase of its bigrams.

Queries use Gmail's syntax: words, "phrases", operators, -negation, OR (which binds tighter than
the implied AND) and parentheses. Search returns threads. Conditions on messages that are ANDed
or ORed together must hold for one message, as in Gmail; a negated condition drops every thread
with a message that matches it; label:, is: and in: look at the thread.

from:, to:, subject: and the like match the start of a word, as in Gmail; CJK matches anywhere.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Text,
    and_,
    any_,
    cast,
    exists,
    func,
    literal_column,
    or_,
    select,
    true,
)
from sqlalchemy.orm import QueryableAttribute

from kanso.db.models import Attachment, Label, Message, Thread

# Kana, Han (with extension A and the compatibility block) and Hangul syllables.
_CJK_RUN = re.compile(
    r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿가-힯"
    r"\U00020000-\U0002fa1f]+"
)
# A tsvector holds at most 1 MB; long bodies are indexed up to this many characters.
MAX_INDEXED_CHARS = 200_000


def cjk_bigrams(text: str, query: bool = False) -> str:
    """Indexed text also ends each run with its last character, for one-character queries."""

    def split(match: re.Match[str]) -> str:
        run = match.group()
        grams = [run[i : i + 2] for i in range(len(run) - 1)]
        if not query or len(run) == 1:
            grams.append(run[-1])
        return f" {' '.join(grams)} "

    return _CJK_RUN.sub(split, text)


def index_text(*parts: str) -> str:
    return cjk_bigrams(" ".join(parts)[:MAX_INDEXED_CHARS])


_TOKEN = re.compile(
    r"\s*(?:(?P<close>\))|(?P<neg>-)?(?:(?P<open>\()"
    r'|(?:(?P<key>\w+):)?(?:"(?P<quoted>[^"]*)"?|(?P<bare>[^\s()"]+))))'
)


@dataclass
class _Leaf:
    condition: ColumnElement[bool]
    on_message: bool  # a condition on one message, else on the thread


@dataclass
class _And:
    children: list["_Node"]


@dataclass
class _Or:
    children: list["_Node"]


@dataclass
class _Not:
    child: "_Node"


_Node = _Leaf | _And | _Or | _Not


def _join(kind: type[_And] | type[_Or], nodes: list[_Node | None]) -> _Node | None:
    present = [n for n in nodes if n is not None]
    if len(present) < 2:
        return present[0] if present else None
    return kind(present)


def _date(value: str) -> datetime | None:
    try:
        day = date.fromisoformat(value.replace("/", "-"))
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


_AGE = re.compile(r"(\d{1,5})([dwmy])", re.IGNORECASE)
_SIZE = re.compile(r"(\d+(?:\.\d+)?)([kmg]?)b?", re.IGNORECASE)


def _age(value: str) -> ColumnElement[datetime] | None:
    """newer_than:2d, 3w, 6m or 1y, as an instant before now."""
    match = _AGE.fullmatch(value)
    if not match:
        return None
    n, unit = int(match.group(1)), match.group(2).lower()
    years, months, weeks, days = (n * (unit == u) for u in "ymwd")
    return func.now() - func.make_interval(years, months, weeks, days)


def _size(value: str) -> int | None:
    match = _SIZE.fullmatch(value)
    if not match:
        return None
    return int(float(match.group(1)) * 1024 ** " kmg".index(match.group(2).lower() or " "))


def _tsquery(term: str) -> ColumnElement[object]:
    words = term.split()
    if len(words) == 1 and not _CJK_RUN.search(term):
        return func.to_tsquery("simple", "'" + term.replace("\\", "").replace("'", "''") + "':*")
    if len(term) == 1:  # a single CJK character: every bigram starting with it
        return func.to_tsquery("simple", f"'{term}':*")
    return func.phraseto_tsquery("simple", cjk_bigrams(term, query=True))


_Column = ColumnElement[Any] | QueryableAttribute[Any]


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _matches(column: _Column, value: str) -> ColumnElement[bool]:
    if _CJK_RUN.search(value):
        return column.ilike(_like(value), escape="\\")
    # \m is the start of a word; a backslash makes any other character literal.
    pattern = "".join(c if c.isalnum() else "\\" + c for c in value)
    return column.op("~*")(("\\m" if value[0].isalnum() else "") + pattern)


def _addresses(column: _Column) -> ColumnElement[str]:
    """The names and addresses in a JSONB address list, as text."""
    return cast(func.jsonb_path_query_array(column, literal_column("'$[*].*'::jsonpath")), Text)


# As indexed by messages_recipients_trgm_idx.
RECIPIENTS = _addresses(Message.to_addrs.op("||")(Message.cc_addrs))
SENDER = Message.from_name + " " + Message.from_email


def _label(name: str) -> ColumnElement[bool]:
    # Gmail writes spaces and nesting slashes in label: searches as dashes.
    dashed = func.replace(func.replace(func.lower(Label.name), " ", "-"), "/", "-")
    return exists(
        select(1).where(
            Label.id == any_(Thread.label_ids),
            (func.lower(Label.name) == name.lower()) | (dashed == name.lower()),
        )
    )


# is: and in: take the same values.
_STATES = {
    "unread": lambda: Thread.unread.is_(True),
    "read": lambda: Thread.unread.is_(False),
    "starred": lambda: Thread.starred.is_(True),
    "important": lambda: Thread.important.is_(True),
    "inbox": lambda: Thread.in_inbox.is_(True),
    "sent": lambda: _label("SENT"),
    "draft": lambda: _label("DRAFT"),
    "drafts": lambda: _label("DRAFT"),
    "trash": lambda: Thread.in_trash.is_(True),
    "spam": lambda: Thread.in_spam.is_(True),
    "anywhere": true,  # only lets Trash and Spam in
}
_TRASH_OR_SPAM = {"trash", "spam", "anywhere"}


def _message(key: str, value: str) -> ColumnElement[bool] | None:
    if key == "from":
        return _matches(SENDER, value)
    if key == "to":
        return _matches(RECIPIENTS, value)
    if key == "cc":
        return _matches(_addresses(Message.cc_addrs), value)
    if key == "bcc":
        return _matches(_addresses(Message.bcc_addrs), value)
    if key == "with":
        everyone = SENDER + " " + RECIPIENTS + " " + _addresses(Message.bcc_addrs)
        return _matches(everyone, value)
    if key == "subject":
        return _matches(Message.subject, value)
    if key == "list":
        return _matches(Message.list_id, value)
    if key == "filename":
        name = Attachment.filename + " " + Attachment.mime_type
        return exists(
            select(1).where(
                Attachment.message_id == Message.id, ~Attachment.inline, _matches(name, value)
            )
        )
    if key == "has" and value.lower() in ("attachment", "attachments"):
        return Message.has_attachments.is_(True)
    if key in ("before", "older", "after", "newer") and (day := _date(value)) is not None:
        before = key in ("before", "older")
        return Message.internal_date < day if before else Message.internal_date >= day
    if key in ("older_than", "newer_than") and (instant := _age(value)) is not None:
        older = key == "older_than"
        return Message.internal_date < instant if older else Message.internal_date >= instant
    if key in ("larger", "smaller") and (size := _size(value)) is not None:
        return Message.size > size if key == "larger" else Message.size < size
    return None


class _Parser:
    def __init__(self, q: str) -> None:
        self.tokens = list(_TOKEN.finditer(q))
        self.pos = 0
        self.negated = False
        self.trash_or_spam = False  # a positive term asks for Trash or Spam

    def _keyword(self, word: str) -> bool:
        if self.pos >= len(self.tokens):
            return False
        token = self.tokens[self.pos]
        return token["bare"] == word and not token["key"] and not token["neg"]

    def all(self, closing: bool = False) -> _Node | None:
        nodes: list[_Node | None] = []
        while self.pos < len(self.tokens):
            if self.tokens[self.pos]["close"]:
                self.pos += 1
                if closing:
                    break
                continue  # a stray ")"
            if self._keyword("AND"):
                self.pos += 1
                continue
            nodes.append(self.any())
        return _join(_And, nodes)

    def any(self) -> _Node | None:
        nodes = [self.unary()]
        while self._keyword("OR"):
            self.pos += 1
            if self.pos == len(self.tokens) or self.tokens[self.pos]["close"]:
                break
            nodes.append(self.unary())
        return _join(_Or, nodes)

    def unary(self) -> _Node | None:
        token = self.tokens[self.pos]
        self.pos += 1
        negated = bool(token["neg"])
        self.negated ^= negated
        node = self.all(closing=True) if token["open"] else self.leaf(token)
        self.negated ^= negated
        return _Not(node) if negated and node is not None else node

    def leaf(self, token: re.Match[str]) -> _Node | None:
        key, quoted = token["key"], token["quoted"]
        value = quoted if quoted is not None else token["bare"]
        if key and value:
            lowered = key.lower()
            if lowered in ("is", "in") and value.lower() in _STATES:
                state = value.lower()
                self.trash_or_spam |= not self.negated and state in _TRASH_OR_SPAM
                return _Leaf(_STATES[state](), on_message=False)
            if lowered == "label":
                self.trash_or_spam |= not self.negated and value.lower() in ("trash", "spam")
                return _Leaf(_label(value), on_message=False)
            if (condition := _message(lowered, value)) is not None:
                return _Leaf(condition, on_message=True)
        # Not an operator after all: search it as text.
        text = (f"{key}:" if key else "") + (value or "")
        if not re.search(r"\w", text):  # punctuation alone matches nothing
            return None
        return _Leaf(Message.search.op("@@")(_tsquery(text.strip())), on_message=True)


def _on_message(node: _Node) -> bool:
    if isinstance(node, _Leaf):
        return node.on_message
    if isinstance(node, _Not):
        return False
    return all(_on_message(child) for child in node.children)


def _message_condition(node: _Node) -> ColumnElement[bool]:
    """For a node that is _on_message."""
    if isinstance(node, _Leaf):
        return node.condition
    assert isinstance(node, _And | _Or)
    combine = and_ if isinstance(node, _And) else or_
    return combine(*(_message_condition(child) for child in node.children))


def _thread_condition(node: _Node) -> ColumnElement[bool]:
    if _on_message(node):
        return Thread.id.in_(select(Message.thread_id).where(_message_condition(node)))
    if isinstance(node, _Leaf):
        return node.condition
    if isinstance(node, _Not):
        return ~_thread_condition(node.child)
    if isinstance(node, _Or):
        return or_(*(_thread_condition(child) for child in node.children))
    together = [child for child in node.children if _on_message(child)]
    apart = [child for child in node.children if not _on_message(child)]
    conditions = [_thread_condition(child) for child in apart]
    if together:
        conditions.append(_thread_condition(together[0] if len(together) == 1 else _And(together)))
    return and_(*conditions)


def conditions(q: str) -> tuple[list[ColumnElement[bool]], bool]:
    """WHERE conditions on threads for a search, and whether it asks for Trash or Spam."""
    parser = _Parser(q)
    node = parser.all()
    return ([] if node is None else [_thread_condition(node)]), parser.trash_or_spam
