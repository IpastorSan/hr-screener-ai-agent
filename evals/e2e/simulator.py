"""The simulated candidate: reads Ana's message and widget, answers in text or with a widget tap.

`LLMSimulator` role-plays a persona with a model (the `LLM_SIMULATOR` role) through the eval
client, so every call is ZDR-checked and settled in the cost ledger. `ScriptedSimulator`
answers straight from the persona's ground truth with no model: it proves the pipeline offline
and backs the tests.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from evals.e2e.personas import EVAL_TODAY, Persona
from evals.model_selection.client import EvalClient, ModelSpec
from sazon.engine.models import Availability, Language, MatrixLanguage, PhoneInput, Schedule
from sazon.engine.service_areas import get_location
from sazon.service import WidgetAnswer

MAX_TURNS = 25
DEFAULT_SIMULATOR = "anthropic/claude-haiku-5.5"

type Speaker = Literal["ana", "candidate"]


class SimulatorError(RuntimeError):
    pass


class CandidateTurn(BaseModel):
    """One candidate move: a typed message, a tap on a widget, the phone entry, or leaving."""

    kind: Literal["text", "tap", "multi", "phone", "stop"] = Field(
        description=(
            "text: type a message. tap: press one button or slot (value). multi: tick several "
            "options (values). phone: fill the phone widget. stop: Ana has closed the chat or "
            "you leave."
        )
    )
    text: str | None = Field(default=None, description="The typed message, for kind text.")
    value: str | None = Field(default=None, description="The [value] of the button or slot.")
    values: list[str] | None = Field(default=None, description="The [values] ticked, for multi.")
    phone_country: Literal["ES", "MX"] | None = None
    phone_number: str | None = Field(default=None, description="Digits typed in the phone box.")
    matrix_language: MatrixLanguage = Field(
        default=MatrixLanguage.ES,
        description=(
            "Grammatical frame of your typed message: es, en, or mixed when no frame "
            "dominates. A Spanish sentence with English words inserted is es."
        ),
    )
    explicit_language_request: Language | None = Field(
        default=None, description="Set only when this message explicitly asks to switch language."
    )


class Simulator(Protocol):
    async def next_turn(
        self,
        persona: Persona,
        transcript: Sequence[tuple[Speaker, str]],
        ui: Mapping[str, Any] | None,
        action: Mapping[str, Any] | None,
    ) -> CandidateTurn: ...


# --- what the candidate sees ---


def describe_ui(ui: Mapping[str, Any] | None) -> str:
    """The widget under Ana's message, as the candidate sees it, with the values to send."""
    if not ui:
        return "(no widget: type your answer)"
    kind = ui.get("kind")
    if kind == "quick_replies":
        text = "Buttons: " + " | ".join(_option(o) for o in ui["options"])
        if ui.get("link"):
            text += f" (link: {ui['link']['label']})"
        return text + ". You can tap one or type."
    if kind == "multi_select":
        return (
            "Multi-select, tick one or more then send: "
            + " | ".join(_option(o) for o in ui["options"])
            + ". You can also type."
        )
    if kind == "slots":
        slots = " | ".join(f"[{s['id']}] {s['label']}, {s['location']}" for s in ui["slots"])
        return f"Interview times, tap one: {slots}. Or type (e.g. none suit you)."
    if kind == "confirm":
        rows = "; ".join(f"{f['label']}: {f['value']}" for f in ui["fields"])
        return f"Summary card: {rows}. Buttons: [yes] it's correct | [no] change something."
    if kind == "phone":
        return (
            f"Phone entry: country picker ES +34 / MX +52 (selected {ui['selected']}) and a "
            "number box. Use kind phone, or type the number."
        )
    if kind == "booking" and ui.get("mode") == "video":
        link = ui.get("meetUrl") or "the link comes by email"
        return f"Booked video interview card: {ui['label']}, Google Meet: {link}."
    if kind == "booking":
        return f"Booked interview card: {ui['label']}, {ui['location']}, {ui['address']}."
    return f"(widget {kind})"


def _option(option: Mapping[str, Any]) -> str:
    return f"[{option['value']}] {option['label']}"


def widget_values(ui: Mapping[str, Any] | None) -> set[str]:
    if not ui:
        return set()
    kind = ui.get("kind")
    if kind in ("quick_replies", "multi_select"):
        return {o["value"] for o in ui["options"]}
    if kind == "slots":
        return {s["id"] for s in ui["slots"]}
    if kind == "confirm":
        return {"yes", "no"}
    return set()


def to_message(turn: CandidateTurn, ui: Mapping[str, Any] | None) -> str | WidgetAnswer:
    """What the web client would send for this move. A tap on a value the widget does not
    have is sent as typed text, as is anything when there is no matching widget."""
    kind = ui.get("kind") if ui else None
    allowed = widget_values(ui)
    if turn.kind == "tap" and turn.value and turn.value in allowed:
        widget_kind = {"quick_replies": "quick_replies", "slots": "slots", "confirm": "confirm"}
        return WidgetAnswer(value=turn.value, kind=widget_kind.get(str(kind)))
    if turn.kind == "multi" and turn.values and set(turn.values) <= allowed:
        return WidgetAnswer(value=list(turn.values), kind="multi_select")
    if turn.kind == "phone" and kind == "phone" and turn.phone_number:
        country = turn.phone_country or ui["selected"]  # pyright: ignore[reportOptionalSubscript]
        return WidgetAnswer(
            value=PhoneInput(country=country, number=turn.phone_number), kind="phone"
        )
    text = turn.text or turn.value or ", ".join(turn.values or []) or turn.phone_number
    if not text:
        raise SimulatorError(f"empty candidate move: {turn.model_dump(exclude_none=True)}")
    return text


# --- the model simulator ---

SIMULATOR_INSTRUCTIONS = f"""\
You role-play a job candidate chatting on a careers web page with Ana, the AI recruiting \
assistant of Grupo Sazón (delivery drivers, Spain and Mexico). Today is \
{EVAL_TODAY:%A %d %B %Y}.

Stay in character as described in <persona>: their facts, attitude, language and the things \
they do during the chat. Only state facts from the persona; if asked something it doesn't \
cover, answer plausibly and briefly. Write like a real person in a phone chat: short, \
informal, one message per turn, never more than 2 sentences. Never mention that you are \
simulating or testing.

Each turn choose one move:
- tap: press one of the buttons or interview slots shown, by its [value]. Prefer this for \
yes/no and for the summary card, unless the persona says otherwise.
- multi: tick options of a multi-select by their [values].
- phone: fill the phone widget (country and digits) when the persona uses the widget.
- text: type a message. Use it whenever there is no widget, or the persona would type.
- stop: only when Ana has closed the conversation (said goodbye, confirmed the interview, \
ended the chat) and nothing is left to answer.

Label every move: matrix_language is the grammatical frame of what you typed (es, en, or \
mixed); a tap is labelled with the conversation's current language. Set \
explicit_language_request only when the message explicitly asks to switch language."""


class LLMSimulator:
    def __init__(self, client: EvalClient, spec: ModelSpec, case_prefix: str = "") -> None:
        self.client = client
        self.spec = spec
        self.case_prefix = case_prefix
        self.cost_usd = 0.0
        self.calls = 0

    async def next_turn(
        self,
        persona: Persona,
        transcript: Sequence[tuple[Speaker, str]],
        ui: Mapping[str, Any] | None,
        action: Mapping[str, Any] | None,
    ) -> CandidateTurn:
        result = await self.client.structured(
            self.spec,
            role="simulator",
            case_id=f"{self.case_prefix}{persona.id}",
            instructions=SIMULATOR_INSTRUCTIONS,
            prompt=simulator_prompt(persona, transcript, ui),
            output_type=CandidateTurn,
        )
        self.cost_usd += result.record.cost_usd
        self.calls += 1
        if result.output is None:
            raise SimulatorError(result.record.error or "simulator returned nothing")
        return result.output


def simulator_prompt(
    persona: Persona, transcript: Sequence[tuple[Speaker, str]], ui: Mapping[str, Any] | None
) -> str:
    lines = [f"{'Ana' if who == 'ana' else 'You'}: {text}" for who, text in transcript]
    return (
        f"<persona>\n{json.dumps(persona.card(), ensure_ascii=False, indent=1)}\n</persona>\n"
        f"<chat>\n{chr(10).join(lines)}\n</chat>\n"
        f"<widget>\n{describe_ui(ui)}\n</widget>\n"
        "Your next move:"
    )


# --- the scripted simulator ---

AVAILABILITY_WORDS = {
    Availability.FULL_TIME: "full time",
    Availability.PART_TIME: "part time",
    Availability.WEEKENDS: "weekends",
}
SCHEDULE_WORDS = {
    Schedule.MORNING: "morning",
    Schedule.AFTERNOON: "afternoon",
    Schedule.EVENING: "evening",
    Schedule.FLEXIBLE: "flexible",
}
YES_ACTIONS = frozenset({"greet", "confirm_value", "confirm_summary", "offer_pause", "disqualify"})
STOP_ACTIONS = frozenset({"end", "confirm_booking"})


class ScriptedSimulator:
    """Answers each pending question from the persona's truth, one field per message, taps
    whatever widget fits. No model, no cost: for offline runs and tests."""

    cost_usd = 0.0
    calls = 0

    async def next_turn(
        self,
        persona: Persona,
        transcript: Sequence[tuple[Speaker, str]],
        ui: Mapping[str, Any] | None,
        action: Mapping[str, Any] | None,
    ) -> CandidateTurn:
        language = Language.EN if persona.formality == "en" else Language.ES
        frame = MatrixLanguage(language.value)
        kind = (action or {}).get("kind")
        prompt = _pending(action)
        pending_kind = (prompt or {}).get("kind")
        if kind in STOP_ACTIONS or (kind == "disqualify" and not ui):
            return CandidateTurn(kind="stop", matrix_language=frame)
        if pending_kind in YES_ACTIONS or kind in YES_ACTIONS:
            return CandidateTurn(kind="tap", value="yes", matrix_language=frame)
        if pending_kind == "ask_mode" and ui and ui.get("kind") == "quick_replies":
            mode = str(persona.truth.get("interview_mode", "onsite"))
            return CandidateTurn(kind="tap", value=mode, matrix_language=frame)
        if pending_kind == "offer_slots" and ui and ui.get("kind") == "slots":
            return CandidateTurn(kind="tap", value=ui["slots"][0]["id"], matrix_language=frame)
        field = (prompt or {}).get("field")
        if field is None:
            return CandidateTurn(kind="text", text="ok", matrix_language=frame)
        return _answer_field(persona, str(field), ui, frame)


def _pending(action: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The question in front of the candidate: the action, or the one it repeats."""
    if action is None:
        return None
    return action.get("follow_up") or action


def _answer_field(
    persona: Persona, field: str, ui: Mapping[str, Any] | None, frame: MatrixLanguage
) -> CandidateTurn:
    truth = persona.truth
    allowed = widget_values(ui)
    if field == "phone":
        number = str(truth["phone"])
        country = "MX" if number.startswith("+52") else "ES"
        local = number.removeprefix("+34").removeprefix("+52")
        if ui and ui.get("kind") == "phone":
            return CandidateTurn(
                kind="phone", phone_country=country, phone_number=local, matrix_language=frame
            )
        return CandidateTurn(kind="text", text=number, matrix_language=frame)
    if field == "has_license":
        value = "yes" if truth.get("has_license") else "no"
        if value in allowed:
            return CandidateTurn(kind="tap", value=value, matrix_language=frame)
        return CandidateTurn(kind="text", text=value, matrix_language=frame)
    if field == "city":
        location = get_location(str(truth.get("location_id", "")))
        if location is None:
            city = str(truth.get("city", "Oviedo"))
            return CandidateTurn(kind="text", text=city, matrix_language=frame)
        if location.location_id in allowed:
            return CandidateTurn(kind="tap", value=location.location_id, matrix_language=frame)
        text = location.name.removeprefix("Sazón ")
        return CandidateTurn(kind="text", text=text, matrix_language=frame)
    value = truth.get(field)
    if isinstance(value, list) and value and set(value) <= allowed:
        return CandidateTurn(kind="multi", values=list(value), matrix_language=frame)
    if isinstance(value, str) and value in allowed:
        return CandidateTurn(kind="tap", value=value, matrix_language=frame)
    return CandidateTurn(kind="text", text=_spoken(field, value), matrix_language=frame)


def _spoken(field: str, value: object) -> str:
    if field == "availability" and isinstance(value, list):
        return ", ".join(AVAILABILITY_WORDS[Availability(v)] for v in value)
    if field == "schedule" and isinstance(value, str):
        return SCHEDULE_WORDS[Schedule(value)]
    if field == "platforms" and isinstance(value, list):
        return ", ".join(str(v).replace("_", " ") for v in value) or "none"
    if field == "experience_years":
        return f"{value:g}" if isinstance(value, int | float) else str(value)
    return str(value) if value is not None else "no"
