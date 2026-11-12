"""FastAPI app factory. Run with `uvicorn --factory sazon.api.app:create_app` (`make dev-api`)."""

import asyncio
import logging
import os
import traceback
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.exceptions import HTTPException as StarletteHTTPException

from sazon import logs, metrics, telemetry
from sazon.api import (
    agent_spec,
    analytics,
    assistant,
    auth,
    candidate,
    faq_misses,
    google_auth,
    health,
    interviews,
    locations,
    recruiter,
    recruiters,
    voice,
    webhooks,
)
from sazon.api.schemas import ErrorField, ErrorOut
from sazon.auth import sessions_from
from sazon.calendar.oauth import GoogleOAuth
from sazon.calendar.tokens import cipher_from
from sazon.config import Settings, get_settings
from sazon.guards import SpendGuard, TokenBucket
from sazon.llm.budget import BudgetedLLM
from sazon.llm.interface import LLM
from sazon.pii import redact
from sazon.scheduling.push import CalendarPush, observe_channels
from sazon.service import ScreeningService
from sazon.storage.repo import Repo, init_db, make_engine

logger = logging.getLogger(__name__)

ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}


class UnsafeProductionConfig(RuntimeError):
    """`ENV=production` with a setting meant for development."""


def create_app(
    service: ScreeningService | None = None,
    *,
    spend_guard: SpendGuard | None = None,
) -> FastAPI:
    """Build the API. Without a `service` it migrates `DATABASE_URL`, warms the retrieval model
    and uses a real model (capped by `MAX_DAILY_LLM_USD`), or the offline extractor with template
    replies when `SAZON_FAKE_LLM=1`; it also starts tracing when `OTEL_EXPORTER_OTLP_ENDPOINT`
    is set. Refuses to build when `ENV=production` and a development setting is on or
    `SESSION_SECRET` is missing.

    `/metrics` (Prometheus) is served on the API port; Caddy does not route it."""
    settings = get_settings()
    problems = settings.production_problems()
    if problems:
        raise UnsafeProductionConfig("Refusing to start in production: " + "; ".join(problems))

    if service is None:
        logs.configure(settings.json_logs)
        telemetry.configure(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is None:
            repo = Repo(make_engine())
            guard = spend_guard or SpendGuard(repo, settings.max_daily_llm_usd)
            screening = ScreeningService(_llm(settings, guard), repo)
            app.state.openrouter_probe = health.openrouter_probe()
        else:
            screening = service
            guard = spend_guard or SpendGuard(service.repo, None, clock=service.clock)
        await asyncio.to_thread(init_db, screening.repo.engine)
        warm = getattr(screening.faq, "warm", None)
        if callable(warm) and not await asyncio.to_thread(warm):
            logger.warning("Retrieval index not loaded; questions take the unknown path.")
        app.state.service = screening
        app.state.spend_guard = guard
        app.state.calendar_push = CalendarPush.from_settings(settings, screening)
        yield
        if app.state.calendar_push is not None:
            await app.state.calendar_push.drain()
        if service is None:
            telemetry.shutdown()

    app = FastAPI(
        title="Sazón screener API",
        version="0.1.0",
        lifespan=lifespan,
        responses={422: {"model": ErrorOut}, 500: {"model": ErrorOut}},
    )
    origins = [origin.strip() for origin in settings.web_origin.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["content-type", "accept", "authorization"],
        # The dashboard's session cookie, when the web and the API are on different origins
        # (local development); behind Caddy they share one.
        allow_credentials=True,
    )
    app.add_exception_handler(StarletteHTTPException, _http_error)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RequestValidationError, _validation_error)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(Exception, _unexpected_error)
    Instrumentator(
        excluded_handlers=["/metrics", "/healthz", "/readyz"],
        should_group_untemplated=True,
    ).instrument(app, metric_namespace="sazon")
    telemetry.instrument_app(app)
    app.add_api_route("/metrics", _metrics, include_in_schema=False)
    app.state.conversation_limit = TokenBucket(settings.rate_limit_conversations_per_hour, 3600)
    app.state.message_limit = TokenBucket(settings.rate_limit_messages_per_minute, 60)
    app.state.login_ip_limit = TokenBucket(settings.login_attempts_per_ip, 900)
    app.state.login_email_limit = TokenBucket(settings.login_attempts_per_email, 900)
    app.state.calendar_webhook_limit = TokenBucket(
        settings.rate_limit_calendar_webhooks_per_minute, 60
    )
    app.state.sessions = sessions_from(settings)
    app.state.token_cipher = cipher_from(settings)
    app.state.google_oauth = (
        GoogleOAuth.from_settings(settings) if settings.google_configured else None
    )
    app.include_router(health.router)
    app.include_router(candidate.router)
    app.include_router(auth.router)
    app.include_router(google_auth.router)
    app.include_router(recruiters.router)
    app.include_router(interviews.router)
    app.include_router(recruiter.router)
    app.include_router(locations.router)
    app.include_router(analytics.router)
    app.include_router(agent_spec.router)
    app.include_router(faq_misses.router)
    app.include_router(assistant.router)
    app.include_router(voice.router)
    app.include_router(webhooks.router)
    return app


async def _metrics(request: Request) -> Response:
    """Prometheus exposition, with today's spend, the cap and the newest backup refreshed."""
    guard: SpendGuard | None = getattr(request.app.state, "spend_guard", None)
    if guard is not None:
        metrics.SPEND_TODAY.set(await asyncio.to_thread(guard.spent_today))
        if guard.cap_usd is not None:
            metrics.SPEND_CAP.set(guard.cap_usd)
        database = guard.repo.engine.url.database
        if database and database != ":memory:":
            backups = Path(database).parent / "backups"
            await asyncio.to_thread(metrics.observe_backups, backups)
    push: CalendarPush | None = getattr(request.app.state, "calendar_push", None)
    if push is not None and push.address is not None:
        await asyncio.to_thread(observe_channels, push.repo, push.service.clock())
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def _llm(settings: Settings, guard: SpendGuard) -> LLM:
    if settings.sazon_fake_llm:
        from sazon.llm.offline import OfflineLLM

        return OfflineLLM()
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    from sazon.llm.pydantic_ai_impl import PydanticAILLM

    model = PydanticAILLM()
    if settings.max_daily_llm_usd is None:
        return model
    return BudgetedLLM(model, guard.over_budget)


def error_response(
    status: int,
    detail: str,
    fields: list[ErrorField] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    code = ERROR_CODES.get(status, "error")
    body = ErrorOut(error=code, detail=detail, fields=fields)
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True), status_code=status, headers=headers
    )


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, str(exc.detail), headers=exc.headers)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 with where and why, never the submitted values: they may hold personal data."""
    fields = [
        ErrorField(
            loc=[part for part in error["loc"] if isinstance(part, str | int)],
            message=error["msg"],
        )
        for error in exc.errors()
    ]
    return error_response(422, "The request is not valid.", fields)


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    detail = redact("".join(traceback.format_exception(exc)))
    logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, detail)
    return error_response(500, "Something went wrong. Please try again.")
