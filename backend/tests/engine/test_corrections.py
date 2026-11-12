"""The voice license check, corrections and reopening after a gate (SAZ-47), and no spelled
read-backs (SAZ-48)."""

from datetime import date

import pytest

from sazon.agents.riders import Close, FieldName
from sazon.engine import (
    AskField,
    CheckDuplicate,
    ConfirmSummary,
    ConfirmValue,
    ConversationContext,
    ConversationState,
    Disqualify,
    DisqualifyReason,
    DuplicateChecked,
    End,
    on_lookup,
    start,
    step,
)
from sazon.engine.models import (
    Intent,
    Language,
    MatrixLanguage,
    Outcome,
    Sentiment,
    TurnExtraction,
)

TODAY = date(2026, 10, 19)
PHONE = "+34612345678"
EMAIL = "ps@gmail.com"


def turn(intent: Intent = Intent.ANSWER, confidence: float = 0.9, **fields: object):
    return TurnExtraction.model_validate(
        {
            "intent": intent,
            "fields": {k: {"value": v, "confidence": confidence} for k, v in fields.items()},
            "language": Language.ES,
            "matrix_language": MatrixLanguage.ES,
            "english_ratio": 0.0,
            "code_switched": False,
            "sentiment": Sentiment.NEUTRAL,
        }
    )


def say(state: ConversationState, extraction: TurnExtraction, **kwargs):
    return step(state, extraction, today=TODAY, **kwargs)


def consented(voice: bool) -> ConversationState:
    state, _ = start(ConversationContext(country="ES"))
    state, _ = say(state.model_copy(update={"voice": voice}), turn(Intent.CONFIRM))
    return state


def asks(action: object, field: FieldName, *, again: bool = False) -> bool:
    return isinstance(action, AskField) and action.field == field and action.again == again


def at_license(voice: bool) -> ConversationState:
    """Name, phone and email given; by voice too, nothing is spelled back (SAZ-48)."""
    state = consented(voice)
    state, action = say(state, turn(full_name="Pablo Sánchez", phone="612345678", email=EMAIL))
    assert isinstance(action, CheckDuplicate)
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert asks(action, FieldName.HAS_LICENSE)
    return state


# --- no spelled read-backs (SAZ-48) ---


@pytest.mark.parametrize("voice", [False, True])
def test_phone_and_email_are_never_spelled_back(voice: bool):
    state = consented(voice)
    state, action = say(state, turn(full_name="Pablo Sánchez"))
    state, action = say(state, turn(phone="612 345 678"))
    assert isinstance(action, CheckDuplicate)
    state, action = on_lookup(state, DuplicateChecked(match=None))

    assert asks(action, FieldName.EMAIL)
    assert state.profile.phone == PHONE
    state, action = say(state, turn(email=EMAIL))
    assert isinstance(action, CheckDuplicate)
    assert (action.phone, action.email) == (PHONE, EMAIL)
    assert state.pending_confirmation is None
    assert state.read_back == ()


# --- one check before a voice license close ---


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (False, Close.NO_LICENSE),
        ("in_progress", Close.LICENSE_IN_PROGRESS),
    ],
)
def test_voice_checks_a_no_license_once_before_closing(answer: object, reason: DisqualifyReason):
    state = at_license(voice=True)

    state, action = say(state, turn(has_license=answer))

    assert isinstance(action, ConfirmValue)
    assert action.field == FieldName.HAS_LICENSE
    assert state.outcome is None
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)
    assert state.disqualify_reason == reason
    assert state.profile.has_license is False


def test_web_closes_on_the_first_no_license():
    state = at_license(voice=False)
    state, action = say(state, turn(has_license=False))
    assert isinstance(action, Disqualify)


def test_a_yes_at_the_voice_license_check_carries_on():
    state = at_license(voice=True)
    state, _ = say(state, turn(has_license=False))

    state, action = say(state, turn(Intent.CORRECTION, has_license=True))

    assert asks(action, FieldName.CITY)
    assert state.profile.has_license is True


def test_a_no_at_the_voice_license_check_closes():
    state = at_license(voice=True)
    state, _ = say(state, turn(has_license=False))
    state, action = say(state, turn(has_license=False))
    assert isinstance(action, Disqualify)


def test_the_voice_license_check_is_asked_only_once():
    state = at_license(voice=True)
    state, _ = say(state, turn(has_license=False))

    state, action = say(state, turn(Intent.CORRECTION))
    assert asks(action, FieldName.HAS_LICENSE)
    state, action = say(state, turn(has_license=False))

    assert isinstance(action, Disqualify)


# --- "mi email está mal" ---


@pytest.mark.parametrize("voice", [False, True])
def test_a_wrong_field_is_asked_again_mid_flow(voice: bool):
    """By voice too, the new value is kept as given: nothing is spelled back (SAZ-48)."""
    state = at_license(voice)

    state, action = say(state, turn(Intent.CORRECTION), fix=FieldName.EMAIL)

    assert asks(action, FieldName.EMAIL, again=True)
    assert state.profile.email is None
    assert state.profile.phone == PHONE
    assert state.retries.get(FieldName.EMAIL, 0) == 0
    assert state.retries.get(FieldName.HAS_LICENSE, 0) == 0
    state, action = say(state, turn(email="pps@gmail.com"))
    assert asks(action, FieldName.HAS_LICENSE)
    assert state.profile.email == "pps@gmail.com"
    assert state.fix_field is None


def test_the_same_value_read_again_with_the_request_is_dropped():
    state = at_license(voice=False)
    state, action = say(state, turn(Intent.CORRECTION, email=EMAIL), fix=FieldName.EMAIL)
    assert asks(action, FieldName.EMAIL, again=True)


@pytest.mark.parametrize("voice", [False, True])
@pytest.mark.parametrize("fix", [FieldName.EMAIL, None])
def test_a_new_value_with_the_request_is_applied(voice: bool, fix: FieldName | None):
    """With or without "mi email está mal", a new value is applied and, by voice, not read
    back."""
    state = at_license(voice)
    state, action = say(state, turn(Intent.CORRECTION, email="pps@gmail.com"), fix=fix)
    assert asks(action, FieldName.HAS_LICENSE)
    assert state.profile.email == "pps@gmail.com"


def test_a_wrong_field_at_the_summary_goes_back_to_the_summary():
    state = at_license(voice=False)
    fields = {
        "has_license": True,
        "city": "Gràcia",
        "availability": ["full_time"],
        "schedule": "morning",
        "experience_years": 2,
        "platforms": ["glovo"],
        "start_date": "2026-10-26",
    }
    state, action = say(state, turn(**fields))
    assert isinstance(action, ConfirmSummary)

    state, action = say(state, turn(Intent.CORRECTION), fix=FieldName.PHONE)
    assert asks(action, FieldName.PHONE, again=True)
    state, action = say(state, turn(phone="699111222"))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.phone == "+34699111222"


# --- reopening right after a gate closed ---


def closed_on_license(voice: bool = False) -> ConversationState:
    state = at_license(voice)
    state, action = say(state, turn(has_license=False))
    if voice:
        state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)
    assert state.reopenable
    return state


@pytest.mark.parametrize("voice", [False, True])
def test_a_contradiction_right_after_the_license_close_reopens(voice: bool):
    state = closed_on_license(voice)

    state, action = say(state, turn(Intent.CORRECTION, has_license=True))

    assert state.outcome is None
    assert state.disqualify_reason is None
    assert state.profile.has_license is True
    assert asks(action, FieldName.CITY)


@pytest.mark.parametrize("voice", [False, True])
def test_a_field_fix_right_after_the_license_close_reopens(voice: bool):
    """The live call 2833553637: "No, pps@gmail.com" was cut to "No." and closed the
    application; "Sí, tengo carnet. Digo que mi email está mal." came next."""
    state = closed_on_license(voice)

    state, action = say(state, turn(Intent.CORRECTION, has_license=True), fix=FieldName.EMAIL)

    assert state.outcome is None
    assert state.profile.has_license is True
    assert asks(action, FieldName.EMAIL, again=True)


def test_a_correction_with_a_new_value_reopens():
    state = closed_on_license()
    state, action = say(state, turn(Intent.CORRECTION, email="pps@gmail.com"))
    assert state.outcome is None
    assert state.profile.email == "pps@gmail.com"
    assert asks(action, FieldName.HAS_LICENSE)


@pytest.mark.parametrize(
    "extraction",
    [
        turn(Intent.SMALL_TALK),
        turn(Intent.CORRECTION, has_license=False),
        turn(Intent.OPT_OUT, has_license=True),
        turn(Intent.ANSWER, email="pps@gmail.com"),
    ],
)
def test_anything_else_after_the_close_keeps_it_closed(extraction: TurnExtraction):
    state = closed_on_license()
    state, action = say(state, extraction)
    assert state.outcome == Outcome.DISQUALIFIED
    assert isinstance(action, End)


def test_only_the_very_next_message_can_reopen():
    state = closed_on_license()
    state, _ = say(state, turn(Intent.SMALL_TALK))
    state, action = say(state, turn(Intent.CORRECTION, has_license=True))
    assert state.outcome == Outcome.DISQUALIFIED
    assert isinstance(action, End)


def test_an_injection_after_the_close_never_reopens():
    state = closed_on_license()
    state, _ = say(state, turn(Intent.CORRECTION, has_license=True), suspicious=True)
    assert state.outcome == Outcome.DISQUALIFIED
    assert state.disqualify_reason == Close.NO_LICENSE


def test_a_city_we_serve_right_after_the_out_of_area_close_reopens():
    state = at_license(voice=False)
    state, _ = say(state, turn(has_license=True, city="Lisboa"))
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, Disqualify)
    assert state.disqualify_reason == Close.OUT_OF_AREA

    state, action = say(state, turn(Intent.CORRECTION, city="Gràcia"))

    assert state.outcome is None
    assert state.talent_pool_opt_in is None
    assert state.profile.location_id is not None
    assert asks(action, FieldName.AVAILABILITY)


def test_a_policy_close_never_reopens():
    state = at_license(voice=False)
    state, action = say(state, turn(), suspicious=True)
    assert isinstance(action, Disqualify)
    assert not state.reopenable
    state, _ = say(state, turn(Intent.CORRECTION, has_license=True), fix=FieldName.EMAIL)
    assert state.outcome == Outcome.DISQUALIFIED
