"""Prometheus metrics, served at `/metrics` on the internal network only (Caddy never routes it).

Labels hold no personal data: roles, model slugs, countries, channels, outcomes and flags.
"""

from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, disable_created_metrics

from sazon.llm.interface import FIXED_REPLY_MODEL, CallRecord

disable_created_metrics()  # one `_created` series per counter is noise here

LATENCY_BUCKETS = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0, 15.0, 30.0, 60.0)

TURNS = Counter("sazon_turns", "Stored conversation turns.", ["role"])
LLM_CALLS = Counter(
    "sazon_llm_calls", "Model calls, fallbacks included; fixed replies are not.", ["role", "model"]
)
LLM_LATENCY = Histogram(
    "sazon_llm_latency_seconds",
    "Model call latency, fallbacks included; fixed replies are not.",
    ["role", "model"],
    buckets=LATENCY_BUCKETS,
)
LLM_FALLBACK = Counter(
    "sazon_llm_fallback", "Model calls whose output was not used (templates instead).", ["role"]
)
FIXED_REPLIES = Counter(
    "sazon_fixed_replies", "Replies sent as written by design, with no model call.", ["role"]
)
LLM_COST = Counter("sazon_llm_cost_usd", "Model spend reported by OpenRouter.", ["role", "model"])
CONVERSATIONS_STARTED = Counter(
    "sazon_conversations_started", "Chats opened.", ["country", "channel"]
)
CONVERSATION_OUTCOMES = Counter(
    "sazon_conversation_outcomes", "Conversations reaching an outcome.", ["outcome"]
)
QUALITY_FLAGS = Counter("sazon_quality_flags", "Quality flags raised on conversations.", ["flag"])
FAQ_MISS = Counter("sazon_faq_miss", "Questions neither the FAQ nor the documents answered.")
NUDGES_SERVED = Counter("sazon_nudges_served", "Nudges sent to a quiet candidate.", ["attempt"])
NUDGE_RECOVERIES = Counter(
    "sazon_nudge_recoveries", "Conversations where the candidate wrote again after a nudge."
)
OPENROUTER_ERRORS = Counter(
    "sazon_openrouter_errors", "Failed OpenRouter calls by HTTP status (or `timeout`).", ["status"]
)
SPEND_CAP_BLOCKS = Counter(
    "sazon_spend_cap_blocks", "Requests turned away because the daily model budget was spent."
)
RATE_LIMITED = Counter("sazon_rate_limited", "Requests refused by a per-IP limit.", ["bucket"])
ERASURES = Counter("sazon_erasures", "Candidates erased on request (`cli` or `api`).", ["via"])
SPEND_TODAY = Gauge("sazon_spend_today_usd", "Model spend so far this UTC day.")
SPEND_CAP = Gauge("sazon_spend_cap_usd", "MAX_DAILY_LLM_USD; 0 when no cap is set.")
NEWEST_BACKUP = Gauge(
    "sazon_backup_newest_timestamp_seconds", "Modification time of the newest local backup."
)
CALENDAR_NOTIFICATIONS = Counter(
    "sazon_calendar_notifications",
    "Google Calendar push notifications by resource state, `rejected` when the channel, token "
    "or resource did not match.",
    ["state"],
)
CALENDAR_RECONCILIATIONS = Counter(
    "sazon_calendar_reconciliations",
    "Bookings changed after a change in a recruiter's Google Calendar, by type.",
    ["type"],
)
CALENDAR_WATCH_FAILURES = Counter(
    "sazon_calendar_watch_failures",
    "Failed push-channel operations in this process (`start`, `renew`, `stop`, `sync`).",
    ["op"],
)
CALENDAR_CHANNELS = Gauge(
    "sazon_calendar_channels",
    "Connected calendars by push-channel health: `ok`, `expiring` (under 24 h left: renewal "
    "failed or did not run) or `missing`. Read from the database at scrape time.",
    ["state"],
)

CALENDAR_NOTIFICATION_STATES = ("sync", "exists", "not_exists", "other", "rejected")
CALENDAR_RECONCILIATION_TYPES = (
    "moved",
    "moved_flagged",
    "cancelled",
    "reassigned",
    "recruiter_declined",
    "candidate_declined",
    "resync",
)


def declare_model(role: str, model: str) -> None:
    """Start a configured model's call counter at zero, so `increase()` sees its first calls."""
    LLM_CALLS.labels(role, model)
    LLM_LATENCY.labels(role, model)


def observe_call(call: CallRecord) -> None:
    """One model call (or its template fallback) by role and model. A fixed reply is no model
    call: counting it would dilute the fallback rate and drag the latency percentiles to 0."""
    role = call.role.value
    if call.model == FIXED_REPLY_MODEL:
        FIXED_REPLIES.labels(role).inc()
        return
    LLM_CALLS.labels(role, call.model).inc()
    LLM_LATENCY.labels(role, call.model).observe(call.latency_ms / 1000)
    if call.fallback_used:
        LLM_FALLBACK.labels(role).inc()
    if call.cost_usd:
        LLM_COST.labels(role, call.model).inc(call.cost_usd)


def count_openrouter_error(exc: BaseException) -> None:
    """A failed model call by HTTP status (402 no credits, 403 key limit reached, 429 rate
    limited...), `timeout` or `connection`. Invalid model output is not OpenRouter's."""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        status = str(status_code)
    elif isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:  # httpx.ReadTimeout
        status = "timeout"
    elif "Connection" in type(exc).__name__ or "Connect" in type(exc).__name__:
        status = "connection"
    else:
        return
    OPENROUTER_ERRORS.labels(status).inc()


def observe_backups(directory: Path) -> None:
    """The newest `*.db.gz` in `directory`, when there is one."""
    try:
        newest = max((p.stat().st_mtime for p in directory.glob("*.db.gz")), default=None)
    except OSError:
        newest = None
    if newest is not None:
        NEWEST_BACKUP.set(newest)


def _initialize() -> None:
    """Series that alerts read start at zero, so the first increment shows up in `increase()`."""
    from sazon.engine.models import Outcome, QualityFlag
    from sazon.llm.interface import Role

    for role in (*(r.value for r in Role), "assistant"):
        LLM_FALLBACK.labels(role)
    FIXED_REPLIES.labels(Role.RESPONDER.value)
    for status in ("400", "402", "403", "429", "500", "502", "503", "timeout", "connection"):
        OPENROUTER_ERRORS.labels(status)
    for flag in QualityFlag:
        QUALITY_FLAGS.labels(flag.value)
    for outcome in Outcome:
        CONVERSATION_OUTCOMES.labels(outcome.value)
    for attempt in ("1", "2"):
        NUDGES_SERVED.labels(attempt)
    for via in ("cli", "api"):
        ERASURES.labels(via)
    for country in ("ES", "MX"):
        for channel in ("web", "voice"):
            CONVERSATIONS_STARTED.labels(country, channel)
    for state in CALENDAR_NOTIFICATION_STATES:
        CALENDAR_NOTIFICATIONS.labels(state)
    for kind in CALENDAR_RECONCILIATION_TYPES:
        CALENDAR_RECONCILIATIONS.labels(kind)
    for op in ("start", "renew", "stop", "sync"):
        CALENDAR_WATCH_FAILURES.labels(op)


_initialize()
