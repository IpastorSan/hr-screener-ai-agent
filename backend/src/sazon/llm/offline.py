"""A keyword extractor for offline demos (`sazon chat --fake`): no model, template replies."""

from collections.abc import Sequence
from datetime import date

from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.spec import YES_NO_TYPES, FieldType, agent
from sazon.engine.state import ConversationState
from sazon.engine.text import fold, gives_up
from sazon.engine.validators import _find
from sazon.llm.fake import FakeLLM
from sazon.llm.interface import CallRecord, ChatTurn, ExtractResult, Role

OFFLINE_MODEL = "offline"
YES = frozenset({"si", "s", "yes", "y", "vale", "ok", "claro", "dale", "sure", "correcto", "bien"})
NO = frozenset({"no", "n", "nope"})
TODAY_WORDS = frozenset({"hoy", "ya", "today", "now", "inmediato", "ahora"})
# Common words that frame a message in one language; anything else keeps the current one.
ENGLISH_WORDS = frozenset({
    "yes", "yeah", "yep", "sure", "i", "im", "my", "the", "an", "and", "is", "are", "am",
    "have", "has", "do", "dont", "can", "could", "would", "will", "with", "for", "of", "to",
    "in", "on", "at", "it", "its", "that", "this", "what", "how", "when", "where", "you",
    "your", "please", "thanks", "thank", "live", "living", "year", "years", "month",
    "months", "week", "weeks", "next", "today", "now", "right", "morning", "afternoon",
    "evening", "any", "anytime", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
})  # fmt: skip
SPANISH_WORDS = frozenset({
    "si", "claro", "vale", "correcto", "el", "la", "los", "las", "un", "una", "unos", "unas",
    "y", "o", "es", "soy", "son", "estoy", "tengo", "tienes", "tiene", "de", "del", "que",
    "en", "con", "por", "para", "mi", "mis", "yo", "me", "te", "vivo", "vive", "ano", "anos",
    "mes", "meses", "semana", "proxima", "proximo", "hoy", "ya", "ahora", "manana", "tarde",
    "noche", "cualquiera", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado",
    "domingo", "gracias", "puedo", "quiero",
})  # fmt: skip
LANGUAGE_REQUESTS = {
    "english": Language.EN,
    "ingles": Language.EN,
    "spanish": Language.ES,
    "espanol": Language.ES,
    "castellano": Language.ES,
}


class OfflineLLM(FakeLLM):
    """Reads each message as the answer to the awaited field, or as an opt-out when it gives
    up on the job ("ya no me interesa").

    The language of each message is guessed from common words; a message without any keeps
    the conversation's language. `language` pins it instead.
    """

    def __init__(self, language: Language | None = None) -> None:
        super().__init__()
        self.language = language

    async def extract(
        self,
        history: Sequence[ChatTurn],
        state: ConversationState,
        message: str,
        *,
        today: date | None = None,
    ) -> ExtractResult:
        extraction = self._read(state, message, today or date.today())
        return ExtractResult(
            extraction=extraction, call=CallRecord(role=Role.EXTRACTOR, model=OFFLINE_MODEL)
        )

    def _read(self, state: ConversationState, message: str, today: date) -> TurnExtraction:
        word = fold(message)
        yes, no = word in YES, word in NO
        language = self.language or guess_language(word, state.language)
        request = next((LANGUAGE_REQUESTS[w] for w in word.split() if w in LANGUAGE_REQUESTS), None)
        if _names_option(state.awaiting, word):
            request = None  # "Inglés" to "¿Qué materias puedes impartir?" is the subject

        def extraction(
            intent: Intent, fields: dict[str, str | bool] | None = None, question: str | None = None
        ) -> TurnExtraction:
            return _extraction(language, request, intent, fields or {}, question)

        if message.strip().endswith("?"):
            return extraction(Intent.QUESTION, question=message.strip())
        if not state.consented:
            return extraction(Intent.CONFIRM if yes else Intent.OPT_OUT if no else Intent.UNCLEAR)
        if gives_up(message):
            return extraction(Intent.OPT_OUT)
        confirming = (
            state.pending_confirmation is not None
            or state.pending_identity is not None
            or state.pause_offered
            or state.stage in (Stage.CONFIRM, Stage.DONE)
        )
        if confirming and (yes or no):
            return extraction(Intent.CONFIRM if yes else Intent.CORRECTION)
        field = state.awaiting
        if field is None:
            return extraction(Intent.UNCLEAR)
        value: str | bool = message.strip()
        kind = agent().field(field).type
        if kind in YES_NO_TYPES and (yes or no):
            value = yes
        if kind == FieldType.DATE and word in TODAY_WORDS:
            value = today.isoformat()
        return extraction(Intent.ANSWER, {field: value})


def _names_option(field: str | None, folded: str) -> bool:
    """The answer names an option of the field asked, read by its words (no reader hook)."""
    spec = agent().field(field) if field is not None else None
    if spec is None or spec.type not in (FieldType.ENUM, FieldType.MULTI_ENUM) or spec.reader:
        return False
    return bool(_find(spec.words, folded))


def guess_language(folded: str, current: Language) -> Language:
    """English or Spanish by common words, else the conversation's current language."""
    words = folded.split()
    english = sum(w in ENGLISH_WORDS for w in words)
    spanish = sum(w in SPANISH_WORDS for w in words)
    if english > spanish:
        return Language.EN
    if spanish > english:
        return Language.ES
    return current


def _extraction(
    language: Language,
    request: Language | None,
    intent: Intent,
    fields: dict[str, str | bool],
    question: str | None,
) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={k: FieldCandidate(value=v, confidence=1.0) for k, v in fields.items()},
        language=language,
        matrix_language=MatrixLanguage(language.value),
        english_ratio=1.0 if language == Language.EN else 0.0,
        explicit_language_request=request,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        question=question,
    )
