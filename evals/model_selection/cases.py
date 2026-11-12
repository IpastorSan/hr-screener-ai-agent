"""The benchmark datasets and how each case becomes the production LLM layer's input."""

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from sazon.agents.riders import FieldName, Step
from sazon.engine.actions import Action
from sazon.engine.models import Language, Stage
from sazon.engine.state import (
    Asking,
    ChoosingMode,
    Closed,
    Consent,
    Contact,
    ConversationState,
    Interview,
    Offering,
    Phase,
    Qualified,
    Summary,
)
from sazon.engine.validators import Country
from sazon.llm.interface import Channel, ChatTurn, ReplyContext, faq_snippet

HERE = Path(__file__).resolve().parent
EXTRACTION_CASES = HERE / "extraction_cases.yaml"
REPLY_CASES = HERE / "reply_cases.yaml"
REPLY_TODAY = date(2026, 10, 19)

Case = dict[str, Any]

# The phase the engine is in at each stage; contact asks for phone and email together.
PHASES: dict[str, Phase] = {
    Stage.CONSENT: Consent(),
    Step.NAME: Asking(field=FieldName.FULL_NAME),
    Step.CONTACT: Contact(),
    Step.LICENSE: Asking(field=FieldName.HAS_LICENSE),
    Step.CITY: Asking(field=FieldName.CITY),
    Step.AVAILABILITY: Asking(field=FieldName.AVAILABILITY),
    Step.SCHEDULE: Asking(field=FieldName.SCHEDULE),
    Step.EXPERIENCE: Asking(field=FieldName.EXPERIENCE_YEARS),
    Step.START_DATE: Asking(field=FieldName.START_DATE),
    Stage.CONFIRM: Summary(),
    Stage.MODE: Interview(sub=ChoosingMode()),
    Stage.SCHEDULING: Interview(sub=Offering()),
    Stage.DONE: Closed(variant=Qualified()),
}
ACTION: TypeAdapter[Action] = TypeAdapter(Action)


def load_cases(path: Path) -> list[Case]:
    cases = yaml.safe_load(path.read_text())
    if not isinstance(cases, list):
        raise ValueError(f"{path} must hold a list of cases")
    ids = [case["id"] for case in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids in {path.name}: {sorted(duplicates)}")
    return cases


def spread(cases: list[Case], n: int | None) -> list[Case]:
    """Pick n cases evenly across the file, so a small run still covers every category."""
    if n is None or n >= len(cases):
        return cases
    step = len(cases) / n
    return [cases[int(i * step)] for i in range(n)]


def case_country(case: Case) -> Country:
    return case.get("country") or ("ES" if case["locale"].endswith("-ES") else "MX")


def case_language(case: Case) -> Language:
    return Language.EN if case["locale"] == "en" else Language.ES


def extraction_state(case: Case) -> ConversationState:
    """The engine state the extractor sees: stage, awaited field, country and language."""
    stage = case["stage"]
    return ConversationState(
        phase=PHASES[stage],
        country=case_country(case),
        language=case_language(case),
        consented=stage != Stage.CONSENT,
    )


def extraction_history(case: Case) -> list[ChatTurn]:
    return [ChatTurn(role=turn["role"], text=turn["text"]) for turn in case.get("prior") or ()]


def reply_action(case: Case) -> Action:
    """The case's engine action, with the language and country of its locale."""
    meta = {"language": case_language(case).value, "country": case_country(case)}
    action = {**meta, **case["action"]}
    if isinstance(follow_up := action.get("follow_up"), dict):
        action["follow_up"] = {**meta, **follow_up}
    return ACTION.validate_python(action)


def reply_context(case: Case, action: Action) -> ReplyContext:
    context = case.get("context") or {}
    snippets = [faq_snippet(entry["q"], entry["a"]) for entry in context.get("faq") or ()]
    return ReplyContext.for_action(
        action,
        today=REPLY_TODAY,
        channel=Channel(case["channel"]),
        faq_snippets=snippets,
        candidate_name=context.get("candidate_name"),
        last_message=context.get("last_message"),
    )
