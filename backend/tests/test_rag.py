import os
import re
import zlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlmodel import select

from sazon.agents.riders import FieldName
from sazon.engine import (
    AnswerQuestion,
    AskField,
    ConversationContext,
)
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.state import ConversationState
from sazon.engine.validators import Country
from sazon.llm import Channel, FakeLLM, ReplyContext, ReplyResult
from sazon.llm.fallback_templates import UNKNOWN_ANSWER, UNKNOWN_REST
from sazon.llm.interface import faq_answer, faq_snippet
from sazon.rag.index import (
    MAX_SNIPPETS,
    MIN_SCORE,
    FaqEntry,
    Part,
    RagIndex,
    Snippet,
    build_cache,
    load_entries,
    merge_parts,
    parse_entry,
    retrieve_parts,
)
from sazon.service import ScreeningService
from sazon.storage.models import FaqMiss
from sazon.storage.repo import Repo, init_db, make_engine

# One worker builds and reads the cached vectors: `make check` runs these with `--dist loadgroup`.
pytestmark = pytest.mark.xdist_group("embedding-model")

ES, EN = Language.ES, Language.EN
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
CONTEXT = {
    "ES": ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES"),
    "MX": ConversationContext(country="MX", timezone="America/Mexico_City", locale="es-MX"),
}
COUNTRY_SPECIFIC = ("pay", "own-vehicle", "license", "insurance", "documents", "contract")

# Paraphrases, not the FAQ's own wording: ES, EN and Spanglish, Spain and Mexico.
PARAPHRASES: list[tuple[str, Language, Country, set[str]]] = [
    ("¿cuánto se paga?", ES, "MX", {"pay-mx"}),
    ("¿cuánto se paga?", ES, "ES", {"pay-es"}),
    ("oye y cuánto es la paga por hora", ES, "ES", {"pay-es"}),
    ("¿cuánto me van a pagar a la semana?", ES, "MX", {"pay-mx"}),
    ("¿necesito moto propia?", ES, "MX", {"vehicle"}),
    ("¿tengo que poner yo la moto o me la dais?", ES, "ES", {"vehicle"}),
    ("¿qué papeles tengo que llevar?", ES, "MX", {"documents-mx"}),
    ("¿me vale el NIE?", ES, "ES", {"documents-es"}),
    ("¿es de autónomo o con contrato?", ES, "ES", {"contract-es"}),
    ("¿tiene prestaciones de ley?", ES, "MX", {"contract-mx"}),
    ("¿y si me pasa algo con la moto quién responde?", ES, "ES", {"insurance-es"}),
    ("¿me dan de alta en el IMSS?", ES, "MX", {"contract-mx", "insurance-mx"}),
    ("¿las propinas me las quedo yo?", ES, "MX", {"tips"}),
    ("¿puedo trabajar solo por las tardes?", ES, "ES", {"schedule"}),
    ("¿me dan el uniforme o lo pago yo?", ES, "MX", {"uniform"}),
    ("¿cuánto tardan en contratarme?", ES, "ES", {"timing"}),
    ("¿qué pasa después de esto?", ES, "MX", {"after-chat"}),
    ("¿sirve mi licencia de auto?", ES, "MX", {"license-mx"}),
    ("¿vale el carnet de coche para la moto?", ES, "ES", {"license-es"}),
    ("¿quién paga la gasofa?", ES, "ES", {"fuel"}),
    ("¿mis datos dónde quedan?", ES, "ES", {"privacy"}),
    ("¿hay que currar los findes?", ES, "ES", {"weekends"}),
    ("how much is the pay?", EN, "ES", {"pay-es"}),
    ("what's the hourly wage", EN, "MX", {"pay-mx"}),
    ("do I have to bring my own scooter?", EN, "ES", {"vehicle"}),
    ("what papers do I need to sign up?", EN, "MX", {"documents-mx"}),
    ("am I covered if I get hurt on a delivery?", EN, "ES", {"insurance-es"}),
    ("is it a freelance gig or a real contract?", EN, "ES", {"contract-es"}),
    ("do you give me a delivery bag and clothes?", EN, "MX", {"uniform"}),
    ("can I pick my own hours?", EN, "ES", {"schedule"}),
    ("what happens after we finish here?", EN, "MX", {"after-chat"}),
    ("do I keep my tips?", EN, "ES", {"tips"}),
    ("I've never worked delivery before, is that ok?", EN, "ES", {"experience"}),
    ("how far will I have to ride?", EN, "MX", {"zones"}),
    ("oye, cuánto es el salary?", ES, "MX", {"pay-mx"}),
    ("¿necesito mi own moto o la company me da una?", ES, "ES", {"vehicle"}),
    ("what documents necesito, DNI?", EN, "ES", {"documents-es"}),
    ("¿hay insurance si tengo un accidente?", ES, "MX", {"insurance-mx"}),
    ("do I need a smartphone para la app?", EN, "MX", {"phone"}),
]
# Off-domain questions stay under the threshold. Near-domain ones the FAQ does not cover (a
# staff discount, the menu) can score above it; the responder then says it has no information.
OFF_DOMAIN: list[tuple[str, Language, Country]] = [
    ("¿cuál es la capital de Francia?", ES, "ES"),
    ("do you sponsor visas for the UK?", EN, "ES"),
    ("what's the weather tomorrow?", EN, "MX"),
    ("is there a company pension plan?", EN, "ES"),
    ("¿hay parking para empleados?", ES, "ES"),
    ("¿puedo repartir con patinete eléctrico?", ES, "ES"),
]
PAY_QUESTIONS = ["¿cuánto se paga?", "¿cuál es el sueldo?", "how much do you pay per hour?"]


class HashEmbedder:
    """Bag of words hashed into 256 buckets: deterministic, no model needed."""

    name = "hash-test"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        self.calls += 1
        vectors = np.zeros((len(texts), 256), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                vectors[row, zlib.crc32(word.encode()) % 256] += 1
        return vectors


class BrokenEmbedder:
    name = "broken"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        raise OSError("model not in the local cache")


class StaticFaq:
    """Returns the given snippets for any question and records what it was asked."""

    def __init__(self, *snippets: Snippet) -> None:
        self.snippets = list(snippets)
        self.asked: list[tuple[str, Language, Country]] = []

    def retrieve(
        self, question: str, language: Language, country: Country, k: int = 3
    ) -> list[Snippet]:
        self.asked.append((question, language, country))
        return self.snippets[:k]


class SnippetRecorder(FakeLLM):
    def __init__(self, extractions: Sequence[TurnExtraction]) -> None:
        super().__init__(extractions)
        self.contexts: list[ReplyContext] = []

    async def respond(self, action, ctx: ReplyContext) -> ReplyResult:
        self.contexts.append(ctx)
        return await super().respond(action, ctx)


def entry(id: str, country: Country | None, question: str, answer: str) -> FaqEntry:
    return FaqEntry(id=id, language=ES, country=country, question=question, answer=answer)


def ex(
    intent: Intent = Intent.ANSWER, question: str | None = None, **fields: Any
) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={
            name: FieldCandidate(value=value, confidence=0.95) for name, value in fields.items()
        },
        language=ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        question=question,
    )


CONSENT = ex(Intent.CONFIRM)


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return Repo(engine)


async def run(svc: ScreeningService, messages: int, country: Country = "ES"):
    reply = await svc.start(context=CONTEXT[country])
    for _ in range(messages):
        reply = await svc.handle_turn(reply.conversation_id, "...")
    return reply


def state_of(repo: Repo, conversation_id: str) -> ConversationState:
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    return ConversationState.model_validate(conversation.state)


# --- FAQ content ---


def test_faq_has_about_twenty_entries_per_language_and_country():
    entries = load_entries()
    for language in Language:
        for country in ("ES", "MX"):
            usable = [e for e in entries if e.language == language and e.country in (None, country)]
            assert len(usable) >= 20, (language, country)


def test_every_entry_has_a_translation_and_country_specific_topics_are_tagged():
    entries = load_entries()
    ids = {language: {e.id for e in entries if e.language == language} for language in Language}
    assert ids[ES] == ids[EN]
    for e in entries:
        topic = e.id.removesuffix("-es").removesuffix("-mx")
        if topic in COUNTRY_SPECIFIC:
            assert e.country == e.id[-2:].upper(), e.id
        else:
            assert e.country is None, e.id
    pay = {e.id: e.answer for e in entries if e.id.startswith("pay-")}
    assert all("€" in answer for id, answer in pay.items() if id == "pay-es")
    assert all("MXN" in answer for id, answer in pay.items() if id == "pay-mx")


def test_parse_entry_reads_front_matter_and_answer():
    text = "---\nid: tips\ncountry: both\nquestion: ¿Propinas?\nalso: a | b\n---\nSí,\ntodas.\n"
    parsed = parse_entry(text, ES)
    assert parsed == FaqEntry(
        id="tips",
        language=ES,
        country=None,
        question="¿Propinas?",
        also=("a", "b"),
        answer="Sí, todas.",
    )


def test_faq_is_marked_fictional():
    index = (Path(__file__).parents[1] / "src/sazon/rag/faq/index.md").read_text(encoding="utf-8")
    assert index.splitlines()[0].startswith("Fictional client content")


def test_snippet_text_round_trips_the_answer():
    assert faq_answer(faq_snippet("¿Q?", "Respuesta.")) == "Respuesta."


# --- index mechanics, with a deterministic embedder ---


def test_retrieve_filters_by_country_and_language(tmp_path):
    entries = [
        entry("pay-es", "ES", "cuanto se paga sueldo", "9,50 € por hora"),
        entry("pay-mx", "MX", "cuanto se paga sueldo", "2,100 pesos a la semana"),
        entry("tips", None, "propinas", "todas para ti"),
        FaqEntry(id="pay-en", language=EN, country=None, question="cuanto se paga", answer="x"),
    ]
    index = RagIndex(entries, HashEmbedder(), min_score=0.1, cache_dir=tmp_path)

    mx = index.retrieve("cuanto se paga", ES, "MX")
    assert [s.id for s in mx] == ["pay-mx"]
    assert "pesos" in mx[0].text
    assert [s.id for s in index.retrieve("cuanto se paga", ES, "ES")] == ["pay-es"]


def test_retrieve_drops_scores_under_the_threshold(tmp_path):
    entries = [entry("tips", None, "propinas", "todas para ti")]
    index = RagIndex(entries, HashEmbedder(), min_score=0.5, cache_dir=tmp_path)
    assert index.retrieve("capital de francia", ES, "ES") == []
    assert [s.id for s in index.retrieve("propinas", ES, "ES")] == ["tips"]


def test_vectors_are_cached_on_disk(tmp_path):
    entries = [entry("tips", None, "propinas", "todas para ti")]
    first = HashEmbedder()
    RagIndex(entries, first, cache_dir=tmp_path).retrieve("propinas", ES, "ES")
    assert list((tmp_path / "rag").glob("vectors-*.npy"))

    second = HashEmbedder()
    RagIndex(entries, second, cache_dir=tmp_path).retrieve("propinas", ES, "ES")
    assert second.calls == 1  # the query only; the FAQ vectors came from disk


def test_missing_model_degrades_to_no_snippets(tmp_path, caplog):
    index = RagIndex(load_entries(), BrokenEmbedder(), cache_dir=tmp_path)
    assert index.retrieve("¿cuánto se paga?", ES, "ES") == []
    assert index.retrieve("¿cuánto se paga?", ES, "ES") == []
    assert "sazon rag-index" in caplog.text


# --- service ---


PAY_MX = Snippet(
    id="pay-mx", question="¿Cuánto se paga?", answer="Entre 2,100 y 2,600 MXN.", score=0.9
)


async def test_answered_question_gets_snippets_and_resumes_at_the_same_field(repo):
    faq = StaticFaq(PAY_MX)
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question="¿Cuánto se paga?")])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq)
    reply = await run(svc, 2, "MX")

    answered = llm.actions[-1]
    assert isinstance(answered, AnswerQuestion)
    assert isinstance(answered.follow_up, AskField)
    assert answered.follow_up.field == FieldName.FULL_NAME
    assert faq.asked == [("¿Cuánto se paga?", ES, "MX")]
    assert llm.contexts[-1].faq_snippets == (PAY_MX.text,)
    assert reply.text.startswith("Entre 2,100 y 2,600 MXN.")
    state = state_of(repo, reply.conversation_id)
    assert state.awaiting == FieldName.FULL_NAME
    assert "unknown_faq" not in [flag.value for flag in state.quality_flags]
    assert repo.faq_misses(reply.conversation_id) == []

    llm.queue(ex(full_name="Jorge Ramírez Torres"))
    await svc.handle_turn(reply.conversation_id, "Jorge Ramírez Torres")
    state = state_of(repo, reply.conversation_id)
    assert state.profile.full_name == "Jorge Ramírez Torres"
    assert state.awaiting != FieldName.FULL_NAME


async def test_unknown_question_says_so_flags_and_logs_it(repo):
    question = "¿Hay descuento en la comida? Llámame al 612 345 678"
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question=question)])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=StaticFaq())
    reply = await run(svc, 2)

    assert reply.text.startswith(UNKNOWN_ANSWER["es-ES"])
    assert llm.contexts[-1].faq_snippets == ()
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert "unknown_faq" in conversation.quality_flags
    [miss] = repo.faq_misses(reply.conversation_id)
    assert miss.question == "¿Hay descuento en la comida? Llámame al [phone]"
    assert (miss.language, miss.country) == ("es", "ES")
    assert state_of(repo, reply.conversation_id).awaiting == FieldName.FULL_NAME


async def test_a_near_topic_passage_that_does_not_answer_is_a_miss(repo):
    training = Snippet(
        id="training",
        question="Capacitación",
        answer="Un turno pagado antes de repartir.",
        score=0.55,
    )
    question = "¿Hay gimnasio para empleados?"
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question=question)])
    llm.faq_answered = False
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=StaticFaq(training))
    reply = await run(svc, 2, "MX")

    assert llm.contexts[-1].faq_snippets == (training.text,)
    conversation = repo.get_conversation(reply.conversation_id)
    assert conversation is not None
    assert "unknown_faq" in conversation.quality_flags
    [miss] = repo.faq_misses(reply.conversation_id)
    assert miss.question == question
    agent = [t for t in repo.turns(reply.conversation_id) if t.role == "agent"]
    assert agent[-1].sources is None


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        (PAY_MX, True),
        (PAY_MX.model_copy(update={"score": 0.6}), False),
        (PAY_MX.model_copy(update={"source": "doc:handbook#pay"}), False),
    ],
)
async def test_only_a_close_faq_entry_may_be_sent_as_written(
    repo, snippet: Snippet, expected: bool
):
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question="¿Cuánto se paga?")])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=StaticFaq(snippet))
    await run(svc, 2, "MX")

    assert llm.contexts[-1].faq_send_as_written is expected


async def test_an_answering_passage_keeps_its_sources_and_logs_no_miss(repo):
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question="¿Cuánto se paga?")])
    llm.faq_answered = True
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=StaticFaq(PAY_MX))
    reply = await run(svc, 2, "MX")

    assert repo.faq_misses(reply.conversation_id) == []
    agent = [t for t in repo.turns(reply.conversation_id) if t.role == "agent"]
    assert agent[-1].sources


async def test_ai_identity_question_skips_retrieval(repo):
    faq = StaticFaq(PAY_MX)
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question="¿Eres un bot?")])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq)
    await run(svc, 2)
    assert faq.asked == []
    assert llm.contexts[-1].faq_snippets == ()


# --- the real model (needs the local cache; built once per session) ---


@pytest.fixture(scope="session")
def faq_index() -> RagIndex:
    try:
        index, _ = build_cache()
    except Exception as exc:
        unavailable = f"FAQ model unavailable (offline?): run `uv run sazon rag-index` ({exc})"
        if os.environ.get("CI"):
            pytest.fail(unavailable)  # CI caches the model: a skip there would hide these tests
        pytest.skip(unavailable)
    return index


def test_top3_hit_rate_on_paraphrases(faq_index):
    misses = []
    for question, language, country, expected in PARAPHRASES:
        found = {s.id for s in faq_index.retrieve(question, language, country, k=3)}
        if not found & expected:
            misses.append(question)
    hit_rate = 1 - len(misses) / len(PARAPHRASES)
    assert len(PARAPHRASES) >= 15
    assert hit_rate >= 0.9, misses


@pytest.mark.parametrize(
    ("question", "language", "country"),
    [
        # Live test 4f64892c (SAZ-72), asked while Ana waited for the email.
        ("por que lo necesitas?", ES, "ES"),
        ("para que lo quieres? no quiero dar mis datos", ES, "ES"),
        # As the extractor stores them, naming the field.
        ("¿para qué necesitáis mi correo?", ES, "ES"),
        ("¿para qué quieres mi móvil?", ES, "ES"),
        ("¿por qué me pide el correo?", ES, "MX"),
        ("¿para qué necesitan mi celular?", ES, "MX"),
        ("no quiero dar mi correo", ES, "ES"),
        ("why do you need my email?", EN, "ES"),
        ("what do you need my phone number for?", EN, "MX"),
        # D3: the name too.
        ("¿Para qué queréis mi nombre?", ES, "ES"),
        ("¿Para qué necesita mi nombre?", ES, "MX"),
        ("why do you need my name?", EN, "ES"),
    ],
)
def test_why_we_need_the_phone_and_email_is_answered(
    faq_index, question: str, language: Language, country: Country
):
    [best, *_] = faq_index.retrieve(question, language, country)
    assert best.id == "contact-data", (question, best.id)
    assert best.score >= MIN_SCORE


def test_off_domain_questions_score_under_the_threshold(faq_index):
    for question, language, country in OFF_DOMAIN:
        best = faq_index.scores(question, language, country)[0]
        assert best.score < MIN_SCORE, (question, best.id, best.score)


@pytest.mark.parametrize("language", [ES, EN])
def test_mexican_candidate_never_gets_spanish_pay(faq_index, language):
    for question in PAY_QUESTIONS:
        ids = [s.id for s in faq_index.scores(question, language, "MX")]
        assert "pay-es" not in ids
        assert ids[0] == "pay-mx", question
        assert "pay-mx" not in [s.id for s in faq_index.scores(question, language, "ES")]


async def test_service_answers_from_the_real_index(repo, faq_index):
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question="¿necesito moto propia?")])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq_index)
    reply = await run(svc, 2, "MX")

    snippets = llm.contexts[-1].faq_snippets
    assert snippets
    assert "motos de empresa" in snippets[0]
    assert repo.faq_misses(reply.conversation_id) == []
    with repo.session() as session:
        assert session.exec(select(FaqMiss)).all() == []


# --- standalone and multi-part questions (QA-BR) ---


class ByQuestion:
    """Snippets per exact question; records what it was asked."""

    def __init__(self, found: dict[str, list[Snippet]]) -> None:
        self.found = found
        self.asked: list[str] = []

    def retrieve(
        self, question: str, language: Language, country: Country, k: int = 3
    ) -> list[Snippet]:
        self.asked.append(question)
        return self.found.get(question, [])[:k]


HELMET = Snippet(id="helmet", question="¿Dan casco?", answer="Sí, casco homologado.", score=0.8)


def multi(question: str, *parts: str) -> TurnExtraction:
    return ex(Intent.QUESTION, question=question).model_copy(update={"sub_questions": list(parts)})


async def test_retrieval_uses_the_standalone_question_and_the_transcript_the_typed_one(repo):
    """ "¿Y en México?" after a pay answer is looked up and logged as the extractor rewrote
    it; the transcript keeps the candidate's words."""
    rewritten = "¿Cuánto se paga a los repartidores en México?"
    faq = ByQuestion({})
    llm = SnippetRecorder([CONSENT, ex(Intent.QUESTION, question=rewritten)])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq)
    reply = await svc.start(context=CONTEXT["ES"])
    await svc.handle_turn(reply.conversation_id, "Sí")
    await svc.handle_turn(reply.conversation_id, "¿Y en México?")

    assert faq.asked == [rewritten]
    candidate = [t for t in repo.turns(reply.conversation_id) if t.role == "candidate"]
    assert candidate[-1].text == "¿Y en México?"
    [miss] = repo.faq_misses(reply.conversation_id)
    assert miss.question == rewritten


async def test_a_question_about_the_other_country_searches_its_documents(repo):
    """ "¿Y en México?" in a Spanish conversation (QA batch 4): that question is looked up in
    Mexico's documents and the answer is told so; the next one is Spain's again."""
    rewritten = "¿Cuánto se paga a los repartidores en México?"
    faq = StaticFaq(PAY_MX)
    llm = SnippetRecorder(
        [
            CONSENT,
            ex(Intent.QUESTION, question=rewritten),
            ex(Intent.QUESTION, question="¿Y el casco?"),
        ]
    )
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq)
    await run(svc, 3, "ES")

    assert [country for _, _, country in faq.asked] == ["MX", "ES"]
    answers = [ctx for ctx in llm.contexts if ctx.faq_snippets]
    assert [ctx.faq_country for ctx in answers] == ["MX", None]


def test_the_other_country_finds_its_own_pay(faq_index):
    [part] = retrieve_parts(faq_index, ["¿Cuánto se paga a los repartidores en México?"], ES, "ES")
    assert part.country == "MX"
    assert part.snippets[0].id == "pay-mx"
    [part] = retrieve_parts(faq_index, ["How much is the pay in Spain?"], EN, "MX")
    assert part.country == "ES"
    assert part.snippets[0].id == "pay-es"


async def test_each_part_is_looked_up_and_the_sources_cover_every_part(repo):
    faq = ByQuestion({"¿Cuánto se paga?": [PAY_MX], "¿Dan casco?": [HELMET]})
    question = "¿Cuánto se paga y dan casco?"
    llm = SnippetRecorder([CONSENT, multi(question, "¿Cuánto se paga?", "¿Dan casco?")])
    llm.faq_answered = True
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq)
    reply = await run(svc, 2, "MX")

    assert faq.asked == ["¿Cuánto se paga?", "¿Dan casco?"]
    ctx = llm.contexts[-1]
    assert ctx.faq_snippets == (PAY_MX.text, HELMET.text)
    assert [(p.question, p.snippets) for p in ctx.faq_parts] == [
        ("¿Cuánto se paga?", (PAY_MX.text,)),
        ("¿Dan casco?", (HELMET.text,)),
    ]
    agent = [t for t in repo.turns(reply.conversation_id) if t.role == "agent"]
    assert [s["source"] for s in agent[-1].sources or []] == ["faq:pay-mx", "faq:helmet"]
    assert repo.faq_misses(reply.conversation_id) == []


async def test_a_part_nothing_answers_gets_the_fixed_line_and_its_own_miss(repo):
    faq = ByQuestion({"¿Cuánto se paga?": [PAY_MX]})
    question = "¿Cuánto se paga y hay comedor?"
    llm = SnippetRecorder([CONSENT, multi(question, "¿Cuánto se paga?", "¿Hay comedor?")])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq)
    reply = await run(svc, 2, "MX")

    assert reply.text.startswith(f"Entre 2,100 y 2,600 MXN. {UNKNOWN_REST['es-MX']} ")
    [miss] = repo.faq_misses(reply.conversation_id)
    assert miss.question == "¿Hay comedor?"
    agent = [t for t in repo.turns(reply.conversation_id) if t.role == "agent"]
    assert [s["source"] for s in agent[-1].sources or []] == ["faq:pay-mx"]
    flags = state_of(repo, reply.conversation_id).quality_flags
    assert "unknown_faq" in [flag.value for flag in flags]


async def test_no_part_answered_is_the_fixed_unknown_answer_and_one_miss(repo):
    question = "¿Hay comedor y parking?"
    llm = SnippetRecorder([CONSENT, multi(question, "¿Hay comedor?", "¿Hay parking?")])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=ByQuestion({}))
    reply = await run(svc, 2)

    assert reply.text.startswith(UNKNOWN_ANSWER["es-ES"])
    assert UNKNOWN_REST["es-ES"] not in reply.text
    [miss] = repo.faq_misses(reply.conversation_id)
    assert miss.question == question


async def test_by_voice_only_the_first_two_parts_are_looked_up(repo):
    faq = ByQuestion({"¿Cuánto se paga?": [PAY_MX]})
    parts = ("¿Cuánto se paga?", "¿Dan casco?", "¿Hay comedor?")
    llm = SnippetRecorder([CONSENT, multi("¿Cuánto se paga, dan casco y hay comedor?", *parts)])
    svc = ScreeningService(llm, repo, clock=lambda: NOW, faq=faq)
    reply = await svc.start(Channel.VOICE, CONTEXT["MX"])
    for _ in range(2):
        reply = await svc.handle_turn(reply.conversation_id, "...")

    assert faq.asked == list(parts[:2])
    assert [part.snippets != () for part in llm.contexts[-1].faq_parts] == [True, False, False]
    assert f"{UNKNOWN_REST['es-MX']} " in reply.text
    assert [miss.question for miss in repo.faq_misses(reply.conversation_id)] == ["¿Dan casco?"]


def test_parts_are_merged_best_first_without_repeats():
    pay, helmet, fuel = PAY_MX, HELMET, HELMET.model_copy(update={"id": "fuel"})
    parts = (
        Part(question="a", snippets=(pay, helmet)),
        Part(question="b", snippets=(helmet, fuel)),
    )
    assert [s.id for s in merge_parts(parts)] == ["pay-mx", "helmet", "fuel"]
    many = tuple(
        Part(
            question=str(n), snippets=tuple(pay.model_copy(update={"id": f"{n}{i}"}) for i in "ab")
        )
        for n in range(3)
    )
    assert len(merge_parts(many)) == MAX_SNIPPETS


# Messages that ask several things, split as the extractor writes the parts, with the FAQ entry
# each part needs.
MULTI_PART: list[tuple[str, Language, Country, list[tuple[str, str]]]] = [
    (
        "¿Cuánto pagan y dais casco?",
        ES,
        "ES",
        [("¿Cuánto pagan?", "pay-es"), ("¿Dais casco?", "helmet")],
    ),
    (
        "¿cuánto pagan y tengo seguro si me accidento?",
        ES,
        "MX",
        [("¿Cuánto pagan?", "pay-mx"), ("¿Tengo seguro si tengo un accidente?", "insurance-mx")],
    ),
    (
        "do I need my own bike and can I choose my hours?",
        EN,
        "ES",
        [("Do I need my own bike?", "vehicle"), ("Can I choose my hours?", "schedule")],
    ),
    (
        "¿qué papeles necesito, las propinas son para mí y quién paga la gasolina?",
        ES,
        "MX",
        [
            ("¿Qué papeles necesito?", "documents-mx"),
            ("¿Las propinas son para mí?", "tips"),
            ("¿Quién paga la gasolina?", "fuel"),
        ],
    ),
    (
        "¿hay que currar findes y cuánto tardáis en contratar?",
        ES,
        "ES",
        [
            ("¿Hay que trabajar los findes?", "weekends"),
            ("¿Cuánto tardáis en contratar?", "timing"),
        ],
    ),
]


def test_each_part_of_a_multi_part_question_finds_its_source(faq_index):
    """Looked up on its own, every part finds its entry among its snippets; looked up whole,
    the message loses some parts to the others (QA-BR)."""
    misses, whole_misses, total = [], [], 0
    for message, language, country, parts in MULTI_PART:
        found = retrieve_parts(faq_index, [part for part, _ in parts], language, country)
        whole = {s.id for s in faq_index.retrieve(message, language, country)}
        for part, (question, expected) in zip(found, parts, strict=True):
            total += 1
            if expected not in {s.id for s in part.snippets}:
                misses.append(question)
            if expected not in whole:
                whole_misses.append(question)
    assert total >= 10
    assert misses == []
    assert len(whole_misses) >= len(misses)
