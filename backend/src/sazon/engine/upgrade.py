"""The stored shape of `ConversationState` before the state carried its phase, and the reader
that turns it into the current one.

Version 1 kept the phase as a `stage` and about twenty flags beside it. Version 2 keeps it as one
`phase` (`state.Phase`): each flag became a field of the one state it means something in, so a
pair the engine never writes (a read-back while booking, a correction with no booking) can no
longer be stored. The reader (`upgrade`) builds the phase from the flags, and rejects a state whose
flags no phase holds: the census (`docs/engine/state-census.md`) lists every pair the engine
reaches, and each of them has a home. A flag left over from an earlier step, which nothing reads
again, is dropped (`LEFTOVERS`). `downgrade` writes a phase back as flags, for a rollback.

Kept for one release after the migration that rewrote the stored rows (`0011`), then removed with
the version 1 shape.
"""

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from sazon.engine.models import Outcome, Stage, StepId
from sazon.engine.spec import agent
from sazon.engine.state import (
    VERSION,
    Abandoned,
    Amending,
    Asking,
    CancelConfirm,
    ChoosingMode,
    Closed,
    Consent,
    Contact,
    DeclinedConsent,
    Disqualified,
    Ended,
    HandedOver,
    Idle,
    Interview,
    InterviewBooked,
    Moving,
    Offering,
    Phase,
    Qualified,
    Summary,
    Withdrawn,
)

FLAGS: dict[str, Any] = {
    "stage": Stage.CONSENT.value,
    "awaiting": None,
    "pending_clarification": None,
    "pending_confirmation": None,
    "pending_question": None,
    "pending_identity": None,
    "duplicate_of": None,
    "mode_retries": 0,
    "offered_slots": [],
    "slot_offers": 0,
    "slot_retries": 0,
    "slot_which_asked": False,
    "booking": None,
    "cancel_offered": False,
    "amending": None,
    "amend_retries": 0,
    "move_to": None,
    "move_said": None,
    "outcome": None,
    "disqualify_reason": None,
    "reopenable": False,
    "talent_pool_opt_in": None,
    "drop_off_stage": None,
    "slots_declined": False,
    "opted_out": False,
    "restarted": False,
}
"""The version 1 fields the phase replaced, with their defaults as stored."""

V1_ORDER = (
    "stage",
    "awaiting",
    "profile",
    "context",
    "country",
    "country_source",
    "language",
    "language_locked",
    "recent_matrix",
    "consented",
    "first_name_only",
    "retries",
    "hints",
    "skipped",
    "pending_clarification",
    "pending_confirmation",
    "declined_locations",
    "pending_question",
    "duplicate_checked",
    "duplicate_email_unchecked",
    "duplicate_of",
    "pending_identity",
    "declined_applications",
    "policy_closes",
    "policy_warned",
    "abuse_strikes",
    "frustration_streak",
    "pause_offered",
    "de_escalated",
    "paused",
    "available_modes",
    "mode_retries",
    "offered_slots",
    "slot_offers",
    "slot_retries",
    "slot_which_asked",
    "booking",
    "slots_declined",
    "declined_slots",
    "shown_slots",
    "slot_query",
    "cancel_offered",
    "outcome",
    "disqualify_reason",
    "talent_pool_opt_in",
    "drop_off_stage",
    "quality_flags",
    "confirmed_experience",
    "voice",
    "read_back",
    "fix_field",
    "contact_notice",
    "amending",
    "amend_retries",
    "move_to",
    "move_said",
    "reopenable",
    "opted_out",
    "restarted",
)
"""Every version 1 field, in the order it was declared and dumped."""

LEFTOVERS = frozenset({"slot_offers", "slot_retries", "slot_which_asked", "mode_retries"})
"""Counters of a step that ended without resetting them (a close while times were on offer, a
booking kept when no new time suited): read only in that step, which sets them afresh. Outside
it they are dropped. So is a cancel offer with no interview left to cancel (a calendar
cancellation right after it, `reoffer`), a field still marked as asked after a close (abuse
while correcting a detail after booking), and an answer to the talent-pool question anywhere
but out of area (before SAZ-42 a saved application was asked about new interview dates with the
same field; migration 0008 left it out of the talent pool): nothing reads them again."""

PHASE = TypeAdapter[Phase](Phase)


class StateShapeError(ValueError):
    """A version 1 state whose flags no phase holds: the engine never writes it."""


def is_v1(data: Mapping[str, Any]) -> bool:
    return "phase" not in data and data.get("state_version", 1) < VERSION


def upgrade(data: Mapping[str, Any]) -> dict[str, Any]:
    """A version 1 state as version 2: its flags replaced by the phase they mean. Values may be
    stored JSON or the engine's own objects (a test's keyword arguments)."""
    flags = {**FLAGS, **{name: data[name] for name in FLAGS if name in data}}
    reading = _Reading(flags, consented=bool(data.get("consented", False)))
    phase = reading.phase()
    unread = sorted(
        name
        for name, value in flags.items()
        if name not in reading.read and name not in LEFTOVERS and _is_set(name, value)
    )
    if unread:
        raise StateShapeError(f"{', '.join(unread)} set in a {_describe(phase)} state")
    # A field version 1 no longer had is ignored, as version 1 ignored it.
    kept = {name: data[name] for name in V1_ORDER if name in data and name not in FLAGS}
    return {**kept, "state_version": VERSION, "phase": phase}


def downgrade(data: Mapping[str, Any]) -> dict[str, Any]:
    """A version 2 state as version 1, for a rollback: the phase written back as flags."""
    phase = PHASE.validate_python(data["phase"])
    flat = {**data, **FLAGS, **to_jsonable_python(_flags(phase))}
    return {name: flat[name] for name in V1_ORDER if name in flat}


def _is_set(name: str, value: Any) -> bool:
    default = FLAGS[name]
    return bool(value) if isinstance(default, list) else value != default


def _describe(phase: Phase) -> str:
    inner = getattr(phase, "sub", None) or getattr(phase, "variant", None)
    return ".".join(filter(None, (phase.kind, getattr(inner, "kind", None))))


class _Reading:
    """One version 1 state read into its phase; `read` names the flags the phase took."""

    def __init__(self, flags: dict[str, Any], *, consented: bool) -> None:
        self.flags = flags
        self.consented = consented
        self.read: set[str] = {"stage"}

    def take(self, name: str) -> Any:
        self.read.add(name)
        return self.flags[name]

    def require(self, ok: bool, problem: str) -> None:
        if not ok:
            raise StateShapeError(problem)

    def phase(self) -> Phase:
        stage: StepId = self.flags["stage"]
        outcome = self.take("outcome")
        outcome = Outcome(outcome) if outcome is not None else None
        if stage == Stage.DONE:
            return self.closed(outcome)
        self.require(
            outcome is None or (stage in (Stage.MODE, Stage.SCHEDULING) and outcome == "qualified"),
            f"outcome {outcome} at {stage}",
        )
        if stage == Stage.CONSENT or not self.consented:
            self.require(stage == Stage.CONSENT and not self.consented, f"consent at {stage}")
            return Consent()
        if self.flags["pending_identity"] is not None or (
            stage == agent().contact_step and self.flags["awaiting"] is None
        ):
            return self.contact(stage)
        if stage == Stage.CONFIRM:
            return Summary()
        if stage in (Stage.MODE, Stage.SCHEDULING):
            self.require(outcome is not None, f"no outcome at {stage}")
            return Interview(sub=self.mode() if stage == Stage.MODE else self.offering())
        return self.asking(stage)

    def asking(self, stage: StepId) -> Asking:
        field = self.take("awaiting")
        self.require(field is not None, f"nothing asked at {stage}")
        self.require(field in agent().by_id, f"no field {field}")
        self.require(agent().field(field).funnel_step == stage, f"{field} asked at {stage}")
        return Asking(
            field=field,
            confirmation=self.take("pending_confirmation"),
            clarification=self.take("pending_clarification"),
        )

    def contact(self, stage: StepId) -> Contact:
        self.require(stage == agent().contact_step, f"a contact lookup at {stage}")
        self.require(self.take("awaiting") is None, "a field asked during a contact lookup")
        return Contact(
            match=self.take("pending_identity"),
            parked_question=self.take("pending_question"),
        )

    def mode(self) -> ChoosingMode:
        return ChoosingMode(
            retries=self.take("mode_retries"), parked_question=self.take("pending_question")
        )

    def offering(self) -> Offering:
        booking, cancel_offered = self.take("booking"), self.take("cancel_offered")
        return Offering(
            slots=self.take("offered_slots"),
            offers=self.take("slot_offers"),
            retries=self.take("slot_retries"),
            which_asked=self.take("slot_which_asked"),
            booking=booking,
            cancel_offered=booking is not None and cancel_offered,
            parked_question=self.take("pending_question"),
        )

    def booked(self) -> InterviewBooked:
        sub: Idle | Amending | Moving | CancelConfirm = Idle()
        amending, move_to = self.take("amending"), self.take("move_to")
        self.require(self.flags["awaiting"] == amending, "a detail asked but not corrected")
        if amending is not None:
            sub = Amending(field=amending, retries=self.take("amend_retries"))
        if move_to is not None:
            self.require(amending is None, "a move and a correction both pending")
            sub = Moving(location_id=move_to, said=self.take("move_said"))
        if self.take("cancel_offered"):
            sub = CancelConfirm(then=None if isinstance(sub, Idle) else sub)
        return InterviewBooked(booking=self.take("booking"), sub=sub)

    def closed(self, outcome: Outcome | None) -> Interview | Closed:
        self.read |= {"awaiting", "cancel_offered", "talent_pool_opt_in"}  # see `LEFTOVERS`
        if outcome == Outcome.QUALIFIED and self.flags["booking"] is not None:
            return Interview(sub=self.booked())
        variants: dict[Outcome | None, Callable[[], Any]] = {
            None: lambda: HandedOver(application_id=self.take("duplicate_of")),
            Outcome.DISQUALIFIED: lambda: Disqualified(
                reason=self.take("disqualify_reason"),
                reopenable=self.take("reopenable"),
                talent_pool_opt_in=self.flags["talent_pool_opt_in"]
                if agent().talent_pool(self.flags["disqualify_reason"])
                else None,
            ),
            Outcome.QUALIFIED: lambda: Qualified(
                slots_declined=self.take("slots_declined"), opted_out=self.take("opted_out")
            ),
            Outcome.WITHDRAWN: lambda: Withdrawn(opted_out=self.take("opted_out")),
            Outcome.ABANDONED: lambda: Abandoned(
                drop_off=self.take("drop_off_stage"),
                match=self.take("pending_identity"),
                opted_out=self.take("opted_out"),
                restarted=self.take("restarted"),
            ),
            Outcome.DECLINED_CONSENT: DeclinedConsent,
        }
        return Closed(variant=variants[outcome]())


def _flags(phase: Phase) -> dict[str, Any]:
    """The version 1 flags a phase stands for."""
    match phase:
        case Consent():
            return {"stage": Stage.CONSENT}
        case Asking(field=field):
            return {
                "stage": agent().field(field).funnel_step,
                "awaiting": field,
                "pending_confirmation": phase.confirmation,
                "pending_clarification": phase.clarification,
            }
        case Contact():
            return {
                "stage": agent().contact_step,
                "pending_identity": phase.match,
                "pending_question": phase.parked_question,
            }
        case Summary():
            return {"stage": Stage.CONFIRM}
        case Interview(sub=InterviewBooked() as booked):
            return _flags_booked(booked)
        case Interview(sub=ChoosingMode() as choosing):
            return {
                "stage": Stage.MODE,
                "outcome": Outcome.QUALIFIED,
                "pending_question": choosing.parked_question,
                "mode_retries": choosing.retries,
            }
        case Interview(sub=Offering() as offering):
            return {
                "stage": Stage.SCHEDULING,
                "outcome": Outcome.QUALIFIED,
                "pending_question": offering.parked_question,
                "offered_slots": offering.slots,
                "slot_offers": offering.offers,
                "slot_retries": offering.retries,
                "slot_which_asked": offering.which_asked,
                "booking": offering.booking,
                "cancel_offered": offering.cancel_offered,
            }
        case Closed(variant=variant):
            return {"stage": Stage.DONE, **_flags_closed(variant)}
    raise AssertionError(phase)


def _flags_booked(booked: InterviewBooked) -> dict[str, Any]:
    flags: dict[str, Any] = {"stage": Stage.DONE, "outcome": Outcome.QUALIFIED}
    flags["booking"] = booked.booking
    sub = booked.sub
    if isinstance(sub, CancelConfirm):
        flags["cancel_offered"] = True
        sub = sub.then
    if isinstance(sub, Amending):
        flags |= {"amending": sub.field, "awaiting": sub.field, "amend_retries": sub.retries}
    elif isinstance(sub, Moving):
        flags |= {"move_to": sub.location_id, "move_said": sub.said}
    return flags


def _flags_closed(variant: Disqualified | Ended) -> dict[str, Any]:
    match variant:
        case HandedOver(application_id=application_id):
            return {"duplicate_of": application_id}
        case Disqualified():
            return {
                "outcome": Outcome.DISQUALIFIED,
                "disqualify_reason": variant.reason,
                "reopenable": variant.reopenable,
                "talent_pool_opt_in": variant.talent_pool_opt_in,
            }
        case Qualified():
            return {
                "outcome": Outcome.QUALIFIED,
                "slots_declined": variant.slots_declined,
                "opted_out": variant.opted_out,
            }
        case Withdrawn():
            return {"outcome": Outcome.WITHDRAWN, "opted_out": variant.opted_out}
        case Abandoned():
            return {
                "outcome": Outcome.ABANDONED,
                "drop_off_stage": variant.drop_off,
                "pending_identity": variant.match,
                "opted_out": variant.opted_out,
                "restarted": variant.restarted,
            }
        case DeclinedConsent():
            return {"outcome": Outcome.DECLINED_CONSENT}
    raise AssertionError(variant)
