"""Reading real-world mail shapes (tests/mails/*.eml): bodies, character sets, headers and attachments."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from gmail_mcp.message import contents, drop_quoted, header, parse, render, sent_at

MAILS = Path(__file__).parent / "mails"
NY = ZoneInfo("America/New_York")


def mail(name: str):
    return parse((MAILS / name).read_bytes())


def test_encoded_headers_and_quoted_printable_body() -> None:
    msg = mail("plain_qp.eml")
    assert header(msg, "Subject") == "Grüße aus dem Garten"
    assert header(msg, "From") == "Jürgen Müller <juergen@example.com>"
    assert contents(msg).body == "Die Tomaten sind reif 🍅 und die Kürbisse auch.\nBis Samstag!"


def test_plain_text_wins_over_html() -> None:
    c = contents(mail("alternative.eml"))
    assert c.body == "Order 42: 3 bags of mulch."
    assert c.attachments == []  # the HTML version is the same text, not an attachment


def test_html_only_is_converted_to_markdown() -> None:
    body = contents(mail("html_only.eml")).body
    assert "# November news" in body and "[the full story](https://example.com/n)" in body
    assert "| Bulbs | $12 |" in body
    assert "<" not in body and "color: red" not in body  # no tags, no styles


def test_attachments_are_numbered_with_names_and_types() -> None:
    c = contents(mail("mixed_attachments.eml"))
    assert c.body == "Your invoice is attached."
    assert [a.describe() for a in c.attachments] == [
        "1. logo.png (28 bytes, image/png, inline)",
        "2. Kosten März.csv (19 bytes, text/csv)",  # an RFC 2231 encoded file name
        "3. attachment-3.bin (4 bytes, application/octet-stream)",  # no name at all
    ]
    assert c.attachments[1].data() == b"item,cost\nmulch,36\n"


def test_a_forwarded_mail_is_one_attachment() -> None:
    c = contents(mail("forwarded.eml"))
    assert c.body == "See below."  # the forwarded text stays inside the attachment
    [fwd] = c.attachments
    assert (fwd.name, fwd.content_type) == ("Original.eml", "message/rfc822")
    assert fwd.forwarded is not None
    inner = render(fwd.forwarded, NY)
    assert "From: Pat <pat@example.com>" in inner and "The inner text." in inner
    assert "Date: 2026-11-09 12:00" in inner and "- 1. inner.txt (16 bytes, text/plain)" in inner


def test_text_between_inline_pictures_is_kept() -> None:
    c = contents(mail("apple_split.eml"))
    assert c.body == "Part one.\n\nPart two."
    assert [a.name for a in c.attachments] == ["photo.jpg"]


def test_a_calendar_invite_is_an_attachment() -> None:
    c = contents(mail("calendar.eml"))
    assert c.body == "You are invited."
    [ics] = c.attachments
    assert ics.name == "attachment-1.ics" and b"SUMMARY:Dentist" in ics.data()


def test_no_text_at_all() -> None:
    text = render(mail("only_attachment.eml"), NY)
    assert "(no text)" in text and "- 1. scan.pdf (5 bytes, application/pdf)" in text


@pytest.mark.parametrize(
    ("name", "body"),
    [("latin1.eml", "Grüße, Ihr Kontoauszug für März."), ("unknown_charset.eml", "Still readable.")],
)
def test_character_sets(name: str, body: str) -> None:
    assert contents(mail(name)).body == body


def test_a_broken_mail_still_reads() -> None:
    msg = mail("broken.eml")
    assert sent_at(msg) is None
    text = render(msg, NY)
    assert text.startswith("Subject: (no subject)\nFrom: ")
    assert "Date:" not in text and "Body survives." in text


def test_render_shows_the_received_time_in_the_zone() -> None:
    text = render(mail("alternative.eml"), NY)
    assert text.splitlines()[:5] == [
        "Subject: Your order",
        "From: Shop <shop@example.com>",
        "To: me@example.com",
        "Cc: Sam <sam@example.com>",
        "Date: 2026-11-10 03:30",  # sent 08:30 UTC
    ]


def test_plain_text_loses_pasted_pictures_and_padding() -> None:
    raw = (
        "From: a@example.com\nSubject: Hi\nContent-Type: text/plain; charset=utf-8\n\n"
        "Hello͏‌ [data:image/png;base64," + "iVBORw0KGgo" * 100 + "] bye\n"
    ).encode()
    assert contents(parse(raw)).body == "Hello [image] bye"


GMAIL_REPLY = """Sounds good, see you Saturday.

My list for the trip:

- tent

On Thu, Mar 23, 2023 at 4:33 PM Pat Example <pat@example.com>
wrote:

> Want to go camping?
>
> > Earlier: the lake is open again.
"""


@pytest.mark.parametrize(
    ("text", "kept", "dropped"),
    [
        (GMAIL_REPLY, "Sounds good, see you Saturday.\n\nMy list for the trip:\n\n- tent", 5),
        (
            "Yes.\n\nFrom: Pat <pat@example.com>\nSent: Monday, March 3\nTo: me\n\nWant to go?",
            "Yes.",
            5,
        ),
        ("Ja.\n\n**Von:** Pat\n**Gesendet:** Montag\n\nKommst du?", "Ja.", 4),
        ("Fine.\n-----Original Message-----\nFrom: Pat\nWant to go?", "Fine.", 3),
        ("> Lake or sea?\nLake.\n> When?\nSaturday.", "Lake.\nSaturday.", 2),  # inline answers stay
        ("Two options:\n\n1. lake\n2. sea", "Two options:\n\n1. lake\n2. sea", 0),  # a colon alone is no quote
        ("From: a weekly digest\nnothing else", "From: a weekly digest\nnothing else", 0),
    ],
)
def test_drop_quoted(text: str, kept: str, dropped: int) -> None:
    assert drop_quoted(text) == (kept, dropped)


def test_render_without_quotes_says_so() -> None:
    raw = f"From: a@example.com\nSubject: Re: Trip\nContent-Type: text/plain\n\n{GMAIL_REPLY}".encode()
    text = render(parse(raw), NY, quotes=False)
    assert "Want to go camping" not in text
    assert text.endswith("- tent\n\n[5 quoted line(s) left out; mail_get shows the message in full]")
