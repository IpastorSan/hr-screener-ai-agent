"""recruiter.scope: who interviews whom beyond the location (SAZ-97)

A teacher is interviewed by the head of department of their subject at their campus: a
recruiter covers candidates by the answers to the agent's `interview.scope` fields as well as
by location. Existing recruiters cover every answer (`{}`).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-31 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

recruiter = sa.table("recruiter", sa.column("scope", sa.JSON))


def upgrade() -> None:
    with op.batch_alter_table("recruiter") as batch:
        batch.add_column(sa.Column("scope", sa.JSON(), nullable=True))
    op.get_bind().execute(recruiter.update().values(scope={}))
    with op.batch_alter_table("recruiter") as batch:
        batch.alter_column("scope", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("recruiter") as batch:
        batch.drop_column("scope")
