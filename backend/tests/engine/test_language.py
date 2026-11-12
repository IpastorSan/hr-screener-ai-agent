from datetime import date

import pytest

from sazon.agents.riders import FieldName
from sazon.engine.language import decide_language, value_only
from sazon.engine.machine import start, step
from sazon.engine.models import Intent, Language, MatrixLanguage, Sentiment, TurnExtraction
from sazon.engine.state import (
    Asking,
    ConversationState,
)

ES = MatrixLanguage.ES
EN = MatrixLanguage.EN
MIXED = MatrixLanguage.MIXED


def extraction(
    frame: MatrixLanguage, english_ratio: float = 0.0, request: Language | None = None
) -> TurnExtraction:
    return TurnExtraction(
        intent=Intent.ANSWER,
        language=Language.EN if frame == EN else Language.ES,
        matrix_language=frame,
        english_ratio=english_ratio,
        explicit_language_request=request,
        code_switched=0 < english_ratio < 1,
        sentiment=Sentiment.NEUTRAL,
    )


@pytest.mark.parametrize(
    ("current", "history", "locked", "turn", "expected"),
    [
        # First turn: language of the first message.
        (Language.ES, (), False, extraction(EN, 1.0), Language.EN),
        (Language.ES, (), False, extraction(ES, 0.0), Language.ES),
        # A mixed first turn falls back to the country default.
        (Language.ES, (), False, extraction(MIXED, 0.5), Language.ES),
        # Insertional Spanglish keeps Spanish, even with many English words.
        (Language.ES, (ES,), False, extraction(ES, 0.4), Language.ES),
        (Language.ES, (ES, ES), False, extraction(ES, 0.6), Language.ES),
        # One English-framed turn is not enough to switch.
        (Language.ES, (ES,), False, extraction(EN, 1.0), Language.ES),
        # Two English-framed turns in a row switch.
        (Language.ES, (ES, EN), False, extraction(EN, 1.0), Language.EN),
        # Mixed turns never count towards a switch.
        (Language.ES, (ES, MIXED), False, extraction(EN, 0.9), Language.ES),
        # Switching back needs two Spanish-framed turns too.
        (Language.EN, (EN, EN), False, extraction(ES, 0.0), Language.EN),
        (Language.EN, (EN, ES), False, extraction(ES, 0.0), Language.ES),
        # An explicit request wins at once.
        (Language.ES, (ES,), False, extraction(ES, 0.1, Language.EN), Language.EN),
        (Language.ES, (), False, extraction(ES, 0.1, Language.EN), Language.EN),
        # After an explicit request the language sticks.
        (Language.EN, (ES, ES), True, extraction(ES, 0.0), Language.EN),
        # A request back to Spanish is honoured.
        (Language.EN, (EN, EN), True, extraction(EN, 0.9, Language.ES), Language.ES),
    ],
)
def test_decide_language(current, history, locked, turn, expected):
    state = ConversationState(language=current, recent_matrix=history, language_locked=locked)

    assert decide_language(state, turn) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("daniel.ortiz@example.com", True),
        ("+52 55 1234 5619", True),
        ("2", True),
        (" daniel@example.com. ", True),
        ("mi correo es daniel@example.com", False),
        ("My email is daniel@example.com", False),
        ("Zapopan", False),
    ],
)
def test_value_only(text, expected):
    assert value_only(text) is expected


def test_a_value_only_turn_does_not_count_towards_a_switch():
    """QA sweep 2026-10-21: an email alone came back English-framed, so the next English
    sentence switched the chat on its own."""
    state, _ = start()
    state = state.model_copy(
        update={"consented": True, "recent_matrix": (ES,), "phase": Asking(field=FieldName.EMAIL)}
    )
    today = date(2026, 10, 19)

    state, _ = step(state, extraction(EN, 1.0), today=today, value_only=True)
    assert state.recent_matrix == (ES,)
    state, action = step(state, extraction(EN, 1.0), today=today)

    assert action.language == Language.ES
    assert state.language == Language.ES
