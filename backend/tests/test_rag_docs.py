"""Company documents: content and consistency, chunking, mixed retrieval and sources (SAZ-29)."""

import os
import re
import zlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sazon.api.app import create_app
from sazon.engine import ConversationContext
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.service_areas import load_locations
from sazon.engine.validators import Country
from sazon.llm import FakeLLM, ReplyContext, ReplyResult
from sazon.rag.index import (
    DOCS_DIR,
    LABEL_SEPARATOR,
    DocChunk,
    FaqEntry,
    RagIndex,
    Snippet,
    build_cache,
    chunk_document,
    citations,
    load_corpus,
    load_documents,
    load_entries,
    parse_document,
    section_slug,
)
from sazon.service import ScreeningService
from sazon.storage.repo import Repo, init_db, make_engine

# One worker builds and reads the cached vectors: `make check` runs these with `--dist loadgroup`.
pytestmark = pytest.mark.xdist_group("embedding-model")

ES, EN = Language.ES, Language.EN
S = LABEL_SEPARATOR
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
DOC_IDS = {
    "driver-handbook",
    "compensation-es",
    "compensation-mx",
    "contract-es",
    "contract-mx",
    "vehicle-insurance-es",
    "vehicle-insurance-mx",
    "hiring-process-es",
    "hiring-process-mx",
    "locations-es",
    "locations-mx",
    "privacy-summary",
}
PRIVACY_EMAIL = "privacidad@gruposazon.example"

# Details only the documents have (the Q&A has the short version or nothing): ES, EN, both
# countries. A hit is a passage of the expected document in the top 3.
DOC_QUESTIONS: list[tuple[str, Language, Country, set[str]]] = [
    ("¿qué pasa si tengo un accidente con la moto?", ES, "MX", {"vehicle-insurance-mx"}),
    ("¿qué tengo que llevar a la entrevista?", ES, "MX", {"hiring-process-mx"}),
    ("¿qué tengo que llevar a la entrevista?", ES, "ES", {"hiring-process-es"}),
    ("¿pagan las noches más?", ES, "ES", {"compensation-es"}),
    ("¿cuánto pagan por kilómetro si uso mi moto?", ES, "MX", {"compensation-mx"}),
    ("¿cuánto dura el periodo de prueba?", ES, "ES", {"contract-es"}),
    ("¿cuántos días de vacaciones tengo?", ES, "MX", {"contract-mx"}),
    ("¿dónde está el local de Valencia?", ES, "ES", {"locations-es"}),
    ("¿a qué hora abre la sucursal de Polanco?", ES, "MX", {"locations-mx"}),
    ("¿cuánto se paga en Mérida?", ES, "MX", {"compensation-mx", "locations-mx"}),
    ("¿cuánto pagan la hora en Sevilla?", ES, "ES", {"compensation-es"}),
    ("¿qué día pagan?", ES, "MX", {"compensation-mx"}),
    (
        "¿hay que pagar la franquicia si choco con la moto de empresa?",
        ES,
        "ES",
        {"vehicle-insurance-es"},
    ),
    ("¿qué hago si se me pincha una rueda?", ES, "ES", {"driver-handbook"}),
    ("¿cuánto tiempo espero si el cliente no contesta?", ES, "MX", {"driver-handbook"}),
    ("¿a qué hora es el turno de cena?", ES, "ES", {"driver-handbook", "locations-es"}),
    ("¿hay examen de manejo en la entrevista?", ES, "MX", {"hiring-process-mx"}),
    ("¿cuánto dan por recomendar a un amigo?", ES, "ES", {"compensation-es"}),
    ("¿pagan doble las horas extra?", ES, "MX", {"compensation-mx"}),
    ("¿quién es el responsable de mis datos?", ES, "ES", {"privacy-summary"}),
    ("¿cuándo pagan el aguinaldo?", ES, "MX", {"compensation-mx", "contract-mx"}),
    ("¿los festivos se pagan?", ES, "ES", {"compensation-es", "contract-es"}),
    ("¿puedo llevarme la moto a casa?", ES, "ES", {"vehicle-insurance-es"}),
    ("¿con cuánto tiempo tengo que avisar si me quiero ir?", ES, "MX", {"contract-mx"}),
    ("what do I bring to the interview in Mexico?", EN, "MX", {"hiring-process-mx"}),
    ("do night shifts pay more?", EN, "ES", {"compensation-es"}),
    ("what happens if I crash the company bike?", EN, "ES", {"vehicle-insurance-es"}),
    ("where is the kitchen in Bilbao?", EN, "ES", {"locations-es"}),
    ("how long is the probation period?", EN, "MX", {"contract-mx"}),
    ("what if the customer doesn't answer the door?", EN, "ES", {"driver-handbook"}),
]


# --- content ---


def test_every_document_exists_in_both_languages_with_matching_front_matter():
    documents = load_documents()
    for language in Language:
        ids = {d.id for d in documents if d.language == language}
        assert ids == DOC_IDS, language
    updated = {(d.id, d.language): d.updated for d in documents}
    for document in documents:
        suffix = document.id[-3:]
        expected = {"-es": "ES", "-mx": "MX"}.get(suffix)
        assert document.country == expected, document.id
        assert "2026-10-19" <= document.updated <= "2026-12-31", document.id
        assert {updated[document.id, language] for language in Language} == {document.updated}
        assert 400 <= document.words() <= 900, (document.id, document.language, document.words())


def test_documents_are_marked_fictional():
    index = (DOCS_DIR / "index.md").read_text(encoding="utf-8")
    assert index.splitlines()[0].startswith("Fictional client content")


def text_of(doc_id: str, language: Language = ES) -> str:
    return (DOCS_DIR / language.value / f"{doc_id}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("language", list(Language))
def test_locations_documents_match_the_service_areas(language):
    for country in ("ES", "MX"):
        text = text_of(f"locations-{country.lower()}", language)
        other = text_of(f"locations-{'mx' if country == 'ES' else 'es'}", language)
        for location in load_locations().values():
            if location.country == country:
                assert f"{location.name}: {location.address}." in text, location.location_id
                for zone in location.zones:
                    assert zone in text, (location.location_id, zone)
            else:
                assert location.address not in text
                assert f"{location.name}: {location.address}." in other


def in_both(fragment: str, document: str, answer: str) -> None:
    assert fragment in document
    assert fragment in answer


def test_documents_agree_with_the_faq_on_pay_contract_and_privacy():
    faq = {(e.id, e.language): e.answer for e in load_entries()}
    pay_es, pay_mx = text_of("compensation-es"), text_of("compensation-mx")
    in_both("9,50 a 11 € brutos por hora", pay_es, faq["pay-es", ES])
    in_both("2,100 a 2,600 pesos (MXN) a la semana", pay_mx, faq["pay-mx", ES])
    in_both("a mes vencido por transferencia", pay_es, faq["pay-es", ES])
    in_both("cada semana", pay_mx, faq["pay-mx", ES])
    # Every city rate stays inside the published range, the night allowance included.
    rates = [float(r.replace(",", ".")) for r in re.findall(r": (\d+,\d+) € brutos", pay_es)]
    assert sorted(rates) == [9.5, 9.75, 10.25]
    assert "plus de nocturnidad de 0,75 €" in pay_es
    assert max(rates) + 0.75 == 11
    weekly = [int(w.replace(",", "")) for w in re.findall(r": (\d,\d{3}) MXN a la semana", pay_mx)]
    assert sorted(weekly) == [2100, 2350, 2600]

    in_both("indefinido", text_of("contract-es"), faq["contract-es", ES])
    for benefit in ("IMSS", "aguinaldo", "vacaciones", "prima vacacional"):
        in_both(benefit, text_of("contract-mx"), faq["contract-mx", ES])
    in_both("todo riesgo", text_of("vehicle-insurance-es"), faq["insurance-es", ES])
    in_both("cobertura amplia", text_of("vehicle-insurance-mx"), faq["insurance-mx", ES])
    for licence in ("A1, A2, A o el B con más de 3 años", "AM"):
        in_both(licence, text_of("vehicle-insurance-es"), faq["license-es", ES])
    for paper in ("INE", "CURP", "RFC", "NSS", "comprobante de domicilio reciente"):
        in_both(paper, text_of("hiring-process-mx"), faq["documents-mx", ES])
    for paper in ("DNI", "NIE", "Seguridad Social", "cuenta bancaria"):
        in_both(paper, text_of("hiring-process-es"), faq["documents-es", ES])
    for language in Language:
        summary = text_of("privacy-summary", language)
        in_both(PRIVACY_EMAIL, summary, faq["privacy", language])
        assert "12 months" in summary or "12 meses" in summary
    for entity in ("Grupo Sazón Restauración, S.L.", "Grupo Sazón México, S.A. de C.V."):
        assert entity in text_of("privacy-summary")
    web = Path(__file__).parents[2] / "web/src/components/careers/privacy-notice.tsx"
    if web.exists():
        assert f'CONTACT_EMAIL = "{PRIVACY_EMAIL}"' in web.read_text(encoding="utf-8")


# --- parsing and chunking ---

SAMPLE = """---
id: sample
title: Manual de prueba
country: MX
language: es
updated: 2026-10-19
---
# Manual de prueba

Introducción breve.

## Seguridad

Primera frase. Segunda frase.

- Casco siempre.
- Guantes también.

### Lluvia

Con lluvia se para.
"""


def test_parse_document_reads_sections_and_lists():
    document = parse_document(SAMPLE)
    assert (document.id, document.title, document.country) == ("sample", "Manual de prueba", "MX")
    assert document.sections == (
        ((), ("Introducción breve.",)),
        (("Seguridad",), ("Primera frase. Segunda frase.", "Casco siempre. Guantes también.")),
        (("Seguridad", "Lluvia"), ("Con lluvia se para.",)),
    )


def test_chunks_keep_doc_section_and_overlap_when_long():
    document = parse_document(SAMPLE)
    chunks = chunk_document(document)
    assert [c.source for c in chunks] == [
        "doc:sample#intro",
        "doc:sample#seguridad",
        "doc:sample#seguridad/lluvia",
    ]
    assert chunks[2].label == S.join(("Manual de prueba", "Seguridad", "Lluvia"))
    assert chunks[0].label == "Manual de prueba"
    assert chunks[1].country == "MX"

    small = chunk_document(document, max_words=4)
    seguridad = [c.text for c in small if c.section == ("Seguridad",)]
    assert seguridad == [
        "Primera frase. Segunda frase.",
        "Segunda frase. Casco siempre.",
        "Casco siempre. Guantes también.",
    ]
    assert len({c.id for c in small}) == len(small)


def test_real_chunks_are_sized_for_retrieval():
    chunks = [c for c in load_corpus() if isinstance(c, DocChunk)]
    assert all(len(c.text.split()) <= 200 for c in chunks)
    words = [len(c.text.split()) for c in chunks]
    assert sum(words) / len(words) >= 50


def test_section_slug_is_ascii():
    assert section_slug(("Qué llevar a la entrevista",)) == "que-llevar-a-la-entrevista"
    assert section_slug(()) == "intro"


def test_doc_snippet_shows_the_section_not_the_source_to_the_responder():
    chunk = chunk_document(parse_document(SAMPLE))[1]
    snippet = chunk.snippet(0.8)
    assert snippet.text.startswith("Q: Seguridad\nA: Primera frase.")
    assert "doc:" not in snippet.text
    assert "Manual" not in snippet.text
    assert snippet.citation() == {
        "source": "doc:sample#seguridad",
        "label": S.join(("Manual de prueba", "Seguridad")),
        "score": 0.8,
    }


def test_faq_snippet_defaults_its_source():
    snippet = Snippet(id="pay-mx", question="¿Cuánto se paga?", answer="x", score=0.9)
    assert (snippet.source, snippet.label) == ("faq:pay-mx", "FAQ: ¿Cuánto se paga?")
    assert snippet.is_faq


def test_citations_are_distinct_and_ordered():
    chunk = chunk_document(parse_document(SAMPLE))[1]
    faq = Snippet(id="helmet", question="¿Dan casco?", answer="Sí.", score=0.9)
    cited = citations([faq, chunk.snippet(0.7), chunk.snippet(0.6)])
    assert [c["source"] for c in cited] == ["faq:helmet", "doc:sample#seguridad"]


# --- mixed retrieval, with a deterministic embedder ---


class HashEmbedder:
    name = "hash-test"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), 256), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                vectors[row, zlib.crc32(word.encode()) % 256] += 1
        return vectors


def doc(id: str, country: str, section: str, text: str) -> DocChunk:
    return DocChunk(
        id=f"{id}#{section}:0",
        doc_id=id,
        title=id,
        section=(section,),
        language=ES,
        country=country,  # type: ignore[arg-type]
        text=text,
    )


def test_documents_are_filtered_by_country(tmp_path):
    items = [
        doc("contract-es", "ES", "prueba", "periodo de prueba de un mes"),
        doc("contract-mx", "MX", "prueba", "periodo de prueba de 30 días"),
    ]
    index = RagIndex(items, HashEmbedder(), doc_min_score=0.1, cache_dir=tmp_path)
    assert [s.source for s in index.retrieve("periodo de prueba", ES, "MX")] == [
        "doc:contract-mx#prueba"
    ]
    assert [s.source for s in index.retrieve("periodo de prueba", ES, "ES")] == [
        "doc:contract-es#prueba"
    ]


def test_a_faq_entry_wins_a_near_tie_with_a_passage(tmp_path):
    entry = FaqEntry(id="tips", language=ES, country=None, question="propinas", answer="todas")
    passage = doc("driver-handbook", "ES", "propinas", "propinas")
    index = RagIndex([passage, entry], HashEmbedder(), min_score=0.1, cache_dir=tmp_path)
    ranked = index.retrieve("propinas", ES, "ES")
    assert [s.source for s in ranked] == ["faq:tips", "doc:driver-handbook#propinas"]


# --- service and recruiter API ---


def ex(
    intent: Intent = Intent.ANSWER, question: str | None = None, **fields: Any
) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={name: FieldCandidate(value=v, confidence=0.95) for name, v in fields.items()},
        language=ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        question=question,
    )


class StaticRetriever:
    def __init__(self, *snippets: Snippet) -> None:
        self.snippets = list(snippets)

    def retrieve(
        self, question: str, language: Language, country: Country, k: int = 3
    ) -> list[Snippet]:
        return self.snippets[:k] if "accidente" in question else []


class Recorder(FakeLLM):
    def __init__(self, extractions: Sequence[TurnExtraction]) -> None:
        super().__init__(extractions)
        self.contexts: list[ReplyContext] = []

    async def respond(self, action, ctx: ReplyContext) -> ReplyResult:
        self.contexts.append(ctx)
        return await super().respond(action, ctx)


ACCIDENT = Snippet(
    id="vehicle-insurance-mx#si-tiene-un-accidente:0",
    question="Si tiene un accidente",
    answer="El IMSS da la atención médica sin costo.",
    score=0.71,
    source="doc:vehicle-insurance-mx#si-tiene-un-accidente",
    label=S.join(("Motos, licencia y seguro (México)", "Si tiene un accidente")),
)
INSURANCE = Snippet(id="insurance-mx", question="¿Estoy asegurado?", answer="Sí.", score=0.8)


async def test_sources_are_stored_and_shown_to_recruiters_only(sign_in: Callable[..., object]):
    engine = make_engine("sqlite://")
    init_db(engine)
    repo = Repo(engine)
    llm = Recorder(
        [
            ex(Intent.CONFIRM),
            ex(Intent.QUESTION, question="¿Qué pasa si tengo un accidente?"),
            ex(Intent.QUESTION, question="¿Hay descuento en la comida?"),
        ]
    )
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=StaticRetriever(INSURANCE, ACCIDENT))
    context = ConversationContext(country="MX", timezone="America/Mexico_City", locale="es-MX")
    reply = await svc.start(context=context)
    for _ in range(3):
        reply = await svc.handle_turn(reply.conversation_id, "...")

    assert llm.contexts[2].faq_snippets == (INSURANCE.text, ACCIDENT.text)
    sourced = [t for t in repo.turns(reply.conversation_id) if t.sources]
    assert [t.role for t in sourced] == ["agent"]
    assert [s["source"] for s in sourced[0].sources or ()] == [
        "faq:insurance-mx",
        "doc:vehicle-insurance-mx#si-tiene-un-accidente",
    ]

    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert conversation.candidate_id
    with TestClient(create_app(svc)) as client:
        sign_in(client)
        detail = client.get(f"/api/candidates/{conversation.candidate_id}").json()
        transcript = client.get(f"/api/conversations/{reply.conversation_id}").text
    [shown] = detail["conversations"]
    labels = [[s["label"] for s in t["sources"]] for t in shown["turns"] if t["sources"]]
    assert labels == [["FAQ: ¿Estoy asegurado?", ACCIDENT.label]]
    assert shown["faq_misses"] == ["¿Hay descuento en la comida?"]
    assert "sources" not in transcript
    assert "vehicle-insurance" not in transcript
    assert S.strip() not in transcript


# --- the real model (needs the local cache) ---


@pytest.fixture(scope="session")
def rag_index() -> RagIndex:
    try:
        index, _ = build_cache()
    except Exception as exc:
        unavailable = (
            f"Embedding model unavailable (offline?): run `uv run sazon rag-index` ({exc})"
        )
        if os.environ.get("CI"):
            pytest.fail(unavailable)  # CI caches the model: a skip there would hide these tests
        pytest.skip(unavailable)
    return index


def test_top3_hit_rate_on_document_questions(rag_index):
    misses = []
    for question, language, country, expected in DOC_QUESTIONS:
        found = {
            s.source.removeprefix("doc:").split("#")[0]
            for s in rag_index.retrieve(question, language, country)
        }
        if not found & expected:
            misses.append(question)
    hit_rate = 1 - len(misses) / len(DOC_QUESTIONS)
    assert len(DOC_QUESTIONS) >= 20
    assert hit_rate >= 0.85, misses


def test_country_documents_never_cross(rag_index):
    for question, language, _, _ in DOC_QUESTIONS:
        for country, other in (("ES", "-mx"), ("MX", "-es")):
            for snippet in rag_index.scores(question, language, country):
                if not snippet.is_faq:
                    assert not snippet.source.split("#")[0].endswith(other), (
                        question,
                        snippet.source,
                    )
