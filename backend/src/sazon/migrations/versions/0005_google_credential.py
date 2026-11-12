"""google_credential: a recruiter's encrypted Google Calendar grant

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20 10:36:51.454751
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Adoption stamps head now (`storage/migrate.py`); a database adopted at 0001 before
    # SAZ-55 may still come through here with the table, made by `create_all`.
    if "googlecredential" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "googlecredential",
        sa.Column("recruiter_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("google_email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("google_subject", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("calendar_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("refresh_token_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("connected_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recruiter_id"],
            ["recruiter.id"],
        ),
        sa.PrimaryKeyConstraint("recruiter_id"),
    )


def downgrade() -> None:
    op.drop_table("googlecredential")
