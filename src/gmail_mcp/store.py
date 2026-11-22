"""Where the sign-in is kept: the OS keychain where there is one, else a file readable only by the owner.

The sign-in holds a refresh token, so it lives in the OS keychain where there is one (macOS Keychain, Windows
Credential Manager, Secret Service on Linux desktops). In containers and on headless servers, where there is no
keychain, it falls back to a file readable only by the owner.

From graph-mcp's auth.py, unchanged except for the names.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Literal, Protocol

import keyring
import keyring.backends.fail
import keyring.errors

KEYRING_SERVICE = "gmail-mcp"
KEYRING_USER = "token-cache"

CacheKind = Literal["auto", "keyring", "file"]


class TokenStore(Protocol):
    def load(self) -> str | None: ...
    def save(self, data: str) -> None: ...
    def clear(self) -> None: ...
    def describe(self) -> str: ...


class KeyringStore:
    def load(self) -> str | None:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)

    def save(self, data: str) -> None:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, data)

    def clear(self) -> None:
        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)

    def describe(self) -> str:
        return f"keychain ({type(keyring.get_keyring()).__name__}, service '{KEYRING_SERVICE}')"


class FileStore:
    """A plain JSON file, mode 0600 in a 0700 folder. Not encrypted: protect the folder (or volume) it lives in."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str | None:
        return self.path.read_text(encoding="utf-8") if self.path.is_file() else None

    def save(self, data: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # A temp file of its own (mode 0600) per save, so two writers never rename the same file away.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def describe(self) -> str:
        return f"file {self.path}"


def default_cache_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "gmail-mcp" / "token-cache.json"


def keyring_usable() -> bool:
    return not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)


def make_store(kind: CacheKind, file: Path | None = None) -> TokenStore:
    if kind == "keyring" or (kind == "auto" and keyring_usable()):
        return KeyringStore()
    return FileStore(file or default_cache_file())
