"""A question nothing answers gets the unknown answer and the pending question as written, in
the conversation's register, with no model (SAZ-72)."""

import json
from datetime import UTC, date, datetime
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from sazon.agents.riders import FieldName
from sazon.config import Settings
from sazon.engine import ConversationContext
from sazon.engine.actions import AnswerQuestion, AskField
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.spoken import Register
from sazon.engine.validators import Country
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import UNKNOWN_ANSWER, fallback_reply
from sazon.llm.pydantic_ai_impl import FIXED_REPLY_MODEL, PydanticAILLM
from sazon.llm.reply_checks import USTED_FORMS
from sazon.rag.index import Snippet
from sazon.service import ScreeningService
from sazon.storage.repo import Repo, init_db, make_engine

TODAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
BASE_URL = "https://eu.openrouter.ai/api/v1"
# What the model wrote in live test 4f64892c, given the tú reference.
USTED_REPLY = "No tengo esa información concreta, pero puede preguntarla en la entrevista. ¿Me indica su correo electrónico?"  # noqa: E501
REGISTERS: dict[Register, tuple[Language, Country]] = {
    "es-ES": (Language.ES, "ES"),
    "es-MX": (Language.ES, "MX"),
    "en": (Language.EN, "ES"),
}


def completion(content: str) -> dict[str, Any]:
    message = {"role": "assistant", "content": content}
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "anthropic/claude-haiku-5.5",
        "provider": "Google",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class Model:
    """Extractions in order for the extractor; the usted reply for every responder call."""

    def __init__(self, *extractions: TurnExtraction) -> None:
        self.extractions = list(extractions)
        self.responder_calls = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if "awaiting_field" in json.dumps(body["messages"]):
            content = self.extractions.pop(0).model_dump_json()
            return httpx2.Response(200, json=completion(content))
        self.responder_calls += 1
        return httpx2.Response(200, json=completion(USTED_REPLY))


def make_llm(model: Model) -> PydanticAILLM:
    config = Settings(
        _env_file=None,  # type: ignore[call-arg]
        openrouter_api_key=SecretStr("test-key"),
        openrouter_base_url=BASE_URL,
    )
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(model))
    return PydanticAILLM(config, http_client=client, max_retries=0)


class NoFaq:
    def retrieve(
        self, question: str, language: Language, country: Country, k: int = 3
    ) -> list[Snippet]:
        return []


def ex(intent: Intent = Intent.ANSWER, question: str | None = None, **fields: Any):
    return TurnExtraction(
        intent=intent,
        fields={k: FieldCandidate(value=v, confidence=0.95) for k, v in fields.items()},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        question=question,
    )


@pytest.mark.parametrize("register", list(REGISTERS))
@pytest.mark.parametrize("channel", list(Channel))
async def test_an_unknown_answer_is_sent_as_written(register: Register, channel: Channel):
    language, country = REGISTERS[register]
    follow_up = AskField(field=FieldName.EMAIL, language=language, country=country)
    action = AnswerQuestion(
        question="¿por qué lo necesitas?",
        follow_up=follow_up,
        ui=follow_up.ui,
        language=language,
        country=country,
    )
    ctx = ReplyContext.for_action(action, today=TODAY, channel=channel)
    model = Model()
    llm = make_llm(model)

    result = await llm.respond(action, ctx)
    said: list[str] = []
    streamed = await llm.respond_stream(action, ctx, said.append)

    expected = f"{UNKNOWN_ANSWER[register]} {fallback_reply(follow_up, ctx)}"
    assert result.reply.text == streamed.reply.text == expected
    assert said == [expected]
    assert model.responder_calls == 0
    assert result.call.model == FIXED_REPLY_MODEL


async def test_a_spain_conversation_with_an_unanswered_question_stays_tu():
    """Live test 4f64892c: asked why the email was needed, Ana answered in usted."""
    model = Model(
        ex(Intent.CONFIRM),
        ex(full_name="Lucía Ortega Ruiz"),
        ex(phone="+34 612 345 602"),
        ex(Intent.QUESTION, question="¿Para qué es el color del logo?"),
        ex(email="lucia@example.com"),
    )
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    svc = ScreeningService(make_llm(model), repo, clock=lambda: NOW, faq=NoFaq())
    context = ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES")
    reply = await svc.start(Channel.WEB, context)
    texts = [reply.text]
    for message in ("Sí", "Lucía Ortega Ruiz", "+34 612 345 602", "¿y el logo?", "ok"):
        reply = await svc.handle_turn(reply.conversation_id, message)
        texts.append(reply.text)

    assert texts[4].startswith(UNKNOWN_ANSWER["es-ES"])
    assert texts[4].endswith("tu correo electrónico?"), texts[4]
    for text in texts:
        assert USTED_FORMS.search(text) is None, text
