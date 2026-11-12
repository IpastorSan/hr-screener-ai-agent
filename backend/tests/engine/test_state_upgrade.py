"""The version 1 reader rejects a state whose flags no phase holds, loudly (the census in
`docs/engine/state-census.md` never reaches one), and drops what nothing reads again. Every
state the engine reaches is read in `evals/replay/tests/test_upgrade.py`."""

from typing import Any

import pytest
from pydantic import ValidationError

from sazon.agents.riders import FieldName
from sazon.engine.models import Stage
from sazon.engine.state import (
    Amending,
    Asking,
    CancelConfirm,
    Closed,
    Contact,
    Interview,
    InterviewBooked,
    Offering,
    PendingClarification,
    PendingConfirmation,
    Qualified,
)
from sazon.engine.upgrade import StateShapeError, downgrade, is_v1, upgrade

BOOKED = {
    "kind": "booking",
    "slotId": "s1",
    "startsAt": "2026-11-03T10:00:00+01:00",
    "label": "martes 3 de noviembre a las 10:00",
    "location": "Sol",
    "address": "Calle Mayor 1",
    "mode": None,
    "meetUrl": None,
}
CONFIRMATION = {"field": "city", "value": "Lyon"}
CLARIFICATION = {"field": "city", "options": ["mad-centro", "mad-norte"]}
ASKING_CITY = {"stage": "city", "consented": True, "awaiting": "city"}
BOOKED_DONE = {"stage": "done", "consented": True, "outcome": "qualified", "booking": BOOKED}


def test_a_stored_state_without_a_phase_is_version_1() -> None:
    assert is_v1({"stage": "city"})
    assert is_v1({})
    assert not is_v1({"state_version": 2, "phase": {"kind": "consent"}})


@pytest.mark.parametrize(
    ("flags", "phase"),
    [
        ({}, {"kind": "consent"}),
        (ASKING_CITY | {"pending_confirmation": CONFIRMATION}, "asking"),
        ({"stage": "contact", "consented": True}, "contact"),
        (BOOKED_DONE | {"cancel_offered": True, "amending": "email", "awaiting": "email"}, None),
    ],
)
def test_a_version_1_state_reads_into_its_phase(flags: dict[str, Any], phase: Any) -> None:
    upgraded = upgrade(flags)
    assert upgraded["state_version"] == 2
    assert not {"stage", "awaiting", "booking", "outcome"} & set(upgraded)
    if isinstance(phase, dict):
        assert upgraded["phase"].model_dump(mode="json") == phase
    elif phase is not None:
        assert upgraded["phase"].kind == phase


def test_the_read_back_belongs_to_the_field_asked() -> None:
    phase = upgrade(ASKING_CITY | {"pending_confirmation": CONFIRMATION})["phase"]
    assert phase == Asking(
        field=FieldName.CITY, confirmation=PendingConfirmation.model_validate(CONFIRMATION)
    )


def test_a_cancel_offered_while_correcting_a_detail_keeps_the_correction() -> None:
    flags = BOOKED_DONE | {"cancel_offered": True, "amending": "email", "awaiting": "email"}
    booked = upgrade(flags)["phase"].sub
    assert isinstance(booked, InterviewBooked)
    assert booked.sub == CancelConfirm(then=Amending(field=FieldName.EMAIL))


def test_leftovers_nothing_reads_again_are_dropped() -> None:
    """A close while times were on offer leaves the round's counter; a calendar cancellation
    right after "¿La cancelo?" leaves the offer; abuse while correcting a detail leaves the
    field marked as asked."""
    closed = upgrade(
        {"stage": "done", "consented": True, "outcome": "qualified", "slot_offers": 2}
        | {"slot_which_asked": True, "awaiting": "email"}
    )["phase"]
    assert closed == Closed(variant=Qualified())
    offering = upgrade(
        {"stage": "scheduling", "consented": True, "outcome": "qualified", "cancel_offered": True}
    )["phase"]
    assert offering == Interview(sub=Offering())
    saved = upgrade(
        {"stage": "done", "consented": True, "outcome": "qualified", "slots_declined": True}
        | {"talent_pool_opt_in": True}  # "new interview dates?", before SAZ-42
    )["phase"]
    assert saved == Closed(variant=Qualified(slots_declined=True))


@pytest.mark.parametrize(
    ("flags", "problem"),
    [
        # A detail corrected after booking with no interview booked.
        (
            {"stage": "done", "consented": True, "outcome": "qualified", "amending": "email"},
            "amending set",
        ),
        # A read-back at the summary, and a read-back with a choice between options.
        (
            {"stage": "confirm", "consented": True, "pending_confirmation": CONFIRMATION},
            "pending_confirmation set in a summary state",
        ),
        (
            ASKING_CITY
            | {"pending_confirmation": CONFIRMATION, "pending_clarification": CLARIFICATION},
            "both pending",
        ),
        # A read-back of another field than the one asked.
        (ASKING_CITY | {"pending_confirmation": {"field": "email", "value": "a@b.es"}}, "city"),
        # A booking on an application closed or withdrawn.
        (BOOKED_DONE | {"outcome": "disqualified", "disqualify_reason": "no_license"}, "booking"),
        # A correction and a move after booking both pending.
        (BOOKED_DONE | {"amending": "city", "awaiting": "city", "move_to": "bcn"}, "a move"),
        # An outcome while still asking, and an interview with none.
        (ASKING_CITY | {"outcome": "qualified"}, "outcome"),
        ({"stage": "mode", "consented": True}, "no outcome"),
        # Consent given with the notice still on screen, and a question with no consent.
        ({"stage": "consent", "consented": True}, "consent"),
        ({"stage": "city", "awaiting": "city"}, "consent"),
        # A question parked with no lookup to wait on, and a field asked at another stage.
        (ASKING_CITY | {"pending_question": {"question": "¿Cuánto pagan?"}}, "pending_question"),
        ({"stage": "license", "consented": True, "awaiting": "city"}, "city asked at license"),
        # A gate close reopened for a close that is not one, and a talent pool in no area.
        (
            {"stage": "done", "consented": True, "outcome": "disqualified"}
            | {"disqualify_reason": "policy_violation", "reopenable": True},
            "not reopened",
        ),
        # A close with no outcome that handed over to no application.
        ({"stage": "done", "consented": True}, "application_id"),
    ],
)
def test_a_pair_no_phase_holds_is_rejected(flags: dict[str, Any], problem: str) -> None:
    with pytest.raises((StateShapeError, ValidationError), match=problem):
        upgrade(flags)


def test_impossible_pairs_cannot_be_built_either() -> None:
    with pytest.raises(ValidationError, match="both pending"):
        Asking(
            field=FieldName.CITY,
            confirmation=PendingConfirmation.model_validate(CONFIRMATION),
            clarification=PendingClarification.model_validate(CLARIFICATION),
        )
    with pytest.raises(ValidationError, match="with an interview booked"):
        Offering(cancel_offered=True)
    with pytest.raises(ValidationError, match="not on a match"):
        Contact.model_validate(
            {"match": {"application_id": "a"}, "parked_question": {"question": "¿Cuánto pagan?"}}
        )


def test_downgrade_writes_the_flags_back() -> None:
    flags = BOOKED_DONE | {"amending": "email", "awaiting": "email", "amend_retries": 1}
    upgraded = upgrade(flags)
    stored = {**upgraded, "phase": upgraded["phase"].model_dump(mode="json")}
    back = downgrade(stored)
    assert {name: back[name] for name in flags} == flags
    assert back["stage"] == Stage.DONE.value
