"""Offering and booking interview times from the recruiters' working hours (fixed source)."""

import threading
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from sazon import recruiters
from sazon.engine.models import Language, Schedule
from sazon.engine.slots import Miss, SlotQuery
from sazon.scheduling import service as scheduling
from sazon.scheduling.service import (
    SlotUnavailable,
    _in_block,  # pyright: ignore[reportPrivateUsage]
    book,
    cancel,
    offer,
    reschedule,
)
from sazon.storage.models import (
    Booking,
    BookingStatus,
    Candidate,
    Conversation,
    InterviewSlot,
    Recruiter,
)
from sazon.storage.repo import Repo, init_db, make_engine, to_utc

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # Monday; 12:00 in Madrid, 04:00 in CDMX
MADRID = ZoneInfo("Europe/Madrid")
GRACIA = "es-bcn-gracia"
ZAPOPAN = "mx-gdl-zapopan"
TIJUANA = "mx-tij-centro"
SEVILLA = "es-sev-centro"
BLOCKS = [["10:00", "12:00"], ["16:00", "18:00"]]


def interviewer(
    repo: Repo, name: str, location_id: str, timezone: str, country: str = "ES"
) -> Recruiter:
    return recruiters.create(
        repo,
        NOW,
        name=name,
        email=f"{name.lower()}@gruposazon.example",
        country=country,
        timezone=timezone,
        location_ids=[location_id],
        working_hours=recruiters.weekday_hours(BLOCKS),
    )


def staffed(repo: Repo) -> Repo:
    """Two interviewers at Gràcia, one in Zapopan, one in Tijuana, each 10-12 and 16-18 local."""
    interviewer(repo, "Ana", GRACIA, "Europe/Madrid")
    interviewer(repo, "Bea", GRACIA, "Europe/Madrid")
    interviewer(repo, "Carlos", ZAPOPAN, "America/Mexico_City", "MX")
    interviewer(repo, "Dora", TIJUANA, "America/Tijuana", "MX")
    return repo


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return staffed(Repo(engine))


def applicant(repo: Repo) -> tuple[str, str]:
    candidate = repo.save(Candidate(channel="web", created_at=NOW, updated_at=NOW))
    conversation = repo.save(
        Conversation(
            candidate_id=candidate.id,
            channel="web",
            state={},
            started_at=NOW,
            last_activity_at=NOW,
        )
    )
    return candidate.id, conversation.id


def local(slot: InterviewSlot, zone: str) -> datetime:
    return to_utc(slot.starts_at).astimezone(ZoneInfo(zone))


def active_id(repo: Repo, candidate_id: str) -> str | None:
    booking = scheduling.active_booking(repo, candidate_id)
    return booking.id if booking else None


def taken(repo: Repo, slot_id: str) -> int:
    with repo.session() as session:
        query = select(Booking).where(Booking.slot_id == slot_id, Booking.status == "booked")
        return len(session.exec(query).all())


# --- offering ---


def test_offer_gives_three_soonest_days_in_the_preferred_block(repo):
    morning = offer(repo, GRACIA, [Schedule.MORNING], None, NOW)
    assert [local(s, "Europe/Madrid") for s in morning] == [
        datetime(2026, 10, 20, 10, tzinfo=MADRID),
        datetime(2026, 10, 21, 10, tzinfo=MADRID),
        datetime(2026, 10, 22, 10, tzinfo=MADRID),
    ]
    afternoon = offer(repo, GRACIA, [Schedule.AFTERNOON], None, NOW)
    assert [local(s, "Europe/Madrid").hour for s in afternoon] == [16, 16, 16]


def test_afternoon_and_evening_are_separate_blocks(repo):
    recruiters.create(
        repo,
        NOW,
        name="Eva",
        email="eva@gruposazon.example",
        country="ES",
        location_ids=[SEVILLA],
        working_hours=recruiters.weekday_hours([["14:00", "16:00"], ["18:00", "20:00"]]),
    )
    afternoon = offer(repo, SEVILLA, [Schedule.AFTERNOON], None, NOW)
    assert [local(s, "Europe/Madrid").hour for s in afternoon] == [14, 14, 14]
    evening = offer(repo, SEVILLA, [Schedule.EVENING], None, NOW)
    assert [local(s, "Europe/Madrid").hour for s in evening] == [18, 18, 18]
    assert not _in_block(time(14), [Schedule.EVENING])
    assert _in_block(time(17, 59), [Schedule.AFTERNOON])
    assert not _in_block(time(18), [Schedule.AFTERNOON])
    assert _in_block(time(18), [Schedule.EVENING])


def test_several_blocks_prefer_a_time_in_any_of_them(repo):
    both = offer(repo, GRACIA, [Schedule.MORNING, Schedule.EVENING], None, NOW)
    assert [local(s, "Europe/Madrid").hour for s in both] == [10, 10, 10]
    assert _in_block(time(18), [Schedule.MORNING, Schedule.EVENING])
    assert not _in_block(time(17), [Schedule.MORNING, Schedule.EVENING])
    assert _in_block(time(9), [Schedule.MORNING, Schedule.AFTERNOON])
    assert not _in_block(time(9), [Schedule.AFTERNOON, Schedule.EVENING])
    assert _in_block(time(9), [Schedule.FLEXIBLE])
    assert _in_block(time(9), None)


def test_flexible_gets_the_soonest_slots_one_per_day(repo):
    slots = offer(repo, GRACIA, [Schedule.FLEXIBLE], None, NOW)
    assert [local(s, "Europe/Madrid").strftime("%d %H:%M") for s in slots] == [
        "20 10:00",
        "21 10:00",
        "22 10:00",
    ]


def test_offer_is_in_the_location_time_zone(repo):
    # 04:00 Monday in Mexico City: tomorrow there is Tuesday 20.
    mexico = offer(repo, ZAPOPAN, [Schedule.AFTERNOON], None, NOW)
    assert [local(s, "America/Mexico_City").strftime("%a %d %H:%M") for s in mexico] == [
        "Tue 20 16:00",
        "Wed 21 16:00",
        "Thu 22 16:00",
    ]
    tijuana = offer(repo, TIJUANA, [Schedule.MORNING], None, NOW)
    assert [local(s, "America/Tijuana").hour for s in tijuana] == [10, 10, 10]
    assert to_utc(tijuana[0].starts_at) == datetime(2026, 10, 20, 17, tzinfo=UTC)


def test_offer_never_returns_today_or_the_past(repo):
    late_monday = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)  # Tuesday 09:00 in Madrid
    slots = offer(repo, GRACIA, [Schedule.MORNING], None, late_monday)
    assert all(to_utc(s.starts_at) > late_monday + timedelta(hours=12) for s in slots)
    assert local(slots[0], "Europe/Madrid").date() == date(2026, 10, 21)


def test_offer_looks_two_weeks_ahead_and_never_at_weekends(repo):
    every = offer(repo, GRACIA, None, None, NOW, n=1000)
    days = sorted({local(slot, "Europe/Madrid").date() for slot in every})
    assert days[0] == date(2026, 10, 20)
    assert days[-1] == date(2026, 11, 2)  # 14 days from Tuesday 20, weekdays only
    assert all(day.weekday() < 5 for day in days)
    assert len(every) == len(days) * 16  # 10:00..11:45 and 16:00..17:45


def test_offer_skips_times_nobody_is_free_and_excluded_ones(repo):
    first = offer(repo, GRACIA, [Schedule.MORNING], None, NOW)[0]
    for _ in range(2):  # both Gràcia interviewers take Tuesday 10:00
        book(repo, *applicant(repo), first.id, NOW)
    again = offer(repo, GRACIA, [Schedule.MORNING], None, NOW)
    assert first.id not in {s.id for s in again}
    # 10:15 is inside both interviewers' 10-minute buffer: 10:30 is next.
    assert local(again[0], "Europe/Madrid") == datetime(2026, 10, 20, 10, 30, tzinfo=MADRID)

    more = offer(repo, GRACIA, [Schedule.MORNING], None, NOW, exclude={s.id for s in again})
    assert not {s.id for s in more} & {s.id for s in again}
    assert len(more) == 3


def test_offer_prefers_slots_before_the_start_date(repo):
    start = date(2026, 10, 21)
    slots = offer(repo, GRACIA, [Schedule.AFTERNOON], start, NOW)
    days = [local(s, "Europe/Madrid").date() for s in slots]
    assert days[:2] == [date(2026, 10, 20), date(2026, 10, 21)]
    assert sum(day <= start for day in days) == 2


def weekend_interviewer(repo: Repo, location_id: str) -> Recruiter:
    return recruiters.create(
        repo,
        NOW,
        name="Sara",
        email="sara@gruposazon.example",
        country="ES",
        timezone="Europe/Madrid",
        location_ids=[location_id],
        working_hours={"sat": [["10:00", "12:00"]]},
    )


def test_with_nothing_in_the_window_the_next_four_weeks_are_searched(repo):
    """SAZ-42: no time in `SCHEDULING_DAYS`, so up to 28 days ahead before saying none."""
    weekend_interviewer(repo, SEVILLA)
    short = scheduling.SchedulingConfig(days=3)  # Tuesday to Thursday: she works Saturdays

    slots = offer(repo, SEVILLA, [Schedule.AFTERNOON], date(2026, 10, 26), NOW, config=short)

    days = [local(s, "Europe/Madrid").date() for s in slots]
    assert days == [date(2026, 10, 24), date(2026, 10, 31), date(2026, 11, 7)]


def test_the_wider_search_stops_at_four_weeks(repo):
    weekend_interviewer(repo, SEVILLA)
    every = offer(
        repo, SEVILLA, None, None, NOW, n=1000, config=scheduling.SchedulingConfig(days=3)
    )
    days = sorted({local(slot, "Europe/Madrid").date() for slot in every})
    assert days[-1] == date(2026, 11, 14)  # the last Saturday within 28 days of Tuesday 20


def test_a_window_with_times_is_not_widened(repo):
    weekend_interviewer(repo, GRACIA)
    every = offer(repo, GRACIA, None, None, NOW, n=1000, config=scheduling.SchedulingConfig(days=3))
    days = sorted({local(slot, "Europe/Madrid").date() for slot in every})
    assert days[-1] == date(2026, 10, 22)


def test_the_wider_search_skips_turned_down_times(repo):
    weekend_interviewer(repo, SEVILLA)
    short = scheduling.SchedulingConfig(days=3)
    first = offer(repo, SEVILLA, None, None, NOW, n=1, config=short)
    again = offer(repo, SEVILLA, None, None, NOW, n=1, config=short, exclude={first[0].id})
    assert again
    assert again[0].id != first[0].id


def test_offer_for_an_unknown_location_is_empty(repo):
    assert offer(repo, "xx-nowhere", None, None, NOW) == []


# --- times asked about and days turned down (QA batch 2, D9) ---


def starts(found: scheduling.Found) -> list[datetime]:
    return [local(slot, "Europe/Madrid") for slot in found.slots]


def test_a_question_about_the_afternoon_offers_only_afternoon_times(repo):
    found = scheduling.lookup(
        repo, GRACIA, [Schedule.MORNING], None, NOW, query=SlotQuery(part="afternoon")
    )
    assert found.matched
    assert [moment.hour for moment in starts(found)] == [16, 16, 16]
    assert len({moment.date() for moment in starts(found)}) == 3


def test_a_question_about_a_weekday_offers_that_weekday(repo):
    found = scheduling.lookup(repo, GRACIA, None, None, NOW, query=SlotQuery(weekdays=(4,)))
    assert found.matched
    assert {moment.date() for moment in starts(found)} <= {
        date(2026, 10, 23),
        date(2026, 10, 30),
        date(2026, 11, 6),
        date(2026, 11, 13),
    }
    assert starts(found)[0].date() == date(2026, 10, 23)


def test_a_question_about_next_week_offers_that_week(repo):
    found = scheduling.lookup(
        repo, GRACIA, None, None, NOW, query=SlotQuery(week_of=date(2026, 10, 26))
    )
    assert found.matched
    assert all(date(2026, 10, 26) <= m.date() <= date(2026, 11, 1) for m in starts(found))


def test_nothing_fitting_the_question_offers_the_nearest_days(repo):
    """Nobody works Saturdays at Gràcia: the times closest to Saturday 24, not a promise."""
    found = scheduling.lookup(repo, GRACIA, None, None, NOW, query=SlotQuery(weekdays=(5,)))
    assert not found.matched
    days = {moment.date() for moment in starts(found)}
    assert days == {date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 22)}


# --- a time, a weekday, other options (call of 2026-10-22) ---

TUESDAY_27 = date(2026, 10, 27)


def clocks(found: scheduling.Found) -> list[str]:
    return [moment.strftime("%d %H:%M") for moment in starts(found)]


def test_a_weekday_alone_offers_the_nearest_such_day_first(repo):
    """ "¿Puede ser el martes?" offered three Tuesdays at 10:00, not the nearest one's times."""
    found = scheduling.lookup(
        repo, GRACIA, [Schedule.MORNING], None, NOW, query=SlotQuery(weekdays=(1,))
    )
    assert found.matched
    assert clocks(found) == ["20 10:00", "20 10:15", "20 10:30"]


def test_a_free_time_asked_about_comes_first_then_the_closest(repo):
    query = SlotQuery(days=(TUESDAY_27,), at=(time(11),))
    found = scheduling.lookup(repo, GRACIA, [Schedule.MORNING], None, NOW, query=query)
    assert found.matched
    assert clocks(found) == ["27 11:00", "27 10:45", "27 11:15"]


def test_a_time_asked_about_beats_the_preferred_block(repo):
    """A morning person who asks for 16:00 gets 16:00: the schedule only ranks."""
    query = SlotQuery(days=(TUESDAY_27,), at=(time(4), time(16)))
    found = scheduling.lookup(repo, GRACIA, [Schedule.MORNING], None, NOW, query=query)
    assert found.matched
    assert clocks(found)[0] == "27 16:00"


def test_a_time_that_is_not_free_offers_the_closest_that_day_and_says_so(repo):
    """Nobody interviews at 13:00 at Gràcia (10-12, 16-18)."""
    query = SlotQuery(days=(TUESDAY_27,), at=(time(13),))
    found = scheduling.lookup(repo, GRACIA, None, None, NOW, query=query)
    assert found.miss == Miss.TIME
    assert clocks(found) == ["27 11:45", "27 11:30", "27 11:15"]


def test_a_time_on_a_day_with_nothing_free_offers_that_time_on_the_nearest_days(repo):
    query = SlotQuery(weekdays=(5,), at=(time(11),))
    found = scheduling.lookup(repo, GRACIA, None, None, NOW, query=query)
    assert found.miss == Miss.TIME
    assert {moment.strftime("%H:%M") for moment in starts(found)} == {"11:00"}


def every_time_on(repo: Repo, day: date) -> list[str]:
    every = offer(repo, GRACIA, None, None, NOW, n=1000)
    return [slot.id for slot in every if local(slot, "Europe/Madrid").date() == day]


def test_a_day_with_nothing_left_beyond_the_times_offered_says_so(repo):
    query = SlotQuery(days=(TUESDAY_27,), other=True)
    exclude = every_time_on(repo, TUESDAY_27)
    found = scheduling.lookup(repo, GRACIA, None, None, NOW, query=query, exclude=exclude)
    assert found.miss == Miss.MORE
    assert found.slots
    assert TUESDAY_27 not in {moment.date() for moment in starts(found)}


def test_other_options_move_to_days_not_offered_yet(repo):
    first = offer(repo, GRACIA, [Schedule.MORNING], None, NOW)
    shown = {local(slot, "Europe/Madrid").date() for slot in first}
    found = scheduling.lookup(
        repo,
        GRACIA,
        [Schedule.MORNING],
        None,
        NOW,
        query=SlotQuery(other=True),
        exclude=[slot.id for slot in first],
    )
    assert found.matched
    assert len(found.slots) == 3
    assert not shown & {moment.date() for moment in starts(found)}


def test_a_new_round_skips_the_days_turned_down(repo):
    first = offer(repo, GRACIA, [Schedule.MORNING], None, NOW)
    turned_down = {local(slot, "Europe/Madrid").date() for slot in first}
    found = scheduling.lookup(
        repo,
        GRACIA,
        [Schedule.MORNING],
        None,
        NOW,
        exclude={slot.id for slot in first},
        avoid_days=turned_down,
    )
    assert found.slots
    assert not {moment.date() for moment in starts(found)} & turned_down


# --- booking ---


def test_book_assigns_a_recruiter_and_is_idempotent(repo):
    candidate_id, conversation_id = applicant(repo)
    slot = offer(repo, GRACIA, None, None, NOW)[0]

    first = book(repo, candidate_id, conversation_id, slot.id, NOW)
    again = book(repo, candidate_id, conversation_id, slot.id, NOW)

    assert again.id == first.id
    assert first.recruiter_id is not None
    assert taken(repo, slot.id) == 1


def test_a_time_with_every_interviewer_taken_rejects_another_booking(repo):
    slot = offer(repo, GRACIA, None, None, NOW)[0]
    for _ in range(2):
        candidate_id, conversation_id = applicant(repo)
        book(repo, candidate_id, conversation_id, slot.id, NOW)
    third, conversation_id = applicant(repo)
    with pytest.raises(SlotUnavailable):
        book(repo, third, conversation_id, slot.id, NOW)
    assert taken(repo, slot.id) == 2
    assert scheduling.active_booking(repo, third) is None


def test_a_started_slot_cannot_be_booked(repo):
    candidate_id, conversation_id = applicant(repo)
    slot = offer(repo, GRACIA, None, None, NOW)[0]
    with pytest.raises(SlotUnavailable):
        book(repo, candidate_id, conversation_id, slot.id, to_utc(slot.starts_at))


def test_concurrent_bookings_never_double_book_the_one_free_recruiter(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'race.db'}")
    init_db(engine)
    repo = Repo(engine)
    interviewer(repo, "Ana", GRACIA, "Europe/Madrid")
    slot = offer(repo, GRACIA, None, None, NOW)[0]
    applicants = [applicant(repo) for _ in range(6)]
    barrier = threading.Barrier(len(applicants))
    outcomes: list[str] = []

    def attempt(candidate_id: str, conversation_id: str) -> None:
        barrier.wait()
        try:
            book(repo, candidate_id, conversation_id, slot.id, NOW)
            outcomes.append("booked")
        except SlotUnavailable:
            outcomes.append("rejected")

    threads = [threading.Thread(target=attempt, args=pair) for pair in applicants]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["booked", *["rejected"] * 5]
    with repo.session() as session:
        bookings = session.exec(select(Booking).where(Booking.slot_id == slot.id)).all()
    assert len(bookings) == 1


def test_one_candidate_holds_one_active_booking(repo):
    candidate_id, conversation_id = applicant(repo)
    first, second = offer(repo, GRACIA, None, None, NOW)[:2]

    old = book(repo, candidate_id, conversation_id, first.id, NOW)
    new = book(repo, candidate_id, conversation_id, second.id, NOW)

    with repo.session() as session:
        rows = {b.id: b for b in session.exec(select(Booking)).all()}
    assert rows[old.id].status == BookingStatus.RESCHEDULED
    assert rows[new.id].status == BookingStatus.BOOKED
    assert taken(repo, first.id) == 0
    assert taken(repo, second.id) == 1


def test_reschedule_moves_the_booking_and_frees_the_old_place(repo):
    candidate_id, conversation_id = applicant(repo)
    first, second = offer(repo, GRACIA, None, None, NOW)[:2]
    booking = book(repo, candidate_id, conversation_id, first.id, NOW)

    moved = reschedule(repo, booking.id, second.id, NOW)

    assert moved.slot_id == second.id
    assert active_id(repo, candidate_id) == moved.id
    assert taken(repo, first.id) == 0


def test_a_failed_reschedule_keeps_the_current_booking(repo):
    candidate_id, conversation_id = applicant(repo)
    first, second = offer(repo, GRACIA, None, None, NOW)[:2]
    booking = book(repo, candidate_id, conversation_id, first.id, NOW)
    for _ in range(2):
        other, other_conversation = applicant(repo)
        book(repo, other, other_conversation, second.id, NOW)

    with pytest.raises(SlotUnavailable):
        reschedule(repo, booking.id, second.id, NOW)

    assert active_id(repo, candidate_id) == booking.id
    assert taken(repo, first.id) == 1


def test_cancel_frees_the_place_once(repo):
    candidate_id, conversation_id = applicant(repo)
    slot = offer(repo, GRACIA, None, None, NOW)[0]
    booking = book(repo, candidate_id, conversation_id, slot.id, NOW)

    cancelled = cancel(repo, booking.id, NOW)
    cancel(repo, booking.id, NOW)

    assert cancelled.status == BookingStatus.CANCELLED
    assert taken(repo, slot.id) == 0
    assert scheduling.active_booking(repo, candidate_id) is None


# --- labels ---


def test_slot_labels_are_local_and_localized(repo):
    slot = offer(repo, ZAPOPAN, [Schedule.MORNING], None, NOW)[0]
    spanish = scheduling.slot_view(slot, Language.ES)
    assert spanish.label == "martes 20 a las 10:00"
    assert spanish.time == "10:00"
    assert spanish.location == "Zapopan"
    assert spanish.starts_at == "2026-10-20T10:00:00-06:00"
    assert scheduling.slot_view(slot, Language.EN).label == "Tuesday 20 at 10:00"

    booked = scheduling.booked_view(slot, Language.ES)
    assert booked.label == "martes 20 de octubre a las 10:00"
    assert booked.address.startswith("Av. Patria 1200")
    assert scheduling.booked_view(slot, Language.EN).label == "Tuesday 20 October at 10:00"
