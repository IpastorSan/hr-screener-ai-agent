"""Code-only scorers for the model benchmark.

Extracted field values are compared after passing both sides through the engine validators, so
two spellings that the engine would treat the same ("DF" / "CDMX", "3 meses" / 0.25) score equal.
"""

import re
import statistics
from collections import Counter
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sazon.agents.riders.readers import (
    validate_availability,
    validate_platforms,
    validate_schedule,
)
from sazon.engine.models import TurnExtraction
from sazon.engine.text import fold
from sazon.engine.validators import (
    LICENSE_IN_PROGRESS,
    Ambiguous,
    Country,
    ReasonCode,
    Valid,
    validate_credential,
    validate_location,
    validate_person_name,
    validate_phone,
    validate_years,
)
from sazon.llm.reply_checks import follow_up_promise

FIELD_NAMES = (
    "full_name",
    "phone",
    "email",
    "has_license",
    "city",
    "availability",
    "schedule",
    "experience_years",
    "platforms",
    "start_date",
)
EXPERIENCE_TOLERANCE = 0.01
VAGUE = "vague"

# --- extraction ---


def _text(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def normalize_field(name: str, value: object, *, country: Country) -> Hashable:
    """Reduce a field value to the form the engine would act on."""
    if value is None:
        return None
    match name:
        case "full_name":
            # As the engine stores it: surname abbreviations expanded ("Hdz" -> "Hernández").
            result = validate_person_name(value)
            if isinstance(result, Valid):
                return fold(result.value)
            return fold(_text(value))
        case "phone":
            result = validate_phone(_text(value), country=country)
            if isinstance(result, Valid):
                return result.value
            return re.sub(r"[^\d+]", "", _text(value))
        case "email":
            return re.sub(r"\s+", "", _text(value)).lower()
        case "has_license":
            if value == LICENSE_IN_PROGRESS:
                return LICENSE_IN_PROGRESS
            result = validate_credential(value)
            if isinstance(result, Valid):
                if result.note is ReasonCode.LICENSE_IN_PROGRESS:
                    return LICENSE_IN_PROGRESS
                return result.value
            return fold(_text(value))
        case "city":
            return _normalize_city(_text(value), country)
        case "availability":
            result = validate_availability(value)
            if isinstance(result, Valid):
                return frozenset(result.value)
            return fold(_text(value))
        case "schedule":
            # A list since SAZ-41; a stored output or label with one string reads the same.
            result = validate_schedule(value)
            if isinstance(result, Valid):
                return frozenset(result.value)
            return fold(_text(value))
        case "experience_years":
            result = validate_years(value)
            if isinstance(result, Valid):
                return round(result.value, 2)
            if (
                isinstance(result, Ambiguous)
                and result.reason is ReasonCode.EXPERIENCE_NEEDS_NUMBER
            ):
                return VAGUE
            return fold(_text(value))
        case "platforms":
            result = validate_platforms(value)
            if isinstance(result, Valid):
                return frozenset(result.value.platforms)
            return fold(_text(value))
        case "start_date":
            return _text(value).strip()[:10]
        case _:
            return fold(_text(value))


_CITY_PARTS = re.compile(r"\s*(?:,|\bpor\b|\ben\b|\bnear\b|\bin\b)\s*", re.IGNORECASE)


def _normalize_city(text: str, country: Country) -> Hashable:
    """Resolve a city answer to a location; "Sevilla, Triana" and "Triana, Sevilla" agree."""
    result = validate_location(text, country=country)
    if isinstance(result, Valid):
        return ("location", result.value)
    parts = {
        part_result.value
        for part in _CITY_PARTS.split(text)
        if part.strip()
        and isinstance(part_result := validate_location(part, country=country), Valid)
    }
    if len(parts) == 1:
        return ("location", parts.pop())
    if isinstance(result, Ambiguous):
        return ("ambiguous", frozenset(result.options))
    return ("text", fold(text))


def field_matches(name: str, gold: object, predicted: object, *, country: Country) -> bool:
    g = normalize_field(name, gold, country=country)
    p = normalize_field(name, predicted, country=country)
    if isinstance(g, float) and isinstance(p, float):
        return abs(g - p) <= EXPERIENCE_TOLERANCE
    return g == p


def _accepts(gold: object, predicted: object) -> bool:
    if isinstance(gold, list):
        return predicted in gold
    return predicted == gold


def sentiment_accuracy(
    calls: Iterable[Mapping[str, Any]], golds: Mapping[str, Mapping[str, Any]]
) -> float | None:
    """Share of stored extraction calls whose sentiment the gold accepts, over the calls whose
    case is in `golds`. A call without output counts as wrong. Recomputed from a run's stored
    `calls`, so no model is called again."""
    results = [
        _accepts(golds[call["case_id"]]["sentiment"], (call.get("output") or {}).get("sentiment"))
        for call in calls
        if call["case_id"] in golds
    ]
    return _ratio(sum(results), len(results))


def constant_sentiment_accuracy(
    golds: Mapping[str, Mapping[str, Any]], label: str = "neutral"
) -> float | None:
    """What an extractor that always answers `label` would score on these cases."""
    hits = [_accepts(gold["sentiment"], label) for gold in golds.values()]
    return _ratio(sum(hits), len(hits))


@dataclass
class ExtractionScore:
    schema_valid: bool
    first_try_valid: bool
    tp: Counter[str] = field(default_factory=Counter)
    fp: Counter[str] = field(default_factory=Counter)
    fn: Counter[str] = field(default_factory=Counter)
    gold_fields: Counter[str] = field(default_factory=Counter)
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def fields_exact(self) -> bool:
        return self.schema_valid and not self.fp and not self.fn


def score_extraction(
    gold: Mapping[str, Any],
    predicted: TurnExtraction | None,
    *,
    country: Country,
    output_retries: int = 0,
) -> ExtractionScore:
    gold_fields: Mapping[str, Any] = gold.get("fields") or {}
    score = ExtractionScore(
        schema_valid=predicted is not None,
        first_try_valid=predicted is not None and output_retries == 0,
        gold_fields=Counter(gold_fields.keys()),
    )
    if predicted is None:
        score.fn.update(gold_fields.keys())
        score.checks = dict.fromkeys(
            ("intent", "language", "matrix_language", "explicit_language_request", "sentiment"),
            False,
        )
        if "start_date" in gold_fields:
            score.checks["start_date"] = False
        return score

    predicted_fields = {
        name: c.value for name, c in predicted.fields.items() if c.value not in ([], "")
    }
    for name in set(gold_fields) | set(predicted_fields):
        in_gold, in_pred = name in gold_fields, name in predicted_fields
        if in_gold and in_pred:
            if field_matches(name, gold_fields[name], predicted_fields[name], country=country):
                score.tp[name] += 1
            else:
                score.fp[name] += 1
                score.fn[name] += 1
        elif in_pred:
            score.fp[name] += 1
        else:
            score.fn[name] += 1

    def enum(value: object) -> object:
        return getattr(value, "value", value)

    score.checks = {
        "intent": _accepts(gold["intent"], enum(predicted.intent)),
        "language": _accepts(gold["language"], enum(predicted.language)),
        "matrix_language": _accepts(gold["matrix_language"], enum(predicted.matrix_language)),
        "explicit_language_request": _accepts(
            gold.get("explicit_language_request"), enum(predicted.explicit_language_request)
        ),
        "sentiment": _accepts(gold["sentiment"], enum(predicted.sentiment)),
    }
    if "start_date" in gold_fields:
        score.checks["start_date"] = score.tp["start_date"] == 1
    if "code_switched" in gold:
        score.checks["code_switched"] = gold["code_switched"] == predicted.code_switched
    if "question" in gold:
        score.checks["question"] = bool(gold["question"]) == bool(predicted.question)
    if "question_has" in gold:
        score.checks["question_standalone"] = question_matches(
            predicted.question, gold["question_has"], gold.get("question_lacks") or ()
        )
    if "sub_questions" in gold:
        parts = predicted.sub_questions
        groups = gold.get("sub_questions_have") or ()
        score.checks["sub_questions"] = len(parts) == gold["sub_questions"] and all(
            any(question_matches(part, [group]) for part in parts) for group in groups
        )
    return score


def question_matches(question: str | None, has: Sequence[Any], lacks: Sequence[str] = ()) -> bool:
    """Whether a question names every group in `has` (each a word or a list of alternatives,
    matched as folded substrings) and none of `lacks`: a rewrite that stands on its own, with
    nothing the conversation did not give."""
    text = fold(question or "")
    if not text:
        return False
    named = all(any(fold(alt) in text for alt in _labels(group)) for group in has)
    return named and not any(fold(word) in text for word in lacks)


def _labels(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def aggregate_extraction(scores: Sequence[ExtractionScore]) -> dict[str, Any]:
    tp, fp, fn, gold = Counter[str](), Counter[str](), Counter[str](), Counter[str]()
    for s in scores:
        tp.update(s.tp)
        fp.update(s.fp)
        fn.update(s.fn)
        gold.update(s.gold_fields)
    per_field = {
        name: {
            "precision": _ratio(tp[name], tp[name] + fp[name]),
            "recall": _ratio(tp[name], tp[name] + fn[name]),
            "exact": _ratio(tp[name], gold[name]),
            "support": gold[name],
        }
        for name in FIELD_NAMES
        if gold[name] or fp[name]
    }
    total_tp, total_fp, total_fn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    precision = _ratio(total_tp, total_tp + total_fp)
    recall = _ratio(total_tp, total_tp + total_fn)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else None
    check_names = sorted({name for s in scores for name in s.checks})
    checks = {
        name: _ratio(
            sum(s.checks[name] for s in scores if name in s.checks),
            sum(name in s.checks for s in scores),
        )
        for name in check_names
    }
    n = len(scores)
    return {
        "cases": n,
        "schema_valid": _ratio(sum(s.schema_valid for s in scores), n),
        "schema_valid_first_try": _ratio(sum(s.first_try_valid for s in scores), n),
        "field_precision": precision,
        "field_recall": recall,
        "field_f1": f1,
        "fields_exact": _ratio(sum(s.fields_exact for s in scores), n),
        "checks": checks,
        "per_field": per_field,
    }


def extraction_signature(predicted: TurnExtraction | None, *, country: Country) -> Hashable:
    """A hashable view of an extraction, for consistency across repeats."""
    if predicted is None:
        return None
    fields = frozenset(
        (name, normalize_field(name, c.value, country=country))
        for name, c in predicted.fields.items()
    )
    return (
        predicted.intent,
        predicted.matrix_language,
        predicted.explicit_language_request,
        fields,
    )


def consistency(signatures_by_case: Mapping[str, Sequence[Hashable]]) -> float | None:
    """Share of cases whose repeats all produced the same extraction."""
    repeated = [sigs for sigs in signatures_by_case.values() if len(sigs) > 1]
    if not repeated:
        return None
    return round(sum(len(set(sigs)) == 1 for sigs in repeated) / len(repeated), 4)


# --- replies ---

_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f900-\U0001f9ff\U00002b00-\U00002bff"
    "\U0000fe0f\U0000200d\U00002190-\U000021ff\U00002300-\U000023ff]"
)
_PROMISES = re.compile(
    r"\b(?:un[ao]? reclutador(?:a)?|un[ao]? persona (?:del|de nuestro) equipo|recursos humanos"
    r"|el equipo de (?:selección|reclutamiento)|un agente humano"
    r"|(?:te|le|lo|la) (?:contactar|llamar|escribir)(?:emos|án|a)\b"
    r"|(?:se|nos) pondr(?:á|emos|án) en contacto|revisar(?:á|án|emos) (?:tu|su)"
    r"|a (?:recruiter|human|member of (?:our|the) team)|our recruiters?"
    r"|(?:will|'ll) (?:contact|call|reach out to|get back to|review)"
    r"|est(?:á|ás) contratad[oa]|the job is yours|you(?:'re| are) hired"
    r"|el puesto es (?:tuyo|suyo))",
    re.IGNORECASE,
)
_INTERVIEWER = re.compile(
    r"\b(?:con|with) (?:el equipo de (?:selección|reclutamiento)|the recruiting team"
    r"|un[ao]? reclutador(?:a)?|a recruiter)\b",
    re.IGNORECASE,
)
"""Who a video interview is with ("con el equipo de selección"): naming the interviewer is not a
promise that someone will review or contact the candidate."""


def promise_in(text: str) -> str | None:
    """A promise of a human handoff or an outcome, past the name of the video interviewer."""
    match = _PROMISES.search(_INTERVIEWER.sub(" ", text))
    return match.group(0) if match else None


_TU_MARKERS = re.compile(
    r"\b(?:tú|tienes|puedes|quieres|eres|estás|te|tu|tus|ti|contigo|dime|cuéntame|avísame"
    r"|prefieres|vives|sabes|necesitas|has|escríbeme|elige|dinos|indícame|confírmame"
    r"|cuentas|trabajas|podrías|gustaría que me digas)\b",
    re.IGNORECASE,
)
_USTED_MARKERS = re.compile(r"\b(?:usted|ustedes)\b", re.IGNORECASE)
_ES_WORDS = re.compile(
    r"\b(?:el|la|los|las|de|que|y|en|un|una|por|para|con|su|tu|es|lo|del|al|se|si|sí|no"
    r"|me|le|te|gracias|hola|tiene|tienes|puede|puedes|cuál|qué|cómo)\b",
    re.IGNORECASE,
)
_EN_WORDS = re.compile(
    r"\b(?:the|and|you|your|is|are|to|of|in|for|with|what|do|have|can|we|it|on|thanks"
    r"|hi|hello|please|would|which|that|this)\b",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s", re.MULTILINE)


def detect_language(text: str) -> str | None:
    es = len(_ES_WORDS.findall(text))
    en = len(_EN_WORDS.findall(text))
    if es == en:
        return None
    return "es" if es > en else "en"


@dataclass
class ReplyCheck:
    checks: dict[str, bool]

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def check_reply(text: str | None, expect: Mapping[str, Any]) -> ReplyCheck:
    if text is None:
        return ReplyCheck({"produced": False})
    text = text.strip()
    question_marks = text.count("?")
    checks: dict[str, bool] = {"produced": bool(text)}
    checks["max_chars"] = len(text) <= int(expect.get("max_chars", 300))
    if "max_words" in expect:
        checks["max_words"] = len(text.split()) <= int(expect["max_words"])
    wants_question = expect.get("question")
    if wants_question is True:
        checks["one_question_last"] = question_marks == 1 and text.endswith("?")
    elif wants_question is False:
        checks["no_question"] = question_marks == 0
    else:
        checks["one_question_last"] = question_marks == 0 or (
            question_marks == 1 and text.endswith("?")
        )
    if language := expect.get("language"):
        checks["language"] = detect_language(text) == language
    register = expect.get("register")
    if register == "usted":
        checks["register"] = not _TU_MARKERS.search(text)
    elif register == "tu":
        checks["register"] = not _USTED_MARKERS.search(text)
    checks["no_emoji"] = not _EMOJI.search(text)
    checks["no_promises"] = promise_in(text) is None and follow_up_promise(text) is None
    checks["no_bullets"] = not _BULLET.search(text)
    for pattern in expect.get("must_include") or ():
        checks[f"include:{pattern}"] = re.search(pattern, text, re.IGNORECASE) is not None
    for pattern in expect.get("must_not_include") or ():
        checks[f"exclude:{pattern}"] = re.search(pattern, text, re.IGNORECASE) is None
    return ReplyCheck(checks)


def _family(name: str) -> str:
    return name.split(":", 1)[0]


def aggregate_replies(results: Iterable[ReplyCheck]) -> dict[str, Any]:
    results = list(results)
    totals: Counter[str] = Counter()
    passes: Counter[str] = Counter()
    for result in results:
        families: dict[str, bool] = {}
        for name, ok in result.checks.items():
            fam = _family(name)
            families[fam] = families.get(fam, True) and ok
        for fam, ok in families.items():
            totals[fam] += 1
            passes[fam] += ok
    return {
        "cases": len(results),
        "all_checks_pass": _ratio(sum(r.passed for r in results), len(results)),
        "checks": {fam: _ratio(passes[fam], totals[fam]) for fam in sorted(totals)},
    }


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1], 3)
