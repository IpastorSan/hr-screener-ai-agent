"""The interview mode through the screening service (SAZ-39): asked only when the location
offers both (in person with its manager, by video when a recruiter covers it), chosen by tap,
words or the extractor's hint, switched before or after booking, and confirmed with the Meet
link or the address."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlmodel import select

from sazon import recruiters
from sazon.calendar.fake import FakeCalendar
from sazon.engine import AskMode, ConversationContext, ConversationState, OfferSlots
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
from sazon.engine.state import ChoosingMode, Closed, Interview, Offering, Qualified
from sazon.engine.ui import QuickReplies, Slots
from sazon.llm import Channel, FakeLLM
from sazon.llm.fallback_templates import OFFER_LEAD_MODE, RETRY, VIDEO_INVITE, VIDEO_INVITE_VOICE
from sazon.scheduling.service import SchedulingConfig
from sazon.service import Reply, ScreeningService, WidgetAnswer
from sazon.storage.models import Booking, BookingStatus, Candidate, Recruiter, Turn, TurnRole
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
CONTEXT = ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES")
HOURS = [["10:00", "12:00"], ["16:00", "18:00"]]


def ex(intent: Intent = Intent.ANSWER, *, mode: str | None = None, **fields: Any) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={name: FieldCandidate(value=v, confidence=0.95) for name, v in fields.items()},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        mode=mode,
    )


SCREENING = [
    ex(Intent.CONFIRM),
    ex(full_name="Lucía García", phone="612345678", email="lucia@example.com"),
    ex(has_license=True),
    ex(city="Gràcia"),
    ex(availability=["part_time"]),
    ex(schedule="morning"),
    ex(experience_years=0),
    ex(start_date="2026-10-26"),
    ex(Intent.CONFIRM),
]


def recruiter(
    repo: Repo, name: str, *modes: InterviewMode, location_ids: tuple[str, ...] | list[str] = ()
) -> Recruiter:
    return recruiters.create(
        repo,
        NOW,
        name=name,
        email=f"{name.lower()}@gruposazon.example",
        country="ES",
        location_ids=location_ids or ["es-bcn-gracia"],
        interview_modes=modes,
        working_hours=recruiters.weekday_hours(HOURS),
    )


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return Repo(engine)


@pytest.fixture
def both(repo: Repo) -> Repo:
    """Gràcia's manager interviews in person, Marta by video."""
    recruiter(repo, "Marta", InterviewMode.VIDEO)
    return repo


async def qualify(
    repo: Repo, llm: FakeLLM, calendar: FakeCalendar | None = None
) -> tuple[ScreeningService, Reply]:
    llm.queue(*SCREENING)
    config = SchedulingConfig(calendar=calendar)
    service = ScreeningService(llm, repo, clock=lambda: NOW, scheduling_config=config)
    reply = await service.start(context=CONTEXT)
    for _ in SCREENING:
        reply = await service.handle_turn(reply.conversation_id, "...")
    assert reply.outcome == Outcome.QUALIFIED
    return service, reply


def state_of(service: ScreeningService, conversation_id: str) -> ConversationState:
    conversation = service.repo.get_conversation(conversation_id)
    assert conversation is not None
    return ConversationState.model_validate(conversation.state)


def active(repo: Repo) -> list[Booking]:
    with repo.session() as session:
        query = select(Booking).where(Booking.status == BookingStatus.BOOKED)
        return list(session.exec(query).all())


def by_name(repo: Repo, name: str) -> Recruiter:
    found = recruiters.by_email(repo, f"{name.lower()}@gruposazon.example")
    assert found is not None
    return found


async def test_both_modes_at_the_location_ask_which_one_before_any_slot(both):
    llm = FakeLLM()
    service, reply = await qualify(both, llm)

    assert isinstance(reply.ui, QuickReplies)
    assert [(o.value, o.label) for o in reply.ui.options] == [
        ("onsite", "En persona"),
        ("video", "Videollamada"),
    ]
    assert reply.text == (
        "Gracias, ya tengo todo. ¿Prefieres hacer la entrevista en persona o por videollamada?"
    )
    assert isinstance(llm.actions[-1], AskMode)
    state = state_of(service, reply.conversation_id)
    assert state.stage == Stage.MODE
    assert state.offered_slots == ()
    with both.session() as session:
        kinds = [
            turn.action["kind"]
            for turn in session.exec(select(Turn).where(Turn.role == TurnRole.SYSTEM))
            if turn.action
        ]
    assert kinds == ["check_duplicate", "find_modes"]


async def test_one_mode_at_the_location_goes_straight_to_its_slots(repo):
    recruiter(repo, "Marta", InterviewMode.VIDEO, location_ids=["es-mad-centro"])
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)

    assert isinstance(reply.ui, Slots)
    assert not any(isinstance(action, AskMode) for action in llm.actions)
    assert "el local de Barcelona Gràcia" in reply.text
    state = state_of(service, reply.conversation_id)
    assert state.profile.interview_mode == InterviewMode.ONSITE
    assert state.available_modes == (InterviewMode.ONSITE,)


async def test_an_inactive_recruiter_does_not_make_a_mode_available(repo):
    marta = recruiter(repo, "Marta", InterviewMode.VIDEO)
    recruiters.update(repo, marta.id, NOW, active=False)
    _, reply = await qualify(repo, FakeLLM())
    assert isinstance(reply.ui, Slots)


async def test_tapping_video_offers_video_times_and_books_with_a_meet_link(both):
    calendar = FakeCalendar()
    llm = FakeLLM()
    service, reply = await qualify(both, llm, calendar)

    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="video"))

    assert reply.candidate_text == "Videollamada"
    assert isinstance(reply.ui, Slots)
    assert reply.text.startswith(OFFER_LEAD_MODE["es-ES"][InterviewMode.VIDEO])
    offer = llm.actions[-1]
    assert isinstance(offer, OfferSlots)
    assert offer.mode == InterviewMode.VIDEO

    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )

    assert isinstance(reply.ui, Booked)
    assert reply.ui.mode == InterviewMode.VIDEO
    assert reply.ui.meet_url is not None
    assert reply.ui.meet_url.startswith("https://meet.google.com/")
    assert reply.ui.model_dump(mode="json")["meetUrl"] == reply.ui.meet_url
    assert VIDEO_INVITE["es-ES"][True, True] in reply.text
    assert "http" not in reply.text
    assert "Carrer de Verdi" not in reply.text
    [booking] = active(both)
    assert booking.mode == "video"
    assert booking.recruiter_id == by_name(both, "Marta").id
    assert booking.meet_url == reply.ui.meet_url
    [event] = calendar.live_events()
    assert event.request.video
    with both.session() as session:
        candidate = session.get(Candidate, booking.candidate_id)
    assert candidate is not None
    assert candidate.profile["interview_mode"] == "video"


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("Presencial, por favor", InterviewMode.ONSITE),
        ("en la oficina", InterviewMode.ONSITE),
        ("por videollamada", InterviewMode.VIDEO),
        ("Meet", InterviewMode.VIDEO),
        ("online mejor", InterviewMode.VIDEO),
    ],
)
async def test_the_mode_in_words_is_read_even_when_the_extractor_fails(both, text, mode):
    llm = FakeLLM()
    service, reply = await qualify(both, llm)

    reply = await service.handle_turn(reply.conversation_id, text)  # no extraction queued

    assert isinstance(reply.ui, Slots)
    assert state_of(service, reply.conversation_id).profile.interview_mode == mode


async def test_the_extractor_hint_decides_when_the_words_do_not(both):
    llm = FakeLLM()
    service, reply = await qualify(both, llm)
    llm.queue(ex(mode="video"))

    reply = await service.handle_turn(reply.conversation_id, "lo que sea más cómodo desde casa")

    assert isinstance(reply.ui, Slots)
    assert state_of(service, reply.conversation_id).profile.interview_mode == InterviewMode.VIDEO


async def test_an_unclear_answer_asks_the_mode_again(both):
    llm = FakeLLM()
    service, reply = await qualify(both, llm)
    llm.queue(ex(mode="phone"))

    reply = await service.handle_turn(reply.conversation_id, "no sé")

    assert isinstance(reply.ui, QuickReplies)
    assert reply.text.startswith(RETRY["es-ES"])
    assert state_of(service, reply.conversation_id).profile.interview_mode is None


async def test_a_mode_hint_with_only_one_mode_on_offer_is_ignored(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    llm.queue(ex(mode="video"))

    reply = await service.handle_turn(reply.conversation_id, "¿y por videollamada?")

    assert isinstance(reply.ui, Slots)
    state = state_of(service, reply.conversation_id)
    assert state.profile.interview_mode == InterviewMode.ONSITE
    tapped = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="video"))
    assert state_of(service, tapped.conversation_id).profile.interview_mode == InterviewMode.ONSITE


async def test_switching_to_video_before_booking_reoffers_and_books_video(both):
    calendar = FakeCalendar()
    llm = FakeLLM()
    service, reply = await qualify(both, llm, calendar)
    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="onsite"))
    assert isinstance(reply.ui, Slots)
    assert "el local de Barcelona Gràcia" in reply.text
    assert "Sazón Barcelona" not in reply.text

    reply = await service.handle_turn(reply.conversation_id, "mejor por video")

    assert isinstance(reply.ui, Slots)
    assert reply.text.startswith(OFFER_LEAD_MODE["es-ES"][InterviewMode.VIDEO])
    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )
    assert isinstance(reply.ui, Booked)
    assert reply.ui.mode == InterviewMode.VIDEO
    [booking] = active(both)
    assert booking.recruiter_id == by_name(both, "Marta").id


async def test_switching_to_in_person_after_booking_moves_the_interview(both):
    calendar = FakeCalendar()
    llm = FakeLLM()
    service, reply = await qualify(both, llm, calendar)
    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="video"))
    assert isinstance(reply.ui, Slots)
    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )
    assert isinstance(reply.ui, Booked)

    reply = await service.handle_turn(reply.conversation_id, "al final prefiero ir en persona")

    assert isinstance(reply.ui, Slots)
    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )
    assert isinstance(reply.ui, Booked)
    assert reply.ui.mode == InterviewMode.ONSITE
    assert reply.ui.meet_url is None
    assert "Carrer de Verdi 45" in reply.text
    [booking] = active(both)
    assert booking.mode == "onsite"
    assert booking.interviewer_email == "gerencia.es-bcn-gracia@gruposazon.example"
    assert calendar.live_events(by_name(both, "Marta").id) == []  # the video event is gone


async def test_without_a_calendar_a_video_booking_says_the_link_comes_by_email(both):
    service, reply = await qualify(both, FakeLLM())
    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="video"))
    assert isinstance(reply.ui, Slots)

    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )

    assert isinstance(reply.ui, Booked)
    assert reply.ui.mode == InterviewMode.VIDEO
    assert reply.ui.meet_url is None
    assert VIDEO_INVITE["es-ES"][True, False] in reply.text
    assert "Google Meet" not in reply.text


async def test_by_voice_the_video_confirmation_reads_no_link(both):
    calendar = FakeCalendar()
    service, reply = await qualify(both, FakeLLM(), calendar)
    reply = await service.handle_turn(
        reply.conversation_id, "por videollamada", channel=Channel.VOICE
    )
    assert isinstance(reply.ui, Slots)

    reply = await service.handle_turn(reply.conversation_id, "la primera", channel=Channel.VOICE)

    assert isinstance(reply.ui, Booked)
    assert reply.ui.meet_url is not None  # on screen, never read out
    assert reply.text.endswith(VIDEO_INVITE_VOICE["es-ES"][True])
    assert "http" not in reply.text
    assert "meet.google" not in reply.text


async def test_with_a_calendar_only_connected_recruiters_make_a_mode_available(both):
    calendar = FakeCalendar(connected_ids=set())

    service, reply = await qualify(both, FakeLLM(), calendar)

    assert isinstance(reply.ui, Slots)  # Marta (video) has not connected hers: no question
    state = state_of(service, reply.conversation_id)
    assert state.available_modes == (InterviewMode.ONSITE,)


async def test_our_reading_of_the_words_beats_a_contrary_hint(both):
    llm = FakeLLM()
    service, reply = await qualify(both, llm)
    llm.queue(ex(mode="video"))

    reply = await service.handle_turn(reply.conversation_id, "en persona, por favor")

    assert isinstance(reply.ui, Slots)
    assert state_of(service, reply.conversation_id).profile.interview_mode == InterviewMode.ONSITE


@pytest.mark.parametrize(
    ("modes", "phase", "counts"),
    [
        ((InterviewMode.ONSITE, InterviewMode.VIDEO), Interview(sub=ChoosingMode()), True),
        ((InterviewMode.ONSITE, InterviewMode.VIDEO), Interview(sub=Offering()), True),
        ((InterviewMode.ONSITE,), Interview(sub=Offering()), False),
        ((InterviewMode.ONSITE, InterviewMode.VIDEO), Closed(variant=Qualified()), False),
    ],
)
def test_a_mode_tap_counts_only_while_there_is_a_mode_to_choose(modes, phase, counts):
    from sazon.engine.state import ConversationState
    from sazon.service import widget_extraction

    state = ConversationState(consented=True, available_modes=modes, phase=phase)
    read = widget_extraction(state, WidgetAnswer(kind="quick_replies", value="onsite"))
    chose = read is not None and read[0].mode == InterviewMode.ONSITE
    assert chose is counts
