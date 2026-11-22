"""The time zone. From graph-mcp's test_cli.py (time zone) and test_server.py (parse_local via mail_list)."""

from __future__ import annotations

import pytest

from gmail_mcp import times


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GMAIL_MCP_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(times, "system_timezone", lambda: "Europe/Berlin")


def test_timezone_setting_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_MCP_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("TZ", "America/Chicago")
    assert times.resolve_timezone() == "Asia/Tokyo"


def test_timezone_falls_back_to_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", ":America/Chicago")  # the leading colon is allowed in TZ
    assert times.resolve_timezone() == "America/Chicago"


def test_timezone_falls_back_to_system_then_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    assert times.resolve_timezone() == "Europe/Berlin"
    monkeypatch.setattr(times, "system_timezone", lambda: None)
    assert times.resolve_timezone() == "UTC"


def test_unknown_timezone_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_MCP_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValueError, match="Unknown time zone 'Mars/Olympus'"):
        times.resolve_timezone()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-10", "2026-11-10T00:00:00-05:00"),  # local midnight, EST
        ("2026-11-01", "2026-11-01T00:00:00-04:00"),  # the day summer time ends: midnight is still EDT
        ("2026-11-10T09:30", "2026-11-10T09:30:00-05:00"),
        ("2026-11-10T09:30+01:00", "2026-11-10T09:30:00+01:00"),  # an explicit offset wins
    ],
)
def test_parse_local(value: str, expected: str) -> None:
    assert times.parse_local(value, "since", "America/New_York").isoformat() == expected


def test_parse_local_rejects_anything_but_a_date() -> None:
    with pytest.raises(ValueError, match="since must be an ISO date"):
        times.parse_local("2026-01-01 or true", "since", "UTC")


def test_gmail_times_in_the_zone() -> None:
    zone = times.ZoneInfo("America/New_York")
    received = times.from_epoch_ms("1794299400000")  # 2026-11-10 08:30 UTC
    assert times.when(received, zone) == "2026-11-10 03:30"
    assert times.from_epoch_ms(None) is None and times.from_epoch_ms("soon") is None
    assert times.when(None, zone) == ""
