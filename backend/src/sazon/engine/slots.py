"""Interview slots as the engine sees them. The scheduling service builds them; the engine
only offers, matches and confirms the ones it was given."""

from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sazon.engine.models import InterviewMode

NONE_WORK = "none"
"""`TurnExtraction.slot` when none of the offered times suit the candidate."""
CHANGE = "change"
"""`TurnExtraction.slot` when the candidate wants to move the interview already booked."""


AFTERNOON_FROM = 14
"""Local hour the afternoon starts at, for "por la tarde" (docs/screening-rules.md)."""
VOICE_READ_SLOTS = 2
"""By voice only the first two offered times are read aloud: only those count as offered when
the candidate asks for other times."""


class OfferReason(StrEnum):
    """Why slots are offered: right after qualifying, more after "none work", to move a
    booking, because the picked slot filled up meanwhile, for the other interview mode the
    candidate switched to, for the day or time of day they asked about (`asked`), or again,
    only those the candidate's words fit, to ask which one (`which`)."""

    QUALIFIED = "qualified"
    MORE = "more"
    RESCHEDULE = "reschedule"
    TAKEN = "taken"
    MODE = "mode"
    ASKED = "asked"
    WHICH = "which"


class Miss(StrEnum):
    """Why nothing free fits what the candidate asked about, for the fixed line before the
    nearest times: nothing fits at all (`none`), the clock time they named is not free
    (`time`), or nothing is left beyond the times already offered (`more`). `left`: nothing
    free at all beyond them, and the offer on screen stays."""

    NONE = "none"
    TIME = "time"
    MORE = "more"
    LEFT = "left"


class SlotQuery(BaseModel):
    """Times the candidate asked about ("¿tienes algo por la tarde?", "¿y el viernes?", "¿algo
    la semana que viene?", "más tarde", "¿podría ser a las 12?", "¿y otras opciones?"), read
    from their words by code, never by a model. A local start fits when it meets every
    condition set; a query with none ("¿y otras opciones?") fits every time. `at`: the clock
    times named (both readings of "a las 4"): not a condition but the target, so the lookup
    offers that time if it is free, else the nearest to it."""

    model_config = ConfigDict(frozen=True)

    weekdays: tuple[int, ...] = ()
    days: tuple[date, ...] = ()
    week_of: date | None = None
    """The Monday of the week asked about."""
    part: Literal["morning", "afternoon"] | None = None
    later_than: time | None = None
    earlier_than: time | None = None
    at: tuple[time, ...] = ()
    other: bool = False
    """The words ask for other times ("¿qué otros huecos tienes a las 12?"): never one already
    offered, even at the time named."""

    def fits(self, local: datetime) -> bool:
        day, clock = local.date(), time(local.hour, local.minute)
        checks = (
            not self.weekdays or day.weekday() in self.weekdays,
            not self.days or day in self.days,
            self.week_of is None or 0 <= (day - self.week_of).days < 7,
            self.part != "morning" or local.hour < AFTERNOON_FROM,
            self.part != "afternoon" or local.hour >= AFTERNOON_FROM,
            self.later_than is None or clock > self.later_than,
            self.earlier_than is None or clock < self.earlier_than,
        )
        return all(checks)

    def minutes_off(self, local: datetime) -> int:
        """How far the start is from the nearest time named in `at`, in minutes (0 without)."""
        if not self.at:
            return 0
        minutes = local.hour * 60 + local.minute
        return min(abs(minutes - (at.hour * 60 + at.minute)) for at in self.at)

    @property
    def unconstrained(self) -> bool:
        """No condition and no time: "¿y otras opciones?"."""
        return self.model_copy(update={"other": False}) == SlotQuery()

    @property
    def nearest_day_first(self) -> bool:
        """A weekday alone ("¿puede ser el martes?"): the nearest such day comes first."""
        return bool(self.weekdays) and not self.days and self.week_of is None and not self.at

    def targets(self) -> tuple[date, ...]:
        """The days asked about, when the query names any: the nearest alternatives are the
        times closest to them."""
        if self.days:
            return self.days
        if self.week_of is not None:
            return (self.week_of,)
        return ()


class TimeRequest(BaseModel):
    """What the candidate's words say about the offered times beyond a single pick: several
    offered slots fit them (`matches`: ask which), they named a clock time none has
    (`unmatched_time`: ask again, never book another), or they asked for other times
    (`query`: look them up)."""

    model_config = ConfigDict(frozen=True)

    matches: tuple[str, ...] = ()
    unmatched_time: bool = False
    query: SlotQuery | None = None


class Slot(BaseModel):
    """An offered interview time. `startsAt` is ISO 8601 in the location's local time, with
    its UTC offset; `label` says day and time, `time` only the time."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    id: str
    starts_at: str = Field(alias="startsAt")
    label: str
    location: str
    time: str | None = None


class Booked(BaseModel):
    """The booked interview, as the chat shows it under the confirmation.

    `mode` video: the interview is a Google Meet call, `meetUrl` its link once the calendar
    event exists (None without a calendar: the invitation brings it). `address` is still the
    location's, for the record."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    kind: Literal["booking"] = "booking"
    slot_id: str = Field(alias="slotId")
    starts_at: str = Field(alias="startsAt")
    label: str
    location: str
    address: str
    mode: InterviewMode | None = None
    meet_url: str | None = Field(default=None, alias="meetUrl")
