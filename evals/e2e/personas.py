"""Persona cards for the end-to-end evals (`personas.yaml`)."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sazon.engine.models import Language, MatrixLanguage
from sazon.engine.state import ConversationContext
from sazon.engine.validators import Country

PERSONAS_PATH = Path(__file__).resolve().parent / "personas.yaml"
EVAL_NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
"""The run's clock: Monday 19 October 2026, 10:00 in Madrid. Persona dates are against it."""
EVAL_TODAY = date(2026, 10, 19)

type OutcomeLabel = Literal[
    "qualified",
    "disqualified",
    "abandoned",
    "declined_consent",
    "withdrawn",
    "paused",
    "in_progress",
]
type Formality = Literal["tu", "usted", "en"]
TAG_BASE = 0xE0000
"""Unicode tag characters mirror ASCII invisibly (U+E0000 + code point)."""


class ScriptedMessage(BaseModel):
    """Sent verbatim instead of asking the simulator. `when` is `ask:<field>` or `turn:<n>`."""

    model_config = ConfigDict(frozen=True)

    when: str
    text: str
    matrix_language: MatrixLanguage = MatrixLanguage.ES
    explicit_language_request: Language | None = None
    hidden: str | None = None
    """ASCII instructions appended as invisible Unicode tag characters, wrapped in a zero-width
    space and a right-to-left override, the way a smuggled prompt would arrive."""

    @property
    def message(self) -> str:
        if not self.hidden:
            return self.text
        tags = "".join(chr(TAG_BASE + ord(c)) for c in self.hidden if 0x20 <= ord(c) < 0x7F)
        return f"{self.text}\u200b{tags}\u202e"


class Expect(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: OutcomeLabel
    disqualify_reason: str | None = None
    booked: bool | None = None
    interview_mode: str | None = None
    meet_link: bool | None = None
    talent_pool: bool | None = None
    resumed: bool | None = None
    flags: tuple[str, ...] = ()
    allow_flags: tuple[str, ...] = ()
    final_language: Language | None = None
    read_back: str | None = None
    """A field Ana must read back exactly once before keeping it (an implausible value)."""
    slot_search_days: int | None = None
    """With no free time, how many days ahead the search must reach before the saved close."""


class Persona(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    country: Country
    formality: Formality
    context: ConversationContext
    truth: dict[str, Any]
    score_fields: tuple[str, ...] | None = None
    behaviour: str
    language: str
    phone_via: Literal["text", "widget"] = "text"
    interviewers: Literal["free", "booked_out"] = "free"
    """`booked_out`: every recruiter's calendar is busy for any window searched, and no organizer
    sends in-person invitations, so in person is not offered."""
    prior_application: bool = False
    scripted: tuple[ScriptedMessage, ...] = ()
    expect: Expect

    @property
    def scored_fields(self) -> tuple[str, ...]:
        return self.score_fields if self.score_fields is not None else tuple(self.truth)

    def card(self) -> dict[str, Any]:
        """What the simulator is told about the person it plays."""
        return {
            "who": self.behaviour,
            "language": self.language,
            "facts": self.truth,
            "phone": "use the phone widget" if self.phone_via == "widget" else "type it",
        }


class _Personas(BaseModel):
    personas: list[Persona] = Field(default_factory=list)


def load_personas(path: Path = PERSONAS_PATH) -> list[Persona]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    personas = _Personas(personas=raw).personas
    ids = [p.id for p in personas]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate persona ids")
    return personas


def select_personas(spec: str, path: Path = PERSONAS_PATH) -> list[Persona]:
    personas = load_personas(path)
    if spec.strip() in ("", "all"):
        return personas
    wanted = [part.strip() for part in spec.split(",") if part.strip()]
    by_id = {p.id: p for p in personas}
    unknown = [w for w in wanted if w not in by_id]
    if unknown:
        raise ValueError(f"unknown personas: {', '.join(unknown)}")
    return [by_id[w] for w in wanted]
