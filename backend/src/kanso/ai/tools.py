"""The assistant's tools, on the local mirror only. Change tools only propose: nothing changes
until the user confirms it in the browser (kanso.api.assistant)."""

import json
from datetime import datetime, tzinfo
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import summaries, trim
from kanso.core.settings import settings
from kanso.db import search
from kanso.db.models import Account, Label, Thread

SEARCH_DEFAULT, SEARCH_MAX = 10, 20  # threads per search
SNIPPET_LIMIT = 160
BODY_LIMIT = 1500
THREAD_LIMIT = 10000  # per thread, newest messages first
MAX_TARGETS = 50  # threads a change by thread ids may touch
MAX_MATCHING = 1000  # threads a change by search, or a batch, may touch (actions.CHUNK)
MAX_PARTS = 10  # changes in one batch

# Tool name -> the triage action it proposes (kanso.api.actions.ACTIONS), and what it does.
CHANGES = {
    "archive": "done",
    "delete": "delete",
    "apply_label": "label",
    "remove_label": "unlabel",
    "star": "star",
    "unstar": "unstar",
    "mark_read": "read",
    "mark_unread": "unread",
}
type Change = Literal[
    "archive", "delete", "apply_label", "remove_label", "star", "unstar", "mark_read", "mark_unread"
]
_DOES = {
    "archive": "marking threads done: they leave the inbox",
    "delete": "moving threads to Trash, as Gmail's Delete does",
    "apply_label": "adding one of the user's labels to threads",
    "remove_label": "removing one of the user's labels from threads",
    "star": "starring threads",
    "unstar": "removing the star from threads",
    "mark_read": "marking threads as read",
    "mark_unread": "marking threads as unread",
}
LABELED = {"apply_label", "remove_label"}  # the changes that name a label
BATCHES = {"change_matching", "change_batch"}
PROPOSALS = set(CHANGES) | BATCHES | {"edit_label"}
NAME_LIMIT, DESCRIPTION_LIMIT = 225, 500  # as kanso.api.labels
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

_LABEL = {
    "label": {
        "type": "string",
        "description": "A label name from list_labels: for apply_label and remove_label only.",
    }
}
_CHANGE = {"type": "string", "enum": list(CHANGES), "description": "What to do to the threads."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_threads",
        "description": (
            "Search the user's mailbox (the Gmail account they're in): the full history, archived "
            "(done) mail included; Trash and Spam only with in:trash, in:spam or in:anywhere. "
            'Returns the newest matching threads first. Words match as prefixes, "quoted phrases" '
            "as phrases, and Chinese, Japanese and Korean text works. Gmail's syntax: from:, to:, "
            "cc:, bcc:, with: (any of these), subject:, filename:, list:, label:<name>, "
            "is:/in: unread, read, starred, important, inbox, sent, draft, has:attachment, "
            "after:/before:YYYY-MM-DD (UTC days), newer_than:/older_than: 7d, 2w, 3m or 1y, "
            "larger:/smaller: 5M or 100K, -term to exclude, OR and parentheses. Every word must "
            "match, so use few, distinctive words and try other wordings (or OR) when nothing "
            "turns up. An empty query lists the newest threads."
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
    *(
        {
            "name": name,
            "description": f"Propose {does}." + _CONFIRMED,
            "input_schema": {
                "type": "object",
                "properties": {"thread_ids": _TARGETS} | (_LABEL if name in LABELED else {}),
                "required": ["thread_ids", *(["label"] if name in LABELED else [])],
                "additionalProperties": False,
            },
        }
        for name, does in _DOES.items()
    ),
    {
        "name": "edit_label",
        "description": (
            "Propose renaming one of the user's labels (in Gmail too), changing its description "
            "(what belongs under it, which AI labeling follows), or both." + _CONFIRMED
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "The label's name from list_labels."},
                "new_name": {"type": "string", "minLength": 1, "maxLength": NAME_LIMIT},
                "description": {
                    "type": "string",
                    "maxLength": DESCRIPTION_LIMIT,
                    "description": "The whole new description; empty clears it.",
                },
            },
            "required": ["label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "change_matching",
        "description": (
            "Propose one change to every thread a search matches, up to "
            f"{MAX_MATCHING}: for more threads than thread ids can list. The query works as in "
            "search_threads (the full history; Trash and Spam only with in:trash, in:spam or "
            "in:anywhere); check what it finds with search_threads first." + _CONFIRMED
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "change": _CHANGE,
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Search words and operators.",
                },
            }
            | _LABEL,
            "required": ["change", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "change_batch",
        "description": (
            "Propose several different changes at once, which the user confirms or cancels "
            "together: e.g. archive some threads, label others and star one. Each change "
            "takes thread_ids or a query (as change_matching), not both; at most "
            f"{MAX_MATCHING} threads in all." + _CONFIRMED
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": MAX_PARTS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "change": _CHANGE,
                            "thread_ids": _TARGETS,
                            "query": {"type": "string", "minLength": 1},
                        }
                        | _LABEL,
                        "required": ["change"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["changes"],
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


class MatchingIn(_Input):
    change: Change
    query: str = Field(min_length=1)
    label: str | None = None


class PartIn(_Input):
    change: Change
    thread_ids: list[int] | None = Field(None, min_length=1, max_length=MAX_TARGETS)
    query: str | None = Field(None, min_length=1)
    label: str | None = None


class BatchIn(_Input):
    changes: list[PartIn] = Field(min_length=2, max_length=MAX_PARTS)


class EditLabelIn(_Input):
    label: str
    new_name: str | None = Field(None, min_length=1, max_length=NAME_LIMIT)
    description: str | None = Field(None, max_length=DESCRIPTION_LIMIT)


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
    if settings.demo_mode:  # a demo instance: all of its mail is made up and may be sent
        return select(Account.id).where(false())
    return select(Account.id).where(Account.demo)


def real(account_id: int) -> Any:
    """The conversation's account's threads, outside the demo mailbox: demo mail is never sent to
    Claude."""
    return (Thread.account_id == account_id) & Thread.account_id.not_in(_demo_accounts())


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


async def search_threads(
    db: AsyncSession, data: Any, zone: tzinfo, account_id: int
) -> dict[str, Any]:
    args = _parse(SearchIn, data)
    stmt = select(Thread).where(real(account_id), ~Thread.drafts_only)
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


async def _thread(db: AsyncSession, thread_id: int, account_id: int) -> Thread:
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, real(account_id)))
    if thread is None:
        raise ToolError(f"There is no thread {thread_id}")
    return thread


async def get_thread(db: AsyncSession, data: Any, zone: tzinfo, account_id: int) -> dict[str, Any]:
    args = _parse(ThreadIn, data)
    thread = await _thread(db, args.thread_id, account_id)
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


async def list_labels(db: AsyncSession, data: Any, zone: tzinfo, account_id: int) -> dict[str, Any]:
    _parse(_Input, data)
    rows = await db.execute(
        select(Label.name, Label.description)
        .where(
            Label.type == "user",
            Label.account_id == account_id,
            Label.account_id.not_in(_demo_accounts()),
        )
        .order_by(func.lower(Label.name))
    )
    return {"labels": [{"name": n, "description": d} if d else {"name": n} for n, d in rows]}


READERS = {"search_threads": search_threads, "get_thread": get_thread, "list_labels": list_labels}


async def read(
    db: AsyncSession, name: str, data: Any, zone: tzinfo, account_id: int
) -> dict[str, Any]:
    return await READERS[name](db, data, zone, account_id)


async def _label(db: AsyncSession, account_id: int, change: str, label: str | None) -> str | None:
    """The label as the user named it in Gmail, for the changes that name one."""
    if (change in LABELED) != (label is not None):
        raise ToolError(
            "apply_label and remove_label need `label`; the other changes don't take it"
        )
    if label is None:
        return None
    found = await db.scalar(
        select(Label.name).where(
            Label.type == "user",
            Label.account_id == account_id,
            func.lower(Label.name) == label.strip().lower(),
        )
    )
    if found is None:
        raise ToolError(f"There is no label named “{label}”")
    return found


async def _by_ids(db: AsyncSession, account_id: int, thread_ids: list[int]) -> list[Thread]:
    ids = list(dict.fromkeys(thread_ids))
    threads = list(
        await db.scalars(
            select(Thread).where(Thread.id.in_(ids), real(account_id), ~Thread.drafts_only)
        )
    )
    found = {t.id: t for t in threads}
    if missing := [i for i in ids if i not in found]:
        raise ToolError(f"There are no threads {', '.join(map(str, missing))}")
    return [found[i] for i in ids]


async def _by_query(db: AsyncSession, account_id: int, query: str) -> list[Thread]:
    """Every thread the search finds, as search_threads finds them, newest first."""
    conditions, trash_or_spam = search.conditions(query)
    stmt = select(Thread).where(real(account_id), ~Thread.drafts_only, *conditions)
    if not trash_or_spam:
        stmt = stmt.where(~Thread.in_trash, ~Thread.in_spam)
    stmt = stmt.order_by(Thread.last_message_at.desc(), Thread.id.desc()).limit(MAX_MATCHING + 1)
    threads = list(await db.scalars(stmt))
    if not threads:
        raise ToolError(f"No threads match “{query}”")
    if len(threads) > MAX_MATCHING:
        raise ToolError(
            f"More than {MAX_MATCHING} threads match “{query}”: narrow the query, e.g. with "
            "before: or after:, and change them in parts"
        )
    return threads


async def _part(
    db: AsyncSession,
    account_id: int,
    change: str,
    label: str | None,
    thread_ids: list[int] | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """One change of a proposal, on threads picked by id or by a search."""
    if (thread_ids is None) == (query is None):
        raise ToolError("Give each change either `thread_ids` or `query`")
    name = await _label(db, account_id, change, label)
    if thread_ids is not None:
        threads = await _by_ids(db, account_id, thread_ids)
    else:
        assert query is not None
        threads = await _by_query(db, account_id, query)
    return {
        "tool": change,
        "action": CHANGES[change],
        "label": name,
        "query": query,
        "threads": [{"id": t.id, "subject": t.subject} for t in threads],
    }


async def _edit_label(db: AsyncSession, account_id: int, data: Any) -> dict[str, Any]:
    args = _parse(EditLabelIn, data)
    label = await db.scalar(
        select(Label).where(
            Label.type == "user",
            Label.account_id == account_id,
            func.lower(Label.name) == args.label.strip().lower(),
        )
    )
    if label is None:
        raise ToolError(f"There is no label named “{args.label}”")
    name = args.new_name.strip() if args.new_name is not None else None
    description = args.description.strip() if args.description is not None else None
    if name == label.name:
        name = None
    if description == label.description:
        description = None
    if name is None and description is None:
        raise ToolError("Give a new name or description that differs from the label's own")
    if name is not None and name.lower() != label.name.lower():
        clash = await db.scalar(
            select(Label.id).where(
                Label.account_id == account_id, func.lower(Label.name) == name.lower()
            )
        )
        if clash is not None:
            raise ToolError(f"A label named “{name}” already exists")
    return {
        "tool": "edit_label",
        "action": None,
        "label": label.name,
        "query": None,
        "threads": [],
        "label_id": label.id,
        "edit": {"name": name, "description": description},
        "before": {"name": label.name, "description": label.description},
    }


def parts(action: dict[str, Any]) -> list[dict[str, Any]]:
    """A batch's changes, or the one change of any other proposal."""
    return action.get("parts") or [action]


async def propose(
    db: AsyncSession, tool_use_id: str, name: str, data: Any, account_id: int
) -> dict[str, Any]:
    proposal: dict[str, Any]
    if name == "change_batch":
        batch = _parse(BatchIn, data)
        changes = [
            await _part(db, account_id, p.change, p.label, p.thread_ids, p.query)
            for p in batch.changes
        ]
        if sum(len(c["threads"]) for c in changes) > MAX_MATCHING:
            raise ToolError(f"A batch may change at most {MAX_MATCHING} threads in all")
        proposal = {"tool": name, "action": None, "label": None, "query": None, "threads": []}
        proposal["parts"] = changes
    elif name == "edit_label":
        proposal = await _edit_label(db, account_id, data)
    elif name == "change_matching":
        matching = _parse(MatchingIn, data)
        proposal = await _part(
            db, account_id, matching.change, matching.label, query=matching.query
        )
    else:
        args = _parse(ChangeIn, data)
        proposal = await _part(db, account_id, name, args.label, thread_ids=args.thread_ids)
    return {
        "id": tool_use_id,
        **proposal,
        "status": "pending",  # then "confirmed", "declined", "skipped", "failed" or "undone"
        "entries": [],  # outbox entries of a confirmed change, for undo
    }


DONE = {
    "archive": "Archived (marked done)",
    "delete": "Moved to Trash",
    "apply_label": "Labeled",
    "remove_label": "Removed the label",
    "star": "Starred",
    "unstar": "Unstarred",
    "mark_read": "Marked as read",
    "mark_unread": "Marked as unread",
}


def _done(part: dict[str, Any]) -> str:
    if part["tool"] == "edit_label":
        name, description = part["edit"]["name"], part["edit"]["description"]
        done = [f"renamed it “{name}”"] if name is not None else []
        if description is not None:
            done.append("changed its description" if description else "cleared its description")
        return f"Label “{part['label']}”: {' and '.join(done)}"
    count = len(part["threads"])
    label = f" “{part['label']}”" if part["label"] else ""
    return f"{DONE[part['tool']]}{label}: {count} thread{'s' if count != 1 else ''}"


def outcome(action: dict[str, Any]) -> tuple[str, bool]:
    """What Claude hears about a proposal once it is decided: text, and whether it's an error."""
    match action["status"]:
        case "confirmed":
            how = (
                "Done without asking (confirmation is off)"
                if action.get("auto")
                else ("The user confirmed")
            )
            return f"{how}. {'; '.join(_done(p) for p in parts(action))}.", False
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
