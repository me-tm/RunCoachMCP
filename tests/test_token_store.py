import time
import pytest
from pathlib import Path

from claude2strava.crypto import Crypto
from claude2strava.token_store import TokenData, TokenStore, TokenNotFoundError


@pytest.fixture
def store(tmp_path, fernet_key):
    return TokenStore(tmp_path, Crypto(fernet_key))


def make_token(expires_offset: int = 3600) -> TokenData:
    return TokenData(
        access_token="act_test",
        refresh_token="rft_test",
        expires_at=int(time.time()) + expires_offset,
        athlete_id=42,
        athlete_name="Test Athlete",
    )


def test_save_and_load(store):
    token = make_token()
    store.save(token)
    loaded = store.load()
    assert loaded.access_token == "act_test"
    assert loaded.refresh_token == "rft_test"
    assert loaded.athlete_id == 42
    assert loaded.athlete_name == "Test Athlete"


def test_load_missing_raises(store):
    with pytest.raises(TokenNotFoundError):
        store.load()


def test_exists_false_when_missing(store):
    assert not store.exists()


def test_exists_true_after_save(store):
    store.save(make_token())
    assert store.exists()


def test_delete(store):
    store.save(make_token())
    store.delete()
    assert not store.exists()


def test_delete_noop_when_missing(store):
    store.delete()  # should not raise


def test_is_not_expired(store):
    token = make_token(expires_offset=3600)
    assert not token.is_expired()


def test_is_expired(store):
    token = make_token(expires_offset=-10)
    assert token.is_expired()


def test_is_expired_within_buffer(store):
    # 30 seconds left is within the default 60-second buffer
    token = make_token(expires_offset=30)
    assert token.is_expired()


def test_token_file_permissions(store, tmp_path):
    store.save(make_token())
    token_file = tmp_path / "tokens.enc"
    mode = token_file.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600 but got {oct(mode)}"


def test_encrypted_content_is_not_plaintext(store, tmp_path):
    store.save(make_token())
    raw = (tmp_path / "tokens.enc").read_bytes()
    assert b"act_test" not in raw
    assert b"rft_test" not in raw


def test_wrong_key_cannot_load(tmp_path, fernet_key, other_fernet_key):
    writer = TokenStore(tmp_path, Crypto(fernet_key))
    writer.save(make_token())
    reader = TokenStore(tmp_path, Crypto(other_fernet_key))
    from claude2strava.crypto import CryptoError
    with pytest.raises(CryptoError):
        reader.load()
