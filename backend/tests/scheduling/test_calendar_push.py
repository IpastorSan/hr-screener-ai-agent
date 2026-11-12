"""SAZ-40: Google Calendar push notifications and the two-way booking sync, against the
in-memory calendar standing in for Google (`FakeCalendar`: a change log, sync tokens, push
channels, and what recruiters and candidates do in Google Calendar)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from sazon import metrics, recruiters
from sazon.api.app import create_app
from sazon.calendar import Attendee, Changes, EventState, Interval
from sazon.calendar.fake import FakeCalendar
from sazon.cli import main
from sazon.config import get_settings
from sazon.engine import ConversationContext, ConversationState, awaits_reoffer, reoffer
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    InterviewMode,
    Language,
    MatrixLanguage,
    Outcome,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.slots import Booked
from sazon.engine.state import Closed, Interview, Offering, Withdrawn
from sazon.engine.ui import Slots
from sazon.llm import FakeLLM
from sazon.llm.fallback_templates import OFFER_SLOTS_VIDEO
from sazon.scheduling import push
from sazon.scheduling.availability import Interviewer, MoveIssue, Rules, move_issue
from sazon.scheduling.push import CalendarPush, Change, classify
from sazon.scheduling.service import SchedulingConfig, get_slot, materialize, slot_view
from sazon.service import ScreeningService, WidgetAnswer
from sazon.storage.models import (
    Booking,
    BookingStatus,
    CalendarChannel,
    Candidate,
    Conversation,
    GoogleCredential,
    Recruiter,
    TurnRole,
)
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # Monday
TUESDAY_10 = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)  # 10:00 in Madrid
CONTEXT = ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES")
ADDRESS = "https://sazon.example.com/api/webhooks/google/calendar"
WEEK = timedelta(days=7)
EMAIL = "lucia@example.com"


def ex(intent: Intent = Intent.ANSWER, slot: str | None = None, **fields: Any) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={name: FieldCandidate(value=v, confidence=0.95) for name, v in fields.items()},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        slot=slot,
    )


SCREENING = [
    ex(Intent.CONFIRM),
    ex(full_name="Lucía García", phone="612345678", email=EMAIL),
    ex(has_license=True),
    ex(city="Gràcia"),
    ex(availability=["part_time"]),
    ex(schedule="morning"),
    ex(experience_years=0),
    ex(start_date="2026-10-26"),
    ex(Intent.CONFIRM),
]


def interviewer(
    repo: Repo, name: str, modes: tuple[InterviewMode, ...] = (InterviewMode.VIDEO,)
) -> Recruiter:
    """At Gràcia, 10:00-12:00 and 16:00-18:00 Madrid time on weekdays, calendar connected."""
    recruiter = recruiters.create(
        repo,
        NOW,
        name=name,
        email=f"{name.lower()}@gruposazon.example",
        country="ES",
        location_ids=["es-bcn-gracia"],
        interview_modes=modes,
        working_hours=recruiters.weekday_hours([["10:00", "12:00"], ["16:00", "18:00"]]),
    )
    repo.save(
        GoogleCredential(
            recruiter_id=recruiter.id,
            google_email=recruiter.email,
            google_subject=f"sub-{name}",
            refresh_token_encrypted="unused-by-the-fake",
            connected_at=NOW,
            updated_at=NOW,
        )
    )
    return recruiter


class World:
    """A candidate booked by video on Tuesday at 10:00 with Jordi; Marta is free then too."""

    def __init__(self, repo: Repo, calendar: FakeCalendar, service: ScreeningService) -> None:
        self.repo = repo
        self.calendar = calendar
        self.service = service
        self.push = CalendarPush(service, calendar, address=ADDRESS, ttl=WEEK)
        self.conversation_id = ""

    @property
    def jordi(self) -> str:
        return self._recruiter("Jordi")

    @property
    def marta(self) -> str:
        return self._recruiter("Marta")

    @property
    def pablo(self) -> str:
        return self._recruiter("Pablo")

    def _recruiter(self, name: str) -> str:
        found = recruiters.by_email(self.repo, f"{name.lower()}@gruposazon.example")
        assert found is not None
        return found.id

    def booking(self) -> Booking:
        with self.repo.session() as session:
            rows = session.exec(select(Booking).order_by(Booking.created_at)).all()  # pyright: ignore[reportArgumentType]
        return rows[-1]

    def get(self, booking_id: str) -> Booking:
        with self.repo.session() as session:
            booking = session.get(Booking, booking_id)
        assert booking is not None
        return booking

    def bookings(self) -> list[Booking]:
        with self.repo.session() as session:
            return list(session.exec(select(Booking)).all())

    def state(self) -> ConversationState:
        conversation = self.repo.get_conversation(self.conversation_id)
        assert conversation is not None
        return ConversationState.model_validate(conversation.state)

    def notices(self) -> list[str]:
        return [
            turn.text or ""
            for turn in self.repo.turns(self.conversation_id)
            if turn.action and turn.action.get("kind") == "calendar_update"
        ]

    def start(self) -> None:
        report = push.renew(self.repo, self.calendar, ADDRESS, NOW, WEEK)
        assert report.started == len(push.connected_recruiters(self.repo))

    async def sync(self, recruiter_id: str | None = None) -> int:
        return await self.push.sync(recruiter_id or self.jordi)


@pytest.fixture
async def world() -> World:
    return await booked_world()


async def booked_world(mode: InterviewMode = InterviewMode.VIDEO) -> World:
    """In person: Pablo is the organizer, whose calendar holds the event (SAZ-65)."""
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    interviewer(repo, "Jordi")
    interviewer(repo, "Marta")
    organizer = interviewer(repo, "Pablo")
    recruiters.update(repo, organizer.id, NOW, location_ids=[])
    calendar = FakeCalendar(clock=lambda: NOW)
    config = SchedulingConfig(
        calendar=calendar, organizer_email=organizer.email, organizer_required=True
    )
    llm = FakeLLM()
    llm.queue(*SCREENING)
    service = ScreeningService(llm, repo, clock=lambda: NOW, scheduling_config=config)
    world = World(repo, calendar, service)
    world.start()
    reply = await service.start(context=CONTEXT)
    for _ in range(len(SCREENING)):
        reply = await service.handle_turn(reply.conversation_id, "...")
    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value=mode.value))
    assert isinstance(reply.ui, Slots)
    first = reply.ui.slots[0]
    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value=first.id))
    assert isinstance(reply.ui, Booked)
    world.conversation_id = reply.conversation_id
    booking = world.booking()
    host = world.jordi if mode == InterviewMode.VIDEO else world.pablo
    assert booking.recruiter_id == host
    assert booking.google_event_id == booking.id
    assert await world.sync(host) == 0  # our own event: nothing to reconcile
    return world


def counter(kind: str) -> float:
    return metrics.CALENDAR_RECONCILIATIONS.labels(kind)._value.get()


# --- channels ---


def test_start_opens_a_channel_per_connected_calendar_with_the_token_hashed():
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    jordi = interviewer(repo, "Jordi")
    calendar = FakeCalendar(clock=lambda: NOW)

    report = push.renew(repo, calendar, ADDRESS, NOW, WEEK)

    assert report.started == 1
    [channel] = push.channels_of(repo)
    opened = calendar.channels[channel.id]
    assert opened.address == ADDRESS
    assert opened.recruiter_id == jordi.id
    assert channel.token_hash == push.hash_token(opened.token)
    assert opened.token not in channel.token_hash
    assert channel.resource_id == opened.resource_id
    assert channel.expires_at == NOW + WEEK
    assert channel.sync_token == "fake-0"
    assert push.verify(repo, channel.id, opened.token, opened.resource_id) == channel
    assert push.verify(repo, channel.id, "wrong", opened.resource_id) is None
    assert push.verify(repo, channel.id, opened.token, "other-resource") is None
    assert push.verify(repo, "unknown", opened.token, opened.resource_id) is None
    assert push.verify(repo, channel.id, "", opened.resource_id) is None
    # A forged header with a non-ASCII character is refused, not a TypeError (and a 500).
    assert push.verify(repo, channel.id, opened.token, "résource") is None


def test_renew_replaces_only_channels_expiring_within_48_hours():
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    interviewer(repo, "Jordi")
    calendar = FakeCalendar(clock=lambda: NOW)
    push.renew(repo, calendar, ADDRESS, NOW, WEEK)
    [first] = push.channels_of(repo)
    push.save_sync_token(repo, first.recruiter_id, "fake-7")

    later = NOW + timedelta(days=4)  # three days left: kept
    assert push.renew(repo, calendar, ADDRESS, later, WEEK).renewed == 0
    assert [c.id for c in push.channels_of(repo)] == [first.id]

    later = NOW + timedelta(days=5, hours=1)  # under 48 hours left: replaced
    report = push.renew(repo, calendar, ADDRESS, later, WEEK)

    assert report.renewed == 1
    [second] = push.channels_of(repo)
    assert second.id != first.id
    assert second.sync_token == "fake-7"  # the calendar's place carries over
    assert second.expires_at == NOW + WEEK  # the fake's clock did not move
    assert set(calendar.channels) == {second.id}  # the old one was stopped at Google


def test_a_failed_renewal_keeps_the_old_channel_and_is_counted():
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    jordi = interviewer(repo, "Jordi")
    calendar = FakeCalendar(clock=lambda: NOW)
    push.renew(repo, calendar, ADDRESS, NOW, WEEK)
    calendar.failing_watch.add(jordi.id)
    before = metrics.CALENDAR_WATCH_FAILURES.labels("renew")._value.get()

    report = push.renew(repo, calendar, ADDRESS, NOW + timedelta(days=6), WEEK)

    assert (report.renewed, report.failed) == (0, 1)
    assert len(push.channels_of(repo)) == 1
    assert metrics.CALENDAR_WATCH_FAILURES.labels("renew")._value.get() == before + 1
    health = push.channel_health(repo, NOW + timedelta(days=6, hours=1))
    assert health == {"ok": 0, "expiring": 1, "missing": 0}


def test_a_disconnected_calendar_loses_its_channels_and_health_counts_missing():
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    jordi = interviewer(repo, "Jordi")
    marta = interviewer(repo, "Marta")
    calendar = FakeCalendar(clock=lambda: NOW)
    calendar.failing_watch.add(marta.id)
    push.renew(repo, calendar, ADDRESS, NOW, WEEK)
    assert push.channel_health(repo, NOW) == {"ok": 1, "expiring": 0, "missing": 1}

    with repo.session() as session:
        credential = session.get(GoogleCredential, jordi.id)
        session.delete(credential)
        session.commit()
    report = push.renew(repo, calendar, ADDRESS, NOW, WEEK)

    assert report.stopped == 1
    assert push.channels_of(repo) == []
    assert calendar.channels == {}


async def test_disconnecting_stops_the_channels(world: World):
    stopped = world.push.stop(world.jordi)
    assert stopped == 1
    assert {c.recruiter_id for c in push.channels_of(world.repo)} == {world.marta, world.pablo}
    assert world.jordi not in {c.recruiter_id for c in world.calendar.channels.values()}


async def test_connecting_opens_a_fresh_channel_in_the_background(world: World):
    [old] = push.channels_of(world.repo, world.jordi)
    world.push.start_in_background(world.jordi)
    await world.push.drain()
    [new] = push.channels_of(world.repo, world.jordi)
    assert new.id != old.id
    assert new.sync_token == old.sync_token


# --- reconciliation rules ---


def test_classify_puts_the_most_decisive_change_first():
    owner_no = Attendee("jordi@x", "declined", owner=True)
    guest_no = Attendee(EMAIL, "declined")
    at = TUESDAY_10
    moved = EventState("e", False, start=at + timedelta(hours=1), attendees=(owner_no,))
    assert classify(EventState("e", True), at, EMAIL) == Change.CANCELLED
    assert classify(moved, at, EMAIL) == Change.RECRUITER_DECLINED
    assert classify(EventState("e", False, start=at, attendees=(guest_no,)), at, EMAIL) == (
        Change.CANDIDATE_DECLINED
    )
    other_guest = Attendee("someone@else", "declined")
    assert classify(EventState("e", False, start=at, attendees=(other_guest,)), at, EMAIL) == (
        Change.NONE
    )
    assert classify(EventState("e", False, start=at, attendees=(guest_no,)), at, None) == (
        Change.NONE
    )
    assert classify(EventState("e", False, start=at + timedelta(minutes=30)), at, EMAIL) == (
        Change.MOVED
    )
    assert classify(EventState("e", False, start=None), at, EMAIL) == Change.MOVED
    assert classify(EventState("e", False, start=at), at, EMAIL) == Change.NONE


def jordi_at(*booked: datetime, busy: tuple = (), max_per_day: int = 15) -> Interviewer:
    return Interviewer(
        id="jordi",
        name="Jordi",
        timezone="Europe/Madrid",
        working_hours=recruiters.weekday_hours([["10:00", "12:00"], ["16:00", "18:00"]]),
        max_per_day=max_per_day,
        booked=booked,
        busy=busy,
    )


def test_move_issue_names_the_rule_a_moved_time_breaks():
    rules = Rules(buffer=timedelta(minutes=10))
    at = TUESDAY_10 + timedelta(hours=1)  # 11:00 Madrid
    assert move_issue(jordi_at(), at, rules, NOW) is None
    assert move_issue(jordi_at(), NOW - timedelta(hours=1), rules, NOW) == MoveIssue.PAST
    assert move_issue(jordi_at(), at, rules, at) == MoveIssue.PAST
    late = TUESDAY_10 + timedelta(hours=1, minutes=50)  # 11:50, ends past 12:00
    assert move_issue(jordi_at(), late, rules, NOW) == MoveIssue.OUTSIDE_HOURS
    assert move_issue(jordi_at(), TUESDAY_10 - timedelta(hours=1), rules, NOW) == (
        MoveIssue.OUTSIDE_HOURS
    )
    clash = jordi_at(at + timedelta(minutes=20))  # 11:20 booked: inside the buffer
    assert move_issue(clash, at, rules, NOW) == MoveIssue.CLASH
    own = Interval(at, at + timedelta(minutes=30))
    assert move_issue(jordi_at(busy=(own,)), at, rules, NOW, own) is None  # itself
    merged = Interval(at - timedelta(minutes=30), at + timedelta(minutes=30))  # 10:30 meeting
    assert move_issue(jordi_at(busy=(merged,)), at, rules, NOW, own) == MoveIssue.BUSY
    assert move_issue(jordi_at(busy=(own,)), at, rules, NOW) == MoveIssue.BUSY
    full = jordi_at(TUESDAY_10, max_per_day=1)
    assert move_issue(full, at + timedelta(hours=5), rules, NOW) == MoveIssue.DAILY_MAX


def test_a_reoffer_is_pending_only_without_a_booking_or_times_on_offer(world: World):
    booked = world.state()
    assert booked.booking is not None
    moving = booked.at(Interview(sub=Offering(booking=booked.booking)))  # asked to move it
    assert not awaits_reoffer(moving)
    cancelled, _ = reoffer(booked)
    assert awaits_reoffer(cancelled)
    slot = get_slot(world.repo, world.booking().slot_id)
    assert slot is not None
    offered = (slot_view(slot, Language.ES),)
    assert not awaits_reoffer(cancelled.at(Interview(sub=Offering(slots=offered))))
    assert not awaits_reoffer(cancelled.at(Closed(variant=Withdrawn())))


# --- reconciliation against the calendar ---


async def test_a_move_inside_working_hours_moves_the_booking_and_tells_the_candidate(
    world: World,
):
    booking = world.booking()
    before = counter("moved")
    eleven = TUESDAY_10 + timedelta(hours=1)

    [before_sync] = push.channels_of(world.repo, world.jordi)
    world.calendar.recruiter_moves(booking.id, eleven)
    assert await world.sync() == 1

    [after_sync] = push.channels_of(world.repo, world.jordi)
    assert after_sync.sync_token != before_sync.sync_token  # the next sync starts after this
    moved = world.booking()
    assert moved.id == booking.id  # the same booking, so the event's marker still matches
    slot = get_slot(world.repo, moved.slot_id)
    assert slot is not None
    assert slot.starts_at.replace(tzinfo=UTC) == eleven
    assert moved.calendar_issue is None
    state = world.state()
    assert state.booking is not None
    assert state.booking.slot_id == moved.slot_id
    assert "a las 11:00" in state.booking.label
    [notice] = world.notices()
    assert notice == push.MOVED_VIDEO_NOTICE["es-ES"].format(
        when="martes 20 de octubre a las 11:00"
    )
    assert counter("moved") == before + 1
    assert "update" not in world.calendar.calls[-3:]  # the event is left as the recruiter put it


async def test_a_moved_video_interview_keeps_its_meet_link_in_the_chat(world: World):
    booking = world.booking()
    assert booking.mode == InterviewMode.VIDEO
    assert booking.meet_url

    world.calendar.recruiter_moves(booking.id, TUESDAY_10 + timedelta(hours=1))
    assert await world.sync() == 1

    state = world.state()
    assert state.booking is not None
    assert "a las 11:00" in state.booking.label
    assert state.booking.mode == InterviewMode.VIDEO
    assert state.booking.meet_url == booking.meet_url
    turn = world.repo.turns(world.conversation_id)[-1]  # the notice, with the card
    assert turn.ui is not None
    assert turn.ui["meetUrl"] == booking.meet_url
    [notice] = world.notices()
    assert notice == (
        "El equipo de selección ha cambiado tu videollamada: "
        "ahora es el martes 20 de octubre a las 11:00."
    )


MANAGER = "gerencia.es-bcn-gracia@gruposazon.example"


@pytest.fixture
async def in_person() -> World:
    return await booked_world(InterviewMode.ONSITE)


async def test_an_in_person_interview_is_on_the_organizers_calendar_with_the_manager(
    in_person: World,
):
    booking = in_person.booking()
    assert booking.mode == InterviewMode.ONSITE
    assert booking.interviewer_email == MANAGER
    [event] = in_person.calendar.live_events()
    assert event.recruiter_id == in_person.pablo
    assert event.request.attendee_email == EMAIL
    assert [(g.email, g.name) for g in event.request.guests] == [(MANAGER, "Marc Soler")]
    assert event.request.location == "Carrer de Verdi 45, 08012 Barcelona"
    assert "con la persona encargada del local" in event.request.description
    assert event.request.end - event.request.start == timedelta(minutes=15)


async def test_a_manager_decline_cancels_and_offers_new_times(in_person: World):
    booking = in_person.booking()
    before = counter("manager_declined")

    in_person.calendar.respond(booking.id, "declined", owner=False, email=MANAGER)
    assert await in_person.sync(in_person.pablo) == 1

    cancelled = in_person.booking()
    assert cancelled.id == booking.id
    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.cancelled_by == "manager"
    assert in_person.calendar.live_events() == []
    assert "delete" in in_person.calendar.calls  # Google tells the candidate
    assert [b for b in in_person.bookings() if b.status == BookingStatus.BOOKED] == []
    state = in_person.state()
    assert state.booking is None
    assert state.stage == Stage.SCHEDULING
    [notice] = in_person.notices()
    assert notice == push.MANAGER_DECLINED_NOTICE["es-ES"].format(
        location="Barcelona Gràcia", when="martes 20 de octubre a las 10:00"
    )
    assert counter("manager_declined") == before + 1
    assert await in_person.service.reoffer_if_pending(in_person.conversation_id)


def test_classify_tells_the_manager_from_the_candidate():
    manager_no = Attendee(MANAGER, "declined")
    candidate_no = Attendee(EMAIL, "declined")
    at = TUESDAY_10
    both = EventState("e", False, start=at, attendees=(candidate_no, manager_no))
    assert classify(both, at, EMAIL, MANAGER) == Change.MANAGER_DECLINED
    alone = EventState("e", False, start=at, attendees=(manager_no,))
    assert classify(alone, at, EMAIL, MANAGER) == Change.MANAGER_DECLINED
    assert classify(alone, at, EMAIL) == Change.NONE  # a video interview has no manager


async def test_a_moved_onsite_interview_still_names_the_location(in_person: World):
    booking = in_person.booking()
    in_person.calendar.recruiter_moves(booking.id, TUESDAY_10 + timedelta(hours=1))
    await in_person.sync(in_person.pablo)

    state = in_person.state()
    assert state.booking is not None
    assert state.booking.mode == InterviewMode.ONSITE
    assert state.booking.meet_url is None
    [notice] = in_person.notices()
    assert notice == push.MOVED_NOTICE["es-ES"].format(
        when="martes 20 de octubre a las 11:00", location="Barcelona Gràcia"
    )


async def test_a_moved_onsite_interview_is_checked_against_the_locations_hours(
    in_person: World,
):
    booking = in_person.booking()
    seven = TUESDAY_10 + timedelta(hours=9)  # 19:00: the location's hours, not Pablo's
    in_person.calendar.recruiter_moves(booking.id, seven)
    await in_person.sync(in_person.pablo)
    assert in_person.booking().calendar_issue is None

    in_person.calendar.recruiter_moves(booking.id, TUESDAY_10 + timedelta(hours=10))  # 20:00
    await in_person.sync(in_person.pablo)
    assert in_person.booking().calendar_issue == MoveIssue.OUTSIDE_HOURS


async def test_an_organizer_decline_of_an_in_person_interview_cancels_it(in_person: World):
    booking = in_person.booking()
    in_person.calendar.respond(booking.id, "declined", owner=True)
    await in_person.sync(in_person.pablo)

    cancelled = in_person.booking()
    assert cancelled.status == BookingStatus.CANCELLED  # nobody else takes the manager's place
    assert cancelled.cancelled_by == "recruiter"
    assert in_person.state().stage == Stage.SCHEDULING


async def test_a_move_on_a_voice_application_is_told_in_words(world: World):
    """The notice is what a call says next (its opening line): the time in words (SAZ-48), the
    day of the month in digits (SAZ-50). The booking card keeps the digits."""
    conversation = world.repo.get_conversation(world.conversation_id)
    assert conversation is not None
    conversation.channel = "voice"
    world.repo.save(conversation)
    booking = world.booking()
    world.calendar.recruiter_moves(booking.id, TUESDAY_10 + timedelta(hours=1))
    await world.sync()

    [notice] = world.notices()
    assert notice == (
        "El equipo de selección ha cambiado tu videollamada: ahora es el martes 20 a las "
        "once de la mañana."
    )
    state = world.state()
    assert state.booking is not None
    assert "a las 11:00" in state.booking.label


async def test_a_move_outside_working_hours_is_followed_but_flagged(world: World):
    booking = world.booking()
    before = counter("moved_flagged")
    evening = TUESDAY_10 + timedelta(hours=11)  # 21:00 in Madrid

    world.calendar.recruiter_moves(booking.id, evening)
    await world.sync()

    flagged = world.booking()
    assert flagged.calendar_issue == MoveIssue.OUTSIDE_HOURS
    slot = get_slot(world.repo, flagged.slot_id)
    assert slot is not None
    assert slot.starts_at.replace(tzinfo=UTC) == evening
    assert counter("moved_flagged") == before + 1

    world.calendar.recruiter_moves(booking.id, TUESDAY_10 + timedelta(hours=8))  # 18:00 → no
    world.calendar.recruiter_moves(booking.id, TUESDAY_10 + timedelta(hours=6))  # 16:00
    await world.sync()
    assert world.booking().calendar_issue is None
    assert len(world.notices()) == 2  # two syncs, two moves: the intermediate one never seen


async def test_a_move_over_another_interview_is_flagged_as_a_clash(world: World):
    booking = world.booking()
    four = TUESDAY_10 + timedelta(hours=6)  # Jordi interviews someone else at 16:00
    other = world.repo.save(Candidate(channel="web", created_at=NOW, updated_at=NOW))
    chat = world.repo.save(
        Conversation(
            channel="web",
            state={},
            candidate_id=other.id,
            started_at=NOW,
            last_activity_at=NOW,
        )
    )
    slot = materialize(world.repo, "es-bcn-gracia", [four])[0]
    world.repo.save(
        Booking(
            candidate_id=other.id,
            conversation_id=chat.id,
            slot_id=slot.id,
            recruiter_id=world.jordi,
            mode=InterviewMode.VIDEO,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    world.calendar.recruiter_moves(booking.id, four + timedelta(minutes=20))
    await world.sync()

    assert world.get(booking.id).calendar_issue == MoveIssue.CLASH


async def test_an_all_day_event_is_flagged_once_and_keeps_the_time(world: World):
    booking = world.booking()
    all_day = EventState(event_id=booking.id, cancelled=False, booking_id=booking.id)

    assert await world.push.reconcile(world.jordi, all_day) == 1
    assert await world.push.reconcile(world.jordi, all_day) == 0

    flagged = world.get(booking.id)
    assert flagged.calendar_issue == "all_day"
    assert flagged.slot_id == booking.slot_id


async def test_a_deleted_event_cancels_and_the_next_visit_offers_new_times(world: World):
    booking = world.booking()
    before = counter("cancelled")

    world.calendar.recruiter_deletes(booking.id)
    assert await world.sync() == 1

    cancelled = world.booking()
    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.cancelled_by == "recruiter"
    assert cancelled.google_event_id is None
    state = world.state()
    assert state.booking is None
    assert state.stage == Stage.SCHEDULING
    assert state.outcome == Outcome.QUALIFIED
    [notice] = world.notices()
    assert notice == push.CANCELLED_NOTICE["es-ES"].format(when="martes 20 de octubre a las 10:00")
    assert counter("cancelled") == before + 1

    assert await world.service.reoffer_if_pending(world.conversation_id)
    assert not await world.service.reoffer_if_pending(world.conversation_id)
    last = world.repo.turns(world.conversation_id)[-1]
    assert last.role == TurnRole.AGENT
    assert last.ui is not None
    assert last.ui["kind"] == "slots"
    assert last.text is not None
    assert last.text.startswith(OFFER_SLOTS_VIDEO["es-ES"].split("{slots}")[0])
    assert world.state().offered_slots


async def test_the_candidate_picks_a_new_time_after_a_cancellation(world: World):
    world.calendar.recruiter_deletes(world.booking().id)
    await world.sync()
    await world.service.reoffer_if_pending(world.conversation_id)
    offered = world.state().offered_slots

    reply = await world.service.handle_turn(
        world.conversation_id, WidgetAnswer(value=offered[0].id)
    )

    assert isinstance(reply.ui, Booked)
    active = [b for b in world.bookings() if b.status == BookingStatus.BOOKED]
    assert len(active) == 1
    assert active[0].google_event_id is not None


async def test_a_recruiter_decline_goes_to_another_free_recruiter(world: World):
    booking = world.booking()
    before = counter("reassigned")

    world.calendar.respond(booking.id, "declined", owner=True)
    assert await world.sync() == 1

    old = next(b for b in world.bookings() if b.id == booking.id)
    assert old.status == BookingStatus.RESCHEDULED
    new = next(b for b in world.bookings() if b.status == BookingStatus.BOOKED)
    assert new.recruiter_id == world.marta
    assert new.slot_id == booking.slot_id
    assert new.google_event_id == new.id
    assert [e.recruiter_id for e in world.calendar.live_events()] == [world.marta]
    assert world.state().booking is not None
    [notice] = world.notices()
    assert notice == push.REASSIGNED_NOTICE["es-ES"].format(when="martes 20 de octubre a las 10:00")
    assert counter("reassigned") == before + 1
    # The old event's deletion comes back as a notification on Jordi's calendar: nothing to do.
    assert await world.sync(world.jordi) == 0
    assert await world.sync(world.marta) == 0


async def test_a_recruiter_decline_with_nobody_free_cancels_and_reoffers(world: World):
    booking = world.booking()
    world.calendar.add_busy(world.marta, TUESDAY_10, TUESDAY_10 + timedelta(hours=1))
    before = counter("recruiter_declined")

    world.calendar.respond(booking.id, "declined", owner=True)
    await world.sync()

    cancelled = world.booking()
    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.cancelled_by == "recruiter"
    assert world.calendar.live_events() == []
    assert "delete" in world.calendar.calls  # the candidate is told by Google
    assert world.state().stage == Stage.SCHEDULING
    assert counter("recruiter_declined") == before + 1


async def test_the_candidate_declining_the_invite_cancels_quietly(world: World):
    booking = world.booking()
    before = counter("candidate_declined")

    world.calendar.respond(booking.id, "declined", owner=False)
    await world.sync()

    cancelled = world.booking()
    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.cancelled_by == "candidate"
    assert "delete-quiet" in world.calendar.calls
    assert world.calendar.live_events() == []
    [notice] = world.notices()
    assert notice.startswith(push.DECLINED_NOTICE["es-ES"].split("{when}")[0])
    assert world.state().booking is None
    assert counter("candidate_declined") == before + 1


async def test_duplicate_notifications_change_the_booking_once(world: World):
    booking = world.booking()
    world.calendar.recruiter_deletes(booking.id)
    world.calendar.expired_tokens.clear()

    world.push.notify(world.jordi)
    world.push.notify(world.jordi)
    world.push.notify(world.jordi)
    await world.push.drain()
    assert await world.sync() == 0

    assert len(world.notices()) == 1


async def test_out_of_order_notifications_end_on_the_calendar_as_it_is(world: World):
    """The recruiter moves the event twice; the second notification arrives while the first
    sync is reading the calendar. One more sync follows, and the booking ends where the
    calendar is, whatever order the notifications came in."""
    booking = world.booking()
    eleven = TUESDAY_10 + timedelta(hours=1)
    four = TUESDAY_10 + timedelta(hours=6)
    loop = asyncio.get_running_loop()
    read = world.calendar.changes
    calls = 0

    def changes_then_another_move(recruiter_id: str, token: str) -> Changes:
        nonlocal calls
        calls += 1
        found = read(recruiter_id, token)
        if calls == 1:  # Google sends message 2 while we process message 1
            world.calendar.recruiter_moves(booking.id, four)
            loop.call_soon_threadsafe(world.push.notify, recruiter_id)
        return found

    world.calendar.changes = changes_then_another_move  # pyright: ignore[reportAttributeAccessIssue]
    world.calendar.recruiter_moves(booking.id, eleven)
    world.push.notify(world.jordi)
    world.push.notify(world.jordi)  # a duplicate of message 1
    await world.push.drain()

    assert calls == 2
    slot = get_slot(world.repo, world.booking().slot_id)
    assert slot is not None
    assert slot.starts_at.replace(tzinfo=UTC) == four
    state = world.state()
    assert state.booking is not None
    assert "16:00" in state.booking.label
    assert len(world.notices()) == 2


async def test_an_expired_sync_token_resyncs_our_upcoming_events(world: World):
    booking = world.booking()
    [channel] = push.channels_of(world.repo, world.jordi)
    assert channel.sync_token is not None
    world.calendar.expired_tokens.add(channel.sync_token)
    world.calendar.recruiter_deletes(booking.id)
    before = counter("resync")

    assert await world.sync() == 1

    assert counter("resync") == before + 1
    assert world.booking().status == BookingStatus.CANCELLED
    [channel] = push.channels_of(world.repo, world.jordi)
    assert channel.sync_token not in world.calendar.expired_tokens
    assert "get" in world.calendar.calls
    assert await world.sync() == 0


async def test_a_candidate_reschedule_in_the_chat_is_not_a_calendar_change(world: World):
    world.service.llm.queue(ex(slot="change"))  # pyright: ignore[reportAttributeAccessIssue]
    reply = await world.service.handle_turn(world.conversation_id, "¿puedo cambiar la hora?")
    assert isinstance(reply.ui, Slots)
    reply = await world.service.handle_turn(
        world.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )
    assert isinstance(reply.ui, Booked)

    assert await world.sync() == 0
    assert world.notices() == []


async def test_events_that_are_not_ours_are_ignored(world: World):
    stranger = EventState(event_id="someone-elses-meeting", cancelled=True)
    marked = EventState(event_id="copy", cancelled=True, booking_id=world.booking().id)
    assert await world.push.reconcile(world.jordi, stranger) == 0
    assert await world.push.reconcile(world.jordi, marked) == 0  # a copy of our event
    assert await world.push.reconcile(world.marta, EventState(world.booking().id, True)) == 0
    assert world.booking().status == BookingStatus.BOOKED


# --- the webhook ---


def channel_headers(world: World, state: str = "exists", **overrides: str) -> dict[str, str]:
    [channel] = push.channels_of(world.repo, world.jordi)
    opened = world.calendar.channels[channel.id]
    headers = {
        "X-Goog-Channel-ID": channel.id,
        "X-Goog-Channel-Token": opened.token,
        "X-Goog-Resource-ID": opened.resource_id,
        "X-Goog-Resource-State": state,
        "X-Goog-Message-Number": "2",
    }
    headers.update(overrides)
    return headers


def drain(client: TestClient) -> None:
    """Wait for the syncs the app started after answering."""
    app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
    calendar_push: CalendarPush = app.state.calendar_push
    assert client.portal is not None
    client.portal.call(calendar_push.drain)


def notified(state: str) -> float:
    return metrics.CALENDAR_NOTIFICATIONS.labels(state)._value.get()


async def test_the_webhook_answers_at_once_and_syncs_after(world: World, caplog, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://sazon.example.com")
    get_settings.cache_clear()
    world.calendar.recruiter_deletes(world.booking().id)
    before = notified("exists")
    with caplog.at_level(logging.DEBUG), TestClient(create_app(world.service)) as client:
        headers = channel_headers(world)
        response = client.post("/api/webhooks/google/calendar", headers=headers)
        assert response.status_code == 200
        assert response.content == b""
        drain(client)

        assert world.booking().status == BookingStatus.CANCELLED
        assert notified("exists") == before + 1
        page = client.get(f"/api/conversations/{world.conversation_id}").json()
        texts = [turn["text"] for turn in page["turns"]]
        cancelled = push.CANCELLED_NOTICE["es-ES"].split("{when}")[0]
        assert any(text.startswith(cancelled) for text in texts)
        assert page["turns"][-1]["ui"]["kind"] == "slots"  # new times, on the next visit
        assert not page["state"]["ended"]

        metrics_page = client.get("/metrics").text
    assert headers["X-Goog-Channel-Token"] not in caplog.text
    assert "sazon_calendar_notifications_total" in metrics_page
    assert "sazon_calendar_reconciliations_total" in metrics_page
    assert "sazon_calendar_watch_failures_total" in metrics_page
    assert 'sazon_calendar_channels{state="ok"} 3.0' in metrics_page


async def test_the_sync_notification_is_acknowledged_without_a_sync(world: World):
    world.calendar.recruiter_deletes(world.booking().id)
    before = notified("sync")
    with TestClient(create_app(world.service)) as client:
        response = client.post(
            "/api/webhooks/google/calendar", headers=channel_headers(world, "sync")
        )
        drain(client)
    assert response.status_code == 200
    assert notified("sync") == before + 1
    assert world.booking().status == BookingStatus.BOOKED


@pytest.mark.parametrize(
    "overrides",
    [
        {"X-Goog-Channel-Token": "guessed"},
        {"X-Goog-Channel-ID": "unknown-channel"},
        {"X-Goog-Resource-ID": "another-calendar"},
        {"X-Goog-Channel-Token": ""},
    ],
)
async def test_a_notification_that_does_not_match_is_refused(world: World, overrides, caplog):
    world.calendar.recruiter_deletes(world.booking().id)
    before = notified("rejected")
    with caplog.at_level(logging.DEBUG), TestClient(create_app(world.service)) as client:
        headers = channel_headers(world, **overrides)
        response = client.post("/api/webhooks/google/calendar", headers=headers)
        drain(client)
    assert response.status_code == 403
    assert notified("rejected") == before + 1
    assert world.booking().status == BookingStatus.BOOKED
    real = world.calendar.channels[push.channels_of(world.repo, world.jordi)[0].id].token
    assert real not in caplog.text


async def test_the_webhook_over_its_rate_limit_answers_503_so_google_retries(
    world: World, monkeypatch
):
    monkeypatch.setenv("RATE_LIMIT_CALENDAR_WEBHOOKS_PER_MINUTE", "2")
    get_settings.cache_clear()
    with TestClient(create_app(world.service)) as client:
        codes = [
            client.post(
                "/api/webhooks/google/calendar", headers=channel_headers(world, "sync")
            ).status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 503]


# --- the command ---


def test_the_cli_starts_renews_and_stops_channels(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'sazon.db'}"
    engine = make_engine(url)
    init_db(engine)
    interviewer(Repo(engine), "Jordi")
    monkeypatch.setenv("SCHEDULING_SOURCE", "fake")
    get_settings.cache_clear()
    assert main(["--db", url, "calendar-watch", "start"]) == 2  # no PUBLIC_URL
    monkeypatch.setenv("PUBLIC_URL", "https://sazon.example.com")
    get_settings.cache_clear()

    assert main(["--db", url, "calendar-watch", "start"]) == 0
    assert "1 started" in capsys.readouterr().out
    assert main(["--db", url, "calendar-watch", "renew"]) == 0
    assert "0 renewed" in capsys.readouterr().out
    assert main(["--db", url, "calendar-watch", "renew", "--within-hours", "200"]) == 0
    assert "1 renewed" in capsys.readouterr().out
    assert main(["--db", url, "calendar-watch", "status"]) == 0
    assert "'ok': 1" in capsys.readouterr().out
    assert main(["--db", url, "calendar-watch", "stop"]) == 0
    assert "Stopped 1 channels" in capsys.readouterr().out
    with Repo(engine).session() as session:
        assert session.exec(select(CalendarChannel)).all() == []


async def test_the_agenda_shows_a_flagged_move(world: World):
    booking = world.booking()
    world.calendar.recruiter_moves(booking.id, TUESDAY_10 + timedelta(hours=11))
    await world.sync()

    with TestClient(create_app(world.service)) as client:
        signed = client.post("/api/auth/dev-login", json={"email": "jordi@gruposazon.example"})
        assert signed.status_code == 200
        [item] = client.get("/api/interviews").json()
    assert item["booking_id"] == booking.id
    assert item["calendar_issue"] == "outside_hours"
    assert item["local_start"].startswith("2026-10-20T21:00")
