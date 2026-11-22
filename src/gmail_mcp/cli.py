"""Command line entry point: `gmail-mcp <command>`.

Settings come from environment variables, so every MCP client can pass them the same way:
  GMAIL_MCP_CLIENT_ID      client ID of your Google OAuth client (required)
  GMAIL_MCP_CLIENT_SECRET  its client secret (required)
  GMAIL_MCP_TOKEN_CACHE    auto (default: keychain if there is one, else a file), keyring, or file
  GMAIL_MCP_CACHE_FILE     sign-in file (default ~/.config/gmail-mcp/token-cache.json)
  GMAIL_MCP_DOWNLOADS      keep the originals of attachments here (default: keep nothing)
  GMAIL_MCP_TIMEZONE       IANA time zone for every date shown and given (default: $TZ, else this machine's)
  GMAIL_MCP_TOKEN          Bearer token clients must send when serving over HTTP
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import cast

from gmail_mcp import __version__
from gmail_mcp.auth import Auth, AuthError, NotSignedIn
from gmail_mcp.store import CacheKind, make_store
from gmail_mcp.times import resolve_timezone


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"GMAIL_MCP_{name}") or default


def make_auth() -> Auth:
    kind = cast(CacheKind, _env("TOKEN_CACHE", "auto"))
    if kind not in ("auto", "keyring", "file"):
        raise AuthError(f"GMAIL_MCP_TOKEN_CACHE must be auto, keyring or file, not {kind!r}.")
    file = Path(_env("CACHE_FILE")).expanduser() if _env("CACHE_FILE") else None
    return Auth(client_id=_env("CLIENT_ID"), client_secret=_env("CLIENT_SECRET"), store=make_store(kind, file))


def quiet_logs() -> None:
    """No log line per request to Gmail (the MCP server turns on INFO logging): they'd fill a container's log with
    message ids. Warnings and errors still show."""
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_serve(args: argparse.Namespace) -> int:
    from gmail_mcp.gmail import Gmail
    from gmail_mcp.server import build_server

    quiet_logs()
    auth = make_auth()
    downloads = Path(_env("DOWNLOADS")).expanduser() if _env("DOWNLOADS") else None
    server = build_server(Gmail(auth.token), downloads, resolve_timezone())
    if args.transport == "stdio":
        server.run("stdio")
        return 0
    from gmail_mcp.httpserve import check_token, serve_http

    token = _env("TOKEN") or None
    check_token(args.host, token, args.no_auth)
    serve_http(server, args.host, args.port, token, signed_in=lambda: auth.account() is not None)
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    auth = make_auth()
    user = auth.login(open_browser=not args.no_browser, port=args.port, bind=args.bind)
    print(f"Signed in as {user}. Sign-in kept in: {auth.store.describe()}.")
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    auth = make_auth()
    auth.logout()
    print(f"Signed out; removed the sign-in ({auth.store.describe()}).")
    print("Google still lists the app until you remove it at https://myaccount.google.com/connections.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    auth = make_auth()
    print(f"Sign-in kept in: {auth.store.describe()}")
    user = auth.account()
    if not user:
        print("Not signed in. Run `gmail-mcp login`.")
        return 1
    try:
        auth.token()
    except NotSignedIn as e:
        print(f"Signed in as {user}, but the sign-in no longer works: {e}")
        return 1
    print(f"Signed in as {user}; token is valid.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmail-mcp",
        description="Read-only MCP server for Gmail.",
        epilog=__doc__.split("\n", 2)[2] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"gmail-mcp {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    serve = sub.add_parser("serve", help="run the MCP server (the default command)")
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: loopback only)")
    serve.add_argument("--port", type=int, default=int(_env("PORT", "8767")), help="default: $GMAIL_MCP_PORT or 8767")
    serve.add_argument("--no-auth", action="store_true", help="allow HTTP without a token, on loopback only")
    serve.set_defaults(func=cmd_serve)

    login = sub.add_parser("login", help="sign in to Google in a browser")
    login.add_argument("--no-browser", action="store_true", help="only print the sign-in URL, to open elsewhere")
    login.add_argument(
        "--port", type=int, default=0, help="port for Google's redirect to http://localhost (default: a free one)"
    )
    login.add_argument(
        "--bind", default="localhost", help="address to listen on for the redirect (0.0.0.0 in a container)"
    )
    login.set_defaults(func=cmd_login)
    sub.add_parser("logout", help="forget the saved sign-in").set_defaults(func=cmd_logout)
    sub.add_parser("status", help="show who is signed in and where the sign-in is kept").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:  # bare `gmail-mcp` serves over stdio, so client configs stay short
        args = parser.parse_args(["serve"])
    try:
        return args.func(args)
    except (AuthError, ValueError) as e:
        print(f"gmail-mcp: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
