"""The assistant's tools. They work on kanso's local mirror only, never on Gmail directly.

- Read tools (search_threads, get_thread, list_labels) answer at once with lean JSON: capped
  result counts, short snippets, and bodies trimmed by ai/trim.py (no quoted replies or
  signatures) within a budget. Never recipients, attachments or drafts, and never anything from
  the demo mailbox (its threads don't exist as far as the tools are concerned).
- Change tools (archive, apply_label, star, mark_read) only *propose*: `propose` checks the input
  and returns the proposal the user sees with Confirm and Cancel. Nothing changes until the user
  confirms it in the browser (kanso.api.assistant), and then it goes through the same actions
  path and outbox as a click in the app. There is no tool that sends, drafts or deletes mail.
"""

import json
from datetime import datetime, tzinfo
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import summaries, trim
from kanso.db import search
from kanso.db.models import Account, Label, Thread

SEARCH_DEFAULT, SEARCH_MAX = 10, 20  # threads per search
SNIPPET_LIMIT = 160
BODY_LIMIT = 1500  # characters of each trimmed message body
THREAD_LIMIT = 10000  # characters of bodies per thread, newest messages first
MAX_TARGETS = 50  # threads one proposed change may touch

# Tool name -> the triage action it proposes (kanso.api.actions.ACTIONS).
CHANGES = {"archive": "done", "apply_label": "label", "star": "star", "mark_read": "read"}
READS = {"search_threads", "get_thread", "list_labels"}

_TARGETS = {
    "type": "array",
    "items": {"type": "integer"},
    "minItems": 1,
    "maxItems": MAX_TARGETS,
    "description": "Thread ids from search_threads or get_thread.",
}
_CONFIRMED = (
    " Nothing changes until the user confirms it in kanso; the result says whether they did. "
    "Only use it when the user asked for this change."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_threads",
        "description": (
            "Search the user's whole mailbox: every connected account, the full history, archived "
            "(done) mail included; Trash and Spam only with label:trash or label:spam. Returns the "
            'newest matching threads first. Words match as prefixes, "quoted phrases" as '
            "phrases, and Chinese, Japanese and Korean text works. Operators: from:, to:, "
            "label:<name>, is:unread, is:starred, has:attachment, after:YYYY-MM-DD and "
            "before:YYYY-MM-DD (UTC days). Every word must match, so use few, distinctive words "
            "and try other wordings when nothing turns up. An empty query lists the newest threads."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search words and operators."},
                "inbox_only": {
                    "type": "boolean",
                    "description": "Only threads still in the inbox (not done yet).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": SEARCH_MAX,
                    "description": f"How many threads to return (default {SEARCH_DEFAULT}).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_thread",
        "description": (
            "Read one thread: its subject, labels and messages (sender, date, and the text the "
            "sender wrote, without quoted replies or signatures; long threads keep the newest "
            'messages). "me" is the user.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {"thread_id": {"type": "integer"}},
            "required": ["thread_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_labels",
        "description": "The user's labels, with the description of what belongs under each.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "archive",
        "description": "Propose marking threads done: they leave the inbox." + _CONFIRMED,
        "input_schema": {
            "type": "object",
            "properties": {"thread_ids": _TARGETS},
            "required": ["thread_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_label",
        "description": "Propose adding one of the user's labels to threads." + _CONFIRMED,
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_ids": _TARGETS,
                "label": {"type": "string", "description": "A label name from list_labels."},
            },
            "required": ["thread_ids", "label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "star",
        "description": "Propose starring threads." + _CONFIRMED,
        "input_schema": {
            "type": "object",
            "properties": {"thread_ids": _TARGETS},
            "required": ["thread_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mark_read",
        "description": "Propose marking threads as read." + _CONFIRMED,
        "input_schema": {
            "type": "object",
            "properties": {"thread_ids": _TARGETS},
            "required": ["thread_ids"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    """The call can't run as asked; the message goes back to Claude as an error result."""


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchIn(_Input):
    query: str = ""
    inbox_only: bool = False
    limit: int = Field(SEARCH_DEFAULT, ge=1, le=SEARCH_MAX)


class ThreadIn(_Input):
    thread_id: int


class ChangeIn(_Input):
    thread_ids: list[int] = Field(min_length=1, max_length=MAX_TARGETS)
    label: str | None = None


def _parse[T: BaseModel](model: type[T], data: Any) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in error.errors()
        )
        raise ToolError(f"Invalid input ({problems})") from error


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _when(moment: datetime, zone: tzinfo) -> str:
    return moment.astimezone(zone).strftime("%a %d %b %Y %H:%M")


def _demo_accounts() -> Any:
    return select(Account.id).where(Account.demo)


def real() -> Any:
    """Threads outside the demo mailbox: demo mail is never sent to Claude."""
    return Thread.account_id.not_in(_demo_accounts())


async def _label_names(db: AsyncSession, threads: list[Thread]) -> dict[int, str]:
    ids = {i for t in threads for i in t.label_ids}
    rows = await db.execute(
        select(Label.id, Label.name).where(Label.id.in_(ids), Label.type == "user")
    )
    return dict(rows.all())


def _flags(thread: Thread) -> str:
    flags = [
        name
        for name, on in (
            ("inbox", thread.in_inbox),
            ("unread", thread.unread),
            ("starred", thread.starred),
            ("important", thread.important),
            ("trash", thread.in_trash),
            ("spam", thread.in_spam),
        )
        if on
    ]
    return ", ".join(flags) or "done"


async def search_threads(db: AsyncSession, data: Any, zone: tzinfo) -> dict[str, Any]:
    args = _parse(SearchIn, data)
    stmt = select(Thread).where(real(), ~Thread.drafts_only)
    trash_or_spam = False
    if args.query.strip():
        conditions, trash_or_spam = search.conditions(args.query)
        stmt = stmt.where(*conditions)
    if not trash_or_spam:
        stmt = stmt.where(~Thread.in_trash, ~Thread.in_spam)
    if args.inbox_only:
        stmt = stmt.where(Thread.in_inbox)
    stmt = stmt.order_by(Thread.last_message_at.desc(), Thread.id.desc()).limit(args.limit + 1)
    threads = list(await db.scalars(stmt))
    more = len(threads) > args.limit
    threads = threads[: args.limit]
    names = await _label_names(db, threads)
    return {
        "threads": [
            {
                "id": t.id,
                "subject": t.subject,
                "from": ", ".join(t.senders[:3]),
                "date": _when(t.last_message_at, zone),
                "messages": t.message_count,
                "status": _flags(t),
                "labels": [names[i] for i in t.label_ids if i in names],
                "snippet": trim.trim(t.snippet, SNIPPET_LIMIT),
            }
            for t in threads
        ],
        "more": more,  # more threads match: narrow the query to see them
    }


async def _thread(db: AsyncSession, thread_id: int) -> Thread:
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, real()))
    if thread is None:
        raise ToolError(f"There is no thread {thread_id}")
    return thread


async def get_thread(db: AsyncSession, data: Any, zone: tzinfo) -> dict[str, Any]:
    args = _parse(ThreadIn, data)
    thread = await _thread(db, args.thread_id)
    context = await summaries.load(db, thread.id)
    assert context is not None
    messages: list[dict[str, str]] = []
    budget = THREAD_LIMIT
    for entry in reversed(context.entries):  # newest first, within the budget
        if messages and budget < 200:
            break
        body = trim.trim(entry.body, min(BODY_LIMIT, budget))
        messages.append({"from": entry.sender, "date": _when(entry.date, zone), "text": body})
        budget -= len(body)
    messages.reverse()
    names = await _label_names(db, [thread])
    result: dict[str, Any] = {
        "id": thread.id,
        "subject": thread.subject,
        "status": _flags(thread),
        "labels": [names[i] for i in thread.label_ids if i in names],
        "messages": messages,
    }
    if left_out := len(context.entries) - len(messages):
        result["earlier_messages_left_out"] = left_out
    return result


async def list_labels(db: AsyncSession, data: Any, zone: tzinfo) -> dict[str, Any]:
    _parse(_Input, data)
    rows = await db.execute(
        select(Label.name, func.max(Label.description))
        .where(Label.type == "user", Label.account_id.not_in(_demo_accounts()))
        .group_by(Label.name)
        .order_by(func.lower(Label.name))
    )
    return {"labels": [{"name": n, "description": d} if d else {"name": n} for n, d in rows]}


READERS = {"search_threads": search_threads, "get_thread": get_thread, "list_labels": list_labels}


async def read(db: AsyncSession, name: str, data: Any, zone: tzinfo) -> dict[str, Any]:
    return await READERS[name](db, data, zone)


async def propose(db: AsyncSession, tool_use_id: str, name: str, data: Any) -> dict[str, Any]:
    """Check a change tool's input and describe the change for the user to confirm."""
    args = _parse(ChangeIn, data)
    if (name == "apply_label") != (args.label is not None):
        raise ToolError("apply_label needs `label`; the other tools take only `thread_ids`")
    ids = list(dict.fromkeys(args.thread_ids))
    threads = list(
        await db.scalars(select(Thread).where(Thread.id.in_(ids), real(), ~Thread.drafts_only))
    )
    found = {t.id: t for t in threads}
    if missing := [i for i in ids if i not in found]:
        raise ToolError(f"There are no threads {', '.join(map(str, missing))}")
    label = None
    if args.label is not None:
        # The label as the user named it in Gmail, in every account the threads are in.
        wanted = args.label.strip().lower()
        rows = await db.execute(
            select(Label.account_id, Label.name).where(
                Label.type == "user",
                func.lower(Label.name) == wanted,
                Label.account_id.in_({t.account_id for t in threads}),
            )
        )
        by_account = dict(rows.all())
        if not by_account or any(t.account_id not in by_account for t in threads):
            raise ToolError(f"There is no label named “{args.label}” for these threads")
        label = next(iter(by_account.values()))
    return {
        "id": tool_use_id,
        "tool": name,
        "action": CHANGES[name],
        "label": label,
        "threads": [{"id": i, "subject": found[i].subject} for i in ids],
        "status": "pending",  # then "confirmed", "declined", "skipped", "failed" or "undone"
        "entries": [],  # outbox entries of a confirmed change, for undo
    }


DONE = {
    "archive": "Archived (marked done)",
    "apply_label": "Labeled",
    "star": "Starred",
    "mark_read": "Marked as read",
}


def outcome(action: dict[str, Any]) -> tuple[str, bool]:
    """What Claude hears about a proposal once it is decided: text, and whether it's an error."""
    count = len(action["threads"])
    threads = f"{count} thread{'s' if count != 1 else ''}"
    match action["status"]:
        case "confirmed":
            label = f" “{action['label']}”" if action["label"] else ""
            how = (
                "Done without asking (confirmation is off)"
                if action.get("auto")
                else ("The user confirmed")
            )
            return f"{how}. {DONE[action['tool']]}{label}: {threads}.", False
        case "declined":
            return (
                "The user declined: nothing changed. Don't propose it again unless they ask.",
                True,
            )
        case "skipped":
            return "The user didn't confirm and wrote a new message instead: nothing changed.", True
        case _:
            return f"It couldn't be done: {action.get('error') or 'unknown error'}.", True


def activity(name: str, data: Any, result: Any) -> str | None:
    """A short line for the chat about a read tool call ("Searched “lisbon” · 3 threads")."""
    if not isinstance(data, dict) or not isinstance(result, dict):
        return None
    if name == "search_threads":
        query = str(data.get("query") or "").strip()
        where = " in the inbox" if data.get("inbox_only") else ""
        text = f"Searched “{query}”{where}" if query else f"Looked at the newest threads{where}"
        count = len(result.get("threads") or [])
        found = f"{count}{'+' if result.get('more') else ''} thread{'s' if count != 1 else ''}"
        return f"{text} · {found if count else 'nothing found'}"
    if name == "get_thread":
        return f"Read “{result.get('subject') or '(no subject)'}”"
    if name == "list_labels":
        return "Looked at your labels"
    return None
