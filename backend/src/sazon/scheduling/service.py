"""Interviews: offer the best open times, book them, move and cancel them.

Video interviews are the recruiters'. Open times come from the recruiters, not from a slot
table: every active recruiter who covers the location by video (and, with a calendar, has
connected it) is free inside their working hours, minus their calendar's busy blocks and their
booked video interviews, with a buffer around each and at most `max_interviews_per_day`
(`availability`). A candidate sees a time when any of them is free. Booking re-checks that
time against the calendars, gives the interview to the least-loaded recruiter still free, and
creates the event on their calendar with a Google Meet link and the candidate invited. A
recruiter's bookings are serialized by locking their row, so two candidates can never get the
same recruiter at overlapping times.

In-person interviews are the location manager's (SAZ-64): open inside the location's
interview hours, up to its interviews per slot and per day (`onsite`), whatever the
recruiters' calendars say, so several locations can hold one at the same time. Booking locks
the location's slot rows of that day and counts again, so concurrent bookings cannot go over
either limit.

Times are stored in UTC and shown in the location's time zone. `InterviewSlot` rows are the
time keys bookings point at, created the first time a time is offered.
"""

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from sazon.calendar import (
    CalendarError,
    CalendarEvent,
    CalendarProvider,
    EventRequest,
    Guest,
    Interval,
)
from sazon.config import Settings
from sazon.engine.models import CandidateProfile, Language, Schedule
from sazon.engine.models import InterviewMode as ViewMode
from sazon.engine.service_areas import Location, Manager, branch_name, get_location
from sazon.engine.slots import Booked, Miss, Slot, SlotQuery
from sazon.engine.ui import WEEKDAYS, format_date
from sazon.observability import record
from sazon.scheduling import onsite
from sazon.scheduling.availability import (
    INTERVIEW_LENGTH,
    Interviewer,
    MoveIssue,
    Rules,
    assignment_order,
    is_free,
    move_issue,
    open_times,
)
from sazon.storage.models import (
    Booking,
    BookingStatus,
    Candidate,
    InterviewMode,
    InterviewSlot,
    Recruiter,
)
from sazon.storage.repo import Repo, to_utc

logger = logging.getLogger(__name__)

AFTERNOON_FROM = time(14)
EVENING_FROM = time(18)
"""The preferred blocks, local time: morning before 14:00, afternoon 14:00-18:00, evening from
18:00 (docs/screening-rules.md, `schedule`)."""
OFFER_COUNT = 3
MINUTES = int(INTERVIEW_LENGTH.total_seconds() // 60)
AT = {Language.ES: "a las", Language.EN: "at"}
SOURCES = ("fixed", "google", "fake")


class SlotUnavailable(Exception):
    """Nobody is free at that time any more, it has started, or it does not exist."""


@dataclass(frozen=True)
class SchedulingConfig:
    """How interviews are scheduled. Without a `calendar` (`SCHEDULING_SOURCE=fixed`) only
    working hours and our own bookings count and no events are created."""

    calendar: CalendarProvider | None = None
    buffer: timedelta = timedelta(minutes=10)
    days: int = 14
    organizer_email: str | None = None
    """`ONSITE_ORGANIZER_EMAIL`: the recruiter whose calendar holds the in-person events."""
    organizer_required: bool = False
    """In person only with that organizer connected (`SCHEDULING_SOURCE=google`); otherwise
    it is offered without one, and its bookings get no event."""

    @property
    def rules(self) -> Rules:
        return Rules(buffer=self.buffer)


FIXED = SchedulingConfig()
WIDER_DAYS = 28
"""How far ahead interviews are looked for when the configured window has none: 4 weeks."""


def scheduling_from(settings: Settings, repo: Repo) -> SchedulingConfig:
    """The configured source: `fixed`, `google` (needs the OAuth client) or `fake`."""
    source = settings.scheduling_source.strip().lower() or "fixed"
    if source not in SOURCES:
        raise ValueError(f"SCHEDULING_SOURCE must be one of {', '.join(SOURCES)}")
    calendar: CalendarProvider | None = None
    if source == "fake":
        from sazon.calendar.fake import FakeCalendar

        calendar = FakeCalendar()
    elif source == "google":
        from sazon.calendar.google import GoogleCalendar
        from sazon.calendar.tokens import cipher_from

        if not settings.google_configured:
            raise ValueError("SCHEDULING_SOURCE=google needs GOOGLE_CLIENT_ID and SECRET")
        calendar = GoogleCalendar(
            repo,
            client_id=settings.google_client_id.strip(),
            client_secret=settings.google_client_secret.get_secret_value().strip(),
            cipher=cipher_from(settings),
        )
    return SchedulingConfig(
        calendar=calendar,
        buffer=timedelta(minutes=max(0, settings.interview_buffer_minutes)),
        days=max(1, settings.scheduling_days),
        organizer_email=settings.onsite_organizer_email.strip().lower() or None,
        organizer_required=source == "google",
    )


# --- who is available ---


type Scope = Mapping[str, Collection[str]]
"""A candidate's answers to the agent's `interview.scope` fields, by field id: what a recruiter
must share to interview them (`Recruiter.scope`)."""
ANYONE: Scope = {}
"""No answers to match: every recruiter at the location."""


def _eligible(
    session: Session, location_id: str, mode: InterviewMode, scope: Scope = ANYONE
) -> list[Recruiter]:
    return [
        recruiter
        for recruiter in session.exec(select(Recruiter).where(col(Recruiter.active)))
        if location_id in recruiter.location_ids
        and mode in recruiter.interview_modes
        and _covers(recruiter, scope)
    ]


def _covers(recruiter: Recruiter, scope: Scope) -> bool:
    """The recruiter interviews a candidate with these answers: for each field they are scoped
    on, one of the candidate's answers is theirs (the head of a subject the teacher teaches)."""
    return all(
        set(recruiter.scope[field]) & set(answers)
        for field, answers in scope.items()
        if recruiter.scope.get(field)
    )


def available_modes(
    repo: Repo, location_id: str, *, config: SchedulingConfig = FIXED, scope: Scope = ANYONE
) -> list[InterviewMode]:
    """The interview modes on offer at the location, in `InterviewMode` order: in person when
    it has a manager and interview hours, video when some active recruiter covering it does
    video (with a calendar, one who has connected it). The candidate chooses between them only
    when there are two."""
    with repo.session() as session:
        video = _eligible(session, location_id, InterviewMode.VIDEO, scope)
    if config.calendar is not None:
        connected = config.calendar.connected({recruiter.id for recruiter in video})
        video = [recruiter for recruiter in video if recruiter.id in connected]
    offered = {
        InterviewMode.ONSITE: _onsite_ready(repo, location_id, config),
        InterviewMode.VIDEO: bool(video),
    }
    return [mode for mode in InterviewMode if offered[mode]]


def organizer(repo: Repo, config: SchedulingConfig) -> Recruiter | None:
    """The active recruiter whose calendar holds the in-person events (`organizer_email`),
    when there is one and, with a calendar, it is connected."""
    if not config.organizer_email:
        return None
    with repo.session() as session:
        found = session.exec(
            select(Recruiter).where(col(Recruiter.email) == config.organizer_email)
        ).first()
    if found is None or not found.active:
        return None
    if config.calendar is not None and found.id not in config.calendar.connected([found.id]):
        return None
    return found


def _onsite_ready(repo: Repo, location_id: str, config: SchedulingConfig) -> bool:
    """In person at the location: it has a manager and hours, and the organizer is there when
    one is required."""
    location = get_location(location_id)
    if location is None or onsite.venue_of(location) is None:
        return False
    return not config.organizer_required or organizer(repo, config) is not None


def _booked_starts(
    session: Session,
    recruiter_ids: Collection[str],
    start: datetime,
    end: datetime,
    *,
    skip_candidate: str | None = None,
) -> dict[str, list[datetime]]:
    """Start times of each recruiter's active video interviews in `[start, end)`. In-person
    ones on their calendar (the organizer's) are not theirs to run."""
    if not recruiter_ids:
        return {}
    query = (
        select(Booking.recruiter_id, InterviewSlot.starts_at)
        .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id))
        .where(col(Booking.status) == BookingStatus.BOOKED)
        .where(col(Booking.mode) == InterviewMode.VIDEO)
        .where(col(Booking.recruiter_id).in_(list(recruiter_ids)))
        .where(col(InterviewSlot.starts_at) >= start)
        .where(col(InterviewSlot.starts_at) < end)
    )
    if skip_candidate is not None:
        query = query.where(col(Booking.candidate_id) != skip_candidate)
    booked: dict[str, list[datetime]] = {}
    for recruiter_id, starts_at in session.exec(query).all():
        if recruiter_id is not None:
            booked.setdefault(recruiter_id, []).append(to_utc(starts_at))
    return booked


def interviewers(
    repo: Repo,
    location_id: str,
    mode: InterviewMode,
    start: datetime,
    end: datetime,
    config: SchedulingConfig,
    *,
    skip_candidate: str | None = None,
    scope: Scope = ANYONE,
) -> list[Interviewer]:
    """The recruiters who could interview at the location in `[start, end)`, with their
    bookings and (with a calendar) busy blocks. With a calendar, only those connected and
    readable now count."""
    pad = timedelta(days=1)  # whole local days, for the daily maximum
    with repo.session() as session:
        eligible = _eligible(session, location_id, mode, scope)
        booked = _booked_starts(
            session, [r.id for r in eligible], start - pad, end + pad, skip_candidate=skip_candidate
        )
    busy: dict[str, list] = {}
    if config.calendar is not None:
        connected = config.calendar.connected([r.id for r in eligible])
        eligible = [r for r in eligible if r.id in connected]
        found = config.calendar.busy(
            [r.id for r in eligible], start - config.buffer, end + config.buffer
        )
        eligible = [r for r in eligible if r.id in found]
        busy = {rid: list(blocks) for rid, blocks in found.items()}
    return [
        Interviewer(
            id=r.id,
            name=r.name,
            timezone=r.timezone,
            working_hours=r.working_hours,
            max_per_day=r.max_interviews_per_day,
            booked=tuple(booked.get(r.id, ())),
            busy=tuple(busy.get(r.id, ())),
        )
        for r in eligible
    ]


def _onsite_starts(
    session: Session,
    location_id: str,
    start: datetime,
    end: datetime,
    *,
    skip_candidate: str | None = None,
) -> list[datetime]:
    """Start times of the location's active in-person interviews in `[start, end)` (a booking
    without a mode is one from before video, in person)."""
    query = (
        select(InterviewSlot.starts_at)
        .join(Booking, col(InterviewSlot.id) == col(Booking.slot_id))
        .where(col(Booking.status) == BookingStatus.BOOKED)
        .where(or_(col(Booking.mode) == InterviewMode.ONSITE, col(Booking.mode).is_(None)))
        .where(col(InterviewSlot.location_id) == location_id)
        .where(col(InterviewSlot.starts_at) >= start)
        .where(col(InterviewSlot.starts_at) < end)
    )
    if skip_candidate is not None:
        query = query.where(col(Booking.candidate_id) != skip_candidate)
    return [to_utc(moment) for moment in session.exec(query).all()]


def venue(
    repo: Repo,
    location_id: str,
    start: datetime,
    end: datetime,
    *,
    skip_candidate: str | None = None,
) -> onsite.Venue | None:
    """The location as in-person availability sees it around `[start, end)`, with its booked
    in-person interviews; None when it holds none (no manager or no hours)."""
    location = get_location(location_id)
    if location is None:
        return None
    pad = timedelta(days=1)  # whole local days, for the daily maximum
    with repo.session() as session:
        booked = _onsite_starts(
            session, location_id, start - pad, end + pad, skip_candidate=skip_candidate
        )
    return onsite.venue_of(location, booked)


# --- offering ---


def offer(
    repo: Repo,
    location_id: str,
    schedule: Collection[Schedule] | None,
    start_date: date | None,
    now: datetime,
    *,
    n: int = OFFER_COUNT,
    exclude: Collection[str] = (),
    mode: InterviewMode = InterviewMode.VIDEO,
    config: SchedulingConfig = FIXED,
) -> list[InterviewSlot]:
    """Up to `n` open times at the location, soonest first, from tomorrow (local time) on.

    Interviews come before the job starts, so times on or before `start_date` go first, then
    those in any of the candidate's preferred blocks (morning before 14:00, afternoon and
    evening after), one per day while there are enough days. `exclude` are slot ids already
    turned down. With nothing in the configured window (`SCHEDULING_DAYS`), the next
    `WIDER_DAYS` are searched before the candidate is told there are none (SAZ-42). Video
    times come from the recruiters, in-person ones from the location (`onsite`).
    """
    return lookup(
        repo,
        location_id,
        schedule,
        start_date,
        now,
        n=n,
        exclude=exclude,
        mode=mode,
        config=config,
    ).slots


@dataclass(frozen=True)
class Found:
    """Times found for an offer. `miss` set: none fits the times asked about (why: `Miss`),
    and these are the nearest to them."""

    slots: list[InterviewSlot]
    miss: Miss | None = None

    @property
    def matched(self) -> bool:
        return self.miss is None


def lookup(
    repo: Repo,
    location_id: str,
    schedule: Collection[Schedule] | None,
    start_date: date | None,
    now: datetime,
    *,
    n: int = OFFER_COUNT,
    exclude: Collection[str] = (),
    mode: InterviewMode = InterviewMode.VIDEO,
    config: SchedulingConfig = FIXED,
    query: SlotQuery | None = None,
    avoid_days: Collection[date] = (),
    scope: Scope = ANYONE,
) -> Found:
    """`offer`, also for the times a candidate asked about. `avoid_days` (local) are skipped:
    days the candidate turned down (D9). With a `query` ("¿tienes algo por la tarde?", "¿y el
    viernes?") only free times that fit it are offered, from the next `WIDER_DAYS`; when none
    does, the nearest free ones, with the `miss`. See `_asked` for the order."""
    location = get_location(location_id)
    if location is None:
        return Found([])
    zone = ZoneInfo(location.timezone)
    today = to_utc(now).astimezone(zone).date()
    tomorrow = datetime.combine(today + timedelta(days=1), time(0), tzinfo=zone)
    start = to_utc(tomorrow)
    turned_down = _starts_of(repo, exclude)
    avoided = set(avoid_days)

    def free_times(days: int) -> list[datetime]:
        end = to_utc(tomorrow + timedelta(days=days))
        if mode == InterviewMode.ONSITE:
            ready = _onsite_ready(repo, location_id, config)
            place = venue(repo, location_id, start, end) if ready else None
            found = onsite.open_starts(place, start, end, config.rules) if place else []
        else:
            people = interviewers(repo, location_id, mode, start, end, config, scope=scope)
            found = list(open_times(people, start, end, config.rules))
        return [
            t for t in found if t not in turned_down and t.astimezone(zone).date() not in avoided
        ]

    if query is not None:
        times = free_times(max(config.days, WIDER_DAYS))
        found = _asked(times, zone, query, today, schedule, start_date, n, turned_down)
        return Found(materialize(repo, location_id, found.slots), found.miss)
    times = free_times(config.days)
    if not times and config.days < WIDER_DAYS:
        times = free_times(WIDER_DAYS)
    picked = pick(times, zone, schedule, start_date, n)
    return Found(materialize(repo, location_id, picked))


@dataclass(frozen=True)
class _Asked:
    slots: list[datetime]
    miss: Miss | None = None


def _asked(
    times: Sequence[datetime],
    zone: ZoneInfo,
    query: SlotQuery,
    today: date,
    schedule: Collection[Schedule] | None,
    start_date: date | None,
    n: int,
    excluded: Collection[datetime],
) -> _Asked:
    """The free `times` for a query, best first.

    A clock time named (`at`): the free times on the days asked about closest to it, that time
    itself first when it is free (an explicit time overrides the preferred block); none free
    that day: the same time on the nearest days. A weekday alone: the nearest such day's times
    first. "¿Y otras opciones?" (no condition): days with nothing offered yet first. Nothing
    fitting: the nearest, and the `miss` says why, `more` when only times already offered
    (`excluded`) fit."""
    fitting = [t for t in times if query.fits(t.astimezone(zone))]
    offered = any(
        query.fits(t.astimezone(zone)) for t in excluded if t.astimezone(zone).date() > today
    )
    if query.at:
        if not fitting:
            other_days = SlotQuery(at=query.at)
            near = _nearest(
                times, zone, _targets(query, today), schedule, start_date, n, other_days
            )
            return _Asked(near, Miss.MORE if query.other and offered else Miss.TIME)
        closest = sorted(fitting, key=lambda t: (query.minutes_off(t.astimezone(zone)), t))[:n]
        # More times around one already offered: that one is not "not free".
        exact = query.other or query.minutes_off(closest[0].astimezone(zone)) == 0
        return _Asked(closest, None if exact else Miss.TIME)
    if fitting and query.nearest_day_first:

        def nearest_day(moment: datetime) -> tuple[date, bool, datetime]:
            local = moment.astimezone(zone)
            return local.date(), not _in_block(local.time(), schedule), moment

        return _Asked(sorted(fitting, key=nearest_day)[:n])
    if fitting and query.unconstrained:
        seen = {t.astimezone(zone).date() for t in excluded}
        new = [t for t in fitting if t.astimezone(zone).date() not in seen]
        picked = pick(new, zone, schedule, start_date, n)
        if len(picked) < n:
            rest = [t for t in fitting if t not in picked]
            picked = sorted([*picked, *pick(rest, zone, schedule, start_date, n - len(picked))])
        return _Asked(picked)
    if fitting:
        return _Asked(pick(fitting, zone, schedule, start_date, n))
    near = _nearest(times, zone, _targets(query, today), schedule, start_date, n)
    return _Asked(near, Miss.MORE if offered else Miss.NONE)


def _targets(query: SlotQuery, today: date) -> list[date]:
    """The days a query asks about, for the nearest alternatives: its dates or week, else the
    next date of each weekday it names."""
    if query.targets():
        return list(query.targets())
    return [
        today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)
        for weekday in query.weekdays
    ]


def _nearest(
    times: Sequence[datetime],
    zone: ZoneInfo,
    targets: Sequence[date],
    schedule: Collection[Schedule] | None,
    start_date: date | None,
    n: int,
    clock: SlotQuery | None = None,
) -> list[datetime]:
    """The `n` free times closest to the days asked about, one per day while there are days,
    each the closest to the `clock` time asked about, if any; with no day asked about (a time
    of day), the best by the usual preferences."""
    if not targets and clock is None:
        return pick(times, zone, schedule, start_date, n)
    off = clock.minutes_off if clock is not None else lambda _: 0

    def distance(moment: datetime) -> tuple[int, int, datetime]:
        local = moment.astimezone(zone)
        days = min((abs((local.date() - target).days) for target in targets), default=0)
        return days, off(local), moment

    picked: list[datetime] = []
    days: set[date] = set()
    for moment in sorted(times, key=distance):
        day = moment.astimezone(zone).date()
        if len(picked) < n and day not in days:
            picked.append(moment)
            days.add(day)
    return sorted(picked)


def _starts_of(repo: Repo, slot_ids: Collection[str]) -> set[datetime]:
    if not slot_ids:
        return set()
    with repo.session() as session:
        query = select(InterviewSlot.starts_at).where(col(InterviewSlot.id).in_(list(slot_ids)))
        return {to_utc(moment) for moment in session.exec(query)}


def pick(
    times: Sequence[datetime],
    zone: ZoneInfo,
    schedule: Collection[Schedule] | None,
    start_date: date | None,
    n: int,
) -> list[datetime]:
    """The `n` best times by the candidate's preferences, one per day while there are days."""

    def rank(moment: datetime) -> tuple[bool, bool, datetime]:
        local = moment.astimezone(zone)
        late = start_date is not None and local.date() > start_date
        return late, not _in_block(local.time(), schedule), moment

    ranked = sorted(times, key=rank)
    picked: list[datetime] = []
    days: set[date] = set()
    for moment in ranked:
        day = moment.astimezone(zone).date()
        if len(picked) < n and day not in days:
            picked.append(moment)
            days.add(day)
    chosen = set(picked)
    picked.extend(moment for moment in ranked if moment not in chosen)
    return sorted(picked[:n])


def materialize(repo: Repo, location_id: str, starts: Sequence[datetime]) -> list[InterviewSlot]:
    """The `InterviewSlot` rows for these times at the location, created when missing."""
    if not starts:
        return []
    with repo.session() as session:
        for start in starts:
            if _slot_at(session, location_id, start) is None:
                session.add(
                    InterviewSlot(
                        location_id=location_id, starts_at=start, ends_at=start + INTERVIEW_LENGTH
                    )
                )
                try:
                    session.commit()
                except IntegrityError:  # offered to someone else at the same moment
                    session.rollback()
        slots = [_slot_at(session, location_id, start) for start in starts]
    return [slot for slot in slots if slot is not None]


def _slot_at(session: Session, location_id: str, start: datetime) -> InterviewSlot | None:
    return session.exec(
        select(InterviewSlot)
        .where(col(InterviewSlot.location_id) == location_id)
        .where(col(InterviewSlot.starts_at) == start)
    ).first()


def _in_block(moment: time, schedule: Collection[Schedule] | None) -> bool:
    """In any of the preferred blocks; no preference or `flexible` is every time."""
    if not schedule or Schedule.FLEXIBLE in schedule:
        return True
    return any(_in_one_block(moment, block) for block in schedule)


def _in_one_block(moment: time, block: Schedule) -> bool:
    if block == Schedule.MORNING:
        return moment < AFTERNOON_FROM
    if block == Schedule.AFTERNOON:
        return AFTERNOON_FROM <= moment < EVENING_FROM
    return moment >= EVENING_FROM


# --- booking ---


def book(
    repo: Repo,
    candidate_id: str,
    conversation_id: str,
    slot_id: str,
    now: datetime,
    *,
    mode: InterviewMode = InterviewMode.VIDEO,
    config: SchedulingConfig = FIXED,
    scope: Scope = ANYONE,
) -> Booking:
    """Book `slot_id` for the candidate, or move their current booking there.

    Video: the recruiters free at that time are re-checked (calendars included) and the least
    loaded gets it; a moved booking keeps its recruiter while they are free. In person: the
    location is re-checked and its manager gets it. Idempotent: booking the slot the candidate
    already holds, in the same mode, returns that booking. Raises `SlotUnavailable` when nobody
    is free any more or the time has started; the current booking then stays.
    """
    current = active_booking(repo, candidate_id)
    if current is not None and current.slot_id == slot_id and mode_of(current) == mode:
        return _ensure_event(repo, current, config)
    slot = get_slot(repo, slot_id)
    if slot is None or to_utc(slot.starts_at) <= to_utc(now):
        raise SlotUnavailable(slot_id)
    if mode == InterviewMode.ONSITE:
        host = organizer(repo, config)
        if host is None and config.organizer_required:
            raise SlotUnavailable(slot_id)
        booking = _claim_onsite(
            repo, candidate_id, conversation_id, slot, now, config, host.id if host else None
        )
        if booking is None:
            raise SlotUnavailable(slot_id)
        return _sync_event(repo, booking, current, config)
    start = to_utc(slot.starts_at)
    people = interviewers(
        repo,
        slot.location_id,
        mode,
        start,
        start + INTERVIEW_LENGTH,
        config,
        skip_candidate=candidate_id,
        scope=scope,
    )
    free = assignment_order([p for p in people if is_free(p, start, config.rules)], start)
    keep = current.recruiter_id if current is not None else None
    free.sort(key=lambda person: person.id != keep)  # stable: the rest keep their order
    for person in free:
        booking = _claim(repo, person, candidate_id, conversation_id, slot, mode, now, config)
        if booking is None:
            continue
        return _sync_event(repo, booking, current, config)
    raise SlotUnavailable(slot_id)


def _claim(
    repo: Repo,
    person: Interviewer,
    candidate_id: str,
    conversation_id: str,
    slot: InterviewSlot,
    mode: InterviewMode,
    now: datetime,
    config: SchedulingConfig,
) -> Booking | None:
    """Give the interview to `person` if they are still free in the database; None when a
    concurrent booking took them. Their row is locked first (a write: a row lock on Postgres,
    the write lock on SQLite), so the check and the insert cannot interleave with another."""
    start = to_utc(slot.starts_at)
    moment = to_utc(now)
    with repo.session() as session:
        session.execute(
            update(Recruiter)
            .where(col(Recruiter.id) == person.id)
            .values(updated_at=col(Recruiter.updated_at))
        )
        booked = _booked_starts(
            session,
            [person.id],
            start - timedelta(days=1),
            start + timedelta(days=1),
            skip_candidate=candidate_id,
        ).get(person.id, [])
        # The calendar was read just before, outside the lock; only the bookings are rechecked.
        fresh = replace(person, booked=tuple(booked), busy=())
        if not is_free(fresh, start, config.rules):
            session.rollback()
            return None
        booking = Booking(
            candidate_id=candidate_id,
            conversation_id=conversation_id,
            slot_id=slot.id,
            recruiter_id=person.id,
            mode=mode,
            created_at=moment,
            updated_at=moment,
        )
        return _insert(session, booking, moment)


def _claim_onsite(
    repo: Repo,
    candidate_id: str,
    conversation_id: str,
    slot: InterviewSlot,
    now: datetime,
    config: SchedulingConfig,
    organizer_id: str | None,
) -> Booking | None:
    """Give the in-person interview to the location's manager if the location still has room
    then; None when it is full. Its event goes on the organizer's calendar. The location's
    slot rows of that local day are locked first (a write: row locks on Postgres, the write
    lock on SQLite), so two bookings there cannot both count the room left and go over the
    per-slot or the daily limit."""
    location = get_location(slot.location_id)
    if location is None or location.manager is None:
        return None
    start = to_utc(slot.starts_at)
    moment = to_utc(now)
    zone = ZoneInfo(location.timezone)
    day = datetime.combine(start.astimezone(zone).date(), time(0), tzinfo=zone)
    day_start, day_end = to_utc(day), to_utc(day + timedelta(days=1))
    with repo.session() as session:
        session.execute(
            update(InterviewSlot)
            .where(col(InterviewSlot.location_id) == slot.location_id)
            .where(col(InterviewSlot.starts_at) >= day_start)
            .where(col(InterviewSlot.starts_at) < day_end)
            .values(booked_count=col(InterviewSlot.booked_count))
        )
        booked = _onsite_starts(
            session, slot.location_id, day_start, day_end, skip_candidate=candidate_id
        )
        place = onsite.venue_of(location, booked)
        if place is None or not onsite.is_open(place, start, config.rules):
            session.rollback()
            return None
        booking = Booking(
            candidate_id=candidate_id,
            conversation_id=conversation_id,
            slot_id=slot.id,
            recruiter_id=organizer_id,
            mode=InterviewMode.ONSITE,
            interviewer_email=location.manager.email,
            created_at=moment,
            updated_at=moment,
        )
        return _insert(session, booking, moment)


def _insert(session: Session, booking: Booking, moment: datetime) -> Booking:
    """Add the new booking, the candidate's current one marked `rescheduled`, in the session
    that holds the lock."""
    current = _active(session, booking.candidate_id)
    if current is not None:
        current.status = BookingStatus.RESCHEDULED
        current.updated_at = moment
        session.add(current)
    session.add(booking)
    try:
        session.commit()
    except IntegrityError:
        # The same candidate booked concurrently: keep the booking that won.
        session.rollback()
        winner = _active(session, booking.candidate_id)
        if winner is not None and winner.slot_id == booking.slot_id:
            return winner
        raise SlotUnavailable(booking.slot_id) from None
    session.refresh(booking)
    return booking


def reschedule(
    repo: Repo, booking_id: str, slot_id: str, now: datetime, *, config: SchedulingConfig = FIXED
) -> Booking:
    """Move an active booking to another slot. The old one is marked `rescheduled`."""
    current = _get(repo, booking_id)
    if current.status != BookingStatus.BOOKED:
        raise SlotUnavailable(slot_id)
    return book(
        repo,
        current.candidate_id,
        current.conversation_id,
        slot_id,
        now,
        mode=mode_of(current),
        config=config,
    )


def cancel(
    repo: Repo,
    booking_id: str,
    now: datetime,
    *,
    config: SchedulingConfig = FIXED,
    notify: bool = True,
    by: str | None = None,
) -> Booking:
    """Cancel a booking and delete its calendar event, telling the candidate when `notify`.
    `by` records who cancelled it from the calendar (`candidate`, `recruiter`). Cancelling
    twice changes nothing. When the calendar refuses the delete, the booking keeps the event's
    id so `delete_stale_events` can retry."""
    with repo.session() as session:
        booking = session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(booking_id)
        if booking.status != BookingStatus.BOOKED:
            return booking
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_by = by
        booking.updated_at = to_utc(now)
        session.add(booking)
        session.commit()
        session.refresh(booking)
    if (
        config.calendar is not None
        and _event_of(booking) is not None
        and _delete_event(config.calendar, booking, notify=notify)
    ):
        booking = _set_event(repo, booking.id, None)
    return booking


def reassign(
    repo: Repo, booking_id: str, now: datetime, *, config: SchedulingConfig = FIXED
) -> Booking | None:
    """Give a video interview its recruiter turned down to another one free at the same time:
    a new booking and event on their calendar, the old event deleted. None when nobody else is
    free (or it is no longer an upcoming, active video booking: an in-person one has the
    location's manager and nobody else); the booking then stays as it was."""
    current = _get(repo, booking_id)
    slot = get_slot(repo, current.slot_id)
    if current.status != BookingStatus.BOOKED or slot is None:
        return None
    start = to_utc(slot.starts_at)
    mode = mode_of(current)
    if start <= to_utc(now) or mode != InterviewMode.VIDEO:
        return None
    people = interviewers(
        repo,
        slot.location_id,
        mode,
        start,
        start + INTERVIEW_LENGTH,
        config,
        skip_candidate=current.candidate_id,
    )
    others = [p for p in people if p.id != current.recruiter_id and is_free(p, start, config.rules)]
    for person in assignment_order(others, start):
        booking = _claim(
            repo, person, current.candidate_id, current.conversation_id, slot, mode, now, config
        )
        if booking is not None:
            return _sync_event(repo, booking, current, config)
    return None


def move(
    repo: Repo,
    booking_id: str,
    start: datetime,
    now: datetime,
    *,
    end: datetime | None = None,
    config: SchedulingConfig = FIXED,
) -> Booking:
    """Follow a recruiter who moved the interview in their own calendar to `start` (`end` is
    the event's new end): the booking takes that time, and `calendar_issue` says which rule
    the time breaks (outside their working hours, over another interview...; in person, the
    location's hours and limits), None when it breaks none. The event is left as the
    recruiter put it."""
    booking = _get(repo, booking_id)
    slot = get_slot(repo, booking.slot_id)
    if slot is None:
        raise LookupError(booking_id)
    start = to_utc(start)
    if mode_of(booking) == InterviewMode.ONSITE:
        place = venue(repo, slot.location_id, start, start, skip_candidate=booking.candidate_id)
        issue = (
            onsite.move_issue(place, start, config.rules, to_utc(now))
            if place is not None
            else MoveIssue.OUTSIDE_HOURS
        )
        return _moved_to(repo, booking_id, slot.location_id, start, issue, now)
    if booking.recruiter_id is None:
        raise LookupError(booking_id)
    own = Interval(
        start, to_utc(end) if end is not None and end > start else start + INTERVIEW_LENGTH
    )
    person = _interviewer(repo, booking.recruiter_id, own, config, booking.candidate_id)
    issue = (
        move_issue(person, start, config.rules, to_utc(now), own)
        if person is not None
        else MoveIssue.OUTSIDE_HOURS
    )
    return _moved_to(repo, booking_id, slot.location_id, start, issue, now)


def _moved_to(
    repo: Repo,
    booking_id: str,
    location_id: str,
    start: datetime,
    issue: MoveIssue | None,
    now: datetime,
) -> Booking:
    moved = materialize(repo, location_id, [start])
    with repo.session() as session:
        row = session.get(Booking, booking_id)
        if row is None or not moved:
            raise LookupError(booking_id)
        row.slot_id = moved[0].id
        row.calendar_issue = issue.value if issue is not None else None
        row.updated_at = to_utc(now)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _interviewer(
    repo: Repo, recruiter_id: str, around: Interval, config: SchedulingConfig, candidate_id: str
) -> Interviewer | None:
    """One recruiter as availability sees them around `around`: their other interviews and,
    when their calendar can be read, its busy blocks."""
    with repo.session() as session:
        recruiter = session.get(Recruiter, recruiter_id)
        if recruiter is None:
            return None
        pad = timedelta(days=1)
        booked = _booked_starts(
            session,
            [recruiter_id],
            around.start - pad,
            around.end + pad,
            skip_candidate=candidate_id,
        )
    busy: list[Interval] = []
    if config.calendar is not None:
        found = config.calendar.busy(
            [recruiter_id], around.start - config.buffer, around.end + config.buffer
        )
        busy = list(found.get(recruiter_id, ()))
    return Interviewer(
        id=recruiter.id,
        name=recruiter.name,
        timezone=recruiter.timezone,
        working_hours=recruiter.working_hours,
        max_per_day=recruiter.max_interviews_per_day,
        booked=tuple(booked.get(recruiter_id, ())),
        busy=tuple(busy),
    )


def assign_unassigned(repo: Repo) -> int:
    """Give every active video booking without a recruiter one who is free then by working
    hours and bookings, soonest first. Returns how many it assigned. In-person bookings are the
    managers' and need no recruiter."""
    assigned = 0
    with repo.session() as session:
        rows = session.exec(
            select(Booking, InterviewSlot)
            .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id))
            .where(col(Booking.status) == BookingStatus.BOOKED)
            .where(col(Booking.mode) == InterviewMode.VIDEO)
            .where(col(Booking.recruiter_id).is_(None))
            .order_by(col(InterviewSlot.starts_at))
        ).all()
    for booking, slot in rows:
        mode = InterviewMode.VIDEO
        start = to_utc(slot.starts_at)
        people = interviewers(repo, slot.location_id, mode, start, start + INTERVIEW_LENGTH, FIXED)
        free = assignment_order([p for p in people if is_free(p, start, FIXED.rules)], start)
        if not free:
            continue
        with repo.session() as session:
            row = session.get(Booking, booking.id)
            if row is None:
                continue
            row.recruiter_id = free[0].id
            row.mode = mode
            session.add(row)
            session.commit()
        assigned += 1
    return assigned


# --- calendar events ---


def event_request(repo: Repo, booking: Booking) -> EventRequest | None:
    """The calendar event for a booking: the candidate's name, email and the time, in the
    candidate's language; a Meet link for video, or the address and the location's manager
    (invited) in person."""
    with repo.session() as session:
        slot = session.get(InterviewSlot, booking.slot_id)
        candidate = session.get(Candidate, booking.candidate_id)
    if slot is None or candidate is None:
        return None
    location = get_location(slot.location_id)
    profile = CandidateProfile.model_validate(candidate.profile)
    name = profile.full_name or ""
    language = Language.EN if candidate.language == Language.EN else Language.ES
    video = booking.mode == InterviewMode.VIDEO
    summary, description = _event_text(language, name, location, video)
    start = to_utc(slot.starts_at)
    manager = None if video else interviewer_of(booking, slot.location_id)
    return EventRequest(
        booking_id=booking.id,
        summary=summary,
        description=description,
        start=start,
        end=start + INTERVIEW_LENGTH,
        timezone=location.timezone if location else "UTC",
        video=video,
        location=None if video or location is None else _address(location),
        attendee_email=candidate.email or profile.email,
        attendee_name=name or None,
        guests=(Guest(manager.email, manager.name),) if manager else (),
    )


def who_runs(booking: Booking, slot: InterviewSlot) -> tuple[str | None, Manager | None]:
    """The recruiter running a video booking, or the manager running an in-person one (whose
    `recruiter_id` is only the organizer holding the event)."""
    if mode_of(booking) == InterviewMode.VIDEO:
        return booking.recruiter_id, None
    return None, interviewer_of(booking, slot.location_id)


def interviewer_of(booking: Booking, location_id: str) -> Manager | None:
    """The manager who runs an in-person booking: the location's when it is still the one
    booked (or the booking, from before SAZ-64, names none), else whoever was (by email only).
    None for a video booking."""
    if mode_of(booking) != InterviewMode.ONSITE:
        return None
    location = get_location(location_id)
    manager = location.manager if location is not None else None
    if manager is not None and booking.interviewer_email in (None, manager.email):
        return manager
    if not booking.interviewer_email:
        return None
    return Manager(name="", email=booking.interviewer_email)


def _event_text(
    language: Language, name: str, location: Location | None, video: bool
) -> tuple[str, str]:
    place = location.name if location else ""
    if language == Language.EN:
        summary = f"Grupo Sazón interview: {name}" if name else "Grupo Sazón interview"
        how = (
            "Join with the Google Meet link in this invitation."
            if video
            else f"We'll see you at {_address(location)}, with the manager of the location."
            if location
            else ""
        )
        text = (
            f"{MINUTES}-minute interview for the delivery driver role at {place}. {how} "
            "To change the time, reply to Ana in your application chat."
        )
        return summary, text.replace("  ", " ")
    summary = f"Entrevista Grupo Sazón: {name}" if name else "Entrevista Grupo Sazón"
    host = "la gerencia" if location and location.country == "MX" else "la persona encargada"
    how = (
        "Únete con el enlace de Google Meet de esta invitación."
        if video
        else f"Te esperamos en {_address(location)}, con {host} del local."
        if location
        else ""
    )
    text = (
        f"Entrevista de {MINUTES} minutos para el puesto de repartidor/a en {place}. {how} "
        "Para cambiar la hora, responde a Ana en el chat de tu solicitud."
    )
    return summary, text.replace("  ", " ")


def _address(location: Location) -> str:
    return location.address or f"{location.name}, {location.city}"


def _event_of(booking: Booking) -> CalendarEvent | None:
    if not booking.google_event_id or not booking.recruiter_id:
        return None
    return CalendarEvent(
        event_id=booking.google_event_id,
        calendar_id=booking.google_calendar_id or "primary",
        meet_url=booking.meet_url,
    )


def _sync_event(
    repo: Repo, booking: Booking, previous: Booking | None, config: SchedulingConfig
) -> Booking:
    """Put the new booking on its recruiter's calendar: move the previous booking's event when
    the recruiter is the same, otherwise delete it and create one. A calendar failure leaves
    the booking without an event (logged; `ensure_events` retries) rather than undoing it, and
    an old event the calendar would not delete stays on the previous booking
    (`delete_stale_events` retries)."""
    calendar = config.calendar
    if calendar is None:
        return booking
    old = _event_of(previous) if previous is not None else None
    if booking.recruiter_id is None:  # in person without an organizer: no event to write
        if previous is not None and old is not None and _delete_event(calendar, previous):
            _set_event(repo, previous.id, None)
        return booking
    request = event_request(repo, booking)
    if request is None:
        return booking
    old_gone = False
    try:
        if (
            old is not None
            and previous is not None
            and previous.recruiter_id == booking.recruiter_id
        ):
            event = calendar.update_event(booking.recruiter_id, old, request)
            old_gone = True  # moved onto the new booking
        else:
            if old is not None and previous is not None:
                old_gone = _delete_event(calendar, previous)
            event = calendar.create_event(booking.recruiter_id, request)
    except CalendarError as exc:
        logger.warning("Calendar event for booking %s not written: %s", booking.id, exc)
        record("calendar_event_failed", booking_id=booking.id)
        event = None
    if previous is not None and old_gone:
        _set_event(repo, previous.id, None)
    return booking if event is None else _set_event(repo, booking.id, event)


def refresh_event(repo: Repo, candidate_id: str, config: SchedulingConfig) -> bool:
    """Rewrite the calendar event of the candidate's booked interview after they corrected their
    name, phone or email: the invitation goes to the email they have now. Returns whether an
    event was rewritten; a calendar failure is logged and leaves the old one."""
    booking = active_booking(repo, candidate_id)
    calendar = config.calendar
    if booking is None or calendar is None or booking.recruiter_id is None:
        return False
    existing = _event_of(booking)
    request = event_request(repo, booking)
    if existing is None or request is None:
        return False
    try:
        event = calendar.update_event(booking.recruiter_id, existing, request)
    except CalendarError as exc:
        logger.warning("Calendar event for booking %s not updated: %s", booking.id, exc)
        record("calendar_event_failed", booking_id=booking.id)
        return False
    _set_event(repo, booking.id, event)
    return True


def _ensure_event(repo: Repo, booking: Booking, config: SchedulingConfig) -> Booking:
    if config.calendar is None or booking.google_event_id or booking.recruiter_id is None:
        return booking
    return _sync_event(repo, booking, None, config)


def ensure_events(repo: Repo, now: datetime, config: SchedulingConfig) -> int:
    """Create the missing events of upcoming bookings (after a calendar failure). Returns how
    many bookings now have one."""
    if config.calendar is None:
        return 0
    with repo.session() as session:
        rows = session.exec(
            select(Booking)
            .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id))
            .where(col(Booking.status) == BookingStatus.BOOKED)
            .where(col(Booking.google_event_id).is_(None))
            .where(col(Booking.recruiter_id).is_not(None))
            .where(col(InterviewSlot.starts_at) > to_utc(now))
        ).all()
    return sum(bool(_ensure_event(repo, booking, config).google_event_id) for booking in rows)


def delete_stale_events(repo: Repo, config: SchedulingConfig) -> int:
    """Delete the events still held by cancelled or rescheduled bookings (the calendar refused
    the delete at the time), quietly: the candidate was told when it happened. Returns how
    many are gone now."""
    if config.calendar is None:
        return 0
    with repo.session() as session:
        rows = session.exec(
            select(Booking)
            .where(col(Booking.status) != BookingStatus.BOOKED)
            .where(col(Booking.google_event_id).is_not(None))
            .where(col(Booking.recruiter_id).is_not(None))
        ).all()
    deleted = 0
    for booking in rows:
        if _delete_event(config.calendar, booking, notify=False):
            _set_event(repo, booking.id, None)
            deleted += 1
    return deleted


def _delete_event(calendar: CalendarProvider, booking: Booking, *, notify: bool = True) -> bool:
    event = _event_of(booking)
    if event is None or booking.recruiter_id is None:
        return False
    try:
        calendar.delete_event(booking.recruiter_id, event, notify=notify)
    except CalendarError as exc:
        logger.warning("Calendar event of booking %s not deleted: %s", booking.id, exc)
        record("calendar_event_failed", booking_id=booking.id)
        return False
    return True


def delete_events(
    calendar: CalendarProvider | None,
    bookings: Sequence[tuple[Booking, datetime | None]],
    now: datetime,
) -> list[Booking]:
    """Delete the calendar events of bookings (with their start times) about to be erased or
    purged: upcoming ones tell the attendees, past ones go quietly. Returns the bookings whose
    event is still there (the calendar refused, or there is no calendar)."""
    left: list[Booking] = []
    for booking, start in bookings:
        upcoming = booking.status == BookingStatus.BOOKED and (
            start is None or to_utc(start) > to_utc(now)
        )
        if calendar is None or not _delete_event(calendar, booking, notify=upcoming):
            left.append(booking)
    return left


# --- in-person bookings made before SAZ-64 ---


class OrganizerMissing(Exception):
    """`ONSITE_ORGANIZER_EMAIL` is needed (and its calendar connected) but is not there."""


@dataclass
class OnsiteMigration:
    """What `migrate_onsite_bookings` did (or, on a dry run, would do)."""

    found: int = 0
    """Upcoming in-person bookings still in the old model."""
    moved: int = 0
    events: int = 0
    """Moved bookings whose event is now on the organizer's calendar with both guests."""
    failed: int = 0
    """Left as they were: the old event could not be deleted (run again)."""


def migrate_onsite_bookings(
    repo: Repo, now: datetime, config: SchedulingConfig, *, dry_run: bool = False
) -> OnsiteMigration:
    """Move the upcoming in-person bookings made before SAZ-64, held by a recruiter and without
    the manager, to the new model: the organizer's account (`ONSITE_ORGANIZER_EMAIL`), the
    location's manager as interviewer, and their event re-created through the sync path. The
    old event leaves the recruiter's calendar quietly (the new invitation is the candidate's
    notice); one on the organizer's own calendar is rewritten with the manager invited. A
    booking whose old event cannot be deleted stays as it was, so running it again finishes
    the job; one whose new event fails is moved and `sazon sync-events` writes the event.
    Idempotent. Raises `OrganizerMissing` when the organizer is set but missing or not
    connected, or required (Google) and not set."""
    host = organizer(repo, config)
    if host is None and (config.organizer_email or config.organizer_required):
        raise OrganizerMissing(config.organizer_email or "ONSITE_ORGANIZER_EMAIL")
    host_id = host.id if host else None
    report = OnsiteMigration()
    with repo.session() as session:
        rows = session.exec(
            select(Booking, InterviewSlot)
            .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id))
            .where(col(Booking.status) == BookingStatus.BOOKED)
            .where(or_(col(Booking.mode) == InterviewMode.ONSITE, col(Booking.mode).is_(None)))
            .where(col(InterviewSlot.starts_at) > to_utc(now))
            .order_by(col(InterviewSlot.starts_at))
        ).all()
    for booking, slot in rows:
        location = get_location(slot.location_id)
        if location is None or location.manager is None:
            continue
        manager = location.manager.email
        if booking.recruiter_id == host_id and booking.interviewer_email == manager:
            continue
        report.found += 1
        if dry_run:
            continue
        moved = _repoint(repo, booking, host_id, manager, now, config.calendar)
        if moved is None:
            report.failed += 1
            continue
        report.moved += 1
        if config.calendar is not None and moved.recruiter_id is not None:
            same = booking.recruiter_id == moved.recruiter_id
            moved = _sync_event(repo, moved, booking if same else None, config)
            report.events += bool(moved.google_event_id)
    return report


def _repoint(
    repo: Repo,
    booking: Booking,
    host_id: str | None,
    manager: str,
    now: datetime,
    calendar: CalendarProvider | None,
) -> Booking | None:
    """Give one old in-person booking to the organizer and the manager. Its old event goes
    first from the recruiter's calendar (kept when the organizer is that recruiter, to be
    rewritten); None, with the booking restored, when the calendar refuses the delete."""
    same = booking.recruiter_id == host_id
    changes: dict[str, object] = {
        "recruiter_id": host_id,
        "interviewer_email": manager,
        "mode": InterviewMode.ONSITE,
    }
    if not same:  # the event leaves with the recruiter: a push from there no longer finds it
        changes.update(google_event_id=None, google_calendar_id=None, meet_url=None)
    before = {key: getattr(booking, key) for key in changes}
    row = _update(repo, booking.id, changes, now)
    if same or calendar is None or _event_of(booking) is None:
        return row
    if _delete_event(calendar, booking, notify=False):
        return row
    _update(repo, booking.id, before, now)
    return None


def _update(repo: Repo, booking_id: str, changes: dict[str, object], now: datetime) -> Booking:
    with repo.session() as session:
        row = session.get(Booking, booking_id)
        if row is None:
            raise LookupError(booking_id)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = to_utc(now)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _set_event(repo: Repo, booking_id: str, event: CalendarEvent | None) -> Booking:
    with repo.session() as session:
        booking = session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(booking_id)
        booking.google_event_id = event.event_id if event else None
        booking.google_calendar_id = event.calendar_id if event else None
        booking.meet_url = event.meet_url if event else None
        session.add(booking)
        session.commit()
        session.refresh(booking)
        return booking


# --- reads ---


def mode_of(booking: Booking) -> InterviewMode:
    """A booking's mode; one without (made before video interviews) is in person."""
    return InterviewMode(booking.mode or InterviewMode.ONSITE)


def active_booking(repo: Repo, candidate_id: str) -> Booking | None:
    with repo.session() as session:
        return _active(session, candidate_id)


def active_bookings(
    repo: Repo, candidate_ids: Sequence[str]
) -> dict[str, tuple[Booking, InterviewSlot]]:
    """The active booking and its slot per candidate, for the ones that have one."""
    if not candidate_ids:
        return {}
    with repo.session() as session:
        rows = session.exec(
            select(Booking, InterviewSlot)
            .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id))
            .where(col(Booking.candidate_id).in_(list(candidate_ids)))
            .where(col(Booking.status) == BookingStatus.BOOKED)
        ).all()
    return {booking.candidate_id: (booking, slot) for booking, slot in rows}


def get_slot(repo: Repo, slot_id: str) -> InterviewSlot | None:
    with repo.session() as session:
        return session.get(InterviewSlot, slot_id)


def _get(repo: Repo, booking_id: str) -> Booking:
    with repo.session() as session:
        booking = session.get(Booking, booking_id)
    if booking is None:
        raise LookupError(booking_id)
    return booking


def _active(session: Session, candidate_id: str) -> Booking | None:
    query = (
        select(Booking)
        .where(col(Booking.candidate_id) == candidate_id)
        .where(col(Booking.status) == BookingStatus.BOOKED)
    )
    return session.exec(query).first()


# --- what the candidate sees ---


def local_start(slot: InterviewSlot) -> datetime:
    location = get_location(slot.location_id)
    zone = ZoneInfo(location.timezone if location else "UTC")
    return to_utc(slot.starts_at).astimezone(zone)


def slot_view(slot: InterviewSlot, language: Language) -> Slot:
    """The slot as offered in the chat: "jueves 22 a las 10:00", local time."""
    local = local_start(slot)
    clock = local.strftime("%H:%M")
    weekday = WEEKDAYS[language][local.weekday()]
    return Slot(
        id=slot.id,
        startsAt=local.isoformat(),
        label=f"{weekday} {local.day} {AT[language]} {clock}",
        location=_location_name(slot),
        time=clock,
    )


def booked_view(slot: InterviewSlot, language: Language, booking: Booking | None = None) -> Booked:
    """The booked interview: "jueves 22 de octubre a las 10:00", local time, and the address;
    with the `booking`, also its mode (in person when unset) and Meet link."""
    local = local_start(slot)
    location = get_location(slot.location_id)
    day = format_date(local.date(), language)
    view = Booked(
        slotId=slot.id,
        startsAt=local.isoformat(),
        label=f"{day} {AT[language]} {local.strftime('%H:%M')}",
        location=_location_name(slot),
        address=location.address if location else "",
    )
    if booking is None:
        return view
    mode = ViewMode(booking.mode) if booking.mode else ViewMode.ONSITE
    return view.model_copy(update={"mode": mode, "meet_url": booking.meet_url})


def _location_name(slot: InterviewSlot) -> str:
    """The location as Ana names it to the candidate ("Benito Juárez"), never its internal
    name with the brand."""
    return branch_name(slot.location_id)
