# ruff: noqa: E501 (the demo mails are long one-line strings)
"""Fill the database with a fake mailbox (DEMO DATA): `python -m kanso.demo_seed`.

Safe to run again. On a demo instance (DEMO_MODE) it empties every table first.
"""

import asyncio
import logging
import struct
import zlib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from sqlalchemy import delete, exists, select, text, update

from kanso.core.settings import settings
from kanso.db import contacts
from kanso.db import redis as events
from kanso.db.models import Account, AiUsage, Base, Label
from kanso.db.session import Session, engine
from kanso.gmail import mime
from kanso.sync import store

log = logging.getLogger("kanso.demo_seed")

DEMO_EMAIL = "demo@example.com"
ME = ("Demo", DEMO_EMAIL)

SYSTEM_LABELS = ["INBOX", "SENT", "DRAFT", "TRASH", "SPAM", "UNREAD", "STARRED", "IMPORTANT"]
USER_LABELS = {
    "Home": "#A07338",
    "Personal": "#4F6E8C",
    "Work": "#6B6B66",
    "Travel": "#556B3F",
    "Finance": "#87607E",
    "Health": "#A35A3A",
    "Newsletters": "#B8B3A2",
    "Receipts": "#B8B3A2",
    "Notifications": "#B8B3A2",
}
DESCRIPTIONS = {
    "Home": "the apartment, the landlord, utilities at home, the library and the neighborhood",
    "Personal": "friends and family: plans, photos, invitations",
    "Work": "colleagues, contracts, meetings and recruiting",
    "Travel": "flights, trips, bookings and travel plans",
    "Finance": "bills, bank statements and payment reminders",
    "Health": "doctors, appointments and prescriptions",
    "Newsletters": "newsletters and digests I subscribed to",
    "Receipts": "order confirmations, shipping updates and subscription receipts",
    "Notifications": "automated notifications from services, CI and code review",
}
BUNDLES = {"Newsletters", "Receipts", "Notifications"}


def _person(name: str) -> tuple[str, str]:
    local = name.lower().replace("’", "").replace(".", "").replace(" ", ".")
    return name, f"{local}@example.com"


# (label, [(sender, hours ago, body)], subject, flags, charset)
DemoThread = tuple[str | None, list[tuple[tuple[str, str], float, str]], str, set[str], str]
# fmt: off
THREADS: list[DemoThread] = [
    ("Home", [(_person("Mika Tanaka"), 2, "Hi,\n\nAttaching the renewal contract for apartment 4B. The new term starts January 1st; please sign page 3 and send it back by the 20th.\n\nThanks,\nMika")], "Lease renewal for apartment 4B", {"unread", "attachment"}, "utf-8"),
    ("Home", [(("City Library", "holds@library.example.com"), 50, "Your hold is ready for pickup. Waiting at the front desk for 7 days.")], "Your hold is ready for pickup", set(), "utf-8"),
    ("Personal", [(_person("Daniel Reyes"), 30, "Are you free for dinner on Saturday? There's a new ramen place near the station."), (ME, 20, "Sounds great! What time works for you?"), (_person("Daniel Reyes"), 3, "Works for me. 7pm?")], "Re: Dinner on Saturday?", {"unread"}, "utf-8"),
    ("Personal", [(_person("Priya Shah"), 75, "The ridge ones came out great. Uploading the rest tonight.")], "Photos from the hike", set(), "utf-8"),
    ("Personal", [(_person("Tomás Silva"), 100, "I can pick it up Sunday morning. Is 10 ok?")], "Re: Bike for sale", set(), "utf-8"),
    ("Personal", [(_person("Yuki Sato"), 125, "Vote in the thread. Three candidates this month.")], "Book club: next pick", set(), "utf-8"),
    ("Personal", [(_person("Sofia Nguyen"), 170, "Come by from 6pm. Bring nothing but yourself!")], "Housewarming on the 14th", set(), "utf-8"),
    ("Work", [(_person("Kenji Mori"), 4, "Payment terms are in section 4. Let me know if legal has comments by Thursday.")], "Contract review, draft v2", {"unread", "attachment"}, "utf-8"),
    ("Work", [(_person("Lena Fischer"), 74, "Main open question is hiring. Notes attached below.\n\n- Two backend roles\n- One designer")], "Q4 planning notes", set(), "utf-8"),
    ("Work", [(("Fernwood Recruiting", "talent@fernwood.example.com"), 120, "Thanks for your time. Next is a short take-home.")], "Interview follow-up", set(), "utf-8"),
    ("Work", [(("王芳", "wang.fang@example.cn"), 6, "大家好，\n\n周五下午三点在三楼会议室开项目会议，请提前准备好本周的进度报告。\n\n谢谢！\n王芳")], "周五的项目会议安排", {"unread"}, "gb2312"),
    # Long threads, with canned summaries in SUMMARIES.
    ("Work", [(_person("Lena Fischer"), 100, "Hi all,\n\nI'd like to plan a three-day team offsite in March. Two options: March 10–12 or March 17–19. Lisbon is the front-runner for the venue, since flights are cheap from everywhere.\n\nPlease tell me which dates work for you.\n\nLena"), (_person("Kenji Mori"), 80, "March 10–12 works for me. Do we have budget approval for travel, or is that still pending with finance?"), (_person("Lena Fischer"), 30, "Thanks Kenji. Finance approved the travel budget this morning, so we're good. Let's go with March 10–12 in Lisbon.\n\nI'm holding a venue near Cais do Sodré, but I need a final headcount by Friday to confirm it."), (_person("Kenji Mori"), 9, "Great. One open question: do the remote folks in Berlin join in person, or do we set up a video link for them? That changes the headcount.")], "Team offsite in March", {"unread"}, "utf-8"),
    ("Work", [(("李伟", "li.wei@example.cn"), 110, "王芳你好，\n\n新版官网的设计稿已经定稿，开发这边预计下周三完成。测试需要两三天，所以最早下下周一可以上线。\n\n李伟"), (("王芳", "wang.fang@example.cn"), 90, "收到。市场部希望配合十一月底的活动一起上线，下下周一可以接受。翻译稿什么时候能到？"), (("李伟", "li.wei@example.cn"), 50, "翻译稿还在等供应商，他们说周四前给。如果周四拿不到，日文版可能要晚一周。"), (("王芳", "wang.fang@example.cn"), 20, "好的。请在周四下午告诉我翻译稿的情况，我再决定是否把日文版单独推迟。")], "新版官网上线时间", set(), "utf-8"),
    ("Work", [(("陳志明", "chen.zhiming@example.tw"), 52, "各位同事好：\n\n台北辦公室將於下個月搬遷至信義區新大樓，詳細時間表請見附件。\n\n陳志明")], "台北辦公室搬遷通知", {"html"}, "big5"),
    ("Travel", [(_person("Ana Lopes"), 78, "Seats 14A and 14B. Departure 08:35 from gate B12.")], "Flight itinerary: Lisbon, Dec 12", {"starred"}, "utf-8"),
    ("Travel", [(_person("Marco Bianchi"), 124, "The moss garden one especially. Can you send the full-size version?")], "Re: Kyoto trip photos", set(), "utf-8"),
    ("Travel", [(("山田花子", "hanako.yamada@example.jp"), 26, "お世話になっております。\n\n来週の打ち合わせですが、火曜日の14時からでいかがでしょうか。京都駅前のカフェを予約しておきます。\n\nよろしくお願いいたします。\n山田")], "来週の打ち合わせについて", {"unread", "html"}, "iso-2022-jp"),
    ("Finance", [(("Northwind Bank", "statements@northwind.example.com"), 77, "Your October statement is ready. Period ending Oct 31.")], "Your October statement is ready", {"attachment"}, "utf-8"),
    ("Finance", [(("Harbor Electric", "billing@harbor.example.com"), 122, "Your bill for October is $64.20. Payment is due by Nov 15.")], "Your bill for October", set(), "utf-8"),
    ("Health", [(("Dr. Akiyama’s Office", "office@akiyama-clinic.example.com"), 98, "Appointment reminder: Nov 9, 10:30. Please arrive 10 minutes early.")], "Appointment reminder: Nov 9, 10:30", set(), "utf-8"),
    ("Newsletters", [(("The Morning Brief", "brief@morning.example.com"), 5, "Today: markets, weather and one long read worth your time.")], "The Morning Brief: Monday", {"unread", "html", "images"}, "utf-8"),
    ("Newsletters", [(("Hacker Weekly", "editor@hackerweekly.example.com"), 30, "This week: Postgres full-text search tricks and a CJK bigram primer.")], "Hacker Weekly #312", {"html", "images"}, "utf-8"),
    ("Newsletters", [(("Design Notes", "notes@design.example.com"), 54, "On whitespace, calm interfaces and paper tones.")], "Design Notes: quiet interfaces", {"html", "images"}, "utf-8"),
    ("Receipts", [(("Paper & Ink", "orders@paperink.example.com"), 8, "Your order has shipped. Tracking number 1Z999AA10123456784.")], "Your order has shipped", {"unread"}, "utf-8"),
    ("Receipts", [(("Cloud Notes", "billing@cloudnotes.example.com"), 60, "Subscription receipt: $4.00 for November.")], "Subscription receipt", {"attachment"}, "utf-8"),
    ("Notifications", [(("GitHub", "notifications@github.example.com"), 1, "kanso: PR #12 Gmail history sync — review requested.")], "PR #12: Gmail history sync", {"unread"}, "utf-8"),
    ("Notifications", [(("CI", "ci@example.com"), 1.5, "All checks passed on main.")], "CI passed", set(), "utf-8"),
    # Not labeled yet.
    (None, [(_person("Hana Ito"), 7, "Are you around next week for a coffee? Tuesday or Wednesday works for me.")], "Coffee next week?", {"unread"}, "utf-8"),
    (None, [(("Parcel Point", "notices@parcelpoint.example.com"), 12, "We tried to deliver your parcel today. It is waiting at the pickup point on 3rd Street.")], "Delivery attempt", set(), "utf-8"),
    ("Travel", [(_person("Ana Lopes"), 900, "Booked the Alfama apartment for Dec 12–16. Confirmation attached.")], "Lisbon apartment booking", {"done", "attachment"}, "utf-8"),
    ("Finance", [(("Northwind Bank", "statements@northwind.example.com"), 800, "Your September statement is ready.")], "Your September statement is ready", {"done"}, "utf-8"),
    ("Personal", [(ME, 40, "Hi Priya, attaching the trail map for next time.")], "Trail map", {"sent", "done"}, "utf-8"),
    (None, [(ME, 10, "Hi Kenji, a few notes on section 4 before")], "Notes on the contract", {"draft"}, "utf-8"),
    (None, [(("Prize Center", "winner@prizes.example.com"), 200, "You have won! Click here.")], "Congratulations!!!", {"trash"}, "utf-8"),
    (None, [(("Account Security", "security@paypa1-verify.example.com"), 14, "Your account is locked. Confirm your password within 24 hours to avoid suspension.")], "Action required: your account is locked", {"spam", "unread"}, "utf-8"),
    (None, [(("Crypto Yield Pro", "invest@cryptoyield.example.com"), 33, "Earn 40% a month, guaranteed. Reply with your wallet address to get started.")], "Guaranteed 40% monthly returns", {"spam"}, "utf-8"),
    (None, [(("Rank Booster", "growth@rankbooster.example.com"), 60, "We noticed your website isn't on page one of Google. We can fix that for $99.")], "Quick question about your website", {"spam"}, "utf-8"),
]
# fmt: on


# A demo thread is never sent to Claude; it gets these summaries instead.
SUMMARIES = {
    "Team offsite in March": "Lena is holding a venue in Lisbon for the offsite on March 10–12 and needs the final headcount by Friday; finance approved the travel budget. Still open: whether the Berlin team joins in person or by video, which changes the headcount.",
    "新版官网上线时间": "新版官网预计下下周一上线，配合十一月底的市场活动；开发下周三完成，之后测试两三天。翻译稿供应商承诺周四前交付，李伟需在周四下午向王芳确认，再决定日文版是否推迟一周。",
}


def demo_summary(subject: str, bodies: list[str]) -> str:
    if subject in SUMMARIES:
        return SUMMARIES[subject]
    latest = " ".join(bodies[-1].split()) if bodies else ""
    if len(latest) > 140:
        latest = latest[:140] + " …"
    count = f"{len(bodies)} messages" if len(bodies) != 1 else "One message"
    return f"Demo summary, made without Claude. {count}; the latest says: “{latest}”"


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\0" + bytes(rgb) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


UNSPLASH = "https://images.unsplash.com"

# Demo mail has no Gmail copy, so the attachment endpoint serves these.
DEMO_FILES = {
    "image/png": _png(120, 24, (0x2F, 0x4B, 0x6E)),
    "application/pdf": b"%PDF-1.4\n% kanso demo attachment (DEMO DATA)\n%%EOF\n",
}


def _mime(
    sender: tuple[str, str],
    recipient: tuple[str, str],
    subject: str,
    body: str,
    when: datetime,
    charset: str,
    flags: set[str],
) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{sender[0]} <{sender[1]}>"
    msg["To"] = f"{recipient[0]} <{recipient[1]}>"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(when)
    msg["Message-ID"] = make_msgid(domain="kanso.demo")
    msg["X-Kanso-Demo"] = "true"
    msg.set_content(body, charset=charset)
    if "html" in flags:
        paragraphs = "".join(f"<p>{line}</p>" for line in body.splitlines() if line)
        if "images" not in flags:
            msg.add_alternative(
                f"<html><body>{paragraphs}</body></html>", subtype="html", charset=charset
            )
        else:
            # Remote images (an <img> and a CSS background) the reader blocks until "Show images".
            msg.add_alternative(
                f'<html><body style="font-family:Georgia,serif"><img src="cid:logo@kanso.demo" '
                f'alt="logo" width="120"><h1>{subject}</h1>'
                f'<img src="{UNSPLASH}/photo-1506744038136-46273834b3fb?w=960&q=70&auto=format&fit=crop" '
                f'alt="Hero image" width="480">'
                f"{paragraphs}"
                f'<div style="padding:56px 24px;background:#556b3f url({UNSPLASH}/'
                f"photo-1470071459604-3b5ec3a7fe05?w=960&q=60&auto=format&fit=crop) center/cover;"
                f'color:#fff;font-size:20px;font-weight:bold">Read the long read &rarr;</div>'
                f"<script>alert('stripped by the sanitizer')</script></body></html>",
                subtype="html",
            )
            html_part = msg.get_body(("html",))
            assert isinstance(html_part, EmailMessage)
            html_part.add_related(
                DEMO_FILES["image/png"],
                "image",
                "png",
                cid="<logo@kanso.demo>",
                filename="logo.png",
                disposition="inline",
            )
    if "attachment" in flags:
        msg.add_attachment(
            DEMO_FILES["application/pdf"], "application", "pdf", filename=f"{subject[:30]}.pdf"
        )
    return msg.as_bytes()


async def seed() -> int:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    async with Session() as db:
        await db.execute(delete(Account).where(Account.demo.is_(True)))  # cascades
        account = Account(
            email=DEMO_EMAIL,
            display_name="Demo",
            color="#2F4B6E",
            refresh_token=b"",
            demo=True,
            sync_state="ready",
        )
        db.add(account)
        await db.flush()
        labels = await store.save_labels(
            db,
            account.id,
            [{"id": g, "name": g, "type": "system"} for g in SYSTEM_LABELS]
            + [
                {"id": f"Label_{i}", "name": name, "type": "user", "color": {"backgroundColor": c}}
                for i, (name, c) in enumerate(USER_LABELS.items(), 1)
            ],
        )
        thread_ids = set()
        for t, (label, messages, subject, flags, charset) in enumerate(THREADS, 1):
            for m, (sender, hours_ago, body) in enumerate(messages, 1):
                when = now - timedelta(hours=hours_ago)
                from_me = sender == ME
                default = _person("Kenji Mori" if "draft" in flags else "Priya Shah")
                other = next((s for s, _, _ in messages if s != ME), default)
                raw = _mime(sender, other if from_me else ME, subject, body, when, charset, flags)
                last = m == len(messages)
                gmail_labels = (
                    [] if label is None else [f"Label_{list(USER_LABELS).index(label) + 1}"]
                )
                if from_me:
                    gmail_labels.append("DRAFT" if "draft" in flags else "SENT")
                if not ({"done", "draft", "trash", "spam"} & flags) and not from_me:
                    gmail_labels.append("INBOX")
                if "unread" in flags and last:
                    gmail_labels.append("UNREAD")
                if "starred" in flags:
                    gmail_labels.append("STARRED")
                if "trash" in flags:
                    gmail_labels.append("TRASH")
                if "spam" in flags:
                    gmail_labels.append("SPAM")
                meta = {
                    "id": f"demo{t:03}{m:02}",
                    "threadId": f"demo{t:03}",
                    "labelIds": gmail_labels,
                    "snippet": " ".join(body.split())[:140],
                    "internalDate": str(int(when.timestamp() * 1000)),
                    "sizeEstimate": len(raw),
                }
                thread_ids.add(
                    await store.save_message(db, account.id, meta, mime.parse(raw), labels)
                )
        await store.refresh_threads(db, thread_ids)
        for name, description in DESCRIPTIONS.items():
            await db.execute(
                update(Label)
                .where(Label.account_id == account.id, Label.name == name)
                .values(description=description, ai_managed=True, is_bundle=name in BUNDLES)
            )
        # Fake AI usage; not on a demo instance, where usage is real.
        today = now.date()
        if not settings.demo_mode:
            await db.execute(
                delete(AiUsage).where(
                    AiUsage.feature == "labeling", AiUsage.day >= today - timedelta(days=1)
                )
            )
            for day, requests in ((today - timedelta(days=1), 18), (today, 7)):
                db.add(
                    AiUsage(
                        day=day,
                        feature="labeling",
                        model="claude-haiku-5-5",
                        requests=requests,
                        input_tokens=requests * 340,
                        output_tokens=requests * 24,
                    )
                )
        await db.commit()
        account_id = account.id
        await contacts.refresh(db)
    try:
        await events.threads_changed(account_id, thread_ids)
        await events.sync_changed(account_id)
    except Exception as error:  # live updates are a nicety here
        log.warning("could not publish the change to Redis: %s", error)
    return len(thread_ids)


async def reset() -> int:
    """Ids start over, so a browser left open finds the same threads under the same ids."""
    async with Session() as db:
        if await db.scalar(select(exists().where(Account.demo.is_(False)))):
            raise RuntimeError("Not resetting: this database has a connected Gmail account.")
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        await db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await db.commit()
    return await seed()


async def reset_daily() -> None:
    while True:
        now = datetime.now(UTC)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((midnight - now).total_seconds())
        try:
            log.info("demo reset: %d threads", await reset())
        except Exception:
            log.exception("demo reset failed")


async def main() -> None:
    try:
        count = await (reset() if settings.demo_mode else seed())
    finally:
        await engine.dispose()
        await events.redis.aclose()
    print(f"Demo mailbox ready: {DEMO_EMAIL} with {count} threads (DEMO DATA, not real mail).")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(main())
