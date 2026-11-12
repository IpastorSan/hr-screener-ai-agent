"""SAZ-40 calls of the Google client against a fake Google (`httpx.MockTransport`): opening and
stopping a push channel, the sync token, incremental changes (410 when it expired) and one
event. Nothing here reaches the network."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet

from sazon import recruiters
from sazon.calendar import (
    BOOKING_PROPERTY,
    Attendee,
    CalendarError,
    CalendarWatcher,
    EventState,
    SyncTokenExpired,
)
from sazon.calendar.google import GoogleCalendar, event_state
from sazon.calendar.tokens import TokenCipher
from sazon.config import Settings
from sazon.storage.models import GoogleCredential
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 19, 10, tzinfo=UTC)
TOKEN = "channel-token-never-logged"
BOOKING = "0123456789abcdef0123456789abcdef"


def event(**fields: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": BOOKING,
        "status": "confirmed",
        "start": {"dateTime": "2026-10-20T10:00:00+02:00", "timeZone": "Europe/Madrid"},
        "end": {"dateTime": "2026-10-20T10:30:00+02:00", "timeZone": "Europe/Madrid"},
        "extendedProperties": {"private": {BOOKING_PROPERTY: BOOKING}},
        "attendees": [
            {"email": "ana@gruposazon.example", "organizer": True, "self": True,
             "responseStatus": "accepted"},
            {"email": "Lucia@Example.com", "responseStatus": "needsAction"},
        ],
    }  # fmt: skip
    data.update(fields)
    return data


class FakeGoogle:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pages: list[dict[str, object]] = []
        self.list_status = 200
        self.stop_status = 204
        self.watch_status = 200

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if str(req.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3599})
        path = req.url.path
        if path.endswith("/events/watch"):
            if self.watch_status != 200:
                return httpx.Response(self.watch_status, json={"error": {"code": 401}})
            body = json.loads(req.content)
            return httpx.Response(
                200,
                json={
                    "kind": "api#channel",
                    "id": body["id"],
                    "resourceId": "res-1",
                    "resourceUri": "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                    "token": body["token"],
                    "expiration": "1761487200000",
                },
            )
        if path.endswith("/channels/stop"):
            return httpx.Response(self.stop_status)
        if req.method == "GET" and path.endswith("/events"):
            if self.list_status != 200:
                return httpx.Response(self.list_status, json={"error": {"code": 410}})
            return httpx.Response(200, json=self.pages.pop(0))
        if req.method == "GET" and path.endswith(f"/events/{BOOKING}"):
            return httpx.Response(200, json=event())
        return httpx.Response(404, json={"error": {"code": 404}})


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def setup(google: FakeGoogle) -> tuple[GoogleCalendar, str]:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    cipher = TokenCipher(Fernet.generate_key())
    recruiter = recruiters.create(
        repo, NOW, name="Ana", email="ana@gruposazon.example", country="ES"
    )
    repo.save(
        GoogleCredential(
            recruiter_id=recruiter.id,
            google_email=recruiter.email,
            google_subject="sub-ana",
            refresh_token_encrypted=cipher.encrypt("refresh"),
            connected_at=NOW,
            updated_at=NOW,
        )
    )
    calendar = GoogleCalendar(
        repo,
        client_id="id",
        client_secret="secret",
        cipher=cipher,
        http=httpx.Client(transport=httpx.MockTransport(google)),
    )
    return calendar, recruiter.id


def test_the_google_calendar_is_a_watcher(setup):
    calendar, _ = setup
    assert isinstance(calendar, CalendarWatcher)


def test_watch_registers_a_web_hook_with_the_token_and_ttl(setup, google):
    calendar, ana = setup
    address = "https://sazon.example.com/api/webhooks/google/calendar"
    watch = calendar.watch(ana, "chan-1", TOKEN, address, 604800)

    sent = google.requests[-1]
    assert sent.url.path == "/calendar/v3/calendars/primary/events/watch"
    assert json.loads(sent.content) == {
        "id": "chan-1",
        "type": "web_hook",
        "address": address,
        "token": TOKEN,
        "params": {"ttl": "604800"},
    }
    assert watch.resource_id == "res-1"
    assert watch.calendar_id == "primary"
    assert watch.expires_at == datetime(2025, 10, 26, 14, tzinfo=UTC)


def test_a_refused_watch_is_a_calendar_error(setup, google):
    calendar, ana = setup
    google.watch_status = 400
    with pytest.raises(CalendarError):
        calendar.watch(ana, "chan-1", TOKEN, "https://x.example/hook", 60)


def test_stop_sends_the_channel_and_tolerates_an_unknown_one(setup, google):
    calendar, ana = setup
    calendar.stop_watch(ana, "chan-1", "res-1")
    assert json.loads(google.requests[-1].content) == {"id": "chan-1", "resourceId": "res-1"}
    google.stop_status = 404
    calendar.stop_watch(ana, "chan-1", "res-1")
    google.stop_status = 500
    with pytest.raises(CalendarError):
        calendar.stop_watch(ana, "chan-1", "res-1")


def test_the_sync_token_comes_from_a_full_listing_without_the_events(setup, google):
    calendar, ana = setup
    google.pages = [{"nextPageToken": "p2"}, {"nextSyncToken": "sync-1"}]
    assert calendar.sync_token(ana) == "sync-1"
    first, second = (r for r in google.requests if r.url.path.endswith("/events"))
    assert first.url.params["fields"] == "nextPageToken,nextSyncToken"
    assert "pageToken" not in first.url.params
    assert second.url.params["pageToken"] == "p2"


def test_changes_page_through_and_keep_the_newest_state(setup, google):
    calendar, ana = setup
    google.pages = [
        {"items": [event()], "nextPageToken": "p2"},
        {"items": [event(status="cancelled"), {"id": "other", "status": "confirmed"}],
         "nextSyncToken": "sync-2"},
    ]  # fmt: skip
    changes = calendar.changes(ana, "sync-1")
    assert changes.sync_token == "sync-2"
    assert [(e.event_id, e.cancelled) for e in changes.events] == [
        (BOOKING, True),
        ("other", False),
    ]
    sent = next(r for r in google.requests if r.url.path.endswith("/events"))
    assert sent.url.params["syncToken"] == "sync-1"


def test_an_expired_sync_token_asks_for_a_full_sync(setup, google):
    calendar, ana = setup
    google.list_status = 410
    with pytest.raises(SyncTokenExpired):
        calendar.changes(ana, "sync-1")


def test_get_event_reads_one_event_and_a_missing_one_is_none(setup):
    calendar, ana = setup
    state = calendar.get_event(ana, BOOKING)
    assert state is not None
    assert state.booking_id == BOOKING
    assert calendar.get_event(ana, "gone") is None


def test_event_state_reads_times_marker_and_responses():
    state = event_state(event())
    assert state == EventState(
        event_id=BOOKING,
        cancelled=False,
        booking_id=BOOKING,
        start=datetime(2026, 10, 20, 8, tzinfo=UTC),
        end=datetime(2026, 10, 20, 8, 30, tzinfo=UTC),
        attendees=(
            Attendee("ana@gruposazon.example", "accepted", owner=True),
            Attendee("lucia@example.com", "needsAction", owner=False),
        ),
    )
    deleted = event_state({"id": BOOKING, "status": "cancelled"})
    assert deleted == EventState(event_id=BOOKING, cancelled=True)
    all_day = event_state(event(start={"date": "2026-10-20"}, end={"date": "2026-10-21"}))
    assert all_day.start is None


def test_push_needs_a_public_url_and_production_wants_https():
    assert Settings.model_validate({"public_url": ""}).calendar_push_url is None
    settings = Settings.model_validate({"public_url": "https://sazon.example.com/"})
    assert settings.calendar_push_url == "https://sazon.example.com/api/webhooks/google/calendar"
    base = {"env": "production", "openrouter_api_key": "k", "session_secret": "s" * 40}
    plain = Settings.model_validate(base | {"public_url": "http://sazon.example.com"})
    assert any("PUBLIC_URL" in p for p in plain.production_problems())
