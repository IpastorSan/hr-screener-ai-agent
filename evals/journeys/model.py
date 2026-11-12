"""Journey cards (`cases/<group>.yaml`): scripted candidate journeys over one or more sessions.

See `evals/journeys/README.md` for the format.
"""

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sazon.engine.models import Language
from sazon.engine.state import ConversationContext
from sazon.engine.validators import Country

CASES_DIR = Path(__file__).resolve().parent / "cases"
DEFAULT_AGENT = "riders"
"""The agent of the journeys in `cases/` itself; another agent's are in `cases/<agent>/`."""
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([dhm])\s*$")
DURATION_UNITS = {"d": "days", "h": "hours", "m": "minutes"}
BUSY_WINDOW = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})-(\d{2}:\d{2})$")
"""A busy window in a `scheduling.busy` list: "2026-10-27 00:00-11:50", local time."""
DEFAULT_CONTEXT: dict[Country, dict[str, str]] = {
    "ES": {"locale": "es-ES", "timezone": "Europe/Madrid"},
    "MX": {"locale": "es-MX", "timezone": "America/Mexico_City"},
}

type Register = Literal["tu", "usted"]


class _Card(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Expect(_Card):
    """What must hold after a step, checked against Ana's last action, the state and the reply.

    Every key is optional; `live` holds expectations only checked with real models (an offline
    run, with the keyword extractor, template replies and no retrieval, skips them)."""

    asks: str | None = None
    """The question in front of the candidate: a field name, or consent, confirm, mode, slot,
    talent_pool, pause, cancel; `none` when nothing is asked."""
    action: str | None = None
    outcome: str | None = None
    """qualified, disqualified, abandoned, declined_consent, withdrawn, or in_progress."""
    reason: str | None = None
    register_: Register | None = Field(default=None, alias="register")
    language: Language | None = None
    booked: bool | None = None
    mode: str | None = None
    interviewer: str | None = None
    """Who runs the booked interview: the recruiter by video, the location's manager in person."""
    resumed: bool | None = None
    faq_source: str | list[str] | None = None
    """A FAQ id (`privacy`), several (each cited), `any` FAQ, or `none`."""
    reply_matches: str | None = None
    reply_not_matches: str | None = None
    card_matches: str | None = None
    """A regex over the summary card's rows, "Zona: Roma · local Cuauhtémoc; ..."."""
    closed: bool | None = None
    ui: str | None = None
    """The widget under the reply (quick_replies, phone, slots, confirm, booking) or `none`."""
    live: "Expect | None" = None


class PhoneTap(_Card):
    country: Country
    number: str


class Step(_Card):
    """One of `say` (typed text), `tap` (a widget answer) or `persona` (simulated stretch)."""

    say: str | None = None
    offline_say: str | None = None
    """Sent instead of `say` offline, where the keyword extractor can't read free text."""
    tap: str | list[str] | PhoneTap | None = None
    """An option value, `slot:<n>` (the n-th offered time), several values (multi-select), or
    `{country, number}` for the phone picker."""
    persona: str | None = None
    facts: dict[str, Any] | None = None
    until: Expect | None = None
    max_turns: int = 12
    expect: Expect | None = None

    @model_validator(mode="after")
    def _one_move(self) -> Self:
        moves = [m for m in (self.say, self.tap, self.persona) if m is not None]
        if len(moves) != 1:
            raise ValueError("a step has exactly one of say, tap or persona")
        if self.persona is not None and self.until is None:
            raise ValueError("a persona step needs `until`")
        return self


class Scheduling(_Card):
    """What interviews the journey's world offers. `modes`: only these are on offer (in
    person needs the location's manager, video the recruiters, who are switched off without
    it). `busy`: days (ISO dates, or `all`) the recruiters' calendars are full, so no video
    time is free then, or windows of a day ("2026-10-27 00:00-11:50", each recruiter's local
    time); in-person times are the locations' and stay free."""

    modes: list[Literal["onsite", "video"]] = Field(
        default_factory=lambda: ["onsite", "video"], min_length=1
    )
    busy: Literal["all"] | list[date | str] = Field(default_factory=list)

    @field_validator("busy", mode="before")
    @classmethod
    def _busy_windows(cls, value: object) -> object:
        """Whole days as dates, windows as they are written."""
        if not isinstance(value, list):
            return value
        items: list[object] = []
        for item in value:
            if isinstance(item, str) and not BUSY_WINDOW.match(item):
                try:
                    item = date.fromisoformat(item)
                except ValueError:
                    raise ValueError(f"busy {item!r}: a date or 'YYYY-MM-DD HH:MM-HH:MM'") from None
            items.append(item)
        return items


class Session(_Card):
    after: str | None = None
    """Clock advance before the session starts: `3d`, `5h`, `30m`."""
    restart: bool = False
    """The session opens through the previous chat's restart button ("Empezar de nuevo"), not a
    new tab: an application still open there is closed first."""
    country: Country | None = None
    """A country-wide posting of this country instead of the journey's posting."""
    register_: Register | Literal["mixed"] | None = Field(default=None, alias="register")
    """The register owed in this session; `mixed` skips the per-reply register check (a return
    through another country's posting), so steps check `register` themselves."""
    steps: list[Step]

    @property
    def advance(self) -> timedelta:
        return parse_duration(self.after) if self.after else timedelta()


class Journey(_Card):
    id: str
    description: str
    country: Country
    location: str | None = None
    """A posting at this location id (`es-mad-centro`); none is a country-wide posting."""
    context: dict[str, Any] = Field(default_factory=dict)
    """Overrides for the chat context (locale, timezone, language)."""
    register_: Register | None = Field(default=None, alias="register")
    """The register Ana owes the candidate in Spanish; default tú in Spain, usted in Mexico."""
    candidate: dict[str, Any] = Field(default_factory=dict)
    """Facts and language for `persona` steps (the e2e persona truth fields)."""
    channel: Literal["web", "voice"] = "web"
    """`voice`: the chat runs as a phone call (spoken replies, no card to read), typed as the
    speech to text would write it; its steps never tap."""
    xfail: str | None = None
    """Known to fail until this issue lands: a failure is reported, not counted."""
    scheduling: Scheduling = Field(default_factory=Scheduling)
    """Only some interview modes, or recruiters busy on some days (default: both, all free)."""
    sessions: list[Session] = Field(min_length=1)
    """`steps:` at the top level is shorthand for one session."""
    group: str = ""
    agent: str = DEFAULT_AGENT
    """The agent it screens for: the directory of its card (`load_journeys`)."""

    @model_validator(mode="before")
    @classmethod
    def _one_session(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "steps" not in data:
            return data
        if "sessions" in data:
            raise ValueError("use either steps or sessions")
        card: dict[str, Any] = dict(data)  # pyright: ignore[reportUnknownArgumentType]
        card["sessions"] = [{"steps": card.pop("steps")}]
        return card

    @property
    def chat_context(self) -> ConversationContext:
        return ConversationContext(
            **{
                "country": self.country,
                "location_id": self.location,
                **DEFAULT_CONTEXT[self.country],
                **self.context,
            }
        )

    @property
    def expected_register(self) -> Register:
        return self.register_ or ("usted" if self.country == "MX" else "tu")

    def session_context(self, session: Session) -> ConversationContext:
        """The chat context of a session: the journey's, or another country's posting."""
        if session.country is None or session.country == self.country:
            return self.chat_context
        defaults = DEFAULT_CONTEXT[session.country]
        return ConversationContext(
            country=session.country, locale=defaults["locale"], timezone=defaults["timezone"]
        )

    def session_register(self, session: Session) -> Register | Literal["mixed"]:
        if session.register_ is not None:
            return session.register_
        if session.country is not None and session.country != self.country:
            return "usted" if session.country == "MX" else "tu"
        return self.expected_register


def parse_duration(text: str) -> timedelta:
    match = DURATION.match(text)
    if match is None:
        raise ValueError(f"bad duration {text!r}; use e.g. 3d, 5h, 30m")
    return timedelta(**{DURATION_UNITS[match.group(2)]: float(match.group(1))})


def load_group(path: Path, agent: str = DEFAULT_AGENT) -> list[Journey]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [Journey.model_validate({**item, "group": path.stem, "agent": agent}) for item in raw]


def load_journeys(
    group: str | None = None,
    ids: str | None = None,
    cases_dir: Path = CASES_DIR,
    agent: str | None = None,
) -> list[Journey]:
    """Every journey in `cases_dir`, the riders' at its top and each other agent's in its own
    directory (`cases/teachers/`); or one agent's, one group's, or the comma-separated `ids`."""
    paths = [(path, DEFAULT_AGENT) for path in sorted(cases_dir.glob("*.yaml"))]
    paths += [(path, path.parent.name) for path in sorted(cases_dir.glob("*/*.yaml"))]
    if agent:
        paths = [(path, owner) for path, owner in paths if owner == agent]
    if group:
        paths = [(path, owner) for path, owner in paths if path.stem == group]
        if not paths:
            raise ValueError(f"unknown group {group!r}")
    journeys = [j for path, owner in paths for j in load_group(path, owner)]
    seen = [j.id for j in journeys]
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate journey ids")
    if ids:
        wanted = [i.strip() for i in ids.split(",") if i.strip()]
        by_id = {j.id: j for j in journeys}
        unknown = [w for w in wanted if w not in by_id]
        if unknown:
            raise ValueError(f"unknown journeys: {', '.join(unknown)}")
        journeys = [by_id[w] for w in wanted]
    return journeys
