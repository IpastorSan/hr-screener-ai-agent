"""Deterministic checks on a responder reply before it is sent.

A reply that fails is retried once, then replaced by the template (`PydanticAILLM.respond`).
The style rules come from docs/screening-rules.md section 6: at most 2 short sentences (an
answer or an interruption may add one; one when the action says `simplify`), about 300
characters, one question and it is the last thing in the message, the country's register, one
language, and a read-back that leaves the values to the summary card. A greeting names Ana as
an AI ("asistente de IA", "AI assistant"): the AI Act's Article 50(1) disclosure. A reply
never talks about its options or instructions ("con más de dos opciones, pregunto por...").

By voice, no hour is written in digits (SAZ-48): text to speech garbles "a las 09:00", so the
labels come in words and the reply keeps them; the day of the month stays in digits. An FAQ
answer may quote a time in digits that its snippets carry.

An offer of interview times never says by itself what is free or not ("Las 12 no tengo
disponible"): only the lookup knows, and its fixed lines say it.

Interview replies never write a link (the booking card shows the Meet link, and voice never
reads one); a video confirmation says where the link is (the email or the card) and gives no
address; an in-person one never talks of a video call.

A goodbye after consent never says that nothing is kept or that the data is deleted: the
application is stored once the candidate accepted the privacy notice (QA batch 3). Only the
goodbye to a declined privacy notice says so, and it is true there.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from functools import cache

from sazon.engine.actions import (
    Action,
    AnswerQuestion,
    AskField,
    AskMode,
    ConfirmBooking,
    ConfirmSummary,
    DeEscalate,
    Deflect,
    End,
    EndReason,
    Greet,
    OfferPause,
    OfferSlots,
    ReEngage,
    SkipEmail,
    SkipPhone,
    WarnPolicy,
    WelcomeBack,
)
from sazon.engine.models import InterviewMode, Language
from sazon.engine.service_areas import get_location, load_locations
from sazon.engine.slots import OfferReason
from sazon.engine.spec import agent
from sazon.engine.text import fold, strip_accents
from sazon.engine.validators import REFUSALS, Country

MAX_REPLY_CHARS = 600
"""Above this a reply is runaway output, whatever it was for."""
MAX_CHARS = 300
MAX_ANSWER_CHARS = 400
"""`answer_question` (an answer plus the pending question) may run to 400 (respond.md)."""
MAX_SENTENCES = 2
MAX_SENTENCES_WITH_LEAD = 3
"""A greeting, an answer or an interruption before the pending question, a lead before the
offered slots, an acknowledgement of the frustration before offering a pause, or the welcome
back before a returning candidate's pending question."""
MAX_SIMPLIFIED_SENTENCES = 1
"""`simplify` (a frustrated candidate): one sentence, the buttons carry the options. A short
opening ("Entiendo, Rosa.") does not count; an answer to a question, or the calm line after an
insult, may add the pending question."""
LEAD_ACTIONS = (
    Greet,
    AnswerQuestion,
    SkipEmail,
    SkipPhone,
    Deflect,
    DeEscalate,
    ReEngage,
    OfferSlots,
    OfferPause,
    WelcomeBack,
    WarnPolicy,
)

# Lines a model writes to itself rather than to the candidate.
REASONING_OPENERS = re.compile(
    r"(?:^|[.!?]\s+)(?:wait|hmm+|actually|let me|let's see|note|ok,? so|okay,? so|espera|nota)\b",
    re.IGNORECASE,
)
REASONING_PHRASES = re.compile(
    r"context language|the (?:reply|response|message) (?:must|should)|reference text|\bjson\b"
    r"|</?[a-z_]+>|idioma del contexto|la respuesta debe|el mensaje debe"
    r"|\bi (?:should|need to|must) (?:reply|respond|write|answer)\b",
    re.IGNORECASE,
)
SPANISH_MARKERS = frozenset(
    re.findall(
        r"\S+",
        "el la los las que de del tu tus su sus aquí está están es por para con una un y qué "
        "cuál cuándo gracias hola tienes tiene datos todo correcto soy eres puedes puede "
        "quieres perfecto vale listo",
    )
)
ENGLISH_MARKERS = frozenset(
    re.findall(
        r"\S+",
        "the is are your you here what what's which when wait must should and with please i "
        "i'm thanks do does have can could would that this it my so shall we let's details "
        "everything correct reply",
    )
)
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
WORD = re.compile(r"[a-záéíóúñü']+")
MIN_MARKERS = 2

# tú forms in a Mexican reply, usted forms in a Spanish one. "su", "tiene" or "puede" alone are
# also the third person ("su local", "el local tiene parking"): usted is caught only where it
# addresses the candidate (SAZ-72), "¿me indica su correo?", "puede preguntarlo".
# Beyond the pronouns, the verb endings that only the second person has (QA batch 4: "buscas"
# to a Mexican candidate passed): the tú forms of the verbs Ana addresses a candidate with,
# the tú preterite ("-aste", "-iste"), future ("-rás") and imperfect ("-abas"), and an
# imperative with a pronoun attached, whose vowel tells the register ("indícame" is tú,
# "indíqueme" usted; for -er and -ir verbs the other way round, "escríbeme", "escríbame").
# A word that is also a noun ("las entregas", "con ganas") counts only after a word that makes
# it a verb addressed to the candidate. Checked on the reply without quoted text or place names
# (`register_slip`).
_ER_IR_IMPERATIVE = (
    r"escríb|dí|díg|píd|sígu|vuélv|entiénd|hág|póng|téng|véng|elíj|elíg|síg|recíb|ábr|respónd|permít|añád|compárt|descríb"
    r"|léa|lé|aprénd|vénd|escóg|escój|cúbr|sálg|tráig|cómprend|ofréc|conózc|condúzc"
    r"|repárt|asíst|decíd|subscríb|suscríb|inscríb"
)
_CLITIC = r"(?:me|nos|lo|la|los|las|te|se)"
_NAMES_LIKE_IMPERATIVES = r"(?!(?:ángela|ángeles|ávalos|dávalos|ánimos)\b)"
# An infinitive or gerund with two pronouns attached carries an accent too, and either register
# says it ("decírmelo", "enviártela", "explicándoselo"): its vowel before the last pronoun is
# the pronoun's, not an imperative's.
_NOT_INFINITIVE_OR_GERUND = (
    r"(?!\w*(?:[áéí]r|[áé]ndo)(?:me|te|se|nos|os)(?:lo|la|los|las|le|les)\b)"
)
_TU_VERBS = (
    r"tienes|puedes|quieres|vives|eres|estás|prefieres|necesitas|sabes|trabajas|has|buscas"
    r"|vienes|vas|haces|dices|conoces|conduces|manejas|repartes|aceptas|confirmas|eliges|escoges"
    r"|recibes|asistes|entiendes|piensas|crees|deseas|sigues|empiezas|comienzas|terminas|llegas"
    r"|sales|usas|recuerdas|olvidas|mueves|cancelas|apuntas|envías|mandas|lees|escribes"
    r"|respondes|contestas|estudias|hablas|encajas|sueles|dispones|vuelves|tardas|quedas"
    r"|puedas|quieras|tengas|prefieras|necesites|vivas|estés|seas|busques|sepas|vengas|hagas"
    r"|digas|elijas|recibas|llegues|empieces|olvides|preocupes|sientas|vayas|veas|uses|envíes"
    r"|escribas|respondas|confirmes|aceptes|cuentes|lleves|encajes"
    r"|podrías|tendrías|querrías|preferirías|estarías|serías|tenías|vivías|querías|podías|eras"
    r"|estabas|ibas|hacías|sabías|dime|dame|hazme|ponme|tráeme|avísame|cuéntame"
)
_TU_NOUN_LIKE = r"cuentas|entregas|pagas|ganas|cobras|esperas|llamas|pasas|marcas|cambias|llevas"
TU_FORMS = re.compile(
    rf"\b(?:tú|tu|tus|ti|te|contigo|{_TU_VERBS})\b|(?<!\bse )\b(?:elige|escoge)\b"
    rf"|(?:¿\s*(?:y\s+)?|\b(?:si|ya|no|tú|que|cuando|también|aún|todavía)\s+)(?:{_TU_NOUN_LIKE})\b"
    r"|\b(?!(?:contraste|desgaste|triste|chiste|alpiste|existe|consiste|asiste|insiste|resiste"
    r"|persiste|desiste|subsiste|viste)\b)\w{2,}(?:aste|iste)\b"
    r"|\b(?!(?:atrás|detrás)\b)\w{2,}rás\b"
    r"|\b(?!sílabas\b)\w{3,}abas\b"
    rf"|\b{_NAMES_LIKE_IMPERATIVES}{_NOT_INFINITIVE_OR_GERUND}(?!{_ER_IR_IMPERATIVE})"
    rf"\w*[áéíóú]\w*a{_CLITIC}\b"
    rf"|\b(?:{_ER_IR_IMPERATIVE})e{_CLITIC}\b",
    re.IGNORECASE,
)
USTED_FORMS = re.compile(
    r"\b(?:usted|ustedes|dígame|indíqueme|confírmeme|escríbame|cuénteme)\b"
    r"|\b(?:me|nos)\s+(?:indica|dice|confirma|facilita|deja|pasa|escribe)\b"
    r"|\bsu\s+(?:correo|e-?mail|móvil|teléfono|número|nombre|apellidos?|solicitud"
    r"|entrevista|carnet|datos|disponibilidad|experiencia)\b"
    r"|(?<!\bse )\bpuede\s+\w+(?:ar|er|ir)(?:lo|la|los|las)\b"
    # A question that opens on the verb, or ends on it, has the candidate as its subject.
    r"|¿\s*(?:y\s+)?(?:tiene|prefiere|vive|quiere|necesita|busca|trabaja|sabe|lleva|conoce"
    r"|maneja|conduce|desea|acepta|cuenta con|puede(?! ser\b))\b"
    r"|\b(?:vive|prefiere|tiene|necesita|busca|puede|quiere|trabaja|lleva|maneja)\s*\?"
    r"|\ble (?:va|viene|cuadra|encaja|parece) bien\b"
    rf"|\b{_NAMES_LIKE_IMPERATIVES}{_NOT_INFINITIVE_OR_GERUND}(?!{_ER_IR_IMPERATIVE})"
    rf"\w*[áéíóú]\w*e{_CLITIC}\b"
    rf"|\b(?:{_ER_IR_IMPERATIVE})a{_CLITIC}\b",
    re.IGNORECASE,
)
QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|«[^»]*»")


def register_slip(text: str, country: Country) -> str | None:
    """A form of the other register addressing the candidate in a Spanish reply: tú in Mexico,
    usted in Spain. Quoted words and place names ("Los Ángeles") are left out."""
    forms = TU_FORMS if country == "MX" else USTED_FORMS
    if match := forms.search(_places().sub(PLACE, QUOTED.sub(" ", text))):
        return match.group(0)
    return None


# Values a read-back must leave to the summary card (matched on folded text).
FIELD_WORDS = re.compile(
    r"\b(?:mananas?|tardes?|noches?|mornings?|afternoons?|evenings?|nights?|flexible"
    r"|tiempo completo|tiempo parcial|medio tiempo|jornada completa|media jornada"
    r"|fines? de semana|full ?time|part ?time|weekends?"
    r"|lunes|martes|miercoles|jueves|viernes|sabado|domingo"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre"
    r"|january|february|march|april|june|july|august|september|october|november|december"
    r"|glovo|uber|just eat|rappi|didi|deliveroo|amazon)\b"
)
DIGITS_OR_EMAIL = re.compile(r"[\d@]")

ABBREVIATIONS = re.compile(r"\b(?:Av|Avda|Col|Calz|Blvd|Fracc|Pza|Sr|Sra|Dr|Dra|St|No)\.")
CLOCK_ABBREVIATION = re.compile(r"\b([ap])\.m\.(?=\s+[a-z])")
"""A spoken English time inside a sentence ("at nine a.m. at our..."): not a sentence end."""
CONNECTOR = r"(?:de|del|la|las|los|el|y|of|the)"
NAME_WORD = r"[A-ZÁÉÍÓÚÑÜ][\w'\u2019-]*"
PROPER_NAME = re.compile(rf"(?<=[\w,:;] ){NAME_WORD}(?:\s+(?:{CONNECTOR}\s+)*{NAME_WORD})+")
"""Two or more capitalised words inside a sentence ("Col Jardines Universidad")."""
INTERJECTION = re.compile(r"^[¡]?\s*\w+(?:\s+\w+)?\s*!$")
"""A greeting such as "¡Hola!" or "Great!" does not count as a sentence."""
SHORT_LEAD = re.compile(r"^[¡]?\s*\w+(?:,?\s+\w+){0,2}\s*[.!]$")
"""An opening of up to three words ("Entiendo, Diego.", "Done, Diego.") is not a sentence."""
PLACE = "Lugar"
EMOJI_STYLE = "\N{VARIATION SELECTOR-16}"
"""Follows a symbol to draw it as an emoji ("❤️"); its category is Mn, not So."""
LINK = re.compile(r"https?://|www\.|meet\.google|\b[\w-]+\.(?:com|es|mx|net|org)/", re.IGNORECASE)
INTERVIEW_ACTIONS = (AskMode, OfferSlots, ConfirmBooking)
VIDEO_TERMS = re.compile(
    r"\b(?:video|videos|videollamada|video ?call|google meet|meet link|online|enlace|link)\b"
)
"""Matched on folded text: an in-person confirmation must not send the candidate to a call.
Not the bare "meet": "you'll meet the manager" is in person."""
MANAGER_TERMS = re.compile(r"\b(?:gerencia|gerente|encargad[oa]s?|managers?)\b")
"""Matched on folded text: the in-person interviewer, never a video call's, unless the agent
names its own (`AgentSpec.interviewers`)."""
RECRUITER_TERMS = re.compile(
    r"\b(?:equipo de (?:seleccion|reclutamiento)|reclutador\w*|recruit\w*|selection team)\b"
)
"""Matched on folded text: the video interviewer, never an in-person one's, unless the agent
names its own."""
INVITE_TERMS = re.compile(r"\b(?:correo|email|e mail|mail|enlace|link|invitacion|invitation)\b")
"""Matched on folded text: a video confirmation says where the Meet link comes."""
AI_TERM = re.compile(r"\b(?:ia|ai|inteligencia artificial|artificial intelligence)\b")
"""Matched on folded text: the greeting must say Ana is an AI, not just "virtual"."""
FOLLOW_UP_VERBS = (
    r"(?:avis|contact|llam)(?:ar|amos|aremos|ara|aran|e|emos|en)"
    r"|escrib(?:ir|imos|iremos|ira|iran|a|amos|an)"
    r"|notific(?:ar|amos|aremos|ara|aran)|informar(?:emos|a|an)|comunicar(?:emos|a|an)"
)
FOLLOW_UP_PROMISE = re.compile(
    rf"\b(?:te|le|les|lo|la|los|las) (?:{FOLLOW_UP_VERBS})\b"
    r"|\b(?:avisar|contactar|llamar|escribir|notificar|informar)(?:te|le|les|lo|la)\b"
    r"|\b(?:nos|se) pondr(?:a|an|emos) en contacto\b"
    r"|\b(?:nos|se) comunicar(?:a|an|emos)\b"
    r"|\b(?:recibir|tendr|dar)(?:as|a|an|emos) noticias\b"
    r"|\blet you know\b|\bnotify you\b|\bbe in touch\b|\bget in touch\b|\breach out\b"
    r"|\bhear (?:back )?from us\b|\bget back to you\b|\bkeep you (?:posted|updated|informed)\b"
    r"|\b(?:we|i|someone|they|the team)(?: ll| will) (?:contact|call|text|message) you\b"
)
"""Matched on folded text: a promise to contact, notify or tell the candidate something later.
Nobody follows up from the chat (SAZ-42): not when no time is free, not out of area."""
META_TALK = re.compile(
    r"\bopcion(?:es)?\b|\boptions?\b|\binstruccion(?:es)?\b|\binstructions?\b|\bprompt\b"
    r"|\bpregunt(?:o|are) por\b|(?<!may )(?<!can )(?<!could )\bi(?: ll| will)? ask\b"
)
"""Matched on folded text: talk about the options or the instructions instead of to the
candidate. Call 90cd0d40 (SAZ-49) said "Con más de dos opciones en Madrid, pregunto por el
barrio". A word the reference itself uses is allowed."""
NOT_WHAT_I_NEEDED = re.compile(
    r"\bno (?:era|es|fue) (?:el|lo|la|eso) (?:dato )?que (?:necesit|ped|buscab|pregunt)\w*"
    r"|\bno (?:era|es) (?:el dato|la respuesta|lo) que\b"
    r"|\b(?:te|le|se) (?:lo |la )?(?:pregunto|vuelvo a preguntar|repito)\b(?: \w+){0,2}"
    r" (?:de nuevo|otra vez)\b"
    r"|\b(?:vuelvo a preguntar|pregunto de nuevo|pregunto otra vez|repito la pregunta)\b"
    r"|\bno (?:te |le |lo |la )?(?:he )?entend\w*|\bno me (?:ha )?quedad?o claro\b"
    r"|\b(?:wasn t|was not|isn t|is not|not) (?:quite )?what i (?:needed|need|asked|was after)\b"
    r"|\b(?:ask|asking) (?:you |that |it )?again\b"
    r"|\bdidn t (?:quite )?(?:get|catch|understand) (?:that|it|you)\b"
)
"""Matched on folded text: a reply that treats the last answer as wrong ("ese dato no era el
que necesito, así que te lo pregunto de nuevo"). Only a retry, a hint or a correction may; a
first question after a valid answer never does (QA batch 4)."""
AVAILABILITY_CLAIM = re.compile(
    r"\bno (?:tengo|tenemos|hay|queda|quedan|me queda|me quedan|esta|estan|puede ser)\b"
    r"|\b(?:ocupad[oa]s?|lleno|llena|completo|completa)\b"
    r"|\b(?:not|isn t|aren t|no longer|nothing) (?:free|available)\b"
    r"|\b(?:taken|fully booked|no (?:times|slots))\b"
)
"""Matched on folded text: a statement that a time is not free ("las 12 no tengo disponible",
call of 2026-10-22). In an offer of times only the fixed lines of the lookup say it; a model
reply that does is retried, then the template is sent. The times it offers are free: saying
so is no claim."""
DIGITS_SPOKEN = re.compile(r"\b\d{1,2}[:.h]\d{2}\b|\b\d{1,2} ?(?:am|pm|a\.m\.|p\.m\.|h|hrs)(?!\w)")
"""Matched on lowercased text without accents: a time in digits ("09:00", "9 am"). By voice the
hour is said in words (SAZ-48); the day of the month stays in digits ("jueves 22")."""


def reply_problem(
    text: str | None,
    language: Language | None = None,
    *,
    country: Country | None = None,
    action: Action | None = None,
    reference: str | None = None,
    partial: bool = False,
    voice: bool = False,
    quotes: Sequence[str] = (),
) -> str | None:
    """Why a responder reply can't be sent as is, or None when it can.

    Without an `action`, only the content checks apply: length, leaked reasoning, language,
    register, promises to follow up and, by voice, hours in digits. With the `action` (and its
    template `reference`) the style rules apply too; `partial` checks a reply still being
    streamed, which may not have reached its question yet. `voice`: the reply is spoken, so no
    hour is in digits, except one the `reference` or the `quotes` (FAQ snippets) carry.
    """
    if not text or not text.strip():
        return "empty"
    if len(text) > MAX_REPLY_CHARS:
        return "too long"
    if REASONING_OPENERS.search(text) or REASONING_PHRASES.search(text):
        return "leaked reasoning"
    if language is not None and _mixes_languages(text, language):
        return "mixed languages"
    if language == Language.ES and country is not None and (slip := register_slip(text, country)):
        return f"wrong register: '{slip}'"
    if promise := follow_up_promise(text):
        return f"promises to follow up ('{promise}'): nobody contacts or notifies them later"
    if voice and (digits := spoken_digits(text, (reference or "", *quotes))):
        return f"a time in digits ('{digits}'): by voice say the hour in words, as the label"
    if action is None:
        return None
    return _style_problem(text, action, reference or "", partial, voice)


def _style_problem(
    text: str, action: Action, reference: str, partial: bool, voice: bool = False
) -> str | None:
    base = MAX_ANSWER_CHARS if isinstance(action, AnswerQuestion) else MAX_CHARS
    limit = max(base, len(reference))
    if len(text) > limit:
        return f"over {limit} characters"
    sentences = count_sentences(text)
    most = most_sentences(action, reference)
    if sentences > most:
        return f"{sentences} sentences (at most {most})"
    if text.count("?") > max(1, reference.count("?")):
        return "more than one question"
    end = text.rstrip().rstrip('"').rstrip()
    if "?" in text and not end.endswith("?"):
        return "the question is not the last thing"
    if not partial and reference.rstrip().endswith("?") and not end.endswith("?"):
        return "no question at the end (end with the action's question)"
    if _first_ask(action) and (wrong := NOT_WHAT_I_NEEDED.search(fold(text))):
        return f"treats the last answer as wrong ('{wrong.group(0)}'): this is a new question"
    if meta := meta_talk(text, reference):
        return f"talks about its options or instructions ('{meta}'): just ask the question"
    if _lacks_ai_disclosure(text, action, partial):
        return "no AI disclosure (say 'asistente de IA' or 'AI assistant')"
    # By voice there is no card: the summary reads its values aloud (sent as written).
    if (
        isinstance(action, ConfirmSummary)
        and not action.voice
        and (value := _restated_value(text, action))
    ):
        return f"read-back restates a value: '{value}'"
    if not isinstance(action, AnswerQuestion) and (name := internal_location_name(text)):
        return f"names a location as '{name}': say it as the reference does, without 'Sazón'"
    if isinstance(action, INTERVIEW_ACTIONS) and LINK.search(text):
        return "writes a link (the card shows the Meet link; never write or read one out)"
    if isinstance(action, OfferSlots | ConfirmBooking) and (
        who := _wrong_interviewer(text, action)
    ):
        return who
    taken = isinstance(action, OfferSlots) and action.reason == OfferReason.TAKEN
    if (
        isinstance(action, OfferSlots)
        and not taken
        and (claim := availability_claim(text, reference))
    ):
        return f"says what is free or not ('{claim}'): only the lookup says it"
    if isinstance(action, ConfirmBooking):
        return _booking_problem(text, action, partial, voice)
    stored = isinstance(action, End) and action.reason != EndReason.DECLINED_CONSENT
    if stored and (claim := nothing_stored_claim(text)):
        return f"says nothing is kept ('{claim}'): after consent the application is stored"
    return None


def interviewer_terms(mode: InterviewMode) -> re.Pattern[str]:
    """Who runs an interview in `mode`, as the agent names them ("jefatura de estudios"), else
    the riders' location manager in person and recruiting team by video."""
    own = agent().interviewer_words.get(mode.value)
    return own or (RECRUITER_TERMS if mode == InterviewMode.VIDEO else MANAGER_TERMS)


def _wrong_interviewer(text: str, action: OfferSlots | ConfirmBooking) -> str | None:
    """A video interview is with a recruiter, an in-person one with the location's manager
    (QA batch 4: "videollamada con la gerencia")."""
    mode = action.booking.mode if isinstance(action, ConfirmBooking) else action.mode
    folded = fold(text)
    onsite, video = (interviewer_terms(m) for m in (InterviewMode.ONSITE, InterviewMode.VIDEO))
    if mode == InterviewMode.VIDEO and (match := onsite.search(folded)):
        return f"video interview with the location's manager ('{match.group(0)}'): a recruiter"
    if mode != InterviewMode.VIDEO and (match := video.search(folded)):
        return f"in-person interview with a recruiter ('{match.group(0)}'): the manager"
    return None


def _booking_problem(
    text: str, action: ConfirmBooking, partial: bool, voice: bool = False
) -> str | None:
    folded = fold(text)
    booking = action.booking
    if booking.mode != InterviewMode.VIDEO:
        if match := VIDEO_TERMS.search(folded):
            return f"in-person interview described as a video call: '{match.group(0)}'"
        if partial or action.kept:
            return None
        if not interviewer_terms(InterviewMode.ONSITE).search(folded):
            return "in-person interview without the location's manager"
        street = fold(booking.address.split(",")[0]) if booking.address else ""
        if street and not voice and street not in folded:
            return "in-person interview without the location's address"
        return None
    if booking.address and fold(booking.address) in folded:
        return "video interview given the location's address"
    if (
        not partial
        and not action.kept
        and not interviewer_terms(InterviewMode.VIDEO).search(folded)
    ):
        return "video interview without the recruiting team ('el equipo de selección')"
    told = action.kept or not (action.has_email or booking.meet_url)
    if not partial and not told and not INVITE_TERMS.search(folded):
        return "video interview without saying where the Meet link comes (email or below)"
    return None


def _first_ask(action: Action) -> bool:
    """A question asked for the first time: no retry, hint or correction behind it."""
    if isinstance(action, AskField):
        return action.retry == 0 and action.hint is None and not action.again
    return isinstance(action, AskMode) and action.retry == 0


def most_sentences(action: Action, reference: str = "") -> int:
    """The most sentences a reply for `action` may have (`count_sentences`)."""
    if action.simplify:
        # Not loosened by the template: the cap is the point of simplifying.
        lead = (
            isinstance(action, AnswerQuestion | DeEscalate | SkipEmail | SkipPhone | WelcomeBack)
            and action.follow_up is not None
        ) or (isinstance(action, AskField) and action.hint in REFUSALS)
        return MAX_SIMPLIFIED_SENTENCES + int(lead)
    return max(
        MAX_SENTENCES_WITH_LEAD if isinstance(action, LEAD_ACTIONS) else MAX_SENTENCES,
        count_sentences(reference),
    )


def _lacks_ai_disclosure(text: str, action: Action, partial: bool) -> bool:
    """A first greeting that never says Ana is an AI. A partial (streamed) greeting is checked
    once it reaches its question, by which point Ana has introduced herself."""
    if not isinstance(action, Greet) or action.retry > 0:
        return False
    if partial and "?" not in text:
        return False
    return AI_TERM.search(fold(text)) is None


def count_sentences(text: str) -> int:
    """Sentences in a reply, not counting short exclamations or abbreviations in addresses."""
    masked = mask_places(CLOCK_ABBREVIATION.sub(r"\1m", text))
    parts = [part.strip() for part in SENTENCE_END.split(masked.strip())]
    if len(parts) > 1 and SHORT_LEAD.match(parts[0]):
        parts = parts[1:]
    return sum(1 for part in parts if re.search(r"\w", part) and not INTERJECTION.match(part))


def mask_places(text: str) -> str:
    """Location names, zones and addresses, and other multiword proper names, as one word.

    "Col. Rincón de la Sierra" is a place, not Spanish inside an English sentence.
    """
    text = _places().sub(PLACE, text)
    return PROPER_NAME.sub(PLACE, ABBREVIATIONS.sub(PLACE, text))


@cache
def _places() -> re.Pattern[str]:
    """Every place name, ignoring case. Zones that are everyday words ("Roma", "Centro",
    "Retiro") are masked too, which is harmless while `PLACE` is neutral to every check."""
    names: set[str] = {"Grupo Sazón", "Sazón"}
    for location in load_locations().values():
        names.update({location.name, location.city, location.address, *location.zones})
        names.update(alias for alias in location.aliases if len(alias) > 3)
        names.update(part.strip() for part in location.address.split(","))
    return _any_of(name for name in names if len(name) > 3)


def _any_of(names: Iterable[str]) -> re.Pattern[str]:
    """Any of `names` as whole words, longest first, ignoring case."""
    ordered = sorted(set(names), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(map(re.escape, ordered)) + r")\b", re.IGNORECASE)


NOTHING_STORED = re.compile(
    r"\bno (?:te |le |les )?(?:guardamos|guardo|guardare|guardaremos|hemos guardado|"
    r"he guardado|conservamos|conservo|conservaremos|almacenamos|nos quedamos con|"
    r"me quedo con|se guarda|se ha guardado|queda guardad[oa]|se queda)\b(?: \w+){0,6} "
    r"(?:nada|ningun|ninguna|dato|datos|informacion)\b"
    r"|\bnada (?:queda|se queda) guardad[oa]\b"
    r"|\b(?:borramos|eliminamos|borrare|eliminare|borro|elimino|hemos borrado|"
    r"hemos eliminado|he borrado|he eliminado) (?:todos )?(?:tus|sus|los) datos\b"
    r"|\b(?:won t|will not|don t|do not|didn t|did not|haven t|have not) "
    r"(?:keep|store|save|retain|hold)\b(?: \w+){0,4} (?:data|details|information)\b"
    r"|\bnothing (?:is|was|has been|will be|gets) (?:kept|stored|saved|retained)\b"
    r"|\b(?:we ll|we will|i ll|i will|we ve|we have|i ve) (?:delete|deleted|erase|erased|"
    r"remove|removed) (?:all )?your (?:data|details|information)\b"
)
"""A goodbye saying nothing is kept, or that the data is deleted (folded text)."""


COUNTRY_NAMES: dict[Country, re.Pattern[str]] = {
    "MX": re.compile(r"\b(?:mexico|mexican[oa]s?)\b"),
    "ES": re.compile(r"\b(?:espana|spain|spanish)\b"),
}
"""Matched on folded text: the country an answer from the other country's documents names."""


def names_country(text: str, country: Country) -> bool:
    """Whether `text` says it is about `country` ("En México se paga...")."""
    return COUNTRY_NAMES[country].search(fold(text)) is not None


def nothing_stored_claim(text: str) -> str | None:
    """The words of a claim that nothing of the candidate's is kept, if the reply makes one."""
    if match := NOTHING_STORED.search(fold(text)):
        return match.group(0)
    return None


def follow_up_promise(text: str) -> str | None:
    """The words of a promise to contact or notify the candidate later, if the reply makes one."""
    if match := FOLLOW_UP_PROMISE.search(fold(text)):
        return match.group(0)
    return None


def meta_talk(text: str, reference: str = "") -> str | None:
    """Words about the options or the instructions, if the reply has any its `reference` lacks."""
    allowed = {match.group(0) for match in META_TALK.finditer(fold(reference))}
    for match in META_TALK.finditer(fold(text)):
        if match.group(0) not in allowed:
            return match.group(0)
    return None


def availability_claim(text: str, reference: str = "") -> str | None:
    """Words about which times are free, if the reply has any its `reference` lacks."""
    allowed = {match.group(0) for match in AVAILABILITY_CLAIM.finditer(fold(reference))}
    for match in AVAILABILITY_CLAIM.finditer(fold(text)):
        if match.group(0) not in allowed:
            return match.group(0)
    return None


def spoken_digits(text: str, sources: Iterable[str] = ()) -> str | None:
    """A time written in digits, which text to speech reads badly, unless it is quoted from
    one of the `sources` (a document says "de 12:30 a 16:30")."""
    quoted = " ".join(strip_accents(source.casefold()) for source in sources)
    for match in DIGITS_SPOKEN.finditer(strip_accents(text.casefold())):
        if match.group(0) not in quoted:
            return match.group(0)
    return None


def internal_location_name(text: str) -> str | None:
    """A location's internal name ("Sazón CDMX Benito Juárez") in a reply. Ana says "la
    sucursal de Benito Juárez" (SAZ-42); FAQ answers, which quote the documents, may name it."""
    if match := _internal_names().search(fold(text)):
        return match.group(0)
    return None


@cache
def _internal_names() -> re.Pattern[str]:
    return _any_of(fold(location.name) for location in load_locations().values())


def _mixes_languages(text: str, language: Language) -> bool:
    if language == Language.EN and ("¿" in text or "¡" in text):
        return True
    for sentence in SENTENCE_END.split(mask_places(text)):
        words = WORD.findall(sentence.lower())
        spanish = sum(word in SPANISH_MARKERS for word in words)
        english = sum(word in ENGLISH_MARKERS for word in words)
        if language == Language.EN and spanish >= MIN_MARKERS and english == 0:
            return True
        if language == Language.ES and english >= MIN_MARKERS and spanish == 0:
            return True
    return False


def _restated_value(text: str, action: ConfirmSummary) -> str | None:
    """A field value written into the read-back sentence; the card already shows them all."""
    if match := DIGITS_OR_EMAIL.search(text):
        return match.group(0)
    folded = fold(text)
    if match := FIELD_WORDS.search(folded):
        return match.group(0)
    location = get_location(action.profile.location_id or "")
    if location is not None:
        for name in (location.city, *location.zones):
            if re.search(rf"\b{re.escape(fold(name))}\b", folded):
                return name
    return None


def strip_symbols(text: str) -> str:
    """Emoji and variation selectors removed, whitespace collapsed, wrapping quotes dropped."""
    text = "".join(c for c in text if unicodedata.category(c) != "So" and c != EMOJI_STYLE)
    return " ".join(text.split()).strip().strip('"').strip()


def clean_reply(
    text: str,
    language: Language | None = None,
    *,
    country: Country | None = None,
    action: Action | None = None,
    reference: str | None = None,
) -> str:
    """Strip emoji and wrapping quotes. A reply that fails `reply_problem` is discarded
    (empty string)."""
    text = strip_symbols(text)
    problem = reply_problem(text, language, country=country, action=action, reference=reference)
    return "" if problem is not None else text
