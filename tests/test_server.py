"""The 7 tools end to end through a real MCP client, in-process, against the fake Gmail in conftest.py."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mcp.client import Client

from conftest import Call, FakeGmail, make_gmail
from gmail_mcp.auth import NotSignedIn
from gmail_mcp.gmail import Gmail
from gmail_mcp.server import build_server

pytestmark = pytest.mark.anyio

TOOLS = {"whoami", "mail_labels", "mail_search", "mail_list", "mail_get", "mail_thread", "mail_attachment"}

# ---- the read-only guarantees ----


async def test_exactly_these_tools_all_read_only(fake: FakeGmail) -> None:
    async with Client(build_server(make_gmail(fake)), mode="legacy") as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS  # a new tool must be added here on purpose, after review
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


async def test_whoami_sends_bearer_token(fake: FakeGmail, call: Call) -> None:
    err, text = await call("whoami", gmail=make_gmail(fake, "secret-token"))
    assert not err and text == "test@gmail.com (1,200 messages, 800 threads)"
    assert fake.requests[0].headers["authorization"] == "Bearer secret-token"


# ---- errors ----


async def test_gmail_errors_become_tool_errors(call: Call) -> None:
    err, text = await call("mail_get", message_id="missing")
    assert err and "404" in text and "NOT_FOUND" in text


async def test_not_signed_in_is_a_tool_error(fake: FakeGmail, call: Call) -> None:
    def no_token() -> str:
        raise NotSignedIn("Not signed in to Google. Run `gmail-mcp login` in a terminal.")

    gmail = Gmail(no_token, httpx.Client(transport=httpx.MockTransport(fake)))
    err, text = await call("whoami", gmail=gmail)
    assert err and "gmail-mcp login" in text
    assert fake.requests == []


async def test_ids_are_url_quoted(fake: FakeGmail, call: Call) -> None:
    await call("mail_attachment", message_id="a/b?c", attachment=1)
    assert fake.requests[0].url.raw_path.startswith(b"/gmail/v1/users/me/messages/a%2Fb%3Fc")


# ---- labels ----


async def test_mail_labels_system_first_with_counts(call: Call) -> None:
    err, text = await call("mail_labels")
    assert not err
    assert text.splitlines() == [
        "- INBOX: 2 unread / 1,234 total",
        "- SENT: 0 unread / 300 total",
        "- UNREAD: 5 unread / 5 total",
        "- CATEGORY_PROMOTIONS: 0 unread / 77 total",
        "- Bills: 0 unread / 0 total (id: Label_2)",  # your own labels show even when empty
        "- Receipts: 1 unread / 40 total (id: Label_1)",
    ]


# ---- listing and searching ----


async def test_mail_search(fake: FakeGmail, call: Call) -> None:
    err, text = await call("mail_search", query="from:shop invoice", top=500)
    assert not err
    assert text.splitlines()[:3] == [
        "- 2026-11-10 08:30 | Shop <shop@example.com> | Invoice [unread]",
        "  id: m1 thread: t1",
        "  Your invoice is attached & paid",  # Gmail's snippets are HTML-escaped, and padded in newsletters
    ]
    assert "Fwd: Original\n" in text  # m2, not unread
    search = fake.requests[0]
    assert search.url.params["q"] == "from:shop invoice" and search.url.params["maxResults"] == "50"  # capped
    meta = fake.requests[1]
    assert meta.url.params["format"] == "metadata"
    assert meta.url.params.get_list("metadataHeaders") == ["From", "Subject", "Date"]


async def test_mail_search_nothing_found(call: Call) -> None:
    def nothing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resultSizeEstimate": 0})

    err, text = await call("mail_search", gmail=make_gmail(nothing), query="xyzzy")
    assert not err and text == "No messages found."


async def test_mail_list_by_label_name(fake: FakeGmail, call: Call) -> None:
    err, text = await call("mail_list", label="receipts", unread_only=True)
    assert not err and "Invoice" in text
    listing = next(r for r in fake.requests if r.url.path.endswith("/messages"))
    assert listing.url.params.get_list("labelIds") == ["Label_1", "UNREAD"]
    assert "q" not in listing.url.params


@pytest.mark.parametrize(
    ("since", "after"),
    [
        ("2026-11-10", 1794286800),  # local midnight, EST = 05:00 UTC
        ("2026-11-10T09:30+01:00", 1794299400),  # an explicit offset wins: 08:30 UTC
    ],
)
async def test_mail_list_since_is_local(fake: FakeGmail, call: Call, since: str, after: int) -> None:
    err, _ = await call("mail_list", server_tz="America/New_York", since=since)
    assert not err
    listing = next(r for r in fake.requests if r.url.path.endswith("/messages"))
    assert listing.url.params["q"] == f"after:{after}"
    assert listing.url.params.get_list("labelIds") == ["INBOX"]


async def test_mail_list_rejects_anything_but_a_date(fake: FakeGmail, call: Call) -> None:
    err, text = await call("mail_list", since="2026-01-01 OR in:anywhere")
    assert err and "since must be an ISO date" in text
    assert not any(r.url.path.endswith("/messages") for r in fake.requests)  # never reaches the query


async def test_mail_list_unknown_label(call: Call) -> None:
    err, text = await call("mail_list", label="Taxes")
    assert err and "No label 'Taxes'. Labels: Bills, CATEGORY_PROMOTIONS, CHAT, INBOX, Receipts, SENT, UNREAD" in text


# ---- reading ----


async def test_mail_get(fake: FakeGmail, call: Call) -> None:
    err, text = await call("mail_get", message_id="m1", server_tz="America/New_York")
    assert not err
    lines = text.splitlines()
    assert lines[0] == "Message id: m1 | thread: t1 | labels: INBOX, UNREAD, Receipts"
    assert lines[1:5] == [
        "Subject: Invoice",
        "From: Shop <shop@example.com>",
        "To: me@example.com",
        "Date: 2026-11-10 03:30",
    ]
    assert "Your invoice is attached." in text
    assert "- 2. Kosten März.csv (19 bytes, text/csv)" in text
    assert any(r.url.params.get("format") == "raw" for r in fake.requests)


async def test_mail_thread(call: Call) -> None:
    err, text = await call("mail_thread", thread_id="t1")
    assert not err
    first, second = text.index("=== Message 1 of 2 ==="), text.index("=== Message 2 of 2 ===")
    assert first < text.index("Subject: Invoice") < second < text.index("Subject: Fwd: Original")
    assert "See below." in text


async def test_mail_attachment_is_converted_in_memory(
    call: Call, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    err, text = await call("mail_attachment", message_id="m1", attachment=2)
    assert not err
    assert text.startswith("Kosten März.csv (19 bytes).") and "mulch" in text
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere by default


async def test_mail_attachment_is_saved_when_downloads_set(call: Call, tmp_path: Path) -> None:
    err, text = await call("mail_attachment", downloads=tmp_path, message_id="m1", attachment=2)
    assert not err
    saved = tmp_path / "mail" / "Kosten März.csv"
    assert saved.read_bytes() == b"item,cost\nmulch,36\n"
    assert str(saved) in text


async def test_mail_attachment_forwarded_mail(call: Call) -> None:
    err, text = await call("mail_attachment", message_id="m2", attachment=1)
    assert not err
    assert text.startswith("Original.eml, a forwarded message:")
    assert "From: Pat <pat@example.com>" in text and "The inner text." in text


@pytest.mark.parametrize("number", [0, 4])
async def test_mail_attachment_number_out_of_range(call: Call, number: int) -> None:
    err, text = await call("mail_attachment", message_id="m1", attachment=number)
    assert err and "The message has 3 attachment(s): use 1 to 3." in text


async def test_instructions_name_the_time_zone(fake: FakeGmail) -> None:
    server = build_server(make_gmail(fake), timezone="Asia/Tokyo")
    assert "All dates and times, shown and given, are in Asia/Tokyo" in (server.instructions or "")
