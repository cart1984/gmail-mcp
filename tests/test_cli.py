from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gmail_mcp import cli
from gmail_mcp.auth import Auth, NotSignedIn
from gmail_mcp.store import FileStore


class StubAuth:
    def __init__(self, user: str | None, valid: bool = True) -> None:
        self.user, self.valid, self.logged_out = user, valid, False
        self.store = type("Store", (), {"describe": lambda self: "file /x/cache.json"})()

    def account(self) -> str | None:
        return self.user

    def token(self) -> str:
        if not self.valid:
            raise NotSignedIn("revoked")
        return "t"

    def logout(self) -> None:
        self.logged_out = True


@pytest.mark.parametrize(
    ("auth", "code", "message"),
    [
        (StubAuth("me@gmail.com"), 0, "Signed in as me@gmail.com; token is valid."),
        (StubAuth(None), 1, "Not signed in. Run `gmail-mcp login`."),
        (StubAuth("me@gmail.com", valid=False), 1, "but the sign-in no longer works: revoked"),
    ],
)
def test_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], auth: StubAuth, code: int, message: str
) -> None:
    monkeypatch.setattr(cli, "make_auth", lambda: auth)
    assert cli.main(["status"]) == code
    out = capsys.readouterr().out
    assert "Sign-in kept in: file /x/cache.json" in out and message in out


def test_logout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    auth = StubAuth("me@gmail.com")
    monkeypatch.setattr(cli, "make_auth", lambda: auth)
    assert cli.main(["logout"]) == 0
    out = capsys.readouterr().out
    assert auth.logged_out and "Signed out" in out and "myaccount.google.com/connections" in out


def test_login_options(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(Auth, "login", lambda self, **kw: calls.append(kw) or "me@gmail.com")
    monkeypatch.setenv("GMAIL_MCP_CLIENT_ID", "id")
    assert cli.main(["login"]) == 0
    assert cli.main(["login", "--no-browser", "--port", "8780", "--bind", "0.0.0.0"]) == 0
    assert calls == [
        {"open_browser": True, "port": 0, "bind": "localhost"},
        {"open_browser": False, "port": 8780, "bind": "0.0.0.0"},
    ]
    assert "Signed in as me@gmail.com" in capsys.readouterr().out


def test_no_command_serves_over_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[str] = []
    monkeypatch.setattr(cli, "cmd_serve", lambda args: served.append(args.transport) or 0)
    assert cli.main([]) == 0
    assert served == ["stdio"]


def test_unknown_timezone_stops_serve(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("GMAIL_MCP_TIMEZONE", "Mars/Olympus")
    assert cli.main(["serve"]) == 2
    assert "Unknown time zone 'Mars/Olympus'" in capsys.readouterr().err


def test_http_without_token_is_refused(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("GMAIL_MCP_TIMEZONE", "UTC")
    monkeypatch.delenv("GMAIL_MCP_TOKEN", raising=False)
    assert cli.main(["serve", "--transport", "http", "--host", "0.0.0.0"]) == 2
    assert "HTTP needs a token" in capsys.readouterr().err


def test_bad_token_cache_setting(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("GMAIL_MCP_TOKEN_CACHE", "cloud")
    assert cli.main(["status"]) == 2
    assert "GMAIL_MCP_TOKEN_CACHE must be auto, keyring or file" in capsys.readouterr().err


def test_serve_logs_no_line_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging.getLogger("httpx"), "level", logging.NOTSET)
    monkeypatch.setenv("GMAIL_MCP_TIMEZONE", "Mars/Olympus")  # stops serve right after the logging is set up
    cli.main(["serve"])
    assert logging.getLogger("httpx").level == logging.WARNING


def test_make_auth_reads_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GMAIL_MCP_CLIENT_ID", "abc")
    monkeypatch.setenv("GMAIL_MCP_CLIENT_SECRET", "shh")
    monkeypatch.setenv("GMAIL_MCP_TOKEN_CACHE", "file")
    monkeypatch.setenv("GMAIL_MCP_CACHE_FILE", str(tmp_path / "c.json"))
    auth = cli.make_auth()
    assert (auth.client_id, auth.client_secret) == ("abc", "shh")
    assert isinstance(auth.store, FileStore) and auth.store.path == tmp_path / "c.json"
