"""The availability rules on their own: working hours per time zone, busy blocks, buffer,
daily maximum, daylight saving, and who gets an interview."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sazon.calendar import Interval
from sazon.recruiters import weekday_hours
from sazon.scheduling.availability import (
    Interviewer,
    Rules,
    assignment_order,
    free_starts,
    is_free,
    open_times,
)

MADRID = ZoneInfo("Europe/Madrid")
MEXICO = ZoneInfo("America/Mexico_City")
TIJUANA = ZoneInfo("America/Tijuana")
NO_BUFFER = Rules()
BUFFER = Rules(buffer=timedelta(minutes=10))


def person(
    name: str = "Ana",
    timezone: str = "Europe/Madrid",
    hours: list[list[str]] | None = None,
    *,
    max_per_day: int = 15,
    booked: tuple[datetime, ...] = (),
    busy: tuple[Interval, ...] = (),
) -> Interviewer:
    return Interviewer(
        id=name.lower(),
        name=name,
        timezone=timezone,
        working_hours=weekday_hours(hours or [["10:00", "12:00"]]),
        max_per_day=max_per_day,
        booked=booked,
        busy=busy,
    )


def at(zone: ZoneInfo, day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=zone).astimezone(UTC)


def clocks(starts: list[datetime], zone: ZoneInfo) -> list[str]:
    return [moment.astimezone(zone).strftime("%H:%M") for moment in starts]


def day_of(zone: ZoneInfo, day: int, month: int = 10) -> tuple[datetime, datetime]:
    return at(zone, day, 0, month=month), at(zone, day, 23, 59, month=month)


def test_working_hours_are_read_in_the_recruiters_time_zone():
    madrid = person(hours=[["09:00", "14:00"]])
    starts = free_starts(madrid, *day_of(MADRID, 20), NO_BUFFER)
    assert clocks(starts, MADRID) == [
        "09:00", "09:15", "09:30", "09:45", "10:00", "10:15", "10:30", "10:45",
        "11:00", "11:15", "11:30", "11:45", "12:00", "12:15", "12:30", "12:45",
        "13:00", "13:15", "13:30", "13:45",
    ]  # fmt: skip
    assert starts[0] == datetime(2026, 10, 20, 7, tzinfo=UTC)

    mexico = person("Carlos", "America/Mexico_City", [["09:00", "18:00"]])
    starts = free_starts(mexico, *day_of(MEXICO, 20), NO_BUFFER)
    assert starts[0] == datetime(2026, 10, 20, 15, tzinfo=UTC)  # UTC-6 all year
    assert starts[-1] == datetime(2026, 10, 20, 23, 45, tzinfo=UTC)
    assert len(starts) == 36


def test_daylight_saving_changes_keep_local_hours():
    madrid = person()
    friday = free_starts(madrid, *day_of(MADRID, 23), NO_BUFFER)
    monday = free_starts(madrid, *day_of(MADRID, 26), NO_BUFFER)  # CET from 25 October
    assert friday[0] == datetime(2026, 10, 23, 8, tzinfo=UTC)
    assert monday[0] == datetime(2026, 10, 26, 9, tzinfo=UTC)

    tijuana = person("Dora", "America/Tijuana")
    before = free_starts(tijuana, *day_of(TIJUANA, 30), NO_BUFFER)
    after = free_starts(tijuana, *day_of(TIJUANA, 2, month=11), NO_BUFFER)  # PST from 1 November
    assert before[0] == datetime(2026, 10, 30, 17, tzinfo=UTC)
    assert after[0] == datetime(2026, 11, 2, 18, tzinfo=UTC)
    assert clocks(before, TIJUANA) == clocks(after, TIJUANA) == [
        "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45",
    ]  # fmt: skip


def test_a_madrid_recruiter_interviewing_in_mexico_keeps_madrid_hours():
    """Video interviews cross time zones: the recruiter's own hours decide."""
    madrid = person(hours=[["16:00", "19:00"]])
    starts = free_starts(madrid, at(MEXICO, 20, 0), at(MEXICO, 21, 0), NO_BUFFER)
    assert clocks(starts, MADRID) == [
        "16:00", "16:15", "16:30", "16:45", "17:00", "17:15", "17:30", "17:45",
        "18:00", "18:15", "18:30", "18:45",
    ]  # fmt: skip
    assert clocks(starts, MEXICO)[0] == "08:00"


def test_no_hours_at_the_weekend_and_an_interview_must_fit_the_block():
    short = person(hours=[["10:00", "11:10"]])
    assert clocks(free_starts(short, *day_of(MADRID, 20), NO_BUFFER), MADRID) == [
        "10:00",
        "10:15",
        "10:30",
        "10:45",
    ]
    assert free_starts(short, *day_of(MADRID, 24), NO_BUFFER) == []  # Saturday
    assert not is_free(short, at(MADRID, 20, 11), NO_BUFFER)  # would end at 11:15


def test_overlapping_busy_blocks_and_the_buffer():
    busy = (
        Interval(at(MADRID, 20, 10, 10), at(MADRID, 20, 10, 45)),
        Interval(at(MADRID, 20, 10, 40), at(MADRID, 20, 11, 20)),
    )
    ana = person(busy=busy)
    assert clocks(free_starts(ana, *day_of(MADRID, 20), NO_BUFFER), MADRID) == [
        "11:30",
        "11:45",
    ]
    # A block ending at 11:20 and a buffer of 10 minutes still leave 11:30 (they only touch).
    assert clocks(free_starts(ana, *day_of(MADRID, 20), BUFFER), MADRID) == ["11:30", "11:45"]
    later = person(busy=(Interval(at(MADRID, 20, 10, 10), at(MADRID, 20, 11, 36)),))
    assert free_starts(later, *day_of(MADRID, 20), BUFFER) == []
    assert clocks(free_starts(later, *day_of(MADRID, 20), NO_BUFFER), MADRID) == ["11:45"]


def test_the_buffer_keeps_back_to_back_interviews_apart():
    ana = person(booked=(at(MADRID, 20, 10),))
    assert clocks(free_starts(ana, *day_of(MADRID, 20), NO_BUFFER), MADRID) == [
        "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45",
    ]  # fmt: skip
    assert clocks(free_starts(ana, *day_of(MADRID, 20), BUFFER), MADRID) == [
        "10:30", "10:45", "11:00", "11:15", "11:30", "11:45",
    ]  # fmt: skip
    assert not is_free(ana, at(MADRID, 20, 10), NO_BUFFER)  # already booked then


def test_the_daily_maximum_counts_the_recruiters_local_day():
    full = person(max_per_day=2, booked=(at(MADRID, 20, 10), at(MADRID, 20, 11)))
    assert free_starts(full, *day_of(MADRID, 20), NO_BUFFER) == []
    assert len(free_starts(full, *day_of(MADRID, 21), NO_BUFFER)) == 8

    # 18:30 Monday in Mexico City is 00:30 UTC on Tuesday: it counts for Monday there.
    carlos = person(
        "Carlos",
        "America/Mexico_City",
        [["09:00", "20:00"]],
        max_per_day=1,
        booked=(at(MEXICO, 19, 18, 30),),
    )
    assert carlos.booked[0].day == 20  # Tuesday in UTC
    assert free_starts(carlos, *day_of(MEXICO, 19), NO_BUFFER) == []
    assert free_starts(carlos, *day_of(MEXICO, 20), NO_BUFFER)


def test_a_candidate_sees_a_time_when_any_recruiter_is_free():
    ana = person("Ana", booked=(at(MADRID, 20, 10),))
    bea = person("Bea", busy=(Interval(at(MADRID, 20, 11), at(MADRID, 20, 12)),))
    times = open_times([ana, bea], *day_of(MADRID, 20), BUFFER)
    # 10:15 is Bea's only: Ana's buffer after 10:00. 10:45 is Ana's only: Bea's buffer before
    # her 11:00 meeting.
    assert dict(zip(clocks(list(times), MADRID), times.values(), strict=True)) == {
        "10:00": ["bea"],
        "10:15": ["bea"],
        "10:30": ["ana", "bea"],
        "10:45": ["ana"],
        "11:00": ["ana"],
        "11:15": ["ana"],
        "11:30": ["ana"],
        "11:45": ["ana"],
    }
    nobody = person("Cris", busy=(Interval(at(MADRID, 20, 9), at(MADRID, 20, 13)),))
    assert open_times([nobody], *day_of(MADRID, 20), NO_BUFFER) == {}


def test_the_least_loaded_recruiter_that_day_comes_first():
    start = at(MADRID, 21, 10)
    ana = person("Ana", booked=(at(MADRID, 21, 11), at(MADRID, 22, 10)))
    bea = person("Bea", booked=(at(MADRID, 20, 10), at(MADRID, 20, 11), at(MADRID, 22, 11)))
    cris = person("Cris", booked=(at(MADRID, 22, 10),))
    dani = person("Dani", booked=(at(MADRID, 23, 10),))
    # Day load first (Bea 0, Cris 0, Dani 0, Ana 1), then the week (Cris 1, Dani 1, Bea 3),
    # then the name.
    assert [p.name for p in assignment_order([ana, bea, cris, dani], start)] == [
        "Cris",
        "Dani",
        "Bea",
        "Ana",
    ]
