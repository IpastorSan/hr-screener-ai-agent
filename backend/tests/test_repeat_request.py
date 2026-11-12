"""A request to say it again (SAZ-48): call 13b77d6e answered "¿A las qué?" with "No tengo esa
información, pero puedes preguntarlo en la entrevista". Ana says her last message again, word
for word, on any channel; no model reads the request and the screening does not move."""

from datetime import UTC, datetime
from typing import Any

import pytest

from sazon.engine import ConversationContext
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.state import ConversationState
from sazon.llm import FakeLLM
from sazon.llm.interface import Channel
from sazon.service import ScreeningService
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 20, 16, 33, tzinfo=UTC)
CONTEXT = ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES")


def ex(intent: Intent = Intent.ANSWER, **fields: Any) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={k: FieldCandidate(value=v, confidence=0.95) for k, v in fields.items()},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def service(*extractions: TurnExtraction) -> tuple[ScreeningService, FakeLLM, Repo]:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    llm = FakeLLM(extractions)
    return ScreeningService(llm, repo, clock=lambda: NOW), llm, repo


def state_of(repo: Repo, conversation_id: str) -> ConversationState:
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    return ConversationState.model_validate(conversation.state)


@pytest.mark.parametrize("channel", list(Channel))
async def test_the_last_message_is_said_again_word_for_word(channel: Channel):
    svc, llm, repo = service(ex(Intent.CONFIRM), ex(full_name="Alfredo Pérez"))
    reply = await svc.start(channel, CONTEXT)
    reply = await svc.handle_turn(reply.conversation_id, "Sí, sí, acepto.")
    asked = await svc.handle_turn(reply.conversation_id, "Alfredo Pérez.")
    before = state_of(repo, asked.conversation_id)
    read = len(llm.messages)

    again = await svc.handle_turn(asked.conversation_id, "¿Cómo?")

    assert again.text == asked.text
    assert again.ui == asked.ui
    assert again.candidate_text == "¿Cómo?"
    assert len(llm.messages) == read  # no model read it
    after = state_of(repo, asked.conversation_id)
    assert after.model_dump(exclude={"voice"}) == before.model_dump(exclude={"voice"})
    turns = repo.turns(asked.conversation_id)
    assert [turn.role for turn in turns[-2:]] == ["candidate", "agent"]
    assert turns[-1].text == turns[-3].text
    assert turns[-1].action == turns[-3].action
    assert turns[-1].ui == turns[-3].ui
    assert turns[-2].text == "¿Cómo?"


async def test_the_privacy_question_is_said_again_and_nothing_is_kept():
    svc, llm, repo = service()
    greeting = await svc.start(Channel.VOICE, CONTEXT)

    again = await svc.handle_turn(greeting.conversation_id, "¿Perdona?")

    assert again.text == greeting.text
    assert llm.messages == []
    assert [turn.text for turn in repo.turns(greeting.conversation_id)][-2:] == [
        None,  # nothing the candidate says is kept before consent
        greeting.text,
    ]
    assert not state_of(repo, greeting.conversation_id).consented


async def test_a_question_still_goes_to_the_model():
    svc, llm, _ = service(ex(Intent.CONFIRM), ex(Intent.QUESTION))
    reply = await svc.start(Channel.VOICE, CONTEXT)
    reply = await svc.handle_turn(reply.conversation_id, "Sí")
    await svc.handle_turn(reply.conversation_id, "¿A qué hora abre el local?")
    assert llm.messages[-1] == "¿A qué hora abre el local?"


async def test_the_repeat_is_streamed_to_the_call():
    svc, _, _ = service(ex(Intent.CONFIRM))
    reply = await svc.start(Channel.VOICE, CONTEXT)
    asked = await svc.handle_turn(reply.conversation_id, "Sí")
    said: list[str] = []

    again = await svc.handle_turn(asked.conversation_id, "¿A las qué?", emit=said.append)

    assert said == [asked.text]
    assert again.text == asked.text
