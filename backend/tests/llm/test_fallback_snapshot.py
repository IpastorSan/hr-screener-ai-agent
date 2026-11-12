"""Every fixed line the templates can say, in every register and by web and by voice, against a
recorded snapshot (`snapshots/fallback_lines.json`): moving a line between the engine's skeleton
and an agent's text pack must not change a word of it. After a deliberate change to a line,
re-record with `SNAPSHOT_UPDATE=1`, and review the diff."""

import json
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from sazon.agents.riders import Close
from sazon.engine.actions import (
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
    Availability,
    CandidateProfile,
    InterviewMode,
    Language,
    Platform,
    QuestionTopic,
    Schedule,
)
from sazon.engine.slots import Booked, Miss, OfferReason, Slot
from sazon.engine.spec import FieldType, agent
from sazon.engine.state import DisqualifyReason
from sazon.engine.texts import REGISTERS
from sazon.engine.ui import field_label, field_options, option_label
from sazon.engine.validators import REFUSALS, Country, ReasonCode
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import (
    disqualify_text,
    fallback_reply,
    fallback_summary,
    is_fixed,
    overview,
    pack,
    refusal_lead,
    welcome_back,
    welcome_back_with_times,
)
from sazon.llm.interface import FaqPart

SNAPSHOT = Path(__file__).parent / "snapshots" / "fallback_lines.json"
TODAY = date(2026, 10, 21)
REASONS = (*Close, *DisqualifyReason)
"""Every close: the riders' and the engine's own."""
RIDER_HINTS = tuple(code for code in ReasonCode if code != ReasonCode.ANSWER_INVALID)
"""The hints the snapshot holds: a rider never gives `answer_invalid` (it has no yes/no, enum or
free text, and its multi_enums have readers of their own)."""
SPEAKERS: tuple[tuple[Language, Country], ...] = (
    (Language.ES, "ES"),
    (Language.ES, "MX"),
    (Language.EN, "ES"),
    (Language.EN, "MX"),
)
SLOTS = (
    Slot(
        id="s1",
        startsAt="2026-10-22T09:00:00+02:00",
        label="jueves 22 a las 09:00",
        location="Madrid Centro",
    ),
    Slot(
        id="s2",
        startsAt="2026-10-23T09:00:00+02:00",
        label="viernes 23 a las 09:00",
        location="Madrid Centro",
    ),
    Slot(
        id="s3",
        startsAt="2026-10-23T16:30:00+02:00",
        label="viernes 23 a las 16:30",
        location="Madrid Centro",
    ),
)
SAME_DAY = (SLOTS[1], SLOTS[2])
ONSITE = Booked(
    slotId="s1",
    startsAt="2026-10-22T09:00:00+02:00",
    label="jueves 22 de octubre a las 09:00",
    location="Madrid Centro",
    address="Calle Mayor 1",
)
VIDEO = ONSITE.model_copy(update={"mode": InterviewMode.VIDEO})
VIDEO_LINK = VIDEO.model_copy(update={"meet_url": "https://meet.google.com/abc-defg-hij"})
PROFILE = CandidateProfile(
    full_name="Lucía Pérez García",
    phone="+34612345678",
    email="lucia@example.com",
    has_license=True,
    location_id="mx-cdmx-cuauh",
    place_said="Roma",
    availability=[Availability.FULL_TIME, Availability.WEEKENDS],
    schedule=[Schedule.MORNING, Schedule.AFTERNOON],
    experience_years=1.5,
    platforms=[Platform.GLOVO, Platform.OTHER],
    platform_other="Stuart",
    start_date=date(2026, 10, 26),
    late_start_days=None,
)


def _prompts(language: Language, country: Country) -> Iterator[tuple[str, Any]]:
    """The actions that ask something, so they can follow another."""
    meta = {"language": language, "country": country}
    spec = agent()
    yield "greet", Greet(**meta)
    for field in spec.fields:
        yield f"ask {field.id}", AskField(field=field.id, **meta)
        yield f"ask {field.id} again", AskField(field=field.id, again=True, **meta)
        yield f"ask {field.id} retry", AskField(field=field.id, retry=1, **meta)
        for hint in RIDER_HINTS if field.id == "availability" else REFUSALS:
            yield f"ask {field.id} {hint}", AskField(field=field.id, retry=1, hint=hint, **meta)
        options = field_options(field.id, country)
        if options:
            yield f"clarify {field.id}", Clarify(field=field.id, options=options[:2], **meta)
            yield f"clarify {field.id} 3", Clarify(field=field.id, options=options[:3], **meta)
    yield "clarify none", Clarify(field="city", options=(), **meta)
    yield "clarify 5", Clarify(field="city", options=("a", "b", "c", "d", "e"), **meta)
    for options in (
        ("mx-cdmx-bju",),
        ("mx-cdmx-bju", "mx-cdmx-coy"),
        ("es-mad-centro", "es-mad-sur"),
    ):
        yield f"clarify city {options}", Clarify(field="city", options=options, **meta)
    for city in ("Ciudad de México", "Madrid"):
        yield (
            f"clarify zone {city}",
            Clarify(field="city", options=("a", "b", "c"), city=city, **meta),
        )
    yield "confirm license", ConfirmValue(field="has_license", value="no_license", **meta)
    yield "confirm place", ConfirmValue(field="city", value="Oaxaca", **meta)
    yield (
        "confirm loose",
        ConfirmValue(field="city", value="Grasia", location_id="es-bcn-gracia", **meta),
    )
    for said, location in (
        ("Roma", "mx-cdmx-cuauh"),
        ("Gràcia", "es-bcn-gracia"),
        ("", "es-mad-centro"),
        ("Madrid Centro", "es-mad-centro"),
    ):
        yield (
            f"confirm same place {said} {location}",
            ConfirmValue(field="city", value=said, location_id=location, explain=True, **meta),
        )
    for years in ("12", "0.5", "1", "20"):
        yield f"confirm years {years}", ConfirmValue(field="experience_years", value=years, **meta)
    for platform in (p for p in Platform if p != Platform.OTHER):
        yield (
            f"confirm platform {platform}",
            ConfirmValue(field="experience_years", value="9", platform=platform, **meta),
        )
    yield "confirm email", ConfirmValue(field="email", value="lucia@gmail.com", **meta)
    yield "confirm date", ConfirmValue(field="start_date", value="2027-03-01", **meta)
    yield "confirm date words", ConfirmValue(field="start_date", value="pronto", **meta)
    yield (
        "confirm date rolled",
        ConfirmValue(field="start_date", value="2027-10-01", today=TODAY, **meta),
    )
    for correcting in (False, True):
        for voice in (False, True):
            yield (
                f"summary {correcting} {voice}",
                ConfirmSummary(profile=PROFILE, correcting=correcting, voice=voice, **meta),
            )
    yield (
        "summary voice same place",
        ConfirmSummary(
            profile=PROFILE.model_copy(update={"place_said": None, "full_name": None}),
            voice=True,
            **meta,
        ),
    )
    for qualified, retry in ((False, 0), (True, 0), (False, 1)):
        yield (
            f"mode {qualified} {retry}",
            AskMode(
                options=(InterviewMode.ONSITE, InterviewMode.VIDEO),
                qualified=qualified,
                retry=retry,
                **meta,
            ),
        )
    yield from _offers(meta)
    for name in ("Lucía Pérez", None):
        for by in ("phone", "email"):
            yield f"identity {name} {by}", ConfirmIdentity(name=name, matched_by=by, **meta)


def _offers(meta: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    yield "offer none", OfferSlots(location_id="es-mad-centro", **meta)
    for slots in (SLOTS, SAME_DAY, SLOTS[:1]):
        n = len(slots)
        yield f"offer {n}", OfferSlots(location_id="es-mad-centro", slots=slots, **meta)
        yield (
            f"offer {n} retry",
            OfferSlots(location_id="es-mad-centro", slots=slots, retry=1, **meta),
        )
        yield (
            f"offer {n} pick",
            OfferSlots(location_id="es-mad-centro", slots=slots, pick=True, **meta),
        )
        for reason in OfferReason:
            for mode in (
                (None, InterviewMode.VIDEO) if reason != OfferReason.MODE else InterviewMode
            ):
                yield (
                    f"offer {n} {reason} {mode}",
                    OfferSlots(
                        location_id="es-mad-centro", slots=slots, reason=reason, mode=mode, **meta
                    ),
                )
        for miss in (None, *Miss):
            yield (
                f"offer {n} miss {miss}",
                OfferSlots(
                    location_id="es-mad-centro",
                    slots=slots,
                    reason=OfferReason.ASKED,
                    matched=False,
                    miss=miss,
                    **meta,
                ),
            )


def _actions(language: Language, country: Country) -> Iterator[tuple[str, Any]]:
    meta = {"language": language, "country": country}
    prompts = list(_prompts(language, country))
    yield from prompts
    follow_ups: list[tuple[str, Any]] = [
        ("", None),
        (" > ask full_name", AskField(field="full_name", **meta)),
        (" > ask availability retry", AskField(field="availability", retry=1, **meta)),
        (" > offer", OfferSlots(location_id="es-mad-centro", slots=SLOTS, **meta)),
    ]
    yield "greet retry", Greet(retry=1, **meta)
    yield "check duplicate", CheckDuplicate(phone="+34612345678", email=None, **meta)
    yield "resume", ResumeExisting(application_id="a1", **meta)
    for name in ("Lucía Pérez", None):
        for _, follow_up in follow_ups[1:]:
            yield (
                f"welcome back {name} {follow_up.kind}",
                WelcomeBack(name=name, follow_up=follow_up, **meta),
            )
        yield (
            f"welcome back {name} saved",
            WelcomeBack(name=name, follow_up=ApplicationSaved(**meta), **meta),
        )
    for tail, follow_up in follow_ups[1:]:
        yield f"warn policy{tail}", WarnPolicy(follow_up=follow_up, **meta)
    for tail, follow_up in follow_ups:
        yield (
            f"answer ai{tail}",
            AnswerQuestion(
                question="¿Eres un bot?",
                follow_up=follow_up,
                topic=QuestionTopic.AI_IDENTITY,
                **meta,
            ),
        )
        yield (
            f"answer unknown{tail}",
            AnswerQuestion(question="¿Hay gimnasio?", follow_up=follow_up, **meta),
        )
        yield f"skip email{tail}", SkipEmail(follow_up=follow_up, **meta)
        for has_email in (False, True):
            yield (
                f"skip phone {has_email}{tail}",
                SkipPhone(has_email=has_email, follow_up=follow_up, **meta),
            )
        for kind in DeflectKind:
            yield f"deflect {kind}{tail}", Deflect(reason=kind, follow_up=follow_up, **meta)
        yield f"de-escalate{tail}", DeEscalate(follow_up=follow_up, **meta)
        for attempt in (1, 2):
            yield (
                f"re-engage {attempt}{tail}",
                ReEngage(attempt=attempt, follow_up=follow_up, **meta),
            )
    yield "de-escalate closed", DeEscalate(follow_up=None, closed=True, **meta)
    yield "offer pause", OfferPause(**meta)
    for reason in REASONS:
        yield (
            f"disqualify {reason}",
            Disqualify(reason=reason, invite_reapply=False, talent_pool_offer=False, **meta),
        )
    yield (
        "disqualify blocked",
        Disqualify(
            reason=DisqualifyReason.POLICY_VIOLATION,
            invite_reapply=False,
            talent_pool_offer=False,
            blocked=True,
            **meta,
        ),
    )
    yield "find modes", FindModes(location_id="es-mad-centro", **meta)
    yield (
        "find slots",
        FindSlots(
            location_id="es-mad-centro",
            schedule=None,
            start_date=None,
            reason=OfferReason.QUALIFIED,
            **meta,
        ),
    )
    yield "book slot", BookSlot(slot_id="s1", **meta)
    for booking in (ONSITE, VIDEO, VIDEO_LINK):
        for has_email in (False, True):
            for flags in ({}, {"rescheduled": True}, {"kept": True}):
                yield (
                    f"booking {booking.mode} {booking.meet_url} {has_email} {flags}",
                    ConfirmBooking(booking=booking, has_email=has_email, **flags, **meta),
                )
        for changed in (False, True):
            yield (
                f"details updated {booking.mode} {changed}",
                DetailsUpdated(field="email", booking=booking, changed=changed, **meta),
            )
        yield f"status {booking.mode}", ApplicationStatus(booking=booking, **meta)
        yield f"ask cancel {booking.mode}", AskCancel(booking=booking, **meta)
    yield "status saved", ApplicationStatus(**meta)
    yield (
        "confirm move",
        ConfirmMove(
            booking=ONSITE, from_location_id="es-mad-centro", location_id="es-mad-sur", **meta
        ),
    )
    for none_free in (False, True):
        yield f"saved {none_free}", ApplicationSaved(none_free=none_free, **meta)
    for reason in EndReason:
        yield f"end {reason}", End(reason=reason, **meta)
        yield (
            f"end {reason} cancelled",
            End(reason=reason, booking_cancelled=True, cancelled=ONSITE, **meta),
        )


def _faq(language: Language, country: Country) -> Iterator[tuple[str, Any, dict[str, Any]]]:
    """An answer from the FAQ: as written, from the other country's documents, in parts."""
    meta = {"language": language, "country": country}
    follow_up = AskField(field="availability", **meta)
    action = AnswerQuestion(question="¿Cuánto se paga?", follow_up=follow_up, **meta)
    snippets = ("Q: ¿Cuánto se paga?\nA: 10 € la hora.",)
    other: Country = "MX" if country == "ES" else "ES"
    parts = (
        FaqPart(question="¿Cuánto se paga?", snippets=snippets),
        FaqPart(question="¿Hay gimnasio?"),
    )
    yield "faq", action, {"faq_snippets": snippets}
    yield "faq country", action, {"faq_snippets": snippets, "faq_country": other}
    yield "faq parts", action, {"faq_snippets": snippets, "faq_parts": parts}


def _lines() -> dict[str, Any]:
    lines: dict[str, Any] = {}
    for language, country in SPEAKERS:
        speaker = f"{language.value}-{country}"
        for channel in Channel:
            for simplify in (False, True):
                for name, action in _actions(language, country):
                    if simplify:
                        action = action.model_copy(update={"simplify": True})
                    ctx = ReplyContext.for_action(action, today=TODAY, channel=channel)
                    key = f"{speaker} {channel.value}{' simplify' if simplify else ''} | {name}"
                    lines[key] = [fallback_reply(action, ctx), is_fixed(action)]
            for name, action, faq in _faq(language, country):
                ctx = ReplyContext.for_action(action, today=TODAY, channel=channel, **faq)
                lines[f"{speaker} {channel.value} | {name}"] = [fallback_reply(action, ctx), False]
            offer = OfferSlots(
                location_id="es-mad-centro", slots=SLOTS, language=language, country=country
            )
            for name in ("Lucía Pérez", None):
                text = welcome_back_with_times(offer, name, voice=channel == Channel.VOICE)
                lines[f"{speaker} {channel.value} | welcome back with times {name}"] = text
        for voice in (False, True):
            lines[f"{speaker} | overview {voice}"] = overview(language, country, voice)
        for name in ("Lucía Pérez", None):
            lines[f"{speaker} | welcome back {name}"] = welcome_back(language, country, name)
        summary = fallback_summary(PROFILE, language, country)
        lines[f"{speaker} | summary"] = summary.model_dump(mode="json")
        spec = agent()
        for field in spec.fields:
            lines[f"{speaker} | label {field.id}"] = field_label(field.id, language, country)
            if field.type in (FieldType.MULTI_ENUM, FieldType.ENUM):
                for option in field.choices:
                    lines[f"{speaker} | option {field.id} {option}"] = option_label(
                        field.id, option, language
                    )
    for register in REGISTERS:
        lines[f"{register} | busy"] = pack(register).busy
        for reason in REASONS:
            for voice in (False, True):
                for blocked in (False, True):
                    text = disqualify_text(reason, register, voice, blocked=blocked)
                    lines[f"{register} | disqualify {reason} {voice} {blocked}"] = text
        for field in agent().fields:
            for hint in (ReasonCode.DECLINED, ReasonCode.DECLINED_AGAIN):
                lines[f"{register} | refusal {field.id} {hint}"] = refusal_lead(
                    field.id, hint, register
                )
    return lines


def test_every_fixed_line_is_as_recorded() -> None:
    lines = _lines()
    if os.environ.get("SNAPSHOT_UPDATE"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(
            json.dumps(lines, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
        )
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert lines.keys() == recorded.keys()
    changed = {key: (recorded[key], line) for key, line in lines.items() if line != recorded[key]}
    assert not changed, f"{len(changed)} lines changed, e.g. {next(iter(changed.items()))}"
