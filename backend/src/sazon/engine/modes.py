"""Read the interview mode from the candidate's own words: in person or by video call.

Deterministic and conservative, like the slot resolver: a mode only when the words name one
and not the other ("presencial", "mejor por video", "in person"). A mode that is negated ("no
quiero videollamada") is not chosen, and words naming both modes resolve to nothing, so the
extractor's hint decides.
"""

import re
from collections.abc import Collection

from sazon.engine.models import InterviewMode
from sazon.engine.text import fold

MODE_PHRASES: dict[InterviewMode, re.Pattern[str]] = {
    InterviewMode.ONSITE: re.compile(
        r"\b(?:presencial|presencialmente|en persona|cara a cara|en la oficina|en el local|"
        r"en la sucursal|en el restaurante|in person|face to face|onsite|on site|at the office|"
        r"at the location)\b"
    ),
    InterviewMode.VIDEO: re.compile(
        r"\b(?:video|videollamada|videoconferencia|video ?call|google meet|"
        r"(?:por|en|via|on|by|over) meet|online|on line|en linea|virtual|virtualmente|"
        r"a distancia|remoto|remote|remotely)\b|^meet$"
    ),
}
CLAUSE = re.compile(r"[,.;:!?¡¿()\n]+|\b(?:pero|but|mejor|better|rather)\b")
"""Clause breaks: "No, mejor por video" refuses nothing; "presencial no, online" refuses one."""
NEGATED_BEFORE = re.compile(r"\b(?:no|ni|sin|nada de|not|don t|dont)\s+(?:\w+\s+){0,2}$")
NEGATED_AFTER = re.compile(r"^\s*(?:no|tampoco|not)\b")


def resolve_mode(text: str) -> InterviewMode | None:
    """The one mode the words choose, or None when they name neither, both, or only refuse."""
    chosen: set[InterviewMode] = set()
    refused: set[InterviewMode] = set()
    for clause in CLAUSE.split(text.casefold()):
        words = fold(clause)
        for mode, pattern in MODE_PHRASES.items():
            for match in pattern.finditer(words):
                before, after = words[: match.start()], words[match.end() :]
                negated = NEGATED_BEFORE.search(before) or NEGATED_AFTER.match(after)
                (refused if negated else chosen).add(mode)
    chosen -= refused
    if len(chosen) == 1:
        return next(iter(chosen))
    return None


def read_mode(value: str | None) -> InterviewMode | None:
    """The extractor's `mode` hint as a mode: the value itself or words that name one."""
    if not value:
        return None
    try:
        return InterviewMode(value.strip().lower())
    except ValueError:
        return resolve_mode(value)


def allowed_mode(value: str | None, available: Collection[InterviewMode]) -> InterviewMode | None:
    """The hint as a mode, only when it is one of the `available` modes."""
    mode = read_mode(value)
    return mode if mode is not None and mode in available else None
