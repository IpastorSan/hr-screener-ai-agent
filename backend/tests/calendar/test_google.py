"""The Google Calendar client against a fake Google (`httpx.MockTransport`): event payloads,
free/busy, token refresh and revocation. Nothing here reaches the network."""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet

from sazon import recruiters
from sazon.calendar import BOOKING_PROPERTY, CalendarError, CalendarEvent, EventRequest, Guest
from sazon.calendar.google import GoogleCalendar, event_body, event_id_for
from sazon.calendar.tokens import TokenCipher, TokenDecryptError
from sazon.config import Settings
from sazon.storage.models import CredentialStatus, GoogleCredential
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 19, 10, tzinfo=UTC)
START = datetime(2026, 10, 20, 8, tzinfo=UTC)
REFRESH = "1//refresh-token-never-logged"
BOOKING = "0123456789abcdef0123456789abcdef"


def request(video: bool, **overrides: object) -> EventRequest:
    fields: dict[str, object] = {
        "booking_id": BOOKING,
        "summary": "Entrevista Grupo Sazón: Lucía García",
        "description": "Entrevista de 20 minutos.",
        "start": START,
        "end": START + timedelta(minutes=20),
        "timezone": "Europe/Madrid",
        "video": video,
        "location": None if video else "Carrer de Verdi 45, 08012 Barcelona",
        "attendee_email": "lucia@example.com",
        "attendee_name": "Lucía García",
    }
    fields.update(overrides)
    return EventRequest(**fields)  # pyright: ignore[reportArgumentType]


def test_an_onsite_event_has_the_address_and_no_conference():
    body = event_body(request(video=False))
    assert body["id"] == BOOKING
    assert body["start"] == {"dateTime": "2026-10-20T10:00:00+02:00", "timeZone": "Europe/Madrid"}
    assert body["end"] == {"dateTime": "2026-10-20T10:20:00+02:00", "timeZone": "Europe/Madrid"}
    assert body["location"] == "Carrer de Verdi 45, 08012 Barcelona"
    assert "conferenceData" not in body
    assert body["attendees"] == [{"email": "lucia@example.com", "displayName": "Lucía García"}]
    assert body["extendedProperties"] == {"private": {BOOKING_PROPERTY: BOOKING}}
    assert body["guestsCanSeeOtherGuests"] is False


def test_a_video_event_asks_for_a_meet_conference():
    body = event_body(request(video=True))
    assert body["conferenceData"] == {
        "createRequest": {"requestId": BOOKING, "conferenceSolutionKey": {"type": "hangoutsMeet"}}
    }
    assert "location" not in body


def test_an_in_person_event_invites_the_manager_too():
    manager = Guest("gerencia.es-bcn-gracia@gruposazon.example", "Nuria Castells")
    body = event_body(request(video=False, guests=(manager,)))
    assert body["attendees"] == [
        {"email": "lucia@example.com", "displayName": "Lucía García"},
        {"email": "gerencia.es-bcn-gracia@gruposazon.example", "displayName": "Nuria Castells"},
    ]
    assert body["guestsCanSeeOtherGuests"] is False
    alone = event_body(request(video=False, attendee_email=None, guests=(manager,)))
    assert alone["attendees"] == [body["attendees"][1]]


def test_an_event_without_an_email_invites_nobody():
    assert "attendees" not in event_body(request(video=False, attendee_email=None))


def test_event_ids_are_valid_base32hex():
    assert event_id_for(BOOKING) == BOOKING
    other = event_id_for("not-hex-Z")
    assert set(other) <= set("0123456789abcdef")
    assert len(other) == 64


class FakeGoogle:
    """Answers the token, freeBusy and events endpoints and records what it was sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.refreshes = 0
        self.refresh_error: str | None = None
        self.unauthorized_once = False
        self.insert_status = 200
        self.busy: list[dict[str, str]] = []
        self.freebusy_errors: list[dict[str, str]] | None = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url = str(req.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            if self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error})
            self.refreshes += 1
            return httpx.Response(
                200, json={"access_token": f"at-{self.refreshes}", "expires_in": 3599}
            )
        if self.unauthorized_once:
            self.unauthorized_once = False
            return httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}})
        if req.url.path.endswith("/freeBusy"):
            calendar: dict[str, object] = {"busy": self.busy}
            if self.freebusy_errors:
                calendar = {"errors": self.freebusy_errors}
            return httpx.Response(200, json={"calendars": {"primary": calendar}})
        if req.method == "POST" and req.url.path.endswith("/events"):
            if self.insert_status != 200:
                return httpx.Response(self.insert_status, json={"error": {"code": 409}})
            body = json.loads(req.content)
            return httpx.Response(200, json=self.created(body))
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": BOOKING,
                    "status": "confirmed",
                    "hangoutLink": "https://meet.google.com/abc-defg-hij",
                },
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": req.url.path.rsplit("/", 1)[1]})
        if req.method == "DELETE":
            return httpx.Response(410)
        return httpx.Response(404)

    @staticmethod
    def created(body: dict[str, object]) -> dict[str, object]:
        data = dict(body)
        if "conferenceData" in body:
            data["hangoutLink"] = "https://meet.google.com/abc-defg-hij"
        return data

    def last(self, path_end: str) -> httpx.Request:
        return next(r for r in reversed(self.requests) if r.url.path.endswith(path_end))


@pytest.fixture
def repo() -> Repo:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    return repo


@pytest.fixture
def cipher() -> TokenCipher:
    return TokenCipher(Fernet.generate_key())


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def ana(repo: Repo, cipher: TokenCipher) -> str:
    recruiter = recruiters.create(
        repo, NOW, name="Ana", email="ana@gruposazon.example", country="ES"
    )
    repo.save(
        GoogleCredential(
            recruiter_id=recruiter.id,
            google_email=recruiter.email,
            google_subject="sub-ana",
            scopes=["openid"],
            refresh_token_encrypted=cipher.encrypt(REFRESH),
            connected_at=NOW,
            updated_at=NOW,
        )
    )
    return recruiter.id


@pytest.fixture
def calendar(repo: Repo, cipher: TokenCipher, google: FakeGoogle) -> GoogleCalendar:
    return GoogleCalendar(
        repo,
        client_id="client-id",
        client_secret="client-secret",
        cipher=cipher,
        http=httpx.Client(transport=httpx.MockTransport(google)),
    )


def test_creating_an_event_sends_meet_and_invites(calendar, google, ana):
    event = calendar.create_event(ana, request(video=True))

    assert event == CalendarEvent(
        event_id=BOOKING, calendar_id="primary", meet_url="https://meet.google.com/abc-defg-hij"
    )
    sent = google.last("/events")
    assert sent.url.params["conferenceDataVersion"] == "1"
    assert sent.url.params["sendUpdates"] == "all"
    assert sent.url.path == "/calendar/v3/calendars/primary/events"
    assert sent.headers["authorization"] == "Bearer at-1"
    token_call = google.requests[0]
    assert b"grant_type=refresh_token" in token_call.content
    assert b"refresh-token-never-logged" in token_call.content  # only to Google's token endpoint


def test_a_repeated_insert_returns_the_existing_event(calendar, google, ana):
    google.insert_status = 409
    event = calendar.create_event(ana, request(video=True))
    assert event.event_id == BOOKING
    assert event.meet_url == "https://meet.google.com/abc-defg-hij"
    assert google.requests[-1].method == "GET"


def test_the_access_token_is_cached_and_refreshed_on_a_401(calendar, google, ana):
    calendar.busy([ana], START, START + timedelta(days=1))
    calendar.busy([ana], START, START + timedelta(days=1))
    assert google.refreshes == 1
    google.unauthorized_once = True
    calendar.busy([ana], START, START + timedelta(days=1))
    assert google.refreshes == 2
    assert google.last("/freeBusy").headers["authorization"] == "Bearer at-2"


def test_free_busy_is_parsed_into_utc_blocks(calendar, google, ana):
    google.busy = [{"start": "2026-10-20T10:00:00+02:00", "end": "2026-10-20T11:00:00Z"}]
    blocks = calendar.busy([ana, "not-connected"], START, START + timedelta(days=1))
    assert list(blocks) == [ana]
    [block] = blocks[ana]
    assert (block.start, block.end) == (START, datetime(2026, 10, 20, 11, tzinfo=UTC))
    body = json.loads(google.last("/freeBusy").content)
    assert body["items"] == [{"id": "primary"}]
    assert body["timeMin"] == "2026-10-20T08:00:00+00:00"


def test_a_calendar_that_cannot_be_read_is_left_out(calendar, google, ana):
    google.freebusy_errors = [{"domain": "global", "reason": "notFound"}]
    assert calendar.busy([ana], START, START + timedelta(days=1)) == {}


def test_a_refused_refresh_token_marks_the_grant_revoked(calendar, google, repo, ana, caplog):
    google.refresh_error = "invalid_grant"
    with caplog.at_level(logging.DEBUG):
        assert calendar.busy([ana], START, START + timedelta(days=1)) == {}
    with repo.session() as session:
        credential = session.get(GoogleCredential, ana)
    assert credential is not None
    assert credential.status == CredentialStatus.REVOKED
    assert calendar.connected([ana]) == set()
    assert REFRESH not in caplog.text
    with pytest.raises(CalendarError):
        calendar.create_event(ana, request(video=False))


def test_patch_keeps_the_meet_link_and_delete_tolerates_a_gone_event(calendar, google, ana):
    existing = CalendarEvent(BOOKING, "primary", "https://meet.google.com/abc-defg-hij")
    calendar.update_event(ana, existing, request(video=True, start=START + timedelta(hours=1)))
    patch = google.last(BOOKING)
    assert patch.method == "PATCH"
    body = json.loads(patch.content)
    assert "conferenceData" not in body
    assert "id" not in body
    assert body["start"]["dateTime"] == "2026-10-20T11:00:00+02:00"

    calendar.delete_event(ana, existing)  # 410: already deleted
    assert google.requests[-1].url.params["sendUpdates"] == "all"
    calendar.delete_event(ana, existing, notify=False)
    assert google.requests[-1].url.params["sendUpdates"] == "none"


def test_a_mode_change_on_the_same_event_swaps_the_meet_link_and_the_address(calendar, google, ana):
    with_meet = CalendarEvent(BOOKING, "primary", "https://meet.google.com/abc-defg-hij")
    calendar.update_event(ana, with_meet, request(video=False))
    onsite = json.loads(google.last(BOOKING).content)
    assert onsite["conferenceData"] is None  # PATCH null: the Meet link is removed
    assert onsite["location"] == "Carrer de Verdi 45, 08012 Barcelona"

    without_meet = CalendarEvent(BOOKING, "primary", None)
    calendar.update_event(ana, without_meet, request(video=True))
    video = json.loads(google.last(BOOKING).content)
    assert video["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {
        "type": "hangoutsMeet"
    }
    assert video["conferenceData"]["createRequest"]["requestId"] != BOOKING
    assert video["location"] is None  # the kitchen's address goes
    assert google.last(BOOKING).url.params["conferenceDataVersion"] == "1"

    calendar.update_event(ana, without_meet, request(video=False))
    plain = json.loads(google.last(BOOKING).content)
    assert "conferenceData" not in plain


def test_tokens_are_encrypted_at_rest(cipher: TokenCipher):
    stored = cipher.encrypt(REFRESH)
    assert REFRESH not in stored
    assert cipher.decrypt(stored) == REFRESH
    with pytest.raises(TokenDecryptError):
        TokenCipher(Fernet.generate_key()).decrypt(stored)


def problems(**fields: object) -> list[str]:
    base = {"env": "production", "openrouter_api_key": "k", "session_secret": "s" * 40}
    return Settings.model_validate(base | fields).production_problems()


def test_production_needs_an_encryption_key_with_google_on():
    google = {
        "google_client_id": "id",
        "google_client_secret": "secret",
        "google_workspace_domain": "sazon.example",
    }
    assert any("TOKEN_ENCRYPTION_KEY is empty" in p for p in problems(**google))
    bad = problems(**google, token_encryption_key="nope")
    assert any("not a Fernet key" in p for p in bad)
    assert problems(**google, token_encryption_key=Fernet.generate_key().decode()) == []
    assert problems() == []
    calendarless = problems(scheduling_source="google")
    assert any("SCHEDULING_SOURCE=google without" in p for p in calendarless)
    assert any("SCHEDULING_SOURCE=fake" in p for p in problems(scheduling_source="fake"))


def test_google_stays_off_without_a_workspace_domain():
    """No default domain: an empty one would match a personal account, which has no `hd`."""
    google = {"google_client_id": "id", "google_client_secret": "secret"}
    assert Settings.model_validate(google).google_workspace_domain == ""
    assert not Settings.model_validate(google).google_configured
    key = Fernet.generate_key().decode()
    assert problems(**google, token_encryption_key=key) == [
        "GOOGLE_CLIENT_ID is set without GOOGLE_WORKSPACE_DOMAIN"
    ]
