"""Liveness and readiness for the container healthcheck, Caddy and the deploy smoke test."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx2
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from sazon.config import get_settings
from sazon.service import ScreeningService
from sazon.storage.migrate import current_revision, head_revision
from sazon.storage.repo import is_in_memory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])

OPENROUTER_TTL_S = 60.0
OPENROUTER_TIMEOUT_S = 5.0


class Check(BaseModel):
    ok: bool
    detail: str = ""


class Ready(BaseModel):
    """The public body: the status only. Which check failed is logged; the day's spend and the
    cap are Prometheus gauges (`sazon_spend_today_usd`, `sazon_spend_cap_usd`)."""

    status: str
    """`ok` or `fail`."""


type Probe = Callable[[], Awaitable[Check]]


class OpenRouterProbe:
    """`GET /models` on the configured OpenRouter base URL, at most once per minute."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        ttl_s: float = OPENROUTER_TTL_S,
        monotonic: Callable[[], float] = time.monotonic,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = base_url.rstrip("/") + "/models"
        self.api_key = api_key
        self.ttl_s = ttl_s
        self.monotonic = monotonic
        self.transport = transport
        self._last: tuple[float, Check] | None = None
        self._lock = asyncio.Lock()

    async def __call__(self) -> Check:
        async with self._lock:
            now = self.monotonic()
            if self._last is not None and now - self._last[0] < self.ttl_s:
                return self._last[1]
            check = await self._probe()
            self._last = (now, check)
            return check

    async def _probe(self) -> Check:
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx2.AsyncClient(
                timeout=OPENROUTER_TIMEOUT_S, transport=self.transport
            ) as client:
                response = await client.get(self.url, headers=headers)
        except Exception as exc:
            return Check(ok=False, detail=f"{type(exc).__name__}")
        if response.status_code != 200:
            return Check(ok=False, detail=f"HTTP {response.status_code}")
        return Check(ok=True, detail="models endpoint reachable")


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """The process is up. Says nothing about its dependencies; see `/readyz`."""
    return {"status": "ok"}


@router.get("/readyz", response_model=Ready, responses={503: {"model": Ready}})
async def readyz(request: Request, deep: bool = False) -> JSONResponse:
    """Database reachable and migrated, retrieval index loaded. 503 when either fails, with the
    failing checks logged; the body is the status alone, as anyone can reach it.

    OpenRouter is not part of it: without a model the chat degrades to templates, and a
    provider blip during a deploy must not roll it back. `?deep=1` adds the OpenRouter check
    (checked at most once a minute; skipped with `SAZON_FAKE_LLM=1`) to the status.
    """
    service: ScreeningService = request.app.state.service
    checks = {
        "database": await asyncio.to_thread(database_check, service),
        "rag": rag_check(service),
    }
    if deep:
        probe: Probe | None = getattr(request.app.state, "openrouter_probe", None)
        checks["openrouter"] = (
            await probe() if probe else Check(ok=True, detail="skipped (offline)")
        )
    failed = {name: check.detail for name, check in checks.items() if not check.ok}
    if failed:
        logger.warning("Readiness failed: %s", failed)
    body = Ready(status="fail" if failed else "ok")
    return JSONResponse(body.model_dump(mode="json"), 503 if failed else 200)


def database_check(service: ScreeningService) -> Check:
    engine = service.repo.engine
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        if is_in_memory(engine):
            return Check(ok=True, detail="in memory")
        revision, head = current_revision(engine), head_revision()
    except Exception as exc:
        logger.warning("Readiness: database check failed: %s", type(exc).__name__)
        return Check(ok=False, detail=type(exc).__name__)
    if revision != head:
        return Check(ok=False, detail=f"schema at {revision}, expected {head}")
    return Check(ok=True, detail=f"schema {revision}")


def rag_check(service: ScreeningService) -> Check:
    ready = getattr(service.faq, "ready", None)
    if ready is None:
        return Check(ok=False, detail="no retrieval index configured")
    if not ready:
        return Check(ok=False, detail="index not loaded; run `sazon rag-index`")
    describe = getattr(service.faq, "describe", None)
    return Check(ok=True, detail=str(describe()) if callable(describe) else "loaded")


def openrouter_probe() -> OpenRouterProbe | None:
    settings = get_settings()
    if settings.sazon_fake_llm:
        return None
    return OpenRouterProbe(
        settings.openrouter_base_url, settings.openrouter_api_key.get_secret_value()
    )
