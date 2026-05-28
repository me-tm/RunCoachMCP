import os
import pytest
from cryptography.fernet import Fernet

# Use a fixed test key so no .env needed during tests
TEST_FERNET_KEY = Fernet.generate_key()

@pytest.fixture
def fernet_key() -> bytes:
    return TEST_FERNET_KEY

@pytest.fixture
def other_fernet_key() -> bytes:
    return Fernet.generate_key()
