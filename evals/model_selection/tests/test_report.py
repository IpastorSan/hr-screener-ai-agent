"""The report table, rebuilt from a stored extraction run without calling a model."""

import json

from evals.model_selection.__main__ import (
    baseline_line,
    intervals_table,
    judge_intervals,
    report_rows,
    report_table,
)
from evals.model_selection.client import RESULTS_DIR

FINAL = RESULTS_DIR / "model_selection_2026-10-19_extraction_final.json"


def test_sentiment_accuracy_and_the_neutral_baseline_come_from_the_stored_run():
    extraction = json.loads(FINAL.read_text())

    rows = {row["model"]: row for row in report_rows(extraction, None, None)}

    assert rows["google/gemini-3.5-flash-lite@minimal"]["sentiment"] == 0.9394
    assert rows["z-ai/glm-5.3-flash@minimal"]["sentiment"] == 0.9697
    for label, row in rows.items():
        assert row["sentiment"] == extraction["models"][label]["quality"]["checks"]["sentiment"]
    assert "| Sentiment |" in report_table(list(rows.values()))
    assert baseline_line(extraction) == (
        "Constant `neutral` sentiment baseline: 95.2% (157 of 165 cases accept `neutral`)."
    )


def test_rescoring_uses_the_stored_outputs_and_todays_labels():
    """SAZ-41: var-sched-es-03 became a list; a stored output with both blocks now passes."""
    from evals.model_selection.__main__ import rescore_extraction
    from evals.model_selection.cases import EXTRACTION_CASES, load_cases

    cases = load_cases(EXTRACTION_CASES)
    case = next(c for c in cases if c["id"] == "var-sched-es-03")
    output = {
        "intent": "answer",
        "fields": {"schedule": {"value": ["morning", "afternoon"], "confidence": 0.9}},
        "language": "es",
        "matrix_language": "es",
        "english_ratio": 0.0,
        "explicit_language_request": None,
        "code_switched": False,
        "sentiment": "neutral",
        "question": None,
        "question_topic": None,
    }
    record = {"output_retries": 0}
    extraction = {
        "meta": {"run_id": "x"},
        "models": {"m": {"quality": {"field_f1": 0.5, "fields_exact": 0.0, "case_pass": 0.0}}},
        "calls": [
            {
                "model": "m",
                "case_id": case["id"],
                "output": output,
                "record": record,
                "score": {"pass": False},
            },
            {"model": "m", "case_id": "gone-case", "output": output, "record": record},
        ],
    }

    payload, deltas = rescore_extraction(extraction, cases)

    assert [call["case_id"] for call in payload["calls"]] == ["var-sched-es-03"]
    assert payload["calls"][0]["score"]["pass"] is True
    assert deltas["m"]["changed_cases"] == ["var-sched-es-03: pass"]
    assert deltas["m"]["case_pass"] == {"before": 0.0, "after": 1.0}
    assert payload["meta"]["rescored_from_stored_outputs"]


ANCHORED = RESULTS_DIR / "model_selection_2026-10-19_judge_anchored.json"


def test_the_stored_judge_intervals_are_reproduced_from_the_verdicts():
    """QA-B6: the case-level bootstrap (10,000 resamples, seed 24) recomputes every stored
    interval, count and score in `vs_anchor` from the stored verdicts alone."""
    judge = json.loads(ANCHORED.read_text())

    intervals = judge_intervals(judge)

    assert intervals is not None
    assert intervals["models"] == judge["vs_anchor"]["models"]
    assert intervals["models"]["moonshotai/kimi-k2.6"]["ci95"] == [0.2561, 0.5183]
    assert "| moonshotai/kimi-k2.6 | 39% | 26%-52% | 27 / 10 / 45 |" in intervals_table(intervals)
