"""Recruiter sign-in, sessions and access: who reaches the dashboard routes, and who does not."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sazon import recruiters
from sazon.api import auth as auth_routes
from sazon.api.app import create_app
from sazon.api.schemas import StartIn
from sazon.auth import COOKIE_NAME, REFRESH_AFTER_S, Sessions
from sazon.config import Settings
from sazon.guards import TokenBucket
from sazon.service import ScreeningService
from sazon.storage.models import Recruiter

EMAIL = "lucia.reclutadora@gruposazon.example"
PASSWORD = "correct horse battery"
WRONG = {"error": "unauthorized", "detail": "Email o contraseña incorrectos."}
ORIGIN = "http://localhost:3000"  # WEB_ORIGIN's default

DASHBOARD_READS = [
    "/api/candidates",
    "/api/candidates/someone",
    "/api/candidates/someone/export",
    "/api/locations",
    "/api/analytics",
    "/api/agent-spec",
    "/api/interviews",
    "/api/recruiters",
    "/api/me",
]


@pytest.fixture
def lucia(service: ScreeningService, now: datetime) -> Recruiter:
    return recruiters.create(
        service.repo, now, name="Lucía", email=EMAIL, country="ES", password=PASSWORD
    )


def app_of(client: TestClient) -> FastAPI:
    return client.app  # pyright: ignore[reportReturnType]


def login(client: TestClient, email: str = EMAIL, password: str = PASSWORD, **kw: Any):
    return client.post("/api/auth/login", json={"email": email, "password": password}, **kw)


# --- sign-in ---


def test_login_sets_a_hardened_cookie_and_me_answers(anonymous: TestClient, lucia: Recruiter):
    response = login(anonymous, email="  Lucia.Reclutadora@GrupoSazon.Example ")
    assert response.status_code == 200
    assert response.json()["id"] == lucia.id
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=43200" in cookie  # 12 hours
    assert "Secure" not in cookie  # plain HTTP outside production
    me = anonymous.get("/api/me").json()
    assert me == {
        "id": lucia.id,
        "name": "Lucía",
        "email": EMAIL,
        "is_admin": False,
        "country": "ES",
        "timezone": "Europe/Madrid",
        "calendar": {"status": "disconnected", "google_email": None, "connected_at": None},
    }


def test_the_cookie_is_secure_in_production(
    anonymous: TestClient, lucia: Recruiter, monkeypatch: pytest.MonkeyPatch
):
    import sazon.api.deps as deps

    monkeypatch.setattr(deps, "get_settings", lambda: Settings(env="production"))
    assert "Secure" in login(anonymous).headers["set-cookie"]


def test_wrong_password_and_unknown_email_get_the_same_answer(
    anonymous: TestClient, lucia: Recruiter, service: ScreeningService, now: datetime
):
    recruiters.create(
        service.repo, now, name="Sin clave", email="nopass@gruposazon.example", country="MX"
    )
    off = recruiters.create(
        service.repo,
        now,
        name="Baja",
        email="off@gruposazon.example",
        country="ES",
        password=PASSWORD,
    )
    recruiters.update(service.repo, off.id, now, active=False)
    attempts = [
        login(anonymous, password="not the password"),
        login(anonymous, email="nobody@gruposazon.example"),
        login(anonymous, email="nopass@gruposazon.example"),  # no password set yet
        login(anonymous, email="off@gruposazon.example"),  # deactivated
    ]
    assert {r.status_code for r in attempts} == {401}
    assert all(r.json() == WRONG for r in attempts)
    assert all("set-cookie" not in r.headers for r in attempts)
    assert anonymous.get("/api/me").status_code == 401


def test_login_attempts_are_logged_without_the_email_or_password(
    anonymous: TestClient, lucia: Recruiter, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.INFO):
        login(anonymous, password="a wrong guess")
        login(anonymous)
    events = [r.getMessage() for r in caplog.records if r.name == "sazon.events"]
    assert any("event=login_failed" in line for line in events)
    assert any("event=login_succeeded" in line and lucia.id in line for line in events)
    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert "lucia" not in everything.lower()
    assert PASSWORD not in everything
    assert "a wrong guess" not in everything


def test_login_is_rate_limited_per_email(anonymous: TestClient, lucia: Recruiter):
    for _ in range(5):
        assert login(anonymous, password="guess").status_code == 401
    blocked = login(anonymous)  # even the right password, until the bucket refills
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    # Another address from the same IP is not blocked by this one's limit.
    assert login(anonymous, email="other@gruposazon.example").status_code == 401


def test_login_is_rate_limited_per_ip(anonymous: TestClient, lucia: Recruiter):
    app_of(anonymous).state.login_ip_limit = TokenBucket(3, 900)
    for n in range(3):
        assert login(anonymous, email=f"x{n}@gruposazon.example").status_code == 401
    assert login(anonymous).status_code == 429
    # Behind Caddy the client's address is the last X-Forwarded-For entry.
    assert login(anonymous, headers={"x-forwarded-for": "203.0.113.9"}).status_code == 200


def test_logout_ends_the_session(anonymous: TestClient, lucia: Recruiter):
    login(anonymous)
    response = anonymous.post("/api/auth/logout", json={})
    assert response.status_code == 204
    assert 'sazon_session=""' in response.headers["set-cookie"]
    assert anonymous.get("/api/me").status_code == 401


def test_a_new_password_ends_open_sessions(
    anonymous: TestClient, lucia: Recruiter, service: ScreeningService, now: datetime
):
    login(anonymous)
    recruiters.update(service.repo, lucia.id, now, password="another long password")
    assert anonymous.get("/api/me").status_code == 401
    assert login(anonymous).status_code == 401
    assert login(anonymous, password="another long password").status_code == 200


def test_a_deactivated_recruiter_is_signed_out(
    anonymous: TestClient, lucia: Recruiter, service: ScreeningService, now: datetime
):
    login(anonymous)
    recruiters.update(service.repo, lucia.id, now, active=False)
    assert anonymous.get("/api/me").status_code == 401


def test_a_forged_or_tampered_cookie_is_refused(anonymous: TestClient, lucia: Recruiter):
    forged = Sessions("not-the-server-secret" * 3, 3600).issue(lucia.id, 0)
    anonymous.cookies.set(COOKIE_NAME, forged)
    assert anonymous.get("/api/me").status_code == 401
    anonymous.cookies.clear()
    login(anonymous)
    token = anonymous.cookies.get(COOKIE_NAME)
    assert token
    anonymous.cookies.clear()
    anonymous.cookies.set(COOKIE_NAME, token[:-3] + "AAA")
    assert anonymous.get("/api/me").status_code == 401


# --- expiry ---


class FakeTime:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def ticking(anonymous: TestClient) -> FakeTime:
    clock = FakeTime()
    app_of(anonymous).state.sessions = Sessions("s" * 48, 12 * 3600, clock=clock)
    return clock


def test_a_session_expires_after_twelve_idle_hours(
    anonymous: TestClient, lucia: Recruiter, ticking: FakeTime
):
    login(anonymous)
    ticking.now += 12 * 3600 - 60
    response = anonymous.get("/api/me")
    assert response.status_code == 200
    ticking.now += 12 * 3600 - 60  # 24 hours after sign-in, but 12 since the last request
    assert anonymous.get("/api/me").status_code == 200
    ticking.now += 12 * 3600 + 1  # idle for longer than the limit
    assert anonymous.get("/api/me").json()["detail"] == "Inicia sesión para continuar."


def test_the_cookie_is_reissued_only_once_it_is_a_few_minutes_old(
    anonymous: TestClient, lucia: Recruiter, ticking: FakeTime
):
    login(anonymous)
    assert "set-cookie" not in anonymous.get("/api/me").headers
    ticking.now += REFRESH_AFTER_S + 1
    assert anonymous.get("/api/me").headers["set-cookie"].startswith(f"{COOKIE_NAME}=")


# --- who reaches what ---


@pytest.mark.parametrize("path", DASHBOARD_READS)
def test_dashboard_reads_need_a_session(anonymous: TestClient, path: str):
    response = anonymous.get(path)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_dashboard_writes_and_the_assistant_need_a_session(anonymous: TestClient):
    assert anonymous.post("/api/assistant", json={"question": "¿Cuántos?"}).status_code == 401
    assert anonymous.patch("/api/candidates/x", json={"status": "hired"}).status_code == 401
    assert anonymous.delete("/api/candidates/x").status_code == 401
    assert anonymous.post("/api/recruiters", json={}).status_code == 401


def test_candidate_routes_stay_public(anonymous: TestClient):
    body = StartIn().model_dump(mode="json")
    started = anonymous.post("/api/conversations", json=body)
    assert started.status_code == 200, started.text
    conversation_id = started.json()["conversation_id"]
    assert anonymous.get(f"/api/conversations/{conversation_id}").status_code == 200
    message = anonymous.post(f"/api/conversations/{conversation_id}/messages", json={"text": "Sí"})
    assert message.status_code == 200
    nudge = anonymous.post(f"/api/conversations/{conversation_id}/nudge", json={"attempt": 1})
    assert nudge.status_code != 401
    assert anonymous.get("/healthz").status_code == 200


# --- CSRF ---


def test_writes_from_another_origin_are_refused(client: TestClient):
    evil = {"origin": "https://evil.example"}
    patch = client.patch("/api/candidates/x", json={"status": "hired"}, headers=evil)
    assert patch.status_code == 403
    assert patch.json()["error"] == "forbidden"
    assert client.delete("/api/candidates/x", headers=evil).status_code == 403
    assert client.post("/api/auth/logout", json={}, headers=evil).status_code == 403
    # The dashboard's own origin goes through (404: the candidate does not exist).
    ok = client.patch("/api/candidates/x", json={"status": "hired"}, headers={"origin": ORIGIN})
    assert ok.status_code == 404
    # Reads are not state-changing, whatever the origin.
    assert client.get("/api/me", headers=evil).status_code == 200


JSON_BODY = ("application/json", '{"status": "hired"}')
FORM_BODY = ("application/x-www-form-urlencoded", "status=hired")
TEXT_BODY = ("text/plain", '{"status": "hired"}')


@pytest.mark.parametrize(
    ("method", "origin", "body", "status"),
    [
        # No Origin: not a browser's cross-site write (curl, the CLI, server to server).
        ("PATCH", None, JSON_BODY, 404),
        ("PATCH", ORIGIN, JSON_BODY, 404),
        ("PATCH", ORIGIN + "/", JSON_BODY, 404),
        ("PATCH", ORIGIN, ("application/json; charset=utf-8", '{"status": "hired"}'), 404),
        # A sandboxed iframe or a privacy redirect sends "null": never ours.
        ("PATCH", "null", JSON_BODY, 403),
        ("PATCH", "https://evil.example", JSON_BODY, 403),
        ("PATCH", "https://evil.example", FORM_BODY, 403),
        ("PATCH", ORIGIN.replace("http://", "https://"), JSON_BODY, 403),
        ("PATCH", ORIGIN, FORM_BODY, 415),
        ("PATCH", None, TEXT_BODY, 415),
        ("PATCH", ORIGIN, None, 415),
        # DELETE has no body, so no JSON check: the origin is its whole defence.
        ("DELETE", "https://evil.example", None, 403),
        ("DELETE", "null", None, 403),
        ("DELETE", None, None, 404),
        ("DELETE", ORIGIN, None, 404),
        ("GET", "https://evil.example", None, 404),  # reads change nothing
    ],
)
def test_the_csrf_guard_by_method_origin_and_content_type(
    client: TestClient,
    method: str,
    origin: str | None,
    body: tuple[str, str] | None,
    status: int,
):
    """404: the guard let it through to a candidate that does not exist."""
    headers = {} if origin is None else {"origin": origin}
    content = None
    if body is not None:
        headers["content-type"], content = body
    response = client.request(method, "/api/candidates/x", content=content, headers=headers)
    assert response.status_code == status, response.text


def test_login_from_another_origin_is_refused(anonymous: TestClient, lucia: Recruiter):
    response = login(anonymous, headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_writes_must_be_json(client: TestClient, anonymous: TestClient, lucia: Recruiter):
    """A cross-site form can post urlencoded or text bodies without a preflight; not JSON."""
    form = client.patch("/api/candidates/x", data={"status": "hired"})  # pyright: ignore[reportCallIssue]
    assert form.status_code == 415
    text = anonymous.post(
        "/api/auth/login",
        content=f'{{"email": "{EMAIL}", "password": "{PASSWORD}"}}',
        headers={"content-type": "text/plain"},
    )
    assert text.status_code == 415


# --- admins ---


def test_only_admins_manage_recruiters(anonymous: TestClient, sign_in: Callable[..., Recruiter]):
    sign_in(anonymous, admin=False)
    assert anonymous.get("/api/recruiters").status_code == 200  # the team list is for all
    new = {"name": "Nuevo", "email": "nuevo@gruposazon.example", "country": "MX"}
    assert anonymous.post("/api/recruiters", json=new).status_code == 403
    someone = anonymous.get("/api/recruiters").json()[0]["id"]
    assert anonymous.patch(f"/api/recruiters/{someone}", json={"is_admin": True}).status_code == 403
    assert anonymous.delete(f"/api/recruiters/{someone}").status_code == 403


def test_an_admin_adds_edits_and_deactivates_a_recruiter(client: TestClient):
    created = client.post(
        "/api/recruiters",
        json={
            "name": "Diego",
            "email": " Diego.Reclutador@GrupoSazon.Example",
            "country": "MX",
            "password": "una clave bastante larga",
            "location_ids": ["mx-gdl-centro"],
        },
    )
    assert created.status_code == 201, created.text
    diego = created.json()
    assert diego["interview_modes"] == ["video"]
    assert diego["max_interviews_per_day"] == 15
    assert diego["email"] == "diego.reclutador@gruposazon.example"
    assert diego["timezone"] == "America/Mexico_City"
    assert diego["working_hours"]["mon"] == [["09:00", "18:00"]]
    assert diego["has_password"] is True
    assert "password" not in diego
    assert "password_hash" not in diego

    duplicate = client.post(
        "/api/recruiters",
        json={"name": "Otro", "email": "diego.reclutador@gruposazon.example", "country": "MX"},
    )
    assert duplicate.status_code == 409

    edited = client.patch(
        f"/api/recruiters/{diego['id']}",
        json={"max_interviews_per_day": 5, "working_hours": {"sat": [["10:00", "13:00"]]}},
    )
    assert edited.status_code == 200
    assert edited.json()["max_interviews_per_day"] == 5
    assert edited.json()["working_hours"] == {"sat": [["10:00", "13:00"]]}

    gone = client.delete(f"/api/recruiters/{diego['id']}")
    assert gone.status_code == 200
    assert gone.json()["active"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"name": "X", "email": "not-an-email", "country": "ES"},
        {"name": "X", "email": "x@gruposazon.example", "country": "ES", "password": "short"},
        {"name": "X", "email": "x@gruposazon.example", "country": "ES", "location_ids": ["nope"]},
        {"name": "X", "email": "x@gruposazon.example", "country": "ES", "timezone": "Mars/Base"},
        {"name": "X", "email": "x@gruposazon.example", "country": "ES", "interview_modes": []},
        {
            "name": "X",
            "email": "x@gruposazon.example",
            "country": "ES",
            "interview_modes": ["onsite", "video"],  # in person is the managers' (SAZ-64)
        },
        {
            "name": "X",
            "email": "x@gruposazon.example",
            "country": "ES",
            "working_hours": {"mon": [["14:00", "09:00"]]},
        },
        {"name": "X", "email": "x@gruposazon.example", "country": "ES", "is_root": True},
    ],
)
def test_recruiter_bodies_are_validated(client: TestClient, body: dict[str, Any]):
    response = client.post("/api/recruiters", json=body)
    assert response.status_code == 422
    assert "short" not in response.text  # never echoes a submitted password


def test_an_admin_cannot_lock_themselves_out(
    anonymous: TestClient, sign_in: Callable[..., Recruiter]
):
    me = sign_in(anonymous, admin=True)
    assert anonymous.delete(f"/api/recruiters/{me.id}").status_code == 409
    assert anonymous.patch(f"/api/recruiters/{me.id}", json={"active": False}).status_code == 409
    assert anonymous.patch(f"/api/recruiters/{me.id}", json={"is_admin": False}).status_code == 409


# --- development sign-in ---


def test_dev_login_is_off_in_production(
    anonymous: TestClient, lucia: Recruiter, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(auth_routes, "get_settings", lambda: Settings(env="production"))
    response = anonymous.post("/api/auth/dev-login", json={"email": EMAIL})
    assert response.status_code == 404
    assert anonymous.get("/api/me").status_code == 401


def test_the_api_refuses_to_boot_in_production_without_a_session_secret(
    monkeypatch: pytest.MonkeyPatch, service: ScreeningService
):
    import sazon.api.app as app_module

    settings = Settings(env="production", openrouter_api_key="sk-x")  # pyright: ignore[reportArgumentType]
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    with pytest.raises(app_module.UnsafeProductionConfig, match="SESSION_SECRET"):
        create_app(service)
