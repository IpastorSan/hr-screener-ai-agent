"""Recruiter credentials and sessions: argon2id password hashes and a signed session cookie.

The cookie holds `{recruiter id, session version}` signed with `SESSION_SECRET` and timestamped;
it is valid for `SESSION_TTL_HOURS` after it was issued and is re-issued as it is used, so a
session ends after that long without a request. Raising a recruiter's `session_version`
(password change, deactivation) ends every session issued before. The sign-in provider (password
now, Google SSO in SAZ-38) only decides who the recruiter is; the session is the same.
"""

import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner, URLSafeTimedSerializer

from sazon.config import Settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "sazon_session"
SALT = "sazon.recruiter-session"
FLOW_SALT = "sazon.google-oauth-flow"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
REFRESH_AFTER_S = 5 * 60
"""Re-issue the cookie on a request when it is older than this, sliding the expiry."""

_hasher = PasswordHasher()  # argon2id with the library's (RFC 9106 low-memory) parameters
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """True when `password` matches. Without a hash it still spends the time of a check, so an
    unknown email answers as slowly as a wrong password."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problem(password: str) -> str | None:
    """Why a new password is refused, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
    if len(password) > MAX_PASSWORD_LENGTH:
        return "La contraseña es demasiado larga."
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


@dataclass(frozen=True)
class SessionClaims:
    recruiter_id: str
    version: int
    age_s: float


class _ClockSigner(TimestampSigner):
    """A timestamp signer on an injectable clock, so tests can let a session expire."""

    def __init__(self, *args: Any, clock: Callable[[], float] = time.time, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.clock = clock

    def get_timestamp(self) -> int:
        return int(self.clock())


class Sessions:
    """Issues and reads session tokens."""

    def __init__(self, secret: str, ttl_s: float, clock: Callable[[], float] = time.time) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._serializer = URLSafeTimedSerializer(
            secret, salt=SALT, signer=_ClockSigner, signer_kwargs={"clock": clock}
        )
        self._audit_key = hashlib.sha256(f"audit:{secret}".encode()).digest()
        self._flows = URLSafeTimedSerializer(
            secret, salt=FLOW_SALT, signer=_ClockSigner, signer_kwargs={"clock": clock}
        )

    def sign_flow(self, data: dict[str, Any]) -> str:
        """A signed, timestamped value for the OAuth flow cookie (state, PKCE verifier)."""
        return self._flows.dumps(data)

    def read_flow(self, token: str | None, max_age_s: float) -> object | None:
        if not token:
            return None
        try:
            return self._flows.loads(token, max_age=int(max_age_s))
        except (SignatureExpired, BadSignature):
            return None

    def issue(self, recruiter_id: str, version: int) -> str:
        return self._serializer.dumps({"r": recruiter_id, "v": version})

    def read(self, token: str | None) -> SessionClaims | None:
        """The claims of a valid, unexpired token; None otherwise."""
        if not token:
            return None
        try:
            data, signed_at = self._serializer.loads(
                token, max_age=int(self.ttl_s), return_timestamp=True
            )
        except (SignatureExpired, BadSignature):
            return None
        if not isinstance(data, dict):
            return None
        recruiter_id, version = data.get("r"), data.get("v")
        if not isinstance(recruiter_id, str) or not isinstance(version, int):
            return None
        age = self.clock() - signed_at.timestamp()
        return SessionClaims(recruiter_id=recruiter_id, version=version, age_s=age)

    def pseudonym(self, value: str) -> str:
        """A stable keyed hash for audit lines: the same email always maps to the same short
        value, which cannot be reversed without the secret."""
        digest = hmac.new(self._audit_key, value.encode(), hashlib.sha256).hexdigest()
        return digest[:12]


@lru_cache(maxsize=1)
def _ephemeral_secret() -> str:
    logger.warning(
        "SESSION_SECRET is empty: using a random key for this process, so recruiter sessions "
        "end when the API restarts."
    )
    return secrets.token_urlsafe(48)


def sessions_from(settings: Settings) -> Sessions:
    secret = settings.session_secret.get_secret_value().strip() or _ephemeral_secret()
    return Sessions(secret, settings.session_ttl_hours * 3600)
