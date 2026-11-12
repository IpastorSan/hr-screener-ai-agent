"""The voice channel (SAZ-16): ElevenLabs Agents calls us as its custom LLM.

ElevenLabs does the listening (speech to text), the speaking (text to speech) and the turn
taking. Every time the candidate finishes speaking it sends an OpenAI chat-completions request
to `/v1/chat/completions` with its own copy of the call so far. We take only the latest user
message, run it through the same `ScreeningService` turn as the web chat (`channel=voice`, so
the voice phrasing: short turns, at most two options read aloud) and stream the reply back
sentence by sentence. Our database stays the source of truth; ElevenLabs' history is ignored.

Which of our conversations a request belongs to travels in the session: the call page creates
the conversation first (`POST /api/conversations` with `channel: voice`) and starts the
ElevenLabs session with it as a dynamic variable and in the custom LLM extra body.
"""

import asyncio
import hmac
import json
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from sazon.engine import current_prompt
from sazon.engine.models import Language
from sazon.engine.spoken import Register
from sazon.engine.text import fold
from sazon.engine.validators import NUMBER_WORDS
from sazon.llm.fallback_templates import fallback_reply, register_of
from sazon.llm.interface import Channel, ReplyContext
from sazon.llm.offline import guess_language
from sazon.observability import record
from sazon.service import ScreeningService, is_ended, local_today
from sazon.storage.models import Conversation
from sazon.storage.repo import state_of

CONVERSATION_VAR = "sazon_conversation_id"
ELEVENLABS_VAR = "elevenlabs_conversation_id"
SECRET_HEADER = "x-sazon-voice-secret"
CONVERSATION_HEADER = "x-sazon-conversation-id"
ELEVENLABS_HEADER = "x-elevenlabs-conversation-id"
# The agent's system prompt carries both ids as `name=value` (see deploy/elevenlabs/agent.json).
CONVERSATION_MARK = re.compile(rf"{CONVERSATION_VAR}\s*[=:]\s*([0-9a-f]{{32}})")
ELEVENLABS_MARK = re.compile(rf"{ELEVENLABS_VAR}\s*[=:]\s*([A-Za-z0-9_-]{{6,100}})")
OUR_ID = re.compile(r"[0-9a-f]{32}")
ELEVENLABS_ID = re.compile(r"[A-Za-z0-9_-]{6,100}")
MODEL_NAME = "sazon-screener"
END_CALL_TOOL = "end_call"
END_CALL_ARGS = {"reason": "The screening has ended."}
# The language_detection system tool requires both `reason` and `language`, a code in the
# agent's languages (`agent.language` and `language_presets` in deploy/elevenlabs/agent.json).
LANGUAGE_TOOL = "language_detection"
LANGUAGE_REASON = "The candidate's language changed."
CACHE_SIZE = 1000
# How long after a request an early transcript's final may follow it (SAZ-44: 1.5 s live;
# SAZ-48: 2.6 s and 3.0 s). Known risk: a final that extends the early one ("Sí" -> "Sí, pero
# está caducado") counts as the same turn, so the reply and any gate it passed come from the
# early words. `speculative_turn` is off in deploy/elevenlabs/agent.json, yet calls still show
# early finals (SAZ-48).
EARLY_TRANSCRIPT_WINDOW_S = 4.0
# A final transcript that rewords the early one (SAZ-48): this alike, word for word, at least.
REWORD_SIMILARITY = 0.8
# Two words are variants of each other ("está", "estoy", "primera", "primero") when they
# differ only in their last letters: they share at least three, and all but three of the
# longer one. "Chamartín" and "Chamberí" are not.
VARIANT_PREFIX = 3
VARIANT_ENDING = 3
FILLER_WORD = re.compile(r"m+|h?m+h?|e+h+|e+m+|u+m+|u+h+|a+h+|este|pues|bueno|o+k|vale")
"""Folded words said while thinking, dropped at the ends before comparing transcripts."""
POLARITY_WORDS = frozenset(
    {"si", "no", "yes", "not", "nunca", "never", "ni", "tampoco", "nada", "nadie", "nope"}
)
"""A yes or a no: a final that changes one is a different answer, however alike."""

UNKNOWN_CONVERSATION: dict[Language, str] = {
    Language.ES: (
        "Perdona, no encuentro tu solicitud. Vuelve a abrir la página para empezar de nuevo."
    ),
    Language.EN: "Sorry, I can't find your application. Please reopen the page to start again.",
}
RATE_LIMITED: dict[Language, str] = {
    Language.ES: "Un momento, por favor. Dímelo otra vez en unos segundos.",
    Language.EN: "One moment, please. Say that again in a few seconds.",
}
# A buffer said while a slow reply is being written; the "... " keeps the speech flowing.
FILLER: dict[Language, str] = {Language.ES: "Vale... ", Language.EN: "Okay... "}
NOT_HEARD: dict[Language, str] = {
    Language.ES: "Perdona, no te he oído bien. ¿Me lo repites?",
    Language.EN: "Sorry, I didn't catch that. Could you say it again?",
}
STILL_THERE: dict[Register, str] = {
    "es-ES": "¿Sigues ahí?",
    "es-MX": "¿Sigue ahí?",
    "en": "Are you still there?",
}
"""Said when the candidate was silent: never a turn, so never a failed answer or the whole
question again. ElevenLabs documents no behaviour for an empty completion (only the
`skip_turn` system tool stays silent), so a short line is the safe reply."""


class ChatMessage(BaseModel):
    """One OpenAI chat message. `content` is a string or a list of content parts."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    """The body ElevenLabs sends to a custom LLM: OpenAI chat completions plus its extras.

    Unknown fields (temperature, max_tokens, tools...) are accepted and ignored.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    elevenlabs_extra_body: dict[str, Any] | None = None


class VoiceSession(BaseModel):
    """Who a request is about: our conversation and, when given, the ElevenLabs one."""

    model_config = ConfigDict(frozen=True)

    conversation_id: str | None
    elevenlabs_conversation_id: str | None = None
    language: Language | None = None


def authorized(headers: Mapping[str, str], secret: str) -> bool:
    """The shared secret, as `Authorization: Bearer <secret>` (the custom LLM API key field)
    or the `X-Sazon-Voice-Secret` header. An empty secret turns the endpoint off."""
    if not secret:
        return False
    bearer = headers.get("authorization", "")
    given = bearer[7:].strip() if bearer[:7].lower() == "bearer " else ""
    given = given or headers.get(SECRET_HEADER, "").strip()
    return bool(given) and hmac.compare_digest(given.encode(), secret.encode())


def message_text(message: ChatMessage) -> str:
    content = message.content
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return " ".join(str(part.get("text", "")) for part in content if part.get("type") == "text")


def latest_user_turn(messages: list[ChatMessage]) -> tuple[int, str] | None:
    """The candidate's latest words and how many user messages the call has had so far.

    Silence comes back as no words: when the candidate says nothing for the agent's turn
    timeout, ElevenLabs sends a user message of "..." (its transcript marks it
    `user_turn_timeout`). A message without a letter or a digit is not something said."""
    users = [message for message in messages if message.role == "user"]
    if not users:
        return None
    words = message_text(users[-1]).strip()
    return len(users), words if any(c.isalnum() for c in words) else ""


def session_of(request: ChatCompletionRequest, headers: Mapping[str, str]) -> VoiceSession:
    """Our conversation id from, in order: the custom LLM extra body, a request header, or the
    `sazon_conversation_id=` line of the system prompt (filled by a dynamic variable).
    Values that don't look like ids are ignored, so an unfilled `{{variable}}` is no id."""
    extra = request.elevenlabs_extra_body or {}
    system = "\n".join(
        message_text(message) for message in request.messages if message.role == "system"
    )
    ours = _first_match(
        OUR_ID,
        extra.get(CONVERSATION_VAR),
        extra.get("conversation_id"),
        headers.get(CONVERSATION_HEADER),
        _group(CONVERSATION_MARK, system),
    )
    theirs = _first_match(
        ELEVENLABS_ID,
        extra.get(ELEVENLABS_VAR),
        headers.get(ELEVENLABS_HEADER),
        _group(ELEVENLABS_MARK, system),
    )
    language = extra.get("language")
    return VoiceSession(
        conversation_id=ours,
        elevenlabs_conversation_id=theirs,
        language=Language(language) if language in ("es", "en") else None,
    )


def _group(pattern: re.Pattern[str], text: str) -> str | None:
    found = pattern.search(text)
    return found.group(1) if found else None


def _first_match(pattern: re.Pattern[str], *values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and pattern.fullmatch(value.strip()):
            return value.strip()
    return None


def spoken_language(session: VoiceSession, words: str) -> Language:
    return session.language or guess_language(fold(words), Language.ES)


TurnKey = tuple[str, int, str]
"""Our conversation, the number of user messages in the call and the words."""


class TurnCache:
    """The reply to each user message, so one thing said is one turn, however often it is sent.

    A retried request (same key) gets the stored reply, or waits for the turn still running.
    So does an early transcript's successor (SAZ-44): ElevenLabs predicts the end of a turn
    and may request a reply on an early transcript, then again on the final one, with the
    same history and the user message extended or normalised ("dos años", then "2 años").
    A request is that successor when, within `window_s` of the previous request for the
    conversation, it has the same number of user messages and its words extend or equal the
    earlier ones (`continues`). The earlier reply was never committed to ElevenLabs' history
    (the count would have grown), so it answers the request instead of a second turn."""

    def __init__(
        self,
        size: int = CACHE_SIZE,
        window_s: float = EARLY_TRANSCRIPT_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.size = size
        self.window_s = window_s
        self.clock = clock
        self._replies: OrderedDict[TurnKey, str] = OrderedDict()
        self._running: dict[TurnKey, asyncio.Future[str]] = {}
        self._latest: dict[str, tuple[TurnKey, float]] = {}
        self._aliases: dict[str, str] = {}
        self._hung_up: OrderedDict[str, int] = OrderedDict()
        self._languages: OrderedDict[str, tuple[Language, int]] = OrderedDict()

    def answering(self, key: TurnKey) -> TurnKey:
        """The turn that answers a request for `key`: an earlier one it continues, or its own."""
        target, count, words = key
        now = self.clock()
        latest = self._latest.get(target)
        if latest is not None:
            (_, earlier_count, earlier_words), at = latest
            if (
                now - at <= self.window_s
                and earlier_count == count
                and continues(earlier_words, words)
            ):
                self._latest[target] = (latest[0], now)
                return latest[0]
        self._latest[target] = (key, now)
        while len(self._latest) > self.size:
            self._latest.pop(next(iter(self._latest)))
        return key

    def get(self, key: TurnKey) -> str | None:
        return self._replies.get(key)

    def running(self, key: TurnKey) -> asyncio.Future[str] | None:
        return self._running.get(key)

    def start(self, key: TurnKey) -> None:
        self._running[key] = asyncio.get_running_loop().create_future()

    def put(self, key: TurnKey, text: str) -> None:
        future = self._running.pop(key, None)
        if future is not None and not future.done():
            future.set_result(text)
        self._replies[key] = text
        self._replies.move_to_end(key)
        while len(self._replies) > self.size:
            self._replies.popitem(last=False)

    def drop(self, key: TurnKey) -> None:
        """A turn that never finished: whoever waits for it is cancelled too."""
        future = self._running.pop(key, None)
        if future is not None:
            future.cancel()

    def resolve(self, conversation_id: str) -> str:
        """A returning candidate's call carries on in their earlier application."""
        return self._aliases.get(conversation_id, conversation_id)

    def alias(self, conversation_id: str, target: str) -> None:
        """From now on the call's turns go to `target`. The request that resumed the
        application stays its latest, so its final transcript is the same turn (SAZ-47)."""
        if conversation_id != target:
            self._aliases[conversation_id] = target
            while len(self._aliases) > self.size:
                self._aliases.pop(next(iter(self._aliases)))
            latest = self._latest.get(conversation_id)
            if latest is not None:
                self._latest[target] = latest

    def hang_up(self, conversation_id: str, count: int) -> None:
        """`end_call` was sent in reply to the call's `count`th user message."""
        self._hung_up[conversation_id] = count
        self._hung_up.move_to_end(conversation_id)
        while len(self._hung_up) > self.size:
            self._hung_up.popitem(last=False)

    def hung_up(self, conversation_id: str, count: int) -> bool:
        """`end_call` was already sent and the candidate has said nothing new since: a final
        transcript or a retry of the goodbye's turn, with no tool message to tell it apart."""
        return self._hung_up.get(conversation_id) == count

    def switched(self, call_id: str) -> tuple[Language, int] | None:
        """The language this call was last switched to and the user message that did it."""
        return self._languages.get(call_id)

    def switch(self, call_id: str, language: Language, count: int) -> None:
        self._languages[call_id] = (language, count)
        self._languages.move_to_end(call_id)
        while len(self._languages) > self.size:
            self._languages.popitem(last=False)


def spoken_words(text: str) -> list[str]:
    """Folded words with number words as digits: "Sí, dos años" and "si 2 anos" match."""
    return [str(NUMBER_WORDS.get(word, word)) for word in fold(text).split()]


def continues(earlier: str, later: str) -> bool:
    """`later` is `earlier` again, normalised ("dos" as "2", "612 345 678" as "612345678"),
    extended ("Sí", then "Sí, desde hace dos años") or reworded (`rewords`): an early
    transcript and its final."""
    before, after = spoken_words(earlier), spoken_words(later)
    if not before:
        return False
    if after[: len(before)] == before or "".join(after) == "".join(before):
        return True
    return rewords(_without_fillers(before), _without_fillers(after))


def _without_fillers(words: list[str]) -> list[str]:
    """The words without leading and trailing fillers ("mmm", "eh", "este", "pues", "um")."""
    start, end = 0, len(words)
    while start < end and FILLER_WORD.fullmatch(words[start]):
        start += 1
    while end > start and FILLER_WORD.fullmatch(words[end - 1]):
        end -= 1
    return words[start:end]


def rewords(before: list[str], after: list[str]) -> bool:
    """The final transcript says the same as the early one in slightly other words (SAZ-48,
    call 13b77d6e): "Sí, está todo bien" then "Sí, estoy todo bien", "Mejor el jueves..."
    then "Mmm, mejor el jueves...". Fillers aside (`_without_fillers`), equal or extended, or
    at least `REWORD_SIMILARITY` alike word for word, a word counting as the same when it is a
    close variant ("está", "estoy"). Never when a number or a yes or no differs: "a las diez"
    and "a las doce", "tengo carnet" and "no tengo carnet" are different answers."""
    if not before or not after:
        return False
    if after[: len(before)] == before:
        return True
    if sorted(_numbers(before)) != sorted(_numbers(after)):
        return False
    if sorted(w for w in before if w in POLARITY_WORDS) != sorted(
        w for w in after if w in POLARITY_WORDS
    ):
        return False
    matched = [_closest(word, before) for word in after]
    return SequenceMatcher(None, before, matched, autojunk=False).ratio() >= REWORD_SIMILARITY


def _shared_letters(word: str, other: str) -> int:
    """How many letters the two words start with in common."""
    for index, (letter, theirs) in enumerate(zip(word, other, strict=False)):
        if letter != theirs:
            return index
    return min(len(word), len(other))


def _numbers(words: list[str]) -> list[str]:
    return [word for word in words if any(char.isdigit() for char in word)]


def _closest(word: str, words: list[str]) -> str:
    """`word`, or the word of `words` it is a variant of ("estoy" of "está")."""
    if word in words:
        return word
    for other in words:
        shared = _shared_letters(word, other)
        if shared >= max(VARIANT_PREFIX, len(word) - VARIANT_ENDING, len(other) - VARIANT_ENDING):
            return other
    return word


_running: set[asyncio.Task[Any]] = set()


async def run_turn(
    service: ScreeningService, cache: TurnCache, conversation_id: str, count: int, words: str
) -> AsyncIterator[str]:
    """One spoken turn: the reply in pieces as the responder writes them.

    The turn runs in its own task, so it is stored even if ElevenLabs hangs up mid-reply.
    A request the cache maps to an earlier turn (a retry, an early transcript's final) gets
    that turn's reply, waiting for it while it runs.
    """
    target = cache.resolve(conversation_id)
    key = cache.answering((target, count, words))
    if key[2] != words:
        record("voice_early_transcript")
    cached = cache.get(key)
    if cached is not None:
        if cached:
            yield cached
        return
    running = cache.running(key)
    if running is not None:
        yield await asyncio.shield(running)
        return
    cache.start(key)
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    task = asyncio.create_task(
        service.handle_turn(target, words, channel=Channel.VOICE, emit=queue.put_nowait)
    )
    _running.add(task)
    task.add_done_callback(_running.discard)
    task.add_done_callback(lambda done: _finish(cache, conversation_id, key, done))
    task.add_done_callback(lambda _: queue.put_nowait(None))
    said: list[str] = []
    while (piece := await queue.get()) is not None:
        said.append(piece)
        yield piece
    reply = task.result()
    rest = remainder(" ".join(said), reply.text)
    if rest:
        yield rest


def _finish(cache: TurnCache, conversation_id: str, key: TurnKey, task: asyncio.Task[Any]) -> None:
    """Store the reply when the turn ends, even if the request that started it is gone."""
    if task.cancelled() or task.exception() is not None:
        cache.drop(key)
        return
    reply = task.result()
    cache.alias(conversation_id, reply.conversation_id or key[0])
    cache.put(key, reply.text)


def remainder(said: str, text: str) -> str:
    """What is left to say of `text` after `said`: all of it when they differ (a resumed
    application adds the welcome back and the pending question after the first line)."""
    if not said:
        return text
    if text.startswith(said):
        return text[len(said) :].strip()
    return "" if said.endswith(text) else text


def opening_line(service: ScreeningService, conversation: Conversation) -> str | None:
    """What Ana says when a call starts: the greeting, or for a chat switched to voice, the
    pending question in its voice phrasing. None once there is nothing left to answer."""
    state = state_of(conversation)
    if is_ended(state):
        return None
    said = service.last_said(conversation.id)
    last = said.text if said else None
    if conversation.channel == Channel.VOICE.value and last:
        return last
    prompt = current_prompt(state)
    if prompt is None:
        return last
    ctx = ReplyContext.for_action(
        prompt, today=local_today(state, service.clock()), channel=Channel.VOICE
    )
    return fallback_reply(prompt, ctx)


def remember_elevenlabs_id(
    service: ScreeningService, conversation_id: str, elevenlabs_id: str | None
) -> None:
    """Store ElevenLabs' id for the call. Outside the conversation lock on purpose: it runs
    before every voice request, and waiting there for a turn in flight would hold up the
    early/final transcript merging (`TurnCache`). Safe because no turn writes this column and
    `Repo.save` writes only the attributes changed on the row it saves, so neither save undoes
    the other (`test_a_save_writes_only_the_columns_it_changed`)."""
    if not elevenlabs_id:
        return
    conversation = service.repo.get_conversation(conversation_id)
    if conversation is not None and conversation.elevenlabs_conversation_id != elevenlabs_id:
        conversation.elevenlabs_conversation_id = elevenlabs_id
        service.repo.save(conversation)


def offers_tool(request: ChatCompletionRequest, name: str) -> bool:
    """The agent has the system tool `name` enabled: ElevenLabs lists it in `tools`."""
    for tool in request.tools or ():
        function = tool.get("function") or {}
        if function.get("name") == name:
            return True
    return False


def language_switch(
    service: ScreeningService,
    cache: TurnCache,
    session: VoiceSession,
    conversation_id: str,
    count: int,
    before: Language,
) -> Language | None:
    """The language to switch the call to after its `count`th user message (SAZ-59).

    The engine decides the conversation's language (an explicit request, or Spanglish past its
    hysteresis); ElevenLabs keeps its own, which picks the voice and the speech recognition.
    The call's language is the one we last switched it to, else the one it started in (the
    call page's extra body), else `before`. A switch goes with the reply of the turn that made
    it, and again whenever that turn is answered again: ElevenLabs drops an early
    transcript's reply, tool call included, and a retry means the first answer was lost. From
    the next user message on it is taken as done. Keyed by ElevenLabs' call id, so a second
    call on the same application starts from its own language.
    """
    conversation = service.repo.get_conversation(cache.resolve(conversation_id))
    if conversation is None:
        return None
    call_id = session.elevenlabs_conversation_id or conversation_id
    language = state_of(conversation).language
    switched = cache.switched(call_id)
    if switched == (language, count):
        return language
    current = switched[0] if switched else session.language or before
    if language == current:
        return None
    cache.switch(call_id, language, count)
    return language


def ended(service: ScreeningService, conversation_id: str) -> bool:
    """The call should hang up: nothing is left to answer, or Ana's last turn said goodbye or
    closed the application, even with an interview booked (SAZ-47)."""
    conversation = service.repo.get_conversation(conversation_id)
    if conversation is None:
        return False
    return service.call_over(conversation_id, state_of(conversation))


def after_tool_call(request: ChatCompletionRequest) -> bool:
    """The request comes after the agent ran a tool we called (`end_call`, or
    `language_detection` after the reply was said): its last message is the tool call or the
    tool's result. ElevenLabs asks once more about a second later (SAZ-47); the reply that
    called the tool has been said, so this one is empty."""
    if not request.messages:
        return False
    last = request.messages[-1]
    extra = last.model_extra or {}
    return last.role == "tool" or (last.role == "assistant" and bool(extra.get("tool_calls")))


def still_there(conversation: Conversation) -> str:
    return STILL_THERE[_register(conversation)]


def register_language(conversation: Conversation | None) -> Language | None:
    if conversation is None:
        return None
    return Language.EN if _register(conversation) == "en" else Language.ES


def _register(conversation: Conversation) -> Register:
    state = state_of(conversation)
    return register_of(state.language, state.country)


# --- OpenAI wire format ---


class Completion:
    """Builds the chat-completion chunks of one response."""

    def __init__(self, model: str | None = None) -> None:
        self.id = f"chatcmpl-{uuid4().hex}"
        self.created = int(time.time())
        self.model = model or MODEL_NAME
        self.first_pending = True

    def _chunk(self, delta: dict[str, Any], finish_reason: str | None = None) -> str:
        body = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    def text(self, piece: str, *, lead_space: bool = False) -> str:
        delta: dict[str, Any] = {"content": (" " if lead_space else "") + piece}
        if self.first_pending:
            delta["role"] = "assistant"
            self.first_pending = False
        return self._chunk(delta)

    def end_call(self) -> str:
        # ElevenLabs rejects an end_call without its required `reason` ("Tool request is
        # missing required parameters") and fails the call instead of hanging up.
        return self._tool_call(END_CALL_TOOL, json.dumps(END_CALL_ARGS))

    def switch_language(self, language: Language) -> str:
        """Switch the call's voice and speech recognition to `language`'s preset (SAZ-59)."""
        arguments = {"reason": LANGUAGE_REASON, "language": language.value}
        return self._tool_call(LANGUAGE_TOOL, json.dumps(arguments))

    def _tool_call(self, name: str, arguments: str) -> str:
        call = {
            "index": 0,
            "id": f"call_{uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
        return self._chunk({"tool_calls": [call]})

    def stop(self, reason: str = "stop") -> str:
        return self._chunk({}, reason)

    @staticmethod
    def done() -> str:
        return "data: [DONE]\n\n"

    def whole(self, text: str) -> dict[str, Any]:
        """The non-streaming response (`stream: false`)."""
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
