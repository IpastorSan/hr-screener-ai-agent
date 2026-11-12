"""The responder streamed sentence by sentence, for the voice channel (SAZ-16). No network."""

import json
from datetime import date
from typing import Any

import httpx2
import pytest
from prometheus_client import REGISTRY
from pydantic import SecretStr

from sazon.agents.riders import FieldName
from sazon.config import Settings
from sazon.engine.actions import AskField, Greet, OfferSlots
from sazon.engine.models import InterviewMode, Language
from sazon.engine.slots import OfferReason, Slot
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import fallback_reply
from sazon.llm.pydantic_ai_impl import PydanticAILLM, split_sentences

TODAY = date(2026, 10, 19)


def sse(*deltas: str) -> bytes:
    events = []
    for index, text in enumerate(deltas):
        chunk = {
            "id": "gen-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "anthropic/claude-haiku-5.5",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        if index == 0:
            chunk["choices"][0]["delta"]["role"] = "assistant"
        events.append(chunk)
    events.append({
        "id": "gen-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "anthropic/claude-haiku-5.5",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "cost": 1e-4},
    })  # fmt: skip
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    return body.encode()


def make_llm(*deltas: str, status: int = 200) -> tuple[PydanticAILLM, list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        if status != 200:
            return httpx2.Response(status, json={"error": {"message": "boom"}})
        return httpx2.Response(
            200, content=sse(*deltas), headers={"content-type": "text/event-stream"}
        )

    config = Settings(
        _env_file=None,  # type: ignore[call-arg]
        openrouter_api_key=SecretStr("test-key"),
        openrouter_base_url="https://eu.openrouter.ai/api/v1",
    )
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return PydanticAILLM(config, http_client=client, max_retries=0), bodies


def voice(action: Any) -> ReplyContext:
    return ReplyContext.for_action(action, today=TODAY, channel=Channel.VOICE)


ASK_NAME = AskField(field=FieldName.FULL_NAME, language=Language.ES, country="ES")


def test_split_sentences_keeps_the_unfinished_rest():
    assert split_sentences("Gracias, Lucía. ¿Tienes carn") == (["Gracias, Lucía."], "¿Tienes carn")
    assert split_sentences("Son las 10.30 h") == ([], "Son las 10.30 h")
    # The same sentence end as the reply checks' sentence count.
    assert split_sentences("Un momento… ¿Cuál") == (["Un momento…"], "¿Cuál")


async def test_sentences_are_emitted_as_they_complete():
    llm, bodies = make_llm("Gracias", ", Lucía. ", "¿Cuál es tu ", "nombre completo?")
    said: list[str] = []

    result = await llm.respond_stream(ASK_NAME, voice(ASK_NAME), said.append)

    assert said == ["Gracias, Lucía.", "¿Cuál es tu nombre completo?"]
    assert result.reply.text == "Gracias, Lucía. ¿Cuál es tu nombre completo?"
    assert bodies[0]["stream"] is True
    assert bodies[0]["provider"] == {"zdr": True, "data_collection": "deny"}
    assert not result.call.fallback_used
    assert result.call.output_tokens == 20


async def test_a_failed_stream_says_the_template():
    llm, _ = make_llm(status=500)
    said: list[str] = []

    result = await llm.respond_stream(ASK_NAME, voice(ASK_NAME), said.append)

    template = fallback_reply(ASK_NAME, voice(ASK_NAME))
    assert said == [template]
    assert result.reply.text == template
    assert result.call.fallback_used


async def test_a_failed_stream_is_counted_as_an_openrouter_error():
    labels = {"status": "500"}
    before = REGISTRY.get_sample_value("sazon_openrouter_errors_total", labels) or 0
    llm, _ = make_llm(status=500)

    await llm.respond_stream(ASK_NAME, voice(ASK_NAME), lambda _: None)

    assert REGISTRY.get_sample_value("sazon_openrouter_errors_total", labels) == before + 1


async def test_a_leak_before_anything_was_said_falls_back_to_the_template():
    llm, _ = make_llm("Wait, the context says Spanish. ", "¿Tu nombre?")
    said: list[str] = []

    result = await llm.respond_stream(ASK_NAME, voice(ASK_NAME), said.append)

    assert said == [fallback_reply(ASK_NAME, voice(ASK_NAME))]
    assert result.call.fallback_used


async def test_a_leak_after_a_good_sentence_adds_the_pending_question():
    llm, _ = make_llm("Gracias, Lucía. ", "Note: the reply must be short.")
    said: list[str] = []

    result = await llm.respond_stream(ASK_NAME, voice(ASK_NAME), said.append)

    assert said == ["Gracias, Lucía.", "¿Cuál es tu nombre completo?"]
    assert result.reply.text == "Gracias, Lucía. ¿Cuál es tu nombre completo?"
    assert result.call.error is not None


@pytest.mark.parametrize("language", [Language.ES, Language.EN])
async def test_the_greeting_streams_in_the_voice_variant(language: Language):
    greet = Greet(language=language, country="MX")
    llm, bodies = make_llm("Hola.")
    said: list[str] = []
    await llm.respond_stream(greet, voice(greet), said.append)
    system = json.dumps(bodies[0]["messages"])
    assert "Voice channel" in system


# --- times in words by voice (SAZ-48) ---

OFFER = OfferSlots(
    location_id="es-mad-centro",
    slots=(
        Slot(
            id="s1",
            startsAt="2026-10-21T09:00:00+02:00",
            label="miércoles 21 a las 09:00",
            location="Madrid Centro",
        ),
        Slot(
            id="s2",
            startsAt="2026-10-22T09:00:00+02:00",
            label="jueves 22 a las 09:00",
            location="Madrid Centro",
        ),
    ),
    reason=OfferReason.MODE,
    mode=InterviewMode.ONSITE,
    language=Language.ES,
    country="ES",
)


async def test_a_voice_offer_written_back_in_digits_is_replaced_by_the_words():
    """Call 13b77d6e: "¿Te va bien el miércoles 21 a las 09:00 o el jueves 22 a las 09:00?"
    came out garbled. The model is given the words; if it writes digits anyway, the template
    in words is said instead."""
    llm, bodies = make_llm("Perfecto, en persona. ", "¿Te va bien el miércoles 21 a las 09:00?")
    said: list[str] = []

    result = await llm.respond_stream(OFFER, voice(OFFER), said.append)

    assert said == [
        "Perfecto, en persona.",
        "¿Te va bien la entrevista el miércoles 21 o el jueves 22, a las nueve de la mañana?",
    ]
    assert result.call.error is not None
    assert "digits" in result.call.error
    prompt = json.dumps(bodies[0], ensure_ascii=False)
    assert "miércoles 21 a las nueve de la mañana" in prompt
    assert "a las 09:00" not in prompt


async def test_a_voice_offer_in_words_is_said_as_written():
    words = "¿Te va bien el miércoles 21 o el jueves 22, a las nueve de la mañana?"
    llm, _ = make_llm("Perfecto, en persona. ", words)
    said: list[str] = []

    result = await llm.respond_stream(OFFER, voice(OFFER), said.append)

    assert said == ["Perfecto, en persona.", words]
    assert not result.call.error
