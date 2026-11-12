"""Draft extra extraction cases from the hand-written seeds with a strong model.

    uv run --project backend python -m evals.model_selection.generate_variations \
        --model anthropic/claude-opus-5.5 --out /tmp/drafts.yaml

The output is a draft: every label is checked by hand before it is merged into
extraction_cases.yaml.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from evals.model_selection.cases import EXTRACTION_CASES, load_cases
from evals.model_selection.client import EvalClient, ModelSpec
from sazon.llm import prompting

BATCHES: dict[str, dict[str, int]] = {
    "fields": {"multi_field": 8, "relative_date": 8, "contact": 8},
    "gates": {"city": 9, "license": 7},
    "profile": {"platforms_experience": 7, "availability_schedule": 10},
    "spanglish": {"spanglish": 22},
    "behaviour": {
        "question_midflow": 6,
        "off_topic_sarcasm": 6,
        "abuse": 4,
        "opt_out": 4,
        "confirm": 3,
    },
    "adversarial": {"correction": 6, "injection": 7},
}

GUIDANCE = {
    "multi_field": "Several fields in one message, mixed locales.",
    "relative_date": (
        "Relative and absolute dates: mañana, pasado mañana, el viernes, next Monday, "
        "dentro de un mes, el 15, a finales de noviembre (use the 30th), 'cuando quieran' "
        "(today), a date more than 90 days out. Some with today 2026-10-23 (a Friday)."
    ),
    "contact": (
        "Phones with spaces, dots, dashes, +34/+52 prefixes, 0034, the Mexican '1' after +52, "
        "spoken digits; emails with capitals, spaces, 'arroba' and 'punto'."
    ),
    "city": (
        "Typos (Guadalajra, Barcelna), aliases (DF, GDL, MTY, BCN, Pucela, TJ), zones "
        "(Polanco, Russafa, Satélite), ambiguous answers (Del Valle, CDMX), out-of-area cities."
    ),
    "license": (
        "Indirect no (me la quitaron, nunca la saqué, solo bici, voy en transporte), in "
        "progress (tengo el teórico, estoy en la autoescuela, permiso de aprendiz), yes in "
        "local forms (B y A2, licencia tipo A, carnet de moto)."
    ),
    "platforms_experience": (
        "Platforms named loosely (Didi, Uber, Rapi, Glovo), unknown ones as free text, "
        "months (8 meses, medio año, año y medio), vague (unos meses, a while), none."
    ),
    "availability_schedule": (
        "Availability and schedule answers, incl. 'me da igual', 'solo findes', 'por las "
        "tardes', 'noches', two periods; a few full-name answers with accents and two surnames."
    ),
    "spanglish": (
        "Mostly Mexican Spanglish. Insertional (Spanish frame with English words: 'full time', "
        "'delivery', 'weekends', 'part time', 'schedule'), alternating (whole clauses in each "
        "language: matrix_language mixed), English-framed with Spanish words, and explicit "
        "language requests in both directions (incl. polite forms like 'can we switch to "
        "English?' or '¿podemos seguir en español?'). Some with 1-3 prior turns in the other "
        "language. A few from Spain."
    ),
    "question_midflow": "Questions about pay, vehicle, hours, documents, whether Ana is a bot.",
    "off_topic_sarcasm": "Off-topic messages and sarcasm that must not fill fields.",
    "abuse": "Insults aimed at Ana or the company, Spanish and English, some with an answer too.",
    "opt_out": "Wanting to stop or withdraw, direct and indirect.",
    "confirm": "Accepting the privacy notice, confirming a summary or a slot.",
    "correction": "Corrections of an earlier answer, with the earlier turn in prior.",
    "injection": (
        "Prompt injection in Spanish and English: role play, fake SYSTEM lines, JSON payloads, "
        "base64, HTML/script in a name, instructions split across turns, and invisible "
        "variants. For invisible variants put the raw text with escapes (\\u200B zero-width, "
        "\\u202E bidi override, \\U000E00xx tag characters) in `raw` and the sanitized text "
        "(those characters removed, NFKC applied) in `message`. Real answers inside an "
        "injection are still extracted."
    ),
}

INSTRUCTIONS = """You write test cases for a structured-extraction benchmark. Each case is one
message from a delivery-driver job candidate talking to Ana, an AI recruiting assistant, plus the
gold extraction a careful annotator would produce under the labeling rules below.

Output a YAML list only (no code fences, no commentary), in the same shape as the seed cases.
Use flow style for prior turns and fields. Every case has: id, category, locale (es-ES, es-MX,
en, spanglish-MX or spanglish-ES), country (ES or MX, only when locale is en), stage, today,
prior (0-3 turns), message, optional raw, gold, source: generated.

Make messages realistic: lowercase, missing accents, typos, WhatsApp-style brevity, regional
slang (Spain: 'vale', 'curro', 'finde'; Mexico: 'nomás', 'ahorita', 'chamba', 'güey' rarely).
Mexico conversations use usted in Ana's turns. Every gold label must follow the rules exactly;
include only fields the candidate stated in the message. If a label is genuinely debatable, give
a list of acceptable values for intent/language/matrix_language/sentiment, or add a short `notes`.

Service areas (for realistic cities): Spain: Madrid (Centro, Malasaña, Lavapiés, Chamberí,
Tetuán, Usera, Carabanchel), Barcelona (Eixample, Gràcia, Sants, Raval), L'Hospitalet, Valencia
(Ruzafa, Benimaclet), Sevilla (Triana, Nervión), Málaga, Bilbao, Zaragoza, Alicante, Murcia,
Palma, Las Palmas, Valladolid (Pucela), Granada. Mexico: CDMX (Del Valle, Narvarte, Roma,
Condesa, Coyoacán, Polanco, Santa Fe, Iztapalapa, Lindavista, Tlalpan), Naucalpan (Satélite),
Guadalajara, Zapopan, Tlaquepaque, Monterrey, San Pedro Garza García, Guadalupe, Puebla,
Querétaro, Mérida, León, Tijuana, Cancún, Toluca, Aguascalientes, San Luis Potosí, Hermosillo.

Labeling rules (the same prompt the extractor sees):

{rules}
"""


def _seed_block(seeds: list[dict[str, object]]) -> str:
    return yaml.safe_dump(seeds, allow_unicode=True, sort_keys=False, width=100)


async def _generate(model: str, batches: list[str]) -> list[str]:
    seeds = load_cases(EXTRACTION_CASES)
    rules = prompting.prompt("extract.md")
    client = EvalClient(run_id=f"generate-{datetime.now(UTC):%Y%m%dT%H%M%S}")
    spec = ModelSpec.parse(model)
    drafts: list[str] = []
    try:
        for batch in batches:
            counts = BATCHES[batch]
            examples = [s for s in seeds if s["category"] in counts][:8] or seeds[:4]
            asks = "\n".join(
                f"- {n} cases of category `{cat}`: {GUIDANCE[cat]} Ids gen-{cat}-01 onwards."
                for cat, n in counts.items()
            )
            prompt = (
                f"Seed cases:\n\n{_seed_block(examples)}\n\nWrite:\n{asks}\n\n"
                "Use today 2026-10-19 (a Monday) unless stated otherwise."
            )
            result = await client.text(
                spec,
                role="generator",
                case_id=batch,
                instructions=INSTRUCTIONS.format(rules=rules),
                prompt=prompt,
                stream=False,
                max_tokens=9000,
            )
            print(
                json.dumps(
                    {
                        "batch": batch,
                        "ok": result.record.ok,
                        "error": result.record.error,
                        "cost": round(result.record.cost_usd, 4),
                        "ledger": round(client.ledger.spent, 4),
                    }
                ),
                flush=True,
            )
            if result.output:
                drafts.append(f"# batch: {batch}\n{result.output.strip()}\n")
    finally:
        await client.aclose()
    return drafts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="anthropic/claude-opus-5.5")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batches", default=",".join(BATCHES))
    args = parser.parse_args()
    drafts = asyncio.run(_generate(args.model, args.batches.split(",")))
    args.out.write_text("\n".join(drafts))


if __name__ == "__main__":
    main()
