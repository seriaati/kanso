"""Icons for the labels that have none: Claude picks one per label from the UI's label icons,
by its name and description. Only a confident pick is kept; the others stay a colored dot.
Claude only."""

import json
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import keys, usage
from kanso.db.models import Account, Label

FEATURE = "icons"
CONFIDENCE = 0.7  # as AI labeling's
MAX_TOKENS = 4096  # room for some thinking plus the JSON answer

# The UI's label icons (frontend/src/app/mail.ts labelIcons).
ICONS = (
    "home", "briefcase", "receipt", "cart", "plane", "heart", "book", "people", "bell", "bolt",
    "gift", "calendar", "star", "code", "image", "link", "newspaper", "tag", "package", "shield",
    "bank", "chat", "school", "food", "fitness", "music", "film", "game", "car", "pin", "globe",
    "key", "megaphone", "ticket", "paw", "leaf",
)  # fmt: skip

INSTRUCTIONS = f"""You pick an icon for each of the user's email labels. The icons, by name: \
{", ".join(ICONS)}.

For each label, pick the icon that best shows what mail belongs under it, judging by its name \
and description, and give your confidence from 0 to 1 that someone would recognize the label by \
it. When no icon clearly fits, still pick the closest one, with a low confidence."""

SCHEMA = {
    "type": "object",
    "properties": {
        "icons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "icon": {"type": "string", "enum": list(ICONS)},
                    "confidence": {"type": "number"},
                },
                "required": ["label", "icon", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["icons"],
    "additionalProperties": False,
}


class Pick(BaseModel):
    label: str
    icon: str
    confidence: float


class Picked(BaseModel):
    label: str
    icon: str | None  # None: no icon was a confident fit


class Failed(Exception):
    """Shown to the user as is."""


def prompt(labels: list[Label]) -> str:
    lines = [
        f"- {label.name}: {label.description}" if label.description else f"- {label.name}"
        for label in labels
    ]
    return "The labels:\n" + "\n".join(lines)


def request(labels: list[Label], model: str) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": INSTRUCTIONS,
        "messages": [{"role": "user", "content": prompt(labels)}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def parse(response: Any) -> dict[str, str]:
    """The confident picks, by label name."""
    if response.stop_reason == "refusal":
        raise Failed("Claude declined to pick icons.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if response.stop_reason == "max_tokens" or text is None:
        raise Failed("Claude’s answer was cut off. Try again.")
    try:
        picks = [Pick.model_validate(item) for item in json.loads(text)["icons"]]
    except (ValueError, KeyError, TypeError, ValidationError) as error:
        raise Failed("Claude’s answer couldn’t be read. Try again.") from error
    return {p.label: p.icon for p in picks if p.icon in ICONS and p.confidence >= CONFIDENCE}


def client(key: str) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=key, timeout=60.0, max_retries=1)


async def pick(db: AsyncSession, account: Account, model: str) -> list[Picked]:
    """Sets the icons of the account's labels that have none. The caller commits."""
    labels = list(
        await db.scalars(
            select(Label)
            .where(Label.account_id == account.id, Label.type == "user", Label.icon.is_(None))
            .order_by(func.lower(Label.name))
        )
    )
    if not labels:
        return []
    ai = client(await keys.get(db, "anthropic"))
    try:
        response = await ai.messages.create(**request(labels, model))
    except anthropic.APIStatusError as error:
        if usage.key_rejected(error):
            await usage.reject_key(db, error)
            await db.commit()
            raise Failed(usage.KEY_REJECTED) from error
        raise Failed(f"Claude answered {error.status_code}. Try again in a moment.") from error
    except anthropic.APIConnectionError as error:
        raise Failed("Couldn’t reach Claude. Try again in a moment.") from error
    await usage.record(db, FEATURE, model, response.usage)
    icons = parse(response)
    for label in labels:
        label.icon = icons.get(label.name)
    return [Picked(label=label.name, icon=label.icon) for label in labels]
