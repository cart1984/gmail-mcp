"""The time zone every date is shown and given in.

From graph-mcp: `system_timezone` and `resolve_timezone` from its cli.py, `parse_local` from its server.py, unchanged
except for the setting's name. New: `from_epoch_ms` and `when`, for Gmail's times.
"""

from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def system_timezone() -> str | None:
    """The system's zone name from the /etc/localtime link (Linux, macOS), or None."""
    target = os.path.realpath("/etc/localtime")
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else None


def resolve_timezone() -> str:
    """GMAIL_MCP_TIMEZONE, else TZ, else the system's zone, else UTC. Unknown names are an error, not a silent UTC."""
    name = os.environ.get("GMAIL_MCP_TIMEZONE") or os.environ.get("TZ", "").lstrip(":") or system_timezone() or "UTC"
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(
            f"Unknown time zone {name!r}: use a name like America/New_York (GMAIL_MCP_TIMEZONE or TZ)."
        ) from e
    return name


def parse_local(value: str, name: str, timezone: str) -> dt.datetime:
    """An ISO date or date-time as an aware datetime. Without an offset it's taken in `timezone` (a date: midnight)."""
    try:
        d = dt.datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"{name} must be an ISO date or date-time, e.g. 2026-11-16 or 2026-11-16T09:00") from e
    return d if d.tzinfo else d.replace(tzinfo=ZoneInfo(timezone))


def from_epoch_ms(value: str | int | None) -> dt.datetime | None:
    """Gmail's `internalDate` (milliseconds since 1970, as a string) as an aware datetime; None if missing."""
    try:
        return dt.datetime.fromtimestamp(int(value or "") / 1000, dt.UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def when(d: dt.datetime | None, zone: ZoneInfo) -> str:
    """`2026-11-10 03:30` in `zone`, or "" for no date."""
    return d.astimezone(zone).strftime("%Y-%m-%d %H:%M") if d else ""
