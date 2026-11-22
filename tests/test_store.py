"""Where the sign-in is kept. From graph-mcp's test_auth.py (its store tests)."""

from __future__ import annotations

import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import gmail_mcp.store as store_module
from gmail_mcp.store import FileStore, KeyringStore, make_store


def test_file_store_round_trip_is_private(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "sub" / "cache.json")
    assert store.load() is None
    store.save('{"a": 1}')
    assert store.load() == '{"a": 1}'
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    store.clear()
    assert store.load() is None
    store.clear()  # clearing twice is fine


def test_make_store_choices(tmp_path: Path) -> None:
    assert isinstance(make_store("file", tmp_path / "c.json"), FileStore)
    assert isinstance(make_store("keyring"), KeyringStore)


def test_make_store_auto_falls_back_to_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "keyring_usable", lambda: False)
    store = make_store("auto", tmp_path / "c.json")
    assert isinstance(store, FileStore) and store.path == tmp_path / "c.json"
    monkeypatch.setattr(store_module, "keyring_usable", lambda: True)
    assert isinstance(make_store("auto"), KeyringStore)


def test_default_cache_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert store_module.default_cache_file() == tmp_path / "gmail-mcp" / "token-cache.json"


def test_keyring_store_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(store_module.keyring, "get_password", lambda svc, user: saved.get((svc, user)))
    monkeypatch.setattr(store_module.keyring, "set_password", lambda svc, user, pw: saved.__setitem__((svc, user), pw))

    def delete(svc: str, user: str) -> None:
        if (svc, user) not in saved:
            raise store_module.keyring.errors.PasswordDeleteError("missing")
        del saved[(svc, user)]

    monkeypatch.setattr(store_module.keyring, "delete_password", delete)
    store = KeyringStore()
    assert store.load() is None
    store.save("{}")
    assert saved == {("gmail-mcp", "token-cache"): "{}"} and store.load() == "{}"
    store.clear()
    store.clear()  # clearing twice is fine
    assert store.load() is None


def test_file_store_concurrent_saves(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "cache.json")
    payloads = [f'{{"n": {i}}}' for i in range(20)]
    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(store.save, payloads))  # graph-mcp's bug: every save renamed the same .tmp file
    assert store.load() in payloads
    assert [p.name for p in tmp_path.iterdir()] == ["cache.json"]  # no temp files left behind
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
