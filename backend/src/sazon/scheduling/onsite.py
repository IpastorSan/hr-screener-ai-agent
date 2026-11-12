"""When a location can hold an in-person interview: pure rules, no database and no network.

In-person interviews are with the location's manager (`service_areas.json`), never on a
recruiter's calendar and never taking a recruiter's time, so several locations can hold one at
the same moment. A start time `t` is open at a location when
- `[t, t + length)` lies inside the location's interview hours for that weekday, read in its
  own time zone;
- fewer than `per_slot` of its in-person interviews overlap `[t, t + length)`;
- it has fewer than `max_per_day` in-person interviews on that local day.

Start times sit on the same grid as video interviews (`availability.STEP`). There is no buffer:
the manager is at the location anyway.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sazon.calendar import Interval
from sazon.engine.service_areas import Location
from sazon.scheduling.availability import MoveIssue, Rules, ceil_to_step, working_windows


@dataclass(frozen=True)
class Venue:
    """A location as in-person availability sees it. `booked` are the start times of its
    active in-person interviews."""

    location_id: str
    timezone: str
    working_hours: Mapping[str, Sequence[Sequence[str]]]
    per_slot: int
    max_per_day: int
    booked: Sequence[datetime] = ()

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def local_day(self, moment: datetime) -> date:
        return moment.astimezone(self.zone).date()


def venue_of(location: Location, booked: Sequence[datetime] = ()) -> Venue | None:
    """The location's in-person availability; None when it has no manager or no hours."""
    if location.manager is None or not any(location.interview_hours.values()):
        return None
    return Venue(
        location_id=location.location_id,
        timezone=location.timezone,
        working_hours=location.interview_hours,
        per_slot=location.interviews_per_slot,
        max_per_day=location.max_interviews_per_day,
        booked=tuple(booked),
    )


def interviews_on(venue: Venue, day: date) -> int:
    return sum(venue.local_day(booked) == day for booked in venue.booked)


def overlapping(venue: Venue, start: datetime, rules: Rules) -> int:
    """How many of the location's interviews overlap `[start, start + length)`."""
    taken = Interval(start, start + rules.length)
    return sum(taken.overlaps(Interval(b, b + rules.length)) for b in venue.booked)


def in_hours(venue: Venue, start: datetime, rules: Rules) -> bool:
    end = start + rules.length
    return any(w.start <= start and end <= w.end for w in working_windows(venue, start, end))


def is_open(venue: Venue, start: datetime, rules: Rules) -> bool:
    if not in_hours(venue, start, rules):
        return False
    if overlapping(venue, start, rules) >= venue.per_slot:
        return False
    return interviews_on(venue, venue.local_day(start)) < venue.max_per_day


def open_starts(venue: Venue, start: datetime, end: datetime, rules: Rules) -> list[datetime]:
    """Every open start time in `[start, end)`, soonest first."""
    found: list[datetime] = []
    for window in working_windows(venue, start, end):
        moment = ceil_to_step(max(window.start, start), rules.step)
        while moment + rules.length <= window.end and moment < end:
            if is_open(venue, moment, rules):
                found.append(moment)
            moment += rules.step
    return sorted(set(found))


def move_issue(venue: Venue, start: datetime, rules: Rules, now: datetime) -> MoveIssue | None:
    """The first rule a move to `start` breaks (`venue.booked` without the moved interview),
    or None when the location would have offered that time."""
    if start <= now:
        return MoveIssue.PAST
    if not in_hours(venue, start, rules):
        return MoveIssue.OUTSIDE_HOURS
    if overlapping(venue, start, rules) >= venue.per_slot:
        return MoveIssue.CLASH
    if interviews_on(venue, venue.local_day(start)) >= venue.max_per_day:
        return MoveIssue.DAILY_MAX
    return None
