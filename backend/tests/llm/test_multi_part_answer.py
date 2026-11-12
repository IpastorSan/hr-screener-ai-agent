"""A question that asks several things (QA-BR): the answerer reads the snippets by part, and a
part nothing answers gets one fixed line, within the limits of one answer."""

import json
from datetime import date
from typing import Any

import httpx2
import pytest
from test_llm import COUNTRY_OF, completion, make_llm

from sazon.agents.riders import FieldName
from sazon.engine.actions import AnswerQuestion, AskField
from sazon.engine.models import Language
from sazon.engine.spoken import Register
from sazon.engine.texts import REGISTERS
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import UNKNOWN_ANSWER, UNKNOWN_REST, fallback_reply
from sazon.llm.interface import FaqPart
from sazon.llm.pydantic_ai_impl import respond_prompt, with_line_before_question
from sazon.llm.reply_checks import reply_problem

TODAY = date(2026, 10, 19)
PAY = "Q: ¿Cuánto se paga?\nA: Entre 2,100 y 2,600 MXN a la semana."
HELMET = "Q: ¿Dan casco?\nA: Sí, la empresa da casco homologado."
SNIPPETS = {
    Language.ES: (PAY, HELMET),
    Language.EN: ("Q: How much is the pay?\nA: 9.50 euros an hour.", "Q: Helmet?\nA: Yes."),
}


def action(register: Register = "es-MX") -> AnswerQuestion:
    language, country = COUNTRY_OF[register]
    follow_up = AskField(field=FieldName.EMAIL, language=language, country=country)
    return AnswerQuestion(
        question="¿Cuánto se paga, dan casco y hay comedor?",
        parts=("¿Cuánto se paga?", "¿Dan casco?", "¿Hay comedor?"),
        follow_up=follow_up,
        language=language,
        country=country,
    )


def ctx(
    answer: AnswerQuestion, channel: Channel = Channel.WEB, *, known: bool = True
) -> ReplyContext:
    snippets = SNIPPETS[answer.language] if known else ()
    parts = (
        FaqPart(question="¿Cuánto se paga?", snippets=snippets[:1]),
        FaqPart(question="¿Dan casco?", snippets=snippets[1:]),
        FaqPart(question="¿Hay comedor?"),
    )
    return ReplyContext.for_action(
        answer,
        today=TODAY,
        channel=channel,
        faq_snippets=snippets,
        faq_parts=parts,
        faq_send_as_written=True,
    )


def responder(*drafts: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    bodies: list[dict[str, Any]] = []
    queue = [json.dumps(draft) for draft in drafts]

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx2.Response(200, json=completion({"role": "assistant", "content": text}))

    return bodies, handler


def test_the_prompt_groups_the_snippets_by_part():
    answer = action()
    prompt = respond_prompt(answer, ctx(answer), "ref")
    faq = prompt.split("<faq>\n", 1)[1].split("\n</faq>", 1)[0]
    assert faq.splitlines() == [
        "Part 1: ¿Cuánto se paga?",
        *f"- {PAY}".splitlines(),
        "Part 2: ¿Dan casco?",
        *f"- {HELMET}".splitlines(),
        "Part 3: ¿Hay comedor?",
        "- (nothing found)",
    ]


def test_one_question_keeps_the_flat_snippet_list():
    answer = action().model_copy(update={"parts": ()})
    one = ReplyContext.for_action(answer, today=TODAY, faq_snippets=(PAY,))
    assert "Part 1" not in respond_prompt(answer, one, "ref")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Se paga bien. ¿Y su correo?", "Se paga bien. LINE ¿Y su correo?"),
        ("¿Y su correo?", "LINE ¿Y su correo?"),
        ("Se paga bien.", "Se paga bien. LINE"),
        ("Se paga bien. LINE ¿Y su correo?", "Se paga bien. LINE ¿Y su correo?"),
        (
            "Se paga bien. No tengo información sobre el comedor; puede preguntarlo. ¿Y su correo?",
            "Se paga bien. LINE ¿Y su correo?",
        ),
        (
            "Pay is fine. I don't have details on parking. Your email?",
            "Pay is fine. LINE Your email?",
        ),
    ],
)
def test_the_fixed_line_goes_before_the_question(text: str, expected: str):
    assert with_line_before_question(text, "LINE") == expected


async def test_a_part_with_nothing_found_gets_the_fixed_line_in_the_register():
    reply = "Se pagan entre 2,100 y 2,600 MXN a la semana y le damos casco. ¿Cuál es su correo?"
    _, handler = responder({"answered": True, "text": reply, "answered_parts": [1, 2]})
    answer = action()

    result = await make_llm(handler).respond(answer, ctx(answer))

    assert not result.call.fallback_used
    assert result.answered is True
    assert result.missed_parts == ()
    assert result.reply.text == (
        "Se pagan entre 2,100 y 2,600 MXN a la semana y le damos casco. "
        f"{UNKNOWN_REST['es-MX']} ¿Cuál es su correo?"
    )


async def test_a_part_whose_snippets_do_not_answer_it_is_reported_as_missed():
    reply = "Se pagan entre 2,100 y 2,600 MXN a la semana. ¿Cuál es su correo?"
    _, handler = responder({"answered": True, "text": reply, "answered_parts": [1]})
    answer = action()
    parts = ctx(answer)
    no_gaps = parts.model_copy(update={"faq_parts": parts.faq_parts[:2]})

    result = await make_llm(handler).respond(answer, no_gaps)

    assert result.missed_parts == ("¿Dan casco?",)
    assert UNKNOWN_REST["es-MX"] in result.reply.text


async def test_the_fixed_line_does_not_loosen_the_limits():
    """Two answer sentences, the line and the question are four: retried, not allowed."""
    long = "Se pagan entre 2,100 y 2,600 MXN a la semana. Le damos casco. ¿Cuál es su correo?"
    short = "Se pagan entre 2,100 y 2,600 MXN y le damos casco. ¿Cuál es su correo?"
    bodies, handler = responder({"answered": True, "text": long}, {"answered": True, "text": short})
    answer = action()

    result = await make_llm(handler).respond(answer, ctx(answer))

    assert len(bodies) == 2
    assert "4 sentences (at most 3)" in json.dumps(bodies[1], ensure_ascii=False)
    assert result.reply.text.startswith("Se pagan entre 2,100 y 2,600 MXN y le damos casco.")
    assert not result.call.fallback_used


@pytest.mark.parametrize(
    "draft",
    [
        {"answered": False, "text": "Eso no lo sé, consúltelo en la entrevista. ¿Y su correo?"},
        {"answered": True, "answered_parts": [], "text": "Pues no sabría decirle. ¿Su correo?"},
    ],
)
async def test_no_part_answered_sends_the_fixed_unknown_answer(draft: dict[str, Any]):
    """The model's own wording of "I don't know" is not sent (QA batch 4): weak hits on
    every part get the same fixed line as no hit at all."""
    _, handler = responder(draft)
    answer = action()

    result = await make_llm(handler).respond(answer, ctx(answer))

    assert result.answered is False
    assert result.reply.text == fallback_reply(answer, ctx(answer, known=False))
    assert result.reply.text.startswith(UNKNOWN_ANSWER["es-MX"])


async def test_nothing_found_for_any_part_sends_the_fixed_unknown_answer():
    _, handler = responder({"answered": True, "text": "unused"})
    answer = action()

    result = await make_llm(handler).respond(answer, ctx(answer, known=False))

    assert result.reply.text.startswith(UNKNOWN_ANSWER["es-MX"])
    assert UNKNOWN_REST["es-MX"] not in result.reply.text


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize("channel", list(Channel))
def test_the_partly_unknown_template_passes_the_checks(register: Register, channel: Channel):
    language, country = COUNTRY_OF[register]
    answer = action(register)
    context = ctx(answer, channel)
    text = fallback_reply(answer, context)
    assert UNKNOWN_REST[register] in text
    assert text.endswith("?")
    assert (
        reply_problem(
            text,
            language,
            country=country,
            action=answer,
            reference=text,
            voice=channel == Channel.VOICE,
        )
        is None
    )
    assert reply_problem(UNKNOWN_REST[register], language, country=country) is None


def one_question() -> tuple[AnswerQuestion, ReplyContext]:
    follow_up = AskField(field=FieldName.EMAIL, language=Language.ES, country="ES")
    answer = AnswerQuestion(
        question="¿Cuánto se paga a los repartidores en México?",
        follow_up=follow_up,
        language=Language.ES,
        country="ES",
    )
    context = ReplyContext.for_action(
        answer,
        today=TODAY,
        faq_snippets=(PAY,),
        faq_send_as_written=True,
        faq_country="MX",
    )
    return answer, context


async def test_an_answer_from_the_other_country_says_which_country():
    """QA batch 4: an answer from Mexico's documents in a Spanish chat names Mexico; one that
    doesn't is retried, then the template says it."""
    vague = {"answered": True, "text": "Se pagan entre 2,100 y 2,600 MXN. ¿Cuál es tu correo?"}
    bodies, handler = responder(vague)
    answer, context = one_question()

    result = await make_llm(handler).respond(answer, context)

    assert len(bodies) == 2
    assert "say which country" in json.dumps(bodies[1], ensure_ascii=False)
    assert '"faq_country": "MX"' in bodies[0]["messages"][-1]["content"]
    assert result.call.fallback_used
    assert result.reply.text.startswith("En México: Entre 2,100 y 2,600 MXN a la semana.")


async def test_an_answer_that_names_the_other_country_is_sent():
    named = {"answered": True, "text": "En México se pagan entre 2,100 y 2,600 MXN. ¿Y tu correo?"}
    _, handler = responder(named)
    answer, context = one_question()

    result = await make_llm(handler).respond(answer, context)

    assert result.reply.text == named["text"]
    assert not result.call.fallback_used
