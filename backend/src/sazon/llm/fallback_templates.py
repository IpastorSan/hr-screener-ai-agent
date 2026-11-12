"""Static replies used when the responder model fails, and safe defaults for the other roles."""

import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, time

from sazon.engine.actions import (
    Action,
    AnswerQuestion,
    ApplicationSaved,
    ApplicationStatus,
    AskCancel,
    AskField,
    AskMode,
    BookSlot,
    CheckDuplicate,
    Clarify,
    ConfirmBooking,
    ConfirmIdentity,
    ConfirmMove,
    ConfirmSummary,
    ConfirmValue,
    DeEscalate,
    Deflect,
    DeflectKind,
    DetailsUpdated,
    Disqualify,
    End,
    EndReason,
    FindModes,
    FindSlots,
    Greet,
    OfferPause,
    OfferSlots,
    ReEngage,
    ResumeExisting,
    SkipEmail,
    SkipPhone,
    WarnPolicy,
    WelcomeBack,
)
from sazon.engine.models import (
    CandidateProfile,
    FieldId,
    Intent,
    InterviewMode,
    Language,
    MatrixLanguage,
    QuestionTopic,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.service_areas import branch_name, get_location
from sazon.engine.slots import VOICE_READ_SLOTS, Booked, Miss, OfferReason, Slot
from sazon.engine.spec import FieldType, agent
from sazon.engine.spoken import Register, spoken_day, spoken_time, spoken_when
from sazon.engine.state import DisqualifyReason, ReasonId
from sazon.engine.text import fold
from sazon.engine.texts import SPANISH, TextPack
from sazon.engine.ui import (
    MAX_BUTTONS,
    WEEKDAYS,
    day_month,
    field_label,
    format_date,
    format_years,
    option_label,
    summary_value,
)
from sazon.engine.validators import REFUSALS, Country, ReasonCode
from sazon.llm.interface import CandidateSummary, Channel, ReplyContext, faq_answer
from sazon.llm.reply_checks import count_sentences, most_sentences

VOICE_MAX_OPTIONS = 2
CLOCK_DOUBLE_STOP = re.compile(r"\b([ap]\.m\.)\.")
"""A spoken English time at the end of a sentence: "at nine a.m.", not "a.m.."."""
SENTENCE_BREAK = re.compile(r"\.\s+(?=[¿¡]?\w)")
KEEPS_CAPITAL = ("I", "Google")
"""Words that keep their capital when sentences are run together, with the agent's own names."""

SKELETON: dict[str, "Skeleton"] = {}
"""Every skeleton line, by its name: the lines an agent's pack may say otherwise."""


class Skeleton(Mapping[Register, str]):
    """An engine line in each register, as the agent's text pack says it when it overrides
    it (`TextPack.skeleton`)."""

    def __init__(self, name: str, lines: Mapping[Register, str]) -> None:
        assert name not in SKELETON, name
        self.name = name
        self.lines = lines
        SKELETON[name] = self

    def __getitem__(self, register: Register) -> str:
        texts = agent().texts
        override = texts[register].skeleton.get(self.name) if texts else None
        return self.lines[register] if override is None else override

    def __iter__(self) -> Iterator[Register]:
        return iter(self.lines)

    def __len__(self) -> int:
        return len(self.lines)


def pack(register: Register) -> TextPack:
    """The agent's domain lines in `register`."""
    return agent().texts[register]


GREET_RETRY = Skeleton(
    "greet_retry",
    {
        "es-ES": "Para empezar necesito que aceptes el aviso de privacidad. ¿Empezamos?",
        "es-MX": "Para empezar necesito que acepte el aviso de privacidad. ¿Empezamos?",
        "en": "Before we start I need your OK on the privacy notice. Shall we start?",
    },
)


ASK_AGAIN = Skeleton(
    "ask_again",
    {
        "es-ES": "Vale, lo corregimos.",
        "es-MX": "Claro, lo corregimos.",
        "en": "Sure, let's fix that.",
    },
)
"""Before asking again for a value the candidate said is wrong ("mi email está mal")."""
RETRY = Skeleton(
    "retry",
    {
        "es-ES": "Perdona, no lo he entendido.",
        "es-MX": "Disculpe, no le entendí.",
        "en": "Sorry, I didn't catch that.",
    },
)

CLARIFY = Skeleton(
    "clarify",
    {
        "es-ES": "¿Te refieres a {options}?",
        "es-MX": "¿Se refiere a {options}?",
        "en": "Do you mean {options}?",
    },
)
CLARIFY_PLACE = Skeleton(
    "clarify_place",
    {
        "es-ES": "¿Te refieres al local de {options}?",
        "es-MX": "¿Se refiere a la sucursal de {options}?",
        "en": "Do you mean our {options} location?",
    },
)
"""Locations by the name Ana says ("la sucursal de Benito Juárez"), never "Sazón CDMX ..."."""
OR: dict[Register, str] = {"es-ES": "o", "es-MX": "o", "en": "or"}

CONFIRM_EMAIL = Skeleton(
    "confirm_email",
    {
        "es-ES": "¿Quisiste decir {value}?",
        "es-MX": "¿Quiso decir {value}?",
        "en": "Did you mean {value}?",
    },
)
"""An email whose domain looks mistyped, read back as corrected (D2). Sent as written."""
CONFIRM_DATE_ROLLED = Skeleton(
    "confirm_date_rolled",
    {
        "es-ES": "Hoy es {today}, ¿te refieres al {value}?",
        "es-MX": "Hoy es {today}, ¿se refiere al {value}?",
        "en": "Today is {today}, so do you mean {value}?",
    },
)
"""A day and month already past this year, read as next year's (D6). Sent as written."""
CHECK_DUPLICATE = Skeleton(
    "check_duplicate",
    {
        "es-ES": "Un momento, reviso tus datos.",
        "es-MX": "Un momento, reviso sus datos.",
        "en": "One moment while I check your details.",
    },
)
RESUME_EXISTING = Skeleton(
    "resume_existing",
    {
        "es-ES": "Ya tienes una solicitud abierta con estos datos, así que seguimos con esa.",
        "es-MX": "Ya tiene una solicitud abierta con estos datos, así que seguimos con esa.",
        "en": (
            "You already have an open application with these details, so we'll carry on with that."
        ),
    },
)
IDENTITY_KNOWN: dict[Register, dict[str, str]] = {
    "es-ES": {
        "phone": "Ya tenemos este teléfono registrado.",
        "email": "Ya tenemos este correo registrado.",
    },
    "es-MX": {
        "phone": "Ya tenemos este teléfono registrado.",
        "email": "Ya tenemos este correo registrado.",
    },
    "en": {
        "phone": "We already have this phone number on file.",
        "email": "We already have this email address on file.",
    },
}
IDENTITY_QUESTION: dict[Register, tuple[str, str]] = {
    "es-ES": ("¿Quieres continuar como {name}?", "¿Quieres continuar con esa solicitud?"),
    "es-MX": ("¿Quiere continuar como {name}?", "¿Quiere continuar con esa solicitud?"),
    "en": ("Do you want to continue as {name}?", "Do you want to continue with that application?"),
}
IDENTITY_VOICE: dict[Register, dict[str, str]] = {
    "es-ES": {"phone": "Ya tenemos este teléfono.", "email": "Ya tenemos este correo."},
    "es-MX": {"phone": "Ya tenemos este teléfono.", "email": "Ya tenemos este correo."},
    "en": {"phone": "We have this number already.", "email": "We have this email already."},
}
IDENTITY_QUESTION_VOICE: dict[Register, tuple[str, str]] = {
    "es-ES": ("¿Sigues como {name}?", "¿Sigues con esa solicitud?"),
    "es-MX": ("¿Sigue como {name}?", "¿Sigue con esa solicitud?"),
    "en": ("Continue as {name}?", "Continue with that application?"),
}
"""A returning phone or email (D10): asked before the application is resumed, with its first
name and nothing else of it. Sent as written."""
WELCOME_BACK: dict[Register, tuple[str, str]] = {
    "es-ES": (
        "Hola de nuevo, {name}, seguimos donde lo dejaste.",
        "Hola de nuevo, seguimos donde lo dejaste.",
    ),
    "es-MX": (
        "Qué gusto saludarle de nuevo, {name}, seguimos donde nos quedamos.",
        "Qué gusto saludarle de nuevo, seguimos donde nos quedamos.",
    ),
    "en": (
        "Welcome back, {name}! Let's pick up where we left off.",
        "Welcome back! Let's pick up where we left off.",
    ),
}
WELCOME_BACK_TIMES: dict[Register, tuple[str, str]] = {
    "es-ES": (
        "Hola de nuevo, {name}. Tengo nuevas horas para tu entrevista:",
        "Hola de nuevo. Tengo nuevas horas para tu entrevista:",
    ),
    "es-MX": (
        "Hola de nuevo, {name}. Tengo nuevos horarios para su entrevista:",
        "Hola de nuevo. Tengo nuevos horarios para su entrevista:",
    ),
    "en": (
        "Welcome back, {name}. I have new times for your interview:",
        "Welcome back. I have new times for your interview:",
    ),
}
WELCOME_BACK_TIMES_QUESTION = Skeleton(
    "welcome_back_times_question",
    {
        "es-ES": "¿Cuál te viene mejor?",
        "es-MX": "¿Cuál le acomoda mejor?",
        "en": "Which one suits you?",
    },
)
"""A returning candidate saved without an interview: the welcome, then the new times."""
APPLICATION_STATUS_BOOKED = Skeleton(
    "application_status_booked",
    {
        "es-ES": "Ya tienes la entrevista reservada el {when} en el local de {location}.",
        "es-MX": "Ya tiene su entrevista reservada el {when} en la sucursal de {location}.",
        "en": "Your interview is already booked for {when} at our {location} location.",
    },
)
APPLICATION_STATUS_VIDEO = Skeleton(
    "application_status_video",
    {
        "es-ES": "Ya tienes la videollamada reservada el {when}.",
        "es-MX": "Ya tiene su videollamada reservada el {when}.",
        "en": "Your video interview is already booked for {when}.",
    },
)
APPLICATION_STATUS_SAVED = Skeleton(
    "application_status_saved",
    {
        "es-ES": "Ya tienes una solicitud con estos datos y está guardada.",
        "es-MX": "Ya tiene una solicitud con estos datos y está guardada.",
        "en": "You already have an application with these details, and it's saved.",
    },
)
"""A returning candidate whose application is closed: its status in one sentence, then the
goodbye; by web, a booked interview gets the line that it can be moved here instead
(`VIDEO_CHANGE`). No new dates, no review, no contact promised (SAZ-47)."""
AI_DISCLOSURE = Skeleton(
    "ai_disclosure",
    {
        "es-ES": "Sí, soy una asistente de IA. Te ayudo con el primer paso de tu solicitud.",
        "es-MX": "Sí, soy una asistente de IA. Le ayudo con el primer paso de su solicitud.",
        "en": "Yes, I'm an AI assistant. I help with the first step of your application.",
    },
)
UNKNOWN_ANSWER = Skeleton(
    "unknown_answer",
    {
        "es-ES": "No tengo esa información; puedes preguntarlo en la entrevista.",
        "es-MX": "No tengo esa información; puede preguntarlo en la entrevista.",
        "en": "I don't have that information; you can ask it at the interview.",
    },
)
IN_COUNTRY: dict[Register, dict[Country, str]] = {
    "es-ES": {"ES": "En España:", "MX": "En México:"},
    "es-MX": {"ES": "En España:", "MX": "En México:"},
    "en": {"ES": "In Spain:", "MX": "In Mexico:"},
}
"""Before an answer from the other country's documents ("¿Y en México?" in a Spanish chat)."""
COUNTRY_LABELS: dict[Country, str] = {"ES": "Spain", "MX": "Mexico"}
UNKNOWN_REST = Skeleton(
    "unknown_rest",
    {
        "es-ES": "De lo demás no tengo información; puedes preguntarlo en la entrevista.",
        "es-MX": "De lo demás no tengo información; puede preguntarlo en la entrevista.",
        "en": "I don't have information on the rest; you can ask at the interview.",
    },
)
"""A question that asks several things, some of which Ana can't answer: added, as written,
after the answer and before the pending question. The template answers one part, so it always
adds it."""
SKIP_EMAIL = Skeleton(
    "skip_email",
    {
        "es-ES": (
            "Seguimos sin el correo, con tu móvil basta; eso sí, sin correo no puedo enviarte la "
            "invitación de la entrevista, así que la verás aquí en el chat."
        ),
        "es-MX": (
            "Seguimos sin el correo, con su celular basta; eso sí, sin correo no puedo enviarle la "
            "invitación de la entrevista, así que la verá aquí en el chat."
        ),
        "en": (
            "We'll carry on without the email, your phone is enough; without an email I can't send "
            "you the interview invitation, though, so you'll see it here in the chat."
        ),
    },
)
"""No email, with a phone in: refused (SAZ-72) or failed its tries. Once, and the screening
carries on without it, warning that the invitation can only be seen in the chat (D4)."""
SKIP_PHONE: dict[Register, tuple[str, str]] = {
    "es-ES": (
        "Seguimos sin el móvil, así que necesito al menos tu correo.",
        "Ya tenemos tu correo, así que seguimos sin el móvil.",
    ),
    "es-MX": (
        "Seguimos sin el celular, así que necesito al menos su correo.",
        "Ya tenemos su correo, así que seguimos sin el celular.",
    ),
    "en": (
        "We'll carry on without your phone, so I need at least your email.",
        "We have your email, so we'll carry on without your phone.",
    ),
}
"""No phone: refused, or failed its tries. Without an email yet, and with one in (D4)."""
REFUSAL_OPTIONAL: dict[Register, tuple[str, str]] = {
    "es-ES": (
        "No es obligatorio, pero nos ayuda a preparar tu entrevista.",
        "Si prefieres no decirlo, lo dejamos y seguimos.",
    ),
    "es-MX": (
        "No es obligatorio, pero nos ayuda a preparar su entrevista.",
        "Si prefiere no decirlo, lo dejamos y seguimos.",
    ),
    "en": (
        "It's not required, but it helps us prepare your interview.",
        "If you'd rather not say, we'll leave it and carry on.",
    ),
}
"""A refused value, before the question again: why it is asked (`DECLINED`), then what happens
without it (`DECLINED_AGAIN`). Never "I didn't understand" or, for an optional field (D5),
anything that says it is needed. Sent as written (D7: the city's reason is a fixed line)."""
DEFLECT: dict[Register, dict[DeflectKind, str]] = {
    "es-ES": {
        DeflectKind.OFF_TOPIC: "Con eso no te puedo ayudar, yo solo llevo tu solicitud.",
        DeflectKind.INJECTION: "Sigamos con tu solicitud.",
    },
    "es-MX": {
        DeflectKind.OFF_TOPIC: "Con eso no le puedo ayudar, yo solo llevo su solicitud.",
        DeflectKind.INJECTION: "Sigamos con su solicitud.",
    },
    "en": {
        DeflectKind.OFF_TOPIC: "I can't help with that, I only handle your application.",
        DeflectKind.INJECTION: "Let's carry on with your application.",
    },
}
DE_ESCALATE = Skeleton(
    "de_escalate",
    {
        "es-ES": "Entiendo, vamos rápido y lo terminamos en un momento.",
        "es-MX": "Entiendo, vamos rápido y terminamos en un momento.",
        "en": "I understand, let's keep it quick.",
    },
)
DE_ESCALATE_CLOSED = Skeleton(
    "de_escalate_closed",
    {
        "es-ES": "Entiendo. Si necesitas algo sobre tu solicitud, aquí estoy.",
        "es-MX": "Entiendo. Si necesita algo sobre su solicitud, aquí estoy.",
        "en": "I understand. If you need anything about your application, I'm here.",
    },
)
"""The calm line after the screening ended: nothing left to finish quickly, no question."""
OFFER_PAUSE = Skeleton(
    "offer_pause",
    {
        "es-ES": "¿Prefieres que lo dejemos aquí y sigamos más tarde donde lo dejamos?",
        "es-MX": "¿Prefiere que lo dejemos aquí y sigamos más tarde donde nos quedamos?",
        "en": "Would you rather stop here and pick up later where we left off?",
    },
)
"""Always to a frustrated candidate (`simplify`): one sentence, the buttons say yes or no."""
CONFIRM_SUMMARY = Skeleton(
    "confirm_summary",
    {
        "es-ES": "Estos son tus datos. ¿Está todo bien?",
        "es-MX": "Estos son sus datos. ¿Está todo correcto?",
        "en": "Here are your details. Is everything correct?",
    },
)
CONFIRM_SUMMARY_VOICE = Skeleton(
    "confirm_summary_voice",
    {
        "es-ES": "Repaso: {details}. ¿Está todo bien?",
        "es-MX": "Repaso: {details}. ¿Está todo correcto?",
        "en": "To check: {details}. Is everything correct?",
    },
)
"""By voice there is no card: the key values read aloud, short (about 15 words), built in code
and sent as written: the first name, the place and its location, the availability and the
start date ("Repaso: Jorge, Roma, local Cuauhtémoc, tiempo completo, desde el lunes 26.")."""
VOICE_SUMMARY_PLACE = {Language.ES: "{place}, local {location}", Language.EN: "{place}, {location}"}
VOICE_SUMMARY_START = {Language.ES: "desde el {day}", Language.EN: "from {day}"}
CONFIRM_SAME_PLACE = Skeleton(
    "confirm_same_place",
    {
        "es-ES": "{place} está en {area}, así que te corresponde ese local. ¿Es correcto?",
        "es-MX": "{place} está en {area}, así que le corresponde ese local. ¿Es correcto?",
        "en": "{place} is in {area}, so that is your location. Is that right?",
    },
)
"""A correction or a question that names the place the candidate already has ("La zona es
Roma", "¿por qué pone Cuauhtémoc?"): why it is that location, sent as written."""
SAME_PLACE_AREA = {
    Language.ES: ("la alcaldía {location}", "la zona del local de {location}"),
    Language.EN: ("the {location} borough", "the area of our {location} location"),
}
"""The area a place is in: an alcaldía in Mexico City, a location's area anywhere else."""
MEXICO_CITY = "Ciudad de México"
CONFIRM_LOCATION = Skeleton(
    "confirm_location",
    {
        "es-ES": "Te corresponde el local de {location}. ¿Es correcto?",
        "es-MX": "Le corresponde la sucursal de {location}. ¿Es correcto?",
        "en": "Your location is {location}. Is that right?",
    },
)
"""The same, when the candidate named the location itself: no place to explain."""
CORRECT_SUMMARY = Skeleton(
    "correct_summary",
    {
        "es-ES": "Vale. ¿Qué dato quieres cambiar?",
        "es-MX": "Claro. ¿Qué dato quiere cambiar?",
        "en": "Sure. Which detail would you like to change?",
    },
)
POLICY_CLOSE = Skeleton(
    "policy_close",
    {
        "es-ES": (
            "No podemos continuar con esta solicitud. Puedes leer cómo tratamos tus datos en el "
            "aviso de privacidad, aquí abajo."
        ),
        "es-MX": (
            "No podemos continuar con esta solicitud. Puede leer cómo tratamos sus datos en el "
            "aviso de privacidad, aquí abajo."
        ),
        "en": (
            "We can't continue with this application. You can read how we handle your data in the "
            "privacy notice below."
        ),
    },
)
POLICY_BLOCKED = Skeleton(
    "policy_blocked",
    {
        "es-ES": (
            "Lo siento, no podemos abrir una nueva solicitud con estos datos. Puedes leer cómo "
            "tratamos tus datos en el aviso de privacidad, aquí abajo."
        ),
        "es-MX": (
            "Lo siento, no podemos abrir una nueva solicitud con estos datos. Puede leer cómo "
            "tratamos sus datos en el aviso de privacidad, aquí abajo."
        ),
        "en": (
            "Sorry, we can't open a new application with these details. You can read how we handle "
            "your data in the privacy notice below."
        ),
    },
)
POLICY_BLOCKED_VOICE = Skeleton(
    "policy_blocked_voice",
    {
        "es-ES": (
            "Lo siento, no podemos abrir una nueva solicitud con estos datos. El aviso de "
            "privacidad está en nuestra web."
        ),
        "es-MX": (
            "Lo siento, no podemos abrir una nueva solicitud con estos datos. El aviso de "
            "privacidad está en nuestra web."
        ),
        "en": (
            "Sorry, we can't open a new application with these details. Our privacy notice is on "
            "our website."
        ),
    },
)
"""A phone or email closed three times for manipulation tries again (D11): neutral, no
reason given, no screening. Sent as written."""
POLICY_WARNING = Skeleton(
    "policy_warning",
    {
        "es-ES": (
            "Antes de seguir: un nuevo intento de manipular al asistente cerrará la solicitud otra "
            "vez."
        ),
        "es-MX": (
            "Antes de seguir: un nuevo intento de manipular al asistente cerrará la solicitud otra "
            "vez."
        ),
        "en": (
            "Before we go on: another attempt to manipulate the assistant will close the "
            "application again."
        ),
    },
)
"""The first words of a new application after a manipulation close on this phone or email
(D11), before the next question. Sent as written."""
POLICY_CLOSE_VOICE = Skeleton(
    "policy_close_voice",
    {
        "es-ES": (
            "No podemos continuar con esta solicitud. El aviso de privacidad está en nuestra web."
        ),
        "es-MX": (
            "No podemos continuar con esta solicitud. El aviso de privacidad está en nuestra web."
        ),
        "en": "We can't continue with this application. Our privacy notice is on our website.",
    },
)
"""A manipulation attempt: neutral, no accusation, and where to read the privacy notice. Sent as
written: the model that read the attempt never phrases the answer to it."""
UNRESOLVED_REST = Skeleton(
    "unresolved_rest",
    {
        "es-ES": (
            ", así que no puedo seguir con la solicitud. Puedes empezar de nuevo cuando quieras."
        ),
        "es-MX": (
            ", así que no puedo seguir con la solicitud. Puede empezar de nuevo cuando quiera."
        ),
        "en": ", so I can't continue with the application. You can start again whenever you like.",
    },
)
"""Required data that could not be resolved: what was missing (the field's `unresolved`, or
`NO_CONTACT`), and that they can start again. Sent as written, by web and by voice."""
NO_CONTACT = Skeleton(
    "no_contact",
    {
        "es-ES": "Sin un teléfono o un correo no puedo reservarte una entrevista",
        "es-MX": "Sin un teléfono o un correo no puedo reservarle una entrevista",
        "en": "Without a phone number or an email I can't book you an interview",
    },
)


FIND_MODES = Skeleton(
    "find_modes",
    {
        "es-ES": "Un momento, miro cómo puede ser la entrevista.",
        "es-MX": "Un momento, reviso cómo puede ser la entrevista.",
        "en": "One moment while I check how the interview can be done.",
    },
)
ASK_MODE = Skeleton(
    "ask_mode",
    {
        "es-ES": "¿Prefieres hacer la entrevista en persona o por videollamada?",
        "es-MX": "¿Prefiere hacer la entrevista en persona o por videollamada?",
        "en": "Would you like the interview in person or by video call?",
    },
)
ASK_MODE_VOICE = Skeleton(
    "ask_mode_voice",
    {
        "es-ES": "¿Prefieres la entrevista en persona o por videollamada?",
        "es-MX": "¿Prefiere la entrevista en persona o por videollamada?",
        "en": "Would you like it in person or by video call?",
    },
)
ASK_MODE_LEAD = Skeleton(
    "ask_mode_lead",
    {
        "es-ES": "Gracias, ya tengo todo.",
        "es-MX": "Gracias, ya tengo todo.",
        "en": "Thanks, that's everything.",
    },
)
FIND_SLOTS = Skeleton(
    "find_slots",
    {
        "es-ES": "Un momento, busco horarios de entrevista.",
        "es-MX": "Un momento, busco horarios de entrevista.",
        "en": "One moment while I look for interview times.",
    },
)
BOOK_SLOT = Skeleton(
    "book_slot",
    {
        "es-ES": "Un momento, reservo tu entrevista.",
        "es-MX": "Un momento, reservo su entrevista.",
        "en": "One moment while I book your interview.",
    },
)
OFFER_LEAD: dict[Register, dict[OfferReason, str]] = {
    "es-ES": {
        OfferReason.QUALIFIED: "Gracias, ya tengo todo.",
        OfferReason.MORE: "Sin problema, te propongo otros.",
        OfferReason.RESCHEDULE: "Claro, la cambiamos.",
        OfferReason.TAKEN: "Ese horario se acaba de ocupar.",
        OfferReason.ASKED: "Claro.",
        OfferReason.WHICH: "Hay más de uno así.",
    },
    "es-MX": {
        OfferReason.QUALIFIED: "Gracias, ya tengo todo.",
        OfferReason.MORE: "Sin problema, le propongo otros.",
        OfferReason.RESCHEDULE: "Claro, la cambiamos.",
        OfferReason.TAKEN: "Ese horario se acaba de ocupar.",
        OfferReason.ASKED: "Claro.",
        OfferReason.WHICH: "Hay más de uno así.",
    },
    "en": {
        OfferReason.QUALIFIED: "Thanks, that's everything.",
        OfferReason.MORE: "No problem, here are some other times.",
        OfferReason.RESCHEDULE: "Sure, let's move it.",
        OfferReason.TAKEN: "That time has just been taken.",
        OfferReason.ASKED: "Sure.",
        OfferReason.WHICH: "There's more than one like that.",
    },
}
"""Why times are offered; after a choice of mode the lead is `OFFER_LEAD_MODE`. `asked`: the
times that fit what the candidate asked about; `which`: their words fit several."""
OFFER_LEAD_UNMATCHED: dict[Register, dict[Miss, str]] = {
    "es-ES": {
        Miss.NONE: "Para eso no tengo huecos libres, pero sí otros cercanos.",
        Miss.TIME: "A esa hora no tengo hueco libre, pero sí otros cercanos.",
        Miss.MORE: "No me quedan más huecos libres para eso, pero sí otros cercanos.",
        Miss.LEFT: "No me quedan más huecos libres, solo estos.",
    },
    "es-MX": {
        Miss.NONE: "Para eso no tengo horarios libres, pero sí otros cercanos.",
        Miss.TIME: "A esa hora no tengo horario libre, pero sí otros cercanos.",
        Miss.MORE: "No me quedan más horarios libres para eso, pero sí otros cercanos.",
        Miss.LEFT: "No me quedan más horarios libres, solo estos.",
    },
    "en": {
        Miss.NONE: "I have nothing free for that, but there are times close to it.",
        Miss.TIME: "That time isn't free, but there are times close to it.",
        Miss.MORE: "I have no more free times for that, but there are times close to it.",
        Miss.LEFT: "I have no more free times, only these.",
    },
}
"""Nothing free fits the times the candidate asked about (`Miss`): said from the lookup,
never by a model, before the nearest times (or the same ones, `left`)."""
OFFER_UNMATCHED_SHORT: dict[Register, dict[Miss, str]] = {
    "es-ES": {
        Miss.NONE: "Para eso no tengo huecos libres, pero",
        Miss.TIME: "A esa hora no tengo hueco libre, pero",
        Miss.MORE: "No me quedan más huecos libres para eso, pero",
        Miss.LEFT: "No me quedan más huecos libres, así que",
    },
    "es-MX": {
        Miss.NONE: "Para eso no tengo horarios libres, pero",
        Miss.TIME: "A esa hora no tengo horario libre, pero",
        Miss.MORE: "No me quedan más horarios libres para eso, pero",
        Miss.LEFT: "No me quedan más horarios libres, así que",
    },
    "en": {
        Miss.NONE: "I have nothing free for that, but",
        Miss.TIME: "That time isn't free, but",
        Miss.MORE: "I have no more free times for that, but",
        Miss.LEFT: "I have no more free times, so",
    },
}
"""The same, run into the question for a frustrated candidate (`simplify`: one sentence)."""
OFFER_SIMPLIFIED: dict[Register, dict[OfferReason | None, str]] = {
    "es-ES": {
        None: "¿Cuál de estos horarios te viene mejor?",
        OfferReason.MORE: "Te propongo otros: ¿cuál te viene mejor?",
        OfferReason.TAKEN: "Ese ya se ha ocupado: ¿cuál de estos te viene mejor?",
    },
    "es-MX": {
        None: "¿Cuál de estos horarios le acomoda mejor?",
        OfferReason.MORE: "Le propongo otros: ¿cuál le acomoda mejor?",
        OfferReason.TAKEN: "Ese ya se ocupó: ¿cuál de estos le acomoda mejor?",
    },
    "en": {
        None: "Which of these times suits you best?",
        OfferReason.MORE: "Here are some others: which suits you best?",
        OfferReason.TAKEN: "That one has just been taken: which of these suits you best?",
    },
}
OFFER_PICK = Skeleton(
    "offer_pick",
    {
        "es-ES": "Elige en los botones: ¿cuál te viene mejor?",
        "es-MX": "Elija en los botones: ¿cuál le acomoda mejor?",
        "en": "Pick one of the buttons: which suits you best?",
    },
)
"""The candidate's words fit several times again after "which one?": by web, the times are on
the buttons and the question is one line, never "Hay más de uno así" again."""
OFFER_PICK_VOICE = Skeleton(
    "offer_pick_voice",
    {
        "es-ES": "¿Cuál prefieres: el {slots}?",
        "es-MX": "¿Cuál prefiere: el {slots}?",
        "en": "Which do you prefer: {slots}?",
    },
)
"""The same by voice, with no buttons: the days alone when the times are the same."""
OFFER_LEAD_MODE: dict[Register, dict[InterviewMode, str]] = {
    "es-ES": {
        InterviewMode.ONSITE: "Perfecto, en persona.",
        InterviewMode.VIDEO: "Perfecto, por videollamada.",
    },
    "es-MX": {
        InterviewMode.ONSITE: "Perfecto, en persona.",
        InterviewMode.VIDEO: "Perfecto, por videollamada.",
    },
    "en": {
        InterviewMode.ONSITE: "Great, in person it is.",
        InterviewMode.VIDEO: "Great, a video call it is.",
    },
}
"""The lead when slots follow the candidate's choice (or switch) of interview mode."""
OFFER_SLOTS_VIDEO = Skeleton(
    "offer_slots_video",
    {
        "es-ES": "Tengo estos huecos para la videollamada: {slots}. ¿Cuál te viene mejor?",
        "es-MX": "Tengo estos horarios para la videollamada: {slots}. ¿Cuál le acomoda mejor?",
        "en": "I have these times for the video call: {slots}. Which one suits you?",
    },
)
OFFER_SLOTS_VIDEO_VOICE = Skeleton(
    "offer_slots_video_voice",
    {
        "es-ES": "¿Te va bien la videollamada el {slots}?",
        "es-MX": "¿Le acomoda la videollamada el {slots}?",
        "en": "Does a video call on {slots} work for you?",
    },
)
OFFER_SLOTS = Skeleton(
    "offer_slots",
    {
        "es-ES": (
            "Tengo estos huecos para la entrevista con la persona encargada del local de "
            "{location}: {slots}. ¿Cuál te viene mejor?"
        ),
        "es-MX": (
            "Tengo estos horarios para la entrevista con la gerencia del local de {location}: "
            "{slots}. ¿Cuál le acomoda mejor?"
        ),
        "en": (
            "I have these interview times with the manager of the {location} location: {slots}. "
            "Which one suits you?"
        ),
    },
)
"""In person: the interview is with the location's manager (SAZ-65), never the recruiting
team."""
OFFER_SLOTS_VOICE = Skeleton(
    "offer_slots_voice",
    {
        "es-ES": "¿Te va bien la entrevista el {slots}?",
        "es-MX": "¿Le acomoda la entrevista el {slots}?",
        "en": "Does an interview on {slots} work for you?",
    },
)
QUALIFIED_LEAD_VOICE = Skeleton(
    "qualified_lead_voice",
    {
        "es-ES": "¡Perfecto!",
        "es-MX": "¡Perfecto!",
        "en": "Great!",
    },
)
NO_SLOTS = Skeleton(
    "no_slots",
    {
        "es-ES": "Ahora mismo no tengo horarios de entrevista libres en tu local.",
        "es-MX": "Por ahora no tengo horarios de entrevista libres en su sucursal.",
        "en": "I don't have any interview times free at your location right now.",
    },
)
CONFIRM_BOOKING = Skeleton(
    "confirm_booking",
    {
        "es-ES": (
            "Listo, tu entrevista es el {when} con la persona encargada del local de {location}, "
            "{address}. Si no puedes ir, escribe aquí y la cambiamos."
        ),
        "es-MX": (
            "Listo, su entrevista es el {when} con la gerencia del local de {location}, "
            "{address}. Si no puede asistir, escriba aquí y la cambiamos."
        ),
        "en": (
            "Done, your interview is on {when} with the manager of the {location} location, "
            "{address}. If you can't make it, reply here and we'll move it."
        ),
    },
)
CONFIRM_BOOKING_VOICE = Skeleton(
    "confirm_booking_voice",
    {
        "es-ES": (
            "Listo, tu entrevista es el {when} con la persona encargada del local de {location}."
        ),
        "es-MX": "Listo, su entrevista es el {when} con la gerencia del local de {location}.",
        "en": "Done, your interview is on {when} with the manager of the {location} location.",
    },
)
CONFIRM_RESCHEDULE = Skeleton(
    "confirm_reschedule",
    {
        "es-ES": (
            "Hecho, tu entrevista con la persona encargada del local de {location} pasa al "
            "{when}, {address}. Si necesitas cambiarla otra vez, escribe aquí."
        ),
        "es-MX": (
            "Hecho, su entrevista con la gerencia del local de {location} pasa al {when}, "
            "{address}. Si necesita cambiarla otra vez, escriba aquí."
        ),
        "en": (
            "Done, your interview with the manager of the {location} location is now on {when}, "
            "{address}. If you need to move it again, reply here."
        ),
    },
)
KEEP_BOOKING = Skeleton(
    "keep_booking",
    {
        "es-ES": (
            "Entendido, mantengo tu entrevista del {when} con la persona encargada del local de "
            "{location}. Si quieres cambiarla más adelante, escribe aquí."
        ),
        "es-MX": (
            "Entendido, mantengo su entrevista del {when} con la gerencia del local de "
            "{location}. Si quiere cambiarla más adelante, escriba aquí."
        ),
        "en": (
            "Understood, I'll keep your interview on {when} with the manager of the {location} "
            "location. If you want to move it later, reply here."
        ),
    },
)
CONFIRM_RESCHEDULE_VOICE = Skeleton(
    "confirm_reschedule_voice",
    {
        "es-ES": (
            "Hecho, tu entrevista con la persona encargada del local de {location} pasa al {when}."
        ),
        "es-MX": "Hecho, su entrevista con la gerencia del local de {location} pasa al {when}.",
        "en": "Done, your interview with the manager of the {location} location is now on {when}.",
    },
)
KEEP_BOOKING_VOICE = Skeleton(
    "keep_booking_voice",
    {
        "es-ES": "Entendido, mantengo tu entrevista del {when}.",
        "es-MX": "Entendido, mantengo su entrevista del {when}.",
        "en": "Understood, I'll keep your interview on {when}.",
    },
)
"""By voice a moved or kept booking is said short, with the time in words (SAZ-49): no address,
and nothing about writing to change it."""
CONFIRM_VIDEO = Skeleton(
    "confirm_video",
    {
        "es-ES": "Listo, tu entrevista por videollamada con el equipo de selección es el {when}.",
        "es-MX": "Listo, su entrevista por videollamada con el equipo de selección es el {when}.",
        "en": "Done, your video interview with the recruiting team is on {when}.",
    },
)
CONFIRM_VIDEO_VOICE = Skeleton(
    "confirm_video_voice",
    {
        "es-ES": "Listo, tu videollamada con el equipo de selección es el {when}.",
        "es-MX": "Listo, su videollamada con el equipo de selección es el {when}.",
        "en": "Done, your video call with the recruiting team is on {when}.",
    },
)
RESCHEDULE_VIDEO = Skeleton(
    "reschedule_video",
    {
        "es-ES": "Hecho, tu entrevista por videollamada con el equipo de selección pasa al {when}.",
        "es-MX": "Hecho, su entrevista por videollamada con el equipo de selección pasa al {when}.",
        "en": "Done, your video interview with the recruiting team is now on {when}.",
    },
)
KEEP_VIDEO = Skeleton(
    "keep_video",
    {
        "es-ES": "Entendido, mantengo tu videollamada del {when}.",
        "es-MX": "Entendido, mantengo su videollamada del {when}.",
        "en": "Understood, I'll keep your video interview on {when}.",
    },
)
VIDEO_INVITE: dict[Register, dict[tuple[bool, bool], str]] = {
    "es-ES": {
        (True, True): (
            "Te llegará una invitación con el enlace de Google Meet a tu correo, y también lo "
            "tienes aquí abajo."
        ),
        (True, False): "Te enviaremos el enlace de la videollamada a tu correo.",
        (False, True): "Tienes el enlace de Google Meet aquí abajo.",
    },
    "es-MX": {
        (True, True): (
            "Le llegará una invitación con el enlace de Google Meet a su correo, y también lo "
            "tiene aquí abajo."
        ),
        (True, False): "Le enviaremos el enlace de la videollamada a su correo.",
        (False, True): "Tiene el enlace de Google Meet aquí abajo.",
    },
    "en": {
        (True, True): (
            "You'll get an invitation with the Google Meet link by email, and it's also just below."
        ),
        (True, False): "We'll send you the video call link by email.",
        (False, True): "The Google Meet link is just below.",
    },
}
"""Where the Meet link is, by (the candidate gave an email, the booking has a link). Without a
calendar (`SCHEDULING_SOURCE=fixed`) there is no link yet: it is sent by email."""
VIDEO_INVITE_VOICE: dict[Register, dict[bool, str]] = {
    "es-ES": {True: "Te envío el enlace por correo.", False: "Tienes el enlace en la pantalla."},
    "es-MX": {True: "Le envío el enlace por correo.", False: "Tiene el enlace en la pantalla."},
    "en": {True: "I'll email you the link.", False: "The link is on your screen."},
}
"""Voice never reads a link out: the email, or the screen when there is no email."""
VIDEO_CHANGE = Skeleton(
    "video_change",
    {
        "es-ES": "Si no puedes, escribe aquí y la cambiamos.",
        "es-MX": "Si no puede asistir, escriba aquí y la cambiamos.",
        "en": "If you can't make it, reply here and we'll move it.",
    },
)
DETAILS_UPDATED: dict[Register, tuple[str, str]] = {
    "es-ES": (
        "Hecho, he actualizado tus datos. Tu entrevista sigue el {when}.",
        "Lo dejamos como estaba. Tu entrevista sigue el {when}.",
    ),
    "es-MX": (
        "Listo, actualicé sus datos. Su entrevista sigue el {when}.",
        "Lo dejamos como estaba. Su entrevista sigue el {when}.",
    ),
    "en": (
        "Done, I've updated your details. Your interview is still on {when}.",
        "We'll leave it as it was. Your interview is still on {when}.",
    ),
}
"""A detail corrected after booking (changed, unchanged): the interview stays as booked."""
CONFIRM_MOVE = Skeleton(
    "confirm_move",
    {
        "es-ES": (
            "Para pasarte al local de {new} cancelo tu entrevista en el de {old} y te busco otra"
            " hora allí, ¿lo hago?"
        ),
        "es-MX": (
            "Para pasarle a la sucursal de {new} cancelo su entrevista en la de {old} y le busco"
            " otra hora allí, ¿lo hago?"
        ),
        "en": (
            "To move you to our {new} location I'll cancel your interview at {old} and find you a"
            " time there. Shall I?"
        ),
    },
)
"""A place of another location after booking: moving cancels the interview, so asked first."""
APPLICATION_SAVED = Skeleton(
    "application_saved",
    {
        "es-ES": "Entendido, ahora mismo no tengo más horarios. Tu solicitud queda guardada.",
        "es-MX": "Entendido, por ahora no tengo más horarios. Su solicitud queda guardada.",
        "en": "Understood, I don't have any other times right now. Your application is saved.",
    },
)
APPLICATION_SAVED_NONE_FREE = Skeleton(
    "application_saved_none_free",
    {
        "es-ES": (
            "Ahora mismo no tengo horarios de entrevista libres en tu local. "
            "Tu solicitud queda guardada."
        ),
        "es-MX": (
            "Por ahora no tengo horarios de entrevista libres en su sucursal. "
            "Su solicitud queda guardada."
        ),
        "en": (
            "I don't have any interview times free at your location right now. "
            "Your application is saved."
        ),
    },
)
"""No time free or none suited: the screening ends, qualified. No question and no promise of
new dates or of being contacted (SAZ-42)."""
END: dict[Register, dict[EndReason, str]] = {
    "es-ES": {
        EndReason.DECLINED_CONSENT: "Entendido, no guardo ningún dato. ¡Que vaya bien!",
        EndReason.OPT_OUT: "De acuerdo, lo dejamos aquí. Gracias por tu tiempo.",
        EndReason.ABUSE: "Voy a cerrar la conversación aquí, gracias.",
        EndReason.PAUSED: (
            "Perfecto, lo dejamos aquí. Vuelve a este chat cuando quieras y seguimos."
        ),
        EndReason.CLOSED: "Gracias por tu tiempo, ¡que vaya bien!",
        EndReason.CANCELLED: "He cancelado tu entrevista. Gracias por tu tiempo.",
        EndReason.RESTARTED: "De acuerdo, empezamos de nuevo.",
    },
    "es-MX": {
        EndReason.DECLINED_CONSENT: "Entendido, no guardo ningún dato. ¡Que le vaya bien!",
        EndReason.OPT_OUT: "De acuerdo, lo dejamos aquí. Gracias por su tiempo.",
        EndReason.ABUSE: "Voy a cerrar la conversación aquí, gracias.",
        EndReason.PAUSED: "Perfecto, lo dejamos aquí. Vuelva a este chat cuando guste y seguimos.",
        EndReason.CLOSED: "Gracias por su tiempo, ¡que le vaya bien!",
        EndReason.CANCELLED: "Cancelé su entrevista. Gracias por su tiempo.",
        EndReason.RESTARTED: "De acuerdo, empezamos de nuevo.",
    },
    "en": {
        EndReason.DECLINED_CONSENT: "Understood, I won't keep any of your data. All the best!",
        EndReason.OPT_OUT: "No problem, we'll stop here. Thanks for your time.",
        EndReason.ABUSE: "I'm going to close the conversation here, thank you.",
        EndReason.PAUSED: "Sure, we'll stop here. Come back to this chat any time to carry on.",
        EndReason.CLOSED: "Thanks for your time, all the best!",
        EndReason.CANCELLED: "I've cancelled your interview. Thanks for your time.",
        EndReason.RESTARTED: "Sure, let's start again.",
    },
}
END_ABUSE_CANCELLED = Skeleton(
    "end_abuse_cancelled",
    {
        "es-ES": "Voy a cerrar la conversación aquí y cancelo tu entrevista, gracias.",
        "es-MX": "Voy a cerrar la conversación aquí y cancelo su entrevista, gracias.",
        "en": "I'm going to close the conversation here and cancel your interview, thank you.",
    },
)
"""An abuse close that dropped a booked interview: sent as written, so it always says so. One
sentence, as every abuse close: the candidate is likely frustrated (`simplify`)."""
ASK_CANCEL = Skeleton(
    "ask_cancel",
    {
        "es-ES": "¿La cancelo o prefieres otra hora?",
        "es-MX": "¿La cancelo o prefiere otra hora?",
        "en": "Shall I cancel it, or would you rather have another time?",
    },
)
"""A request to cancel the booked interview, asked once; the buttons are Cancelar and Otra
hora."""
ASK_CANCEL_VOICE = Skeleton(
    "ask_cancel_voice",
    {
        "es-ES": "¿Cancelo la entrevista o prefieres otra hora?",
        "es-MX": "¿Cancelo la entrevista o prefiere otra hora?",
        "en": "Shall I cancel the interview, or find another time?",
    },
)
"""By voice there are no buttons: the question names the interview."""
CANCELLED = Skeleton(
    "cancelled",
    {
        "es-ES": "He cancelado tu entrevista del {day}. Gracias por tu tiempo.",
        "es-MX": "Cancelé su entrevista del {day}. Gracias por su tiempo.",
        "en": "I've cancelled your interview on {day}. Thanks for your time.",
    },
)
"""The candidate cancelled their interview: it names the day, and the chat ends."""
OPT_OUT_CANCELLED = Skeleton(
    "opt_out_cancelled",
    {
        "es-ES": (
            "De acuerdo, he cancelado tu entrevista del {day} y lo dejamos aquí. Gracias por tu "
            "tiempo."
        ),
        "es-MX": (
            "De acuerdo, cancelé su entrevista del {day} y lo dejamos aquí. Gracias por su tiempo."
        ),
        "en": (
            "No problem, I've cancelled your interview on {day} and we'll stop here. "
            "Thanks for your time."
        ),
    },
)
"""The goodbye to an opt-out that cancelled the booked interview: it always says so."""
RE_ENGAGE: dict[Register, tuple[str, str]] = {
    "es-ES": ("Hola de nuevo, tu solicitud está a medias.", "Te quedan pocas preguntas."),
    "es-MX": ("Hola de nuevo, su solicitud quedó a medias.", "Le quedan pocas preguntas."),
    "en": ("Hi again, your application is half done.", "Only a few questions left."),
}
RE_ENGAGE_ALONE = Skeleton(
    "re_engage_alone",
    {
        "es-ES": "¿Seguimos con tu solicitud?",
        "es-MX": "¿Seguimos con su solicitud?",
        "en": "Shall we carry on with your application?",
    },
)


def register_of(language: Language, country: Country) -> Register:
    return "en" if language == Language.EN else SPANISH[country]


def disqualify_text(
    reason: ReasonId, register: Register, voice: bool = False, *, blocked: bool = False
) -> str:
    """A gate's close as the pack says it, the policy close, or what could not be resolved."""
    if blocked:
        return (POLICY_BLOCKED_VOICE if voice else POLICY_BLOCKED)[register]
    if reason == DisqualifyReason.POLICY_VIOLATION:
        return (POLICY_CLOSE_VOICE if voice else POLICY_CLOSE)[register]
    texts = pack(register)
    if reason in texts.closes:
        return texts.closes[reason]
    if reason == DisqualifyReason.NO_CONTACT:
        return NO_CONTACT[register] + UNRESOLVED_REST[register]
    field = next(field for field in agent().fields if field.required == reason)
    return f"{texts.fields[field.id].unresolved}{UNRESOLVED_REST[register]}"


def is_fixed(action: Action) -> bool:
    """Replies sent as the template, with no model: license and policy closes, an abuse close
    that cancelled the interview, the question whether to cancel it and the line that it is
    cancelled, the goodbye to an opt-out (a model said "no guardamos nada"
    after consent, QA batch 3), the voice check of a no to the license question, the status
    of a closed application, going on without the phone or the email (SAZ-72, D4), a refused
    value asked again (D5, D7), the zone question for a city with several locations
    (SAZ-49: phrased by the model, it read its own instruction aloud), and the times offered
    for a question about them or to ask which one, and the one-line ask when the words fit
    several again (a model must never say what is free), why a place is the location the
    candidate has, and the summary by voice, read aloud with its values."""
    if isinstance(action, Clarify) and action.city:
        return True
    if isinstance(action, End) and (
        action.booking_cancelled or action.reason in (EndReason.OPT_OUT, EndReason.CANCELLED)
    ):
        return True
    if isinstance(action, AskCancel):
        return True
    if isinstance(action, ConfirmValue):
        spec = agent().field(action.field)
        read_back = spec.voice_read_back or spec.read_back.domain_typo
        return read_back or action.today is not None or action.explain
    if isinstance(action, ConfirmSummary):
        return action.voice and not action.correcting
    if isinstance(
        action,
        ApplicationStatus | SkipEmail | SkipPhone | ConfirmIdentity | DetailsUpdated | ConfirmMove,
    ):
        return True
    if isinstance(action, WarnPolicy):
        return True
    if isinstance(action, AskField):
        return action.hint in REFUSALS
    if isinstance(action, OfferSlots):
        return (
            action.reason in (OfferReason.ASKED, OfferReason.WHICH)
            or action.pick
            or not action.matched
        )
    return isinstance(action, Disqualify) and not agent().talent_pool(action.reason)


def is_unknown_answer(action: Action, ctx: ReplyContext) -> bool:
    """A question the FAQ and the documents did not cover: the unknown answer and the pending
    question are sent as written. Phrased by the model, the tú reference came back in usted
    (SAZ-72)."""
    return isinstance(action, AnswerQuestion) and action.topic is None and not ctx.faq_snippets


def unanswered_parts(ctx: ReplyContext) -> tuple[str, ...]:
    """The parts of a question that asks several things that retrieval found nothing for."""
    return tuple(part.question for part in ctx.faq_parts if not part.snippets)


def overview(language: Language, country: Country, voice: bool = False) -> str:
    """The fixed sentence on what the process looks like, said right after consent."""
    texts = pack(register_of(language, country))
    return texts.overview_voice if voice else texts.overview


def fallback_reply(action: Action, ctx: ReplyContext) -> str:
    """A static, register-correct phrasing of `action`; by voice, with interview days and
    times in words (`spoken_action`)."""
    register = register_of(ctx.language, ctx.country)
    voice = ctx.channel == Channel.VOICE
    if voice:
        action = spoken_action(action, register)
    if isinstance(action, AnswerQuestion) and action.topic is None and ctx.faq_snippets:
        lead = faq_answer(ctx.faq_snippets[0])
        if ctx.faq_country:
            lead = f"{IN_COUNTRY[register][ctx.faq_country]} {lead}"
        if ctx.faq_parts:
            # The best snippet answers one part at most: the line covers the others.
            lead = f"{lead} {UNKNOWN_REST[register]}"
        text = _with_follow_up(lead, action.follow_up, register, voice)
    else:
        text = _render(action, register, voice)
    text = CLOCK_DOUBLE_STOP.sub(r"\1", text) if voice else text
    return run_together(text, most_sentences(action)) if action.simplify else text


def run_together(text: str, most: int) -> str:
    """`text` in at most `most` sentences, its sentences joined by commas from the first: a
    frustrated candidate (`simplify`) gets one sentence, and a template is held to that too
    ("Gracias, ya tengo todo, ¿prefieres hacer la entrevista en persona o por videollamada?")."""
    while (count := count_sentences(text)) > most:
        for match in SENTENCE_BREAK.finditer(text):
            rest = text[match.end() :]
            if not _keeps_capital(rest):
                rest = re.sub(r"\w", lambda letter: letter.group(0).lower(), rest, count=1)
            joined = f"{text[: match.start()]}, {rest}"
            if count_sentences(joined) < count:
                text = joined
                break
        else:
            return text
    return text


def _render(action: Action, register: Register, voice: bool) -> str:
    match action:
        case Greet(retry=retry):
            if retry:
                return GREET_RETRY[register]
            texts = pack(register)
            return texts.greet_voice if voice else texts.greet
        case AskField():
            return _ask(action, register, voice)
        case Clarify(field=field, city=city) if city:
            zone = pack(register).fields[field].zone
            assert zone is not None
            return zone.format(city=_city_phrase(city, register))
        case Clarify(field=field, options=options):
            most = VOICE_MAX_OPTIONS if voice else MAX_BUTTONS
            if not options or len(options) > most:
                return pack(register).clarify_open
            labels = [option_label(field, option, _language(register)) for option in options]
            location = agent().field(field).type == FieldType.LOCATION
            template = CLARIFY_PLACE if location else CLARIFY
            return template[register].format(options=_join(labels, register, voice))
        case ConfirmValue():
            return _confirm_value(action, register, voice)
        case CheckDuplicate():
            return CHECK_DUPLICATE[register]
        case ResumeExisting():
            return RESUME_EXISTING[register]
        case WelcomeBack(name=name, follow_up=follow_up):
            lead = welcome_back(_language(register), action.country, name)
            return f"{lead} {_render(follow_up, register, voice)}"
        case ConfirmIdentity(name=name, matched_by=matched_by):
            known = (IDENTITY_VOICE if voice else IDENTITY_KNOWN)[register][matched_by]
            named, plain = (IDENTITY_QUESTION_VOICE if voice else IDENTITY_QUESTION)[register]
            first = (name or "").split()[:1]
            question = named.format(name=first[0]) if first else plain
            return f"{known} {question}"
        case AnswerQuestion(follow_up=follow_up):
            return _with_follow_up(answer_lead(action, register), follow_up, register, voice)
        case SkipEmail(follow_up=follow_up):
            return _with_follow_up(SKIP_EMAIL[register], follow_up, register, voice)
        case SkipPhone(has_email=has_email, follow_up=follow_up):
            lead = SKIP_PHONE[register][has_email]
            return _with_follow_up(lead, follow_up, register, voice)
        case Deflect(reason=reason, follow_up=follow_up):
            return _with_follow_up(DEFLECT[register][reason], follow_up, register, voice)
        case DeEscalate(closed=True):
            return DE_ESCALATE_CLOSED[register]
        case DeEscalate(follow_up=follow_up):
            return _with_follow_up(DE_ESCALATE[register], follow_up, register, voice)
        case OfferPause():
            return OFFER_PAUSE[register]
        case ConfirmSummary(correcting=True):
            return CORRECT_SUMMARY[register]
        case ConfirmSummary(profile=profile) if voice:
            details = _voice_summary(profile, _language(register))
            return CONFIRM_SUMMARY_VOICE[register].format(details=details)
        case ConfirmSummary():
            return CONFIRM_SUMMARY[register]
        case Disqualify(reason=reason, blocked=blocked):
            return disqualify_text(reason, register, voice, blocked=blocked)
        case WarnPolicy(follow_up=follow_up):
            return _with_follow_up(POLICY_WARNING[register], follow_up, register, voice)
        case FindModes():
            return FIND_MODES[register]
        case AskMode():
            return _ask_mode(action, register, voice)
        case FindSlots():
            return FIND_SLOTS[register]
        case BookSlot():
            return BOOK_SLOT[register]
        case OfferSlots():
            return _offer_slots(action, register, voice)
        case ConfirmBooking(booking=booking) if booking.mode == InterviewMode.VIDEO:
            return _confirm_video(action, booking, register, voice)
        case ConfirmBooking(booking=booking, rescheduled=rescheduled, kept=kept):
            if kept:
                template = KEEP_BOOKING_VOICE if voice else KEEP_BOOKING
            elif rescheduled:
                template = CONFIRM_RESCHEDULE_VOICE if voice else CONFIRM_RESCHEDULE
            else:
                template = CONFIRM_BOOKING_VOICE if voice else CONFIRM_BOOKING
            return template[register].format(
                when=booking.label, location=booking.location, address=booking.address
            )
        case ApplicationSaved(none_free=none_free):
            return (APPLICATION_SAVED_NONE_FREE if none_free else APPLICATION_SAVED)[register]
        case DetailsUpdated(booking=booking, changed=changed):
            changed_line, kept_line = DETAILS_UPDATED[register]
            return (changed_line if changed else kept_line).format(when=booking.label)
        case ConfirmMove(from_location_id=old, location_id=new):
            return CONFIRM_MOVE[register].format(new=branch_name(new), old=branch_name(old))
        case ApplicationStatus(booking=booking):
            # By web the chat stays open on a booking: replying here moves it. A call ends.
            if booking is not None and not voice:
                return f"{_status(booking, register)} {VIDEO_CHANGE[register]}"
            return f"{_status(booking, register)} {END[register][EndReason.CLOSED]}"
        case AskCancel():
            return (ASK_CANCEL_VOICE if voice else ASK_CANCEL)[register]
        case End(reason=EndReason.ABUSE, booking_cancelled=True):
            return END_ABUSE_CANCELLED[register]
        case End(reason=EndReason.OPT_OUT | EndReason.CANCELLED as reason, cancelled=Booked() as b):
            template = OPT_OUT_CANCELLED if reason == EndReason.OPT_OUT else CANCELLED
            return template[register].format(day=_booked_day(b, register))
        case End(reason=reason):
            return END[register][reason]
        case ReEngage(attempt=attempt, follow_up=follow_up):
            if follow_up is None:
                return RE_ENGAGE_ALONE[register]
            lead = RE_ENGAGE[register][0 if attempt <= 1 else 1]
            return _with_follow_up(lead, follow_up, register, voice)


def _confirm_value(action: ConfirmValue, register: Register, voice: bool) -> str:
    """A value read back, by the field's type: the pack's line, or the skeleton's for a
    location's loose match, an email's domain or a day and month gone by."""
    field = agent().field(action.field)
    texts = pack(register).fields[field.id]
    language = _language(register)
    value = action.value
    match field.type:
        case FieldType.LOCATION if action.location_id and action.explain:
            return _same_place(value, action.location_id, register)
        case FieldType.LOCATION if action.location_id:
            labels = [option_label(field.id, action.location_id, language)]
            return CLARIFY_PLACE[register].format(options=_join(labels, register, voice))
        case FieldType.YEARS if action.platform:
            assert texts.check is not None
            return texts.check.format(option=_option_name(action.platform, language))
        case FieldType.YEARS:
            line = texts.read_back_voice if voice else texts.read_back
            assert line is not None
            return line.format(years=format_years(float(value), language))
        case FieldType.CONTACT:
            return CONFIRM_EMAIL[register].format(value=value)
        case FieldType.DATE if action.today is not None:
            start = day_month(date.fromisoformat(value), language, year=True)
            return CONFIRM_DATE_ROLLED[register].format(
                today=day_month(action.today, language), value=start
            )
        case FieldType.DATE:
            value = _format_date(value, register)
        case _:
            pass
    assert texts.read_back is not None
    return texts.read_back.format(value=value)


def _option_name(option: str, language: Language) -> str:
    """An option by its label, in whichever field offers it (the platform a check doubts)."""
    return next(
        labels[option]
        for field in agent().fields
        if option in (labels := field.option_labels.get(language, {}))
    )


def spoken_action[A: Action](action: A, register: Register) -> A:
    """`action` with every interview time it carries in words, for the voice channel
    (SAZ-48): the labels of offered slots ("jueves 22 a las nueve de la mañana") and of
    a booked interview, also in a follow-up. Built from each one's local
    start, never by a model; the `ui` the page shows keeps the digits."""
    update: dict[str, object] = {}
    match action:
        case OfferSlots(slots=slots):
            update["slots"] = tuple(_spoken_slot(slot, register) for slot in slots)
        case (
            ConfirmBooking(booking=booking)
            | ApplicationStatus(booking=Booked() as booking)
            | DetailsUpdated(booking=booking)
        ):
            update["booking"] = _spoken_booking(booking, register)
        case _:
            pass
    follow_up = getattr(action, "follow_up", None)
    if follow_up is not None:
        update["follow_up"] = spoken_action(follow_up, register)
    return action.model_copy(update=update) if update else action


def _spoken_slot(slot: Slot, register: Register) -> Slot:
    return slot.model_copy(update={"label": spoken_when(slot.starts_at, register), "time": None})


def _spoken_booking(booking: Booked, register: Register) -> Booked:
    return booking.model_copy(update={"label": spoken_when(booking.starts_at, register)})


def welcome_back(language: Language, country: Country, full_name: str | None) -> str:
    """The line before the pending question when a returning candidate is resumed: one
    sentence, the lead of the `WelcomeBack` reply."""
    named, plain = WELCOME_BACK[register_of(language, country)]
    first = (full_name or "").split()[:1]
    return named.format(name=first[0]) if first else plain


def welcome_back_with_times(action: OfferSlots, full_name: str | None, *, voice: bool) -> str:
    """A returning candidate saved without an interview: the welcome and the new times in one
    fixed reply (QA batch 2), never "Seguimos donde lo dejaste" before a whole offer. By voice
    the first two times are said in words."""
    register = register_of(action.language, action.country)
    if voice:
        action = spoken_action(action, register)
        slots = _spoken_slots(action.slots, register)
    else:
        slots = _join([slot.label for slot in action.slots], register, voice)
    named, plain = WELCOME_BACK_TIMES[register]
    first = (full_name or "").split()[:1]
    head = named.format(name=first[0]) if first else plain
    return f"{head} {slots}. {WELCOME_BACK_TIMES_QUESTION[register]}"


def answer_lead(action: AnswerQuestion, register: Register) -> str:
    """The fixed answer for a known topic, else "I don't have that information"."""
    if action.topic == QuestionTopic.AI_IDENTITY:
        return AI_DISCLOSURE[register]
    return UNKNOWN_ANSWER[register]


def render_follow_up(follow_up: Action, ctx: ReplyContext) -> str:
    return _render(follow_up, register_of(ctx.language, ctx.country), ctx.channel == Channel.VOICE)


def _ask_mode(action: AskMode, register: Register, voice: bool) -> str:
    question = ASK_MODE_VOICE[register] if voice else ASK_MODE[register]
    if action.retry and action.simplify:
        # One sentence to a frustrated candidate; "no le entendí" reads as not listening.
        return question
    if action.retry:
        return f"{RETRY[register]} {question}"
    if action.qualified:
        lead = QUALIFIED_LEAD_VOICE[register] if voice else ASK_MODE_LEAD[register]
        return f"{lead} {question}"
    return question


def _confirm_video(action: ConfirmBooking, booking: Booked, register: Register, voice: bool) -> str:
    """A video interview: when, and where the Meet link is; never the link itself."""
    head = KEEP_VIDEO if action.kept else RESCHEDULE_VIDEO if action.rescheduled else CONFIRM_VIDEO
    if voice and head is CONFIRM_VIDEO:
        head = CONFIRM_VIDEO_VOICE
    parts = [head[register].format(when=booking.label)]
    link = bool(booking.meet_url)
    tell_link = not action.kept and (action.has_email or link)
    if tell_link and voice:
        parts.append(VIDEO_INVITE_VOICE[register][action.has_email])
    elif tell_link:
        parts.append(VIDEO_INVITE[register][(action.has_email, link)])
    if not voice:
        parts.append(VIDEO_CHANGE[register])
    return " ".join(parts)


def _offer_slots(action: OfferSlots, register: Register, voice: bool) -> str:
    """Only the slots in the action, by their labels; voice reads at most two. A frustrated
    candidate (`simplify`) gets the question alone by web: the times are on the buttons."""
    if not action.slots:
        return NO_SLOTS[register]
    if action.pick:
        if not voice:
            return OFFER_PICK[register]
        return OFFER_PICK_VOICE[register].format(
            slots=_spoken_slots(action.slots, register, days_only=True)
        )
    if action.simplify and not voice:
        simplified = OFFER_SIMPLIFIED[register]
        if action.matched:
            return simplified.get(action.reason, simplified[None])
        question = simplified[None]
        short = OFFER_UNMATCHED_SHORT[register][_miss(action)]
        return f"{short} {question[0].lower()}{question[1:]}"
    labels = [slot.label for slot in action.slots]
    slots = _spoken_slots(action.slots, register) if voice else _join(labels, register, voice)
    video = action.mode == InterviewMode.VIDEO
    if video:
        template = OFFER_SLOTS_VIDEO_VOICE if voice else OFFER_SLOTS_VIDEO
    else:
        template = OFFER_SLOTS_VOICE if voice else OFFER_SLOTS
    offer = template[register].format(location=action.slots[0].location, slots=slots)
    if voice and action.simplify and not action.matched:
        short = OFFER_UNMATCHED_SHORT[register][_miss(action)]
        return f"{short} {offer[0].lower()}{offer[1:]}"
    lead = _offer_lead(action, register, voice)
    return f"{lead} {offer}" if lead else offer


def _offer_lead(action: OfferSlots, register: Register, voice: bool) -> str | None:
    """The sentence before the times: why they are offered, or that the answer was not caught."""
    if not action.matched:
        return OFFER_LEAD_UNMATCHED[register][_miss(action)]
    if voice and action.reason in (OfferReason.QUALIFIED, OfferReason.MODE):
        return QUALIFIED_LEAD_VOICE[register]
    if action.reason == OfferReason.MODE:
        return OFFER_LEAD_MODE[register][action.mode or InterviewMode.ONSITE]
    if action.reason is not None:
        return OFFER_LEAD[register][action.reason]
    return RETRY[register] if action.retry else None


def _miss(action: OfferSlots) -> Miss:
    return action.miss or Miss.NONE


def _spoken_slots(slots: Sequence[Slot], register: Register, *, days_only: bool = False) -> str:
    """The first two slots read aloud, the time in words (SAZ-48): "martes 20 o el miércoles
    21, a las nueve de la mañana" when they share the time, else each with its own.
    `days_only`: a shared time is left out."""
    starts = [datetime.fromisoformat(slot.starts_at) for slot in slots[:VOICE_READ_SLOTS]]
    days = [spoken_day(start.date(), register) for start in starts]
    times = [spoken_time(time(start.hour, start.minute), register) for start in starts]
    joiner = f" {OR[register]} " if register == "en" else f" {OR[register]} el "
    if len(set(times)) == 1:
        return joiner.join(days) if days_only else f"{joiner.join(days)}, {times[0]}"
    if len(set(days)) == 1 and not days_only:
        # Two times on one day ("¿puede ser el martes?"): the day is said once.
        return f"{days[0]}, {f' {OR[register]} '.join(times)}"
    return joiner.join(f"{day} {at}" for day, at in zip(days, times, strict=True))


def _booked_day(booking: Booked, register: Register) -> str:
    """The interview's day as people name it: "viernes 23", "Friday the 23rd"."""
    return spoken_day(datetime.fromisoformat(booking.starts_at).date(), register)


def _status(booking: Booked | None, register: Register) -> str:
    if booking is None:
        return APPLICATION_STATUS_SAVED[register]
    if booking.mode == InterviewMode.VIDEO:
        return APPLICATION_STATUS_VIDEO[register].format(when=booking.label)
    return APPLICATION_STATUS_BOOKED[register].format(when=booking.label, location=booking.location)


def _ask(action: AskField, register: Register, voice: bool) -> str:
    texts = pack(register)
    asked = texts.fields[action.field]
    question = (asked.ask_voice if voice else None) or asked.ask
    if action.again:
        return f"{ASK_AGAIN[register]} {question}"
    if action.hint in REFUSALS:
        return f"{refusal_lead(action.field, action.hint, register)} {question}"
    if action.retry == 0 or (action.simplify and action.hint is None):
        # "No le entendí" to a frustrated candidate's complaint reads as not listening.
        return question
    hint = texts.hints.get(action.hint) if action.hint else None
    return f"{hint or RETRY[register]} {question}"


def refusal_lead(field: FieldId, hint: ReasonCode | None, register: Register) -> str:
    """Before asking again for a value the candidate would rather not give."""
    again = hint == ReasonCode.DECLINED_AGAIN
    texts = pack(register).fields[field]
    line = texts.refusal_again if again else texts.refusal
    return line or REFUSAL_OPTIONAL[register][again]


def _keeps_capital(text: str) -> bool:
    """`text` starts with a name: "I", a brand, the persona's name, the company's first word."""
    persona = agent().persona
    names = (*KEEPS_CAPITAL, *((persona.name, persona.company.split()[0]) if persona else ()))
    return re.match(rf"^[¿¡]?(?:{'|'.join(map(re.escape, names))})\b", text) is not None


def _with_follow_up(lead: str, follow_up: Action | None, register: Register, voice: bool) -> str:
    if follow_up is None:
        return lead
    return f"{lead} {_render(follow_up, register, voice)}"


def _join(items: list[str], register: Register, voice: bool) -> str:
    if voice:
        items = items[:VOICE_MAX_OPTIONS]
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} {OR[register]} {items[-1]}"


def _same_place(said: str, location_id: str, register: Register) -> str:
    """ "Roma está en la alcaldía Cuauhtémoc, así que le corresponde ese local. ¿Es correcto?" """
    language = _language(register)
    name = branch_name(location_id)
    if not said or fold(said) == fold(name):
        return CONFIRM_LOCATION[register].format(location=name)
    location = get_location(location_id)
    borough, area = SAME_PLACE_AREA[language]
    in_cdmx = location is not None and location.city == MEXICO_CITY
    where = (borough if in_cdmx else area).format(location=name)
    return CONFIRM_SAME_PLACE[register].format(place=said, area=where)


def _voice_summary(profile: CandidateProfile, language: Language) -> str:
    """The key values to read aloud by voice, comma-separated: first name, place and
    location, availability, start date (weekday and day: "desde el lunes 26")."""
    parts: list[str] = []
    if profile.full_name:
        parts.append(profile.full_name.split()[0])
    if profile.location_id:
        location = branch_name(profile.location_id)
        place = profile.place_said
        if place and fold(place) != fold(location):
            location = VOICE_SUMMARY_PLACE[language].format(place=place, location=location)
        parts.append(location)
    if profile.availability:
        joiner = " y " if language == Language.ES else " and "
        labels = (option_label("availability", a, language) for a in profile.availability)
        parts.append(joiner.join(label.lower() for label in labels))
    if profile.start_date:
        weekday = WEEKDAYS[language][profile.start_date.weekday()]
        parts.append(
            VOICE_SUMMARY_START[language].format(day=f"{weekday} {profile.start_date.day}")
        )
    return ", ".join(parts)


def _city_phrase(city: str, register: Register) -> str:
    """ "la Ciudad de México" in Spanish: the city's name takes the article."""
    if register != "en" and city.startswith("Ciudad de "):
        return f"la {city}"
    return city


def _language(register: Register) -> Language:
    return Language.EN if register == "en" else Language.ES


def _format_date(value: str, register: Register) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return format_date(parsed, _language(register), year=True)


def fallback_extraction(language: Language) -> TurnExtraction:
    """What the engine gets when the extractor fails: nothing understood, nothing stored."""
    return TurnExtraction(
        intent=Intent.UNCLEAR,
        fields={},
        language=language,
        matrix_language=MatrixLanguage(language.value),
        english_ratio=1.0 if language == Language.EN else 0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def key_fields(
    profile: CandidateProfile, language: Language = Language.ES, country: Country = "ES"
) -> dict[str, str]:
    fields: dict[str, str] = {}
    for field in agent().summary_fields:
        value = summary_value(profile, field, language)
        if value is not None:
            fields[field_label(field, language, country)] = value
    return fields


def profile_flags(profile: CandidateProfile) -> tuple[str, ...]:
    flags: list[str] = []
    if profile.late_start_days is not None:
        flags.append(f"late_start: {profile.late_start_days} days")
    if profile.experience_unverified:
        flags.append("experience_unverified")
    return tuple(flags)


def fallback_summary(
    profile: CandidateProfile, language: Language = Language.ES, country: Country = "ES"
) -> CandidateSummary:
    fields = key_fields(profile, language, country)
    line = "; ".join(f"{label}: {value}" for label, value in fields.items())
    return CandidateSummary(
        lines=(line,) if line else (),
        key_fields=fields,
        flags=profile_flags(profile),
    )
