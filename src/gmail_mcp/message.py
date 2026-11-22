"""Reading a mail: its headers, its text, and its attachments, from the original RFC 822 bytes.

Messages are fetched in Gmail's `raw` format and parsed with Python's email package, which handles what real mail
throws at a reader: nested multipart structures, any character set, encoded headers and file names, forwarded
messages. Gmail's own `full` format would need all of that rebuilt by hand.

Which text is the body: the plain-text version if there is one, else the HTML one, converted to Markdown. Text parts
that sit between attachments (Apple Mail splits a message around inline pictures like that) are added to the body.
Everything else with a file name, or marked as an attachment, is an attachment, numbered from 1 in the order it
appears. A forwarded message attached whole is one attachment; its own text and attachments stay inside it.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from gmail_mcp.convert import tidy, to_text
from gmail_mcp.times import when

FORWARDED = "message/rfc822"


def parse(raw: bytes) -> EmailMessage:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    return msg


def header(msg: EmailMessage, name: str) -> str:
    """A header, decoded (`=?utf-8?q?...?=` becomes text). A header too broken to decode comes back as it is."""
    try:
        return " ".join(str(msg.get(name) or "").split())
    except Exception:  # the email package can fail on malformed addresses and encodings
        for key, value in msg.raw_items():
            if key.lower() == name.lower():
                return " ".join(str(value).split())
        return ""


def sent_at(msg: EmailMessage) -> dt.datetime | None:
    """The Date header as an aware datetime (UTC if it has no zone), or None if it's missing or unreadable."""
    try:
        d = parsedate_to_datetime(header(msg, "Date"))
    except (TypeError, ValueError, IndexError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


@dataclass
class Attachment:
    number: int
    name: str
    content_type: str
    size: int
    inline: bool
    part: EmailMessage

    @property
    def forwarded(self) -> EmailMessage | None:
        """The attached message, if this is a forwarded mail."""
        return _inner(self.part)

    def data(self) -> bytes:
        payload = self.part.get_payload(decode=True)
        return payload if isinstance(payload, bytes) else b""

    def describe(self) -> str:
        return (
            f"{self.number}. {self.name} ({self.size:,} bytes, {self.content_type}{', inline' if self.inline else ''})"
        )


@dataclass
class Contents:
    body: str
    attachments: list[Attachment]


def _inner(part: EmailMessage) -> EmailMessage | None:
    """The message inside a message/rfc822 part."""
    if part.get_content_type() != FORWARDED:
        return None
    inner = part.get_payload()
    if isinstance(inner, list) and inner and isinstance(inner[0], EmailMessage):
        return inner[0]
    return None


def _decoded_text(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:  # a charset Python doesn't know, e.g. `unknown-8bit`
        return payload.decode("utf-8", errors="replace")


def _as_text(part: EmailMessage) -> str:
    text = _decoded_text(part)
    if part.get_content_type() == "text/html":
        return to_text(text.encode("utf-8"), "message.html").strip()
    return tidy(text).strip()


def _size(part: EmailMessage) -> int:
    if part.get_content_type() == FORWARDED:
        try:
            return len(part.as_bytes())
        except Exception:
            return 0
    payload = part.get_payload(decode=True)
    return len(payload) if isinstance(payload, bytes) else 0


def _name(part: EmailMessage, number: int) -> str:
    try:
        name = part.get_filename()
    except Exception:
        name = None
    if name:
        return " ".join(name.split())
    if part.get_content_type() == FORWARDED:
        inner = _inner(part)
        subject = header(inner, "Subject") if inner else ""
        return f"{subject or 'forwarded message'}.eml"
    return f"attachment-{number}{mimetypes.guess_extension(part.get_content_type()) or '.bin'}"


def contents(msg: EmailMessage) -> Contents:
    """The message's text and its attachments (see the module docstring for which is which)."""
    try:
        body = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        body = None
    texts = [_as_text(body)] if isinstance(body, EmailMessage) else []
    attachments: list[Attachment] = []

    def visit(part: EmailMessage, parent: str) -> None:
        ctype = part.get_content_type()
        if part is body:
            return
        if ctype == FORWARDED:  # one attachment, not opened up
            n = len(attachments) + 1
            attachments.append(Attachment(n, _name(part, n), ctype, _size(part), False, part))
            return
        if part.is_multipart():
            for sub in part.iter_parts():
                if isinstance(sub, EmailMessage):
                    visit(sub, ctype)
            return
        disposition = part.get_content_disposition()
        try:
            has_name = bool(part.get_filename())
        except Exception:
            has_name = True
        if has_name or disposition == "attachment" or ctype not in ("text/plain", "text/html"):  # e.g. text/calendar
            n = len(attachments) + 1
            inline = disposition == "inline" or "Content-ID" in part
            attachments.append(Attachment(n, _name(part, n), ctype, _size(part), inline, part))
        elif ctype == "text/plain" and parent != "multipart/alternative" and (text := _as_text(part)):
            texts.append(text)  # more text between attachments; the other versions of the body are skipped

    visit(msg, "")
    return Contents("\n\n".join(t for t in texts if t), attachments)


# Where Outlook-style quoting starts: everything from here on is the earlier mail.
QUOTE_START = re.compile(r"\s*-{2,}\s*(Original Message|Ursprüngliche Nachricht|Forwarded message)\s*-{2,}\s*", re.I)
QUOTE_HEADER = re.compile(r"\*{0,2}(From|Von):\*{0,2} .+")
QUOTE_HEADER_NEXT = re.compile(r"\*{0,2}(Sent|Date|Gesendet|Datum):\*{0,2} .+")


def drop_quoted(text: str) -> tuple[str, int]:
    """`text` without the earlier mails it quotes, and how many lines were left out. Quotes are `>` lines with the
    "On ... wrote:" line before them, and Outlook's style: the rest of the mail from "-----Original Message-----", or
    from a "From: ..." line followed by "Sent: ..." or "Date: ...". Meant for replies whose earlier mails are shown
    anyway, as in a thread."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        follows = next((x for x in lines[i + 1 : i + 3] if x.strip()), "")
        if QUOTE_START.fullmatch(line) or (QUOTE_HEADER.fullmatch(line.strip()) and QUOTE_HEADER_NEXT.match(follows)):
            dropped = len(lines) - i
            lines = lines[:i]
            break
    else:
        dropped = 0
    kept: list[str] = []
    quoting = False
    for line in lines:
        if not line.lstrip().startswith(">"):
            kept.append(line)
            quoting = quoting and not line.strip()  # blank lines inside a quote don't end it
            continue
        dropped += 1
        if not quoting:  # a quote starts: drop the "On ..., X wrote:" paragraph before it (at most 3 lines)
            end = len(kept)
            while end > 0 and not kept[end - 1].strip():
                end -= 1
            start = end
            while start > 0 and kept[start - 1].strip() and end - start < 3:
                start -= 1
            whole = start == 0 or not kept[start - 1].strip()
            if end > start and whole and kept[end - 1].rstrip().endswith(":"):
                dropped += end - start
                del kept[start:end]
        quoting = True
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return body, dropped


def render(msg: EmailMessage, zone: ZoneInfo, received: dt.datetime | None = None, quotes: bool = True) -> str:
    """Headers, text and attachment list of one message. `received` (Gmail's own time) wins over the Date header.
    `quotes=False` leaves out the earlier mails a reply quotes (see `drop_quoted`)."""
    c = contents(msg)
    out = [f"Subject: {header(msg, 'Subject') or '(no subject)'}", f"From: {header(msg, 'From')}"]
    for name in ("To", "Cc"):
        if value := header(msg, name):
            out.append(f"{name}: {value}")
    if date := when(received or sent_at(msg), zone):
        out.append(f"Date: {date}")
    body = c.body
    if not quotes:
        body, dropped = drop_quoted(body)
        if dropped:
            body += f"\n\n[{dropped} quoted line(s) left out; mail_get shows the message in full]"
    out += ["", body or "(no text)"]
    if c.attachments:
        out.append("\nAttachments:")
        out += [f"- {a.describe()}" for a in c.attachments]
    return "\n".join(out)
