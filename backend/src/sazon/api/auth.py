"""Recruiter sign-in: email and password, sign-out, the current recruiter, and a development
sign-in by email alone (never in production).

Failures say only "email o contraseña incorrectos", whatever went wrong (unknown email, wrong
password, deactivated account, no password set), and take about as long. Audit lines carry a
keyed hash of the email and the IP, never the address or the password.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from sazon import recruiters
from sazon.api.deps import (
    CurrentRecruiter,
    RepoDep,
    SessionsDep,
    caller_ip,
    clear_session_cookie,
    same_origin,
    spend,
    start_session,
)
from sazon.api.google_auth import calendar_out
from sazon.api.schemas import CalendarOut, DevLoginIn, ErrorOut, LoginIn, MeOut
from sazon.auth import hash_password, needs_rehash, normalize_email, verify_password
from sazon.calendar.oauth import credential_of
from sazon.config import get_settings
from sazon.observability import record
from sazon.storage.models import Recruiter
from sazon.storage.repo import Repo

logger = logging.getLogger(__name__)

BAD_CREDENTIALS = "Email o contraseña incorrectos."
LOGIN_RATE_LIMITED = "Demasiados intentos. Espera unos minutos e inténtalo de nuevo."

router = APIRouter(
    prefix="/api",
    tags=["auth"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 429: {"model": ErrorOut}},
)


def me_out(recruiter: Recruiter, calendar: CalendarOut | None = None) -> MeOut:
    return MeOut(
        calendar=calendar or CalendarOut(),
        id=recruiter.id,
        name=recruiter.name,
        email=recruiter.email,
        is_admin=recruiter.is_admin,
        country="MX" if recruiter.country == "MX" else "ES",
        timezone=recruiter.timezone,
    )


@router.post("/auth/login", response_model=MeOut, dependencies=[Depends(same_origin)])
async def login(
    body: LoginIn, request: Request, response: Response, repo: RepoDep, sessions: SessionsDep
) -> MeOut:
    """Sign in with email and password; sets the session cookie. Limited per IP and per email
    address (429). Any failure is the same 401."""
    ip = caller_ip(request)
    email = normalize_email(body.email)
    who = sessions.pseudonym(email)
    spend(request, "login_ip_limit", ip, LOGIN_RATE_LIMITED)
    spend(request, "login_email_limit", who, LOGIN_RATE_LIMITED)
    recruiter = await asyncio.to_thread(recruiters.by_email, repo, email)
    password_hash = recruiter.password_hash if recruiter else None
    valid = await asyncio.to_thread(verify_password, password_hash, body.password)
    if recruiter is None or not valid or not recruiter.active:
        record("login_failed", who=who, ip=sessions.pseudonym(ip))
        raise HTTPException(401, BAD_CREDENTIALS)
    if password_hash and needs_rehash(password_hash):
        await asyncio.to_thread(_rehash, repo, recruiter.id, body.password)
    record("login_succeeded", recruiter_id=recruiter.id, ip=sessions.pseudonym(ip))
    start_session(response, sessions, recruiter)
    return me_out(recruiter)


def _rehash(repo: Repo, recruiter_id: str, password: str) -> None:
    with repo.session() as session:
        row = session.get(Recruiter, recruiter_id)
        if row is not None:
            row.password_hash = hash_password(password)
            session.add(row)
            session.commit()


@router.post(
    "/auth/logout", status_code=204, dependencies=[Depends(same_origin)], response_class=Response
)
async def logout() -> Response:
    """End the session on this browser. Signing out without a session is fine."""
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.post(
    "/auth/dev-login",
    response_model=MeOut,
    dependencies=[Depends(same_origin)],
    include_in_schema=False,
)
async def dev_login(
    body: DevLoginIn, request: Request, response: Response, repo: RepoDep, sessions: SessionsDep
) -> MeOut:
    """Development and end-to-end tests only: sign in as an active recruiter by email. 404 in
    production."""
    if get_settings().production:
        raise HTTPException(404, "Not Found")
    recruiter = recruiters.by_email(repo, body.email)
    if recruiter is None or not recruiter.active:
        raise HTTPException(401, BAD_CREDENTIALS)
    record("login_succeeded", recruiter_id=recruiter.id, via="dev")
    start_session(response, sessions, recruiter)
    return me_out(recruiter)


@router.get("/me", response_model=MeOut)
async def me(recruiter: CurrentRecruiter, repo: RepoDep) -> MeOut:
    """The signed-in recruiter and their calendar connection; 401 without a session."""
    return me_out(
        recruiter, calendar_out(await asyncio.to_thread(credential_of, repo, recruiter.id))
    )
