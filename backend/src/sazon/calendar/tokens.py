"""Refresh tokens at rest: Fernet (AES-128-CBC with HMAC-SHA256) under `TOKEN_ENCRYPTION_KEY`.

Generate a key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
"""

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from sazon.config import Settings

logger = logging.getLogger(__name__)


class TokenDecryptError(Exception):
    """The stored token was encrypted with another key (rotated, or a random development key
    from an earlier process): the recruiter has to connect again."""


class TokenCipher:
    def __init__(self, key: str | bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode()).decode()

    def decrypt(self, stored: str) -> str:
        try:
            return self._fernet.decrypt(stored.encode()).decode()
        except InvalidToken as exc:
            raise TokenDecryptError from exc


@lru_cache(maxsize=1)
def _ephemeral_key() -> bytes:
    logger.warning(
        "TOKEN_ENCRYPTION_KEY is empty: using a random key for this process, so Google "
        "Calendar connections must be made again after the API restarts."
    )
    return Fernet.generate_key()


def cipher_from(settings: Settings) -> TokenCipher:
    """The configured cipher. Production refuses to boot without a key (`production_problems`);
    elsewhere an empty key means a random one per process."""
    key = settings.token_encryption_key.get_secret_value().strip()
    return TokenCipher(key or _ephemeral_key())
