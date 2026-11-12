"""Google sign-in (corporate SSO) and the Google Calendar connection.

`GET /api/auth/google/start` signs a recruiter in with their Workspace account;
`GET /api/auth/google/connect` connects the calendar of a recruiter already signed in (with a
password, say). Both go through Google's consent page and come back to
`GET /api/auth/google/callback`, which checks the state and the PKCE verifier kept in a signed
ten-minute cookie, verifies the ID token and the Workspace (`calendar.oauth`), and ends on the
dashboard. Failures come back to the web as `?error=<code>` (sign-in) or `?calendar=<code>`
(connect), never with Google's details. The same grant connects the calendar when it carries
a refresh token and the calendar scopes; `DELETE /api/auth/google/calendar` revokes it.
"""

import asyncio
import hmac
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from sazon import recruiters
from sazon.api.deps import (
    CurrentRecruiter,
    RepoDep,
    Service,
    SessionsDep,
    allowed_origins,
    caller_ip,
    same_origin,
    spend,
    start_session,
)
from sazon.api.schemas import AuthProvidersOut, CalendarOut
from sazon.calendar.oauth import (
    Flow,
    GoogleOAuth,
    OAuthError,
    credential_of,
    disconnect,
    save_credential,
)
from sazon.calendar.tokens import TokenCipher
from sazon.config import get_settings
from sazon.observability import record
from sazon.storage.models import CredentialStatus, GoogleCredential

FLOW_COOKIE = "sazon_google_flow"
FLOW_PATH = "/api/auth/google"
FLOW_TTL_S = 600
DEFAULT_NEXT = "/recruiters"
LOGIN_RATE_LIMITED = "Demasiados intentos. Espera unos minutos e inténtalo de nuevo."
NOT_CONFIGURED = "El acceso con Google no está configurado."

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_oauth(request: Request) -> GoogleOAuth | None:
    return getattr(request.app.state, "google_oauth", None)


def get_cipher(request: Request) -> TokenCipher:
    return request.app.state.token_cipher


OAuthDep = Annotated[GoogleOAuth | None, Depends(get_oauth)]
CipherDep = Annotated[TokenCipher, Depends(get_cipher)]
NextPath = Annotated[str, Query(max_length=512)]


def calendar_out(credential: GoogleCredential | None) -> CalendarOut:
    if credential is None:
        return CalendarOut()
    status: Literal["connected", "revoked"] = (
        "connected" if credential.status == CredentialStatus.CONNECTED else "revoked"
    )
    return CalendarOut(
        status=status, google_email=credential.google_email, connected_at=credential.connected_at
    )


@router.get("/providers", response_model=AuthProvidersOut)
async def providers(oauth: OAuthDep) -> AuthProvidersOut:
    """Public: whether "Continuar con Google" and "Conectar Google Calendar" are on."""
    settings = get_settings()
    source = settings.scheduling_source.strip().lower()
    return AuthProvidersOut(
        google=oauth is not None,
        domain=settings.google_workspace_domain,
        scheduling_source=source if source in ("google", "fake") else "fixed",  # pyright: ignore[reportArgumentType]
    )


@router.get("/google/start", response_class=RedirectResponse, status_code=302)
async def google_start(
    request: Request, oauth: OAuthDep, sessions: SessionsDep, next: NextPath = DEFAULT_NEXT
) -> RedirectResponse:
    """Sign in with Google: off to Google's account chooser, limited to the Workspace. Nothing
    is stored and Google is not called here, so the login limit is spent once, at the callback."""
    if oauth is None:
        raise HTTPException(404, NOT_CONFIGURED)
    flow = oauth.new_flow("login", safe_next(next))
    return _to_google(oauth, sessions, flow, consent=False)


@router.get("/google/connect", response_class=RedirectResponse, status_code=302)
async def google_connect(
    recruiter: CurrentRecruiter,
    oauth: OAuthDep,
    sessions: SessionsDep,
    next: NextPath = DEFAULT_NEXT,
) -> RedirectResponse:
    """Connect the signed-in recruiter's Google Calendar. Always asks for consent, the only way
    Google hands out a new refresh token."""
    if oauth is None:
        raise HTTPException(404, NOT_CONFIGURED)
    flow = oauth.new_flow("connect", safe_next(next), recruiter_id=recruiter.id)
    return _to_google(oauth, sessions, flow, consent=True, login_hint=recruiter.email)


@router.get("/google/callback", response_class=RedirectResponse, status_code=302)
async def google_callback(
    request: Request,
    repo: RepoDep,
    sessions: SessionsDep,
    oauth: OAuthDep,
    cipher: CipherDep,
    state: Annotated[str, Query(max_length=512)] = "",
    code: Annotated[str, Query(max_length=2048)] = "",
    error: Annotated[str, Query(max_length=128)] = "",
) -> RedirectResponse:
    """Where Google sends the browser back. Always a redirect to the web, but over the per-IP
    login limit (one token per sign-in or calendar connection), a 429."""
    if oauth is None:
        raise HTTPException(404, NOT_CONFIGURED)
    spend(request, "login_ip_limit", caller_ip(request), LOGIN_RATE_LIMITED)
    flow = Flow.load(sessions.read_flow(request.cookies.get(FLOW_COOKIE), FLOW_TTL_S))
    ip = sessions.pseudonym(caller_ip(request))
    if flow is None or not state or not hmac.compare_digest(flow.state, state):
        record("google_login_failed", reason="state", ip=ip)
        return _done(_web("/login", error="state"))
    try:
        if error or not code:
            raise OAuthError("denied" if error == "access_denied" else "failed")
        tokens = await asyncio.to_thread(oauth.exchange, code, flow)
        identity = await asyncio.to_thread(oauth.verify, tokens.id_token, flow)
        recruiter_id = flow.recruiter_id if flow.intent == "connect" else None
        recruiter = (
            recruiters.get(repo, recruiter_id)
            if recruiter_id
            else recruiters.by_email(repo, identity.email)
        )
        if recruiter is None or not recruiter.active:
            raise OAuthError("unknown")
        if recruiter.email != identity.email:
            raise OAuthError("mismatch")
    except OAuthError as exc:
        record("google_login_failed", reason=exc.code, intent=flow.intent, ip=ip)
        if flow.intent == "connect":
            return _done(_web(flow.next, calendar=exc.code))
        return _done(_web("/login", error=exc.code))

    calendar = "unchanged"
    if tokens.refresh_token and tokens.calendar_granted:
        await asyncio.to_thread(save_credential, repo, cipher, recruiter.id, identity, tokens)
        calendar = "connected"
        push = getattr(request.app.state, "calendar_push", None)
        if push is not None:
            push.start_in_background(recruiter.id)  # push notifications (SAZ-40)
    elif flow.intent == "connect":
        calendar = "scopes" if not tokens.calendar_granted else "failed"
    if flow.intent == "connect":
        record(
            "google_calendar_connected" if calendar == "connected" else "google_login_failed",
            recruiter_id=recruiter.id,
            reason=calendar,
        )
        return _done(_web(flow.next, calendar=calendar))
    record("login_succeeded", recruiter_id=recruiter.id, via="google", ip=ip)
    response = _done(_web(flow.next, calendar=calendar if calendar == "connected" else None))
    start_session(response, sessions, recruiter)
    return response


@router.delete(
    "/google/calendar",
    status_code=204,
    dependencies=[Depends(same_origin)],
    response_class=Response,
)
async def google_disconnect(
    request: Request,
    recruiter: CurrentRecruiter,
    service: Service,
    oauth: OAuthDep,
    cipher: CipherDep,
) -> Response:
    """Revoke the signed-in recruiter's calendar grant at Google and forget it. Interviews
    already booked keep their recruiter; their events stay on that calendar. Its push
    channels are stopped first, while the grant still works."""
    push = getattr(request.app.state, "calendar_push", None)
    if push is not None:
        await asyncio.to_thread(push.stop, recruiter.id)
    removed = await asyncio.to_thread(disconnect, service.repo, cipher, oauth, recruiter.id)
    forget = getattr(service.scheduling.calendar, "forget", None)
    if callable(forget):
        forget(recruiter.id)
    if removed:
        record("google_calendar_disconnected", recruiter_id=recruiter.id)
    return Response(status_code=204)


@router.get("/google/calendar", response_model=CalendarOut)
async def google_calendar(recruiter: CurrentRecruiter, repo: RepoDep) -> CalendarOut:
    """The signed-in recruiter's calendar connection."""
    return calendar_out(await asyncio.to_thread(credential_of, repo, recruiter.id))


def safe_next(value: str | None) -> str:
    """A path on the dashboard, never another site (`//evil.example`, `/\\evil`)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return DEFAULT_NEXT
    return value


def _to_google(
    oauth: GoogleOAuth,
    sessions: SessionsDep,
    flow: Flow,
    *,
    consent: bool,
    login_hint: str | None = None,
) -> RedirectResponse:
    url = oauth.authorization_url(flow, consent=consent, login_hint=login_hint)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        FLOW_COOKIE,
        sessions.sign_flow(flow.dump()),
        max_age=FLOW_TTL_S,
        httponly=True,
        samesite="lax",  # sent on Google's top-level redirect back
        secure=get_settings().production,
        path=FLOW_PATH,
    )
    return response


def _web(path: str, **params: str | None) -> str:
    origins = allowed_origins()
    base = origins[0].rstrip("/") if origins else ""
    query = urlencode({key: value for key, value in params.items() if value})
    joiner = "&" if "?" in path else "?"
    return f"{base}{path}{joiner + query if query else ''}"


def _done(url: str) -> RedirectResponse:
    response = RedirectResponse(url, status_code=302)
    response.delete_cookie(
        FLOW_COOKIE, httponly=True, samesite="lax", secure=get_settings().production, path=FLOW_PATH
    )
    return response
