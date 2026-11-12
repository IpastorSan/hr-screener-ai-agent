"""Operational events: one structured log line each (shipped to Loki), and the matching
Prometheus counter where there is one."""

import json
import logging

from sazon import metrics

logger = logging.getLogger("sazon.events")


def record(event: str, **fields: object) -> None:
    """One operational event, e.g. `budget_exceeded` or `rate_limited`. Never personal data."""
    if event == "rate_limited":
        metrics.RATE_LIMITED.labels(str(fields.get("bucket", "unknown"))).inc()
    elif event in ("chat_turned_away", "assistant_turned_away"):
        metrics.SPEND_CAP_BLOCKS.inc()
    elif event == "candidate_erased":
        metrics.ERASURES.labels(str(fields.get("via", "unknown"))).inc()
    logger.info(
        "event=%s %s",
        event,
        json.dumps(fields, default=str, sort_keys=True),
        extra={"event": event, "fields": fields},
    )
