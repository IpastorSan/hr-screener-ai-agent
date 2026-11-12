"""A recruiter's agenda ("Mis entrevistas"): the video interviews assigned to them, and the
in-person ones at their locations, which the location's manager runs (SAZ-66)."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, false, or_
from sqlmodel import col, select

from sazon import recruiters
from sazon.api.deps import FORBIDDEN, CurrentRecruiter, Now, RepoDep
from sazon.api.schemas import InterviewItem
from sazon.engine.models import CandidateProfile
from sazon.engine.service_areas import get_location
from sazon.scheduling import service as scheduling
from sazon.storage.models import Booking, BookingStatus, Candidate, InterviewMode, InterviewSlot
from sazon.storage.repo import to_utc

router = APIRouter(prefix="/api/interviews", tags=["recruiter"])

RECENT = timedelta(hours=12)
"""How far back "upcoming" reaches, so this morning's interviews stay on the agenda."""


@router.get("", response_model=list[InterviewItem])
async def list_interviews(
    me: CurrentRecruiter,
    repo: RepoDep,
    now: Now,
    recruiter_id: Annotated[
        str, Query(max_length=64, description="`me` (default) or a recruiter id")
    ] = "me",
    include_past: bool = False,
) -> list[InterviewItem]:
    """Booked interviews on a recruiter's agenda, soonest first: from 12 hours ago on, or
    every one with `include_past`. Their video interviews, and the in-person ones at their
    locations (every location for an admin's own agenda), which they only see. Recruiters read
    their own agenda; only an admin reads another's."""
    owner = me.id if recruiter_id == "me" else recruiter_id
    if owner != me.id and not me.is_admin:
        raise HTTPException(403, FORBIDDEN)
    person = me if owner == me.id else recruiters.get(repo, owner)
    in_person = or_(col(Booking.mode) == InterviewMode.ONSITE, col(Booking.mode).is_(None))
    if person is None:
        in_person = false()
    elif not (person.is_admin and owner == me.id):
        in_person = and_(in_person, col(InterviewSlot.location_id).in_(person.location_ids))
    video = and_(col(Booking.mode) == InterviewMode.VIDEO, col(Booking.recruiter_id) == owner)
    query = (
        select(Booking, InterviewSlot, Candidate)
        .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id))
        .join(Candidate, col(Candidate.id) == col(Booking.candidate_id))
        .where(or_(video, in_person))
        .where(col(Booking.status) == BookingStatus.BOOKED)
        .order_by(col(InterviewSlot.starts_at))
    )
    if not include_past:
        query = query.where(col(InterviewSlot.starts_at) >= now - RECENT)
    with repo.session() as session:
        rows = session.exec(query).all()
    name = person.name if person else None
    items: list[InterviewItem] = []
    for booking, slot, candidate in rows:
        location = get_location(slot.location_id)
        runner, manager = scheduling.who_runs(booking, slot)
        items.append(
            InterviewItem(
                booking_id=booking.id,
                candidate_id=candidate.id,
                candidate_name=CandidateProfile.model_validate(candidate.profile).full_name,
                phone=candidate.phone,
                email=candidate.email,
                status=candidate.status,  # pyright: ignore[reportArgumentType]
                starts_at=to_utc(slot.starts_at),
                ends_at=to_utc(slot.ends_at),
                local_start=scheduling.local_start(slot).isoformat(),
                location_id=slot.location_id,
                location_name=location.name if location else None,
                address=location.address if location else None,
                mode=InterviewMode(booking.mode) if booking.mode else None,
                meet_url=booking.meet_url,
                recruiter_id=runner,
                recruiter_name=name if runner else None,
                interviewer_name=manager.name or None if manager else None,
                interviewer_email=manager.email if manager else None,
                calendar_issue=booking.calendar_issue,
            )
        )
    return items
