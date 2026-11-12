"""The questions Ana could not answer, grouped for recruiters to grow the FAQ.

Every `FaqMiss` (redacted, rewritten to stand alone) asked in the date range, by its own day
(UTC, both ends included), language and country, in conversations of the recruiter's scope:
the candidates at their locations, every conversation for an admin. At most the latest
`MAX_MISSES`; grouping is in `sazon.rag.misses`.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import col, select

from sazon.api.analytics import Filters, day_start
from sazon.api.deps import CurrentRecruiter, RepoDep, Service, require_recruiter
from sazon.engine.models import Language
from sazon.engine.validators import Country
from sazon.llm.interface import Channel
from sazon.rag.index import Embedder, RagIndex
from sazon.rag.misses import Group, Question, group_questions
from sazon.recruiters import in_scope, location_scope
from sazon.storage.models import Candidate, Conversation, FaqMiss
from sazon.storage.repo import Repo, to_utc

router = APIRouter(
    prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_recruiter)]
)

MAX_MISSES = 2000
"""The latest misses read per request; older ones in the range are left out (`truncated`)."""
MAX_EXAMPLES = 3
CSV_FIELDS = (
    "group",
    "group_question",
    "question",
    "asked_at",
    "country",
    "language",
    "conversation_id",
    "candidate_id",
)


class Example(BaseModel):
    conversation_id: str
    candidate_id: str | None


class MissGroup(BaseModel):
    id: int = Field(description="1 for the largest group; the CSV export's `group`")
    question: str = Field(description="The most frequent wording in the group")
    count: int
    last_seen: datetime
    countries: list[str]
    languages: list[str]
    examples: list[Example] = Field(description=f"The latest {MAX_EXAMPLES} conversations")


class FaqMissesOut(BaseModel):
    total: int = Field(description="Misses read, at most MAX_MISSES")
    truncated: bool = Field(description="More misses in the range than were read")
    by_similarity: bool = Field(
        description="Grouped by meaning with the retrieval model; false: by exact text, when "
        "the model is not available"
    )
    groups: list[MissGroup]


@dataclass(frozen=True)
class Miss:
    row: FaqMiss
    candidate_id: str | None

    @property
    def question(self) -> Question:
        return Question(self.row.id or 0, self.row.question, to_utc(self.row.created_at))


@router.get("/faq-misses", response_model=FaqMissesOut)
def get_faq_misses(
    repo: RepoDep,
    service: Service,
    me: CurrentRecruiter,
    start: Annotated[date | None, Query(alias="from", description="First day, UTC")] = None,
    end: Annotated[date | None, Query(alias="to", description="Last day, UTC")] = None,
    channel: Channel | None = None,
    country: Country | None = None,
    language: Language | None = None,
) -> FaqMissesOut:
    """Unanswered questions grouped by meaning, the most asked first."""
    filters = Filters(start, end, channel, country, language, location_scope(me))
    misses, truncated = read_misses(repo, filters)
    groups, similar = group_misses(misses, _embedder(service.faq))
    return FaqMissesOut(
        total=len(misses),
        truncated=truncated,
        by_similarity=similar,
        groups=[_group_out(n, group, misses) for n, group in enumerate(groups, 1)],
    )


@router.get("/faq-misses.csv", response_class=Response)
def export_faq_misses(
    repo: RepoDep,
    service: Service,
    me: CurrentRecruiter,
    start: Annotated[date | None, Query(alias="from", description="First day, UTC")] = None,
    end: Annotated[date | None, Query(alias="to", description="Last day, UTC")] = None,
    channel: Channel | None = None,
    country: Country | None = None,
    language: Language | None = None,
) -> Response:
    """The same misses, one row each with its group, the largest group first."""
    filters = Filters(start, end, channel, country, language, location_scope(me))
    misses, _ = read_misses(repo, filters)
    groups, _ = group_misses(misses, _embedder(service.faq))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_FIELDS)
    for n, group in enumerate(groups, 1):
        for question in sorted(group.members, key=lambda q: q.created_at, reverse=True):
            miss = misses[question.id]
            writer.writerow(
                [
                    n,
                    _cell(group.representative),
                    _cell(question.text),
                    question.created_at.isoformat(),
                    miss.row.country,
                    miss.row.language,
                    miss.row.conversation_id,
                    miss.candidate_id or "",
                ]
            )
    return Response(
        # A BOM so spreadsheet apps read the accents as UTF-8.
        "﻿" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="preguntas-sin-respuesta.csv"'},
    )


def read_misses(repo: Repo, filters: Filters) -> tuple[dict[int, Miss], bool]:
    """The latest misses matching `filters`, by id, and whether more were left out."""
    query = select(FaqMiss, Conversation.candidate_id).join(
        Conversation, col(Conversation.id) == col(FaqMiss.conversation_id)
    )
    if filters.start is not None:
        query = query.where(col(FaqMiss.created_at) >= day_start(filters.start))
    if filters.end is not None:
        query = query.where(col(FaqMiss.created_at) < day_start(filters.end + timedelta(days=1)))
    if filters.channel is not None:
        query = query.where(col(Conversation.channel) == filters.channel)
    if filters.country is not None:
        query = query.where(col(FaqMiss.country) == filters.country)
    if filters.language is not None:
        query = query.where(col(FaqMiss.language) == filters.language.value)
    if filters.location_ids is not None:
        query = query.join(Candidate, col(Candidate.id) == col(Conversation.candidate_id)).where(
            in_scope(filters.location_ids)
        )
    query = query.order_by(col(FaqMiss.created_at).desc(), col(FaqMiss.id).desc())
    with repo.session() as session:
        rows = session.exec(query.limit(MAX_MISSES + 1)).all()
    misses = {row.id or 0: Miss(row, candidate_id) for row, candidate_id in rows[:MAX_MISSES]}
    return misses, len(rows) > MAX_MISSES


def group_misses(misses: dict[int, Miss], embedder: Embedder | None) -> tuple[list[Group], bool]:
    return group_questions([miss.question for miss in misses.values()], embedder)


def _embedder(faq: object) -> Embedder | None:
    """The retrieval model, when the service's index has it loaded (None: group by text)."""
    if isinstance(faq, RagIndex) and faq.vectors() is not None:
        return faq.embedder
    return None


def _group_out(n: int, group: Group, misses: dict[int, Miss]) -> MissGroup:
    latest = sorted(group.members, key=lambda q: q.created_at, reverse=True)
    rows = [misses[q.id] for q in latest]
    examples: list[Example] = []
    for miss in rows:
        if len(examples) == MAX_EXAMPLES:
            break
        if all(e.conversation_id != miss.row.conversation_id for e in examples):
            examples.append(
                Example(conversation_id=miss.row.conversation_id, candidate_id=miss.candidate_id)
            )
    return MissGroup(
        id=n,
        question=group.representative,
        count=len(group.members),
        last_seen=latest[0].created_at,
        countries=sorted({miss.row.country for miss in rows}),
        languages=sorted({miss.row.language for miss in rows}),
        examples=examples,
    )


def _cell(text: str) -> str:
    """A spreadsheet reads a cell starting with = + - @ as a formula; a quote keeps it text."""
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
