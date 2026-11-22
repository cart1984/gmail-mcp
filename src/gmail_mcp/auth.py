"""Signing in to Google in a browser, and getting access tokens from the saved sign-in.

The sign-in is Google's flow for installed apps: a browser opens Google's consent page, and Google redirects back to
a small web server this command runs on http://localhost:<port>/ (with PKCE). Google's device-code sign-in doesn't
allow Gmail scopes, so there's no device code. On a machine without a browser, `login --no-browser` prints the URL
to open elsewhere (see the README for containers and servers).

Only the refresh token and the account's address are saved (in the keychain or a 0600 file, see store.py). Access
tokens live in memory and are refreshed from the refresh token about once an hour.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gmail_mcp.store import TokenStore

# Read-only, and nothing but Gmail: no send, compose, modify, labels or settings scope.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

EXPIRED_HINT = (
    "If this happens about a week after every sign-in, the app is still in 'Testing' on Google's side: publish it "
    "(README, step 1)."
)


class NotSignedIn(Exception):
    pass


class AuthError(Exception):
    pass


def profile_email(access_token: str) -> str:
    """The signed-in Gmail address, read with the new token: also proves the token can read Gmail."""
    r = httpx.get(PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    if r.status_code != 200:
        raise AuthError(
            f"Signed in, but Gmail refused the token ({r.status_code}). On Google's consent page, the box for "
            "reading your mail must be ticked; run `gmail-mcp login` again."
        )
    return r.json()["emailAddress"]


@dataclass
class Auth:
    client_id: str
    client_secret: str
    store: TokenStore
    # Tools run in worker threads: the lock makes parallel calls share one refresh instead of each doing its own.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)
    _creds: Credentials | None = field(default=None, init=False, repr=False, compare=False)

    def _check_client(self) -> None:
        if not self.client_id:
            raise AuthError("GMAIL_MCP_CLIENT_ID is not set: the client ID of your Google OAuth client.")
        if not self.client_secret:
            raise AuthError("GMAIL_MCP_CLIENT_SECRET is not set: the client secret of your Google OAuth client.")

    def _saved(self) -> dict:
        data = self.store.load()
        if not data:
            return {}
        try:
            saved = json.loads(data)
        except ValueError as e:
            raise AuthError(
                f"The saved sign-in ({self.store.describe()}) is unreadable. Run `gmail-mcp logout`, then "
                "`gmail-mcp login`."
            ) from e
        return saved if isinstance(saved, dict) else {}

    def account(self) -> str | None:
        """The signed-in address from the saved sign-in, without asking Google; None if there's no sign-in."""
        saved = self._saved()
        return saved.get("account") if saved.get("refresh_token") else None

    def token(self) -> str:
        """An access token: the one in memory while it's valid, else a fresh one. Raises NotSignedIn if a new
        sign-in is needed."""
        with self._lock:  # the second caller then finds the token the first one refreshed
            if self._creds is None or not self._creds.valid:
                self._creds = self._refresh()
            return self._creds.token

    def _refresh(self) -> Credentials:
        self._check_client()
        saved = self._saved()
        if not saved.get("refresh_token"):
            raise NotSignedIn("Not signed in to Google. Run `gmail-mcp login` in a terminal.")
        creds = Credentials(
            None,
            refresh_token=saved["refresh_token"],
            token_uri=TOKEN_URI,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=SCOPES,
        )
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise NotSignedIn(
                f"The Google sign-in expired or was revoked ({e}). Run `gmail-mcp login` in a terminal. {EXPIRED_HINT}"
            ) from e
        except TransportError as e:
            raise AuthError(f"Couldn't reach Google to refresh the sign-in: {e}") from e
        if creds.refresh_token and creds.refresh_token != saved["refresh_token"]:  # Google rarely sends a new one
            self.store.save(json.dumps({**saved, "refresh_token": creds.refresh_token}))
        return creds

    def login(
        self,
        open_browser: bool = True,
        port: int = 0,
        bind: str = "localhost",
        timeout: int = 300,
        email_of: Callable[[str], str] = profile_email,
    ) -> str:
        """Browser sign-in. Prints the URL (and opens it if `open_browser`), waits for Google's redirect to
        http://localhost:<port>/, saves the sign-in and returns the address. `port` 0 picks a free one; `bind` is
        where the redirect is listened for (0.0.0.0 inside a container, whose port is published on loopback)."""
        self._check_client()
        config = {
            "installed": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(config, SCOPES)
        try:
            creds = flow.run_local_server(
                host="localhost",
                bind_addr=bind,
                port=port,
                open_browser=open_browser,
                authorization_prompt_message="Sign in to Google at this URL:\n\n{url}\n",
                success_message="Signed in to gmail-mcp. You can close this tab.",
                timeout_seconds=timeout,
                prompt="consent",  # always returns a refresh token, also when signing in again
            )
        except webbrowser.Error as e:
            raise AuthError("No browser here: run `gmail-mcp login --no-browser` and open the URL it prints.") from e
        except Exception as e:  # timeout, a refused consent, a narrowed scope, a busy port
            raise AuthError(f"Sign-in failed: {e}") from e
        if not isinstance(creds, Credentials) or not creds.token or not creds.refresh_token:
            raise AuthError("Sign-in failed: Google sent no refresh token. Run `gmail-mcp login` again.")
        email = email_of(creds.token)
        self.store.save(json.dumps({"account": email, "refresh_token": creds.refresh_token, "scopes": SCOPES}))
        with self._lock:
            self._creds = creds
        return email

    def logout(self) -> None:
        """Forgets the sign-in here. Google still knows it until it's removed at myaccount.google.com/connections."""
        self.store.clear()
        with self._lock:
            self._creds = None
