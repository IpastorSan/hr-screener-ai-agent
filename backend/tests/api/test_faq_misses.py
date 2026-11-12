"""The recruiter view of the questions Ana could not answer: grouping, scope, CSV."""

import csv
import io
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sazon.api import faq_misses
from sazon.api.app import create_app
from sazon.engine.models import CandidateProfile, Language
from sazon.rag import misses
from sazon.rag.index import FaqEntry, NoRetriever, RagIndex
from sazon.rag.misses import Question, group_questions, normalize
from sazon.service import ScreeningService
from sazon.storage.models import Candidate, Conversation, FaqMiss
from sazon.storage.repo import Repo

DAY = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
TOPICS = ("parking", "uniforme", "vacaciones")


class TopicEmbedder:
    """One axis per topic keyword, plus a little of the text's length so rewordings differ."""

    def __init__(self, name: str = "topics") -> None:
        self.name = name
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        self.calls.append(list(texts))
        rows = []
        for text in texts:
            row = [1.0 if topic in text.lower() else 0.0 for topic in TOPICS]
            rows.append([*row, 0.1 + len(text) / 1000])
        return np.asarray(rows, dtype=np.float32)


class BrokenEmbedder:
    name = "broken"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        raise FileNotFoundError("no model in the cache")


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    misses._vectors.clear()
    yield
    misses._vectors.clear()


def q(n: int, text: str, minutes: int = 0) -> Question:
    return Question(n, text, DAY + timedelta(minutes=minutes or n))


# --- grouping ---


def test_similar_questions_group_together_the_largest_first():
    questions = [
        q(1, "¿Hay parking?"),
        q(2, "¿Quién paga el uniforme?"),
        q(3, "¿Tenéis parking para el personal?"),
        q(4, "¿Puedo dejar el coche en el parking?"),
        q(5, "¿El uniforme lo pone la empresa?"),
        q(6, "¿Cuántas vacaciones hay?"),
    ]
    groups, similar = group_questions(questions, TopicEmbedder())

    assert similar
    assert [[m.id for m in g.members] for g in groups] == [[1, 3, 4], [2, 5], [6]]


def test_a_tie_in_size_puts_the_most_recent_group_first():
    groups, _ = group_questions([q(1, "parking"), q(2, "uniforme")], TopicEmbedder())
    assert [g.members[0].id for g in groups] == [2, 1]


def test_a_question_below_the_threshold_opens_its_own_group():
    a = np.array([1.0, 0.0])
    b = np.array([np.cos(0.9), np.sin(0.9)])  # cosine 0.62 < 0.65
    c = np.array([np.cos(0.8), np.sin(0.8)])  # cosine 0.70 >= 0.65

    class Fixed:
        name = "fixed"

        def embed(self, texts: Sequence[str]) -> np.ndarray:
            return np.stack([{"a": a, "b": b, "c": c}[t] for t in texts])

    groups, _ = group_questions([q(1, "a"), q(2, "b")], Fixed())
    assert len(groups) == 2
    groups, _ = group_questions([q(1, "a"), q(2, "c")], Fixed())
    assert len(groups) == 1


def test_the_representative_is_the_most_asked_wording_the_latest_on_a_tie():
    questions = [
        q(1, "¿Hay parking?"),
        q(2, "hay PARKING"),
        q(3, "¿Tenéis parking?"),
    ]
    groups, _ = group_questions(questions, TopicEmbedder())
    assert groups[0].representative == "hay PARKING"

    groups, _ = group_questions([q(1, "¿Hay parking?"), q(2, "¿Tenéis parking?")], TopicEmbedder())
    assert groups[0].representative == "¿Tenéis parking?"


def test_without_the_model_questions_group_by_normalised_text():
    questions = [
        q(1, "¿Hay parking?"),
        q(2, "hay   PÁRKING"),
        q(3, "¿Tenéis parking?"),
    ]
    for embedder in (None, BrokenEmbedder()):
        groups, similar = group_questions(questions, embedder)
        assert not similar
        assert [[m.id for m in g.members] for g in groups] == [[1, 2], [3]]


def test_normalize_ignores_case_accents_punctuation_and_spacing():
    assert normalize("¿Cuántas  VACACIONES hay?") == normalize("cuantas vacaciones hay")
    assert normalize("¿Hay parking?") != normalize("¿Tenéis parking?")


def test_questions_are_embedded_once_across_requests():
    embedder = TopicEmbedder()
    group_questions([q(1, "parking"), q(2, "uniforme")], embedder)
    group_questions([q(1, "parking"), q(2, "uniforme"), q(3, "vacaciones")], embedder)
    assert embedder.calls == [["parking", "uniforme"], ["vacaciones"]]


def test_the_endpoint_uses_the_retrieval_model_when_it_loads():
    entry = FaqEntry(id="parking", language=Language.ES, country=None, question="¿Hay parking?",
                     answer="Sí.")  # fmt: skip
    index = RagIndex([entry], TopicEmbedder())
    assert faq_misses._embedder(index) is index.embedder
    assert faq_misses._embedder(RagIndex([entry], BrokenEmbedder())) is None
    assert faq_misses._embedder(NoRetriever()) is None


# --- the API ---


def miss(
    repo: Repo,
    question: str,
    *,
    location_id: str | None = "es-mad-centro",
    country: str = "ES",
    language: str = "es",
    minutes: int = 0,
    conversation: Conversation | None = None,
) -> Conversation:
    """A miss in a new conversation of a candidate at `location_id` (or in `conversation`)."""
    if conversation is None:
        candidate = repo.save(
            Candidate(
                channel="web",
                country=country,
                location_id=location_id,
                profile=CandidateProfile().model_dump(mode="json"),
                created_at=DAY,
                updated_at=DAY,
            )
        )
        conversation = repo.save(
            Conversation(
                candidate_id=candidate.id,
                channel="web",
                state={},
                started_at=DAY,
                last_activity_at=DAY,
            )
        )
    repo.save(
        FaqMiss(
            conversation_id=conversation.id,
            question=question,
            language=language,
            country=country,
            created_at=DAY + timedelta(minutes=minutes),
        )
    )
    return conversation


@pytest.fixture
def fixtures(client: TestClient, service: ScreeningService) -> dict[str, Conversation]:
    """`client` creates the tables."""
    repo = service.repo
    madrid = miss(repo, "¿Hay parking?", minutes=1)
    miss(repo, "¿Hay parking?", minutes=2, conversation=madrid)
    return {
        "madrid": madrid,
        "madrid2": miss(repo, "hay parking", minutes=3),
        "gdl": miss(repo, "¿Hay parking?", location_id="mx-gdl-centro", country="MX", minutes=4),
        "gdl_en": miss(
            repo,
            "Is there parking?",
            location_id="mx-gdl-centro",
            country="MX",
            language="en",
            minutes=5,
        ),
        "uniform": miss(repo, "¿Quién paga el uniforme?", minutes=60 * 24 * 3),
    }


def get(client: TestClient, **params: str) -> dict:
    response = client.get("/api/analytics/faq-misses", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_admin_sees_every_miss_grouped(client: TestClient, fixtures: dict[str, Conversation]):
    data = get(client)

    assert data["total"] == 6
    assert not data["truncated"]
    assert not data["by_similarity"]  # no model in the tests: exact text
    first, second, *rest = data["groups"]
    assert first["question"] == "¿Hay parking?"
    assert first["count"] == 4
    assert first["countries"] == ["ES", "MX"]
    assert first["languages"] == ["es"]
    assert first["last_seen"].startswith("2026-10-10T10:04")
    # The latest conversations, each once: Guadalajara, then Madrid (twice there).
    assert [e["conversation_id"] for e in first["examples"]] == [
        fixtures["gdl"].id,
        fixtures["madrid2"].id,
        fixtures["madrid"].id,
    ]
    assert first["examples"][0]["candidate_id"] == fixtures["gdl"].candidate_id
    assert [g["id"] for g in data["groups"]] == [1, 2, 3]
    assert second["count"] == rest[0]["count"] == 1
    # On a tie the most recent group first.
    assert second["question"] == "¿Quién paga el uniforme?"


def test_a_recruiter_sees_only_misses_in_their_scope(
    service: ScreeningService, sign_in: Callable[..., object], fixtures: dict[str, Conversation]
):
    with TestClient(create_app(service)) as client:
        sign_in(client, admin=False, location_ids=["mx-gdl-centro"], country="MX")
        data = get(client)
        exported = client.get("/api/analytics/faq-misses.csv").text

    assert data["total"] == 2
    ids = {e["conversation_id"] for g in data["groups"] for e in g["examples"]}
    assert ids == {fixtures["gdl"].id, fixtures["gdl_en"].id}
    assert fixtures["madrid"].id not in exported
    assert fixtures["gdl"].id in exported


def test_a_recruiter_without_locations_sees_none(
    service: ScreeningService, sign_in: Callable[..., object], fixtures: dict[str, Conversation]
):
    with TestClient(create_app(service)) as client:
        sign_in(client, admin=False)
        assert get(client)["groups"] == []


def test_filters(client: TestClient, fixtures: dict[str, Conversation]):
    assert get(client, to="2026-10-12")["total"] == 5
    assert get(client, **{"from": "2026-10-13"})["total"] == 1
    assert get(client, country="MX")["total"] == 2
    assert get(client, language="en")["total"] == 1
    assert get(client, channel="voice")["total"] == 0
    assert client.get("/api/analytics/faq-misses", params={"country": "FR"}).status_code == 422


def test_the_latest_misses_only(
    client: TestClient, fixtures: dict[str, Conversation], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(faq_misses, "MAX_MISSES", 2)
    data = get(client)
    assert data["total"] == 2
    assert data["truncated"]
    questions = {g["question"] for g in data["groups"]}
    assert questions == {"¿Quién paga el uniforme?", "Is there parking?"}


def test_similar_groups_with_the_model(
    client: TestClient,
    service: ScreeningService,
    fixtures: dict[str, Conversation],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(faq_misses, "_embedder", lambda faq: TopicEmbedder())
    data = get(client)
    assert data["by_similarity"]
    assert [g["count"] for g in data["groups"]] == [5, 1]  # the English one joins too


def test_csv_has_one_row_per_miss_with_its_group(
    client: TestClient, fixtures: dict[str, Conversation]
):
    response = client.get("/api/analytics/faq-misses.csv", params={"from": "2026-10-01"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(response.text.removeprefix("﻿"))))

    assert len(rows) == 6
    assert list(rows[0]) == list(faq_misses.CSV_FIELDS)
    assert [row["group"] for row in rows] == ["1", "1", "1", "1", "2", "3"]
    assert rows[0]["group_question"] == "¿Hay parking?"
    assert rows[0]["conversation_id"] == fixtures["gdl"].id
    assert rows[0]["candidate_id"] == fixtures["gdl"].candidate_id
    assert rows[0]["country"] == "MX"
    assert rows[4]["question"] == "¿Quién paga el uniforme?"


def test_csv_keeps_formulas_as_text(client: TestClient, service: ScreeningService):
    miss(service.repo, '=HYPERLINK("x")')
    rows = list(csv.reader(io.StringIO(client.get("/api/analytics/faq-misses.csv").text)))
    assert rows[1][1] == rows[1][2] == '\'=HYPERLINK("x")'


def test_needs_a_session(anonymous: TestClient):
    assert anonymous.get("/api/analytics/faq-misses").status_code == 401
    assert anonymous.get("/api/analytics/faq-misses.csv").status_code == 401
