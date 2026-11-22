"""The Gmail client: GET only, errors, timeouts."""

from __future__ import annotations

import base64

import httpx
import pytest

from conftest import FakeGmail, make_gmail
from gmail_mcp.gmail import Gmail, GmailError, decode_raw


def test_gmail_client_has_no_write_methods() -> None:
    public = {n for n in dir(Gmail) if not n.startswith("_")}
    assert public == {"get"}


def test_get_sends_the_token_and_repeated_params(fake: FakeGmail) -> None:
    gmail = make_gmail(fake, "tok")
    assert gmail.get("/profile")["emailAddress"] == "test@gmail.com"
    gmail.get("/messages/m1", {"format": "metadata", "metadataHeaders": ["From", "Subject"]})
    assert all(r.headers["authorization"] == "Bearer tok" for r in fake.requests)
    assert fake.requests[1].url.params.get_list("metadataHeaders") == ["From", "Subject"]


def test_error_messages() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/json"):
            return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found", "status": "NOT_FOUND"}})
        return httpx.Response(502, text="<html>Bad gateway</html>")

    gmail = make_gmail(broken)
    with pytest.raises(GmailError, match=r"\(404\) NOT_FOUND: Not Found"):
        gmail.get("/json")
    with pytest.raises(GmailError, match=r"\(502\) <html>Bad gateway"):
        gmail.get("/html")


def test_timeout_is_retried_once(fake: FakeGmail) -> None:
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return fake(request)

    assert make_gmail(flaky).get("/profile")["emailAddress"] == "test@gmail.com"
    assert calls == 2


def test_repeated_timeout_is_an_error() -> None:
    def stuck(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GmailError, match="didn't answer in time"):
        make_gmail(stuck).get("/profile")


def test_network_error_is_an_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(GmailError, match="Couldn't reach Gmail"):
        make_gmail(down).get("/profile")


@pytest.mark.parametrize("data", [b"x", b"xy", b"xyz", b"\xff\xfe\xfd"])
def test_decode_raw_with_or_without_padding(data: bytes) -> None:
    encoded = base64.urlsafe_b64encode(data).decode()
    assert decode_raw(encoded) == data
    assert decode_raw(encoded.rstrip("=")) == data
