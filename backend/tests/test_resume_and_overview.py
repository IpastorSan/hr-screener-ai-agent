"""The service side of SAZ-47: the process overview after consent, resuming an earlier
application from its state, reopening right after a gate closed, and a call that said goodbye
saying nothing more."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import select

from said import said
from sazon.agents.riders import FieldName
from sazon.engine import AskField, ConversationContext, CountrySource, EndReason
from sazon.engine.actions import Action, ApplicationStatus, OfferSlots
from sazon.engine.models import (
    CandidateProfile,
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Outcome,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.slots import Booked, Slot
from sazon.engine.spoken import Register
from sazon.engine.state import (
    Closed,
    ConversationState,
    Interview,
    InterviewBooked,
    Qualified,
)
from sazon.engine.validators import Country
from sazon.llm import FakeLLM
from sazon.llm.fallback_templates import (
    END,
    VIDEO_CHANGE,
    fallback_reply,
    pack,
    welcome_back,
    welcome_back_with_times,
)
from sazon.llm.interface import Channel, ReplyContext
from sazon.llm.reply_checks import follow_up_promise, reply_problem, spoken_digits
from sazon.service import Reply, ScreeningService, closes_call
from sazon.storage.models import Candidate, CandidateStatus, Conversation
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
CONTEXTS: dict[Register, ConversationContext] = {
    "es-ES": ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES"),
    "es-MX": ConversationContext(country="MX", timezone="America/Mexico_City", locale="es-MX"),
    "en": ConversationContext(country="ES", timezone="Europe/Madrid", locale="en"),
}
PHONE = "612345601"
EMAIL = "carlos.navarro@example.com"


def ex(intent: Intent = Intent.ANSWER, language: Language = Language.ES, **fields: Any):
    return TurnExtraction(
        intent=intent,
        fields={k: FieldCandidate(value=v, confidence=0.95) for k, v in fields.items()},
        language=language,
        matrix_language=MatrixLanguage(language.value),
        english_ratio=1.0 if language == Language.EN else 0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


CONSENT = ex(Intent.CONFIRM)
YES = ex(Intent.CONFIRM)
CONTACT = ex(full_name="Carlos Navarro", phone=PHONE, email=EMAIL)


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return Repo(engine)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def rows[T](repo: Repo, table: type[T]) -> list[T]:
    with repo.session() as session:
        return list(session.exec(select(table)).all())  # pyright: ignore[reportArgumentType, reportCallIssue]


async def say(svc: ScreeningService, reply: Reply, *texts: str, **kwargs: Any) -> Reply:
    for text in texts:
        reply = await svc.handle_turn(reply.conversation_id, text, **kwargs)
    return reply


# --- 1. the process overview ---


@pytest.mark.parametrize("register", list(CONTEXTS))
@pytest.mark.parametrize("channel", list(Channel))
async def test_the_overview_leads_the_first_question_after_consent(
    repo: Repo, register: Register, channel: Channel
):
    language = Language.EN if register == "en" else Language.ES
    llm = FakeLLM([ex(Intent.CONFIRM, language), ex(language=language, full_name="Carlos Navarro")])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(channel, CONTEXTS[register])

    reply = await svc.handle_turn(reply.conversation_id, "Sí")

    voice = channel == Channel.VOICE
    lead = pack(register).overview_voice if voice else pack(register).overview
    assert reply.text.startswith(f"{lead} ")
    question = reply.text.removeprefix(f"{lead} ")
    assert isinstance(llm.actions[-1], AskField)
    assert question == _reply(llm.actions[-1], channel)
    later = await svc.handle_turn(reply.conversation_id, "Carlos Navarro")
    assert lead not in later.text
    stored = [t.text for t in repo.turns(reply.conversation_id) if t.role == "agent"]
    assert stored[1] == reply.text


async def test_the_overview_is_a_template_the_model_never_writes(repo: Repo):
    llm = FakeLLM([CONSENT], replies=["¿Me dices tu nombre completo?"])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.WEB, CONTEXTS["es-ES"])
    llm.replies.clear()
    llm.queue_replies("¿Me dices tu nombre completo?")

    reply = await svc.handle_turn(reply.conversation_id, "Sí")

    assert reply.text == f"{pack('es-ES').overview} ¿Me dices tu nombre completo?"


async def test_the_overview_streams_first_by_voice(repo: Repo):
    llm = FakeLLM([CONSENT])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.VOICE, CONTEXTS["es-MX"])
    said: list[str] = []

    await svc.handle_turn(reply.conversation_id, "Sí", emit=said.append)

    assert said[0] == pack("es-MX").overview_voice


async def test_no_overview_without_consent(repo: Repo):
    llm = FakeLLM([ex(Intent.UNCLEAR), ex(Intent.OPT_OUT)])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.WEB, CONTEXTS["es-ES"])
    retry = await svc.handle_turn(reply.conversation_id, "¿Qué?")
    declined = await svc.handle_turn(reply.conversation_id, "No")
    assert pack("es-ES").overview not in retry.text + declined.text


@pytest.mark.parametrize("register", list(CONTEXTS))
def test_the_overview_promises_no_review_or_contact(register: Register):
    for text in (pack(register).overview, pack(register).overview_voice):
        assert follow_up_promise(text) is None
        assert not re.search(r"\b(?:revis|review)", text.lower())
    tu = {"te", "ti", "tu", "encajas"}
    words = set(pack(register).overview.lower().replace(",", " ").replace(".", " ").split())
    if register == "es-MX":
        assert not words & tu
    if register == "es-ES":
        assert not words & {"le", "usted", "su", "encaja"}


def _reply(action: Action, channel: Channel) -> str:
    ctx = ReplyContext.for_action(action, today=NOW.date(), channel=channel)
    return fallback_reply(action, ctx)


# --- 2. resuming into an earlier application ---


async def earlier_application(repo: Repo, **state: Any) -> Conversation:
    """A Mexican application (usted, Monterrey) that went as far as the license question,
    then `state` on top. Returns its conversation."""
    llm = FakeLLM([CONSENT, CONTACT])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.WEB, CONTEXTS["es-MX"])
    reply = await say(svc, reply, "Sí", "Carlos Navarro, 612345601, carlos.navarro@example.com")
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    loaded = ConversationState.model_validate(conversation.state).model_copy(update=state)
    conversation.state = loaded.model_dump(mode="json")
    repo.save(conversation)
    candidate = repo.get_candidate(conversation.candidate_id or "")
    assert candidate is not None
    if loaded.outcome == Outcome.QUALIFIED:
        candidate.status = CandidateStatus.QUALIFIED
        candidate.closed_at = NOW
        repo.save(candidate)
    return conversation


BOOKED = Booked(
    slotId="slot-gone",
    startsAt="2026-10-22T09:00-06:00",
    label="jueves 22 de octubre a las 09:00",
    location="Monterrey Centro",
    address="Av. Juárez 1",
)
MTY = "mx-mty-centro"
CONTACT_PROFILE = CandidateProfile(
    full_name="Carlos Navarro", phone="+34612345601", email="carlos.navarro@example.com"
)
CLOSED = {"phase": Closed(variant=Qualified()), "country": "MX"}
BOOKED_PHASE = {"phase": Interview(sub=InterviewBooked(booking=BOOKED))}


async def call_again(repo: Repo, clock: Clock, register: Register = "es-ES") -> Reply:
    clock.now = NOW + timedelta(hours=4)
    # By voice too the duplicate check follows the email: nothing is spelled back (SAZ-48).
    # The match is asked about first ("¿Sigues como Carlos?", D10): yes.
    llm = FakeLLM([CONSENT, CONTACT, YES])
    svc = ScreeningService(llm, repo, clock)
    reply = await svc.start(Channel.VOICE, CONTEXTS[register])
    contact = "Carlos Navarro, 612345601, carlos.navarro@example.com"
    asked = await say(svc, reply, "Sí", contact)
    assert asked.text == "Ya tenemos este teléfono. ¿Sigues como Carlos?"
    return await say(svc, asked, "Sí")


@pytest.mark.parametrize("closed", [BOOKED_PHASE])
async def test_a_closed_application_gets_its_status_and_a_goodbye(repo: Repo, closed):
    """The live call 0c7032168: the phone and email matched a qualified Mexican application
    (usted) whose stored question no longer exists. The status is said in the new call's
    register (tú), by voice in words (SAZ-48)."""
    earlier = await earlier_application(repo, **{**CLOSED, **closed})
    turns_before = len(repo.turns(earlier.id))
    clock = Clock()

    reply = await call_again(repo, clock)

    status = said(ApplicationStatus, booking=BOOKED, channel=Channel.VOICE, today=NOW.date())
    assert reply.text == status
    assert spoken_digits(reply.text) is None, reply.text
    assert reply.text.endswith(END["es-ES"][EndReason.CLOSED])
    assert reply.ended
    assert reply.conversation_id != earlier.id
    assert len(repo.turns(earlier.id)) == turns_before  # the closed application is untouched
    [candidate] = rows(repo, Candidate)
    assert candidate.id == earlier.candidate_id
    # The new call's details never overwrite the application it matched.
    assert (candidate.status, candidate.country) == (CandidateStatus.QUALIFIED, "MX")
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert conversation.candidate_id == earlier.candidate_id
    [last] = [t for t in repo.turns(conversation.id) if t.role == "agent"][-1:]
    assert last.action is not None
    assert last.action["kind"] == "application_status"
    assert closes_call(last.action)


async def test_a_booked_application_keeps_its_status_after_an_earlier_return(repo: Repo):
    """SAZ-72: a first return linked a stub conversation to the application. The second
    return reads the application's own conversation, with the booking, not the stub."""
    earlier = await earlier_application(repo, **{**CLOSED, **BOOKED_PHASE})
    clock = Clock()
    first = await call_again(repo, clock)
    assert repo.latest_conversation(earlier.candidate_id or "") is not None
    assert first.conversation_id != earlier.id

    clock.now += timedelta(hours=1)
    second = await call_again(repo, clock)

    status = said(ApplicationStatus, booking=BOOKED, channel=Channel.VOICE, today=NOW.date())
    assert first.text == second.text == status
    holder = repo.application_conversation(earlier.candidate_id or "")
    assert holder is not None
    assert holder.id == earlier.id


async def test_the_restart_button_after_a_return_leaves_the_application_it_found(repo: Repo):
    """SAZ-88: the restart button on the chat that was told a booked application's status
    restarted that application, and the same phone no longer found it. The chat holds no
    application of its own: nothing is closed."""
    earlier = await earlier_application(repo, **{**CLOSED, **BOOKED_PHASE})
    clock = Clock()
    first = await call_again(repo, clock)
    assert first.conversation_id != earlier.id

    svc = ScreeningService(FakeLLM([]), repo, clock)
    assert not await svc.restart(first.conversation_id)

    [candidate] = rows(repo, Candidate)
    assert (candidate.status, candidate.outcome_reason) == (CandidateStatus.QUALIFIED, None)
    holder = repo.application_conversation(earlier.candidate_id or "")
    assert holder is not None
    assert holder.id == earlier.id
    clock.now += timedelta(hours=1)
    second = await call_again(repo, clock)  # asks "¿Sigues como Carlos?" again
    assert second.text == first.text


async def test_a_booked_application_returning_by_web_can_move_its_interview(repo: Repo):
    """QA batch 2: the status of a booked interview kept the chat on a conversation without
    the booking, so "¿la puedo cambiar a otro día?" got the unknown answer. The chat now
    carries on in the application's conversation, which moves it."""
    profile = CONTACT_PROFILE.model_copy(update={"has_license": True, "location_id": MTY})
    earlier = await earlier_application(repo, **{**CLOSED, **BOOKED_PHASE}, profile=profile)
    clock = Clock()
    clock.now = NOW + timedelta(hours=4)
    llm = FakeLLM([CONSENT, CONTACT, YES, ex(Intent.QUESTION)])
    svc = ScreeningService(llm, repo, clock)
    reply = await svc.start(Channel.WEB, CONTEXTS["es-ES"])
    reply = await say(svc, reply, "Sí", "Carlos Navarro, 612345601, carlos.navarro@example.com")
    # Nothing of the booking before the yes (D10): no day, time or place.
    assert reply.text == "Ya tenemos este teléfono registrado. ¿Quieres continuar como Carlos?"
    reply = await say(svc, reply, "Sí")

    assert reply.conversation_id == earlier.id
    assert not reply.ended
    assert reply.text.endswith(VIDEO_CHANGE["es-ES"])

    reply = await say(svc, reply, "¿La puedo cambiar a otro día?")

    assert reply.conversation_id == earlier.id
    last = repo.turns(earlier.id)[-1].action
    assert last is not None
    assert last["kind"] == "offer_slots", last


async def test_a_saved_application_without_an_interview_is_offered_times_again(repo: Repo):
    """Live test 4f64892c (SAZ-72): the phone matched a qualified application saved with no
    interview, and Ana said goodbye. It goes back to scheduling instead."""
    profile = CONTACT_PROFILE.model_copy(update={"has_license": True, "location_id": MTY})
    earlier = await earlier_application(
        repo, **{**CLOSED, "phase": Closed(variant=Qualified(slots_declined=True))}, profile=profile
    )
    clock = Clock()

    reply = await call_again(repo, clock)

    assert reply.conversation_id == earlier.id
    # One fixed line with the times, never "Seguimos donde lo dejaste" before a whole offer.
    assert reply.text.startswith("Hola de nuevo, Carlos. Tengo nuevas horas para tu entrevista: ")
    assert reply.text.endswith("¿Cuál te viene mejor?")
    assert spoken_digits(reply.text) is None, reply.text  # by voice, the times in words
    assert reply.text.count(".") <= 2, reply.text
    assert not reply.ended
    resumed = repo.get_conversation(earlier.id)
    assert resumed is not None
    state = ConversationState.model_validate(resumed.state)
    assert (state.stage, state.outcome) == (Stage.SCHEDULING, Outcome.QUALIFIED)
    assert state.offered_slots
    last = repo.turns(earlier.id)[-1].action
    assert last is not None
    assert last["kind"] == "offer_slots"


@pytest.mark.parametrize("register", list(CONTEXTS))
def test_the_welcome_back_with_new_times_fits_the_reply_limits(register: Register):
    country: Country = "MX" if register == "es-MX" else "ES"
    language = Language.EN if register == "en" else Language.ES
    slot = Slot(
        id="s", startsAt="2026-10-22T10:45:00+02:00", label="jueves 22 a las 10:45", location="X"
    )
    action = OfferSlots(
        location_id=None, slots=(slot, slot, slot), language=language, country=country
    )
    for voice in (False, True):
        text = welcome_back_with_times(action, "Lucía Gil", voice=voice)
        assert reply_problem(text, language, country=country, action=action) is None, text
        assert "Lucía" in text
        assert "Gil" not in text


async def test_an_open_application_resumes_in_the_new_register(repo: Repo):
    earlier = await earlier_application(
        repo, country="MX", country_source=CountrySource.TIMEZONE, duplicate_checked=True
    )
    clock = Clock()

    reply = await call_again(repo, clock, "es-ES")

    assert reply.conversation_id == earlier.id
    welcome = welcome_back(Language.ES, "ES", "Carlos Navarro")
    question = said(AskField, field=FieldName.HAS_LICENSE, channel=Channel.VOICE)
    assert reply.text == f"{welcome} {question}"
    resumed = repo.get_conversation(earlier.id)
    assert resumed is not None
    state = ConversationState.model_validate(resumed.state)
    assert (state.language, state.country) == (Language.ES, "ES")
    assert resumed.channel == "voice"


async def test_an_open_application_resumes_in_english_on_the_web(repo: Repo):
    earlier = await earlier_application(repo, duplicate_checked=True)
    clock = Clock()
    clock.now = NOW + timedelta(hours=4)
    contact = ex(language=Language.EN, full_name="Carlos Navarro", phone=PHONE, email=EMAIL)
    llm = FakeLLM([ex(Intent.CONFIRM, Language.EN), contact, ex(Intent.CONFIRM, Language.EN)])
    svc = ScreeningService(llm, repo, clock)
    reply = await svc.start(Channel.WEB, CONTEXTS["en"])
    reply = await say(svc, reply, "Yes", "Carlos, 612345601")
    assert (
        reply.text
        == "We already have this phone number on file. Do you want to continue as Carlos?"
    )
    reply = await say(svc, reply, "Yes")

    assert reply.conversation_id == earlier.id
    welcome = welcome_back(Language.EN, "ES", "Carlos Navarro")
    question = said(AskField, field=FieldName.HAS_LICENSE, register="en")
    assert reply.text == f"{welcome} {question}"


async def test_the_resumed_call_says_only_the_welcome_and_the_question(repo: Repo):
    await earlier_application(repo, duplicate_checked=True)
    clock = Clock()
    clock.now = NOW + timedelta(hours=4)
    llm = FakeLLM([CONSENT, CONTACT, YES])
    svc = ScreeningService(llm, repo, clock)
    reply = await svc.start(Channel.VOICE, CONTEXTS["es-ES"])
    reply = await say(svc, reply, "Sí", "...")
    said: list[str] = []

    resumed = await svc.handle_turn(reply.conversation_id, "Sí", emit=said.append)

    assert said == []  # nothing streamed: the reply is the welcome back and the question
    assert resumed.text.startswith(welcome_back(Language.ES, "ES", "Carlos Navarro"))


# --- 4. reopening right after a gate closed ---


async def test_a_contradiction_reopens_the_candidate_and_drops_the_summary(repo: Repo):
    llm = FakeLLM(
        [CONSENT, CONTACT, ex(has_license=False), ex(Intent.CORRECTION, has_license=True)]
    )
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.WEB, CONTEXTS["es-ES"])
    reply = await say(svc, reply, "Sí", "...", "No")
    assert reply.outcome == Outcome.DISQUALIFIED
    assert repo.summary(reply.conversation_id) is not None

    reply = await svc.handle_turn(reply.conversation_id, "Perdona, sí tengo carnet")

    assert reply.outcome is None
    assert not reply.ended
    [candidate] = rows(repo, Candidate)
    assert candidate.status == CandidateStatus.IN_PROGRESS
    assert candidate.outcome_reason is None
    assert candidate.closed_at is None
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert conversation.ended_at is None
    assert conversation.outcome is None
    assert repo.summary(conversation.id) is None


async def test_my_email_is_wrong_asks_for_it_again(repo: Repo):
    llm = FakeLLM([CONSENT, CONTACT, ex(Intent.CORRECTION)])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.WEB, CONTEXTS["es-ES"])
    reply = await say(svc, reply, "Sí", "...")

    reply = await svc.handle_turn(reply.conversation_id, "Espera, mi email está mal")

    assert reply.text == said(AskField, field=FieldName.EMAIL, again=True)


# --- 3, 5. a call that said goodbye says nothing more ---


async def test_after_the_goodbye_the_call_says_nothing_more(repo: Repo):
    llm = FakeLLM([CONSENT, CONTACT, ex(Intent.OPT_OUT), ex(Intent.SMALL_TALK)])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.VOICE, CONTEXTS["es-ES"])
    reply = await say(svc, reply, "Sí", "...")
    reply = await svc.handle_turn(reply.conversation_id, "Lo dejo aquí")
    assert reply.text == END["es-ES"][EndReason.OPT_OUT]

    reply = await svc.handle_turn(reply.conversation_id, "Adiós")

    assert reply.text == ""
    last = repo.turns(reply.conversation_id)[-1]
    assert (last.role, last.text) == ("agent", None)
    assert last.action is not None
    assert last.action["kind"] == "end"


async def test_the_web_still_answers_after_the_goodbye(repo: Repo):
    llm = FakeLLM([CONSENT, CONTACT, ex(Intent.OPT_OUT), ex(Intent.SMALL_TALK)])
    svc = ScreeningService(llm, repo, Clock())
    reply = await svc.start(Channel.WEB, CONTEXTS["es-ES"])
    reply = await say(svc, reply, "Sí", "...", "Lo dejo aquí", "Adiós")
    assert reply.text


@pytest.mark.parametrize(
    ("action", "closes"),
    [
        ({"kind": "end", "reason": "closed"}, True),
        ({"kind": "end", "reason": "paused"}, True),
        ({"kind": "application_status"}, True),
        ({"kind": "disqualify", "reason": "no_license"}, True),
        ({"kind": "disqualify", "reason": "policy_violation"}, True),
        ({"kind": "disqualify", "reason": "out_of_area"}, False),
        ({"kind": "confirm_booking"}, False),
        ({"kind": "ask_field"}, False),
        (None, False),
    ],
)
def test_which_turns_hang_up_a_call(action: dict[str, Any] | None, closes: bool):
    assert closes_call(action) is closes


@pytest.mark.parametrize(
    ("register", "language", "country"),
    [("es-ES", Language.ES, "ES"), ("es-MX", Language.ES, "MX"), ("en", Language.EN, "ES")],
)
def test_application_status_is_one_sentence_then_the_goodbye_or_how_to_move_it(
    register: Register, language: Language, country: Country
):
    for booking in (None, BOOKED):
        action = ApplicationStatus(booking=booking, language=language, country=country)
        for channel in Channel:
            text = _reply(action, channel)
            plain = re.sub(r"\b([ap])\.m\.", r"\1m", text)  # "at nine a.m." in words (SAZ-48)
            assert plain.count(".") + plain.count("!") <= 3, text
            # By web a booked interview can be moved in the chat, which stays open.
            open_chat = booking is not None and channel == Channel.WEB
            ending = VIDEO_CHANGE[register] if open_chat else END[register][EndReason.CLOSED]
            assert text.endswith(ending)
            assert follow_up_promise(text) is None
