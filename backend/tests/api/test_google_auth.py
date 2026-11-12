"""Google sign-in and the calendar connection, end to end through the API against a fake
Google: real RS256 ID tokens signed by a test key, verified by google-auth."""

import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.auth import crypt, jwt

from sazon import recruiters
from sazon.api.app import create_app
from sazon.auth import COOKIE_NAME
from sazon.calendar.oauth import CALENDAR_EVENTS, CALENDAR_FREEBUSY, GoogleOAuth
from sazon.calendar.tokens import TokenCipher
from sazon.guards import TokenBucket
from sazon.llm import FakeLLM
from sazon.logs import PiiFilter
from sazon.service import ScreeningService
from sazon.storage.models import GoogleCredential, Recruiter
from sazon.storage.repo import Repo, make_engine

CLIENT_ID = "test-client.apps.googleusercontent.com"
EMAIL = "lucia.reclutadora@gruposazon.example"
REFRESH = "1//refresh-token-never-logged"
WEB = "http://localhost:3000"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "test-key"


def _certificate() -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(KEY.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(KEY, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


CERTS = {KEY_ID: _certificate()}
SIGNER = crypt.RSASigner.from_string(
    KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ),
    key_id=KEY_ID,
)


def id_token(expected_nonce: str, **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": EMAIL,
        "email_verified": True,
        "hd": "gruposazon.example",
        "name": "Lucía Fernández",
        "nonce": expected_nonce,
        "iat": now,
        "exp": now + 3600,
    } | claims
    return jwt.encode(SIGNER, {k: v for k, v in payload.items() if v is not None}).decode()


class FakeGoogle:
    """Google's token, certificate and revocation endpoints."""

    def __init__(self) -> None:
        self.claims: dict[str, Any] = {}
        self.nonce = ""
        self.refresh_token: str | None = REFRESH
        self.scope = f"openid email profile {CALENDAR_EVENTS} {CALENDAR_FREEBUSY}"
        self.token_status = 200
        self.token_requests: list[dict[str, list[str]]] = []
        self.revoked: list[str] = []
        self.signed_by: Callable[[str], str] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.googleapis.com/oauth2/v1/certs":
            return httpx.Response(200, json=CERTS)
        if url == "https://oauth2.googleapis.com/token":
            self.token_requests.append(parse_qs(request.content.decode()))
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            token = id_token(self.nonce, **self.claims)
            if self.signed_by is not None:
                token = self.signed_by(token)
            body = {"access_token": "at", "id_token": token, "scope": self.scope}
            if self.refresh_token:
                body["refresh_token"] = self.refresh_token
            return httpx.Response(200, json=body)
        if url == "https://oauth2.googleapis.com/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def service() -> ScreeningService:
    return ScreeningService(FakeLLM(), Repo(make_engine("sqlite://")))


@pytest.fixture
def client(service: ScreeningService, google: FakeGoogle) -> Iterator[TestClient]:
    app = create_app(service)
    app.state.google_oauth = GoogleOAuth(
        client_id=CLIENT_ID,
        client_secret="client-secret",
        redirect_uri="http://localhost:8000/api/auth/google/callback",
        domain="gruposazon.example",
        http=httpx.Client(transport=httpx.MockTransport(google)),
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def lucia(service: ScreeningService) -> Recruiter:
    return recruiters.create(
        service.repo, datetime.now(UTC), name="Lucía", email=EMAIL, country="ES"
    )


def start(
    client: TestClient, google: FakeGoogle, path: str = "/api/auth/google/start", **params: str
) -> dict[str, str]:
    response = client.get(path, params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    query = {key: values[0] for key, values in parse_qs(location.query).items()}
    google.nonce = query["nonce"]
    return query


def callback(client: TestClient, **params: str):
    response = client.get("/api/auth/google/callback", params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    return response


def signed_in(client: TestClient) -> bool:
    return client.get("/api/me").status_code == 200


def credential(service: ScreeningService, recruiter: Recruiter) -> GoogleCredential | None:
    with service.repo.session() as session:
        return session.get(GoogleCredential, recruiter.id)


def test_the_authorization_url_asks_for_the_workspace_offline_with_pkce(client, google):
    query = start(client, google, next="/recruiters/interviews")
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == "http://localhost:8000/api/auth/google/callback"
    assert query["response_type"] == "code"
    assert query["scope"].split() == [
        "openid",
        "email",
        "profile",
        CALENDAR_EVENTS,
        CALENDAR_FREEBUSY,
    ]
    assert query["access_type"] == "offline"
    assert query["hd"] == "gruposazon.example"
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43
    assert len(query["state"]) >= 40
    assert query["prompt"] == "select_account"
    assert client.cookies.get("sazon_google_flow")  # state and PKCE verifier, signed


def test_a_workspace_recruiter_signs_in_and_connects_the_calendar(
    client, google, service, lucia, caplog
):
    query = start(client, google, next="/recruiters/interviews")
    with caplog.at_level(logging.DEBUG):
        response = callback(client, state=query["state"], code="auth-code")

    assert response.headers["location"] == f"{WEB}/recruiters/interviews?calendar=connected"
    assert COOKIE_NAME in response.headers["set-cookie"]
    me = client.get("/api/me").json()
    assert me["id"] == lucia.id
    assert me["calendar"]["status"] == "connected"
    assert me["calendar"]["google_email"] == EMAIL
    [exchange] = google.token_requests
    assert exchange["code"] == ["auth-code"]
    assert exchange["grant_type"] == ["authorization_code"]
    assert len(exchange["code_verifier"][0]) >= 43  # PKCE: the verifier only goes to Google
    stored = credential(service, lucia)
    assert stored is not None
    assert REFRESH not in stored.refresh_token_encrypted
    cipher: TokenCipher = client.app.state.token_cipher  # pyright: ignore[reportAttributeAccessIssue]
    assert cipher.decrypt(stored.refresh_token_encrypted) == REFRESH
    assert stored.google_subject == "google-sub-1"
    assert CALENDAR_FREEBUSY in stored.scopes
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("sazon"))
    assert REFRESH not in ours
    assert "auth-code" not in ours
    # The flow cookie is single use.
    assert callback(client, state=query["state"], code="again").headers["location"] == (
        f"{WEB}/login?error=state"
    )


@pytest.mark.parametrize(
    ("claims", "error"),
    [
        ({"hd": "gmail.com"}, "domain"),
        ({"hd": None, "email": "lucia@gmail.com"}, "domain"),
        ({"email_verified": False}, "unverified"),
        ({"email": "nadie@gruposazon.example"}, "unknown"),
        ({"aud": "another-client"}, "token"),
        ({"iss": "https://evil.example"}, "token"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "token"),
        ({"nonce": "replayed"}, "token"),
    ],
)
def test_a_sign_in_outside_the_rules_is_refused(client, google, lucia, claims, error):
    google.claims = claims
    query = start(client, google)
    response = callback(client, state=query["state"], code="auth-code")
    assert response.headers["location"] == f"{WEB}/login?error={error}"
    assert COOKIE_NAME not in response.headers.get("set-cookie", "")
    assert not signed_in(client)


def test_a_google_sign_in_spends_the_login_limit_once(client, google, lucia):
    client.app.state.login_ip_limit = TokenBucket(2, 900)  # pyright: ignore[reportAttributeAccessIssue]
    for _ in range(2):
        query = start(client, google)
        response = callback(client, state=query["state"], code="auth-code")
        assert response.headers["location"] == f"{WEB}/recruiters?calendar=connected"
    query = start(client, google)  # the start itself is never limited
    blocked = client.get(
        "/api/auth/google/callback",
        params={"state": query["state"], "code": "auth-code"},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    assert len(google.token_requests) == 2


def test_a_token_signed_by_another_key_is_refused(client, google, lucia):
    other = crypt.RSASigner.from_string(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key_id=KEY_ID,
    )
    google.signed_by = lambda token: jwt.encode(other, jwt.decode(token, verify=False)).decode()
    query = start(client, google)
    assert callback(client, state=query["state"], code="c").headers["location"] == (
        f"{WEB}/login?error=token"
    )


def test_a_deactivated_recruiter_cannot_sign_in(client, google, service, lucia):
    recruiters.update(service.repo, lucia.id, datetime.now(UTC), active=False)
    query = start(client, google)
    assert callback(client, state=query["state"], code="c").headers["location"] == (
        f"{WEB}/login?error=unknown"
    )


def test_a_bad_or_missing_state_is_refused_before_talking_to_google(client, google, lucia):
    start(client, google)
    assert (
        callback(client, state="forged", code="c").headers["location"] == f"{WEB}/login?error=state"
    )
    client.cookies.clear()
    assert callback(client, state="x", code="c").headers["location"] == f"{WEB}/login?error=state"
    assert google.token_requests == []


def test_declining_consent_and_a_failed_exchange_come_back_to_the_sign_in_page(
    client, google, lucia
):
    query = start(client, google)
    response = callback(client, state=query["state"], error="access_denied")
    assert response.headers["location"] == f"{WEB}/login?error=denied"
    google.token_status = 400
    query = start(client, google)
    response = callback(client, state=query["state"], code="expired")
    assert response.headers["location"] == f"{WEB}/login?error=exchange"


def test_signing_in_without_calendar_scopes_keeps_no_grant(client, google, service, lucia):
    google.scope = "openid email profile"
    query = start(client, google)
    response = callback(client, state=query["state"], code="c")
    assert response.headers["location"] == f"{WEB}/recruiters"
    assert signed_in(client)
    assert credential(service, lucia) is None


def test_a_password_user_connects_and_disconnects_the_calendar(
    client, google, service, sign_in, caplog
):
    me = sign_in(client, admin=False, email="marta@gruposazon.example")
    google.claims = {"email": me.email}
    query = start(client, google, "/api/auth/google/connect", next="/recruiters/team")
    assert query["prompt"] == "consent select_account"
    assert query["login_hint"] == me.email

    response = callback(client, state=query["state"], code="c")

    assert response.headers["location"] == f"{WEB}/recruiters/team?calendar=connected"
    assert credential(service, me) is not None
    team = client.get("/api/recruiters").json()
    assert next(r for r in team if r["id"] == me.id)["calendar"]["status"] == "connected"

    with caplog.at_level(logging.DEBUG):
        assert client.delete("/api/auth/google/calendar").status_code == 204
    assert google.revoked == [REFRESH]
    assert credential(service, me) is None
    assert client.get("/api/me").json()["calendar"]["status"] == "disconnected"
    assert REFRESH not in caplog.text


def test_connecting_needs_a_session_and_the_same_account(client, google, service, sign_in):
    assert client.get("/api/auth/google/connect", follow_redirects=False).status_code == 401
    me = sign_in(client, admin=False, email="marta@gruposazon.example")
    google.claims = {"email": "otra@gruposazon.example"}
    query = start(client, google, "/api/auth/google/connect")
    response = callback(client, state=query["state"], code="c")
    assert response.headers["location"] == f"{WEB}/recruiters?calendar=mismatch"
    assert credential(service, me) is None


def test_connecting_without_a_refresh_token_says_so(client, google, service, sign_in):
    me = sign_in(client, admin=False, email="marta@gruposazon.example")
    google.claims = {"email": me.email}
    google.refresh_token = None
    query = start(client, google, "/api/auth/google/connect")
    assert callback(client, state=query["state"], code="c").headers["location"] == (
        f"{WEB}/recruiters?calendar=failed"
    )


def test_next_never_leaves_the_dashboard(client, google, lucia):
    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        query = start(client, google, next=target)
        response = callback(client, state=query["state"], code="c")
        assert response.headers["location"].startswith(f"{WEB}/recruiters")


def test_without_google_configured_the_routes_are_off(service: ScreeningService):
    app: FastAPI = create_app(service)
    with TestClient(app) as plain:
        assert plain.get("/api/auth/providers").json() == {
            "google": False,
            "domain": "",
            "scheduling_source": "fixed",
        }
        assert plain.get("/api/auth/google/start", follow_redirects=False).status_code == 404


def test_providers_say_google_is_on(client):
    assert client.get("/api/auth/providers").json()["google"] is True


def test_access_lines_mask_the_oauth_code_and_state():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "1.2.3.4",
            "GET",
            "/api/auth/google/callback?state=abc&code=4/0secret&scope=x",
            "1.1",
            302,
        ),
        None,
    )
    PiiFilter().filter(record)
    line = record.getMessage()
    assert "4/0secret" not in line
    assert "abc" not in line
    assert "code=[redacted]" in line
    assert "scope=x" in line
