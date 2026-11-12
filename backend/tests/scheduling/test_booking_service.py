"""Scheduling through the screening service: real slots, text and widget picks, bookings in
the database. Gràcia has no recruiter here, so its interviews are in person with the manager,
inside the location's hours (10:00-14:00 and 16:00-20:00), one at a time."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlmodel import select

from sazon import recruiters
from sazon.calendar.fake import FakeCalendar
from sazon.engine import ConfirmBooking, ConversationContext, OfferSlots
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Outcome,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.service_areas import load_locations
from sazon.engine.slots import Booked, Miss, OfferReason
from sazon.engine.ui import QuickReplies, Slots
from sazon.llm import FakeLLM
from sazon.llm.fallback_templates import (
    APPLICATION_SAVED,
    ASK_CANCEL,
    CANCELLED,
    CONFIRM_MOVE,
    CONFIRM_RESCHEDULE,
    DE_ESCALATE_CLOSED,
    DETAILS_UPDATED,
    END_ABUSE_CANCELLED,
    OFFER_LEAD,
    OFFER_LEAD_UNMATCHED,
    OFFER_PICK,
    OPT_OUT_CANCELLED,
    pack,
)
from sazon.scheduling.service import SchedulingConfig
from sazon.service import Reply, ScreeningService, WidgetAnswer
from sazon.storage.models import Booking, BookingStatus, CandidateStatus, Recruiter
from sazon.storage.repo import Repo, init_db, make_engine, state_of

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
CONTEXT = ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES")

LEAD = OFFER_LEAD["es-ES"]


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


def screening(phone: str = "612345678", email: str = "lucia@example.com") -> list[TurnExtraction]:
    return [
        ex(Intent.CONFIRM),
        ex(full_name="Lucía García", phone=phone, email=email),
        ex(has_license=True),
        ex(city="Gràcia"),
        ex(availability=["part_time"]),
        ex(schedule="morning"),
        ex(experience_years=0),
        ex(start_date="2026-10-26"),
        ex(Intent.CONFIRM),
    ]


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return Repo(engine)


def interviewer(repo: Repo, name: str = "Jordi") -> Recruiter:
    """One recruiter at Gràcia, interviewing by video 10:00-12:00 and 16:00-18:00 on
    weekdays."""
    return recruiters.create(
        repo,
        NOW,
        name=name,
        email=f"{name.lower()}@gruposazon.example",
        country="ES",
        location_ids=["es-bcn-gracia"],
        working_hours=recruiters.weekday_hours([["10:00", "12:00"], ["16:00", "18:00"]]),
    )


async def qualify(
    repo: Repo,
    llm: FakeLLM,
    config: SchedulingConfig | None = None,
    *,
    mode: str | None = None,
    **contact: str,
) -> tuple[ScreeningService, Reply]:
    """Screen a candidate up to the offered times; `mode` answers the mode question, asked
    when the location also has a recruiter by video."""
    llm.queue(*screening(**contact))
    service = ScreeningService(llm, repo, clock=lambda: NOW, scheduling_config=config)
    reply = await service.start(context=CONTEXT)
    for _ in range(9):
        reply = await service.handle_turn(reply.conversation_id, "...")
    assert reply.outcome == Outcome.QUALIFIED
    if mode is not None:
        assert isinstance(reply.ui, QuickReplies)
        reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value=mode))
    assert isinstance(reply.ui, Slots)
    return service, reply


def active(repo: Repo) -> list[Booking]:
    with repo.session() as session:
        query = select(Booking).where(Booking.status == BookingStatus.BOOKED)
        return list(session.exec(query).all())


async def test_offer_reply_lists_real_slots(repo):
    llm = FakeLLM()
    _, reply = await qualify(repo, llm)

    assert isinstance(reply.ui, Slots)
    labels = [slot.label for slot in reply.ui.slots]
    assert labels == ["martes 20 a las 10:00", "miércoles 21 a las 10:00", "jueves 22 a las 10:00"]
    assert reply.text.startswith(LEAD[OfferReason.QUALIFIED])
    for label in labels:
        assert label in reply.text
    assert "el local de Barcelona Gràcia" in reply.text
    assert "Sazón Barcelona" not in reply.text
    assert not reply.ended
    offer = llm.actions[-1]
    assert isinstance(offer, OfferSlots)
    assert offer.slots == reply.ui.slots


async def test_picking_a_slot_in_the_widget_books_it(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    second = reply.ui.slots[1]

    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value=second.id))

    assert isinstance(reply.ui, Booked)
    assert reply.ui.slot_id == second.id
    assert "miércoles 21 de octubre a las 10:00" in reply.text
    assert "Carrer de Verdi 45" in reply.text
    assert not reply.ended  # replying here can still move it
    [booking] = active(repo)
    assert booking.slot_id == second.id
    assert booking.conversation_id == reply.conversation_id


@pytest.mark.parametrize(
    ("message", "index"),
    [("the first one", 0), ("el jueves", 2), ("el miércoles por la mañana", 1), ("2", 1)],
)
async def test_a_pick_in_words_books_the_right_slot(repo, message, index):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    expected = reply.ui.slots[index]
    llm.queue(ex())  # the extractor gave no slot: our own reading decides

    reply = await service.handle_turn(reply.conversation_id, message)

    assert isinstance(reply.ui, Booked)
    assert reply.ui.slot_id == expected.id
    assert [b.slot_id for b in active(repo)] == [expected.id]


async def test_the_extractor_hint_is_used_when_the_words_are_unclear(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    third = reply.ui.slots[2]
    llm.queue(ex(slot=third.id))

    reply = await service.handle_turn(reply.conversation_id, "ese me cuadra")

    assert isinstance(reply.ui, Booked)
    assert reply.ui.slot_id == third.id


async def test_an_invented_slot_from_the_extractor_books_nothing(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    llm.queue(ex(slot="2026-10-19T09:00"))

    reply = await service.handle_turn(reply.conversation_id, "ese")

    assert isinstance(reply.ui, Slots)
    assert active(repo) == []


async def test_a_pick_still_books_when_the_extractor_fails(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    first = reply.ui.slots[0]

    reply = await service.handle_turn(reply.conversation_id, "el primero")  # nothing queued

    assert isinstance(reply.ui, Booked)
    assert reply.ui.slot_id == first.id


async def test_none_work_then_more_then_the_application_is_saved(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    first_ids = {slot.id for slot in reply.ui.slots}

    llm.queue(ex())
    reply = await service.handle_turn(reply.conversation_id, "ninguno me viene bien")
    assert isinstance(reply.ui, Slots)
    assert not first_ids & {slot.id for slot in reply.ui.slots}
    assert reply.text.startswith(LEAD[OfferReason.MORE])

    llm.queue(ex())
    reply = await service.handle_turn(reply.conversation_id, "tampoco, ninguno")
    assert reply.text == APPLICATION_SAVED["es-ES"]
    assert "?" not in reply.text
    assert reply.ui is None
    assert reply.ended
    assert active(repo) == []
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert state_of(conversation).talent_pool_opt_in is None
    assert conversation.outcome == Outcome.QUALIFIED
    candidate = repo.get_candidate(conversation.candidate_id or "")
    assert candidate is not None
    assert candidate.status == "qualified"
    assert not candidate.talent_pool


async def test_rescheduling_by_replying_moves_the_booking(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    first = reply.ui.slots[0]
    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value=first.id))
    assert isinstance(reply.ui, Booked)

    llm.queue(ex())
    reply = await service.handle_turn(reply.conversation_id, "no voy a poder ir, ¿la cambiamos?")
    assert isinstance(reply.ui, Slots)
    assert first.id not in {slot.id for slot in reply.ui.slots}
    assert reply.text.startswith(LEAD[OfferReason.RESCHEDULE])
    new = reply.ui.slots[0]

    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value=new.id))

    assert isinstance(reply.ui, Booked)
    assert reply.text.startswith(CONFIRM_RESCHEDULE["es-ES"].split("{location}")[0])
    assert [b.slot_id for b in active(repo)] == [new.id]


async def test_opting_out_after_booking_cancels_it(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    first = reply.ui.slots[0]
    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value=first.id))

    llm.queue(ex(Intent.OPT_OUT))
    reply = await service.handle_turn(reply.conversation_id, "ya no me interesa")

    assert reply.ended
    assert reply.outcome == Outcome.WITHDRAWN
    assert status_of(repo, reply) == CandidateStatus.WITHDRAWN
    assert active(repo) == []


async def test_opting_out_before_booking_keeps_the_application_qualified(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)

    llm.queue(ex(Intent.OPT_OUT))
    reply = await service.handle_turn(reply.conversation_id, "ya no me interesa")

    assert reply.ended
    assert reply.outcome == Outcome.QUALIFIED
    assert status_of(repo, reply) == CandidateStatus.QUALIFIED


async def test_a_withdrawn_application_is_closed_and_a_new_chat_starts_fresh(repo):
    """Withdrawn counts as closed, as a disqualified application does: the same phone in a new
    chat is not asked "¿Quieres continuar como...?", and a new application is screened."""
    llm = FakeLLM()
    service, reply, _ = await booked_by_video(repo, llm)
    llm.queue(ex(Intent.OPT_OUT))
    first = await service.handle_turn(reply.conversation_id, "ya no me interesa")
    assert status_of(repo, first) == CandidateStatus.WITHDRAWN

    _, again = await qualify(repo, llm, mode="video")

    assert again.outcome == Outcome.QUALIFIED
    assert status_of(repo, again) == CandidateStatus.QUALIFIED
    assert status_of(repo, first) == CandidateStatus.WITHDRAWN


def status_of(repo: Repo, reply: Reply) -> str | None:
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert conversation.candidate_id is not None
    candidate = repo.get_candidate(conversation.candidate_id)
    return candidate.status if candidate is not None else None


async def booked_by_video(repo: Repo, llm: FakeLLM) -> tuple[ScreeningService, Reply, FakeCalendar]:
    """A video interview on Jordi's (fake) calendar, Tuesday 20 at 10:00."""
    calendar = FakeCalendar()
    interviewer(repo)
    service, reply = await qualify(repo, llm, SchedulingConfig(calendar=calendar), mode="video")
    assert isinstance(reply.ui, Slots)
    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )
    assert isinstance(reply.ui, Booked)
    assert len(calendar.live_events()) == 1
    return service, reply, calendar


def cancelled(repo: Repo) -> list[Booking]:
    with repo.session() as session:
        query = select(Booking).where(Booking.status == BookingStatus.CANCELLED)
        return list(session.exec(query).all())


async def test_a_cancel_request_asks_then_cancels_the_booking_and_its_event(repo):
    llm = FakeLLM()
    service, reply, calendar = await booked_by_video(repo, llm)

    llm.queue(ex(Intent.CORRECTION))
    reply = await service.handle_turn(reply.conversation_id, "No puedo ir, cancela la entrevista")

    assert reply.text == ASK_CANCEL["es-ES"]
    assert isinstance(reply.ui, QuickReplies)
    assert [o.label for o in reply.ui.options] == ["Cancelar", "Otra hora"]
    assert len(active(repo)) == 1
    assert not reply.ended

    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="cancel"))

    assert reply.text == CANCELLED["es-ES"].format(day="martes 20")
    assert reply.ended
    assert reply.outcome == Outcome.WITHDRAWN
    assert status_of(repo, reply) == CandidateStatus.WITHDRAWN
    assert active(repo) == []
    [booking] = cancelled(repo)
    assert booking.cancelled_by == "candidate"
    assert calendar.live_events() == []


async def test_another_time_to_the_cancel_question_offers_new_times(repo):
    llm = FakeLLM()
    service, reply, calendar = await booked_by_video(repo, llm)
    llm.queue(ex(Intent.CORRECTION))
    reply = await service.handle_turn(reply.conversation_id, "Cancela la entrevista")

    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="reschedule"))

    assert isinstance(reply.ui, Slots)
    assert reply.text.startswith(LEAD[OfferReason.RESCHEDULE])
    assert len(active(repo)) == 1
    assert len(calendar.live_events()) == 1


async def test_a_cancel_request_the_extractor_reads_as_an_opt_out_still_asks(repo):
    llm = FakeLLM()
    service, reply, _ = await booked_by_video(repo, llm)

    llm.queue(ex(Intent.OPT_OUT))
    reply = await service.handle_turn(reply.conversation_id, "Anúlala, por favor")

    assert reply.text == ASK_CANCEL["es-ES"]
    assert len(active(repo)) == 1


async def test_a_cancel_request_that_gives_up_on_the_job_is_an_opt_out(repo):
    llm = FakeLLM()
    service, reply, calendar = await booked_by_video(repo, llm)

    llm.queue(ex(Intent.CORRECTION))
    reply = await service.handle_turn(
        reply.conversation_id, "Cancela la entrevista, ya no me interesa"
    )

    assert reply.text == OPT_OUT_CANCELLED["es-ES"].format(day="martes 20")
    assert active(repo) == []
    assert calendar.live_events() == []


async def test_opting_out_while_new_times_are_on_offer_cancels_the_booking_and_says_so(repo):
    """QA: "Ya no me interesa, cancela la entrevista" during a reschedule offer said goodbye and
    left the interview booked, its event on the calendar."""
    llm = FakeLLM()
    service, reply, calendar = await booked_by_video(repo, llm)
    llm.queue(ex(Intent.CORRECTION))
    reply = await service.handle_turn(reply.conversation_id, "No puedo ir ese día")
    assert isinstance(reply.ui, Slots)

    llm.queue(ex(Intent.OPT_OUT))
    reply = await service.handle_turn(
        reply.conversation_id, "Ya no me interesa, cancela la entrevista"
    )

    assert reply.text == OPT_OUT_CANCELLED["es-ES"].format(day="martes 20")
    assert reply.ended
    assert status_of(repo, reply) == CandidateStatus.WITHDRAWN
    assert active(repo) == []
    [booking] = cancelled(repo)
    assert booking.cancelled_by == "candidate"
    assert calendar.live_events() == []


async def test_a_second_insult_after_booking_cancels_it(repo):
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    assert isinstance(reply.ui, Slots)
    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )
    assert isinstance(reply.ui, Booked)

    llm.queue(ex(Intent.ABUSE))
    reply = await service.handle_turn(reply.conversation_id, "...")
    assert not reply.ended
    assert reply.text == DE_ESCALATE_CLOSED["es-ES"]
    assert len(active(repo)) == 1

    llm.queue(ex(Intent.ABUSE))
    reply = await service.handle_turn(reply.conversation_id, "...")

    assert reply.ended
    assert reply.text == END_ABUSE_CANCELLED["es-ES"]
    assert active(repo) == []
    assert reply.outcome == Outcome.QUALIFIED


async def test_a_double_booking_is_rejected_and_other_times_offered(repo):
    """One interview at a time at the location: the second candidate to pick the same time is
    offered others."""
    first_llm, second_llm = FakeLLM(), FakeLLM()
    first_service, first_reply = await qualify(repo, first_llm)
    second_service, second_reply = await qualify(
        repo, second_llm, phone="622345678", email="otra@example.com"
    )
    assert isinstance(first_reply.ui, Slots)
    assert isinstance(second_reply.ui, Slots)
    contested = first_reply.ui.slots[0]
    assert contested in second_reply.ui.slots

    first_reply = await first_service.handle_turn(
        first_reply.conversation_id, WidgetAnswer(value=contested.id)
    )
    second_reply = await second_service.handle_turn(
        second_reply.conversation_id, WidgetAnswer(value=contested.id)
    )

    assert isinstance(first_reply.ui, Booked)
    assert isinstance(second_reply.ui, Slots)
    assert contested.id not in {slot.id for slot in second_reply.ui.slots}
    assert second_reply.text.startswith(LEAD[OfferReason.TAKEN])
    assert [b.slot_id for b in active(repo)] == [contested.id]
    assert isinstance(second_llm.actions[-1], OfferSlots)
    assert not any(isinstance(a, ConfirmBooking) for a in second_llm.actions)


async def test_no_free_slots_saves_the_application_and_closes_the_chat(
    monkeypatch: pytest.MonkeyPatch,
):
    engine = make_engine("sqlite://")
    init_db(engine)
    repo = Repo(engine)  # no recruiter, and the location without a manager: nobody interviews
    gracia = load_locations()["es-bcn-gracia"]
    monkeypatch.setitem(load_locations(), gracia.location_id, replace(gracia, manager=None))
    llm = FakeLLM(screening())
    service = ScreeningService(llm, repo, clock=lambda: NOW)
    reply = await service.start(context=CONTEXT)
    for _ in range(9):
        reply = await service.handle_turn(reply.conversation_id, "...")

    assert reply.outcome == Outcome.QUALIFIED
    assert reply.text == (
        "Ahora mismo no tengo horarios de entrevista libres en tu local. "
        "Tu solicitud queda guardada."
    )
    assert reply.ended
    assert reply.ui is None


async def test_the_summary_leaves_the_interview_offer_out(repo):
    seen: list[str] = []

    class Recording(FakeLLM):
        async def summarize(self, transcript, profile, **kwargs):  # pyright: ignore[reportIncompatibleMethodOverride]
            seen.extend(turn.text for turn in transcript)
            return await super().summarize(transcript, profile, **kwargs)

    _, reply = await qualify(repo, Recording())

    assert seen
    assert reply.text not in seen
    # The process overview after consent mentions an interview, but no time (SAZ-47).
    overview = pack("es-ES").overview
    assert not any("entrevista" in text.removeprefix(overview) for text in seen)


async def test_with_a_calendar_the_booking_lands_on_the_recruiters_calendar(repo):
    """Offer -> book through the service with the fake calendar: a video interview with a
    Meet link, the candidate invited, the event id stored on the booking."""
    calendar = FakeCalendar()
    jordi = interviewer(repo)
    calendar.add_busy(
        jordi.id, datetime(2026, 10, 20, 8, tzinfo=UTC), datetime(2026, 10, 20, 9, tzinfo=UTC)
    )
    service, reply = await qualify(
        repo, FakeLLM(), SchedulingConfig(calendar=calendar), mode="video"
    )
    assert isinstance(reply.ui, Slots)
    # 10:00-11:00 Madrid on Tuesday is busy (and 11:00 is inside the buffer): 11:15 is first.
    assert reply.ui.slots[0].label == "martes 20 a las 11:15"

    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[0].id)
    )

    assert isinstance(reply.ui, Booked)
    [booking] = active(repo)
    [event] = calendar.live_events(jordi.id)
    assert booking.recruiter_id == jordi.id
    assert booking.google_event_id == event.event.event_id
    assert booking.mode == "video"
    assert booking.meet_url == event.event.meet_url is not None
    assert event.request.attendee_email == "lucia@example.com"
    assert event.request.attendee_name == "Lucía García"
    assert event.request.location is None
    assert event.request.video


async def ask_for_tuesdays(repo: Repo, llm: FakeLLM) -> tuple[ScreeningService, Reply]:
    service, reply = await qualify(repo, llm)
    llm.queue(ex(Intent.QUESTION))
    reply = await service.handle_turn(
        reply.conversation_id, "Pueees, ¿no podría ser los martes a las 12?"
    )
    assert isinstance(reply.ui, Slots)
    assert [slot.label for slot in reply.ui.slots] == [
        "martes 20 a las 12:00",
        "martes 27 a las 12:00",
        "martes 3 a las 12:00",
    ]
    return service, reply


@pytest.mark.parametrize("hint", [None, 2])
async def test_the_day_of_the_month_books_one_of_several_tuesdays(repo, hint):
    """Production loop: "¿no podría ser el martes?" offered Tuesdays at the same time (now
    "los martes a las 12" does: a weekday alone gets the nearest one's times), and
    "El martes 27 está bien." was read as all of them, then "Hay más de uno así" forever.
    The day of the month picks one, whatever the extractor guessed."""
    llm = FakeLLM()
    service, reply = await ask_for_tuesdays(repo, llm)
    assert isinstance(reply.ui, Slots)
    tuesday_27 = reply.ui.slots[1]
    llm.queue(ex(slot=None if hint is None else reply.ui.slots[hint].id))

    reply = await service.handle_turn(reply.conversation_id, "El martes 27 está bien.")

    assert isinstance(reply.ui, Booked)
    assert reply.ui.slot_id == tuesday_27.id
    assert [b.slot_id for b in active(repo)] == [tuesday_27.id]


async def test_the_which_question_is_never_asked_twice(repo):
    """Words that fit several Tuesdays twice: "Hay más de uno así" once, then the buttons and
    a one-line ask, and a tap books."""
    llm = FakeLLM()
    service, reply = await ask_for_tuesdays(repo, llm)
    llm.queue(ex())
    reply = await service.handle_turn(reply.conversation_id, "el martes a las doce")
    assert reply.text.startswith(LEAD[OfferReason.WHICH])

    llm.queue(ex())
    reply = await service.handle_turn(reply.conversation_id, "el martes a las doce")
    assert reply.text == OFFER_PICK["es-ES"]
    assert isinstance(reply.ui, Slots)
    assert len(reply.ui.slots) == 3

    reply = await service.handle_turn(
        reply.conversation_id, WidgetAnswer(value=reply.ui.slots[1].id)
    )
    assert isinstance(reply.ui, Booked)
    assert reply.ui.label.startswith("martes 27")


# --- the call of 2026-10-22: a weekday, a time, other options ---


def asking(text: str) -> TurnExtraction:
    return ex(Intent.QUESTION).model_copy(update={"question": text})


def labels(reply: Reply) -> list[str]:
    assert isinstance(reply.ui, Slots), reply.text
    return [slot.label for slot in reply.ui.slots]


async def test_times_asked_about_come_from_the_lookup_and_never_repeat(repo):
    """The call of 2026-10-22, replayed at Gràcia (in person 10-14 and 16-20): every question
    about the times is looked up, says what the lookup found, and never offers a time twice."""
    llm = FakeLLM()
    service, reply = await qualify(repo, llm)
    seen = labels(reply)
    assert seen == ["martes 20 a las 10:00", "miércoles 21 a las 10:00", "jueves 22 a las 10:00"]
    chat = reply.conversation_id

    # 1. A weekday alone: the nearest Tuesday's times, not three Tuesdays at 10:00.
    llm.queue(asking("¿Puede ser el martes?"))
    reply = await service.handle_turn(chat, "Ninguna. ¿Puede ser el martes?")
    assert labels(reply) == [
        "martes 20 a las 10:15",
        "martes 20 a las 10:30",
        "martes 20 a las 10:45",
    ]
    seen += labels(reply)

    # 2. A day and a time: that time, free, first, and the closest to it.
    llm.queue(ex(Intent.ANSWER).model_copy(update={"question": "¿Podría ser a las 12?"}))
    reply = await service.handle_turn(
        chat, "El martes 20 puedo, pero ¿podría ser a las 12 de la mañana?"
    )
    assert reply.text.startswith(LEAD[OfferReason.ASKED])
    assert labels(reply) == [
        "martes 20 a las 12:00",
        "martes 20 a las 11:45",
        "martes 20 a las 12:15",
    ]
    seen += labels(reply)

    # 3. A time alone that is not free: said by the lookup's fixed line, the closest that day.
    llm.queue(asking("¿A las 14:00 puede ser?"))
    reply = await service.handle_turn(chat, "No, pregunto que si a las 14:00 puede ser.")
    assert reply.text.startswith(OFFER_LEAD_UNMATCHED["es-ES"][Miss.TIME])
    assert labels(reply) == [
        "martes 20 a las 13:45",
        "martes 20 a las 13:30",
        "martes 20 a las 13:15",
    ]
    seen += labels(reply)

    # 4. Other options: the next times for what was asked, none offered before, never the FAQ.
    llm.queue(asking("¿Y otras opciones?"))
    reply = await service.handle_turn(chat, "¿Y otras opciones?")
    assert reply.text.startswith(LEAD[OfferReason.ASKED])
    assert not set(labels(reply)) & set(seen)
    assert all(label.startswith("martes 20") for label in labels(reply))
    seen += labels(reply)

    # 5. Other times that day: never one offered before.
    llm.queue(asking("¿Qué otros huecos disponibles tienes el martes 20?"))
    reply = await service.handle_turn(chat, "¿Qué otros huecos disponibles tienes el martes 20?")
    assert not set(labels(reply)) & set(seen)
    assert all(label.startswith("martes 20") for label in labels(reply))
    assert not active(repo)


# --- correcting a detail after booking (2026-10-22) ---


def candidate_of(repo: Repo, reply: Reply):
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert conversation.candidate_id is not None
    candidate = repo.get_candidate(conversation.candidate_id)
    assert candidate is not None
    return candidate


async def test_an_email_corrected_after_booking_updates_the_calendar_invitation(repo):
    llm = FakeLLM()
    service, reply, calendar = await booked_by_video(repo, llm)
    [before] = active(repo)

    llm.queue(ex(Intent.CORRECTION))
    reply = await service.handle_turn(reply.conversation_id, "Mi correo está mal")
    assert "correo" in reply.text.lower() or "email" in reply.text.lower()
    assert not reply.ended

    llm.queue(ex(email="lucia.nueva@example.com"))
    reply = await service.handle_turn(reply.conversation_id, "lucia.nueva@example.com")

    assert reply.text == DETAILS_UPDATED["es-ES"][0].format(when="martes 20 de octubre a las 10:00")
    assert isinstance(reply.ui, Booked)
    [after] = active(repo)
    assert after.id == before.id
    assert candidate_of(repo, reply).email == "lucia.nueva@example.com"
    [event] = calendar.live_events()
    assert event.request.attendee_email == "lucia.nueva@example.com"
    assert calendar.calls.count("update") == 1


async def test_a_phone_given_with_the_correction_after_booking_is_kept_at_once(repo):
    llm = FakeLLM()
    service, reply, _ = await booked_by_video(repo, llm)

    llm.queue(ex(Intent.CORRECTION, phone="699111222"))
    reply = await service.handle_turn(reply.conversation_id, "Mi móvil es 699 111 222")

    assert reply.text == DETAILS_UPDATED["es-ES"][0].format(when="martes 20 de octubre a las 10:00")
    assert candidate_of(repo, reply).phone == "+34699111222"
    assert len(active(repo)) == 1


async def test_the_availability_corrected_after_booking_keeps_the_interview(repo):
    llm = FakeLLM()
    service, reply, calendar = await booked_by_video(repo, llm)

    llm.queue(ex(Intent.CORRECTION))
    reply = await service.handle_turn(reply.conversation_id, "quiero cambiar la disponibilidad")
    llm.queue(ex(availability=["weekends"]))
    reply = await service.handle_turn(reply.conversation_id, "Fines de semana")

    assert reply.text == DETAILS_UPDATED["es-ES"][0].format(when="martes 20 de octubre a las 10:00")
    assert candidate_of(repo, reply).profile["availability"] == ["weekends"]
    assert len(active(repo)) == 1
    assert "update" not in calendar.calls


async def test_another_location_after_booking_asks_then_cancels_and_offers_times_there(repo):
    llm = FakeLLM()
    service, reply, calendar = await booked_by_video(repo, llm)

    llm.queue(ex(Intent.CORRECTION))
    reply = await service.handle_turn(reply.conversation_id, "Quiero cambiar la zona")
    llm.queue(ex(city="Sants"))
    reply = await service.handle_turn(reply.conversation_id, "Sants")

    assert reply.text == CONFIRM_MOVE["es-ES"].format(new="Barcelona Sants", old="Barcelona Gràcia")
    assert len(active(repo)) == 1

    reply = await service.handle_turn(reply.conversation_id, WidgetAnswer(value="yes"))

    assert isinstance(reply.ui, Slots)
    assert active(repo) == []
    assert calendar.live_events() == []
    assert candidate_of(repo, reply).location_id == "es-bcn-sants"
    assert reply.outcome == Outcome.QUALIFIED


async def test_the_same_location_after_booking_changes_nothing(repo):
    llm = FakeLLM()
    service, reply, _ = await booked_by_video(repo, llm)

    llm.queue(ex(Intent.CORRECTION, city="Gràcia"))
    reply = await service.handle_turn(reply.conversation_id, "La zona es Gràcia")

    assert reply.text == DETAILS_UPDATED["es-ES"][1].format(when="martes 20 de octubre a las 10:00")
    assert len(active(repo)) == 1


async def test_a_detail_named_after_booking_is_asked_even_when_the_extractor_fails(repo):
    """Live run, 2026-10-22: the extractor timed out on "Mi correo está mal" and the chat
    said goodbye. The words alone are enough."""
    llm = FakeLLM()
    service, reply, _ = await booked_by_video(repo, llm)

    reply = await service.handle_turn(reply.conversation_id, "Mi correo está mal")  # nothing queued

    assert "correo" in reply.text.lower() or "email" in reply.text.lower()
    assert not reply.ended
    assert len(active(repo)) == 1
