"""Recruiter dashboard: the candidate queue, a candidate's detail, review status, export and
erasure.

A recruiter sees the candidates at their locations (`recruiters.location_scope`; a candidate with
no location yet is placed at their posting's, or in their country, `recruiters.in_scope`) and
those whose video interview is assigned to them; an admin sees every candidate. A candidate
outside the scope is not found (404), so its id does not leak."""

import asyncio
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter
from sqlalchemy import ColumnElement, or_
from sqlmodel import Session, col, func, select

from sazon import recruiters
from sazon.api.candidate import UI
from sazon.api.deps import Admin, CurrentRecruiter, Now, RepoDep, Service, require_recruiter
from sazon.api.schemas import (
    BookingOut,
    CandidateDetail,
    CandidateItem,
    CandidatePage,
    CandidatePatch,
    ContextIn,
    ConversationOut,
    ErrorOut,
    Note,
    ProfileExport,
    ScreeningExport,
    SortKey,
    SourceOut,
    StatusCounts,
    TurnOut,
)
from sazon.engine import ConversationState
from sazon.engine.models import (
    Availability,
    CandidateProfile,
    Language,
    QualityFlag,
    Schedule,
    Sentiment,
)
from sazon.engine.service_areas import get_location
from sazon.engine.text import fold
from sazon.engine.validators import Country
from sazon.llm.interface import CandidateSummary, Channel
from sazon.recruiters import in_scope, location_scope
from sazon.reengagement.idle import abandoned_since, effective_state, effective_status
from sazon.scheduling import service as scheduling
from sazon.service import profile_export, utc_now
from sazon.storage.models import (
    Booking,
    BookingStatus,
    Candidate,
    CandidateReview,
    CandidateStatus,
    Conversation,
    InterviewMode,
    InterviewSlot,
    Recruiter,
    ReviewStatus,
)
from sazon.storage.repo import Repo, to_utc
from sazon.storage.retention import erase

router = APIRouter(
    prefix="/api/candidates",
    tags=["recruiter"],
    dependencies=[Depends(require_recruiter)],
    responses={404: {"model": ErrorOut}},
)

NOTES = TypeAdapter(list[Note])
MAX_PAGE_SIZE = 100


def _fits_schedule(blocks: Sequence[Schedule] | None, wanted: Schedule) -> bool:
    """Any of the candidate's blocks; `flexible` fits every block."""
    return bool(blocks) and (wanted in blocks or Schedule.FLEXIBLE in blocks)


@router.get("", response_model=CandidatePage)
async def list_candidates(
    repo: RepoDep,
    now: Now,
    me: CurrentRecruiter,
    status: CandidateStatus | None = None,
    review_status: ReviewStatus | None = None,
    location_id: str | None = None,
    country: Country | None = None,
    availability: Availability | None = None,
    schedule: Schedule | None = None,
    sentiment: Sentiment | None = None,
    quality_flag: QualityFlag | None = None,
    talent_pool: Annotated[
        bool | None, Query(description="Only those in the talent pool (bolsa de candidatos)")
    ] = None,
    q: Annotated[str | None, Query(max_length=100, description="Name, email or phone")] = None,
    recruiter_id: Annotated[
        str | None,
        Query(max_length=64, description="Assigned interviewer: a recruiter id, or `me`"),
    ] = None,
    sort: SortKey = "-last_activity",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> CandidatePage:
    """The candidate queue, filtered, sorted (`-` prefix for descending) and paginated.

    `counts` has the totals per screening status for the other filters, for the status tabs.
    A candidate in progress but idle for more than 72 hours counts as abandoned.
    `recruiter_id` keeps the candidates whose booked interview is assigned to that recruiter
    (`me` for the signed-in one: "Mis entrevistas").
    """
    query = select(Candidate)
    if (visible := _visible(me)) is not None:
        query = query.where(visible)
    if location_id is not None:
        query = query.where(col(Candidate.location_id) == location_id)
    if country is not None:
        query = query.where(col(Candidate.country) == country)
    if sentiment is not None:
        query = query.where(col(Candidate.sentiment) == sentiment)
    if talent_pool is not None:
        query = query.where(col(Candidate.talent_pool) == talent_pool)
    with repo.session() as session:
        candidates = session.exec(query).all()
        reviews = {review.candidate_id: review for review in session.exec(select(CandidateReview))}
        activity = _last_activity(session)

    interviews = scheduling.active_bookings(repo, [c.id for c in candidates])
    team = recruiters.names(repo)
    items = [
        candidate_item(
            c, reviews.get(c.id), activity.get(c.id), interviews.get(c.id), now, team=team
        )
        for c in candidates
    ]
    owner = me.id if recruiter_id == "me" else recruiter_id
    items = [
        item
        for item in items
        if (review_status is None or item.review_status == review_status)
        and (availability is None or availability in (item.availability or ()))
        and (schedule is None or _fits_schedule(item.schedule, schedule))
        and (quality_flag is None or quality_flag in item.quality_flags)
        and (not q or _matches(item, q))
        and (owner is None or item.recruiter_id == owner)
    ]
    by_status = Counter(item.status for item in items)
    counts = StatusCounts(all=len(items), **{s.value: by_status[s] for s in CandidateStatus})
    if status is not None:
        items = [item for item in items if item.status == status]
    _sort(items, sort)
    start = (page - 1) * page_size
    return CandidatePage(
        items=items[start : start + page_size],
        total=len(items),
        page=page,
        page_size=page_size,
        counts=counts,
    )


@router.get("/{candidate_id}", response_model=CandidateDetail)
async def get_candidate(
    candidate_id: str, repo: RepoDep, now: Now, me: CurrentRecruiter
) -> CandidateDetail:
    """Profile, summary, recruiter notes and every conversation with its turns, including the
    extraction behind each candidate turn and the engine action behind each agent turn."""
    return candidate_detail(repo, _candidate(repo, candidate_id, me), now)


@router.patch("/{candidate_id}", response_model=CandidateDetail)
async def update_candidate(
    candidate_id: str, body: CandidatePatch, repo: RepoDep, now: Now, me: CurrentRecruiter
) -> CandidateDetail:
    """Set the recruiter's review status and/or append a note."""
    candidate = _candidate(repo, candidate_id, me)
    with repo.session() as session:
        review = session.get(CandidateReview, candidate_id) or CandidateReview(
            candidate_id=candidate_id, updated_at=now
        )
        if body.status is not None:
            review.status = body.status
        if body.note is not None:
            note = Note(text=body.note, created_at=now)
            review.notes = [*review.notes, note.model_dump(mode="json")]
        review.updated_at = now
        session.add(review)
        session.commit()
    return candidate_detail(repo, candidate, now)


@router.delete("/{candidate_id}", status_code=204, responses={403: {"model": ErrorOut}})
async def erase_candidate(candidate_id: str, service: Service, now: Now, _admin: Admin) -> None:
    """Erase a candidate on request (GDPR Art. 17): the application, its conversations and
    bookings, and the interviews' calendar events. Admins only: erasure cannot be undone.
    Logged as `candidate_erased` without personal data."""
    erased = await asyncio.to_thread(
        erase,
        service.repo,
        candidate_id,
        via="api",
        calendar=service.scheduling.calendar,
        now=now,
    )
    if erased is None:
        raise HTTPException(404, "Candidate not found.")


@router.get("/{candidate_id}/export", response_model=ScreeningExport)
async def export_candidate(
    candidate_id: str, repo: RepoDep, now: Now, me: CurrentRecruiter
) -> ScreeningExport:
    """The screening as JSON for the ATS: every screening field plus phone and email."""
    candidate = _candidate(repo, candidate_id, me)
    detail = candidate_detail(repo, candidate, now)
    state = ConversationState(profile=detail.profile)
    return ScreeningExport(
        candidate_id=candidate.id,
        status=detail.status,
        review_status=detail.review_status,
        outcome_reason=detail.outcome_reason,
        country=detail.country,
        language=detail.language,
        sentiment=detail.sentiment,
        quality_flags=detail.quality_flags,
        profile=ProfileExport.model_validate(profile_export(state)),
        summary=detail.summary,
        notes=detail.notes,
        conversations=detail.conversations,
        exported_at=now,
    )


# --- building responses ---


def candidate_item(
    candidate: Candidate,
    review: CandidateReview | None,
    last_activity: datetime | None = None,
    booked: tuple[Booking, InterviewSlot] | None = None,
    now: datetime | None = None,
    *,
    team: dict[str, str] | None = None,
) -> CandidateItem:
    """`now` works out abandonment at read time: in progress but idle past the limit. `team`
    maps recruiter ids to names."""
    booking, interview = booked if booked else (None, None)
    runs = scheduling.who_runs(booking, interview) if booking and interview else None
    recruiter_id, manager = runs if runs else (None, None)
    profile = CandidateProfile.model_validate(candidate.profile)
    location = get_location(candidate.location_id) if candidate.location_id else None
    last = last_activity or candidate.updated_at
    status = effective_status(candidate.status, last, now or utc_now())
    closed_at = candidate.closed_at
    if status != candidate.status:
        closed_at = abandoned_since(last)
    return CandidateItem(
        id=candidate.id,
        name=profile.full_name,
        phone=candidate.phone,
        email=candidate.email,
        status=status,
        review_status=ReviewStatus(review.status) if review else ReviewStatus.NEW,
        outcome_reason=candidate.outcome_reason,
        location_id=candidate.location_id,
        location_name=location.name if location else None,
        city=location.city if location else None,
        country=_country(candidate.country),
        language=Language(candidate.language) if candidate.language else None,
        sentiment=Sentiment(candidate.sentiment) if candidate.sentiment else None,
        channel=Channel(candidate.channel),
        availability=profile.availability,
        schedule=profile.schedule,
        experience_years=profile.experience_years,
        platforms=profile.platforms,
        start_date=profile.start_date,
        late_start_days=profile.late_start_days,
        quality_flags=[QualityFlag(flag) for flag in candidate.quality_flags],
        talent_pool=candidate.talent_pool,
        created_at=to_utc(candidate.created_at),
        updated_at=to_utc(candidate.updated_at),
        closed_at=to_utc(closed_at) if closed_at else None,
        last_activity_at=to_utc(last),
        interview_at=to_utc(interview.starts_at) if interview else None,
        interview_local_start=scheduling.local_start(interview).isoformat() if interview else None,
        recruiter_id=recruiter_id,
        recruiter_name=(team or {}).get(recruiter_id) if recruiter_id else None,
        interviewer_name=manager.name or None if manager else None,
        interviewer_email=manager.email if manager else None,
        interview_mode=InterviewMode(booking.mode) if booking and booking.mode else None,
    )


def candidate_detail(
    repo: Repo, candidate: Candidate, now: datetime | None = None
) -> CandidateDetail:
    with repo.session() as session:
        review = session.get(CandidateReview, candidate.id)
        conversations = session.exec(
            select(Conversation)
            .where(col(Conversation.candidate_id) == candidate.id)
            .order_by(col(Conversation.started_at))
        ).all()
    last_activity = max((c.last_activity_at for c in conversations), default=None)
    booked = scheduling.active_bookings(repo, [candidate.id]).get(candidate.id)
    booking, slot = booked if booked else (None, None)
    now = now or utc_now()
    team = recruiters.names(repo)
    item = candidate_item(candidate, review, last_activity, booked, now, team=team)
    summary = None
    for conversation in reversed(conversations):
        row = repo.summary(conversation.id)
        if row is not None:
            summary = CandidateSummary.model_validate(row.body)
            break
    return CandidateDetail(
        **item.model_dump(),
        booking=_booking_out(slot, booking, item) if slot and booking else None,
        profile=CandidateProfile.model_validate(candidate.profile),
        summary=summary,
        notes=NOTES.validate_python(review.notes) if review else [],
        conversations=[_conversation_out(repo, c, now) for c in conversations],
    )


def _booking_out(slot: InterviewSlot, booking: Booking, item: CandidateItem) -> BookingOut:
    location = get_location(slot.location_id)
    return BookingOut(
        slot_id=slot.id,
        starts_at=to_utc(slot.starts_at),
        ends_at=to_utc(slot.ends_at),
        local_start=scheduling.local_start(slot).isoformat(),
        location_id=slot.location_id,
        location_name=location.name if location else None,
        address=location.address if location else None,
        booked_at=to_utc(booking.created_at),
        mode=InterviewMode(booking.mode) if booking.mode else None,
        meet_url=booking.meet_url,
        recruiter_id=item.recruiter_id,
        recruiter_name=item.recruiter_name,
        interviewer_name=item.interviewer_name,
        interviewer_email=item.interviewer_email,
    )


def _conversation_out(repo: Repo, conversation: Conversation, now: datetime) -> ConversationOut:
    state = effective_state(conversation, now)
    ended_at = conversation.ended_at
    if ended_at is None and state.outcome is not None and conversation.outcome is None:
        ended_at = abandoned_since(conversation.last_activity_at)
    turns = [
        TurnOut(
            id=turn.id or 0,
            role=turn.role,  # pyright: ignore[reportArgumentType]
            text=turn.text,
            ui=UI.validate_python(turn.ui),
            extraction=turn.extraction,
            action=turn.action,
            model=turn.model,
            latency_ms=turn.latency_ms,
            cost_usd=turn.cost_usd,
            fallback_used=turn.fallback_used,
            nudge=turn.nudge,
            sources=[SourceOut.model_validate(source) for source in turn.sources or ()],
            created_at=to_utc(turn.created_at),
        )
        for turn in repo.turns(conversation.id)
    ]
    return ConversationOut(
        id=conversation.id,
        channel=Channel(conversation.channel),
        stage=state.stage,
        outcome=state.outcome,
        disqualify_reason=state.disqualify_reason,
        drop_off_stage=state.drop_off_stage,
        quality_flags=list(state.quality_flags),
        context=ContextIn.model_validate(conversation.context),
        started_at=to_utc(conversation.started_at),
        ended_at=to_utc(ended_at) if ended_at else None,
        last_activity_at=to_utc(conversation.last_activity_at),
        recovered_by_nudge=conversation.recovered_by_nudge,
        elevenlabs_conversation_id=conversation.elevenlabs_conversation_id,
        turns=turns,
        faq_misses=[miss.question for miss in repo.faq_misses(conversation.id)],
    )


def _last_activity(session: Session) -> dict[str, datetime]:
    """Each candidate's latest conversation activity."""
    latest = func.max(col(Conversation.last_activity_at))
    query = (
        select(col(Conversation.candidate_id), latest)
        .where(col(Conversation.candidate_id).is_not(None))
        .group_by(col(Conversation.candidate_id))
    )
    return {candidate_id: moment for candidate_id, moment in session.exec(query) if candidate_id}


def _visible(me: Recruiter) -> ColumnElement[bool] | None:
    """The candidates `me` may see: in the scope of their locations (`in_scope`), or with a
    video interview assigned to them (their locations may have changed since it was booked).
    None for an admin: all."""
    locations = location_scope(me)
    if locations is None:
        return None
    assigned = (
        select(Booking.candidate_id)
        .where(col(Booking.recruiter_id) == me.id)
        .where(col(Booking.mode) == InterviewMode.VIDEO)
        .where(col(Booking.status) == BookingStatus.BOOKED)
    )
    return or_(in_scope(locations), col(Candidate.id).in_(assigned))


def _candidate(repo: Repo, candidate_id: str, me: Recruiter) -> Candidate:
    """The candidate, or 404 when it does not exist or is outside `me`'s scope."""
    query = select(Candidate).where(col(Candidate.id) == candidate_id)
    if (visible := _visible(me)) is not None:
        query = query.where(visible)
    with repo.session() as session:
        candidate = session.exec(query).first()
    if candidate is None:
        raise HTTPException(404, "Candidate not found.")
    return candidate


def _country(value: str | None) -> Country | None:
    return "MX" if value == "MX" else "ES" if value == "ES" else None


def _matches(item: CandidateItem, q: str) -> bool:
    needle = fold(q)
    digits = "".join(c for c in q if c.isdigit())
    haystack = fold(" ".join(v for v in (item.name, item.email) if v))
    return bool(needle and needle in haystack) or bool(
        digits and item.phone and digits in item.phone
    )


SORT_KEYS: dict[str, Callable[[CandidateItem], Any]] = {
    "last_activity": lambda item: item.last_activity_at,
    "created_at": lambda item: item.created_at,
    "updated_at": lambda item: item.updated_at,
    "name": lambda item: fold(item.name) if item.name else None,
    "experience_years": lambda item: item.experience_years,
    "start_date": lambda item: item.start_date,
}


def _sort(items: list[CandidateItem], sort: str) -> None:
    """Sort in place; candidates without the value go last either way. Ties go by id, so pages
    do not swap rows between requests."""
    descending = sort.startswith("-")
    key = SORT_KEYS[sort.lstrip("-")]
    present = [item for item in items if key(item) is not None]
    missing = [item for item in items if key(item) is None]
    present.sort(key=lambda item: (key(item), item.id), reverse=descending)
    missing.sort(key=lambda item: item.id)
    items[:] = present + missing
