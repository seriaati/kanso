"""An in-memory Gmail and Pub/Sub behind an httpx2 MockTransport, shaped like the REST API."""

import base64
import json
from email import message_from_bytes
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs

import httpx2 as httpx


class Crash(BaseException):
    """Like SIGKILL: not an Exception, so no handler in Kanso catches it."""


SYSTEM = ["INBOX", "SENT", "DRAFT", "TRASH", "SPAM", "UNREAD", "STARRED", "IMPORTANT"]


def make_raw(
    subject: str,
    body: str,
    sender: str = "Daniel <daniel@example.com>",
    headers: dict[str, str] | None = None,
) -> bytes:
    msg = EmailMessage()
    for name, value in (headers or {}).items():
        msg[name] = value
    msg["From"] = sender
    msg["To"] = "me@gmail.com"
    msg["Subject"] = subject
    msg.set_content(body)
    return msg.as_bytes()


class FakeGmail:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.labels: list[dict[str, Any]] = [{"id": i, "name": i, "type": "system"} for i in SYSTEM]
        self.labels.append(
            {
                "id": "Label_1",
                "name": "Work",
                "type": "user",
                "color": {"backgroundColor": "#6b6b66"},
            }
        )
        self.history_id = 1000
        self.history: list[dict[str, Any]] = []
        self.oldest_history = 0  # startHistoryId below this gets 404, like expired history
        self.page_size = 500
        self.requests: list[str] = []
        self.sleeps: list[float] = []  # retry delays (tests replace the client's sleep)
        self.failures: dict[str, list[int]] = {}  # request key -> statuses to return first
        # Request keys whose next call crashes the caller before or after Gmail applies it.
        self.crash_before: set[str] = set()
        self.crash_after: set[str] = set()
        self.writes: list[tuple[str, Any]] = []  # (key, JSON body) of every write request
        # draft id -> its current message id
        self.drafts: dict[str, str] = {}
        self.sent: list[bytes] = []
        self.display_name = "Me Myself"
        self._ids = 0
        # Pub/Sub
        self.pubsub_queue: list[dict[str, Any]] = []
        self.acked: list[str] = []

    def _record(self, **change: Any) -> None:
        self.history_id += 1
        self.history.append({"id": str(self.history_id), **change})

    def add(
        self,
        gmail_id: str,
        thread: str,
        labels: list[str],
        raw: bytes,
        *,
        date: int = 0,
        record: bool = True,
    ) -> None:
        self.messages[gmail_id] = {
            "id": gmail_id,
            "threadId": thread,
            "labelIds": labels,
            "raw": raw,
            "internalDate": str(date or 1_700_000_000_000 + len(self.messages) * 60_000),
            "snippet": raw.split(b"\n\n", 1)[-1][:50].decode().strip().replace("'", "&#39;"),
        }
        if record:
            self._record(messagesAdded=[{"message": self._ref(gmail_id)}])
        else:
            self.history_id += 1

    def delete(self, gmail_id: str, record: bool = True) -> None:
        ref = self._ref(gmail_id)
        del self.messages[gmail_id]
        if record:
            self._record(messagesDeleted=[{"message": ref}])
        else:
            self.history_id += 1

    def relabel(
        self,
        gmail_id: str,
        add: tuple[str, ...] = (),
        remove: tuple[str, ...] = (),
        record: bool = True,
    ) -> None:
        labels = self.messages[gmail_id]["labelIds"]
        labels[:] = [label for label in labels if label not in remove] + [
            label for label in add if label not in labels
        ]
        if not record:
            self.history_id += 1
            return
        if add:
            self._record(labelsAdded=[{"message": self._ref(gmail_id), "labelIds": list(add)}])
        if remove:
            self._record(labelsRemoved=[{"message": self._ref(gmail_id), "labelIds": list(remove)}])

    def notify(self, email: str = "me@gmail.com") -> str:
        ack_id = f"ack-{len(self.pubsub_queue) + len(self.acked)}"
        data = json.dumps({"emailAddress": email, "historyId": self.history_id}).encode()
        self.pubsub_queue.append(
            {"ackId": ack_id, "message": {"data": base64.b64encode(data).decode()}}
        )
        return ack_id

    def _ref(self, gmail_id: str) -> dict[str, Any]:
        m = self.messages[gmail_id]
        return {"id": gmail_id, "threadId": m["threadId"], "labelIds": list(m["labelIds"])}

    def _next_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    def raw_fetches(self, gmail_id: str) -> int:
        return self.requests.count(f"GET /messages/{gmail_id} raw")

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        params = {k: v[0] for k, v in parse_qs(url.query.decode()).items()}
        path = url.path.removeprefix("/upload").removeprefix("/gmail/v1/users/me")
        key = f"{request.method} {path} {params.get('format', '')}".strip()
        self.requests.append(key)
        if self.failures.get(key):
            status = self.failures[key].pop(0)
            return httpx.Response(status, json={"error": {"message": "injected"}})
        if key in self.crash_before:
            self.crash_before.discard(key)
            raise Crash(key)
        if request.method != "GET" and url.host != "pubsub.googleapis.com" and path != "/watch":
            if request.headers.get("content-type", "").startswith("multipart/related"):
                sent = _multipart(request)
            else:
                sent = json.loads(request.content) if request.content else None
            self.writes.append((key, sent))
            response = self._write(request.method, path, sent)
            if key in self.crash_after:
                self.crash_after.discard(key)
                raise Crash(key)
            return response

        if url.host == "pubsub.googleapis.com":
            body = json.loads(request.content)
            if path.endswith(":pull"):
                received, self.pubsub_queue = self.pubsub_queue, []
                return httpx.Response(200, json={"receivedMessages": received})
            self.acked += body["ackIds"]
            return httpx.Response(200, json={})
        if path == "/profile":
            return httpx.Response(
                200,
                json={
                    "emailAddress": "me@gmail.com",
                    "messagesTotal": len(self.messages),
                    "historyId": str(self.history_id),
                },
            )
        if path == "/labels":
            return httpx.Response(200, json={"labels": self.labels})
        if path == "/watch":
            return httpx.Response(
                200, json={"historyId": str(self.history_id), "expiration": "4102444800000"}
            )
        if path == "/messages":
            ordered = sorted(self.messages.values(), key=lambda m: -int(m["internalDate"]))
            start = int(params.get("pageToken", "0"))
            page = ordered[start : start + self.page_size]
            body: dict[str, Any] = {
                "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in page]
            }
            if start + self.page_size < len(ordered):
                body["nextPageToken"] = str(start + self.page_size)
            return httpx.Response(200, json=body)
        if path.startswith("/messages/"):
            message = self.messages.get(path.removeprefix("/messages/"))
            if message is None:
                return httpx.Response(404, json={"error": {"message": "Not Found"}})
            body = {k: v for k, v in message.items() if k != "raw"}
            if params.get("format") == "raw":
                body["raw"] = base64.urlsafe_b64encode(message["raw"]).decode().rstrip("=")
            elif params.get("format") == "metadata":
                parsed = message_from_bytes(message["raw"])
                headers = [{"name": k, "value": str(v)} for k, v in parsed.items()]
                body = {"id": body["id"], "payload": {"headers": headers}}
            else:  # minimal
                body = {k: body[k] for k in ("id", "threadId", "labelIds")}
            return httpx.Response(200, json=body)
        if path == "/drafts":
            drafts = [
                {"id": d, "message": {"id": m, "threadId": self.messages[m]["threadId"]}}
                for d, m in self.drafts.items()
            ]
            return httpx.Response(200, json={"drafts": drafts})
        if path == "/settings/sendAs":
            alias = {"sendAsEmail": "me@gmail.com", "displayName": self.display_name}
            return httpx.Response(200, json={"sendAs": [{**alias, "isDefault": True}]})
        if path == "/history":
            start = int(params["startHistoryId"])
            if start < self.oldest_history:
                return httpx.Response(
                    404, json={"error": {"message": "Requested entity was not found."}}
                )
            records = [r for r in self.history if int(r["id"]) > start]
            return httpx.Response(200, json={"history": records, "historyId": str(self.history_id)})
        return httpx.Response(404, json={"error": {"message": f"fake: no route {key}"}})

    def _write(self, method: str, path: str, body: Any) -> httpx.Response:
        if path.startswith("/drafts"):
            return self._draft(method, path.removeprefix("/drafts").strip("/"), body)
        if path == "/messages/send":
            raw = base64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4))
            sent_id = self._next_id("s")
            self.add(sent_id, self._next_id("t"), ["SENT"], raw)
            self.sent.append(raw)
            return httpx.Response(200, json=self._ref(sent_id))
        if path == "/messages/batchModify":
            if any(i not in self.messages for i in body["ids"]):
                return httpx.Response(400, json={"error": {"message": "Invalid id value"}})
            for gmail_id in body["ids"]:
                labels = self.messages[gmail_id]["labelIds"]
                add = tuple(a for a in body.get("addLabelIds", []) if a not in labels)
                remove = tuple(r for r in body.get("removeLabelIds", []) if r in labels)
                if add or remove:
                    self.relabel(gmail_id, add, remove)
            return httpx.Response(204)
        if path.startswith("/messages/") and path.endswith(("/trash", "/untrash")):
            gmail_id, _, op = path.removeprefix("/messages/").partition("/")
            if gmail_id not in self.messages:
                return httpx.Response(404, json={"error": {"message": "Not Found"}})
            in_trash = "TRASH" in self.messages[gmail_id]["labelIds"]
            if op == "trash" and not in_trash:
                self.relabel(gmail_id, add=("TRASH",))
            elif op == "untrash" and in_trash:
                self.relabel(gmail_id, remove=("TRASH",))
            return httpx.Response(200, json=self._ref(gmail_id))
        if path == "/labels" and method == "POST":
            if any(label["name"].lower() == body["name"].lower() for label in self.labels):
                return httpx.Response(409, json={"error": {"message": "Label name exists"}})
            label = {"id": f"Label_{len(self.labels) + 1}", "type": "user", **body}
            self.labels.append(label)
            return httpx.Response(200, json=label)
        if path.startswith("/labels/"):
            label = next((x for x in self.labels if x["id"] == path.removeprefix("/labels/")), None)
            if label is None:
                return httpx.Response(404, json={"error": {"message": "Not Found"}})
            if method == "DELETE":
                self.labels.remove(label)
                for message in self.messages.values():
                    if label["id"] in message["labelIds"]:
                        message["labelIds"].remove(label["id"])
                return httpx.Response(204)
            label.update(body)
            return httpx.Response(200, json=label)
        return httpx.Response(404, json={"error": {"message": f"fake: no route {method} {path}"}})

    def _draft(self, method: str, draft_id: str, body: Any) -> httpx.Response:
        """Like Gmail: an update replaces the draft's message; send deletes it, adds a new one."""
        not_found = httpx.Response(
            404, json={"error": {"message": "Requested entity was not found."}}
        )
        if draft_id == "send":
            message_id = self.drafts.pop(body["id"], None)
            if message_id is None:
                return not_found
            draft = self.messages[message_id]
            self.delete(message_id)
            sent_id = self._next_id("s")
            self.add(sent_id, draft["threadId"], ["SENT"], draft["raw"])
            self.sent.append(draft["raw"])
            return httpx.Response(200, json=self._ref(sent_id))
        if draft_id and draft_id not in self.drafts:
            return not_found
        if method == "DELETE":
            self.delete(self.drafts.pop(draft_id))
            return httpx.Response(204)
        thread = body["metadata"]["message"].get("threadId")
        if thread and not any(m["threadId"] == thread for m in self.messages.values()):
            return not_found
        if draft_id:  # update
            self.delete(self.drafts[draft_id])
        else:
            draft_id = self._next_id("r")
        message_id = self._next_id("d")
        self.add(message_id, thread or self._next_id("t"), ["DRAFT"], body["raw"])
        self.drafts[draft_id] = message_id
        return httpx.Response(200, json={"id": draft_id, "message": self._ref(message_id)})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def _multipart(request: httpx.Request) -> dict[str, Any]:
    boundary = request.headers["content-type"].split("boundary=", 1)[1].encode()
    parts = request.content.split(b"--" + boundary)
    metadata = parts[1].split(b"\r\n\r\n", 1)[1].strip()
    raw = parts[2].split(b"\r\n\r\n", 1)[1].removesuffix(b"\r\n")
    return {"metadata": json.loads(metadata), "raw": raw}
