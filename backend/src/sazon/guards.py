"""Operational guards: the daily model spend cap and per-IP rate limits."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as dtime

from sqlalchemy import func
from sqlmodel import col, select

from sazon.observability import record
from sazon.storage.models import Summary, Turn
from sazon.storage.repo import Repo, to_utc

logger = logging.getLogger(__name__)

SPEND_CACHE_S = 30.0
MAX_BUCKETS = 10_000


class SpendGuard:
    """Today's model spend (UTC day) against `MAX_DAILY_LLM_USD`.

    Spend is what the screening stored on turns and summaries today, plus the recruiter
    assistant's calls added with `add` (they are not stored). Read at most every 30 seconds.
    """

    def __init__(
        self,
        repo: Repo,
        cap_usd: float | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        cache_s: float = SPEND_CACHE_S,
    ) -> None:
        self.repo = repo
        self.cap_usd = cap_usd
        self.clock = clock
        self.monotonic = monotonic
        self.cache_s = cache_s
        self._lock = threading.Lock()
        self._stored: tuple[float, float] | None = None  # (read at, USD)
        self._extra: dict[str, float] = {}  # day -> USD not stored in the database
        self._warned_day: str | None = None

    def add(self, cost_usd: float | None) -> None:
        if cost_usd:
            with self._lock:
                day = self._day()
                self._extra = {day: self._extra.get(day, 0.0) + cost_usd}

    def spent_today(self) -> float:
        """Blocks on the database when the cached figure is stale: call it from a thread.

        The lock guards the cached values only, never the read, so a caller never waits on
        another's query; two callers racing past a stale cache both read, and the later wins."""
        now = self.monotonic()
        with self._lock:
            stored = self._stored
        if stored is None or now - stored[0] >= self.cache_s:
            stored = (now, self._read())
            with self._lock:
                self._stored = stored
        with self._lock:
            return stored[1] + self._extra.get(self._day(), 0.0)

    def over_budget(self) -> bool:
        if self.cap_usd is None:
            return False
        spent = self.spent_today()
        over = spent >= self.cap_usd
        if over and self._warned_day != self._day():
            self._warned_day = self._day()
            logger.warning(
                "Daily model budget reached: $%.2f of $%.2f. New chats are turned away and "
                "open ones continue on templates until midnight UTC.",
                spent,
                self.cap_usd,
            )
            record("budget_exceeded", spent_usd=round(spent, 4), cap_usd=self.cap_usd)
        return over

    def _day(self) -> str:
        return to_utc(self.clock()).date().isoformat()

    def _read(self) -> float:
        start = datetime.combine(to_utc(self.clock()).date(), dtime.min, tzinfo=UTC)
        with self.repo.session() as session:
            turns = session.exec(
                select(func.coalesce(func.sum(Turn.cost_usd), 0.0)).where(
                    col(Turn.created_at) >= start
                )
            ).one()
            summaries = session.exec(
                select(func.coalesce(func.sum(Summary.cost_usd), 0.0)).where(
                    col(Summary.created_at) >= start
                )
            ).one()
        return float(turns or 0.0) + float(summaries or 0.0)


class TokenBucket:
    """In-process token buckets keyed by client: `capacity` requests, refilled evenly over
    `period_s`. A capacity of 0 turns the limit off."""

    def __init__(
        self,
        capacity: int,
        period_s: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity > 0 and period_s <= 0:
            raise ValueError("a rate limit needs a positive period")
        self.capacity = capacity
        self.rate = capacity / period_s if period_s > 0 else 0.0
        self.monotonic = monotonic
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, updated)
        self._lock = threading.Lock()

    def take(self, key: str) -> float | None:
        """Spend one token. None when allowed, else the seconds until one is available."""
        if self.capacity <= 0:
            return None
        with self._lock:
            now = self.monotonic()
            tokens, updated = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(float(self.capacity), tokens + (now - updated) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return (1.0 - tokens) / self.rate
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > MAX_BUCKETS:
                self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        full = [
            key
            for key, (tokens, updated) in self._buckets.items()
            if tokens + (now - updated) * self.rate >= self.capacity
        ]
        for key in full:
            del self._buckets[key]


def client_ip(forwarded_for: str | None, peer: str | None) -> str:
    """The caller's address. Behind Caddy, the last `X-Forwarded-For` entry is the one Caddy
    added: earlier entries come from the client and can be forged."""
    if forwarded_for:
        last = forwarded_for.split(",")[-1].strip()
        if last:
            return last
    return peer or "unknown"
