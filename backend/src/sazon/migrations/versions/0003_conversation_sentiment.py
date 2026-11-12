"""conversation.sentiment: label counts, peak and latest non-neutral label per conversation

Existing conversations are filled from their candidate turns that a model read (a widget tap has
no model and a fallback extraction is marked), and each candidate's mood is recomputed from
their latest conversation.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20 12:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

conversation = sa.table(
    "conversation",
    sa.column("id", sa.String),
    sa.column("candidate_id", sa.String),
    sa.column("started_at", sa.DateTime),
    sa.column("sentiment", sa.JSON),
)
turn = sa.table(
    "turn",
    sa.column("id", sa.Integer),
    sa.column("conversation_id", sa.String),
    sa.column("role", sa.String),
    sa.column("extraction", sa.JSON),
    sa.column("model", sa.String),
    sa.column("fallback_used", sa.Boolean),
)
candidate = sa.table("candidate", sa.column("id", sa.String), sa.column("sentiment", sa.String))

# A frozen copy of `sazon.sentiment.SentimentStats` as of this revision, so a later change there
# cannot change what this migration writes. The stored shape is SentimentStats.dump()'s.
SEVERITY = {"frustrated": 3, "confused": 2, "neutral": 1, "positive": 0}
NEGATIVE = frozenset({"frustrated", "confused"})
Stats = dict[str, Any]  # {"counts": {label: n}, "peak": label | None, "last_notable": ...}


def empty() -> Stats:
    return {"counts": {}, "peak": None, "last_notable": None}


def record(stats: Stats, label: str) -> Stats:
    peak = stats["peak"]
    if peak is None or SEVERITY[label] > SEVERITY[peak]:
        peak = label
    counts = stats["counts"]
    return {
        "counts": {**counts, label: counts.get(label, 0) + 1},
        "peak": peak,
        "last_notable": label if label != "neutral" else stats["last_notable"],
    }


def mood(stats: Stats) -> str | None:
    peak = stats["peak"]
    if peak is None or peak in NEGATIVE:
        return peak
    return stats["last_notable"] or peak


def upgrade() -> None:
    bind = op.get_bind()
    # Adoption stamps head now (`storage/migrate.py`); a database adopted at 0001 before
    # SAZ-55 may still come through here with the column, made by `create_all`.
    columns = {c["name"] for c in sa.inspect(bind).get_columns("conversation")}
    if "sentiment" not in columns:
        with op.batch_alter_table("conversation", schema=None) as batch_op:
            batch_op.add_column(sa.Column("sentiment", sa.JSON(), nullable=True))
    _backfill(bind)


def downgrade() -> None:
    with op.batch_alter_table("conversation", schema=None) as batch_op:
        batch_op.drop_column("sentiment")


def _backfill(bind: sa.Connection) -> None:
    stats: dict[str, Stats] = {}
    read = (
        sa.select(turn.c.conversation_id, turn.c.extraction)
        .where(turn.c.role == "candidate")
        .where(turn.c.model.is_not(None))
        .where(sa.not_(turn.c.fallback_used))
        .order_by(turn.c.id)
    )
    for conversation_id, extraction in bind.execute(read):
        label = _label(extraction)
        if label is not None:
            stats[conversation_id] = record(stats.get(conversation_id, empty()), label)
    latest: dict[str, str] = {}
    rows = bind.execute(
        sa.select(conversation.c.id, conversation.c.candidate_id).order_by(
            conversation.c.started_at
        )
    )
    for conversation_id, candidate_id in rows:
        found = stats.get(conversation_id)
        if found is None:
            continue
        bind.execute(
            conversation.update()
            .where(conversation.c.id == conversation_id)
            .values(sentiment=found)
        )
        if candidate_id is not None and (label := mood(found)) is not None:
            latest[candidate_id] = label
    for candidate_id, label in latest.items():
        bind.execute(
            candidate.update().where(candidate.c.id == candidate_id).values(sentiment=label)
        )


def _label(extraction: Any) -> str | None:
    if not isinstance(extraction, dict):
        return None
    label = extraction.get("sentiment")  # pyright: ignore[reportUnknownMemberType]
    return label if label in SEVERITY else None
