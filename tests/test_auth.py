"""Google sign-in and token refresh, with Google's libraries replaced by fakes: nothing here touches the network."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials

import gmail_mcp.auth as auth_module
from gmail_mcp.auth import SCOPES, Auth, AuthError, NotSignedIn, profile_email
from gmail_mcp.store import FileStore


def test_the_only_scope_is_gmail_read_only() -> None:
    assert SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def _auth(tmp_path: Path, saved: dict | None = None, client_id: str = "id", secret: str = "secret") -> Auth:
    store = FileStore(tmp_path / "c.json")
    if saved is not None:
        store.save(json.dumps(saved))
    return Auth(client_id=client_id, client_secret=secret, store=store)


SAVED = {"account": "me@gmail.com", "refresh_token": "r1", "scopes": SCOPES}


@pytest.mark.parametrize(("client_id", "secret", "missing"), [("", "s", "CLIENT_ID"), ("id", "", "CLIENT_SECRET")])
def test_missing_client_settings(tmp_path: Path, client_id: str, secret: str, missing: str) -> None:
    auth = _auth(tmp_path, SAVED, client_id, secret)
    with pytest.raises(AuthError, match=f"GMAIL_MCP_{missing} is not set"):
        auth.token()
    with pytest.raises(AuthError, match=f"GMAIL_MCP_{missing} is not set"):
        auth.login()


def test_account_reads_the_saved_sign_in_only(tmp_path: Path) -> None:
    assert _auth(tmp_path).account() is None
    assert _auth(tmp_path, SAVED).account() == "me@gmail.com"
    assert _auth(tmp_path, {"account": "me@gmail.com"}).account() is None  # no refresh token: not signed in


def test_unreadable_sign_in(tmp_path: Path) -> None:
    (tmp_path / "c.json").write_text("not json")
    with pytest.raises(AuthError, match="unreadable"):
        _auth(tmp_path).account()


# ---- token refresh ----


def _in_an_hour() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(hours=1)  # google-auth uses naive UTC


@pytest.fixture
def refreshes(monkeypatch: pytest.MonkeyPatch) -> list[Credentials]:
    """Replaces Google's token endpoint: every refresh hands out `access-<n>`, valid for an hour."""
    done: list[Credentials] = []

    def refresh(self: Credentials, request: object) -> None:
        done.append(self)
        self.token = f"access-{len(done)}"
        self.expiry = _in_an_hour()

    monkeypatch.setattr(Credentials, "refresh", refresh)
    return done


def test_token_is_refreshed_once_and_kept_in_memory(tmp_path: Path, refreshes: list[Credentials]) -> None:
    auth = _auth(tmp_path, SAVED)
    assert auth.token() == "access-1"
    assert auth.token() == "access-1"  # still valid: no second refresh
    creds = refreshes[0]
    assert (creds.refresh_token, creds.client_id, creds.client_secret) == ("r1", "id", "secret")
    assert creds.scopes == SCOPES
    assert json.loads(auth.store.load() or "") == SAVED  # access tokens are never saved


def test_expired_token_is_refreshed_again(tmp_path: Path, refreshes: list[Credentials]) -> None:
    auth = _auth(tmp_path, SAVED)
    auth.token()
    refreshes[0].expiry = dt.datetime(2000, 1, 1)
    assert auth.token() == "access-2"


def test_not_signed_in(tmp_path: Path, refreshes: list[Credentials]) -> None:
    with pytest.raises(NotSignedIn, match="gmail-mcp login"):
        _auth(tmp_path).token()
    assert refreshes == []


def test_revoked_sign_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(self: Credentials, request: object) -> None:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", refused)
    with pytest.raises(NotSignedIn, match=r"expired or was revoked .*invalid_grant.*gmail-mcp login.*Testing"):
        _auth(tmp_path, SAVED).token()


def test_google_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(self: Credentials, request: object) -> None:
        raise TransportError("no route to host")

    monkeypatch.setattr(Credentials, "refresh", offline)
    with pytest.raises(AuthError, match="Couldn't reach Google"):
        _auth(tmp_path, SAVED).token()


def test_a_new_refresh_token_is_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def rotates(self: Credentials, request: object) -> None:
        self.token, self.expiry, self._refresh_token = "a", _in_an_hour(), "r2"

    monkeypatch.setattr(Credentials, "refresh", rotates)
    auth = _auth(tmp_path, SAVED)
    auth.token()
    assert json.loads(auth.store.load() or "") == {**SAVED, "refresh_token": "r2"}


def test_parallel_token_calls_refresh_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    running, most, calls = 0, 0, 0
    guard = threading.Lock()

    def slow(self: Credentials, request: object) -> None:
        nonlocal running, most, calls
        with guard:
            running, calls = running + 1, calls + 1
            most = max(most, running)
        time.sleep(0.02)
        with guard:
            running -= 1
        self.token, self.expiry = "t", _in_an_hour()

    monkeypatch.setattr(Credentials, "refresh", slow)
    auth = _auth(tmp_path, SAVED)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: auth.token(), range(8))) == ["t"] * 8
    assert (calls, most) == (1, 1)


# ---- sign-in ----


class FakeFlow:
    """Stands in for InstalledAppFlow: run_local_server returns `creds` (or raises `error`) and records its kwargs."""

    def __init__(self, creds: Credentials | None = None, error: Exception | None = None) -> None:
        self.creds, self.error = creds, error
        self.config: dict = {}
        self.scopes: list[str] = []
        self.kwargs: dict = {}

    def from_client_config(self, config: dict, scopes: list[str]) -> FakeFlow:
        self.config, self.scopes = config, scopes
        return self

    def run_local_server(self, **kwargs: object) -> Credentials:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        assert self.creds is not None
        return self.creds


def test_login_saves_only_the_refresh_token_and_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flow = FakeFlow(Credentials("access", refresh_token="r1", expiry=_in_an_hour()))
    monkeypatch.setattr(auth_module, "InstalledAppFlow", flow)
    auth = _auth(tmp_path)
    seen: list[str] = []
    assert auth.login(open_browser=False, port=8780, bind="0.0.0.0", email_of=lambda t: seen.append(t) or "me@x") == (
        "me@x"
    )
    assert seen == ["access"]
    assert json.loads(auth.store.load() or "") == {"account": "me@x", "refresh_token": "r1", "scopes": SCOPES}
    assert flow.scopes == SCOPES
    assert flow.config["installed"]["client_id"] == "id" and flow.config["installed"]["client_secret"] == "secret"
    assert flow.kwargs["prompt"] == "consent" and flow.kwargs["host"] == "localhost"
    assert (flow.kwargs["port"], flow.kwargs["bind_addr"], flow.kwargs["open_browser"]) == (8780, "0.0.0.0", False)
    assert auth.token() == "access"  # the new token is used right away


def test_login_failure_saves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "InstalledAppFlow", FakeFlow(error=TimeoutError("timed out")))
    auth = _auth(tmp_path)
    with pytest.raises(AuthError, match="Sign-in failed: timed out"):
        auth.login(email_of=lambda t: "me@x")
    assert auth.store.load() is None


def test_login_without_a_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "InstalledAppFlow", FakeFlow(error=webbrowser.Error("no runnable browser")))
    with pytest.raises(AuthError, match="--no-browser"):
        _auth(tmp_path).login(email_of=lambda t: "me@x")


def test_login_without_refresh_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "InstalledAppFlow", FakeFlow(Credentials("access")))
    auth = _auth(tmp_path)
    with pytest.raises(AuthError, match="no refresh token"):
        auth.login(email_of=lambda t: "me@x")
    assert auth.store.load() is None


def test_logout(tmp_path: Path, refreshes: list[Credentials]) -> None:
    auth = _auth(tmp_path, SAVED)
    auth.token()
    auth.logout()
    assert auth.store.load() is None
    with pytest.raises(NotSignedIn):
        auth.token()  # the token in memory is gone too


@pytest.mark.parametrize(("status", "ok"), [(200, True), (403, False)])
def test_profile_email(monkeypatch: pytest.MonkeyPatch, status: int, ok: bool) -> None:
    sent: list[dict] = []

    def get(url: str, headers: dict, timeout: float) -> httpx.Response:
        sent.append(headers)
        return httpx.Response(status, json={"emailAddress": "me@gmail.com"})

    monkeypatch.setattr(auth_module.httpx, "get", get)
    if ok:
        assert profile_email("tok") == "me@gmail.com"
    else:
        with pytest.raises(AuthError, match="box for reading your mail"):
            profile_email("tok")
    assert sent == [{"Authorization": "Bearer tok"}]
