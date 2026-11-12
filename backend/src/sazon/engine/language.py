import re

from sazon.engine.models import Language, MatrixLanguage, TurnExtraction
from sazon.engine.state import ConversationState

COUNTRY_DEFAULT_LANGUAGE = {"ES": Language.ES, "MX": Language.ES}
SWITCH_AFTER_TURNS = 2
_EMAIL = re.compile(r"\S+@\S+")


def value_only(text: str) -> bool:
    """A message with no words in any language: an email, a phone number, a number. It says
    nothing about the language the candidate writes in, so it never counts towards a switch
    (QA sweep 2026-10-21: an email alone read as English-framed)."""
    return not any(char.isalpha() for char in _EMAIL.sub(" ", text))


def decide_language(state: ConversationState, extraction: TurnExtraction) -> Language:
    """Pick the reply language for this turn.

    1. An explicit request always wins and sticks.
    2. Otherwise keep the current language unless the last 2 turns are both framed
       in the other one.
    3. Insertional Spanglish (Spanish frame, English words) is framed in Spanish,
       so it never switches away from Spanish.
    4. The first turn takes the language of the first message, or keeps the greeting's
       (the country default unless the page asked for English).
    """
    if extraction.explicit_language_request is not None:
        return extraction.explicit_language_request
    if state.language_locked:
        return state.language
    frame = extraction.matrix_language
    if not state.recent_matrix:
        return _language_of(frame) or state.language
    last_turns = (*state.recent_matrix, frame)[-SWITCH_AFTER_TURNS:]
    other = Language.EN if state.language == Language.ES else Language.ES
    if len(last_turns) == SWITCH_AFTER_TURNS and all(
        _language_of(turn) == other for turn in last_turns
    ):
        return other
    return state.language


def _language_of(frame: MatrixLanguage) -> Language | None:
    if frame == MatrixLanguage.ES:
        return Language.ES
    if frame == MatrixLanguage.EN:
        return Language.EN
    return None
