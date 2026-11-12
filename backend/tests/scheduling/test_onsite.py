"""In-person interviews with the location's manager (SAZ-64): the location's hours in its own
time zone, one interview per slot, at most 15 a day, never a recruiter's time, and the
organizer whose calendar holds the events."""

import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from sazon import recruiters
from sazon.calendar.fake import FakeCalendar
from sazon.engine.service_areas import Manager, get_location, load_locations, parse_location
from sazon.scheduling import onsite
from sazon.scheduling import service as scheduling
from sazon.scheduling.availability import MoveIssue, Rules
from sazon.scheduling.service import SchedulingConfig, SlotUnavailable
from sazon.storage.models import (
    Booking,
    BookingStatus,
    Candidate,
    Conversation,
    InterviewMode,
    InterviewSlot,
    Recruiter,
)
from sazon.storage.repo import Repo, init_db, make_engine, to_utc

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # Monday
GRACIA = "es-bcn-gracia"
SANTS = "es-bcn-sants"
TIJUANA = "mx-tij-centro"
MADRID = ZoneInfo("Europe/Madrid")
ONSITE = InterviewMode.ONSITE
TUESDAY_10 = datetime(2026, 10, 20, 8, tzinfo=UTC)  # 10:00 in Madrid
RULES = Rules()
TUESDAY = [  # the 32 in-person times at Gràcia on Tuesday 20
    TUESDAY_10 + timedelta(hours=h, minutes=15 * i) for h in (0, 6) for i in range(16)
]


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return Repo(engine)


def applicant(repo: Repo, location_id: str = GRACIA) -> tuple[str, str]:
    candidate = repo.save(
        Candidate(
            channel="web",
            email="lucia@example.com",
            profile={"full_name": "Lucía García"},
            location_id=location_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    chat = repo.save(
        Conversation(
            candidate_id=candidate.id,
            channel="web",
            state={},
            started_at=NOW,
            last_activity_at=NOW,
        )
    )
    return candidate.id, chat.id


def slot_at(repo: Repo, start: datetime, location_id: str = GRACIA) -> InterviewSlot:
    [slot] = scheduling.materialize(repo, location_id, [start])
    return slot


def book_onsite(
    repo: Repo,
    start: datetime = TUESDAY_10,
    location_id: str = GRACIA,
    config: SchedulingConfig | None = None,
) -> Booking:
    candidate_id, chat_id = applicant(repo, location_id)
    slot = slot_at(repo, start, location_id)
    return scheduling.book(
        repo, candidate_id, chat_id, slot.id, NOW, mode=ONSITE, config=config or SchedulingConfig()
    )


def recruiter(repo: Repo, name: str, **fields: object) -> Recruiter:
    fields.setdefault("location_ids", [GRACIA])
    fields.setdefault("working_hours", recruiters.weekday_hours([["10:00", "12:00"]]))
    return recruiters.create(
        repo,
        NOW,
        name=name,
        email=f"{name.lower()}@gruposazon.example",
        country="ES",
        **fields,  # pyright: ignore[reportArgumentType]
    )


def clocks(starts: list[datetime], zone: ZoneInfo) -> list[str]:
    return [moment.astimezone(zone).strftime("%H:%M") for moment in starts]


def venue(location_id: str = GRACIA, *booked: datetime) -> onsite.Venue:
    location = get_location(location_id)
    assert location is not None
    found = onsite.venue_of(location, booked)
    assert found is not None
    return found


def day(zone: ZoneInfo, on: date) -> tuple[datetime, datetime]:
    start = datetime(on.year, on.month, on.day, tzinfo=zone)
    return to_utc(start), to_utc(start + timedelta(days=1))


# --- the data ---


def test_every_location_has_a_manager_and_the_default_hours():
    for location in load_locations().values():
        assert location.manager is not None, location.location_id
        assert location.manager.email == f"gerencia.{location.location_id}@gruposazon.example"
        assert location.manager.name.strip()
        assert location.interview_hours["mon"] == [["10:00", "14:00"], ["16:00", "20:00"]]
        assert "sat" not in location.interview_hours
        assert (location.interviews_per_slot, location.max_interviews_per_day) == (1, 15)


def test_a_location_can_override_its_countrys_hours_and_limits():
    defaults = {
        "MX": {
            "interview_hours": {"mon": [["10:00", "14:00"]]},
            "interviews_per_slot": 1,
            "max_interviews_per_day": 15,
        }
    }
    raw = {
        "location_id": "mx-test",
        "name": "Sazón Prueba",
        "city": "Prueba",
        "country": "MX",
        "timezone": "America/Mexico_City",
        "zones": [],
        "aliases": [],
        "manager": {"name": "Ana Prueba", "email": " Gerencia.Prueba@GrupoSazon.example "},
        "interview_hours": {"sat": [["09:00", "13:00"]]},
        "interviews_per_slot": 2,
    }
    location = parse_location(raw, defaults)
    assert location.manager == Manager("Ana Prueba", "gerencia.prueba@gruposazon.example")
    assert location.interview_hours == {"sat": [["09:00", "13:00"]]}
    assert (location.interviews_per_slot, location.max_interviews_per_day) == (2, 15)
    assert parse_location({**raw, "country": "ES"}, defaults).max_interviews_per_day == 15


# --- the rules on their own ---


def test_the_hours_are_the_locations_own_local_time_on_a_fifteen_minute_grid():
    tuesday = date(2026, 10, 20)
    starts = onsite.open_starts(venue(), *day(MADRID, tuesday), RULES)
    assert len(starts) == 32  # 16 in 10:00-14:00, 16 in 16:00-20:00
    assert clocks(starts, MADRID)[:5] == ["10:00", "10:15", "10:30", "10:45", "11:00"]
    assert clocks(starts, MADRID)[15:17] == ["13:45", "16:00"]
    assert clocks(starts, MADRID)[-1] == "19:45"

    tijuana = ZoneInfo("America/Tijuana")
    starts = onsite.open_starts(venue(TIJUANA), *day(tijuana, tuesday), RULES)
    assert starts[0] == datetime(2026, 10, 20, 17, tzinfo=UTC)  # 10:00 PDT
    after = onsite.open_starts(venue(TIJUANA), *day(tijuana, date(2026, 11, 3)), RULES)
    assert after[0] == datetime(2026, 11, 3, 18, tzinfo=UTC)  # 10:00 PST
    saturday = onsite.open_starts(venue(), *day(MADRID, date(2026, 10, 24)), RULES)
    assert saturday == []


def test_one_interview_per_slot_and_overlaps_count():
    taken = venue(GRACIA, TUESDAY_10)
    assert not onsite.is_open(taken, TUESDAY_10, RULES)
    assert onsite.is_open(taken, TUESDAY_10 + timedelta(minutes=15), RULES)
    # A booking moved off the grid (10:10) overlaps both 10:00 and 10:15.
    moved = venue(GRACIA, TUESDAY_10 + timedelta(minutes=10))
    assert not onsite.is_open(moved, TUESDAY_10, RULES)
    assert not onsite.is_open(moved, TUESDAY_10 + timedelta(minutes=15), RULES)
    assert onsite.is_open(replace(taken, per_slot=2), TUESDAY_10, RULES)


def test_the_daily_maximum_counts_the_locations_local_day():
    fifteen = tuple(TUESDAY[:15])
    full = venue(GRACIA, *fifteen)
    tuesday = date(2026, 10, 20)
    assert onsite.open_starts(full, *day(MADRID, tuesday), RULES) == []
    assert len(onsite.open_starts(full, *day(MADRID, date(2026, 10, 21)), RULES)) == 32
    fourteen = venue(GRACIA, *fifteen[:-1])
    assert len(onsite.open_starts(fourteen, *day(MADRID, tuesday), RULES)) == 32 - 14


def test_a_moved_interview_is_checked_against_the_location():
    place = venue(GRACIA, TUESDAY_10)
    eleven = TUESDAY_10 + timedelta(hours=1)
    assert onsite.move_issue(place, eleven, RULES, NOW) is None
    assert onsite.move_issue(place, NOW - timedelta(hours=1), RULES, NOW) == MoveIssue.PAST
    late = TUESDAY_10 + timedelta(hours=11)  # 21:00
    assert onsite.move_issue(place, late, RULES, NOW) == MoveIssue.OUTSIDE_HOURS
    assert onsite.move_issue(place, TUESDAY_10, RULES, NOW) == MoveIssue.CLASH
    full = replace(place, max_per_day=1)
    assert onsite.move_issue(full, eleven, RULES, NOW) == MoveIssue.DAILY_MAX


# --- through the scheduling service ---


def test_in_person_times_come_from_the_location_not_the_recruiters(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()
    calendar.add_busy(ana.id, TUESDAY_10 - timedelta(days=1), TUESDAY_10 + timedelta(days=30))
    config = SchedulingConfig(calendar=calendar)

    every = scheduling.offer(repo, GRACIA, None, None, NOW, n=1000, mode=ONSITE, config=config)
    assert len(every) == 10 * 32  # two weeks of weekdays, the location's hours
    assert scheduling.offer(repo, GRACIA, None, None, NOW, config=config) == []  # video: busy
    assert scheduling.available_modes(repo, GRACIA, config=config) == [
        InterviewMode.ONSITE,
        InterviewMode.VIDEO,
    ]


def test_an_in_person_booking_never_takes_a_recruiters_time(repo: Repo):
    ana = recruiter(repo, "Ana", max_interviews_per_day=1)
    booking = book_onsite(repo)
    assert booking.recruiter_id is None
    assert booking.interviewer_email == "gerencia.es-bcn-gracia@gruposazon.example"
    video = scheduling.offer(repo, GRACIA, None, None, NOW, n=50)
    assert TUESDAY_10 in {to_utc(slot.starts_at) for slot in video}
    candidate_id, chat_id = applicant(repo)
    taken = scheduling.book(repo, candidate_id, chat_id, slot_at(repo, TUESDAY_10).id, NOW)
    assert taken.recruiter_id == ana.id


def test_the_same_time_at_two_locations_and_one_per_slot_at_each(repo: Repo):
    first = book_onsite(repo, TUESDAY_10, GRACIA)
    second = book_onsite(repo, TUESDAY_10, SANTS)
    assert {first.interviewer_email, second.interviewer_email} == {
        "gerencia.es-bcn-gracia@gruposazon.example",
        "gerencia.es-bcn-sants@gruposazon.example",
    }
    with pytest.raises(SlotUnavailable):
        book_onsite(repo, TUESDAY_10, GRACIA)
    offered = scheduling.offer(repo, GRACIA, None, None, NOW, n=1000, mode=ONSITE)
    assert TUESDAY_10 not in {to_utc(slot.starts_at) for slot in offered}


def test_fifteen_a_day_fill_the_location(repo: Repo):
    for start in TUESDAY[:15]:
        book_onsite(repo, start)
    offered = scheduling.offer(repo, GRACIA, None, None, NOW, n=1000, mode=ONSITE)
    tuesday = [s for s in offered if s.starts_at.day == 20]
    assert tuesday == []
    with pytest.raises(SlotUnavailable):
        book_onsite(repo, TUESDAY[20])  # 18:40, free but the day is full
    book_onsite(repo, TUESDAY_10 + timedelta(days=1))


def test_concurrent_claims_never_go_over_the_slot_or_the_day(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'race.db'}")
    init_db(engine)
    repo = Repo(engine)
    for start in TUESDAY[:14]:
        book_onsite(repo, start)
    # One place left that day: six candidates race for two different free times.
    slots = [slot_at(repo, start) for start in TUESDAY[20:22]]
    applicants = [applicant(repo) for _ in range(6)]
    barrier = threading.Barrier(len(applicants))
    outcomes: list[str] = []

    def attempt(index: int, candidate_id: str, conversation_id: str) -> None:
        barrier.wait()
        slot = slots[index % 2]
        try:
            scheduling.book(repo, candidate_id, conversation_id, slot.id, NOW, mode=ONSITE)
            outcomes.append("booked")
        except SlotUnavailable:
            outcomes.append("rejected")

    threads = [
        threading.Thread(target=attempt, args=(index, *pair))
        for index, pair in enumerate(applicants)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["booked", *["rejected"] * 5]
    with repo.session() as session:
        rows = session.exec(select(Booking).where(Booking.status == BookingStatus.BOOKED)).all()
    assert len(rows) == 15


def test_with_google_in_person_needs_the_organizer_connected(repo: Repo):
    organizer = recruiter(repo, "Pablo", location_ids=[])
    calendar = FakeCalendar(connected_ids=set())
    config = SchedulingConfig(
        calendar=calendar, organizer_email=organizer.email, organizer_required=True
    )
    assert scheduling.available_modes(repo, GRACIA, config=config) == []
    assert scheduling.offer(repo, GRACIA, None, None, NOW, mode=ONSITE, config=config) == []
    with pytest.raises(SlotUnavailable):
        book_onsite(repo, config=config)
    unset = replace(config, organizer_email=None)
    assert scheduling.available_modes(repo, GRACIA, config=unset) == []

    calendar.connect(organizer.id)
    assert scheduling.available_modes(repo, GRACIA, config=config) == [InterviewMode.ONSITE]
    booking = book_onsite(repo, config=config)
    assert booking.recruiter_id == organizer.id
    recruiters.update(repo, organizer.id, NOW, active=False)
    assert scheduling.available_modes(repo, GRACIA, config=config) == []


def test_the_organizers_in_person_events_never_count_as_their_video_interviews(repo: Repo):
    organizer = recruiter(repo, "Pablo", max_interviews_per_day=1)
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar, organizer_email=organizer.email)
    zapopan = "mx-gdl-zapopan"
    mexico = datetime(2026, 10, 20, 16, tzinfo=UTC)  # 10:00 in Mexico City
    booking = book_onsite(repo, mexico, zapopan, config)

    [event] = calendar.live_events(organizer.id)
    assert booking.recruiter_id == organizer.id
    assert "con la gerencia del local" in event.request.description
    assert [guest.email for guest in event.request.guests] == [
        "gerencia.mx-gdl-zapopan@gruposazon.example"
    ]
    # Its calendar shows the event as busy, but it still has its one video interview that day.
    video = scheduling.offer(repo, GRACIA, None, None, NOW, n=50, config=config)
    assert any(slot.starts_at.day == 20 for slot in video)


def test_without_google_in_person_works_without_an_organizer(repo: Repo):
    config = SchedulingConfig(calendar=FakeCalendar())
    assert scheduling.available_modes(repo, GRACIA, config=config) == [InterviewMode.ONSITE]
    booking = book_onsite(repo, config=config)
    assert booking.recruiter_id is None
    assert booking.google_event_id is None


def test_a_location_without_a_manager_offers_no_in_person(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
):
    gracia = load_locations()[GRACIA]
    monkeypatch.setitem(load_locations(), GRACIA, replace(gracia, manager=None))
    assert scheduling.available_modes(repo, GRACIA) == []
    assert scheduling.offer(repo, GRACIA, None, None, NOW, mode=ONSITE) == []


def test_recruiters_run_video_up_to_fifteen_a_day_by_default(repo: Repo):
    ana = recruiter(repo, "Ana")
    assert ana.interview_modes == [InterviewMode.VIDEO]
    assert ana.max_interviews_per_day == 15
