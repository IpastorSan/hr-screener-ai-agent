"""When a recruiter is free for a video interview: pure rules, no database and no network.
In-person interviews are the location managers' (`onsite`), and never take a recruiter's time.

A start time `t` is free for a recruiter when
- `[t, t + length)` lies inside one of their working-hour blocks for that weekday, read in
  their own time zone (so a Madrid recruiter's 09:00 is 09:00 in Madrid on both sides of a
  daylight-saving change);
- `[t - buffer, t + length + buffer)` touches none of their calendar's busy blocks and none of
  their booked interviews;
- they have fewer than `max_per_day` interviews on that local day.

Every interview lasts 15 minutes, and start times sit on a grid of `step` from the hour (UTC),
which is :00, :15, :30 and :45 local in every zone we serve. A candidate is offered a video
interview at a time when at least one recruiter is free then.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from sazon.calendar import Interval
from sazon.storage.models import WEEKDAY_KEYS

INTERVIEW_LENGTH = timedelta(minutes=15)
STEP = timedelta(minutes=15)
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


class Hours(Protocol):
    """Weekly hours in a time zone: a recruiter's working hours, a location's interview hours."""

    @property
    def zone(self) -> ZoneInfo: ...

    @property
    def working_hours(self) -> Mapping[str, Sequence[Sequence[str]]]: ...


@dataclass(frozen=True)
class Interviewer:
    """A recruiter as availability sees them. `booked` are the start times of their active
    interviews; `busy` their calendar's busy blocks (empty without a calendar)."""

    id: str
    name: str
    timezone: str
    working_hours: Mapping[str, Sequence[Sequence[str]]]
    max_per_day: int
    booked: Sequence[datetime] = ()
    busy: Sequence[Interval] = field(default_factory=tuple)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def local_day(self, moment: datetime) -> date:
        return moment.astimezone(self.zone).date()


@dataclass(frozen=True)
class Rules:
    length: timedelta = INTERVIEW_LENGTH
    buffer: timedelta = timedelta(0)
    step: timedelta = STEP


def working_windows(person: Hours, start: datetime, end: datetime) -> list[Interval]:
    """The blocks of these weekly hours that overlap `[start, end)`, in UTC."""
    zone = person.zone
    first = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date() + timedelta(days=1)
    windows: list[Interval] = []
    day = first
    while day <= last:
        for block in person.working_hours.get(WEEKDAY_KEYS[day.weekday()], ()):
            opens = datetime.combine(day, _clock(block[0]), tzinfo=zone).astimezone(UTC)
            closes = datetime.combine(day, _clock(block[1]), tzinfo=zone).astimezone(UTC)
            window = Interval(opens, closes)
            if opens < closes and window.overlaps(Interval(start, end)):
                windows.append(window)
        day += timedelta(days=1)
    return windows


def interviews_on(person: Interviewer, day: date) -> int:
    return sum(person.local_day(booked) == day for booked in person.booked)


def is_free(person: Interviewer, start: datetime, rules: Rules) -> bool:
    end = start + rules.length
    if not any(w.start <= start and end <= w.end for w in working_windows(person, start, end)):
        return False
    padded = Interval(start - rules.buffer, end + rules.buffer)
    if any(padded.overlaps(block) for block in person.busy):
        return False
    if any(padded.overlaps(Interval(b, b + rules.length)) for b in person.booked):
        return False
    return interviews_on(person, person.local_day(start)) < person.max_per_day


class MoveIssue(StrEnum):
    """Why a time the recruiter moved an interview to in their own calendar breaks a rule we
    offer times by (SAZ-40). The booking follows the calendar anyway and carries the issue."""

    PAST = "past"
    ALL_DAY = "all_day"
    OUTSIDE_HOURS = "outside_hours"
    CLASH = "clash"
    BUSY = "busy"
    DAILY_MAX = "daily_max"


def move_issue(
    person: Interviewer, start: datetime, rules: Rules, now: datetime, own: Interval | None = None
) -> MoveIssue | None:
    """The first rule a move to `start` breaks, or None when we would have offered that time.
    `person.booked` holds their other interviews; `own` is the moved event as it shows in their
    free/busy, which does not clash with itself."""
    end = start + rules.length
    if start <= now:
        return MoveIssue.PAST
    if not any(w.start <= start and end <= w.end for w in working_windows(person, start, end)):
        return MoveIssue.OUTSIDE_HOURS
    padded = Interval(start - rules.buffer, end + rules.buffer)
    if any(padded.overlaps(Interval(b, b + rules.length)) for b in person.booked):
        return MoveIssue.CLASH
    busy = [piece for block in person.busy for piece in _without(block, own)]
    if any(padded.overlaps(block) for block in busy):
        return MoveIssue.BUSY
    if interviews_on(person, person.local_day(start)) >= person.max_per_day:
        return MoveIssue.DAILY_MAX
    return None


def _without(block: Interval, own: Interval | None) -> list[Interval]:
    """`block` minus `own`: free/busy merges the event with whatever touches it."""
    if own is None or not block.overlaps(own):
        return [block]
    pieces = [Interval(block.start, own.start), Interval(own.end, block.end)]
    return [piece for piece in pieces if piece.start < piece.end]


def free_starts(
    person: Interviewer, start: datetime, end: datetime, rules: Rules
) -> list[datetime]:
    """Every free start time in `[start, end)`, soonest first."""
    found: list[datetime] = []
    for window in working_windows(person, start, end):
        moment = ceil_to_step(max(window.start, start), rules.step)
        while moment + rules.length <= window.end and moment < end:
            if is_free(person, moment, rules):
                found.append(moment)
            moment += rules.step
    return sorted(set(found))


def open_times(
    people: Iterable[Interviewer], start: datetime, end: datetime, rules: Rules
) -> dict[datetime, list[str]]:
    """Start time to the recruiters free then, for every time at least one is."""
    times: dict[datetime, list[str]] = {}
    for person in people:
        for moment in free_starts(person, start, end, rules):
            times.setdefault(moment, []).append(person.id)
    return dict(sorted(times.items()))


def assignment_order(people: Iterable[Interviewer], start: datetime) -> list[Interviewer]:
    """Who gets an interview at `start`, first choice first: the fewest interviews that local
    day, then the fewest booked overall (spreads the week), then name and id (stable)."""

    def load(person: Interviewer) -> tuple[int, int, str, str]:
        day = interviews_on(person, person.local_day(start))
        return day, len(person.booked), person.name, person.id

    return sorted(people, key=load)


def _clock(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def ceil_to_step(moment: datetime, step: timedelta) -> datetime:
    elapsed = moment - EPOCH
    remainder = elapsed % step
    return moment if not remainder else moment + (step - remainder)
