"""Shared route dependencies."""

import math
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response

from sazon.auth import COOKIE_NAME, REFRESH_AFTER_S, Sessions
from sazon.config import get_settings
from sazon.guards import SpendGuard, TokenBucket, client_ip
from sazon.observability import record
from sazon.service import ScreeningService
from sazon.storage.models import Recruiter
from sazon.storage.repo import Repo, to_utc

RATE_LIMITED = "Too many requests. Please wait a moment and try again."
SIGNED_OUT = "Inicia sesión para continuar."
FORBIDDEN = "No tienes permiso para hacer esto."
BAD_ORIGIN = "Petición rechazada: origen no permitido."
NOT_JSON = "Petición rechazada: el cuerpo debe ser JSON."
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_service(request: Request) -> ScreeningService:
    return request.app.state.service


def get_repo(service: Annotated[ScreeningService, Depends(get_service)]) -> Repo:
    return service.repo


def get_now(service: Annotated[ScreeningService, Depends(get_service)]) -> datetime:
    """The service's clock, so reads that depend on time follow it in tests."""
    return to_utc(service.clock())


def get_spend_guard(request: Request) -> SpendGuard | None:
    return getattr(request.app.state, "spend_guard", None)


def caller_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    return client_ip(request.headers.get("x-forwarded-for"), peer)


def spend(
    request: Request,
    bucket_name: str,
    key: str,
    message: str = RATE_LIMITED,
    status: int = 429,
) -> None:
    """Spend one token of the app's `bucket_name` for `key`; `status` (429) when it is empty."""
    bucket: TokenBucket | None = getattr(request.app.state, bucket_name, None)
    wait = bucket.take(key) if bucket is not None else None
    if wait is not None:
        record("rate_limited", bucket=bucket_name)
        raise HTTPException(status, message, headers={"Retry-After": str(max(1, math.ceil(wait)))})


def rate_limit(bucket_name: str) -> Callable[[Request], Awaitable[None]]:
    """A dependency spending one token of the app's `bucket_name` for the caller's IP."""

    async def dependency(request: Request) -> None:
        spend(request, bucket_name, caller_ip(request))

    return dependency


limit_conversations = rate_limit("conversation_limit")
limit_messages = rate_limit("message_limit")


def get_sessions(request: Request) -> Sessions:
    return request.app.state.sessions


def allowed_origins() -> list[str]:
    return [origin.strip() for origin in get_settings().web_origin.split(",") if origin.strip()]


async def same_origin(request: Request) -> None:
    """CSRF defence for cookie-authenticated writes, on top of the SameSite=Lax cookie: a
    state-changing request must come from the dashboard's origin (a browser always sends
    `Origin` on these) and, when it has a body, carry JSON, which a cross-site form cannot send
    without a CORS preflight the API refuses. A request without `Origin` is not a browser's
    cross-site one, so it passes; `DELETE` has no body, so it skips the JSON check."""
    if request.method not in UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in allowed_origins():
        record("csrf_rejected", reason="origin")
        raise HTTPException(403, BAD_ORIGIN)
    if request.method != "DELETE":
        content_type = request.headers.get("content-type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            record("csrf_rejected", reason="content_type")
            raise HTTPException(415, NOT_JSON)


def set_session_cookie(response: Response, token: str, max_age_s: float) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(max_age_s),
        httponly=True,
        samesite="lax",
        secure=get_settings().production,
        path="/",
    )


def start_session(response: Response, sessions: Sessions, recruiter: Recruiter) -> None:
    """Issue a session for `recruiter` and set it as the cookie."""
    token = sessions.issue(recruiter.id, recruiter.session_version)
    set_session_cookie(response, token, sessions.ttl_s)


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME, httponly=True, samesite="lax", secure=get_settings().production, path="/"
    )


async def require_recruiter(
    request: Request,
    response: Response,
    repo: Annotated[Repo, Depends(get_repo)],
    sessions: Annotated[Sessions, Depends(get_sessions)],
    _csrf: Annotated[None, Depends(same_origin)],
) -> Recruiter:
    """The signed-in recruiter, from the session cookie; 401 without a valid session. The
    cookie is re-issued once it is a few minutes old, so the session slides while in use."""
    claims = sessions.read(request.cookies.get(COOKIE_NAME))
    if claims is None:
        raise HTTPException(401, SIGNED_OUT)
    with repo.session() as session:
        recruiter = session.get(Recruiter, claims.recruiter_id)
    if recruiter is None or not recruiter.active or recruiter.session_version != claims.version:
        raise HTTPException(401, SIGNED_OUT)
    if claims.age_s >= REFRESH_AFTER_S:
        start_session(response, sessions, recruiter)
    return recruiter


async def require_admin(
    recruiter: Annotated[Recruiter, Depends(require_recruiter)],
) -> Recruiter:
    if not recruiter.is_admin:
        raise HTTPException(403, FORBIDDEN)
    return recruiter


Service = Annotated[ScreeningService, Depends(get_service)]
RepoDep = Annotated[Repo, Depends(get_repo)]
Now = Annotated[datetime, Depends(get_now)]
Guard = Annotated[SpendGuard | None, Depends(get_spend_guard)]
CurrentRecruiter = Annotated[Recruiter, Depends(require_recruiter)]
Admin = Annotated[Recruiter, Depends(require_admin)]
SessionsDep = Annotated[Sessions, Depends(get_sessions)]
