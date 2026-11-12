from typing import Any

import pytest

from evals.model_selection.scoring import (
    VAGUE,
    aggregate_extraction,
    aggregate_replies,
    check_reply,
    consistency,
    constant_sentiment_accuracy,
    extraction_signature,
    field_matches,
    normalize_field,
    percentile,
    score_extraction,
    sentiment_accuracy,
)
from sazon.engine.models import FieldCandidate, TurnExtraction


def _extraction(fields: dict[str, Any], **overrides: Any) -> TurnExtraction:
    values: dict[str, Any] = {
        "intent": "answer",
        "fields": {k: FieldCandidate(value=v, confidence=0.9) for k, v in fields.items()},
        "language": "es",
        "matrix_language": "es",
        "english_ratio": 0.0,
        "code_switched": False,
        "sentiment": "neutral",
    }
    values.update(overrides)
    return TurnExtraction.model_validate(values)


def _gold(fields: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    gold: dict[str, Any] = {
        "intent": "answer",
        "fields": fields,
        "language": "es",
        "matrix_language": "es",
        "explicit_language_request": None,
        "sentiment": "neutral",
    }
    gold.update(overrides)
    return gold


@pytest.mark.parametrize(
    ("name", "gold", "predicted", "country"),
    [
        ("city", "DF", "CDMX", "MX"),
        ("city", "zapopam", "Zapopan", "MX"),
        ("city", "Gràcia", "gracia", "ES"),
        ("experience_years", 0.25, "3 meses", "ES"),
        ("experience_years", 1.5, 1.505, "ES"),
        ("experience_years", "unos meses", "algunos meses", "MX"),
        ("platforms", ["glovo", "uber_eats"], ["Glovo", "Uber"], "ES"),
        ("platforms", ["didi_food"], ["Didi"], "MX"),
        ("availability", ["part_time", "weekends"], ["weekends", "part time"], "ES"),
        ("schedule", "flexible", "cualquiera", "ES"),
        ("has_license", "in_progress", "en trámite", "ES"),
        ("has_license", True, "sí", "MX"),
        ("phone", "612345678", "+34 612 34 56 78", "ES"),
        ("phone", "55 1234 5678", "+525512345678", "MX"),
        ("email", "Ana.Lopez@Example.com", "ana.lopez@example.com ", "ES"),
        ("full_name", "José Pérez", "jose perez", "MX"),
        ("start_date", "2026-10-26", "2026-10-26", "ES"),
    ],
)
def test_equivalent_values_match(name: str, gold: Any, predicted: Any, country: Any) -> None:
    assert field_matches(name, gold, predicted, country=country)


@pytest.mark.parametrize(
    ("name", "gold", "predicted", "country"),
    [
        ("city", "Gràcia", "Barcelona", "ES"),
        ("city", "Zapopan", "Guadalajara", "MX"),
        ("experience_years", 0.25, 3, "ES"),
        ("experience_years", 1.5, 1.52, "ES"),
        ("has_license", "in_progress", False, "ES"),
        ("has_license", False, True, "ES"),
        ("start_date", "2026-10-26", "2026-10-27", "ES"),
        ("platforms", ["glovo"], ["glovo", "rappi"], "MX"),
        ("schedule", "morning", "evening", "ES"),
    ],
)
def test_different_values_do_not_match(name: str, gold: Any, predicted: Any, country: Any) -> None:
    assert not field_matches(name, gold, predicted, country=country)


def test_vague_experience_normalizes_to_marker() -> None:
    assert normalize_field("experience_years", "unos meses", country="ES") == VAGUE


def test_score_counts_tp_fp_fn() -> None:
    gold = _gold({"full_name": "Lucía", "has_license": True, "city": "Gràcia"})
    predicted = _extraction(
        {"full_name": "Lucía", "has_license": True, "city": "Barcelona", "schedule": "mañanas"}
    )
    score = score_extraction(gold, predicted, country="ES")
    assert score.tp == {"full_name": 1, "has_license": 1}
    assert score.fp == {"city": 1, "schedule": 1}
    assert score.fn == {"city": 1}
    assert not score.fields_exact
    assert score.checks["intent"]


def test_empty_predicted_values_count_as_absent() -> None:
    score = score_extraction(
        _gold({"experience_years": 3}),
        _extraction({"experience_years": 3, "platforms": []}),
        country="MX",
    )
    assert score.fields_exact


def test_alternative_gold_labels_are_accepted() -> None:
    gold = _gold({}, intent=["off_topic", "abuse"])
    predicted = _extraction({}, intent="abuse")
    assert score_extraction(gold, predicted, country="ES").checks["intent"]


@pytest.mark.parametrize(
    ("question", "parts", "standalone", "split"),
    [
        ("¿Cuánto se paga en México?", [], True, False),
        ("¿y en México también?", [], False, False),
        ("¿Cuánto se paga a la hora en España?", [], False, False),
        ("¿Cuánto se paga y dan casco?", ["¿Cuánto se paga?", "¿Dan casco?"], True, True),
        ("¿Cuánto se paga y dan casco?", ["¿Cuánto se paga?", "¿Hay seguro?"], True, False),
    ],
)
def test_standalone_and_sub_question_checks(
    question: str, parts: list[str], standalone: bool, split: bool
) -> None:
    gold = _gold(
        {},
        intent="question",
        question=True,
        question_has=["paga"] if parts else ["mexico", ["paga", "sueldo"]],
        question_lacks=["espana"],
        sub_questions=2,
        sub_questions_have=[["paga", "sueldo"], "casco"],
    )
    predicted = _extraction({}, intent="question", question=question, sub_questions=parts)
    checks = score_extraction(gold, predicted, country="ES").checks
    assert checks["question_standalone"] is standalone
    assert checks["sub_questions"] is split


def test_language_checks() -> None:
    gold = _gold({}, matrix_language="es", explicit_language_request="en", code_switched=True)
    predicted = _extraction(
        {}, matrix_language="mixed", explicit_language_request="en", code_switched=True
    )
    checks = score_extraction(gold, predicted, country="MX").checks
    assert not checks["matrix_language"]
    assert checks["explicit_language_request"]
    assert checks["code_switched"]


def test_schema_failure_counts_every_gold_field_as_missed() -> None:
    score = score_extraction(_gold({"city": "Madrid"}), None, country="ES")
    assert not score.schema_valid
    assert score.fn == {"city": 1}
    assert not any(score.checks.values())


def test_aggregate_extraction() -> None:
    gold = _gold({"city": "Madrid", "start_date": "2026-10-26"})
    good = score_extraction(
        gold, _extraction({"city": "madrid", "start_date": "2026-10-26"}), country="ES"
    )
    retried = score_extraction(
        gold,
        _extraction({"city": "Madrid", "start_date": "2026-10-27"}),
        country="ES",
        output_retries=1,
    )
    report = aggregate_extraction([good, retried])
    assert report["schema_valid"] == 1.0
    assert report["schema_valid_first_try"] == 0.5
    assert report["field_precision"] == 0.75
    assert report["field_recall"] == 0.75
    assert report["per_field"]["start_date"]["exact"] == 0.5
    assert report["checks"]["start_date"] == 0.5
    assert report["fields_exact"] == 0.5


def test_consistency() -> None:
    a = extraction_signature(_extraction({"city": "DF"}), country="MX")
    b = extraction_signature(_extraction({"city": "CDMX"}), country="MX")
    c = extraction_signature(_extraction({"city": "Zapopan"}), country="MX")
    assert a == b
    assert consistency({"x": [a, b], "y": [a, c], "z": [a]}) == 0.5


ES_MX = {"language": "es", "register": "usted", "question": True}


def test_good_usted_reply_passes() -> None:
    result = check_reply("Gracias, Jorge. ¿Tiene licencia de manejo vigente?", ES_MX)
    assert result.passed, result.checks


@pytest.mark.parametrize(
    ("text", "failed"),
    [
        ("Gracias, Jorge. ¿Tienes licencia de manejo?", "register"),
        ("¿Tiene licencia? ¿Y moto propia?", "one_question_last"),
        ("¿Tiene licencia? Gracias.", "one_question_last"),
        ("Perfecto 🙌 ¿Tiene licencia de manejo?", "no_emoji"),
        ("Un reclutador le confirmará. ¿Tiene licencia?", "no_promises"),
        ("Thanks, Jorge. Do you have a driving license?", "language"),
        ("¿Tiene licencia " + "a" * 290 + "?", "max_chars"),
    ],
)
def test_reply_failures(text: str, failed: str) -> None:
    result = check_reply(text, ES_MX)
    assert not result.checks[failed]


def test_tu_register_rejects_usted() -> None:
    expect = {"language": "es", "register": "tu"}
    assert not check_reply("¿Usted tiene carnet?", expect).checks["register"]
    assert check_reply("¿Tienes carnet de conducir?", expect).checks["register"]


def test_voice_word_limit_and_patterns() -> None:
    expect = {
        "max_words": 15,
        "question": False,
        "must_include": [r"entrevista"],
        "must_not_include": [r"\d+ ?€"],
    }
    result = check_reply("No tengo ese dato; puedes preguntarlo en la entrevista.", expect)
    assert result.passed, result.checks
    long_reply = " ".join(["palabra"] * 16)
    assert not check_reply(long_reply, expect).checks["max_words"]
    assert not check_reply("Pagamos 9 € la hora en la entrevista.", expect).checks[
        r"exclude:\d+ ?€"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Our team will contact you soon.",
        "Te llamaremos mañana.",
        "Le contactaremos pronto.",
        "Una persona del equipo revisará su caso.",
        "Ya estás contratado.",
        "El equipo de selección revisará su solicitud.",
        "A recruiter will call you.",
    ],
)
def test_promises_are_flagged(text: str) -> None:
    assert not check_reply(text, {}).checks["no_promises"]


@pytest.mark.parametrize(
    "text",
    [
        "Listo, su entrevista por videollamada con el equipo de selección es el jueves 22.",
        "Done, your video interview with the recruiting team is on Thursday 22.",
    ],
)
def test_naming_the_video_interviewer_is_no_promise(text: str) -> None:
    assert check_reply(text, {}).checks["no_promises"]


def test_aggregate_replies_groups_pattern_checks() -> None:
    expect = {"must_include": ["a", "b"]}
    report = aggregate_replies([check_reply("a b", expect), check_reply("a", expect)])
    assert report["checks"]["include"] == 0.5
    assert report["all_checks_pass"] == 0.5


def test_missing_reply_fails() -> None:
    assert not check_reply(None, {}).passed


def test_percentile() -> None:
    assert percentile([], 50) is None
    assert percentile([1.0], 95) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5


GOLDS = {
    "a": {"sentiment": "neutral"},
    "b": {"sentiment": ["neutral", "frustrated"]},
    "c": {"sentiment": "frustrated"},
    "d": {"sentiment": "positive"},
}


def test_sentiment_accuracy_is_recomputed_from_stored_calls():
    calls = [
        {"case_id": "a", "output": {"sentiment": "neutral"}},
        {"case_id": "b", "output": {"sentiment": "frustrated"}},
        {"case_id": "c", "output": {"sentiment": "neutral"}},
        {"case_id": "d", "output": None},
        {"case_id": "not-scored", "output": {"sentiment": "neutral"}},
    ]
    assert sentiment_accuracy(calls, GOLDS) == 0.5
    assert sentiment_accuracy([], GOLDS) is None


def test_a_constant_label_baseline():
    assert constant_sentiment_accuracy(GOLDS) == 0.5
    assert constant_sentiment_accuracy(GOLDS, "frustrated") == 0.5
    assert constant_sentiment_accuracy(GOLDS, "positive") == 0.25
