"""Google Calendar over its REST API with httpx, one recruiter's grant per call.

httpx rather than google-api-python-client: it is already a dependency, the calls we make
(freeBusy, insert, patch, delete; watch, stop and list for push sync) are small JSON
requests, and tests replace the transport (`httpx.MockTransport`) instead of patching a
discovery-built client.

Access tokens are refreshed from the stored (encrypted) refresh token and kept only in memory.
Google refusing a refresh token (`invalid_grant`) marks the grant `revoked`, which takes the
recruiter out of availability until they connect again. Tokens never reach logs or errors.
"""

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlmodel import col, select

from sazon.calendar import (
    BOOKING_PROPERTY,
    Attendee,
    CalendarError,
    CalendarEvent,
    Changes,
    EventRequest,
    EventState,
    Interval,
    SyncTokenExpired,
    Watch,
)
from sazon.calendar.tokens import TokenCipher, TokenDecryptError
from sazon.storage.models import CredentialStatus, GoogleCredential
from sazon.storage.repo import Repo, to_utc

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TIMEOUT = httpx.Timeout(10.0)
EXPIRY_MARGIN_S = 60.0
EVENT_ID = re.compile(r"^[a-v0-9]{5,1024}$")
CHANGES_PAGE = 250
TOKEN_PAGE = 2500
MAX_PAGES = 200


def http_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT)


def event_id_for(booking_id: str) -> str:
    """Our event id for a booking: Google accepts client ids in base32hex (`a-v`, `0-9`), so a
    booking id (hex) is used as it is. A retried insert then answers 409 instead of creating a
    second event."""
    if EVENT_ID.match(booking_id):
        return booking_id
    return hashlib.sha256(booking_id.encode()).hexdigest()


def event_body(event: EventRequest) -> dict[str, Any]:
    """The `events.insert` body. Video: a Meet conference (`conferenceDataVersion=1` on the
    request). Onsite: the address as the location. The attendees are the candidate and the
    request's guests (in person, the location's manager); guests do not see each other."""
    zone = ZoneInfo(event.timezone)

    def moment(value: datetime) -> dict[str, str]:
        return {"dateTime": to_utc(value).astimezone(zone).isoformat(), "timeZone": event.timezone}

    body: dict[str, Any] = {
        "id": event_id_for(event.booking_id),
        "summary": event.summary,
        "description": event.description,
        "start": moment(event.start),
        "end": moment(event.end),
        "extendedProperties": {"private": {BOOKING_PROPERTY: event.booking_id}},
        "guestsCanInviteOthers": False,
        "guestsCanSeeOtherGuests": False,
        "reminders": {"useDefault": True},
    }
    invited = [(event.attendee_email, event.attendee_name)] if event.attendee_email else []
    invited += [(guest.email, guest.name) for guest in event.guests]
    attendees: list[dict[str, Any]] = []
    for email, name in invited:
        attendee: dict[str, Any] = {"email": email}
        if name:
            attendee["displayName"] = name
        attendees.append(attendee)
    if attendees:
        body["attendees"] = attendees
    if event.video:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": event.booking_id,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    elif event.location:
        body["location"] = event.location
    return body


def meet_url(data: Mapping[str, Any]) -> str | None:
    """The Meet link of an event resource, when it has one."""
    link = data.get("hangoutLink")
    if isinstance(link, str) and link:
        return link
    conference = data.get("conferenceData") or {}
    for entry in conference.get("entryPoints") or []:
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return str(entry["uri"])
    return None


class GoogleCalendar:
    """`CalendarProvider` over the Google Calendar API. Thread-safe."""

    def __init__(
        self,
        repo: Repo,
        *,
        client_id: str,
        client_secret: str,
        cipher: TokenCipher,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repo = repo
        self._client_id = client_id
        self._client_secret = client_secret
        self._cipher = cipher
        self._http = http or http_client()
        self._clock = clock
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    # --- CalendarProvider ---

    def connected(self, recruiter_ids: Collection[str]) -> set[str]:
        if not recruiter_ids:
            return set()
        with self.repo.session() as session:
            rows = session.exec(
                select(GoogleCredential.recruiter_id)
                .where(col(GoogleCredential.recruiter_id).in_(list(recruiter_ids)))
                .where(col(GoogleCredential.status) == CredentialStatus.CONNECTED)
            ).all()
        return set(rows)

    def busy(
        self, recruiter_ids: Collection[str], start: datetime, end: datetime
    ) -> Mapping[str, list[Interval]]:
        result: dict[str, list[Interval]] = {}
        for recruiter_id in sorted(self.connected(recruiter_ids)):
            try:
                result[recruiter_id] = self._busy_one(recruiter_id, start, end)
            except CalendarError as exc:
                logger.warning("Free/busy unavailable for recruiter %s: %s", recruiter_id, exc)
        return result

    def create_event(self, recruiter_id: str, event: EventRequest) -> CalendarEvent:
        calendar_id = self._calendar_id(recruiter_id)
        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        response = self._call(
            recruiter_id,
            "POST",
            path,
            params={"conferenceDataVersion": 1, "sendUpdates": "all"},
            json=event_body(event),
        )
        if response.status_code == httpx.codes.CONFLICT:  # created by an earlier attempt
            response = self._call(recruiter_id, "GET", f"{path}/{event_id_for(event.booking_id)}")
            if response.is_success and response.json().get("status") == "cancelled":
                raise CalendarError("the event for this booking was deleted")
        data = _json(response, "create event")
        return CalendarEvent(event_id=data["id"], calendar_id=calendar_id, meet_url=meet_url(data))

    def update_event(
        self, recruiter_id: str, existing: CalendarEvent, event: EventRequest
    ) -> CalendarEvent:
        body = event_body(event)
        body.pop("id")
        if event.video:
            body["location"] = None  # an onsite interview turned into a video call
            if existing.meet_url:
                body.pop("conferenceData", None)  # keep the Meet link it has
            else:  # a fresh request id: Google ignores one it has seen for this event
                body["conferenceData"]["createRequest"]["requestId"] = uuid4().hex
        elif existing.meet_url:
            body["conferenceData"] = None  # now onsite: the Meet link goes
        response = self._call(
            recruiter_id,
            "PATCH",
            f"/calendars/{quote(existing.calendar_id, safe='')}/events/{existing.event_id}",
            params={"conferenceDataVersion": 1, "sendUpdates": "all"},
            json=body,
        )
        data = _json(response, "update event")
        return CalendarEvent(
            event_id=data["id"], calendar_id=existing.calendar_id, meet_url=meet_url(data)
        )

    def delete_event(
        self, recruiter_id: str, existing: CalendarEvent, *, notify: bool = True
    ) -> None:
        response = self._call(
            recruiter_id,
            "DELETE",
            f"/calendars/{quote(existing.calendar_id, safe='')}/events/{existing.event_id}",
            params={"sendUpdates": "all" if notify else "none"},
        )
        if response.status_code in (404, 410):  # already gone
            return
        if not response.is_success:
            raise CalendarError(f"delete event: HTTP {response.status_code}")

    # --- CalendarWatcher (SAZ-40) ---

    def watch(
        self, recruiter_id: str, channel_id: str, token: str, address: str, ttl_s: int
    ) -> Watch:
        calendar_id = self._calendar_id(recruiter_id)
        response = self._call(
            recruiter_id,
            "POST",
            f"/calendars/{quote(calendar_id, safe='')}/events/watch",
            json={
                "id": channel_id,
                "type": "web_hook",
                "address": address,
                "token": token,
                "params": {"ttl": str(ttl_s)},
            },
        )
        data = _json(response, "watch")
        try:
            expires = datetime.fromtimestamp(int(data["expiration"]) / 1000, UTC)
            resource_id = str(data["resourceId"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CalendarError("watch: unexpected body") from exc
        return Watch(
            channel_id=channel_id,
            resource_id=resource_id,
            calendar_id=calendar_id,
            expires_at=expires,
        )

    def stop_watch(self, recruiter_id: str, channel_id: str, resource_id: str) -> None:
        response = self._call(
            recruiter_id,
            "POST",
            "/channels/stop",
            json={"id": channel_id, "resourceId": resource_id},
        )
        if response.status_code in (404, 410):  # expired or stopped already
            return
        if not response.is_success:
            raise CalendarError(f"stop channel: HTTP {response.status_code}")

    def sync_token(self, recruiter_id: str) -> str:
        """The full listing Google needs before it hands out a sync token, with the events
        themselves left out of the responses (`fields`)."""
        path = f"/calendars/{quote(self._calendar_id(recruiter_id), safe='')}/events"
        params: dict[str, Any] = {
            "maxResults": TOKEN_PAGE,
            "fields": "nextPageToken,nextSyncToken",
        }
        for _ in range(MAX_PAGES):
            data = _json(self._call(recruiter_id, "GET", path, params=params), "list events")
            if data.get("nextSyncToken"):
                return str(data["nextSyncToken"])
            if not data.get("nextPageToken"):
                break
            params["pageToken"] = data["nextPageToken"]
        raise CalendarError("list events: no sync token")

    def changes(self, recruiter_id: str, sync_token: str) -> Changes:
        path = f"/calendars/{quote(self._calendar_id(recruiter_id), safe='')}/events"
        params: dict[str, Any] = {"syncToken": sync_token, "maxResults": CHANGES_PAGE}
        events: dict[str, EventState] = {}
        for _ in range(MAX_PAGES):
            response = self._call(recruiter_id, "GET", path, params=params)
            if response.status_code == httpx.codes.GONE:
                raise SyncTokenExpired("sync token expired")
            data = _json(response, "list changes")
            for item in data.get("items") or []:
                if isinstance(item, dict) and item.get("id"):
                    state = event_state(item)
                    events[state.event_id] = state  # a later page has the newer state
            if data.get("nextSyncToken"):
                return Changes(events=tuple(events.values()), sync_token=data["nextSyncToken"])
            if not data.get("nextPageToken"):
                break
            params["pageToken"] = data["nextPageToken"]
        raise CalendarError("list changes: no sync token")

    def get_event(self, recruiter_id: str, event_id: str) -> EventState | None:
        calendar_id = self._calendar_id(recruiter_id)
        response = self._call(
            recruiter_id, "GET", f"/calendars/{quote(calendar_id, safe='')}/events/{event_id}"
        )
        if response.status_code in (404, 410):
            return None
        return event_state(_json(response, "get event"))

    def forget(self, recruiter_id: str) -> None:
        """Drop the cached access token (after a disconnect)."""
        with self._lock:
            self._tokens.pop(recruiter_id, None)

    # --- internals ---

    def _busy_one(self, recruiter_id: str, start: datetime, end: datetime) -> list[Interval]:
        calendar_id = self._calendar_id(recruiter_id)
        response = self._call(
            recruiter_id,
            "POST",
            "/freeBusy",
            json={
                "timeMin": to_utc(start).isoformat(),
                "timeMax": to_utc(end).isoformat(),
                "items": [{"id": calendar_id}],
            },
        )
        data = _json(response, "free/busy")
        calendar = (data.get("calendars") or {}).get(calendar_id) or {}
        if calendar.get("errors"):
            reasons = ",".join(str(e.get("reason")) for e in calendar["errors"])
            raise CalendarError(f"free/busy: {reasons}")
        return [
            Interval(_parse_time(block["start"]), _parse_time(block["end"]))
            for block in calendar.get("busy") or []
        ]

    def _calendar_id(self, recruiter_id: str) -> str:
        credential = self._credential(recruiter_id)
        return credential.calendar_id

    def _credential(self, recruiter_id: str) -> GoogleCredential:
        with self.repo.session() as session:
            credential = session.get(GoogleCredential, recruiter_id)
        if credential is None or credential.status != CredentialStatus.CONNECTED:
            raise CalendarError("calendar not connected")
        return credential

    def _call(self, recruiter_id: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One API request with the recruiter's access token, refreshed once on a 401."""
        for attempt in range(2):
            token = self._access_token(recruiter_id, force=attempt > 0)
            try:
                response = self._http.request(
                    method,
                    CALENDAR_API + path,
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise CalendarError(f"{method} {path.split('/')[1]}: {type(exc).__name__}") from exc
            if response.status_code != httpx.codes.UNAUTHORIZED:
                return response
        raise CalendarError("Google refused the access token")

    def _access_token(self, recruiter_id: str, *, force: bool = False) -> str:
        now = self._clock()
        with self._lock:
            cached = self._tokens.get(recruiter_id)
        if cached is not None and not force and cached[1] > now:
            return cached[0]
        credential = self._credential(recruiter_id)
        try:
            refresh_token = self._cipher.decrypt(credential.refresh_token_encrypted)
        except TokenDecryptError:
            self._mark_revoked(recruiter_id, "the stored token cannot be decrypted")
            raise CalendarError("stored token unreadable; connect again") from None
        try:
            response = self._http.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise CalendarError(f"token refresh: {type(exc).__name__}") from exc
        if response.status_code == 400 and _error_code(response) == "invalid_grant":
            self._mark_revoked(recruiter_id, "Google refused the refresh token")
            raise CalendarError("grant revoked; connect again")
        data = _json(response, "token refresh")
        token = str(data["access_token"])
        expires = now + float(data.get("expires_in", 3600)) - EXPIRY_MARGIN_S
        with self._lock:
            self._tokens[recruiter_id] = (token, expires)
        return token

    def _mark_revoked(self, recruiter_id: str, why: str) -> None:
        logger.warning("Google Calendar grant of recruiter %s is unusable: %s", recruiter_id, why)
        self.forget(recruiter_id)
        with self.repo.session() as session:
            credential = session.get(GoogleCredential, recruiter_id)
            if credential is not None:
                credential.status = CredentialStatus.REVOKED
                credential.updated_at = datetime.now(UTC)
                session.add(credential)
                session.commit()


def event_state(data: Mapping[str, Any]) -> EventState:
    """What sync needs from an event resource: status, our marker, times and responses."""
    private = (data.get("extendedProperties") or {}).get("private") or {}
    marker = private.get(BOOKING_PROPERTY)
    attendees = tuple(
        Attendee(
            email=str(guest.get("email") or "").strip().lower(),
            response=str(guest.get("responseStatus") or "needsAction"),
            owner=bool(guest.get("self") or guest.get("organizer")),
        )
        for guest in data.get("attendees") or []
        if isinstance(guest, dict)
    )
    return EventState(
        event_id=str(data["id"]),
        cancelled=data.get("status") == "cancelled",
        booking_id=marker if isinstance(marker, str) and marker else None,
        start=_event_time(data.get("start")),
        end=_event_time(data.get("end")),
        attendees=attendees,
    )


def _event_time(value: object) -> datetime | None:
    """A timed event's moment in UTC; None for an all-day date or a missing field."""
    if not isinstance(value, dict) or not isinstance(value.get("dateTime"), str):
        return None
    try:
        return _parse_time(value["dateTime"])
    except ValueError:
        return None


def _json(response: httpx.Response, what: str) -> dict[str, Any]:
    if not response.is_success:
        raise CalendarError(f"{what}: HTTP {response.status_code} {_error_code(response)}".strip())
    try:
        data = response.json()
    except ValueError as exc:
        raise CalendarError(f"{what}: not JSON") from exc
    if not isinstance(data, dict):
        raise CalendarError(f"{what}: unexpected body")
    return data


def _error_code(response: httpx.Response) -> str:
    """Google's short error code, never the body (it may echo a token)."""
    try:
        data = response.json()
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return str(error.get("status") or error.get("code") or "")
    return ""


def _parse_time(value: str) -> datetime:
    return to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
