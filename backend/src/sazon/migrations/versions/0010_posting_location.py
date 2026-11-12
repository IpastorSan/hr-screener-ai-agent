"""candidate.posting_location_id: a recruiter's scope for candidates with no location (QA-B5)

A candidate closed before the city question, or out of area, has no location, so a recruiter
scoped to their locations (SAZ-70) never saw them. The posting's location now places them.
Backfilled from the earliest conversation whose context names a known location.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

from sazon.engine.service_areas import get_location

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

candidate = sa.table(
    "candidate", sa.column("id", sa.String), sa.column("posting_location_id", sa.String)
)
conversation = sa.table(
    "conversation",
    sa.column("candidate_id", sa.String),
    sa.column("context", sa.JSON),
    sa.column("started_at", sa.DateTime),
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("candidate")}
    if "posting_location_id" not in columns:
        with op.batch_alter_table("candidate") as batch:
            batch.add_column(
                sa.Column("posting_location_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
            )
            batch.create_index(
                "ix_candidate_posting_location_id", ["posting_location_id"], unique=False
            )
    query = sa.select(conversation.c.candidate_id, conversation.c.context).order_by(
        conversation.c.started_at
    )
    found: dict[str, str] = {}
    for candidate_id, context in bind.execute(query).all():
        location_id = context.get("location_id") if isinstance(context, dict) else None
        if candidate_id is None or candidate_id in found or not isinstance(location_id, str):
            continue
        if get_location(location_id) is not None:
            found[candidate_id] = location_id
    for candidate_id, location_id in found.items():
        bind.execute(
            candidate.update()
            .where(candidate.c.id == candidate_id)
            .where(candidate.c.posting_location_id.is_(None))
            .values(posting_location_id=location_id)
        )


def downgrade() -> None:
    with op.batch_alter_table("candidate") as batch:
        batch.drop_index("ix_candidate_posting_location_id")
        batch.drop_column("posting_location_id")
