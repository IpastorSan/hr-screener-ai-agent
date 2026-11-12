"""What the service found for a lookup the engine asked for (`CheckDuplicate`, `FindModes`,
`FindSlots`, `BookSlot`), handed back through `on_lookup`."""

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from sazon.engine.actions import BookSlot, FindModes, FindSlots
from sazon.engine.models import InterviewMode
from sazon.engine.slots import Booked, Miss, Slot
from sazon.engine.state import DuplicateMatch


class BaseLookupResult(BaseModel):
    model_config = ConfigDict(frozen=True)


class DuplicateChecked(BaseLookupResult):
    """The open application the phone or email matched, if any, and how many earlier
    applications with them were closed for a manipulation attempt (D11)."""

    kind: Literal["duplicate_checked"] = "duplicate_checked"
    match: DuplicateMatch | None = None
    policy_closes: int = 0


class ModesFound(BaseLookupResult):
    """The interview modes the location's recruiters offer."""

    kind: Literal["modes_found"] = "modes_found"
    request: FindModes
    modes: Sequence[InterviewMode]


class SlotsFound(BaseLookupResult):
    """The free slots, best first; for a `query`, whether they fit it (`miss` None) or are the
    nearest to it, and why."""

    kind: Literal["slots_found"] = "slots_found"
    request: FindSlots
    slots: Sequence[Slot]
    miss: Miss | None = None


class SlotBooked(BaseLookupResult):
    """The booking, or None when the slot filled up meanwhile."""

    kind: Literal["slot_booked"] = "slot_booked"
    request: BookSlot
    booked: Booked | None


type LookupResult = Annotated[
    DuplicateChecked | ModesFound | SlotsFound | SlotBooked, Field(discriminator="kind")
]
