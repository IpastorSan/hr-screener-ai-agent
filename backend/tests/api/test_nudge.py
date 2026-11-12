"""Client-side nudges (SAZ-17): the nudge endpoint, recovery, and abandonment at read time."""

import asyncio
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sazon.api.app import create_app
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.llm import FakeLLM
from sazon.reengagement.idle import ABANDON_AFTER
from sazon.service import ScreeningService
from sazon.storage.models import Candidate, Conversation, Summary
from sazon.storage.repo import Repo, make_engine

JSON = {"accept": "application/json"}
CONTEXT = {"country": "MX", "timezone": "America/Mexico_City", "locale": "es-MX"}
IDLE = ABANDON_AFTER + timedelta(minutes=1)


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture
def clock(now: datetime) -> Clock:
    return Clock(now)


@pytest.fixture
def service(llm: FakeLLM, clock: Clock) -> ScreeningService:
    return ScreeningService(llm, Repo(make_engine("sqlite://")), clock=clock)


@pytest.fixture
def client(service: ScreeningService, sign_in: Callable[..., object]) -> Iterator[TestClient]:
    with TestClient(create_app(service), raise_server_exceptions=False) as test_client:
        sign_in(test_client)
        yield test_client


def name(value: str) -> TurnExtraction:
    return TurnExtraction(
        intent=Intent.ANSWER,
        fields={"full_name": FieldCandidate(value=value, confidence=0.95)},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def start(client: TestClient) -> str:
    response = client.post("/api/conversations", json={"channel": "web", "context": CONTEXT})
    assert response.status_code == 200
    return response.json()["conversation_id"]


def send(client: TestClient, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json=body, headers=JSON
    )
    assert response.status_code == 200, response.text
    return response.json()


def consented(client: TestClient) -> str:
    """A conversation at the name question."""
    conversation_id = start(client)
    send(client, conversation_id, {"action": {"kind": "quick_replies", "value": "yes"}})
    return conversation_id


def nudge(client: TestClient, conversation_id: str, attempt: int) -> Any:
    return client.post(f"/api/conversations/{conversation_id}/nudge", json={"attempt": attempt})


def transcript(client: TestClient, conversation_id: str) -> dict[str, Any]:
    return client.get(f"/api/conversations/{conversation_id}").json()


def go_quiet(service: ScreeningService, conversation_id: str, clock: Clock) -> None:
    """Move the clock past the abandonment limit since the conversation's last activity."""
    conversation = service.repo.get_conversation(conversation_id)
    assert conversation is not None
    clock.moment = conversation.last_activity_at + IDLE


def candidate_of(service: ScreeningService, conversation_id: str) -> str:
    conversation = service.repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.candidate_id is not None
    return conversation.candidate_id


# --- nudge endpoint ---


def test_nudges_repeat_the_question_and_are_stored(client: TestClient, clock: Clock):
    conversation_id = consented(client)
    clock.moment += timedelta(minutes=3)

    first = nudge(client, conversation_id, 1)

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["reply"]["text"].startswith("Hola de nuevo, su solicitud quedó a medias.")
    assert body["state"]["ended"] is False
    turns = transcript(client, conversation_id)["turns"]
    assert turns[-1]["nudge"] is True
    assert turns[-1]["text"] == body["reply"]["text"]

    clock.moment += timedelta(minutes=15)
    second = nudge(client, conversation_id, 2).json()
    assert second["reply"]["text"].startswith("Le quedan pocas preguntas.")


def test_nudge_carries_the_pending_widget(client: TestClient, llm: FakeLLM):
    conversation_id = consented(client)
    llm.queue(name("Lucía Pérez"))
    send(client, conversation_id, {"text": "Lucía Pérez"})  # the phone question, its widget

    body = nudge(client, conversation_id, 1).json()

    assert body["reply"]["ui"]["kind"] == "phone"
    assert transcript(client, conversation_id)["turns"][-1]["ui"]["kind"] == "phone"


def test_no_nudge_before_consent(client: TestClient, service: ScreeningService):
    """Before the yes there is no application to be "half done", and the notice is read
    without being hurried."""
    conversation_id = start(client)

    response = nudge(client, conversation_id, 1)

    assert response.status_code == 409
    assert not [turn for turn in service.repo.turns(conversation_id) if turn.nudge]


def test_nudge_is_idempotent_per_attempt(client: TestClient, service: ScreeningService):
    conversation_id = consented(client)

    first = nudge(client, conversation_id, 1).json()
    again = nudge(client, conversation_id, 1).json()

    assert again["reply"] == first["reply"]
    stored = [turn for turn in service.repo.turns(conversation_id) if turn.nudge]
    assert len(stored) == 1


@pytest.mark.parametrize("attempt", [0, 3])
def test_nudge_attempt_out_of_range_is_rejected(client: TestClient, attempt: int):
    conversation_id = consented(client)
    assert nudge(client, conversation_id, attempt).status_code == 409


def test_second_nudge_needs_the_first(client: TestClient):
    conversation_id = consented(client)
    assert nudge(client, conversation_id, 2).status_code == 409


def test_nudge_after_the_end_is_rejected(client: TestClient):
    conversation_id = start(client)
    send(client, conversation_id, {"action": {"kind": "quick_replies", "value": "no"}})

    response = nudge(client, conversation_id, 1)

    assert response.status_code == 409
    assert response.json()["error"]


def frustrated() -> TurnExtraction:
    return name("x").model_copy(
        update={"intent": Intent.UNCLEAR, "fields": {}, "sentiment": Sentiment.FRUSTRATED}
    )


def test_a_paused_chat_gets_no_nudge_until_the_candidate_writes(
    client: TestClient, llm: FakeLLM, clock: Clock
):
    conversation_id = consented(client)
    llm.queue(frustrated(), frustrated())
    send(client, conversation_id, {"text": "¿otra pregunta? esto es eterno"})
    offer = send(client, conversation_id, {"text": "ya me cansé, son demasiadas preguntas"})
    assert offer["state"]["paused"] is False

    paused = send(client, conversation_id, {"action": {"kind": "quick_replies", "value": "yes"}})
    assert paused["state"]["paused"] is True
    assert paused["state"]["ended"] is False
    assert transcript(client, conversation_id)["state"]["paused"] is True
    clock.moment += timedelta(minutes=3)

    response = nudge(client, conversation_id, 1)

    assert response.status_code == 409
    assert "paused" in response.json()["detail"]
    llm.queue(name("Lucía García"))
    back = send(client, conversation_id, {"text": "Lucía García"})
    assert back["state"]["paused"] is False
    assert nudge(client, conversation_id, 1).status_code == 200


def test_nudge_unknown_conversation_is_404(client: TestClient):
    assert nudge(client, "nope", 1).status_code == 404


def test_a_new_silence_starts_from_the_first_nudge(client: TestClient, llm: FakeLLM):
    conversation_id = consented(client)
    nudge(client, conversation_id, 1)
    nudge(client, conversation_id, 2)
    llm.queue(name("Lucía García"))
    send(client, conversation_id, {"text": "Lucía García"})

    assert nudge(client, conversation_id, 2).status_code == 409
    assert nudge(client, conversation_id, 1).status_code == 200


# --- recovery ---


def test_reply_after_a_nudge_marks_the_conversation_recovered(
    client: TestClient, service: ScreeningService, llm: FakeLLM
):
    recovered = consented(client)
    nudge(client, recovered, 1)
    llm.queue(name("Lucía García"))
    send(client, recovered, {"text": "Lucía García"})
    unanswered = consented(client)
    nudge(client, unanswered, 1)

    conversation = service.repo.get_conversation(recovered)
    assert conversation is not None
    assert conversation.recovered_by_nudge
    other = service.repo.get_conversation(unanswered)
    assert other is not None
    assert not other.recovered_by_nudge

    data = client.get("/api/analytics").json()
    assert data["reengagement"] == {"nudged": 2, "recovered": 1, "recovery_rate": 0.5}
    candidate = conversation.candidate_id
    detail = client.get(f"/api/candidates/{candidate}").json()
    assert detail["conversations"][0]["recovered_by_nudge"] is True
    assert any(turn["nudge"] for turn in detail["conversations"][0]["turns"])


# --- abandonment at read time ---


def test_idle_conversation_counts_as_abandoned_everywhere(
    client: TestClient, service: ScreeningService, clock: Clock, llm: FakeLLM
):
    idle = consented(client)
    llm.queue(name("Lucía García"))
    send(client, idle, {"text": "Lucía García"})  # now at the contact stage
    go_quiet(service, idle, clock)
    active = consented(client)  # just started: still in progress

    page = client.get("/api/candidates").json()
    statuses = {item["id"]: item["status"] for item in page["items"]}
    idle_candidate = candidate_of(service, idle)
    active_candidate = candidate_of(service, active)
    assert statuses == {idle_candidate: "abandoned", active_candidate: "in_progress"}
    assert page["counts"]["abandoned"] == 1
    assert page["counts"]["in_progress"] == 1
    filtered = client.get("/api/candidates", params={"status": "abandoned"}).json()
    assert [item["id"] for item in filtered["items"]] == [idle_candidate]
    assert filtered["items"][0]["closed_at"] is not None

    detail = client.get(f"/api/candidates/{idle_candidate}").json()
    assert detail["status"] == "abandoned"
    assert detail["conversations"][0]["outcome"] == "abandoned"
    assert detail["conversations"][0]["drop_off_stage"] == "contact"

    data = client.get("/api/analytics").json()
    assert data["totals"]["abandoned"] == 1
    assert data["totals"]["in_progress"] == 1
    assert data["drop_off"] == [{"stage": "contact", "count": 1}]

    # Nothing was written: reading is enough.
    stored = service.repo.get_conversation(idle)
    assert stored is not None
    assert stored.outcome is None


def test_nudge_on_an_idle_conversation_writes_the_abandonment(
    client: TestClient, service: ScreeningService, clock: Clock
):
    conversation_id = consented(client)
    go_quiet(service, conversation_id, clock)

    assert nudge(client, conversation_id, 1).status_code == 409

    conversation = service.repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.outcome == "abandoned"
    assert conversation.drop_off_stage == "name"
    assert conversation.ended_at is not None
    expected = conversation.last_activity_at + ABANDON_AFTER
    assert conversation.ended_at.replace(tzinfo=None) == expected.replace(tzinfo=None)
    candidate = service.repo.get_candidate(conversation.candidate_id or "")
    assert candidate is not None
    assert candidate.status == "abandoned"
    assert service.repo.summary(conversation_id) is not None


def test_close_stale_closes_only_idle_conversations(
    client: TestClient, service: ScreeningService, clock: Clock
):
    idle = consented(client)
    clock.moment += IDLE
    active = consented(client)

    assert asyncio.run(service.close_stale()) == 1
    assert asyncio.run(service.close_stale()) == 0

    with service.repo.session() as session:
        rows = {c.id: c.outcome for c in session.exec(select(Conversation)).all()}
        statuses = sorted(c.status for c in session.exec(select(Candidate)).all())
    assert rows == {idle: "abandoned", active: None}
    assert statuses == ["abandoned", "in_progress"]


def test_coming_back_to_the_chat_link_resumes_an_idle_abandonment(
    client: TestClient, service: ScreeningService, clock: Clock, llm: FakeLLM
):
    conversation_id = consented(client)
    clock.moment += IDLE
    asyncio.run(service.close_stale())

    restored = transcript(client, conversation_id)

    assert restored["state"]["ended"] is False
    assert restored["state"]["stage"] == "name"
    candidate = service.repo.get_candidate(candidate_of(service, conversation_id))
    assert candidate is not None
    assert candidate.status == "in_progress"
    with service.repo.session() as session:
        assert session.exec(select(Summary)).all() == []  # written again when the screening ends
    llm.queue(name("Lucía García"))
    assert send(client, conversation_id, {"text": "Lucía García"})["state"]["stage"] == "contact"


def test_resuming_an_abandonment_waits_for_a_turn_in_flight(
    client: TestClient, service: ScreeningService, clock: Clock
):
    conversation_id = consented(client)
    clock.moment += IDLE
    asyncio.run(service.close_stale())

    async def resume_during_a_turn() -> bool:
        async with service.conversation_lock(conversation_id):  # a turn holds it
            resuming = asyncio.create_task(service.resume_if_idle(conversation_id))
            for _ in range(5):
                await asyncio.sleep(0)
            assert not resuming.done()
            conversation = service.repo.get_conversation(conversation_id)
            assert conversation is not None
            assert conversation.outcome == "abandoned"
        return await resuming

    assert asyncio.run(resume_during_a_turn())
    conversation = service.repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.outcome is None


def test_an_opt_out_stays_closed(client: TestClient, service: ScreeningService, clock: Clock):
    conversation_id = consented(client)
    stop = TurnExtraction(
        intent=Intent.OPT_OUT,
        fields={},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )
    assert isinstance(service.llm, FakeLLM)
    service.llm.queue(stop)
    send(client, conversation_id, {"text": "ya no me interesa"})

    assert transcript(client, conversation_id)["state"]["ended"] is True
