"""candidate.talent_pool: the recruiter list's "Bolsa de candidatos" filter (SAZ-42)

Until now the opt-in lived only in `conversation.state.talent_pool_opt_in`. Backfilled from the
conversations closed out of area with a yes; the yes to "new interview dates" (no slot suited)
asked something else and is not carried over.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

candidate = sa.table("candidate", sa.column("id", sa.String), sa.column("talent_pool", sa.Boolean))
conversation = sa.table(
    "conversation", sa.column("candidate_id", sa.String), sa.column("state", sa.JSON)
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("candidate")}
    # Adoption stamps head now (`storage/migrate.py`); a database adopted at 0001 before
    # SAZ-55 may still come through here with the column, made by `create_all`.
    if "talent_pool" not in columns:
        with op.batch_alter_table("candidate") as batch:
            batch.add_column(
                sa.Column("talent_pool", sa.Boolean(), nullable=False, server_default=sa.false())
            )
    rows = bind.execute(sa.select(conversation.c.candidate_id, conversation.c.state)).all()
    for candidate_id, state in rows:
        if candidate_id is not None and _opted_in(state):
            bind.execute(
                candidate.update().where(candidate.c.id == candidate_id).values(talent_pool=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("candidate") as batch:
        batch.drop_column("talent_pool")


def _opted_in(state: object) -> bool:
    return (
        isinstance(state, dict)
        and state.get("disqualify_reason") == "out_of_area"
        and state.get("talent_pool_opt_in") is True
    )
