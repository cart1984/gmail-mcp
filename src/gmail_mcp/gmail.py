"""A Gmail API client that can only read: it has no method other than GET.

Like graph-mcp's Graph client: one GET per call, retried once on a timeout, errors turned into readable messages.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailError(Exception):
    pass


class Gmail:
    def __init__(self, token: Callable[[], str], http: httpx.Client | None = None) -> None:
        self._token = token
        self._http = http or httpx.Client(timeout=60)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """GET `path` below users/me (e.g. `/messages`); list values in `params` become repeated parameters."""
        r = self._send(API + path, params, {"Authorization": f"Bearer {self._token()}"})
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
                msg = f"{err.get('status') or err.get('code')}: {err.get('message')}"
            except ValueError:
                msg = r.text[:500]
            raise GmailError(f"Gmail request failed ({r.status_code}) {msg}")
        return r.json()

    def _send(self, url: str, params: dict[str, Any] | None, headers: dict[str, str]) -> httpx.Response:
        for attempt in range(2):
            try:
                return self._http.get(url, params=params, headers=headers)
            except httpx.TimeoutException as e:
                if attempt:
                    raise GmailError("Gmail didn't answer in time (twice). Try again in a minute.") from e
            except httpx.TransportError as e:
                raise GmailError(f"Couldn't reach Gmail: {e}") from e
        raise AssertionError("unreachable")


def decode_raw(data: str) -> bytes:
    """A message's `raw` field (base64url, padding optional) as the bytes of the original RFC 822 message."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
