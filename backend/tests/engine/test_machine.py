from datetime import date, timedelta
from typing import Any

import pytest

from sazon.agents.riders import Close, FieldName, Step
from sazon.engine import (
    Action,
    AnswerQuestion,
    AskField,
    CheckDuplicate,
    Clarify,
    ConfirmIdentity,
    ConfirmSummary,
    ConfirmValue,
    ConversationContext,
    ConversationState,
    DeEscalate,
    Deflect,
    DeflectKind,
    Disqualify,
    DisqualifyReason,
    DuplicateChecked,
    End,
    EndReason,
    FindModes,
    Greet,
    OfferPause,
    ResumeExisting,
    SkipEmail,
    SkipPhone,
    WarnPolicy,
    abandon,
    current_prompt,
    on_lookup,
    reopen,
    start,
    step,
)
from sazon.engine.models import (
    Availability,
    Intent,
    Language,
    MatrixLanguage,
    Outcome,
    Platform,
    QualityFlag,
    QuestionTopic,
    Schedule,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.state import Asking, DuplicateMatch
from sazon.engine.ui import (
    Confirm,
    MultiSelect,
    PhoneEntry,
    QuickReplies,
    format_date,
    format_years,
)
from sazon.engine.validators import ReasonCode

TODAY = date(2026, 10, 19)

SPAIN_FIELDS = {
    "full_name": "Lucía Fernández",
    "phone": "612 345 678",
    "email": "Lucia@Example.com",
    "has_license": True,
    "city": "Gràcia",
    "availability": ["full time"],
    "schedule": "mañanas",
    "experience_years": 2,
    "platforms": ["Glovo"],
    "start_date": "2026-10-26",
}


MATCH = DuplicateMatch(application_id="app-123", name="Lucía", matched_by="phone")


def turn(
    intent: Intent = Intent.ANSWER,
    *,
    frame: MatrixLanguage = MatrixLanguage.ES,
    english_ratio: float = 0.0,
    sentiment: Sentiment = Sentiment.NEUTRAL,
    question: str | None = None,
    request: Language | None = None,
    **fields: object,
) -> TurnExtraction:
    return TurnExtraction.model_validate(
        {
            "intent": intent,
            "fields": {name: {"value": value, "confidence": 0.9} for name, value in fields.items()},
            "language": Language.EN if frame == MatrixLanguage.EN else Language.ES,
            "matrix_language": frame,
            "english_ratio": english_ratio,
            "explicit_language_request": request,
            "code_switched": 0 < english_ratio < 1,
            "sentiment": sentiment,
            "question": question,
        }
    )


def say(state: ConversationState, extraction: TurnExtraction, **kwargs) -> tuple:
    return step(state, extraction, today=TODAY, **kwargs)


def consented(context: ConversationContext | None = None) -> ConversationState:
    state, _ = start(context)
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, AskField)
    assert action.field == FieldName.FULL_NAME
    return state


def no_match_looked_up() -> ConversationState:
    """Consented, with the open applications of the phone and email to come already looked up:
    the read-back of a value given with them comes before the lookup would."""
    return consented().model_copy(update={"duplicate_checked": True})


def at_license(context: ConversationContext | None = None) -> ConversationState:
    """A Spanish candidate who has given name and contact details."""
    state = consented(context)
    state, action = say(
        state,
        turn(full_name="Lucía Fernández", phone="612 345 678", email="lucia@example.com"),
    )
    assert isinstance(action, CheckDuplicate)
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert _asks(action, FieldName.HAS_LICENSE)
    return state


def at_confirm() -> ConversationState:
    state = consented()
    state, action = say(state, turn(**SPAIN_FIELDS))
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert isinstance(action, ConfirmSummary)
    return state


def _asks(action: Action | None, field: FieldName) -> bool:
    return isinstance(action, AskField) and action.field == field


def _asks_without_phone(action: Action, field: FieldName, *, has_email: bool = False) -> bool:
    """The phone was given up on: one line says so, then `field` is asked."""
    assert isinstance(action, SkipPhone)
    assert action.has_email is has_email
    return _asks(action.follow_up, field)


def _values(ui: object) -> set[str]:
    assert isinstance(ui, QuickReplies | MultiSelect)
    return {option.value for option in ui.options}


def at_city(context: ConversationContext | None = None) -> ConversationState:
    state, action = say(at_license(context), turn(has_license=True))
    assert _asks(action, FieldName.CITY)
    return state


def out_of_area() -> ConversationState:
    """Closed as out of area: named a place outside the service areas and confirmed it."""
    state, action = say(at_city(), turn(city="Ourense"))
    assert isinstance(action, ConfirmValue)
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)
    return state


def at_schedule() -> ConversationState:
    state, _ = say(at_city(), turn(city="Gràcia"))
    state, action = say(state, turn(availability=["full time"]))
    assert _asks(action, FieldName.SCHEDULE)
    return state


def test_happy_path_spain():
    state, action = start()
    assert isinstance(action, Greet)
    assert action.ai_disclosure
    assert action.privacy_notice
    assert isinstance(action.ui, QuickReplies)

    state, action = say(state, turn(Intent.CONFIRM))
    assert _asks(action, FieldName.FULL_NAME)

    state, action = say(state, turn(full_name="Lucía Fernández"))
    assert _asks(action, FieldName.PHONE)
    assert isinstance(action.ui, PhoneEntry)
    assert action.ui.selected == "ES"

    # The phone is looked up at once: a returning candidate is not asked for the email.
    state, action = say(state, turn(phone="612 345 678"))
    assert isinstance(action, CheckDuplicate)
    assert (action.phone, action.email) == ("+34612345678", None)
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert _asks(action, FieldName.EMAIL)

    # The email is looked up too: an application may have been started with it alone.
    state, action = say(state, turn(email="Lucia@Example.com"))
    assert isinstance(action, CheckDuplicate)
    assert (action.phone, action.email) == ("+34612345678", "lucia@example.com")

    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert _asks(action, FieldName.HAS_LICENSE)
    assert state.stage == Step.LICENSE

    answers = [
        ({"has_license": True}, FieldName.CITY),
        ({"city": "Gràcia"}, FieldName.AVAILABILITY),
        ({"availability": ["full time", "weekends"]}, FieldName.SCHEDULE),
        ({"schedule": "mañanas"}, FieldName.EXPERIENCE_YEARS),
        ({"experience_years": 2}, FieldName.PLATFORMS),
        ({"platforms": ["Glovo", "Uber Eats"]}, FieldName.START_DATE),
    ]
    for fields, next_field in answers:
        state, action = say(state, turn(**fields))
        assert _asks(action, next_field)

    state, action = say(state, turn(start_date="2026-10-26"))
    assert isinstance(action, ConfirmSummary)
    assert state.stage == Stage.CONFIRM
    assert action.profile.location_id == "es-bcn-gracia"
    assert action.profile.availability == [Availability.FULL_TIME, Availability.WEEKENDS]
    assert action.profile.schedule == [Schedule.MORNING]
    assert action.profile.platforms == [Platform.GLOVO, Platform.UBER_EATS]

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, FindModes)
    assert action.location_id == "es-bcn-gracia"
    assert state.outcome == Outcome.QUALIFIED
    assert state.stage == Stage.MODE
    assert state.quality_flags == ()


def test_happy_path_mexico_from_timezone_hint():
    state, action = start(ConversationContext(timezone="America/Mexico_City"))
    assert action.country == "MX"

    state, action = say(state, turn(Intent.CONFIRM))
    state, action = say(state, turn(full_name="Jorge Ramírez"))
    assert isinstance(action.ui, PhoneEntry)
    assert action.ui.selected == "MX"

    state, action = say(state, turn(phone="55 1234 5678", email="jorge@example.mx"))
    assert isinstance(action, CheckDuplicate)
    assert action.phone == "+525512345678"
    state, action = on_lookup(state, DuplicateChecked(match=None))

    state, action = say(
        state,
        turn(
            has_license="sí, tipo A",
            city="Zapopan",
            availability="medio tiempo",
            schedule="tardes",
            experience_years=0,
        ),
    )
    assert state.profile.has_license is True
    assert _asks(action, FieldName.START_DATE)

    state, action = say(state, turn(start_date="2026-11-02"))
    assert isinstance(action, ConfirmSummary)
    assert action.language == Language.ES
    assert action.country == "MX"
    assert state.profile.location_id == "mx-gdl-zapopan"
    assert state.profile.platforms == []


def test_english_candidate_gets_english_throughout():
    state, _ = start()
    english = MatrixLanguage.EN
    state, action = say(state, turn(Intent.CONFIRM, frame=english))
    assert action.language == Language.EN

    state, action = say(state, turn(frame=english, full_name="Sam Taylor"))
    state, action = say(state, turn(frame=english, phone="+34 612 345 678", email="s@t.com"))
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert action.language == Language.EN
    assert isinstance(action, AskField)


def test_all_fields_in_one_message():
    state = consented()

    state, action = say(state, turn(**SPAIN_FIELDS))
    assert isinstance(action, CheckDuplicate)

    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert isinstance(action, ConfirmSummary)
    assert isinstance(action.ui, Confirm)
    shown = {row.value for row in action.ui.fields}
    assert {"Lucía Fernández", "+34612345678", "lucia@example.com"} <= shown
    assert state.profile.email == "lucia@example.com"
    assert state.profile.experience_years == 2


def test_two_english_turns_switch_language():
    state = consented()
    state, action = say(state, turn(frame=MatrixLanguage.EN, full_name="Lucía Fernández"))
    assert action.language == Language.ES

    state, action = say(state, turn(frame=MatrixLanguage.EN, phone="612345678"))
    assert action.language == Language.EN
    assert state.profile.full_name == "Lucía Fernández"


def test_explicit_language_request_sticks():
    state = consented()
    state, action = say(state, turn(request=Language.EN, full_name="Lucía Fernández"))
    assert action.language == Language.EN

    for _ in range(3):
        state, action = say(state, turn(frame=MatrixLanguage.ES, phone="612345678"))
        assert action.language == Language.EN


def test_no_license_disqualifies_with_reapply_invitation():
    state = at_license()

    state, action = say(state, turn(has_license=False))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.NO_LICENSE
    assert action.invite_reapply
    assert not action.talent_pool_offer
    assert action.ui is None
    assert state.outcome == Outcome.DISQUALIFIED
    assert state.stage == Stage.DONE


@pytest.mark.parametrize("has_license", ["in_progress", "lo estoy sacando", "en trámite"])
def test_license_in_progress_counts_as_no(has_license):
    state = at_license()

    state, action = say(state, turn(has_license=has_license))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.LICENSE_IN_PROGRESS
    assert action.invite_reapply
    assert state.outcome == Outcome.DISQUALIFIED


def test_out_of_area_offers_talent_pool():
    state = at_license()
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city="Ourense"))
    assert isinstance(action, ConfirmValue)
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)
    assert action.reason == Close.OUT_OF_AREA
    assert action.talent_pool_offer
    assert not action.invite_reapply
    assert isinstance(action.ui, QuickReplies)
    assert _values(action.ui) == {"yes", "no"}

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, End)
    assert state.talent_pool_opt_in is True
    assert state.outcome == Outcome.DISQUALIFIED


def test_ambiguous_city_is_resolved_through_options():
    state = at_license(ConversationContext(timezone="America/Mexico_City"))
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city="GDL"))
    assert isinstance(action, Clarify)
    assert action.field == FieldName.CITY
    assert "mx-gdl-zapopan" in action.options
    assert isinstance(action.ui, QuickReplies)
    assert _values(action.ui) == set(action.options)

    state, action = say(state, turn(city="mx-gdl-zapopan"))
    assert _asks(action, FieldName.AVAILABILITY)
    assert state.profile.location_id == "mx-gdl-zapopan"
    assert state.pending_clarification is None


def test_city_with_too_many_matches_asks_for_the_zone():
    """A city with several locations: every one of them, as buttons on the web (SAZ-42)."""
    state = at_license()
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city="CDMX"))
    assert isinstance(action, Clarify)
    assert action.city == "Ciudad de México"
    assert len(action.options) == 8
    assert isinstance(action.ui, QuickReplies)
    assert _values(action.ui) == set(action.options)
    assert all(not option.label.startswith("Sazón") for option in action.ui.options)

    state, action = say(state, turn(city="Coyoacán"))
    assert state.profile.location_id == "mx-cdmx-coy"
    assert state.country == "MX"


def test_two_locations_of_one_city_are_offered_by_name():
    state = at_license()
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city="Valencia"))
    assert isinstance(action, Clarify)
    assert action.options == ("es-vlc-centro", "es-vlc-norte")
    assert action.city is None


def test_options_across_cities_beyond_the_buttons_get_none():
    state = at_license()
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city="Centro"))
    assert isinstance(action, Clarify)
    assert action.city is None
    assert len(action.options) > 4
    assert action.ui is None


@pytest.mark.parametrize("city", ["México, DF", "En México, DF.", "Ciudad de México, DF", "México"])
def test_the_city_alone_asks_for_the_zone_of_that_city(city):
    """SAZ-42, from the first real call: "En México, DF" got two of the eight alcaldías."""
    state = at_license(ConversationContext(timezone="America/Mexico_City"))
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city=city))
    assert isinstance(action, Clarify)
    assert action.city == "Ciudad de México"
    assert len(action.options) == 8


@pytest.mark.parametrize(
    "city",
    ["México, DF, Benito Juárez", "Benito Juárez", "Mmm, Benito Juárez.", "alcaldía Benito Juárez"],
)
def test_a_zone_named_directly_needs_no_confirmation(city):
    """SAZ-42: "Benito Juárez" after the alcaldía question was confirmed twice."""
    state = at_license(ConversationContext(timezone="America/Mexico_City"))
    state, _ = say(state, turn(has_license=True))
    state, _ = say(state, turn(city="México, DF"))

    state, action = say(state, turn(city=city))
    assert state.profile.location_id == "mx-cdmx-bju"
    assert _asks(action, FieldName.AVAILABILITY)


def test_a_loose_match_is_confirmed_once_and_a_yes_picks_it():
    state = at_license(ConversationContext(timezone="America/Mexico_City"))
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city="Sapopa"))
    assert isinstance(action, ConfirmValue)
    assert action.field == FieldName.CITY
    assert action.location_id == "mx-gdl-zapopan"
    assert isinstance(action.ui, QuickReplies)
    assert state.retries.get(FieldName.CITY, 0) == 0

    state, action = say(state, turn(Intent.CONFIRM))
    assert state.profile.location_id == "mx-gdl-zapopan"
    assert _asks(action, FieldName.AVAILABILITY)


def test_a_yes_that_repeats_the_loose_spelling_is_not_read_back_again():
    state = at_license(ConversationContext(timezone="America/Mexico_City"))
    state, _ = say(state, turn(has_license=True))
    state, action = say(state, turn(city="Cancum"))
    assert isinstance(action, ConfirmValue)
    assert action.location_id == "mx-cun-centro"

    state, action = say(state, turn(Intent.CONFIRM, city="Cancum"))

    assert state.profile.location_id == "mx-cun-centro"
    assert state.pending_confirmation is None
    assert _asks(action, FieldName.AVAILABILITY)


def test_a_no_to_a_loose_match_asks_the_city_again_and_never_offers_it_twice():
    state = at_license(ConversationContext(timezone="America/Mexico_City"))
    state, _ = say(state, turn(has_license=True))

    state, action = say(state, turn(city="Santander"))
    assert isinstance(action, ConfirmValue)
    assert action.location_id == "mx-cdmx-ao"

    state, action = say(state, turn(Intent.CORRECTION))
    assert state.declined_locations == ("mx-cdmx-ao",)
    assert _asks(action, FieldName.CITY)
    assert state.profile.location_id is None

    state, action = say(state, turn(city="Santander"))
    assert isinstance(action, ConfirmValue)
    assert action.location_id is None
    assert action.value == "Santander"


def test_a_new_place_in_reply_to_a_loose_match_is_read_as_usual():
    state = at_license(ConversationContext(timezone="America/Mexico_City"))
    state, _ = say(state, turn(has_license=True))
    state, _ = say(state, turn(city="Sapopa"))

    state, _ = say(state, turn(Intent.CONFIRM, city="Coyoacán"))
    assert state.profile.location_id == "mx-cdmx-coy"
    assert state.declined_locations == ()


def test_start_beyond_90_days_is_confirmed_once():
    state = no_match_looked_up()
    fields = {**SPAIN_FIELDS, "start_date": (TODAY + timedelta(days=120)).isoformat()}
    state, action = say(state, turn(**fields))

    assert isinstance(action, ConfirmValue)
    assert action.field == FieldName.START_DATE
    assert state.profile.start_date is None

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.start_date == TODAY + timedelta(days=120)
    assert state.profile.late_start_days == 120


def test_start_beyond_90_days_rejected_is_asked_again():
    state = no_match_looked_up()
    fields = {**SPAIN_FIELDS, "start_date": (TODAY + timedelta(days=120)).isoformat()}
    state, _ = say(state, turn(**fields))

    state, action = say(state, turn(Intent.ANSWER))

    assert _asks(action, FieldName.START_DATE)


def test_question_mid_flow_is_answered_then_flow_resumes():
    state = at_license()

    state, action = say(state, turn(Intent.QUESTION, question="¿Cuánto se cobra?"))
    assert isinstance(action, AnswerQuestion)
    assert action.question == "¿Cuánto se cobra?"
    assert action.follow_up is not None
    assert _asks(action.follow_up, FieldName.HAS_LICENSE)
    assert state.retries == {}

    state, action = say(state, turn(has_license=True))
    assert _asks(action, FieldName.CITY)


def test_a_question_that_asks_several_things_keeps_its_parts():
    state = at_license()
    asked = turn(Intent.QUESTION, question="¿Cuánto se cobra y dan casco?").model_copy(
        update={"sub_questions": ["¿Cuánto se cobra?", "¿Dan casco?"]}
    )

    state, action = say(state, asked)

    assert isinstance(action, AnswerQuestion)
    assert action.parts == ("¿Cuánto se cobra?", "¿Dan casco?")
    assert action.follow_up is not None
    assert _asks(action.follow_up, FieldName.HAS_LICENSE)


def test_consent_with_a_multi_part_question_answers_every_part_after_it():
    state, _ = start()
    asked = turn(Intent.CONFIRM, question="¿Cuánto se paga y dan casco?").model_copy(
        update={"sub_questions": ["¿Cuánto se paga?", "¿Dan casco?"]}
    )

    state, action = say(state, asked)

    assert isinstance(action, AnswerQuestion)
    assert action.parts == ("¿Cuánto se paga?", "¿Dan casco?")


def test_question_with_an_answer_keeps_the_answer():
    state = consented()

    state, action = say(
        state, turn(Intent.QUESTION, question="¿Hay que tener moto?", full_name="Ana López")
    )

    assert isinstance(action, AnswerQuestion)
    assert action.follow_up is not None
    assert _asks(action.follow_up, FieldName.PHONE)


def test_off_topic_is_deflected_back_to_the_question():
    state = at_license()

    state, action = say(state, turn(Intent.OFF_TOPIC))

    assert isinstance(action, Deflect)
    assert action.reason == DeflectKind.OFF_TOPIC
    assert action.follow_up is not None
    assert _asks(action.follow_up, FieldName.HAS_LICENSE)


def test_correcting_the_city_reruns_the_gate():
    state = at_confirm()

    state, action = say(state, turn(Intent.CORRECTION, city="Sevila"))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.location_id == "es-sev-centro"

    state, action = say(state, turn(Intent.CORRECTION, city="Ourense"))
    assert isinstance(action, ConfirmValue)
    assert state.outcome is None

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)
    assert action.reason == Close.OUT_OF_AREA
    assert state.outcome == Outcome.DISQUALIFIED


def test_correcting_availability_at_confirm_shows_the_summary_again():
    state = at_confirm()

    state, action = say(state, turn(Intent.CORRECTION, availability="solo fines de semana"))

    assert isinstance(action, ConfirmSummary)
    assert state.profile.availability == [Availability.WEEKENDS]
    assert state.outcome is None


def test_first_abuse_deescalates_second_ends():
    state = at_license()

    state, action = say(state, turn(Intent.ABUSE, sentiment=Sentiment.FRUSTRATED))
    assert isinstance(action, DeEscalate)
    assert action.follow_up is not None
    assert _asks(action.follow_up, FieldName.HAS_LICENSE)
    assert state.abuse_strikes == 1
    assert state.outcome is None

    state, action = say(state, turn(has_license=True))
    assert _asks(action, FieldName.CITY)

    state, action = say(state, turn(Intent.ABUSE))
    assert isinstance(action, End)
    assert action.reason == EndReason.ABUSE
    assert QualityFlag.ABUSE in state.quality_flags
    assert state.outcome == Outcome.ABANDONED
    assert state.drop_off_stage == Step.CITY


def test_nothing_after_a_disqualifying_answer_is_applied():
    """A no to the licence closes the application: a place named in the same message is not
    read back, and the reason stays the licence."""
    state, action = say(at_license(), turn(has_license=False, city="Ourense"))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.NO_LICENSE
    assert state.disqualify_reason == Close.NO_LICENSE
    assert state.pending_confirmation is None
    assert state.profile.location_id is None


def test_nothing_is_stored_after_a_yes_that_closes_as_out_of_area():
    state, action = say(at_city(), turn(city="Ourense"))
    assert isinstance(action, ConfirmValue)

    state, action = say(state, turn(Intent.CONFIRM, availability=["full time"]))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.OUT_OF_AREA
    assert state.profile.availability is None


def test_frustration_simplifies_then_offers_a_pause():
    state = at_license()
    state, _ = say(state, turn(has_license=True))
    state, _ = say(state, turn(city="Gràcia"))

    state, action = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
    assert _asks(action, FieldName.AVAILABILITY)
    assert action.simplify
    assert isinstance(action.ui, MultiSelect)

    state, action = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
    assert isinstance(action, OfferPause)
    assert QualityFlag.FRUSTRATION in state.quality_flags

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, End)
    assert action.reason == EndReason.PAUSED
    assert state.outcome is None
    assert state.stage == Step.AVAILABILITY


def test_a_complaint_at_a_gate_is_not_a_failed_answer():
    """QA sweep 2026-10-21: complaints counted as failed answers and could close the
    application as unresolved; they feed the pause offer instead."""
    state = at_license()
    for _ in range(3):
        state, _ = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
        state = state.model_copy(update={"pause_offered": False})
    assert state.outcome is None
    assert state.retries.get(FieldName.HAS_LICENSE, 0) == 0


def test_pause_declined_continues_the_flow():
    state = at_license()
    state, _ = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
    state, action = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
    assert isinstance(action, OfferPause)

    state, action = say(state, turn(has_license=True))

    assert _asks(action, FieldName.CITY)
    assert not action.simplify


def test_invalid_three_times_leaves_a_field_empty():
    state = at_license()
    state, _ = say(state, turn(has_license=True))
    state, _ = say(state, turn(city="Gràcia"))
    state, _ = say(state, turn(availability="full time"))

    state, action = say(state, turn(schedule="cuando pueda"))
    assert isinstance(action, AskField)
    assert (action.retry, action.hint, action.ui) == (1, ReasonCode.SCHEDULE_INVALID, None)

    state, action = say(state, turn(schedule="depende"))
    assert isinstance(action, AskField)
    assert action.retry == 2
    assert set(action.options) == set(Schedule)
    assert isinstance(action.ui, MultiSelect)
    assert _values(action.ui) == set(Schedule)
    assert action.ui.exclusive == (Schedule.FLEXIBLE,)

    state, action = say(state, turn(schedule="no sé"))
    assert _asks(action, FieldName.EXPERIENCE_YEARS)
    assert state.profile.schedule is None
    assert FieldName.SCHEDULE in state.skipped
    assert QualityFlag.UNRESOLVED_FIELD in state.quality_flags


def test_a_first_name_alone_asks_for_the_surname():
    state, action = say(consented(), turn(full_name="Carlos"))

    assert isinstance(action, AskField)
    assert action.field == FieldName.FULL_NAME
    assert action.hint == ReasonCode.NAME_NEEDS_SURNAME
    assert state.profile.full_name is None


def test_a_surname_alone_after_the_first_name_completes_it():
    state, _ = say(consented(), turn(full_name="Carlos"))

    state, action = say(state, turn(full_name="Sánchez"))

    assert state.profile.full_name == "Carlos Sánchez"
    assert not _asks(action, FieldName.FULL_NAME)


def test_the_whole_name_in_reply_to_the_surname_question_is_taken_as_given():
    state, _ = say(consented(), turn(full_name="Carlos"))

    state, _ = say(state, turn(full_name="Carlos Sánchez Pérez"))

    assert state.profile.full_name == "Carlos Sánchez Pérez"


def test_a_compound_first_name_alone_asks_for_the_surname():
    state, action = say(consented(), turn(full_name="Juan Carlos"))

    assert isinstance(action, AskField)
    assert action.field == FieldName.FULL_NAME
    assert action.hint == ReasonCode.NAME_NEEDS_SURNAME
    assert state.profile.full_name is None


@pytest.mark.parametrize("first", ["Juan Carlos", "Carlos"])
def test_two_surnames_after_the_first_name_are_joined_to_it(first):
    state, _ = say(consented(), turn(full_name=first))

    state, action = say(state, turn(full_name="Sánchez Pérez"))

    assert state.profile.full_name == f"{first} Sánchez Pérez"
    assert not _asks(action, FieldName.FULL_NAME)


def test_the_whole_name_after_a_compound_first_name_is_taken_as_given():
    state, _ = say(consented(), turn(full_name="Juan Carlos"))

    state, _ = say(state, turn(full_name="juan carlos Sánchez Pérez"))

    assert state.profile.full_name == "juan carlos Sánchez Pérez"


def test_an_answer_restarting_with_a_word_of_the_first_name_is_taken_as_written():
    state, _ = say(consented(), turn(full_name="Juan Carlos"))

    state, action = say(state, turn(full_name="juan Sánchez"))

    assert state.profile.full_name == "juan Sánchez"
    assert not _asks(action, FieldName.FULL_NAME)


def test_another_full_name_after_a_first_name_is_still_joined_to_it():
    state, _ = say(consented(), turn(full_name="Carlos"))

    state, _ = say(state, turn(full_name="Pedro Gómez"))

    assert state.profile.full_name == "Carlos Pedro Gómez"


def test_no_surname_after_the_last_try_closes_and_can_start_again():
    state, _ = say(consented(), turn(full_name="Carlos"))
    state, _ = say(state, turn(full_name="Carlos"))
    state, action = say(state, turn(full_name="Carlos"))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.UNRESOLVED_NAME
    assert state.outcome == Outcome.DISQUALIFIED
    assert not state.reopenable


def test_a_phone_alone_is_enough_contact():
    state = consented()
    state, _ = say(state, turn(full_name="Lucía Fernández", phone="612345678"))
    state, _ = say(state, turn(email="lucia@"))

    state, action = say(state, turn(email="lucia at gmail"))

    assert isinstance(action, CheckDuplicate)
    assert state.outcome is None


def test_the_data_in_a_message_that_closes_at_the_license_is_kept():
    """QA sweep 2026-10-21: name, phone and email in the same message as "no tengo carnet"
    were lost, and the recruiter saw "Sin nombre"."""
    state = consented()
    state, action = say(
        state,
        turn(
            full_name="Lucía Fernández",
            phone="612 345 678",
            email="lucia@example.com",
            has_license=False,
        ),
    )

    assert isinstance(action, Disqualify)
    assert action.reason == Close.NO_LICENSE
    profile = state.profile
    assert (profile.full_name, profile.phone, profile.email) == (
        "Lucía Fernández",
        "+34612345678",
        "lucia@example.com",
    )
    assert profile.has_license is False


def test_experience_corrected_from_none_to_some_asks_the_platforms():
    """QA sweep 2026-10-21: "ninguna" stored the platforms as none, and a correction to 2
    years at the summary went back to the summary without asking them."""
    fields = {**SPAIN_FIELDS, "experience_years": "ninguna"}
    del fields["platforms"]
    state, _ = say(consented(), turn(**fields))
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.platforms == []

    state, action = say(state, turn(Intent.CORRECTION, experience_years=2))

    assert _asks(action, FieldName.PLATFORMS)
    state, action = say(state, turn(platforms=["Glovo"]))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.platforms == [Platform.GLOVO]


def at_start_date() -> ConversationState:
    fields = {**SPAIN_FIELDS}
    del fields["start_date"]
    state, _ = say(consented(), turn(**fields))
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert _asks(action, FieldName.START_DATE)
    return state


def test_a_day_and_month_already_past_is_read_back_with_today():
    """D6: "el 1 de octubre" on 19 October, read as next year's, says today's date."""
    state, action = say(at_start_date(), turn(start_date="2027-10-01"))

    assert isinstance(action, ConfirmValue)
    assert (action.value, action.today) == ("2027-10-01", TODAY)

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.start_date == date(2027, 10, 1)


def test_a_no_to_a_day_and_month_already_past_asks_again():
    state, _ = say(at_start_date(), turn(start_date="2027-10-01"))

    state, action = say(state, turn(Intent.CORRECTION))

    assert _asks(action, FieldName.START_DATE)
    assert state.profile.start_date is None


def test_a_late_date_whose_day_has_not_come_this_year_is_read_back_without_today():
    _, action = say(at_start_date(), turn(start_date="2027-11-02"))

    assert isinstance(action, ConfirmValue)
    assert action.today is None


def test_a_mistyped_email_domain_is_asked_about_once():
    """D2: "gmail.con" is read back as gmail.com; a yes keeps the corrected address."""
    state, action = say(at_email(), turn(email="LUCIA@GMAIL.CON"))
    assert isinstance(action, ConfirmValue)
    assert (action.field, action.value) == (FieldName.EMAIL, "lucia@gmail.com")
    assert state.profile.email is None

    state, action = say(state, turn(Intent.CONFIRM))

    assert state.profile.email == "lucia@gmail.com"
    assert isinstance(action, CheckDuplicate)


def test_a_no_to_the_corrected_email_asks_for_it_again_without_a_failure():
    state, _ = say(at_email(), turn(email="lucia@gmail.con"))

    state, action = say(state, turn(Intent.CORRECTION))

    assert _asks(action, FieldName.EMAIL)
    assert state.retries.get(FieldName.EMAIL, 0) == 0
    state, action = say(state, turn(email="lucia@empresa.es"))
    assert state.profile.email == "lucia@empresa.es"


def test_by_voice_a_mistyped_email_domain_is_kept_corrected():
    state = at_email().model_copy(update={"voice": True})

    state, _ = say(state, turn(email="lucia@gmail.con"))

    assert state.profile.email == "lucia@gmail.com"
    assert state.pending_confirmation is None


def test_neither_phone_nor_email_closes():
    state, _ = say(consented(), turn(full_name="Lucía Fernández"))
    state, _ = say(state, turn(phone="123"))
    state, _ = say(state, turn(phone="123"))
    state, action = say(state, turn(phone="123"))
    assert state.outcome is None
    assert _asks_without_phone(action, FieldName.EMAIL)

    state, _ = say(state, turn(email="lucia@"))
    state, action = say(state, turn(email="lucia at gmail"))

    assert isinstance(action, Disqualify)
    assert action.reason == DisqualifyReason.NO_CONTACT
    assert not state.reopenable


def at_email() -> ConversationState:
    state = consented()
    state, action = say(state, turn(full_name="Lucía Fernández", phone="612345678"))
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert _asks(action, FieldName.EMAIL)
    return state


def declines(intent: Intent = Intent.ANSWER, **kwargs: Any) -> TurnExtraction:
    return turn(intent, **kwargs).model_copy(update={"declines_field": True})


@pytest.mark.parametrize(
    "refusal",
    [
        declines(),
        declines(Intent.QUESTION, question="¿Para qué queréis mi correo?"),
        # Read as wanting to stop, but only the email was refused.
        declines(Intent.OPT_OUT),
    ],
)
def test_refusing_the_email_with_a_phone_in_carries_on_without_it(refusal: TurnExtraction):
    """Live test 4f64892c (SAZ-72): "para que lo quieres? no quiero dar mis datos"."""
    state, action = say(at_email(), refusal)

    assert isinstance(action, SkipEmail)
    follow_up = action.follow_up
    assert isinstance(follow_up, AskField)
    assert follow_up.field == FieldName.HAS_LICENSE
    assert action.ui == follow_up.ui
    assert FieldName.EMAIL in state.skipped
    assert state.outcome is None
    assert state.quality_flags == ()
    state, action = say(state, turn(has_license=True))
    assert _asks(action, FieldName.CITY)


def test_refusing_the_email_keeps_the_other_data_in_the_message():
    state, action = say(at_email(), declines(has_license=True))
    assert isinstance(action, SkipEmail)
    assert isinstance(action.follow_up, AskField)
    assert action.follow_up.field == FieldName.CITY
    assert state.profile.has_license is True


def test_refusing_the_email_without_a_phone_still_ends_without_contact():
    state, _ = say(consented(), turn(full_name="Lucía Fernández"))
    state, _ = say(state, turn(phone="123"))
    state, _ = say(state, turn(phone="123"))
    state, action = say(state, turn(phone="123"))
    assert _asks_without_phone(action, FieldName.EMAIL)

    state, action = say(state, declines())
    assert _asks(action, FieldName.EMAIL)
    assert isinstance(action, AskField)
    assert action.hint == ReasonCode.DECLINED
    state, action = say(state, declines())

    assert isinstance(action, Disqualify)
    assert action.reason == DisqualifyReason.NO_CONTACT


def at_phone() -> ConversationState:
    state, action = say(consented(), turn(full_name="Lucía Fernández"))
    assert _asks(action, FieldName.PHONE)
    return state


def test_refusing_the_phone_with_the_email_in_the_message_carries_on_without_it():
    """QA sweep 2026-10-21: "El móvil prefiero no darlo, pero mi correo es ..." was asked for
    the phone again. One of the two is enough (D4: one line, then the next question)."""
    state, action = say(at_phone(), declines(email="lucia@example.com"))
    assert isinstance(action, CheckDuplicate)
    assert (action.phone, action.email) == (None, "lucia@example.com")

    state, action = on_lookup(state, DuplicateChecked(match=None))

    assert _asks_without_phone(action, FieldName.HAS_LICENSE, has_email=True)
    assert FieldName.PHONE in state.skipped
    assert state.retries.get(FieldName.PHONE, 0) == 0
    assert state.contact_notice is None


@pytest.mark.parametrize("intent", [Intent.ANSWER, Intent.OPT_OUT])
def test_refusing_the_phone_without_an_email_explains_once_then_asks_the_email(intent: Intent):
    """Read as wanting to stop, but only the phone was refused: never an opt-out close."""
    state, action = say(at_phone(), declines(intent))
    assert isinstance(action, AskField)
    assert (action.field, action.hint) == (FieldName.PHONE, ReasonCode.DECLINED)
    assert state.outcome is None

    state, action = say(state, declines(intent, sentiment=Sentiment.FRUSTRATED))
    assert isinstance(action, AskField)
    assert (action.field, action.hint) == (FieldName.PHONE, ReasonCode.DECLINED_AGAIN)

    state, action = say(state, declines(intent, sentiment=Sentiment.FRUSTRATED))
    assert _asks_without_phone(action, FieldName.EMAIL)

    state, action = say(state, declines(intent))
    assert isinstance(action, AskField)
    assert (action.field, action.hint) == (FieldName.EMAIL, ReasonCode.DECLINED)
    state, action = say(state, declines(intent))
    assert isinstance(action, Disqualify)
    assert action.reason == DisqualifyReason.NO_CONTACT


@pytest.mark.parametrize(
    "field",
    [
        FieldName.EXPERIENCE_YEARS,
        FieldName.AVAILABILITY,
        FieldName.SCHEDULE,
        FieldName.START_DATE,
    ],
)
def test_a_refused_optional_field_is_asked_three_times_then_left_out(field: FieldName):
    """D5: asked again with why it helps, never "I didn't understand"; after the third
    refusal the screening carries on without it, flagged."""
    state = at_license()
    for answer in (turn(has_license=True), turn(city="Gràcia")):
        state, action = say(state, answer)
    filled = {
        "availability": [Availability.FULL_TIME],
        "schedule": [Schedule.MORNING],
        "experience_years": 2.0,
        "platforms": [Platform.GLOVO],
        "start_date": TODAY,
    }
    profile = state.profile.model_copy(update={**filled, field.value: None})
    state = state.model_copy(update={"profile": profile, "phase": Asking(field=field)})

    state, action = say(state, declines())
    assert isinstance(action, AskField)
    assert (action.field, action.retry, action.hint) == (field, 1, ReasonCode.DECLINED)
    state, action = say(state, declines())
    assert isinstance(action, AskField)
    assert (action.field, action.hint) == (field, ReasonCode.DECLINED_AGAIN)
    state, action = say(state, declines())

    assert not _asks(action, field)
    assert field in state.skipped
    assert QualityFlag.UNRESOLVED_FIELD in state.quality_flags
    assert state.outcome is None


def test_a_refused_city_says_why_it_is_needed_and_still_counts():
    """D7: the reason first, then the question again; the third refusal closes."""
    state, _ = say(at_license(), turn(has_license=True))
    state, action = say(state, declines())
    assert isinstance(action, AskField)
    assert (action.field, action.hint) == (FieldName.CITY, ReasonCode.DECLINED)
    state, _ = say(state, declines())
    state, action = say(state, declines())

    assert isinstance(action, Disqualify)
    assert action.reason == Close.UNRESOLVED_CITY


def test_email_is_dropped_after_two_failures():
    state = consented()
    state, action = say(state, turn(full_name="Lucía Fernández", phone="612345678"))
    assert isinstance(action, CheckDuplicate)
    state, _ = on_lookup(state, DuplicateChecked(match=None))

    state, action = say(state, turn(email="lucia@"))
    assert _asks(action, FieldName.EMAIL)

    # No email to look up: the phone was, so the screening carries on, saying so (D4).
    state, action = say(state, turn(email="lucia at gmail"))
    assert isinstance(action, SkipEmail)
    assert _asks(action.follow_up, FieldName.HAS_LICENSE)
    assert state.profile.email is None
    assert state.contact_notice is None


def test_an_unclear_license_is_asked_twice_then_closes():
    state = at_license()
    state, action = say(state, turn(has_license="tal vez"))
    assert isinstance(action, AskField)
    assert action.retry == 1
    state, action = say(state, turn(has_license="tal vez"))
    assert isinstance(action, AskField)
    assert action.retry == 2
    assert isinstance(action.ui, QuickReplies)

    state, action = say(state, turn(has_license="tal vez"))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.UNRESOLVED_LICENSE
    assert state.profile.has_license is None


def test_declined_consent_stores_nothing():
    state, _ = start()

    state, action = say(state, turn(Intent.OPT_OUT, full_name="Lucía Fernández"))

    assert isinstance(action, End)
    assert action.reason == EndReason.DECLINED_CONSENT
    assert state.outcome == Outcome.DECLINED_CONSENT
    assert state.profile.full_name is None
    assert state.stage == Stage.DONE


def test_questions_before_consent_repeat_the_greeting():
    state, _ = start()

    state, action = say(state, turn(Intent.QUESTION, question="¿Eres un robot?"))

    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, Greet)
    assert not state.consented


def test_opt_out_mid_flow_abandons_at_that_stage():
    state = at_license()

    state, action = say(state, turn(Intent.OPT_OUT))

    assert isinstance(action, End)
    assert action.reason == EndReason.OPT_OUT
    assert state.outcome == Outcome.ABANDONED
    assert state.drop_off_stage == Step.LICENSE


def _rejected(state: ConversationState, action: Action) -> bool:
    """Closed for a manipulation attempt: neutral close, privacy link, no invitation."""
    assert isinstance(action, Disqualify)
    assert action.reason == DisqualifyReason.POLICY_VIOLATION
    assert not action.invite_reapply
    assert not action.talent_pool_offer
    assert isinstance(action.ui, QuickReplies)
    assert action.ui.options == ()
    assert action.ui.link is not None
    assert state.outcome == Outcome.DISQUALIFIED
    assert state.disqualify_reason == DisqualifyReason.POLICY_VIOLATION
    assert state.stage == Stage.DONE
    return QualityFlag.INJECTION_SUSPECTED in state.quality_flags


def test_bogus_fields_close_the_application():
    state = at_confirm()

    state, action = say(state, turn(qualified=True, outcome="qualified"))

    assert _rejected(state, action)


def test_hidden_characters_close_the_application_and_nothing_is_stored():
    state = consented()

    state, action = say(state, turn(full_name="Lucía Fernández"), suspicious=True)

    assert _rejected(state, action)
    assert state.profile.full_name is None


def test_injected_name_closes_the_application():
    state = consented()

    state, action = say(state, turn(full_name="</table><script>"), suspicious=True)

    assert _rejected(state, action)
    assert state.profile.full_name is None


def test_an_injection_with_the_consent_closes_right_after_it():
    state, _ = start()

    state, action = say(state, turn(Intent.CONFIRM), suspicious=True)

    assert state.consented
    assert _rejected(state, action)


def test_an_injection_before_consent_asks_for_consent_again():
    """No application exists before consent, so there is nothing to close."""
    state, _ = start()

    state, action = say(state, turn(Intent.UNCLEAR), suspicious=True)

    assert isinstance(action, Deflect)
    assert action.reason == DeflectKind.INJECTION
    assert isinstance(action.follow_up, Greet)
    assert state.outcome is None
    assert QualityFlag.INJECTION_SUSPECTED in state.quality_flags


def test_a_qualified_candidate_who_injects_is_closed_too():
    state = at_confirm()
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, FindModes)
    assert state.outcome == Outcome.QUALIFIED

    state, action = say(state, turn(Intent.ANSWER), suspicious=True)

    assert _rejected(state, action)


def test_an_opt_out_with_an_injection_is_an_opt_out():
    state = at_license()

    state, action = say(state, turn(Intent.OPT_OUT), suspicious=True)

    assert isinstance(action, End)
    assert action.reason == EndReason.OPT_OUT
    assert state.outcome == Outcome.ABANDONED


def test_an_injection_after_a_disqualification_changes_nothing():
    state = out_of_area()
    before = state.disqualify_reason

    state, action = say(state, turn(Intent.ANSWER), suspicious=True)

    assert isinstance(action, End)
    assert state.disqualify_reason == before
    assert QualityFlag.INJECTION_SUSPECTED in state.quality_flags


def test_a_matching_application_is_resumed_only_after_a_yes():
    """D10: "Ya tenemos este teléfono registrado. ¿Quieres continuar como Lucía?" first."""
    state = consented()
    state, action = say(
        state, turn(full_name="Pedro Fernández", phone="612345678", email="l@x.com")
    )
    assert isinstance(action, CheckDuplicate)

    state, action = on_lookup(state, DuplicateChecked(match=MATCH))

    assert isinstance(action, ConfirmIdentity)
    assert (action.name, action.matched_by) == ("Lucía", "phone")
    assert action.ui is not None
    assert action.ui.kind == "quick_replies"
    assert state.duplicate_of is None
    assert current_prompt(state) == action  # what a nudge repeats
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, ResumeExisting)
    assert action.application_id == "app-123"
    assert state.duplicate_of == "app-123"
    assert state.stage == Stage.DONE


@pytest.mark.parametrize("no", [turn(Intent.CORRECTION), turn(Intent.ANSWER, full_name="Pedro")])
def test_a_no_to_the_match_carries_on_as_a_new_application(no: TurnExtraction):
    state = consented()
    state, _ = say(state, turn(full_name="Pedro Fernández", phone="612345678"))
    state, _ = on_lookup(state, DuplicateChecked(match=MATCH))

    state, action = say(state, no)

    assert _asks(action, FieldName.EMAIL)
    assert state.duplicate_of is None
    assert state.declined_applications == ("app-123",)
    assert state.profile.full_name == "Pedro Fernández"
    # The email finds the same application again: it is not offered a second time.
    state, action = say(state, turn(email="l@x.com"))
    assert isinstance(action, CheckDuplicate)
    state, action = on_lookup(state, DuplicateChecked(match=MATCH))
    assert _asks(action, FieldName.HAS_LICENSE)


def test_an_unclear_answer_to_the_match_is_asked_once_more_then_read_as_a_no():
    state = consented()
    state, _ = say(state, turn(full_name="Pedro Fernández", phone="612345678"))
    state, _ = on_lookup(state, DuplicateChecked(match=MATCH))

    state, action = say(state, turn(Intent.UNCLEAR))
    assert isinstance(action, ConfirmIdentity)
    state, action = say(state, turn(Intent.QUESTION, question="¿Qué es esto?"))
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, ConfirmIdentity)
    state, action = say(state, turn(Intent.UNCLEAR))
    assert _asks(action, FieldName.EMAIL)
    assert state.declined_applications == ("app-123",)


def test_the_match_is_asked_in_the_register_of_its_location():
    """QA batch 3: back through a Spanish posting to a Mexican application with its location
    chosen, the question is in usted, as everything after the yes."""
    state = consented(ConversationContext(country="ES"))
    state, _ = say(state, turn(full_name="Jorge Ramírez", phone="612345678"))
    state, action = on_lookup(
        state, DuplicateChecked(match=MATCH.model_copy(update={"country": "MX"}))
    )
    assert isinstance(action, ConfirmIdentity)
    assert (action.country, state.country) == ("MX", "ES")


def test_a_new_application_after_a_policy_close_starts_with_the_warning():
    """D11: allowed again, with a fixed warning before the next question, said once."""
    state = consented()
    state, _ = say(state, turn(full_name="Tomás Vargas", phone="612345678"))

    state, action = on_lookup(state, DuplicateChecked(match=None, policy_closes=1))

    assert isinstance(action, WarnPolicy)
    assert _asks(action.follow_up, FieldName.EMAIL)
    assert action.ui == action.follow_up.ui
    state, action = say(state, turn(email="t@x.com"))
    assert isinstance(action, CheckDuplicate)
    state, action = on_lookup(state, DuplicateChecked(match=None, policy_closes=1))
    assert _asks(action, FieldName.HAS_LICENSE)  # warned once


@pytest.mark.parametrize("closes", [3, 4])
def test_the_third_policy_close_stops_any_new_application(closes: int):
    """D11: from the third manipulation close on, no screening: a neutral close."""
    state = consented()
    state, _ = say(state, turn(full_name="Tomás Vargas", phone="612345678"))

    state, action = on_lookup(state, DuplicateChecked(match=MATCH, policy_closes=closes))

    assert isinstance(action, Disqualify)
    assert action.blocked
    assert action.reason == DisqualifyReason.POLICY_VIOLATION
    assert (state.outcome, state.stage) == (Outcome.DISQUALIFIED, Stage.DONE)
    state, action = say(state, turn(Intent.CORRECTION, has_license=True))
    assert state.outcome == Outcome.DISQUALIFIED  # nothing reopens it


def test_the_phone_finds_an_application_before_the_email_is_asked():
    """Live test 4f64892c (SAZ-72): the email was asked, argued over, then thrown away."""
    state = consented()
    state, action = say(state, turn(full_name="Lucía Fernández", phone="612345678"))
    assert isinstance(action, CheckDuplicate)
    assert (action.phone, action.email) == ("+34612345678", None)

    state, action = on_lookup(state, DuplicateChecked(match=MATCH))
    assert isinstance(action, ConfirmIdentity)
    state, action = say(state, turn(Intent.CONFIRM))

    assert isinstance(action, ResumeExisting)
    assert state.profile.email is None


def test_without_a_phone_the_email_alone_is_looked_up_once():
    state = consented()
    state, action = say(state, turn(full_name="Lucía Fernández"))
    for _ in range(3):
        state, action = say(state, turn(phone="12"))
    assert _asks_without_phone(action, FieldName.EMAIL)
    assert not state.duplicate_checked

    state, action = say(state, turn(email="l@x.com"))
    assert isinstance(action, CheckDuplicate)
    assert (action.phone, action.email) == (None, "l@x.com")
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert _asks(action, FieldName.HAS_LICENSE)
    state, action = say(state, turn(has_license=True))
    assert _asks(action, FieldName.CITY)


def test_step_does_not_change_the_input_state():
    state = at_license()
    before = state.model_dump()

    first = say(state, turn(has_license=True, city="Gràcia"))
    second = say(state, turn(has_license=True, city="Gràcia"))

    assert state.model_dump() == before
    assert first == second


def test_consent_requires_an_explicit_yes():
    state, _ = start()

    state, action = say(state, turn(Intent.ANSWER, full_name="Lucía Fernández"))

    assert isinstance(action, Greet)
    assert action.retry == 1
    assert not state.consented
    assert state.profile.full_name is None


def test_explicit_yes_starts_the_screening():
    state, _ = start()

    state, action = say(state, turn(Intent.CONFIRM))

    assert state.consented
    assert isinstance(action, AskField)
    assert action.field == FieldName.FULL_NAME


def frustrated_until_pause(state: ConversationState) -> ConversationState:
    state, _ = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
    state, action = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
    assert isinstance(action, OfferPause)
    return state


def test_declining_the_pause_without_an_answer_asks_the_question_again():
    """ "No, sigamos" answers the pause offer, so it is not a failed answer to the question."""
    state, _ = say(at_city(), turn(city="Gràcia"))
    state = frustrated_until_pause(state)
    retries = dict(state.retries)

    state, action = say(state, turn(Intent.ANSWER))

    assert _asks(action, FieldName.AVAILABILITY)
    assert state.retries == retries
    assert FieldName.AVAILABILITY not in state.skipped
    assert QualityFlag.UNRESOLVED_FIELD not in state.quality_flags
    assert not state.pause_offered


def test_yes_to_the_pause_with_an_answer_keeps_going():
    state, _ = say(at_city(), turn(city="Gràcia"))
    state = frustrated_until_pause(state)

    state, action = say(state, turn(Intent.CONFIRM, availability="fines de semana"))

    assert _asks(action, FieldName.SCHEDULE)
    assert state.profile.availability == [Availability.WEEKENDS]
    assert state.outcome is None


def test_taking_the_pause_marks_the_chat_paused_until_the_candidate_writes():
    state, _ = say(at_city(), turn(city="Gràcia"))
    state = frustrated_until_pause(state)
    assert not state.paused

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, End)
    assert action.reason == EndReason.PAUSED
    assert state.paused
    prompt = current_prompt(state)
    assert prompt is not None
    assert _asks(prompt, FieldName.AVAILABILITY)

    state, action = say(state, turn(availability="fines de semana"))
    assert _asks(action, FieldName.SCHEDULE)
    assert not state.paused


def test_declining_the_pause_does_not_pause():
    state, _ = say(at_city(), turn(city="Gràcia"))
    state = frustrated_until_pause(state)

    state, _ = say(state, turn(Intent.SMALL_TALK))

    assert not state.paused


def test_reopening_a_paused_chat_clears_the_pause():
    state, _ = say(at_city(), turn(city="Gràcia"))
    state = frustrated_until_pause(state)
    state, _ = say(state, turn(Intent.CONFIRM))

    state = reopen(abandon(state))

    assert not state.paused
    prompt = current_prompt(state)
    assert prompt is not None
    assert _asks(prompt, FieldName.AVAILABILITY)


def at_availability() -> ConversationState:
    state, action = say(at_city(), turn(city="Gràcia"))
    assert _asks(action, FieldName.AVAILABILITY)
    return state


def de_escalated(state: ConversationState) -> ConversationState:
    """The candidate insulted Ana once: a de-escalation, and one frustrated turn on the streak."""
    state, action = say(state, turn(Intent.ABUSE, sentiment=Sentiment.FRUSTRATED))
    assert isinstance(action, DeEscalate)
    assert state.frustration_streak == 1
    assert state.de_escalated
    return state


def test_an_apology_that_answers_after_a_de_escalation_is_not_a_second_strike():
    """ "Perdona, llevo un día de perros. Tiempo completo y fines de semana", read as frustrated:
    the answer resets the streak instead of earning a pause offer."""
    state = de_escalated(at_availability())

    state, action = say(
        state,
        turn(sentiment=Sentiment.FRUSTRATED, availability=["full time", "weekends"]),
    )

    assert _asks(action, FieldName.SCHEDULE)
    assert not action.simplify
    assert state.frustration_streak == 0
    assert not state.pause_offered
    assert not state.de_escalated
    assert QualityFlag.FRUSTRATION not in state.quality_flags
    assert state.profile.availability == [Availability.FULL_TIME, Availability.WEEKENDS]


def test_a_frustrated_turn_without_a_valid_answer_after_a_de_escalation_still_counts():
    state = de_escalated(at_availability())

    state, action = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))

    assert isinstance(action, OfferPause)
    assert QualityFlag.FRUSTRATION in state.quality_flags


def test_an_invalid_answer_after_a_de_escalation_still_counts():
    state, _ = say(at_availability(), turn(availability="full time"))
    state = de_escalated(state)

    state, action = say(state, turn(sentiment=Sentiment.FRUSTRATED, schedule="cuando pueda"))

    assert isinstance(action, OfferPause)


def test_a_frustrated_question_after_a_de_escalation_still_counts():
    state = de_escalated(at_availability())

    state, action = say(
        state,
        turn(
            Intent.QUESTION,
            sentiment=Sentiment.FRUSTRATED,
            question="¿y esto para qué?",
            availability="full time",
        ),
    )

    assert isinstance(action, OfferPause)


def test_a_frustrated_answer_counts_when_no_de_escalation_came_before():
    state = at_availability()
    state, _ = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))

    state, action = say(state, turn(sentiment=Sentiment.FRUSTRATED, availability="full time"))

    assert isinstance(action, OfferPause)
    assert state.profile.availability == [Availability.FULL_TIME]


def test_the_de_escalation_reset_covers_only_the_next_turn():
    state = de_escalated(at_availability())
    state, action = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))
    assert isinstance(action, OfferPause)
    state, action = say(state, turn(Intent.SMALL_TALK))
    assert _asks(action, FieldName.AVAILABILITY)
    state, _ = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED))

    state, action = say(state, turn(sentiment=Sentiment.FRUSTRATED, availability="full time"))

    assert isinstance(action, OfferPause)


def test_yes_with_a_change_at_the_summary_updates_before_qualifying():
    state = at_confirm()

    state, action = say(state, turn(Intent.CONFIRM, availability="solo fines de semana"))
    assert isinstance(action, ConfirmSummary)
    assert action.profile.availability == [Availability.WEEKENDS]
    assert state.outcome is None

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, FindModes)
    assert state.profile.availability == [Availability.WEEKENDS]


def test_a_bare_correction_at_the_summary_asks_which_detail():
    """The "Corregir algo" button (or a spoken no) names no field: same card, asking which."""
    state = at_confirm()

    state, action = say(state, turn(Intent.CORRECTION))

    assert isinstance(action, ConfirmSummary)
    assert action.correcting
    assert action.ui is not None
    assert state.outcome is None

    state, action = say(state, turn(Intent.CORRECTION, availability="solo fines de semana"))
    assert isinstance(action, ConfirmSummary)
    assert not action.correcting
    assert state.profile.availability == [Availability.WEEKENDS]


def test_license_correction_at_the_summary_reruns_the_gate():
    state = at_confirm()

    state, action = say(state, turn(Intent.CORRECTION, has_license=False))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.NO_LICENSE
    assert state.outcome == Outcome.DISQUALIFIED


def test_correction_mid_flow_overwrites_an_earlier_answer():
    state = at_schedule()

    state, action = say(state, turn(Intent.CORRECTION, availability="solo fines de semana"))

    assert _asks(action, FieldName.SCHEDULE)
    assert state.profile.availability == [Availability.WEEKENDS]
    assert state.retries == {}


def test_no_license_reason_is_kept_when_the_city_is_also_out_of_area():
    state = at_license()

    state, action = say(state, turn(has_license=False, city="Ourense"))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.NO_LICENSE
    assert action.invite_reapply
    assert state.disqualify_reason == Close.NO_LICENSE


@pytest.mark.parametrize("resolution", ["Gràcia", "es-bcn-gracia"])
def test_city_gate_keeps_clarifying_until_resolved(resolution):
    state = at_city()
    for _ in range(2):
        state, action = say(state, turn(city="Barcelona"))
        assert isinstance(action, Clarify)
        assert set(action.options) == {"es-bcn-eixample", "es-bcn-gracia", "es-bcn-sants"}
    assert state.outcome is None

    state, action = say(state, turn(city=resolution))

    assert _asks(action, FieldName.AVAILABILITY)
    assert state.profile.location_id == "es-bcn-gracia"


def test_a_city_never_narrowed_to_a_location_closes():
    state = at_city()
    state, _ = say(state, turn(city="Barcelona"))
    state, _ = say(state, turn(city="Barcelona"))
    state, action = say(state, turn(city="Barcelona"))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.UNRESOLVED_CITY
    assert state.pending_clarification is None


def test_madrid_offers_its_four_zones_as_buttons():
    state = at_city()

    state, action = say(state, turn(city="Madrid"))

    assert isinstance(action, Clarify)
    expected = {"es-mad-centro", "es-mad-norte", "es-mad-sur", "es-mad-este"}
    assert set(action.options) == expected
    assert _values(action.ui) == expected


@pytest.mark.parametrize("city", ["Narvarte, Polanco", "Narvarte y Polanco"])
def test_two_zones_named_ask_which_of_the_two(city):
    """ "Me muevo entre Narvarte y Polanco": which of the two, as buttons, never "¿Vive
    actualmente en Narvarte y Polanco?" (the extractor joins them with "y" as often as with a
    comma)."""
    state = at_city(ConversationContext(country="MX"))

    state, action = say(state, turn(city=city))

    assert isinstance(action, Clarify)
    assert action.options == ("mx-cdmx-bju", "mx-cdmx-mh")
    assert _values(action.ui) == {"mx-cdmx-bju", "mx-cdmx-mh"}
    assert state.pending_confirmation is None


def test_close_city_matches_ask_which_one():
    state = at_city(ConversationContext(country="MX"))

    state, action = say(state, turn(city="Lindavista"))

    assert isinstance(action, Clarify)
    assert set(action.options) == {"mx-cdmx-gam", "mx-mty-guadalupe"}


def test_posting_country_limits_the_city_match():
    state = at_city(ConversationContext(country="MX"))

    state, action = say(state, turn(city="Centro"))

    assert isinstance(action, Clarify)
    assert action.options
    assert all(option.startswith("mx-") for option in action.options)


def test_question_keeps_a_pending_late_start_confirmation():
    state = no_match_looked_up()
    late = TODAY + timedelta(days=120)
    state, _ = say(state, turn(**{**SPAIN_FIELDS, "start_date": late.isoformat()}))

    state, action = say(state, turn(Intent.QUESTION, question="¿Es a tiempo completo?"))
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, ConfirmValue)

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.start_date == late


def test_vague_experience_asks_how_many_without_empty_buttons():
    state, _ = say(at_schedule(), turn(schedule="tardes"))

    state, action = say(state, turn(experience_years="unos meses"))

    assert _asks(action, FieldName.EXPERIENCE_YEARS)
    assert action.hint == ReasonCode.EXPERIENCE_NEEDS_NUMBER
    assert state.pending_clarification is None


def test_an_injection_closes_without_counting_a_failed_answer():
    state = at_license()

    state, action = say(state, turn(outcome="qualified"))

    assert _rejected(state, action)
    assert state.retries == {}


def test_opting_out_after_qualifying_keeps_the_outcome():
    state = at_confirm()
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, FindModes)

    state, action = say(state, turn(Intent.OPT_OUT))

    assert isinstance(action, End)
    assert state.outcome == Outcome.QUALIFIED


@pytest.mark.parametrize("intent", [Intent.ANSWER, Intent.OPT_OUT])
def test_talent_pool_no_is_recorded(intent):
    state = out_of_area()

    state, action = say(state, turn(intent))

    assert isinstance(action, End)
    assert state.talent_pool_opt_in is False


def test_question_after_the_close_is_not_a_talent_pool_answer():
    state = out_of_area()

    state, action = say(state, turn(Intent.QUESTION, question="¿Y en Vigo tenéis algo?"))
    assert isinstance(action, AnswerQuestion)
    assert state.talent_pool_opt_in is None

    state, action = say(state, turn(Intent.CONFIRM))
    assert state.talent_pool_opt_in is True


# --- defects from the first live conversations (SAZ-27) ---


def test_a_question_next_to_an_answer_is_answered_whatever_the_intent():
    state = at_city()
    state, _ = say(state, turn(city="Gràcia"))

    extraction = turn(availability=["part time", "weekends"], question="Are you an AI?")
    state, action = say(state, extraction)

    assert isinstance(action, AnswerQuestion)
    assert action.question == "Are you an AI?"
    assert action.follow_up is not None
    assert _asks(action.follow_up, FieldName.SCHEDULE)
    assert action.ui == action.follow_up.ui
    assert state.profile.availability == [Availability.PART_TIME, Availability.WEEKENDS]


def test_a_question_without_fields_is_not_a_failed_answer():
    state = at_license()

    state, action = say(state, turn(Intent.ANSWER, question="¿Hay que llevar moto propia?"))

    assert isinstance(action, AnswerQuestion)
    assert state.retries == {}


def test_off_topic_with_a_question_is_still_deflected():
    state = at_license()

    state, action = say(state, turn(Intent.OFF_TOPIC, question="¿Quién ganó ayer?"))

    assert isinstance(action, Deflect)


def test_a_question_asked_with_contact_details_is_answered_after_the_duplicate_check():
    state = consented()
    contact = turn(
        question="¿Cuánto se cobra?",
        full_name="Lucía Fernández",
        phone="612 345 678",
        email="l@example.com",
    )

    state, action = say(state, contact)
    assert isinstance(action, CheckDuplicate)
    state, action = on_lookup(state, DuplicateChecked(match=None))

    assert isinstance(action, AnswerQuestion)
    assert action.question == "¿Cuánto se cobra?"
    assert action.follow_up is not None
    assert _asks(action.follow_up, FieldName.HAS_LICENSE)
    assert state.pending_question is None


def test_a_pending_question_is_dropped_when_an_open_application_resumes():
    state = consented()
    state, action = say(
        state,
        turn(
            question="¿Cuánto se cobra?",
            full_name="Lucía Fernández",
            phone="612 345 678",
            email="l@x.es",
        ),
    )
    assert isinstance(action, CheckDuplicate)
    assert state.pending_question is not None

    state, action = on_lookup(state, DuplicateChecked(match=MATCH))

    # Answered before the question about the match, which nothing of the application precedes.
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, ConfirmIdentity)
    assert state.pending_question is None


@pytest.mark.parametrize(
    "question",
    ["Are you an AI?", "¿Eres un bot?", "¿Hablo con una persona real?", "Are you human?"],
)
def test_questions_about_ana_being_an_ai_get_the_ai_identity_topic(question: str):
    _, action = say(at_license(), turn(question=question, has_license=True))

    assert isinstance(action, AnswerQuestion)
    assert action.topic == QuestionTopic.AI_IDENTITY


def test_the_extractor_topic_is_used_as_is():
    extraction = turn(Intent.QUESTION, question="¿Con quién hablo?").model_copy(
        update={"question_topic": QuestionTopic.AI_IDENTITY}
    )

    _, action = say(at_license(), extraction)

    assert isinstance(action, AnswerQuestion)
    assert action.topic == QuestionTopic.AI_IDENTITY


def test_ordinary_questions_have_no_topic():
    _, action = say(at_license(), turn(Intent.QUESTION, question="¿Cuánto se paga?"))

    assert isinstance(action, AnswerQuestion)
    assert action.topic is None


def test_ai_question_after_the_close_keeps_its_topic():
    state, _ = say(at_license(), turn(has_license=False))

    _, action = say(state, turn(Intent.QUESTION, question="¿Eres una IA?"))

    assert isinstance(action, AnswerQuestion)
    assert action.topic == QuestionTopic.AI_IDENTITY


def test_current_prompt_is_the_pending_question_or_none():
    assert isinstance(current_prompt(start()[0]), Greet)
    assert _asks(current_prompt(at_license()), FieldName.HAS_LICENSE)  # type: ignore[arg-type]

    pending_check, action = say(
        consented(), turn(full_name="Lucía Fernández", phone="612 345 678", email="l@x.es")
    )
    assert isinstance(action, CheckDuplicate)
    assert current_prompt(pending_check) is None

    closed, _ = say(at_license(), turn(has_license=False))
    assert current_prompt(closed) is None


@pytest.mark.parametrize(
    ("context", "language"),
    [
        (ConversationContext(country="ES", timezone="Europe/Madrid", locale="en-ES"), Language.EN),
        (ConversationContext(locale="en"), Language.EN),
        (ConversationContext(locale="en_GB"), Language.EN),
        (ConversationContext(country="MX", language=Language.EN, locale="es-MX"), Language.EN),
        (ConversationContext(country="ES", language=Language.ES, locale="en-ES"), Language.ES),
        (ConversationContext(country="MX", locale="es-MX"), Language.ES),
        (ConversationContext(locale="eng"), Language.ES),
        (None, Language.ES),
    ],
)
def test_the_greeting_language_comes_from_the_page(context, language):
    state, greet = start(context)

    assert greet.language == language
    assert state.language == language


@pytest.mark.parametrize(
    ("context", "href"),
    [
        (ConversationContext(country="ES"), "/privacidad"),
        (ConversationContext(country="MX"), "/privacidad"),
        (ConversationContext(country="MX", locale="en-US"), "/en/privacy"),
    ],
)
def test_the_greeting_links_the_privacy_notice(context, href):
    _, greet = start(context)

    assert isinstance(greet.ui, QuickReplies)
    assert [option.value for option in greet.ui.options] == ["yes", "no"]
    assert greet.ui.link is not None
    assert greet.ui.link.href == href


def test_an_unclear_first_reply_keeps_the_english_greeting():
    state, _ = start(ConversationContext(country="ES", locale="en-ES"))

    state, action = say(state, turn(Intent.CONFIRM, frame=MatrixLanguage.MIXED))

    assert action.language == Language.EN
    assert state.country == "ES"


def test_a_spanish_reply_to_an_english_greeting_switches_to_spanish():
    state, _ = start(ConversationContext(country="ES", locale="en-ES"))

    _, action = say(state, turn(Intent.CONFIRM))

    assert action.language == Language.ES


def test_reopen_resumes_an_abandoned_application_where_it_stopped():
    state, _ = say(at_city(), turn(city="Gràcia"))
    state = abandon(state)
    assert state.outcome == Outcome.ABANDONED
    assert state.drop_off_stage == Step.AVAILABILITY

    state = reopen(state)

    assert state.outcome is None
    assert state.drop_off_stage is None
    assert state.stage == Step.AVAILABILITY
    assert state.awaiting == FieldName.AVAILABILITY
    assert state.profile.location_id == "es-bcn-gracia"
    assert _asks(current_prompt(state), FieldName.AVAILABILITY)  # type: ignore[arg-type]
    state, action = say(state, turn(availability=["full time"]))
    assert _asks(action, FieldName.SCHEDULE)


def test_reopen_before_consent_asks_for_consent_again():
    state = reopen(abandon(start()[0]))

    assert state.outcome is None
    assert state.stage == Stage.CONSENT
    assert isinstance(current_prompt(state), Greet)


def test_reopen_leaves_other_outcomes_and_abuse_closures_alone():
    abused = at_license()
    abused, _ = say(abused, turn(Intent.ABUSE))
    abused, action = say(abused, turn(Intent.ABUSE))
    assert isinstance(action, End)
    assert reopen(abused) == abused

    disqualified, _ = say(at_license(), turn(has_license=False))
    assert reopen(disqualified) == disqualified

    in_progress = at_license()
    assert reopen(in_progress) == in_progress


def test_declining_consent_keeps_nothing_from_earlier_turns():
    state, _ = start()
    state, _ = say(state, turn(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED), suspicious=True)
    assert state.quality_flags == (QualityFlag.INJECTION_SUSPECTED,)

    state, action = say(state, turn(Intent.OPT_OUT))

    assert isinstance(action, End)
    assert state.outcome == Outcome.DECLINED_CONSENT
    assert state.quality_flags == ()
    assert state.recent_matrix == ()
    assert state.frustration_streak == 0


@pytest.mark.parametrize(
    ("years", "language", "text"),
    [
        (1, Language.ES, "1 año"),
        (0.5, Language.ES, "6 meses"),
        (1.5, Language.ES, "1 año y 6 meses"),
        (2, Language.ES, "2 años"),
        (0.25, Language.ES, "3 meses"),
        (1 / 12, Language.ES, "1 mes"),
        (2.08, Language.ES, "2 años y 1 mes"),
        (0, Language.ES, "Sin experiencia"),
        (1, Language.EN, "1 year"),
        (0.5, Language.EN, "6 months"),
        (1.5, Language.EN, "1 year 6 months"),
        (2, Language.EN, "2 years"),
        (0.25, Language.EN, "3 months"),
        (0, Language.EN, "No experience"),
    ],
)
def test_experience_reads_with_plurals(years: float, language: Language, text: str):
    assert format_years(years, language) == text


def test_dates_read_in_the_candidate_language():
    day = date(2026, 10, 19)
    assert format_date(day, Language.ES) == "lunes 19 de octubre"
    assert format_date(day, Language.EN) == "Monday 19 October"
    assert format_date(day, Language.ES, year=True) == "lunes 19 de octubre de 2026"
    assert format_date(day, Language.EN, year=True) == "Monday 19 October 2026"


# --- out of area: confirm before closing (SAZ-20) ---


def at_city_answer(city: str, confidence: float = 0.9) -> tuple:
    state = at_city()
    extraction = turn(city=city)
    extraction.fields["city"] = extraction.fields["city"].model_copy(
        update={"confidence": confidence}
    )
    return say(state, extraction)


def test_a_place_outside_the_areas_is_confirmed_before_disqualifying():
    state, action = at_city_answer("LA")

    assert isinstance(action, ConfirmValue)
    assert action.field == FieldName.CITY
    assert action.value == "LA"
    assert _values(action.ui) == {"yes", "no"}
    assert state.outcome is None
    assert state.stage == Step.CITY
    assert current_prompt(state) == action


def test_grew_up_elsewhere_then_names_the_current_city():
    state, _ = at_city_answer("LA")

    state, action = say(state, turn(Intent.CORRECTION, city="Gràcia"))

    assert _asks(action, FieldName.AVAILABILITY)
    assert state.profile.location_id == "es-bcn-gracia"
    assert state.outcome is None
    assert state.pending_confirmation is None


def test_a_no_to_the_place_asks_for_the_city_again_without_a_failure():
    state, _ = at_city_answer("LA")

    state, action = say(state, turn(Intent.CORRECTION))

    assert _asks(action, FieldName.CITY)
    assert isinstance(action, AskField)
    assert action.retry == 0
    assert state.outcome is None
    assert state.pending_confirmation is None


def test_naming_another_place_outside_the_areas_in_reply_closes():
    state, _ = at_city_answer("LA")

    state, action = say(state, turn(Intent.CORRECTION, city="Ourense"))

    assert isinstance(action, Disqualify)
    assert action.reason == Close.OUT_OF_AREA


def test_a_question_keeps_the_place_confirmation_pending():
    state, _ = at_city_answer("LA")

    state, action = say(state, turn(Intent.QUESTION, question="¿Cuánto pagan?"))
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, ConfirmValue)

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)


def test_an_uncertain_place_outside_the_areas_is_read_back_too():
    """QA sweep 2026-10-21: "Los Ángeles, California" came back at 0.5, was asked again as a
    failed answer and the application ended unresolved instead of reaching the read-back."""
    state, action = at_city_answer("Los Ángeles, California", confidence=0.5)

    assert isinstance(action, ConfirmValue)
    assert action.value == "Los Ángeles, California"
    assert state.retries.get(FieldName.CITY, 0) == 0

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)
    assert action.reason == Close.OUT_OF_AREA


@pytest.mark.parametrize("answer", ["cerca de donde trabajo", "Por ahí", "en mi casa"])
def test_words_that_name_no_place_are_asked_again_not_read_back(answer):
    """Live run 2026-10-21: "Por ahí, cerca de donde trabajo" at 0.5 was read back as a place."""
    state, action = at_city_answer(answer, confidence=0.5)

    assert _asks(action, FieldName.CITY)
    assert state.pending_confirmation is None
    assert state.retries[FieldName.CITY] == 1


def test_an_uncertain_new_place_in_reply_to_the_read_back_is_read_back_not_closed():
    state, _ = at_city_answer("LA")
    extraction = turn(Intent.CORRECTION, city="Ourense")
    extraction.fields["city"] = extraction.fields["city"].model_copy(update={"confidence": 0.5})

    state, action = say(state, extraction)

    assert isinstance(action, ConfirmValue)
    assert action.value == "Ourense"
    assert state.outcome is None


def test_a_place_outside_the_areas_after_a_loose_match_is_read_back():
    """ "¿Se refiere a la sucursal de Álvaro Obregón?" "No, Los Ángeles, California": never
    closed without asking whether they live there."""
    state = at_city(ConversationContext(country="MX"))
    state, action = say(state, turn(city="Los Ángeles"))
    assert isinstance(action, ConfirmValue)
    assert action.location_id == "mx-cdmx-ao"

    state, action = say(state, turn(Intent.CORRECTION, city="Los Ángeles, California"))

    assert isinstance(action, ConfirmValue)
    assert action.location_id is None
    assert state.outcome is None


def test_an_uncertain_no_to_the_license_is_asked_again():
    state = at_license()
    extraction = turn(has_license=False)
    extraction.fields["has_license"] = extraction.fields["has_license"].model_copy(
        update={"confidence": 0.5}
    )

    state, action = say(state, extraction)

    assert _asks(action, FieldName.HAS_LICENSE)
    assert isinstance(action, AskField)
    assert action.hint == ReasonCode.LICENSE_INVALID
    assert state.outcome is None


@pytest.mark.parametrize("answer", ["x" * 50, "Mejor en inglés porfa, I grew up in LA"])
def test_an_answer_too_long_to_be_a_place_is_asked_again(answer):
    state, action = at_city_answer(answer)

    assert _asks(action, FieldName.CITY)
    assert state.pending_confirmation is None
    assert state.outcome is None


def test_a_language_request_alone_is_not_a_failed_answer():
    state = at_license()

    state, action = say(state, turn(Intent.UNCLEAR, request=Language.EN))

    assert _asks(action, FieldName.HAS_LICENSE)
    assert action.language == Language.EN
    assert state.retries == {}


# --- complaints and language requests are not FAQ questions (SAZ-20) ---


def test_a_language_request_is_a_switch_not_a_question():
    state = at_license()

    state, action = say(
        state,
        turn(
            Intent.QUESTION,
            question="Can we do this in English please?",
            frame=MatrixLanguage.EN,
            request=Language.EN,
        ),
    )

    assert _asks(action, FieldName.HAS_LICENSE)
    assert action.language == Language.EN
    assert state.retries == {}


def test_a_complaint_phrased_as_a_question_is_not_answered_from_the_faq():
    state = at_schedule()

    state, action = say(
        state,
        turn(
            Intent.UNCLEAR,
            question="¿otra pregunta?",
            sentiment=Sentiment.FRUSTRATED,
        ),
    )

    assert not isinstance(action, AnswerQuestion)
    assert _asks(action, FieldName.SCHEDULE)
    assert action.simplify


def test_the_ai_question_is_answered_even_with_a_language_request():
    state = at_license()

    state, action = say(
        state,
        turn(
            Intent.QUESTION,
            question="Are you a bot? English please",
            frame=MatrixLanguage.EN,
            request=Language.EN,
            sentiment=Sentiment.FRUSTRATED,
        ),
    )

    assert isinstance(action, AnswerQuestion)
    assert action.topic == QuestionTopic.AI_IDENTITY


# --- strict license, multi-valued schedule (SAZ-41) ---


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (False, Close.NO_LICENSE),
        ("in_progress", Close.LICENSE_IN_PROGRESS),
        ("lo estoy sacando", Close.LICENSE_IN_PROGRESS),
        ("permiso de aprendiz", Close.LICENSE_IN_PROGRESS),
        ("not_valid", Close.LICENSE_NOT_VALID),
        ("vencida", Close.LICENSE_NOT_VALID),
        ("No, está vencida", Close.LICENSE_NOT_VALID),
        ("la estoy renovando", Close.LICENSE_NOT_VALID),
        ("me la quitaron", Close.LICENSE_NOT_VALID),
    ],
)
def test_every_license_that_is_not_valid_now_closes_with_an_invitation(answer, reason):
    state = at_license()

    state, action = say(state, turn(has_license=answer))

    assert isinstance(action, Disqualify)
    assert action.reason == reason
    assert action.invite_reapply
    assert not action.talent_pool_offer
    assert action.ui is None
    assert state.outcome == Outcome.DISQUALIFIED
    assert state.profile.has_license is False


def test_several_schedule_blocks_are_stored_without_asking_again():
    state = at_schedule()

    state, action = say(state, turn(schedule="mañanas y tardes, noches no"))

    assert state.profile.schedule == [Schedule.MORNING, Schedule.AFTERNOON]
    assert _asks(action, FieldName.EXPERIENCE_YEARS)


def test_the_read_back_lists_every_schedule_block():
    state = consented()
    fields = {**SPAIN_FIELDS, "schedule": ["morning", "evening"], "experience_years": 1.5}
    state, action = say(state, turn(**fields))
    state, action = on_lookup(state, DuplicateChecked(match=None))

    assert isinstance(action, ConfirmSummary)
    assert isinstance(action.ui, Confirm)
    rows = {row.label: row.value for row in action.ui.fields}
    assert rows["Horario"] == "Mañana, Noche"
    assert rows["Experiencia"] == "1 año y 6 meses"


# --- implausible experience (SAZ-41, decision 8) ---


def at_experience(context: ConversationContext | None = None) -> ConversationState:
    state = at_schedule() if context is None else at_schedule_in(context)
    state, action = say(state, turn(schedule="mañanas"))
    assert _asks(action, FieldName.EXPERIENCE_YEARS)
    return state


def at_schedule_in(context: ConversationContext) -> ConversationState:
    state, _ = say(at_license(context), turn(has_license=True))
    state, _ = say(state, turn(city="Zapopan"))
    state, action = say(state, turn(availability=["full time"]))
    assert _asks(action, FieldName.SCHEDULE)
    return state


MX = ConversationContext(country="MX", timezone="America/Mexico_City", locale="es-MX")


def test_fifteen_years_or_more_are_read_back_before_keeping_them():
    state = at_experience()

    state, action = say(state, turn(experience_years=20))

    assert isinstance(action, ConfirmValue)
    assert action.field == FieldName.EXPERIENCE_YEARS
    assert action.value == "20"
    assert action.platform is None
    assert _values(action.ui) == {"yes", "no"}
    assert state.profile.experience_years is None

    state, action = say(state, turn(Intent.CONFIRM))

    assert state.profile.experience_years == 20
    assert not state.profile.experience_unverified
    assert _asks(action, FieldName.PLATFORMS)


def test_eleven_years_are_read_back():
    state = at_experience()

    state, action = say(state, turn(experience_years=11))

    assert isinstance(action, ConfirmValue)
    assert state.profile.experience_years is None


def test_ten_years_need_no_read_back():
    state = at_experience()

    state, action = say(state, turn(experience_years=10))

    assert state.profile.experience_years == 10
    assert _asks(action, FieldName.PLATFORMS)


def test_a_no_to_the_read_back_asks_the_years_again():
    state = at_experience()
    state, _ = say(state, turn(experience_years=20))

    state, action = say(state, turn(Intent.CORRECTION))

    assert _asks(action, FieldName.EXPERIENCE_YEARS)
    assert state.profile.experience_years is None
    assert state.retries.get(FieldName.EXPERIENCE_YEARS, 0) == 0


def test_a_new_number_in_reply_to_the_read_back_is_applied():
    state = at_experience()
    state, _ = say(state, turn(experience_years=20))

    state, action = say(state, turn(Intent.CORRECTION, experience_years=2))

    assert state.profile.experience_years == 2
    assert _asks(action, FieldName.PLATFORMS)


def test_years_longer_than_the_platform_has_existed_are_asked_neutrally():
    """ "9 años en DiDi": DiDi Food has been in Mexico since 2019."""
    state = at_experience(MX)

    state, action = say(state, turn(experience_years=7, platforms=["didi"]))
    assert state.profile.experience_years == 7  # 2019-2026 fits

    state = at_experience(MX)
    state, action = say(state, turn(experience_years=9, platforms=["didi"]))

    assert isinstance(action, ConfirmValue)
    assert action.platform == Platform.DIDI_FOOD
    assert action.ui is None
    assert state.profile.experience_years is None

    state, action = say(state, turn(experience_years=9))

    assert state.profile.experience_years == 9
    assert state.profile.experience_unverified
    assert _asks(action, FieldName.START_DATE)


def test_a_yes_keeps_the_platform_years_flagged():
    state = at_experience(MX)
    state, _ = say(state, turn(experience_years=13, platforms=["rappi"]))

    state, _ = say(state, turn(Intent.CONFIRM))

    assert state.profile.experience_years == 13
    assert state.profile.experience_unverified


def test_ten_years_on_glovo_in_spain_are_plausible():
    state = at_experience()

    state, action = say(state, turn(experience_years=10, platforms=["glovo"]))

    assert state.profile.experience_years == 10
    assert not state.profile.experience_unverified
    assert _asks(action, FieldName.START_DATE)


def test_several_platforms_compare_with_the_oldest():
    state = at_experience()

    state, _ = say(state, turn(experience_years=8, platforms=["amazon flex", "glovo"]))

    assert state.profile.experience_years == 8


def test_confirmed_years_later_shown_implausible_by_a_platform_are_flagged_not_asked():
    state = at_experience(MX)
    state, _ = say(state, turn(experience_years=20))
    state, action = say(state, turn(Intent.CONFIRM))
    assert _asks(action, FieldName.PLATFORMS)

    state, action = say(state, turn(platforms=["didi"]))

    assert state.profile.experience_years == 20
    assert state.profile.experience_unverified
    assert _asks(action, FieldName.START_DATE)
