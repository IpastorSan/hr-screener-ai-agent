"""An in-memory calendar per recruiter, for tests, demos (`SCHEDULING_SOURCE=fake`) and
offline runs. Behaves like the Google one where scheduling can tell: busy blocks, idempotent
creation per booking, a Meet link for video interviews, and (SAZ-40) push channels and
incremental sync over a change log, with `recruiter_moves`, `recruiter_deletes` and `respond`
standing in for what people do in Google Calendar."""

import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sazon.calendar import (
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


@dataclass
class FakeEvent:
    recruiter_id: str
    request: EventRequest
    event: CalendarEvent
    deleted: bool = False
    owner_response: str = "accepted"
    guest_response: str = "needsAction"
    """The candidate's answer to the invitation."""
    responses: dict[str, str] = field(default_factory=dict)
    """The other guests' answers, by email (`needsAction` until they answer)."""

    def state(self) -> EventState:
        attendees = [Attendee(f"{self.recruiter_id}@calendar.test", self.owner_response, True)]
        if self.request.attendee_email:
            email = self.request.attendee_email.lower()
            attendees.append(Attendee(email, self.guest_response))
        for guest in self.request.guests:
            email = guest.email.lower()
            attendees.append(Attendee(email, self.responses.get(email, "needsAction")))
        if self.deleted:
            return EventState(event_id=self.event.event_id, cancelled=True)
        return EventState(
            event_id=self.event.event_id,
            cancelled=False,
            booking_id=self.request.booking_id,
            start=self.request.start,
            end=self.request.end,
            attendees=tuple(attendees),
        )


@dataclass
class FakeChannel:
    recruiter_id: str
    token: str
    address: str
    resource_id: str
    expires_at: datetime


@dataclass
class FakeCalendar:
    """`connected_ids` None means every recruiter is connected. `busy_blocks` are the busy
    times outside our own events (meetings, holidays). `failing` recruiters raise
    `CalendarError` on every call (and are left out of free/busy); `failing_writes` only on
    writes."""

    connected_ids: set[str] | None = None
    busy_blocks: dict[str, list[Interval]] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    failing_writes: set[str] = field(default_factory=set)
    events: dict[str, FakeEvent] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    channels: dict[str, FakeChannel] = field(default_factory=dict)
    failing_watch: set[str] = field(default_factory=set)
    """Recruiters whose `watch` raises (Google refusing the webhook address, say)."""
    expired_tokens: set[str] = field(default_factory=set)
    """Sync tokens `changes` answers with `SyncTokenExpired` (Google's 410)."""
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _log: list[tuple[str, str]] = field(default_factory=list, repr=False)
    """`(recruiter_id, event_id)` per change, in order; a sync token is a position in it."""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def connect(self, recruiter_id: str) -> None:
        if self.connected_ids is not None:
            self.connected_ids.add(recruiter_id)

    def add_busy(self, recruiter_id: str, start: datetime, end: datetime) -> None:
        self.busy_blocks.setdefault(recruiter_id, []).append(Interval(start, end))

    def live_events(self, recruiter_id: str | None = None) -> list[FakeEvent]:
        return [
            event
            for event in self.events.values()
            if not event.deleted and (recruiter_id is None or event.recruiter_id == recruiter_id)
        ]

    def connected(self, recruiter_ids: Collection[str]) -> set[str]:
        if self.connected_ids is None:
            return set(recruiter_ids)
        return {rid for rid in recruiter_ids if rid in self.connected_ids}

    def busy(
        self, recruiter_ids: Collection[str], start: datetime, end: datetime
    ) -> Mapping[str, list[Interval]]:
        self.calls.append("busy")
        window = Interval(start, end)
        result: dict[str, list[Interval]] = {}
        for rid in self.connected(recruiter_ids):
            if rid in self.failing:
                continue
            blocks = list(self.busy_blocks.get(rid, []))
            blocks += [
                Interval(e.request.start, e.request.end)
                for e in self.live_events(rid)
                if e.owner_response != "declined"  # free/busy skips declined events
            ]
            result[rid] = [block for block in blocks if block.overlaps(window)]
        return result

    def create_event(self, recruiter_id: str, event: EventRequest) -> CalendarEvent:
        self.calls.append("create")
        self._check(recruiter_id)
        with self._lock:
            existing = self.events.get(event.booking_id)
            if existing is not None and not existing.deleted:
                return existing.event
            created = CalendarEvent(
                event_id=event.booking_id,
                calendar_id="primary",
                meet_url=_meet_url(event.booking_id) if event.video else None,
            )
            self.events[event.booking_id] = FakeEvent(recruiter_id, event, created)
            self._log.append((recruiter_id, created.event_id))
            return created

    def update_event(
        self, recruiter_id: str, existing: CalendarEvent, event: EventRequest
    ) -> CalendarEvent:
        self.calls.append("update")
        self._check(recruiter_id)
        stored = self._find(existing.event_id)
        if stored is None or stored.recruiter_id != recruiter_id:
            raise CalendarError("no such event")
        meet = stored.event.meet_url or (_meet_url(existing.event_id) if event.video else None)
        stored.request = event
        stored.event = replace(stored.event, meet_url=meet if event.video else None)
        self._changed(recruiter_id, stored.event.event_id)
        return stored.event

    def delete_event(
        self, recruiter_id: str, existing: CalendarEvent, *, notify: bool = True
    ) -> None:
        self.calls.append("delete" if notify else "delete-quiet")
        self._check(recruiter_id)
        stored = self._find(existing.event_id)
        if stored is not None:
            stored.deleted = True
            self._changed(stored.recruiter_id, stored.event.event_id)

    # --- CalendarWatcher ---

    def watch(
        self, recruiter_id: str, channel_id: str, token: str, address: str, ttl_s: int
    ) -> Watch:
        self.calls.append("watch")
        self._check(recruiter_id)
        if recruiter_id in self.failing_watch:
            raise CalendarError("watch: HTTP 400 push.webhookUrlUnauthorized")
        channel = FakeChannel(
            recruiter_id=recruiter_id,
            token=token,
            address=address,
            resource_id=f"resource-{recruiter_id}",
            expires_at=self.clock() + timedelta(seconds=ttl_s),
        )
        self.channels[channel_id] = channel
        return Watch(channel_id, channel.resource_id, "primary", channel.expires_at)

    def stop_watch(self, recruiter_id: str, channel_id: str, resource_id: str) -> None:
        self.calls.append("stop")
        self._check(recruiter_id)
        self.channels.pop(channel_id, None)

    def sync_token(self, recruiter_id: str) -> str:
        self.calls.append("sync-token")
        self._check(recruiter_id)
        with self._lock:
            return f"fake-{len(self._log)}"

    def changes(self, recruiter_id: str, sync_token: str) -> Changes:
        self.calls.append("changes")
        self._check(recruiter_id)
        if sync_token in self.expired_tokens or not sync_token.startswith("fake-"):
            raise SyncTokenExpired("sync token expired")
        since = int(sync_token.removeprefix("fake-"))
        with self._lock:
            changed = [eid for rid, eid in self._log[since:] if rid == recruiter_id]
            token = f"fake-{len(self._log)}"
        states = {eid: self._state(eid) for eid in dict.fromkeys(changed)}
        return Changes(tuple(s for s in states.values() if s is not None), token)

    def get_event(self, recruiter_id: str, event_id: str) -> EventState | None:
        self.calls.append("get")
        self._check(recruiter_id)
        state = self._state(event_id)
        return None if state is None or state.cancelled else state

    # --- what people do in Google Calendar ---

    def recruiter_moves(self, event_id: str, start: datetime, end: datetime | None = None) -> None:
        stored = self._require(event_id)
        length = stored.request.end - stored.request.start
        stored.request = replace(stored.request, start=start, end=end or start + length)
        self._changed(stored.recruiter_id, event_id)

    def recruiter_deletes(self, event_id: str) -> None:
        stored = self._require(event_id)
        stored.deleted = True
        self._changed(stored.recruiter_id, event_id)

    def respond(
        self, event_id: str, response: str, *, owner: bool, email: str | None = None
    ) -> None:
        """The recruiter (`owner`), another guest (`email`, the manager) or else the candidate
        answers the invitation."""
        stored = self._require(event_id)
        if owner:
            stored.owner_response = response
        elif email is not None:
            stored.responses[email.lower()] = response
        else:
            stored.guest_response = response
        self._changed(stored.recruiter_id, event_id)

    def _require(self, event_id: str) -> FakeEvent:
        stored = self._find(event_id)
        if stored is None:
            raise KeyError(event_id)
        return stored

    def _state(self, event_id: str) -> EventState | None:
        found = [e for e in self.events.values() if e.event.event_id == event_id]
        live = [e for e in found if not e.deleted]
        chosen = live[0] if live else found[0] if found else None
        return chosen.state() if chosen is not None else None

    def _changed(self, recruiter_id: str, event_id: str) -> None:
        with self._lock:
            self._log.append((recruiter_id, event_id))

    def _find(self, event_id: str) -> FakeEvent | None:
        return next(
            (e for e in self.events.values() if e.event.event_id == event_id and not e.deleted),
            None,
        )

    def _check(self, recruiter_id: str) -> None:
        if recruiter_id in self.failing or recruiter_id in self.failing_writes:
            raise CalendarError("calendar unavailable")


def _meet_url(seed: str) -> str:
    code = seed[:10].ljust(10, "x")
    return f"https://meet.google.com/{code[:3]}-{code[3:7]}-{code[7:10]}"
