"""Who gets each interview, and the events on their calendar (with the fake calendar)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from sazon import recruiters
from sazon.calendar.fake import FakeCalendar
from sazon.scheduling import service as scheduling
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
TUESDAY_10 = datetime(2026, 10, 20, 8, tzinfo=UTC)  # 10:00 in Madrid


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return Repo(engine)


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


def candidate(
    repo: Repo, email: str = "lucia@example.com", language: str = "es"
) -> tuple[str, str]:
    person = repo.save(
        Candidate(
            channel="web",
            email=email,
            language=language,
            profile={"full_name": "Lucía García"},
            location_id=GRACIA,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    chat = repo.save(
        Conversation(
            candidate_id=person.id,
            channel="web",
            state={},
            started_at=NOW,
            last_activity_at=NOW,
        )
    )
    return person.id, chat.id


def slot_at(repo: Repo, start: datetime) -> InterviewSlot:
    [slot] = scheduling.materialize(repo, GRACIA, [start])
    return slot


def book(
    repo: Repo,
    start: datetime = TUESDAY_10,
    config: SchedulingConfig | None = None,
    mode: InterviewMode = InterviewMode.VIDEO,
    **who: str,
) -> Booking:
    candidate_id, conversation_id = candidate(repo, **who)
    return scheduling.book(
        repo,
        candidate_id,
        conversation_id,
        slot_at(repo, start).id,
        NOW,
        mode=mode,
        config=config or SchedulingConfig(),
    )


def test_each_booking_goes_to_the_least_loaded_free_recruiter(repo: Repo):
    ana = recruiter(repo, "Ana")
    bea = recruiter(repo, "Bea")
    config = SchedulingConfig(buffer=timedelta(0))
    picks = [book(repo, TUESDAY_10 + timedelta(hours=h), config).recruiter_id for h in (0, 1)]
    assert picks == [ana.id, bea.id]
    # Ana and Bea have one each on Tuesday: the name breaks the tie.
    assert book(repo, TUESDAY_10 + timedelta(minutes=30), config).recruiter_id == ana.id
    # Next day: Bea has fewer interviews in all (1 against 2).
    assert book(repo, TUESDAY_10 + timedelta(days=1), config).recruiter_id == bea.id


def test_only_active_recruiters_covering_the_location_by_video_qualify(repo: Repo):
    recruiter(repo, "Ana", location_ids=["es-mad-centro"])
    recruiter(repo, "Bea", interview_modes=[InterviewMode.ONSITE])  # a row from before SAZ-64
    carla = recruiter(repo, "Carla")
    inactive = recruiter(repo, "Dora")
    recruiters.update(repo, inactive.id, NOW, active=False)
    assert book(repo).recruiter_id == carla.id
    with pytest.raises(SlotUnavailable):  # Carla is taken, nobody else does video there
        book(repo)


def test_offers_count_only_connected_readable_calendars(repo: Repo):
    ana = recruiter(repo, "Ana")
    bea = recruiter(repo, "Bea", working_hours=recruiters.weekday_hours([["16:00", "18:00"]]))
    cris = recruiter(repo, "Cris", working_hours=recruiters.weekday_hours([["12:00", "13:00"]]))
    calendar = FakeCalendar(connected_ids={ana.id, cris.id}, failing={cris.id})
    config = SchedulingConfig(calendar=calendar)

    offered = scheduling.offer(repo, GRACIA, None, None, NOW, n=50, config=config)
    hours = {scheduling.local_start(slot).hour for slot in offered}
    assert hours == {10, 11}  # Ana's; Bea is not connected and Cris's calendar fails
    assert bea.id not in calendar.connected([bea.id])


def test_a_busy_calendar_block_takes_the_recruiter_out(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()
    calendar.add_busy(ana.id, TUESDAY_10, TUESDAY_10 + timedelta(hours=1))
    config = SchedulingConfig(calendar=calendar)
    offered = scheduling.offer(repo, GRACIA, None, None, NOW, n=50, config=config)
    tuesday = [
        scheduling.local_start(s).strftime("%H:%M") for s in offered if s.starts_at.day == 20
    ]
    assert tuesday == ["11:15", "11:30", "11:45"]  # 10:00-11:00 busy; 11:00 is in the buffer


def test_booking_rechecks_the_calendars_and_falls_to_the_next_recruiter(repo: Repo):
    ana = recruiter(repo, "Ana")
    bea = recruiter(repo, "Bea")
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    slot = scheduling.offer(repo, GRACIA, None, None, NOW, config=config)[0]
    assert to_utc(slot.starts_at) == TUESDAY_10
    # Meanwhile Ana accepts a meeting at that time in her calendar.
    calendar.add_busy(ana.id, TUESDAY_10, TUESDAY_10 + timedelta(minutes=30))

    booking = book(repo, config=config)

    assert booking.recruiter_id == bea.id
    assert [e.recruiter_id for e in calendar.live_events()] == [bea.id]
    calendar.add_busy(bea.id, TUESDAY_10 + timedelta(minutes=30), TUESDAY_10 + timedelta(hours=1))
    with pytest.raises(SlotUnavailable):  # nobody left at 10:30: the engine offers others
        book(repo, TUESDAY_10 + timedelta(minutes=30), config=config, email="otra@example.com")


def test_two_candidates_one_free_recruiter(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    slot = scheduling.offer(repo, GRACIA, None, None, NOW, config=config)[0]
    first, first_chat = candidate(repo)
    second, second_chat = candidate(repo, "otra@example.com")

    won = scheduling.book(repo, first, first_chat, slot.id, NOW, config=config)
    with pytest.raises(SlotUnavailable):
        scheduling.book(repo, second, second_chat, slot.id, NOW, config=config)

    assert won.recruiter_id == ana.id
    assert len(calendar.live_events()) == 1
    assert scheduling.active_booking(repo, second) is None


def test_the_daily_maximum_is_a_hard_limit(repo: Repo):
    recruiter(repo, "Ana", max_interviews_per_day=1)
    book(repo)
    with pytest.raises(SlotUnavailable):
        book(repo, TUESDAY_10 + timedelta(hours=1))
    assert book(repo, TUESDAY_10 + timedelta(days=1)).recruiter_id is not None


def test_an_onsite_booking_goes_to_the_manager_never_a_recruiter(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()

    booking = book(repo, config=SchedulingConfig(calendar=calendar), mode=InterviewMode.ONSITE)

    assert booking.mode == InterviewMode.ONSITE
    assert booking.recruiter_id is None
    assert booking.interviewer_email == "gerencia.es-bcn-gracia@gruposazon.example"
    assert calendar.live_events(ana.id) == []
    # Ana is still free for a video interview at the same time.
    assert book(repo, config=SchedulingConfig(calendar=calendar)).recruiter_id == ana.id


def test_a_video_event_lasts_fifteen_minutes(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()

    booking = book(repo, config=SchedulingConfig(calendar=calendar))

    [event] = calendar.live_events(ana.id)
    assert booking.google_event_id == event.event.event_id == booking.id
    assert booking.google_calendar_id == "primary"
    request = event.request
    assert request.booking_id == booking.id
    assert (request.start, request.end) == (TUESDAY_10, TUESDAY_10 + timedelta(minutes=15))
    assert request.timezone == "Europe/Madrid"
    assert (request.attendee_email, request.attendee_name) == ("lucia@example.com", "Lucía García")
    assert request.summary == "Entrevista Grupo Sazón: Lucía García"
    assert "Entrevista de 15 minutos" in request.description


def test_a_video_booking_gets_a_meet_link_in_the_candidates_language(repo: Repo):
    recruiter(repo, "Ana", interview_modes=[InterviewMode.VIDEO])
    calendar = FakeCalendar()

    booking = book(
        repo, config=SchedulingConfig(calendar=calendar), mode=InterviewMode.VIDEO, language="en"
    )

    [event] = calendar.live_events()
    assert booking.mode == InterviewMode.VIDEO
    assert booking.meet_url is not None
    assert booking.meet_url.startswith("https://meet.google.com/")
    assert event.request.video
    assert event.request.location is None
    assert event.request.summary == "Grupo Sazón interview: Lucía García"
    assert "Google Meet link" in event.request.description


def test_booking_again_is_idempotent_and_creates_one_event(repo: Repo):
    recruiter(repo, "Ana")
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    candidate_id, chat_id = candidate(repo)
    slot = slot_at(repo, TUESDAY_10)

    first = scheduling.book(repo, candidate_id, chat_id, slot.id, NOW, config=config)
    again = scheduling.book(repo, candidate_id, chat_id, slot.id, NOW, config=config)

    assert again.id == first.id
    assert calendar.calls.count("create") == 1
    assert len(calendar.live_events()) == 1


def test_a_failed_event_keeps_the_booking_and_is_retried(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    slot = scheduling.offer(repo, GRACIA, None, None, NOW, config=config)[0]
    candidate_id, chat_id = candidate(repo)
    calendar.failing_writes.add(ana.id)  # free/busy answers, the insert fails

    booking = scheduling.book(repo, candidate_id, chat_id, slot.id, NOW, config=config)

    assert booking.status == BookingStatus.BOOKED
    assert booking.google_event_id is None
    calendar.failing_writes.clear()
    assert scheduling.ensure_events(repo, NOW, config) == 1
    assert scheduling.active_booking(repo, candidate_id).google_event_id == booking.id  # pyright: ignore[reportOptionalMemberAccess]


def test_rescheduling_with_the_same_recruiter_moves_the_event(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    original = book(repo, config=config)
    later = slot_at(repo, TUESDAY_10 + timedelta(hours=1))

    moved = scheduling.reschedule(repo, original.id, later.id, NOW, config=config)

    assert moved.recruiter_id == ana.id
    assert calendar.calls.count("update") == 1
    [event] = calendar.live_events()
    assert moved.google_event_id == original.google_event_id == event.event.event_id
    assert event.request.start == TUESDAY_10 + timedelta(hours=1)
    assert event.request.booking_id == moved.id
    with repo.session() as session:
        old = session.get(Booking, original.id)
    assert old is not None
    assert old.status == BookingStatus.RESCHEDULED
    assert old.google_event_id is None


def test_rescheduling_to_another_recruiter_deletes_and_creates(repo: Repo):
    ana = recruiter(repo, "Ana")
    bea = recruiter(repo, "Bea", working_hours=recruiters.weekday_hours([["16:00", "18:00"]]))
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    original = book(repo, config=config)
    afternoon = slot_at(repo, TUESDAY_10 + timedelta(hours=6))  # 16:00: only Bea works then

    moved = scheduling.reschedule(repo, original.id, afternoon.id, NOW, config=config)

    assert (original.recruiter_id, moved.recruiter_id) == (ana.id, bea.id)
    assert [e.recruiter_id for e in calendar.live_events()] == [bea.id]
    assert calendar.calls.count("delete") == 1


def test_cancel_deletes_the_event(repo: Repo):
    recruiter(repo, "Ana")
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    booking = book(repo, config=config)

    cancelled = scheduling.cancel(repo, booking.id, NOW, config=config)
    scheduling.cancel(repo, booking.id, NOW, config=config)

    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.google_event_id is None
    assert calendar.live_events() == []
    assert calendar.calls.count("delete") == 1


def test_a_refused_delete_keeps_the_event_id_and_is_retried(repo: Repo):
    ana = recruiter(repo, "Ana")
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    booking = book(repo, config=config)
    calendar.failing_writes.add(ana.id)

    cancelled = scheduling.cancel(repo, booking.id, NOW, config=config)

    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.google_event_id == booking.google_event_id
    assert len(calendar.live_events()) == 1
    assert scheduling.delete_stale_events(repo, config) == 0  # still refused
    calendar.failing_writes.clear()
    assert scheduling.delete_stale_events(repo, config) == 1
    assert calendar.live_events() == []
    assert calendar.calls[-1] == "delete-quiet"  # the candidate was told at the cancel
    with repo.session() as session:
        stored = session.get(Booking, booking.id)
    assert stored is not None
    assert stored.google_event_id is None
    assert scheduling.delete_stale_events(repo, config) == 0


def test_rescheduling_to_another_recruiter_keeps_an_old_event_it_could_not_delete(repo: Repo):
    ana = recruiter(repo, "Ana")
    recruiter(repo, "Bea", working_hours=recruiters.weekday_hours([["16:00", "18:00"]]))
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    original = book(repo, config=config)
    afternoon = slot_at(repo, TUESDAY_10 + timedelta(hours=6))
    calendar.failing_writes.add(ana.id)

    moved = scheduling.reschedule(repo, original.id, afternoon.id, NOW, config=config)

    assert moved.google_event_id is not None  # Bea's calendar took the new one
    with repo.session() as session:
        old = session.get(Booking, original.id)
    assert old is not None
    assert old.google_event_id == original.google_event_id
    calendar.failing_writes.clear()
    assert scheduling.delete_stale_events(repo, config) == 1
    assert [e.event.event_id for e in calendar.live_events()] == [moved.google_event_id]


def test_video_bookings_without_a_recruiter_are_assigned_later(repo: Repo):
    first, second = slot_at(repo, TUESDAY_10), slot_at(repo, TUESDAY_10 + timedelta(hours=1))
    for slot, mode in ((first, InterviewMode.VIDEO), (second, InterviewMode.VIDEO), (first, None)):
        candidate_id, chat_id = candidate(repo)
        repo.save(
            Booking(
                candidate_id=candidate_id,
                conversation_id=chat_id,
                slot_id=slot.id,
                mode=mode,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    ana = recruiter(repo, "Ana")
    bea = recruiter(repo, "Bea")
    assert scheduling.assign_unassigned(repo) == 2
    with repo.session() as session:
        assigned = {b.mode: b.recruiter_id for b in session.exec(select(Booking))}
    # The one without a mode is in person, the manager's: it needs no recruiter.
    assert assigned[None] is None
    with repo.session() as session:
        video = [b.recruiter_id for b in session.exec(select(Booking)) if b.mode]
    assert set(video) == {ana.id, bea.id}


def test_a_moved_booking_keeps_its_recruiter_while_they_are_free(repo: Repo):
    ana = recruiter(repo, "Ana")
    bea = recruiter(repo, "Bea")
    config = SchedulingConfig(buffer=timedelta(0))
    book(repo, config=config)  # Ana takes Tuesday 10:00 (name order)
    second = book(repo, config=config)  # Bea is the one free then
    assert second.recruiter_id == bea.id
    wednesday = TUESDAY_10 + timedelta(days=1)
    other, other_chat = candidate(repo, "otra@example.com")
    repo.save(  # Bea already has an interview on Wednesday: by load, Ana would get the move
        Booking(
            candidate_id=other,
            conversation_id=other_chat,
            slot_id=slot_at(repo, wednesday + timedelta(hours=1)).id,
            recruiter_id=bea.id,
            mode=InterviewMode.VIDEO,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    moved = scheduling.reschedule(repo, second.id, slot_at(repo, wednesday).id, NOW, config=config)

    assert moved.recruiter_id == bea.id
    assert ana.id != bea.id
