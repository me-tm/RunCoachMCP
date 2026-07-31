"""Tests for CorosTokenStore — encrypted persistence of COROS OAuth tokens."""
import time
import pytest

from claude2strava.crypto import Crypto
from claude2strava.coros.token_store import (
    CorosTokenData,
    CorosTokenStore,
    CorosNotConnectedError,
)


def _make_token(offset: int = 3600) -> CorosTokenData:
    now = int(time.time())
    return CorosTokenData(
        open_id="open_abc123",
        access_token="acc_tok",
        refresh_token="ref_tok",
        expires_at=now + offset,
        refresh_expires_at=now + 7776000,
    )


# ── save / load round-trip ────────────────────────────────────────────────────

def test_save_and_load_round_trip(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    token = _make_token()
    store.save(token)
    loaded = store.load()
    assert loaded.open_id == token.open_id
    assert loaded.access_token == token.access_token
    assert loaded.refresh_token == token.refresh_token
    assert loaded.expires_at == token.expires_at
    assert loaded.refresh_expires_at == token.refresh_expires_at


def test_file_permissions_are_owner_only(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store.save(_make_token())
    path = tmp_path / "coros_tokens.enc"
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)


def test_custom_filename(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key), filename="my_coros.enc")
    store.save(_make_token())
    assert (tmp_path / "my_coros.enc").exists()


# ── exists / delete ───────────────────────────────────────────────────────────

def test_exists_false_when_empty(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    assert not store.exists()


def test_exists_true_after_save(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store.save(_make_token())
    assert store.exists()


def test_delete_removes_file(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store.save(_make_token())
    store.delete()
    assert not store.exists()


def test_delete_is_idempotent(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store.delete()  # no file — must not raise


# ── load raises when missing ──────────────────────────────────────────────────

def test_load_raises_not_connected(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    with pytest.raises(CorosNotConnectedError, match="http://localhost:8080"):
        store.load()


# ── wrong key raises ──────────────────────────────────────────────────────────

def test_load_with_wrong_key_raises(tmp_path, fernet_key, other_fernet_key):
    store_a = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store_b = CorosTokenStore(tmp_path, Crypto(other_fernet_key))
    store_a.save(_make_token())
    with pytest.raises(Exception):
        store_b.load()


# ── expiry helpers ────────────────────────────────────────────────────────────

def test_is_expired_false_when_fresh(tmp_path, fernet_key):
    token = _make_token(offset=3600)
    assert not token.is_expired()


def test_is_expired_true_when_past(tmp_path, fernet_key):
    token = _make_token(offset=-10)
    assert token.is_expired()


def test_is_expired_respects_buffer(tmp_path, fernet_key):
    # expires in 30 s — expired with default 60 s buffer, not without buffer
    token = _make_token(offset=30)
    assert token.is_expired(buffer_seconds=60)
    assert not token.is_expired(buffer_seconds=0)


def test_refresh_is_expired_false_when_fresh():
    now = int(time.time())
    token = CorosTokenData(
        open_id="x", access_token="a", refresh_token="r",
        expires_at=now + 3600, refresh_expires_at=now + 7776000,
    )
    assert not token.refresh_is_expired()


def test_refresh_is_expired_true_when_past():
    now = int(time.time())
    token = CorosTokenData(
        open_id="x", access_token="a", refresh_token="r",
        expires_at=now - 100, refresh_expires_at=now - 1,
    )
    assert token.refresh_is_expired()
