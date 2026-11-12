"""Retention and erasure.

Applications are deleted 12 months after the candidate's last message (`RETENTION_DAYS`), as
the privacy notice promises; run daily with `sazon purge`. Talent-pool candidates are purged on
the same clock. A candidate who asks to be deleted
is erased at once with `sazon erase` or `DELETE /api/candidates/{id}`.

Both delete the interviews' calendar events too (when a calendar is given), as the privacy
notice promises: upcoming ones with a cancellation to the candidate, past ones quietly. The
events go first, so a crash in between leaves the rows to try again; an event the calendar
refuses to delete is counted in `events_failed` and logged with its id and recruiter (no
personal data) to be removed by hand, since the rows that could retry it are gone."""

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, col, delete, or_, select

from sazon.calendar import CalendarProvider
from sazon.engine.validators import Valid, validate_phone
from sazon.observability import record
from sazon.storage.models import (
    Booking,
    BookingStatus,
    Candidate,
    CandidateReview,
    Conversation,
    FaqMiss,
    InterviewSlot,
    Summary,
    Turn,
)
from sazon.storage.repo import Repo, to_utc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PurgeReport:
    candidates: int
    conversations: int
    events_deleted: int = 0
    events_failed: int = 0


def purge(
    repo: Repo,
    now: datetime,
    days: int,
    *,
    dry_run: bool = False,
    calendar: CalendarProvider | None = None,
) -> PurgeReport:
    """Delete every candidate whose conversations all went quiet before `now - days`, and every
    conversation without a candidate that did, with their turns, summaries and bookings."""
    cutoff = to_utc(now) - timedelta(days=days)
    with repo.session() as session:
        last_seen = (
            select(Conversation.candidate_id, func.max(Conversation.last_activity_at).label("seen"))
            .where(col(Conversation.candidate_id).is_not(None))
            .group_by(col(Conversation.candidate_id))
            .subquery()
        )
        candidate_ids = list(
            session.exec(
                select(Candidate.id)
                .join(last_seen, col(last_seen.c.candidate_id) == col(Candidate.id), isouter=True)
                .where(col(Candidate.updated_at) < cutoff)
                .where((last_seen.c.seen < cutoff) | (last_seen.c.seen.is_(None)))
            ).all()
        )
        conversation_ids = list(
            session.exec(
                select(Conversation.id).where(
                    (col(Conversation.candidate_id).in_(candidate_ids))
                    | (
                        col(Conversation.candidate_id).is_(None)
                        & (col(Conversation.last_activity_at) < cutoff)
                    )
                )
            ).all()
        )
        report = PurgeReport(candidates=len(candidate_ids), conversations=len(conversation_ids))
        if dry_run:
            return report
        events = _with_events(session, candidate_ids, conversation_ids)
        deleted, failed = _delete_events(calendar, events, now)
        _delete(session, candidate_ids, conversation_ids)
        session.commit()
    return replace(report, events_deleted=deleted, events_failed=failed)


@dataclass(frozen=True)
class EraseReport:
    candidate_id: str
    conversations: int
    bookings: int
    slots_freed: int
    """Active bookings among them: the recruiter is free again at that time."""
    events_deleted: int = 0
    events_failed: int = 0


def find_candidates(repo: Repo, *, phone: str | None = None, email: str | None = None) -> list[str]:
    """Ids of every application with this phone (any format) or email (any case)."""
    matches = []
    if phone:
        normalized = validate_phone(phone)
        matches.append(
            col(Candidate.phone) == (normalized.value if isinstance(normalized, Valid) else phone)
        )
    if email:
        matches.append(func.lower(col(Candidate.email)) == email.strip().lower())
    if not matches:
        return []
    with repo.session() as session:
        return list(session.exec(select(Candidate.id).where(or_(*matches))).all())


def erase(
    repo: Repo,
    candidate_id: str,
    *,
    via: str,
    calendar: CalendarProvider | None = None,
    now: datetime | None = None,
) -> EraseReport | None:
    """Delete one candidate and everything about them: conversations, turns (nudges and
    retrieval sources included), summaries, FAQ misses, the review and bookings, freeing the
    slot of an active booking. None when there is no such candidate.

    Writes an audit event with the candidate's random id and counts, no personal data."""
    with repo.session() as session:
        if session.get(Candidate, candidate_id) is None:
            return None
        conversation_ids = list(
            session.exec(
                select(Conversation.id).where(col(Conversation.candidate_id) == candidate_id)
            ).all()
        )
        bookings = session.exec(
            select(Booking).where(
                (col(Booking.candidate_id) == candidate_id)
                | col(Booking.conversation_id).in_(conversation_ids)
            )
        ).all()
        freed = sum(booking.status == BookingStatus.BOOKED for booking in bookings)
        events = _with_events(session, [candidate_id], conversation_ids)
        deleted, failed = _delete_events(calendar, events, now or datetime.now(UTC))
        _delete(session, [candidate_id], conversation_ids)
        session.commit()
    report = EraseReport(
        candidate_id=candidate_id,
        conversations=len(conversation_ids),
        bookings=len(bookings),
        slots_freed=freed,
        events_deleted=deleted,
        events_failed=failed,
    )
    record(
        "candidate_erased",
        via=via,
        candidate_id=candidate_id,
        conversations=report.conversations,
        bookings=report.bookings,
        slots_freed=report.slots_freed,
        events_deleted=report.events_deleted,
        events_failed=report.events_failed,
    )
    return report


def _delete(session: Session, candidate_ids: list[str], conversation_ids: list[str]) -> None:
    for model in (Turn, FaqMiss, Summary):
        session.exec(delete(model).where(col(model.conversation_id).in_(conversation_ids)))
    session.exec(
        delete(Booking).where(
            col(Booking.conversation_id).in_(conversation_ids)
            | col(Booking.candidate_id).in_(candidate_ids)
        )
    )
    session.exec(
        delete(CandidateReview).where(col(CandidateReview.candidate_id).in_(candidate_ids))
    )
    session.exec(delete(Conversation).where(col(Conversation.id).in_(conversation_ids)))
    session.exec(delete(Candidate).where(col(Candidate.id).in_(candidate_ids)))


def _with_events(
    session: Session, candidate_ids: list[str], conversation_ids: list[str]
) -> list[tuple[Booking, datetime | None]]:
    """The bookings about to be deleted that have a calendar event, with their start."""
    rows = session.exec(
        select(Booking, InterviewSlot.starts_at)
        .join(InterviewSlot, col(InterviewSlot.id) == col(Booking.slot_id), isouter=True)
        .where(col(Booking.google_event_id).is_not(None))
        .where(
            col(Booking.candidate_id).in_(candidate_ids)
            | col(Booking.conversation_id).in_(conversation_ids)
        )
    ).all()
    return [(booking, starts_at) for booking, starts_at in rows]


def _delete_events(
    calendar: CalendarProvider | None,
    events: list[tuple[Booking, datetime | None]],
    now: datetime,
) -> tuple[int, int]:
    """Delete the events first; (deleted, failed). Without a calendar there is nothing to do."""
    from sazon.scheduling.service import delete_events

    if calendar is None:
        return 0, 0
    left = delete_events(calendar, events, now)
    for booking in left:
        logger.error(
            "Calendar event %s of recruiter %s left behind: delete it by hand",
            booking.google_event_id,
            booking.recruiter_id,
        )
    return len(events) - len(left), len(left)
