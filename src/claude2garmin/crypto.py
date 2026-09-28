from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    pass


class Crypto:
    def __init__(self, key: bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except Exception as exc:
            raise CryptoError(f"Invalid encryption key: {exc}") from exc

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:
            raise CryptoError("Decryption failed — wrong key or corrupted data") from exc
