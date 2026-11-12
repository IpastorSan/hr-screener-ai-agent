"""Two-way booking sync with Google Calendar: push channels and reconciliation (SAZ-40).

Every connected recruiter calendar has a push channel (`events.watch`) to
`$PUBLIC_URL/api/webhooks/google/calendar`. Google's notification only says "something
changed"; the webhook answers 200 at once and `CalendarPush` lists what changed since the
stored sync token (a full resync of our upcoming bookings when Google answers 410). One sync
runs at a time per calendar: a notification that arrives meanwhile runs one more after it, so
duplicates and out-of-order notifications collapse into syncs that read the calendar as it is.

Only our events count: an event is ours when a booking holds its id (or it carries our
private `BOOKING_PROPERTY` marker). What a change does to the booking (`classify`):

- deleted or cancelled: the booking is cancelled, the conversation goes back to scheduling and
  the candidate finds a notice and new times when they open the chat again;
- the recruiter declined: another free recruiter gets a video interview (new event, old one
  deleted), else as deleted;
- the location's manager declined an in-person one (SAZ-65; the event is on the organizer's
  calendar, the manager a guest): cancelled, event deleted (Google tells the candidate), new
  times offered in the chat; nobody else takes it;
- the candidate declined the invitation: cancelled by the candidate, event deleted quietly,
  new times offered if they come back;
- moved: the booking follows the calendar (Google already told the candidate, and the chat
  says so too). A time our rules would not have offered (outside working hours, over another
  interview, past...) is kept but flagged on the booking for the recruiter (`calendar_issue`).

Applying a change twice changes nothing, since each one compares the calendar with the
booking first. Channels last `CALENDAR_WATCH_TTL_HOURS`; `sazon calendar-watch renew` (daily)
replaces those expiring within 48 hours. Tokens are random per channel, stored hashed and
never logged.
"""

import asyncio
import hashlib
import hmac
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlmodel import col, delete, select, update

from sazon import metrics
from sazon.calendar import CalendarError, CalendarWatcher, EventState, SyncTokenExpired
from sazon.config import Settings
from sazon.engine import ConversationState, rebook, reoffer
from sazon.engine.models import CandidateProfile, Language
from sazon.engine.slots import Booked
from sazon.engine.spoken import Register, spoken_when
from sazon.llm.interface import Channel
from sazon.observability import record
from sazon.scheduling import service as scheduling
from sazon.storage.models import (
    Booking,
    BookingStatus,
    CalendarChannel,
    Candidate,
    CredentialStatus,
    GoogleCredential,
    InterviewMode,
    InterviewSlot,
    Turn,
    TurnRole,
)
from sazon.storage.repo import Repo, state_of, to_utc

if TYPE_CHECKING:
    from sazon.service import ScreeningService

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/api/webhooks/google/calendar"
RENEW_WITHIN = timedelta(hours=48)
HEALTHY_FOR = timedelta(hours=24)
RESYNC_FROM = timedelta(hours=12)
"""A full resync looks at bookings from this long ago on (this morning's interviews too)."""
MAX_HEADER = 256


class Change(StrEnum):
    NONE = "none"
    MOVED = "moved"
    CANCELLED = "cancelled"
    RECRUITER_DECLINED = "recruiter_declined"
    MANAGER_DECLINED = "manager_declined"
    CANDIDATE_DECLINED = "candidate_declined"


def classify(
    event: EventState,
    starts_at: datetime,
    candidate_email: str | None,
    manager_email: str | None = None,
) -> Change:
    """What happened to a booked interview's event, most decisive first. `manager_email` is
    the in-person interviewer's, a guest of the event."""
    if event.cancelled:
        return Change.CANCELLED
    if any(guest.owner and guest.response == "declined" for guest in event.attendees):
        return Change.RECRUITER_DECLINED
    manager = (manager_email or "").strip().lower()
    if manager and any(
        not guest.owner and guest.email == manager and guest.response == "declined"
        for guest in event.attendees
    ):
        return Change.MANAGER_DECLINED
    email = (candidate_email or "").strip().lower()
    if email and any(
        not guest.owner and guest.email == email and guest.response == "declined"
        for guest in event.attendees
    ):
        return Change.CANDIDATE_DECLINED
    if event.start is None or to_utc(event.start) != to_utc(starts_at):
        return Change.MOVED
    return Change.NONE


# --- what the candidate reads in the chat ---

CANCELLED_NOTICE: dict[Register, str] = {
    "es-ES": (
        "Lo siento: el equipo de selección ha cancelado tu entrevista del {when}. "
        "Elige otro horario y te lo confirmo."
    ),
    "es-MX": (
        "Lo siento: el equipo de reclutamiento canceló su entrevista del {when}. "
        "Elija otro horario y se lo confirmo."
    ),
    "en": "Sorry: the recruiting team cancelled your interview on {when}. "
    "Pick another time and I'll confirm it.",
}
MANAGER_DECLINED_NOTICE: dict[Register, str] = {
    "es-ES": (
        "Lo siento: la persona encargada del local de {location} no puede hacer tu entrevista "
        "del {when}. Elige otro horario y te lo confirmo."
    ),
    "es-MX": (
        "Lo siento: la gerencia del local de {location} no puede hacer su entrevista del {when}. "
        "Elija otro horario y se lo confirmo."
    ),
    "en": "Sorry: the manager of the {location} location can't do your interview on {when}. "
    "Pick another time and I'll confirm it.",
}
DECLINED_NOTICE: dict[Register, str] = {
    "es-ES": (
        "Has rechazado la invitación a la entrevista del {when}, así que la hemos anulado. "
        "Si quieres, elige otro horario."
    ),
    "es-MX": (
        "Rechazó la invitación a la entrevista del {when}, así que la cancelamos. "
        "Si gusta, elija otro horario."
    ),
    "en": "You declined the invitation for your interview on {when}, so we've cancelled it. "
    "If you like, pick another time.",
}
MOVED_NOTICE: dict[Register, str] = {
    "es-ES": (
        "Hemos cambiado tu entrevista con la persona encargada del local de {location}: ahora "
        "es el {when}."
    ),
    "es-MX": "Cambiamos su entrevista con la gerencia del local de {location}: ahora es el {when}.",
    "en": "We've moved your interview with the manager of the {location} location: it's now on "
    "{when}.",
}
"""In person: the interview is with the location's manager (SAZ-65)."""
MOVED_VIDEO_NOTICE: dict[Register, str] = {
    "es-ES": "El equipo de selección ha cambiado tu videollamada: ahora es el {when}.",
    "es-MX": "El equipo de reclutamiento cambió su videollamada: ahora es el {when}.",
    "en": "The recruiting team moved your video interview: it's now on {when}.",
}
REASSIGNED_NOTICE: dict[Register, str] = {
    "es-ES": (
        "Tu entrevista del {when} sigue en pie: la hará otra persona del equipo y te llegará "
        "una invitación nueva."
    ),
    "es-MX": (
        "Su entrevista del {when} sigue en pie: la hará otra persona del equipo y le llegará "
        "una invitación nueva."
    ),
    "en": "Your interview on {when} is still on: someone else from the team will run it, "
    "and you'll get a new invitation.",
}


def _register(state: ConversationState) -> Register:
    if state.language == Language.EN:
        return "en"
    return "es-MX" if state.country == "MX" else "es-ES"


# --- channels ---


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def watcher_of(calendar: object) -> CalendarWatcher | None:
    return calendar if isinstance(calendar, CalendarWatcher) else None


def verify(repo: Repo, channel_id: str, token: str, resource_id: str) -> CalendarChannel | None:
    """The channel a notification claims to come from, when its token and resource match."""
    if not channel_id or not token or not resource_id:
        return None
    if max(len(channel_id), len(token), len(resource_id)) > MAX_HEADER:
        return None
    with repo.session() as session:
        channel = session.get(CalendarChannel, channel_id)
    if channel is None:
        return None
    # Bytes: compare_digest raises TypeError on a str with non-ASCII characters.
    token_ok = hmac.compare_digest(channel.token_hash.encode(), hash_token(token).encode())
    resource_ok = hmac.compare_digest(channel.resource_id.encode(), resource_id.encode())
    return channel if token_ok and resource_ok else None


def channels_of(repo: Repo, recruiter_id: str | None = None) -> list[CalendarChannel]:
    query = select(CalendarChannel).order_by(col(CalendarChannel.created_at))
    if recruiter_id is not None:
        query = query.where(col(CalendarChannel.recruiter_id) == recruiter_id)
    with repo.session() as session:
        return list(session.exec(query).all())


def connected_recruiters(repo: Repo) -> list[str]:
    with repo.session() as session:
        return sorted(
            session.exec(
                select(GoogleCredential.recruiter_id).where(
                    col(GoogleCredential.status) == CredentialStatus.CONNECTED
                )
            ).all()
        )


def latest_sync_token(repo: Repo, recruiter_id: str) -> str | None:
    rows = [row for row in channels_of(repo, recruiter_id) if row.sync_token]
    return rows[-1].sync_token if rows else None


def save_sync_token(repo: Repo, recruiter_id: str, sync_token: str) -> None:
    with repo.session() as session:
        session.execute(
            update(CalendarChannel)
            .where(col(CalendarChannel.recruiter_id) == recruiter_id)
            .values(sync_token=sync_token)
        )
        session.commit()


def start_watch(
    repo: Repo,
    watcher: CalendarWatcher,
    recruiter_id: str,
    address: str,
    now: datetime,
    ttl: timedelta,
) -> CalendarChannel:
    """Open a new channel on the recruiter's calendar. The sync token comes first (the
    calendar's current one, or a fresh one), so no change between the two is missed."""
    sync_token = latest_sync_token(repo, recruiter_id) or watcher.sync_token(recruiter_id)
    token = secrets.token_urlsafe(32)
    channel_id = uuid4().hex
    watch = watcher.watch(recruiter_id, channel_id, token, address, int(ttl.total_seconds()))
    channel = CalendarChannel(
        id=channel_id,
        recruiter_id=recruiter_id,
        calendar_id=watch.calendar_id,
        resource_id=watch.resource_id,
        token_hash=hash_token(token),
        expires_at=to_utc(watch.expires_at),
        sync_token=sync_token,
        created_at=to_utc(now),
    )
    with repo.session() as session:
        session.add(channel)
        session.commit()
        session.refresh(channel)
    return channel


def stop_watch(
    repo: Repo, watcher: CalendarWatcher | None, recruiter_id: str, *, keep: str | None = None
) -> int:
    """Close the recruiter's channels (all but `keep`) at Google, best effort, and forget
    them: one Google still runs expires on its own and its notifications are refused."""
    stopped = 0
    for channel in channels_of(repo, recruiter_id):
        if channel.id == keep:
            continue
        if watcher is not None:
            try:
                watcher.stop_watch(recruiter_id, channel.id, channel.resource_id)
            except CalendarError as exc:
                metrics.CALENDAR_WATCH_FAILURES.labels("stop").inc()
                logger.warning("Channel of recruiter %s not stopped: %s", recruiter_id, exc)
        with repo.session() as session:
            session.execute(delete(CalendarChannel).where(col(CalendarChannel.id) == channel.id))
            session.commit()
        stopped += 1
    return stopped


@dataclass
class RenewReport:
    started: int = 0
    renewed: int = 0
    stopped: int = 0
    failed: int = 0


def renew(
    repo: Repo,
    watcher: CalendarWatcher,
    address: str,
    now: datetime,
    ttl: timedelta,
    *,
    within: timedelta = RENEW_WITHIN,
    recruiter_id: str | None = None,
    force: bool = False,
) -> RenewReport:
    """Give every connected calendar (or `recruiter_id`'s) a channel that outlives `within`:
    a new one replaces any expiring sooner, and is opened where there is none (`force`
    replaces them all). Channels of calendars no longer connected are stopped."""
    report = RenewReport()
    connected = connected_recruiters(repo)
    if recruiter_id is not None:
        connected = [rid for rid in connected if rid == recruiter_id]
    by_recruiter: dict[str, list[CalendarChannel]] = {}
    for channel in channels_of(repo, recruiter_id):
        by_recruiter.setdefault(channel.recruiter_id, []).append(channel)
    horizon = to_utc(now) + within
    for rid in connected:
        current = by_recruiter.get(rid, [])
        if not force and any(to_utc(c.expires_at) > horizon for c in current):
            continue
        try:
            channel = start_watch(repo, watcher, rid, address, now, ttl)
        except CalendarError as exc:
            report.failed += 1
            op = "renew" if current else "start"
            metrics.CALENDAR_WATCH_FAILURES.labels(op).inc()
            record("calendar_watch_failed", recruiter_id=rid, op=op, error=str(exc))
            continue
        stop_watch(repo, watcher, rid, keep=channel.id)
        if current:
            report.renewed += 1
        else:
            report.started += 1
    for rid in set(by_recruiter) - set(connected_recruiters(repo)):
        report.stopped += stop_watch(repo, watcher, rid)
    return report


def channel_health(repo: Repo, now: datetime) -> dict[str, int]:
    """Connected calendars by channel state: `ok`, `expiring` (under 24 h left) or `missing`."""
    newest: dict[str, datetime] = {}
    for channel in channels_of(repo):
        expires = to_utc(channel.expires_at)
        newest[channel.recruiter_id] = max(expires, newest.get(channel.recruiter_id, expires))
    counts = {"ok": 0, "expiring": 0, "missing": 0}
    for rid in connected_recruiters(repo):
        expires = newest.get(rid)
        if expires is None:
            counts["missing"] += 1
        elif expires > to_utc(now) + HEALTHY_FOR:
            counts["ok"] += 1
        else:
            counts["expiring"] += 1
    return counts


def observe_channels(repo: Repo, now: datetime) -> None:
    for state, count in channel_health(repo, now).items():
        metrics.CALENDAR_CHANNELS.labels(state).set(count)


# --- reconciliation ---


class CalendarPush:
    """Processes notifications in this process, one sync at a time per calendar. `address`
    is None while `PUBLIC_URL` is unset: notifications for existing channels are still
    processed, but no channel is opened."""

    def __init__(
        self,
        service: "ScreeningService",
        watcher: CalendarWatcher,
        *,
        address: str | None = None,
        ttl: timedelta = timedelta(days=7),
    ) -> None:
        self.service = service
        self.repo = service.repo
        self.watcher = watcher
        self.address = address
        self.ttl = ttl
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._again: set[str] = set()
        self._background: set[asyncio.Task[object]] = set()

    @classmethod
    def from_settings(
        cls, settings: Settings, service: "ScreeningService"
    ) -> "CalendarPush | None":
        watcher = watcher_of(service.scheduling.calendar)
        if watcher is None:
            return None
        ttl = timedelta(hours=max(1, settings.calendar_watch_ttl_hours))
        return cls(service, watcher, address=settings.calendar_push_url, ttl=ttl)

    # --- notifications ---

    def notify(self, recruiter_id: str) -> None:
        """A change on the recruiter's calendar: sync it now, or once more after the sync
        already running."""
        if recruiter_id in self._tasks:
            self._again.add(recruiter_id)
            return
        self._tasks[recruiter_id] = asyncio.create_task(self._run(recruiter_id))

    async def drain(self) -> None:
        """Wait for every sync and background task (tests, shutdown)."""
        while self._tasks or self._background:
            await asyncio.gather(*self._tasks.values(), *self._background, return_exceptions=True)

    async def _run(self, recruiter_id: str) -> None:
        try:
            while True:
                self._again.discard(recruiter_id)
                try:
                    await self.sync(recruiter_id)
                except Exception as exc:
                    metrics.CALENDAR_WATCH_FAILURES.labels("sync").inc()
                    logger.warning(
                        "Calendar sync of recruiter %s failed: %s: %s",
                        recruiter_id,
                        type(exc).__name__,
                        exc if isinstance(exc, CalendarError) else "",
                    )
                if recruiter_id not in self._again:
                    break
        finally:
            self._tasks.pop(recruiter_id, None)

    async def sync(self, recruiter_id: str) -> int:
        """Apply what changed on the recruiter's calendar since the last sync. Returns how
        many bookings changed. The sync token moves on only once every change is applied."""
        lock = self._locks.setdefault(recruiter_id, asyncio.Lock())
        async with lock:
            token = await asyncio.to_thread(latest_sync_token, self.repo, recruiter_id)
            changes = None
            if token is not None:
                try:
                    changes = await asyncio.to_thread(self.watcher.changes, recruiter_id, token)
                except SyncTokenExpired:
                    changes = None
            if changes is not None:
                events, token = changes.events, changes.sync_token
            else:
                token = await asyncio.to_thread(self.watcher.sync_token, recruiter_id)
                events = await asyncio.to_thread(self._upcoming_events, recruiter_id)
                metrics.CALENDAR_RECONCILIATIONS.labels("resync").inc()
                record("calendar_resync", recruiter_id=recruiter_id, events=len(events))
            applied = 0
            for event in events:
                applied += await self.reconcile(recruiter_id, event)
            await asyncio.to_thread(save_sync_token, self.repo, recruiter_id, token)
            return applied

    def _upcoming_events(self, recruiter_id: str) -> tuple[EventState, ...]:
        """A full resync, bounded: the events of this recruiter's upcoming bookings as they
        are now; a missing one counts as deleted."""
        since = to_utc(self.service.clock()) - RESYNC_FROM
        with self.repo.session() as session:
            event_ids = session.exec(
                select(Booking.google_event_id)
                .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id))
                .where(col(Booking.recruiter_id) == recruiter_id)
                .where(col(Booking.status) == BookingStatus.BOOKED)
                .where(col(Booking.google_event_id).is_not(None))
                .where(col(InterviewSlot.starts_at) >= since)
            ).all()
        states: list[EventState] = []
        for event_id in event_ids:
            if event_id is None:
                continue
            state = self.watcher.get_event(recruiter_id, event_id)
            states.append(state or EventState(event_id=event_id, cancelled=True))
        return tuple(states)

    async def reconcile(self, recruiter_id: str, event: EventState) -> int:
        """Apply one event's state to its booking, under the conversation's lock so it cannot
        interleave with a turn of the candidate. 1 when the booking changed."""
        booking = await asyncio.to_thread(find_booking, self.repo, recruiter_id, event)
        if booking is None:
            return 0
        async with self.service.conversation_lock(booking.conversation_id):
            return await asyncio.to_thread(self._apply, recruiter_id, booking.id, event)

    def _apply(self, recruiter_id: str, booking_id: str, event: EventState) -> int:
        booking = find_booking(self.repo, recruiter_id, event)
        if booking is None or booking.id != booking_id:
            return 0
        with self.repo.session() as session:
            slot = session.get(InterviewSlot, booking.slot_id)
            candidate = session.get(Candidate, booking.candidate_id)
        if slot is None:
            return 0
        email = None
        if candidate is not None:
            profile = CandidateProfile.model_validate(candidate.profile)
            email = candidate.email or profile.email
        change = classify(event, slot.starts_at, email, booking.interviewer_email)
        if change == Change.NONE:
            return 0
        if change == Change.MOVED and event.start is None and booking.calendar_issue == "all_day":
            return 0  # flagged already
        now = self.service.clock()
        config = self.service.scheduling
        kind = change.value
        if change == Change.MOVED:
            kind = self._moved(booking, slot, event, now)
        elif change == Change.CANCELLED:
            _mark_cancelled(self.repo, booking.id, now)
            self._tell(booking, slot, CANCELLED_NOTICE, now, change, reoffer_slots=True)
        elif change == Change.RECRUITER_DECLINED:
            moved = scheduling.reassign(self.repo, booking.id, now, config=config)
            if moved is not None:
                kind = "reassigned"
                self._tell(booking, slot, REASSIGNED_NOTICE, now, change)
            else:
                scheduling.cancel(self.repo, booking.id, now, config=config, by="recruiter")
                self._tell(booking, slot, CANCELLED_NOTICE, now, change, reoffer_slots=True)
        elif change == Change.MANAGER_DECLINED:
            scheduling.cancel(self.repo, booking.id, now, config=config, by="manager")
            self._tell(booking, slot, MANAGER_DECLINED_NOTICE, now, change, reoffer_slots=True)
        elif change == Change.CANDIDATE_DECLINED:
            scheduling.cancel(
                self.repo, booking.id, now, config=config, notify=False, by="candidate"
            )
            self._tell(booking, slot, DECLINED_NOTICE, now, change, reoffer_slots=True)
        metrics.CALENDAR_RECONCILIATIONS.labels(kind).inc()
        record("calendar_reconciled", booking_id=booking.id, type=kind)
        return 1

    def _moved(
        self, booking: Booking, slot: InterviewSlot, event: EventState, now: datetime
    ) -> str:
        if event.start is None:  # turned into an all-day event: no time to follow
            _flag(self.repo, booking.id, "all_day", now)
            return "moved_flagged"
        moved = scheduling.move(
            self.repo, booking.id, event.start, now, end=event.end, config=self.service.scheduling
        )
        new_slot = scheduling.get_slot(self.repo, moved.slot_id) or slot
        notice = MOVED_VIDEO_NOTICE if moved.mode == InterviewMode.VIDEO else MOVED_NOTICE
        self._tell(moved, new_slot, notice, now, Change.MOVED, show_booking=True)
        return "moved_flagged" if moved.calendar_issue else "moved"

    def _tell(
        self,
        booking: Booking,
        slot: InterviewSlot,
        notice: Mapping[Register, str],
        now: datetime,
        change: Change,
        *,
        reoffer_slots: bool = False,
        show_booking: bool = False,
    ) -> None:
        """Update the candidate's conversation and leave them a notice in the chat: a turn of
        Ana's they read when they come back (the model sees it in the history too)."""
        conversation = self.repo.get_conversation(booking.conversation_id)
        if conversation is None:
            return
        state = state_of(conversation)
        view = scheduling.booked_view(slot, state.language, booking)
        ui: Booked | None = None
        if reoffer_slots:
            state, _ = reoffer(state)
        elif show_booking:
            state = rebook(state, view)
            ui = view
        register = _register(state)
        when = view.label
        if conversation.channel == Channel.VOICE.value:
            # A call says it next (the opening line): the day and time in words (SAZ-48).
            when = spoken_when(view.starts_at, register)
        text = notice[register].format(when=when, location=view.location)
        metrics.TURNS.labels(TurnRole.AGENT.value).inc()
        self.repo.save(
            Turn(
                conversation_id=conversation.id,
                role=TurnRole.AGENT.value,
                text=text,
                ui=ui.model_dump(mode="json") if ui is not None else None,
                action={"kind": "calendar_update", "change": change.value},
                created_at=to_utc(now),
            )
        )
        conversation.state = state.model_dump(mode="json")
        self.repo.save(conversation)

    # --- channels from the API ---

    def start_in_background(self, recruiter_id: str) -> None:
        """Open a channel for a calendar just connected (replacing any old one); failures
        are counted and `calendar-watch renew` tries again."""
        if self.address is None:
            return
        task = asyncio.create_task(asyncio.to_thread(self._start, recruiter_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _start(self, recruiter_id: str) -> RenewReport:
        assert self.address is not None
        return renew(
            self.repo,
            self.watcher,
            self.address,
            self.service.clock(),
            self.ttl,
            recruiter_id=recruiter_id,
            force=True,
        )

    def stop(self, recruiter_id: str) -> int:
        """Close the recruiter's channels before their grant goes (`channels.stop` needs it)."""
        return stop_watch(self.repo, self.watcher, recruiter_id)


def find_booking(repo: Repo, recruiter_id: str, event: EventState) -> Booking | None:
    """The active booking an event of this recruiter's calendar belongs to: the one holding
    its id, else the one its marker names (our write of the id may not have landed yet)."""
    with repo.session() as session:
        booking = session.exec(
            select(Booking)
            .where(col(Booking.google_event_id) == event.event_id)
            .where(col(Booking.recruiter_id) == recruiter_id)
            .where(col(Booking.status) == BookingStatus.BOOKED)
        ).first()
        if booking is not None or event.booking_id is None:
            return booking
        marked = session.get(Booking, event.booking_id)
    if (
        marked is None
        or marked.status != BookingStatus.BOOKED
        or marked.recruiter_id != recruiter_id
        or marked.google_event_id not in (None, event.event_id)
    ):
        return None
    return marked


def _mark_cancelled(repo: Repo, booking_id: str, now: datetime) -> None:
    """The recruiter deleted the event: cancel the booking; there is no event left to delete."""
    with repo.session() as session:
        booking = session.get(Booking, booking_id)
        if booking is None or booking.status != BookingStatus.BOOKED:
            return
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_by = "recruiter"
        booking.google_event_id = None
        booking.google_calendar_id = None
        booking.meet_url = None
        booking.updated_at = to_utc(now)
        session.add(booking)
        session.commit()


def _flag(repo: Repo, booking_id: str, issue: str, now: datetime) -> None:
    with repo.session() as session:
        booking = session.get(Booking, booking_id)
        if booking is None:
            return
        booking.calendar_issue = issue
        booking.updated_at = to_utc(now)
        session.add(booking)
        session.commit()
