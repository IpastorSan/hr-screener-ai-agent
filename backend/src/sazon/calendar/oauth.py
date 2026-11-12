"""Google sign-in (corporate SSO) and the Calendar grant: one OAuth 2.0 authorization-code flow
with PKCE.

The ID token is verified with google-auth (signature against Google's certificates, `aud`,
`iss`, `exp`); on top of that the account must belong to the Workspace (`hd` claim, never the
`hd` URL parameter, which is only a hint for Google's account chooser) and have a verified
email. Who the recruiter is comes from matching that email to an existing active account;
nobody is created here.
"""

import base64
import contextlib
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from google.auth import exceptions as google_exceptions
from google.auth import transport
from google.oauth2 import id_token as google_id_token
from sqlmodel import select

from sazon.calendar.google import REVOKE_URL, TOKEN_URL, http_client
from sazon.calendar.tokens import TokenCipher, TokenDecryptError
from sazon.config import Settings
from sazon.storage.models import CredentialStatus, GoogleCredential
from sazon.storage.repo import Repo

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CERTS_TTL_S = 3600.0
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"
"""The narrowest pair for what we do: create, patch and delete our events, and read free/busy
(Google lists `calendar.freebusy` as enough for `freeBusy.query`)."""
CALENDAR_SCOPES = frozenset({CALENDAR_EVENTS, CALENDAR_FREEBUSY})
SCOPES = ("openid", "email", "profile", CALENDAR_EVENTS, CALENDAR_FREEBUSY)
CLOCK_SKEW_S = 10


class OAuthError(Exception):
    """The sign-in failed. `code` goes to the web as `?error=` and picks its message."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Flow:
    """What the callback needs from the start of the flow; kept in a signed, short-lived
    cookie. `recruiter_id` is set when a signed-in recruiter connects their calendar."""

    state: str
    verifier: str
    nonce: str
    intent: str
    next: str
    recruiter_id: str | None = None

    def dump(self) -> dict[str, Any]:
        return {
            "s": self.state,
            "v": self.verifier,
            "n": self.nonce,
            "i": self.intent,
            "x": self.next,
            "r": self.recruiter_id,
        }

    @classmethod
    def load(cls, data: object) -> "Flow | None":
        if not isinstance(data, dict):
            return None
        fields = (data.get(key) for key in ("s", "v", "n", "i", "x"))
        state, verifier, nonce, intent, next_path = fields
        recruiter_id = data.get("r")
        if not all(isinstance(v, str) for v in (state, verifier, nonce, intent, next_path)):
            return None
        if recruiter_id is not None and not isinstance(recruiter_id, str):
            return None
        return cls(state, verifier, nonce, intent, next_path, recruiter_id)  # pyright: ignore[reportArgumentType]


@dataclass(frozen=True)
class Tokens:
    id_token: str
    access_token: str
    refresh_token: str | None
    scopes: frozenset[str]

    @property
    def calendar_granted(self) -> bool:
        return self.scopes >= CALENDAR_SCOPES


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    name: str | None


class _Response(transport.Response):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    @property
    def data(self) -> bytes:
        return self._response.content


class _CachedResponse(transport.Response):
    def __init__(self, status: int, data: bytes) -> None:
        self._status, self._data = status, data

    @property
    def status(self) -> int:
        return self._status

    @property
    def headers(self) -> Mapping[str, str]:
        return {}

    @property
    def data(self) -> bytes:
        return self._data


class HttpxRequest(transport.Request):
    """google-auth's transport over httpx, caching GETs (Google's signing certificates) for
    an hour so a sign-in does not fetch them every time."""

    def __init__(self, http: httpx.Client, clock: Callable[[], float] = time.time) -> None:
        self._http = http
        self._clock = clock
        self._cache: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> transport.Response:
        if method == "GET":
            with self._lock:
                cached = self._cache.get(url)
            if cached is not None and cached[0] > self._clock():
                return _CachedResponse(200, cached[1])
        try:
            response = self._http.request(
                method, url, content=body, headers=dict(headers or {}), timeout=timeout or 10.0
            )
        except httpx.HTTPError as exc:
            raise google_exceptions.TransportError(type(exc).__name__) from exc
        if method == "GET" and response.status_code == 200:
            with self._lock:
                self._cache[url] = (self._clock() + CERTS_TTL_S, response.content)
        return _Response(response)


class GoogleOAuth:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        domain: str,
        http: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.domain = domain.strip().lower()
        self._http = http or http_client()
        self._request = HttpxRequest(self._http)

    @classmethod
    def from_settings(cls, settings: Settings, http: httpx.Client | None = None) -> "GoogleOAuth":
        return cls(
            client_id=settings.google_client_id.strip(),
            client_secret=settings.google_client_secret.get_secret_value().strip(),
            redirect_uri=settings.google_redirect_uri.strip(),
            domain=settings.google_workspace_domain,
            http=http,
        )

    def new_flow(self, intent: str, next_path: str, recruiter_id: str | None = None) -> Flow:
        return Flow(
            state=secrets.token_urlsafe(32),
            verifier=secrets.token_urlsafe(64),
            nonce=secrets.token_urlsafe(24),
            intent=intent,
            next=next_path,
            recruiter_id=recruiter_id,
        )

    def authorization_url(self, flow: Flow, *, consent: bool, login_hint: str | None = None) -> str:
        """Google's consent page. `consent` forces the consent screen, the only way to get a
        refresh token for an account that granted access before."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": flow.state,
            "nonce": flow.nonce,
            "code_challenge": _challenge(flow.verifier),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent select_account" if consent else "select_account",
            "hd": self.domain,
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange(self, code: str, flow: Flow) -> Tokens:
        try:
            response = self._http.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "code_verifier": flow.verifier,
                },
            )
        except httpx.HTTPError as exc:
            raise OAuthError("exchange", type(exc).__name__) from exc
        if not response.is_success:
            raise OAuthError("exchange", f"token endpoint answered {response.status_code}")
        data = response.json()
        id_token, access_token = data.get("id_token"), data.get("access_token")
        if not isinstance(id_token, str) or not isinstance(access_token, str):
            raise OAuthError("exchange", "no ID token in the response")
        refresh = data.get("refresh_token")
        return Tokens(
            id_token=id_token,
            access_token=access_token,
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            scopes=frozenset(str(data.get("scope", "")).split()),
        )

    def verify(self, id_token: str, flow: Flow) -> Identity:
        """The Workspace account behind the ID token, or `OAuthError`."""
        try:
            claims = google_id_token.verify_oauth2_token(
                id_token, self._request, audience=self.client_id, clock_skew_in_seconds=CLOCK_SKEW_S
            )
        except (ValueError, google_exceptions.GoogleAuthError) as exc:
            raise OAuthError("token", type(exc).__name__) from exc
        if not hmac.compare_digest(str(claims.get("nonce") or "").encode(), flow.nonce.encode()):
            raise OAuthError("token", "nonce mismatch")
        hosted = str(claims.get("hd") or "").lower()
        if hosted != self.domain:
            raise OAuthError("domain", "account outside the Workspace")
        if claims.get("email_verified") is not True:
            raise OAuthError("unverified", "email not verified")
        email, subject = claims.get("email"), claims.get("sub")
        if not isinstance(email, str) or not isinstance(subject, str):
            raise OAuthError("token", "no email or subject")
        name = claims.get("name")
        return Identity(
            subject=subject,
            email=email.strip().lower(),
            name=name if isinstance(name, str) else None,
        )

    def revoke(self, token: str) -> bool:
        """Revoke a grant at Google (best effort). True when Google confirmed it."""
        try:
            response = self._http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            return False
        return response.is_success


def save_credential(
    repo: Repo, cipher: TokenCipher, recruiter_id: str, identity: Identity, tokens: Tokens
) -> GoogleCredential:
    """Store (or replace) a recruiter's calendar grant with its refresh token encrypted."""
    if tokens.refresh_token is None:
        raise ValueError("no refresh token")
    now = datetime.now(UTC)
    with repo.session() as session:
        credential = session.get(GoogleCredential, recruiter_id)
        if credential is None:
            credential = GoogleCredential(
                recruiter_id=recruiter_id,
                google_email=identity.email,
                google_subject=identity.subject,
                refresh_token_encrypted="",
                connected_at=now,
                updated_at=now,
            )
        credential.google_email = identity.email
        credential.google_subject = identity.subject
        credential.scopes = sorted(tokens.scopes)
        credential.refresh_token_encrypted = cipher.encrypt(tokens.refresh_token)
        credential.status = CredentialStatus.CONNECTED
        credential.connected_at = now
        credential.updated_at = now
        session.add(credential)
        session.commit()
        session.refresh(credential)
        return credential


def disconnect(
    repo: Repo, cipher: TokenCipher, oauth: GoogleOAuth | None, recruiter_id: str
) -> bool:
    """Revoke the recruiter's grant at Google (best effort) and delete it. False when there
    was none."""
    with repo.session() as session:
        credential = session.get(GoogleCredential, recruiter_id)
        if credential is None:
            return False
        stored = credential.refresh_token_encrypted
        session.delete(credential)
        session.commit()
    if oauth is not None:
        # An unreadable token cannot be revoked; the row is gone either way.
        with contextlib.suppress(TokenDecryptError):
            oauth.revoke(cipher.decrypt(stored))
    return True


def credential_of(repo: Repo, recruiter_id: str) -> GoogleCredential | None:
    with repo.session() as session:
        return session.get(GoogleCredential, recruiter_id)


def connection_status(repo: Repo) -> dict[str, str]:
    """`connected` or `revoked` per recruiter with a grant."""
    with repo.session() as session:
        rows = session.exec(select(GoogleCredential.recruiter_id, GoogleCredential.status)).all()
    return dict(rows)


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
