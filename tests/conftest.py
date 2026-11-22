"""Shared test setup: a fake Gmail API that only answers GET."""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from mcp.client import Client
from mcp.types import TextContent

from gmail_mcp.gmail import Gmail
from gmail_mcp.server import build_server

BASE = "/gmail/v1/users/me"

MAILS = Path(__file__).parent / "mails"
RECEIVED = "1794299400000"  # 2026-11-10 08:30 UTC, in ms


def _raw(name: str) -> str:
    return base64.urlsafe_b64encode((MAILS / name).read_bytes()).decode().rstrip("=")


def _message(id_: str, mail: str, subject: str, labels: list[str]) -> Callable[[httpx.Request], dict]:
    """A message in the format the request asks for: metadata (headers and snippet) or raw (the whole mail)."""

    def answer(request: httpx.Request) -> dict:
        base = {"id": id_, "threadId": "t1", "labelIds": labels, "internalDate": RECEIVED}
        if request.url.params.get("format") == "raw":
            return {**base, "raw": _raw(mail)}
        headers = [{"name": "From", "value": "Shop <shop@example.com>"}, {"name": "Subject", "value": subject}]
        return {
            **base,
            "snippet": "Your invoice is attached &amp; paid\u034f \u200c\u034f",
            "payload": {"headers": headers},
        }

    return answer


LABELS = [
    {"id": "Label_1", "name": "Receipts", "type": "user"},
    {"id": "SENT", "name": "SENT", "type": "system"},
    {"id": "CATEGORY_PROMOTIONS", "name": "CATEGORY_PROMOTIONS", "type": "system"},
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "UNREAD", "name": "UNREAD", "type": "system"},
    {"id": "Label_2", "name": "Bills", "type": "user"},  # sorts before the categories by name, but comes after them
    {"id": "CHAT", "name": "CHAT", "type": "system"},  # an empty system label: not shown
]
COUNTS = {
    "INBOX": (2, 1234),
    "SENT": (0, 300),
    "UNREAD": (5, 5),
    "Label_1": (1, 40),
    "Label_2": (0, 0),
    "CATEGORY_PROMOTIONS": (0, 77),
    "CHAT": (0, 0),
}

# Path below users/me → the JSON answer, or a function of the request for answers that depend on the parameters.
ROUTES: dict[str, object] = {
    "/profile": {"emailAddress": "test@gmail.com", "messagesTotal": 1200, "threadsTotal": 800},
    "/labels": {"labels": LABELS},
    **{
        f"/labels/{lb['id']}": {**lb, "messagesUnread": COUNTS[lb["id"]][0], "messagesTotal": COUNTS[lb["id"]][1]}
        for lb in LABELS
    },
    "/messages": {"messages": [{"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t1"}]},
    "/messages/m1": _message("m1", "mixed_attachments.eml", "Invoice", ["INBOX", "UNREAD", "Label_1"]),
    "/messages/m2": _message("m2", "forwarded.eml", "Fwd: Original", ["INBOX"]),
    "/threads/t1": {"id": "t1", "messages": [{"id": "m1"}, {"id": "m2"}]},
}


class FakeGmail:
    """Answers from ROUTES by path; anything else is a Gmail-style 404. Any method but GET fails the test."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", f"the server sent {request.method} {request.url}"
        self.requests.append(request)
        path = request.url.path.removeprefix(BASE)
        body = ROUTES.get(path)
        if callable(body):
            body = body(request)
        if body is None:
            error = {"code": 404, "message": "Requested entity was not found.", "status": "NOT_FOUND"}
            return httpx.Response(404, json={"error": error})
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"content-type": "application/json"})


def make_gmail(handler: Callable[[httpx.Request], httpx.Response], token: str = "tok") -> Gmail:
    return Gmail(lambda: token, httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def fake() -> FakeGmail:
    return FakeGmail()


Call = Callable[..., Awaitable[tuple[bool, str]]]


@pytest.fixture
def call(fake: FakeGmail) -> Call:
    """`await call(tool, **args)`: runs one tool through an MCP client. Options: `gmail=` (default: the fake),
    `downloads=` (default: keep nothing), `server_tz=` (default: UTC)."""

    async def run(
        tool: str, *, gmail: Gmail | None = None, downloads: Path | None = None, server_tz: str = "UTC", **args: object
    ) -> tuple[bool, str]:
        server = build_server(gmail or make_gmail(fake), downloads, server_tz)
        async with Client(server, mode="legacy") as client:
            result = await client.call_tool(tool, args)
        content = result.content[0]
        assert isinstance(content, TextContent)
        return bool(result.is_error), content.text

    return run
