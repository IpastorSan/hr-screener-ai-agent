"""Recruiters' calendars: free/busy for availability and one event per booked interview.

`CalendarProvider` is what scheduling uses; `google.GoogleCalendar` talks to the Google Calendar
API with each recruiter's own grant, `fake.FakeCalendar` keeps everything in memory for tests,
demos and offline runs. Times are aware UTC datetimes.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

BOOKING_PROPERTY = "sazonBookingId"
"""`extendedProperties.private` key on every event we create: the booking it belongs to, so the
push sync (`scheduling/push.py`) can match calendar changes back to bookings."""


class CalendarError(Exception):
    """The calendar could not be read or written (network, quota, revoked grant)."""


class SyncTokenExpired(CalendarError):
    """Google no longer accepts the sync token (410 Gone): a full sync is needed."""


@dataclass(frozen=True)
class Interval:
    """`[start, end)` in UTC."""

    start: datetime
    end: datetime

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Guest:
    """Someone invited to an interview besides the candidate: the location's manager, who runs
    an in-person one."""

    email: str
    name: str | None = None


@dataclass(frozen=True)
class EventRequest:
    """An interview as it goes into the recruiter's calendar. `video` adds a Google Meet link;
    otherwise `location` is the kitchen's address. The candidate is invited by email when there
    is one, and so are the `guests` (in person, the manager; the calendar is the organizer's).
    `timezone` is the location's, for how the event is displayed."""

    booking_id: str
    summary: str
    description: str
    start: datetime
    end: datetime
    timezone: str
    video: bool
    location: str | None = None
    attendee_email: str | None = None
    attendee_name: str | None = None
    guests: tuple[Guest, ...] = ()


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    calendar_id: str
    meet_url: str | None = None


class CalendarProvider(Protocol):
    """Each recruiter's own calendar. `recruiter_id` selects whose grant is used."""

    def connected(self, recruiter_ids: Collection[str]) -> set[str]:
        """The recruiters whose calendar can be read and written now."""
        ...

    def busy(
        self, recruiter_ids: Collection[str], start: datetime, end: datetime
    ) -> Mapping[str, list[Interval]]:
        """Busy blocks between `start` and `end` per recruiter. A recruiter whose calendar
        could not be read is left out of the result (callers treat them as unavailable)."""
        ...

    def create_event(self, recruiter_id: str, event: EventRequest) -> CalendarEvent:
        """Create the event, or return the one already created for `event.booking_id`."""
        ...

    def update_event(
        self, recruiter_id: str, existing: CalendarEvent, event: EventRequest
    ) -> CalendarEvent:
        """Move or rewrite an event this recruiter already has."""
        ...

    def delete_event(
        self, recruiter_id: str, existing: CalendarEvent, *, notify: bool = True
    ) -> None:
        """Delete an event, telling the attendees when `notify`. A missing event is fine."""
        ...


# --- push notifications and incremental sync (SAZ-40) ---


@dataclass(frozen=True)
class Attendee:
    """A guest of an event. `owner` marks the calendar's owner (the recruiter): Google lists
    the organizer among the attendees once there are guests, with `self` set for them."""

    email: str
    response: str
    """`needsAction`, `declined`, `tentative` or `accepted`."""
    owner: bool = False


@dataclass(frozen=True)
class EventState:
    """An event as Google has it now. `cancelled` covers deleted events, which may carry no
    other field; `start` is None for an all-day event."""

    event_id: str
    cancelled: bool
    booking_id: str | None = None
    """Our `BOOKING_PROPERTY` marker, when the event carries it."""
    start: datetime | None = None
    end: datetime | None = None
    attendees: tuple[Attendee, ...] = ()


@dataclass(frozen=True)
class Changes:
    """The events changed since a sync token, and the token to start from next time."""

    events: tuple[EventState, ...]
    sync_token: str


@dataclass(frozen=True)
class Watch:
    """A push channel Google opened: `resource_id` identifies the watched calendar."""

    channel_id: str
    resource_id: str
    calendar_id: str
    expires_at: datetime


@runtime_checkable
class CalendarWatcher(Protocol):
    """Push notifications and incremental sync over a recruiter's calendar."""

    def watch(
        self, recruiter_id: str, channel_id: str, token: str, address: str, ttl_s: int
    ) -> Watch:
        """Open a channel: Google posts to `address` with `token` on every change."""
        ...

    def stop_watch(self, recruiter_id: str, channel_id: str, resource_id: str) -> None:
        """Close a channel. One Google no longer knows is fine."""
        ...

    def sync_token(self, recruiter_id: str) -> str:
        """A sync token for the calendar as it is now (a full listing, events skipped)."""
        ...

    def changes(self, recruiter_id: str, sync_token: str) -> Changes:
        """Every event changed since `sync_token`, deleted ones included. Raises
        `SyncTokenExpired` when Google wants a full sync."""
        ...

    def get_event(self, recruiter_id: str, event_id: str) -> EventState | None:
        """One event of the recruiter's calendar. A deleted one is None or, as Google returns
        it, an `EventState` with `cancelled=True`: callers handle both."""
        ...
