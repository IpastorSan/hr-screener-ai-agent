from collections import Counter
from datetime import date
from typing import Any

import pytest

from evals.model_selection.cases import (
    EXTRACTION_CASES,
    PHASES,
    REPLY_CASES,
    case_country,
    extraction_history,
    extraction_state,
    load_cases,
    reply_action,
    reply_context,
)
from evals.model_selection.scoring import FIELD_NAMES
from sazon.engine.models import (
    Availability,
    Intent,
    Language,
    MatrixLanguage,
    Platform,
    Schedule,
    Sentiment,
)
from sazon.engine.text import sanitize_input

EXTRACTION = load_cases(EXTRACTION_CASES)
REPLIES = load_cases(REPLY_CASES)
LOCALES = {"es-ES", "es-MX", "en", "spanglish-MX", "spanglish-ES"}
SCENARIOS = {
    "greet",
    "reask",
    "clarify_city",
    "disqualify_license",
    "license_in_progress",
    "disqualify_zone",
    "answer_faq",
    "unknown_faq",
    "deescalate_abuse",
    "offer_pause",
    "confirm_summary",
    "offer_slots",
    "reengage",
}


def _labels(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


@pytest.mark.parametrize("case", EXTRACTION, ids=[c["id"] for c in EXTRACTION])
def test_extraction_case_is_well_formed(case: dict[str, Any]) -> None:
    assert case["locale"] in LOCALES
    assert case["locale"] != "en" or case.get("country") in {"ES", "MX"}
    assert case["stage"] in PHASES
    assert isinstance(case["today"], date)
    assert case["message"].strip()
    if "raw" in case:
        assert sanitize_input(case["raw"])[0] == case["message"]
    else:
        assert sanitize_input(case["message"])[0] == case["message"]
    for turn in case.get("prior") or ():
        assert turn["role"] in {"assistant", "candidate"}
    gold = case["gold"]
    assert all(Intent(v) for v in _labels(gold["intent"]))
    assert all(Language(v) for v in _labels(gold["language"]))
    assert all(MatrixLanguage(v) for v in _labels(gold["matrix_language"]))
    assert all(Sentiment(v) for v in _labels(gold["sentiment"]))
    explicit = gold.get("explicit_language_request")
    assert explicit is None or Language(explicit)
    fields = gold["fields"] or {}
    assert set(fields) <= set(FIELD_NAMES)
    for value in fields.get("availability", []):
        assert Availability(value)
    if "schedule" in fields:
        assert isinstance(fields["schedule"], list), "schedule is a list since SAZ-41"
        assert all(Schedule(v) for v in fields["schedule"])
        assert Schedule.FLEXIBLE not in fields["schedule"] or len(fields["schedule"]) == 1
    for value in fields.get("platforms", []):
        assert value in set(Platform) or value[:1].isupper()
    if "has_license" in fields:
        assert fields["has_license"] in (True, False, "in_progress")
    if "start_date" in fields:
        assert isinstance(fields["start_date"], date)
    if "experience_years" in fields:
        assert isinstance(fields["experience_years"], int | float | str)
    state = extraction_state(case)
    assert state.country == case_country(case)
    assert len(extraction_history(case)) == len(case.get("prior") or ())


def test_extraction_coverage() -> None:
    categories = Counter(case["category"] for case in EXTRACTION)
    locales = Counter(case["locale"] for case in EXTRACTION)
    assert len(EXTRACTION) >= 140
    assert categories["spanglish"] >= 25
    assert locales["spanglish-MX"] >= 20
    assert min(locales[loc] for loc in ("es-ES", "es-MX", "en")) >= 15
    assert (
        sum(case["gold"].get("explicit_language_request") is not None for case in EXTRACTION) >= 6
    )
    assert sum("raw" in case for case in EXTRACTION) >= 3


def test_standalone_and_multi_part_questions_are_covered() -> None:
    """QA-BR: rewrites in every locale with negatives, and multi-part questions."""
    rewrites = [case for case in EXTRACTION if case["category"] == "standalone_question"]
    parts = [case for case in EXTRACTION if case["category"] == "multi_part"]
    assert len(rewrites) + len(parts) >= 15
    assert {case["locale"] for case in rewrites} >= {"es-ES", "es-MX", "en"}
    assert sum(bool(case["gold"].get("question_lacks")) for case in rewrites) >= 3
    assert sum(case["gold"]["sub_questions"] == 0 for case in parts) >= 2
    for case in [*rewrites, *parts]:
        gold = case["gold"]
        assert gold["question"] is True
        assert 0 <= gold["sub_questions"] <= 3
        assert len(gold.get("sub_questions_have") or ()) <= gold["sub_questions"]
        assert gold.get("question_has") or gold["sub_questions"] or case["category"] == "multi_part"


def test_polite_frustration_is_covered() -> None:
    """Frustrated without an insult: the label that drives the engine, apart from abuse."""
    cases = [case for case in EXTRACTION if case["category"] == "sentiment"]
    assert len(cases) >= 6
    assert {case["locale"] for case in cases} >= {"es-ES", "es-MX", "en"}
    for case in cases:
        assert case["gold"]["sentiment"] == "frustrated"
        assert "abuse" not in _labels(case["gold"]["intent"])


@pytest.mark.parametrize("case", REPLIES, ids=[c["id"] for c in REPLIES])
def test_reply_case_is_well_formed(case: dict[str, Any]) -> None:
    assert case["scenario"] in SCENARIOS
    action = reply_action(case)
    assert action.country == case["country"]
    ctx = reply_context(case, action)
    assert ctx.channel == case["channel"]
    assert case["locale"] in {"es-ES", "es-MX", "en"}
    assert case["channel"] in {"web", "voice"}
    expect = case["expect"]
    assert expect["language"] == ("en" if case["locale"] == "en" else "es")
    assert expect.get("register") == {"es-ES": "tu", "es-MX": "usted", "en": None}[case["locale"]]
    assert case["channel"] != "voice" or expect.get("max_words")


def test_reply_coverage() -> None:
    assert len(REPLIES) >= 36
    assert {case["scenario"] for case in REPLIES} == SCENARIOS
    assert Counter(case["channel"] for case in REPLIES)["voice"] >= 6
    assert set(Counter(case["locale"] for case in REPLIES)) == {"es-ES", "es-MX", "en"}
