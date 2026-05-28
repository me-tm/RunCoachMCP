import pytest
from cryptography.fernet import Fernet

from claude2strava.crypto import Crypto, CryptoError


def test_roundtrip(fernet_key):
    crypto = Crypto(fernet_key)
    assert crypto.decrypt(crypto.encrypt("hello world")) == "hello world"


def test_empty_string_roundtrip(fernet_key):
    crypto = Crypto(fernet_key)
    assert crypto.decrypt(crypto.encrypt("")) == ""


def test_unicode_roundtrip(fernet_key):
    text = "Ångström café 🚴‍♂️"
    crypto = Crypto(fernet_key)
    assert crypto.decrypt(crypto.encrypt(text)) == text


def test_wrong_key_raises(fernet_key, other_fernet_key):
    enc = Crypto(fernet_key).encrypt("secret")
    with pytest.raises(CryptoError):
        Crypto(other_fernet_key).decrypt(enc)


def test_invalid_key_raises():
    with pytest.raises(CryptoError):
        Crypto(b"not-a-valid-fernet-key")


def test_corrupted_data_raises(fernet_key):
    with pytest.raises(CryptoError):
        Crypto(fernet_key).decrypt(b"corrupted-garbage")


def test_encrypt_produces_bytes(fernet_key):
    result = Crypto(fernet_key).encrypt("test")
    assert isinstance(result, bytes)
    assert len(result) > 0
