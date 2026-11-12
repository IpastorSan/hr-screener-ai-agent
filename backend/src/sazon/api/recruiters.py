"""The hiring team: every signed-in recruiter can list it; admins add, edit and deactivate
recruiters. There is no self sign-up and no hard delete: bookings keep their recruiter."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from sazon import recruiters
from sazon.api.auth import me_out
from sazon.api.deps import Admin, Now, RepoDep, require_recruiter
from sazon.api.schemas import CalendarOut, ErrorOut, RecruiterCreate, RecruiterOut, RecruiterPatch
from sazon.calendar.oauth import connection_status
from sazon.storage.models import InterviewMode, Recruiter

router = APIRouter(
    prefix="/api/recruiters",
    tags=["recruiter"],
    dependencies=[Depends(require_recruiter)],
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)

EMAIL_TAKEN = "Ya existe un reclutador con ese email."
NOT_FOUND = "Reclutador no encontrado."
SELF_LOCKOUT = "No puedes quitarte el acceso ni el rol de administrador a ti mismo."


def recruiter_out(recruiter: Recruiter, calendar: str | None = None) -> RecruiterOut:
    """`calendar`: the recruiter's grant status (`connection_status`), None without one."""
    status = calendar if calendar in ("connected", "revoked") else "disconnected"
    return RecruiterOut(
        **me_out(recruiter, CalendarOut(status=status)).model_dump(),  # pyright: ignore[reportArgumentType]
        active=recruiter.active,
        location_ids=recruiter.location_ids,
        interview_modes=[InterviewMode(mode) for mode in recruiter.interview_modes],
        working_hours={
            day: [(start, end) for start, end in blocks]
            for day, blocks in recruiter.working_hours.items()
        },
        max_interviews_per_day=recruiter.max_interviews_per_day,
        scope=recruiter.scope,
        has_password=bool(recruiter.password_hash),
        created_at=recruiter.created_at,
        updated_at=recruiter.updated_at,
    )


@router.get("", response_model=list[RecruiterOut])
async def list_recruiters(repo: RepoDep) -> list[RecruiterOut]:
    """Every recruiter, active or not, by name, with their calendar connection."""
    calendars = await asyncio.to_thread(connection_status, repo)
    return [recruiter_out(r, calendars.get(r.id)) for r in recruiters.all_recruiters(repo)]


@router.post("", response_model=RecruiterOut, status_code=201)
async def create_recruiter(
    body: RecruiterCreate, repo: RepoDep, now: Now, _admin: Admin
) -> RecruiterOut:
    """Admins only. Without a password the recruiter cannot sign in until one is set."""
    hours = body.working_hours
    try:
        created = await asyncio.to_thread(
            recruiters.create,
            repo,
            now,
            name=body.name,
            email=body.email,
            country=body.country,
            password=body.password,
            is_admin=bool(body.is_admin),
            timezone=body.timezone,
            location_ids=body.location_ids,
            interview_modes=body.interview_modes,
            working_hours=_hours(hours) if hours is not None else None,
            max_interviews_per_day=body.max_interviews_per_day or recruiters.MAX_PER_DAY,
            scope=body.scope,
        )
    except ValueError as exc:
        raise HTTPException(409, EMAIL_TAKEN) from exc
    return recruiter_out(created)


@router.patch("/{recruiter_id}", response_model=RecruiterOut)
async def update_recruiter(
    recruiter_id: str, body: RecruiterPatch, repo: RepoDep, now: Now, admin: Admin
) -> RecruiterOut:
    """Admins only. A new password or a deactivation signs the recruiter out everywhere. An
    admin cannot deactivate themselves or drop their own admin role."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if recruiter_id == admin.id and (
        changes.get("active") is False or changes.get("is_admin") is False
    ):
        raise HTTPException(409, SELF_LOCKOUT)
    if "working_hours" in changes:
        changes["working_hours"] = _hours(changes["working_hours"])
    if "interview_modes" in changes:
        changes["interview_modes"] = [str(mode) for mode in changes["interview_modes"]]
    try:
        updated = await asyncio.to_thread(recruiters.update, repo, recruiter_id, now, **changes)
    except LookupError as exc:
        raise HTTPException(404, NOT_FOUND) from exc
    return recruiter_out(updated)


@router.delete("/{recruiter_id}", response_model=RecruiterOut)
async def deactivate_recruiter(
    recruiter_id: str, repo: RepoDep, now: Now, admin: Admin
) -> RecruiterOut:
    """Admins only: deactivate (the account and its bookings stay; it can no longer sign in or
    be assigned interviews)."""
    if recruiter_id == admin.id:
        raise HTTPException(409, SELF_LOCKOUT)
    try:
        updated = recruiters.update(repo, recruiter_id, now, active=False)
    except LookupError as exc:
        raise HTTPException(404, NOT_FOUND) from exc
    return recruiter_out(updated)


def _hours(hours: dict[str, list[tuple[str, str]]]) -> dict[str, list[list[str]]]:
    return {day: [[start, end] for start, end in blocks] for day, blocks in hours.items()}
