"""Log formatting: one JSON object per line in production (for Loki), plain text elsewhere.

Messages and tracebacks are masked (emails, phone numbers, OAuth codes in callback URLs) in
every format, whatever the caller did: `PiiFilter` on every root and uvicorn handler (access
lines included), and again by the JSON formatter.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sazon import telemetry
from sazon.pii import redact

TEXT_FORMAT = "%(levelname)s %(name)s: %(message)s"
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
# Probed every few seconds by Docker, Prometheus and the blackbox exporter.
QUIET_PATHS = ("/healthz", "/readyz", "/metrics")
# Google's OAuth callback carries a one-time code and our state in the query string.
OAUTH_QUERY = re.compile(r"([?&](?:code|state)=)[^&\s\"']+")
# Readiness reads the migration state on every probe; alembic says so at INFO each time.
QUIET_LOGGERS = ("alembic",)


class QuietProbes(logging.Filter):
    """Drops uvicorn access lines for health and metrics requests that succeeded."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        path, status = args[2], args[4]
        return not (isinstance(path, str) and path.startswith(QUIET_PATHS) and status == 200)


def _mask(text: str) -> str:
    return OAUTH_QUERY.sub(r"\1[redacted]", redact(text))


class PiiFilter(logging.Filter):
    """Rewrites a record's message and traceback with contact details masked. Never drops one.

    Positional arguments are masked one by one and kept: uvicorn's `AccessFormatter` unpacks
    an access record's five arguments (client, method, path, HTTP version, status). Only when
    that still leaves something unmasked (in the format string, or a number split across two
    arguments) is the message replaced by its masked text, which an access record never needs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        masked = _mask(message)
        if masked != message:
            if isinstance(record.args, tuple):
                record.args = tuple(_mask(a) if isinstance(a, str) else a for a in record.args)
            if record.getMessage() != masked:
                record.msg, record.args = masked, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_pii_filter() -> None:
    """Add `PiiFilter` to every handler of the root and uvicorn loggers, once."""
    for name in ("", *UVICORN_LOGGERS):
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, PiiFilter) for f in handler.filters):
                handler.addFilter(PiiFilter())


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        event = getattr(record, "event", None)
        if event:
            entry["event"] = event
            entry["fields"] = getattr(record, "fields", None)
        conversation = telemetry.current_conversation_id()
        if conversation:
            entry["conversation_id"] = conversation
        ids = telemetry.current_ids()
        if ids:
            entry["trace_id"], entry["span_id"] = ids
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure(json_logs: bool) -> None:
    """The app's loggers at INFO on stderr; with `json_logs`, uvicorn's lines become JSON too."""
    formatter: logging.Formatter = JsonFormatter() if json_logs else logging.Formatter(TEXT_FORMAT)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    elif json_logs:
        for handler in root.handlers:
            handler.setFormatter(formatter)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QuietProbes) for f in access.filters):
        access.addFilter(QuietProbes())
    if json_logs:
        for name in UVICORN_LOGGERS:
            for handler in logging.getLogger(name).handlers:
                handler.setFormatter(formatter)
    install_pii_filter()
