"""calendarchannel: Google Calendar push channels; booking.cancelled_by and calendar_issue

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20 16:02:11.180364
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Adoption stamps head now (`storage/migrate.py`); a database adopted at 0001 before
    # SAZ-55 may still come through here with all of this, made by `create_all`.
    inspector = sa.inspect(op.get_bind())
    if "calendarchannel" not in inspector.get_table_names():
        op.create_table(
            "calendarchannel",
            sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("recruiter_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("calendar_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("resource_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("expires_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
            sa.Column("sync_token", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
            sa.ForeignKeyConstraint(["recruiter_id"], ["recruiter.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("calendarchannel", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_calendarchannel_recruiter_id"), ["recruiter_id"], unique=False
            )
    columns = {column["name"] for column in inspector.get_columns("booking")}
    if "cancelled_by" not in columns:
        with op.batch_alter_table("booking", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("cancelled_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("calendar_issue", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("booking", schema=None) as batch_op:
        batch_op.drop_column("calendar_issue")
        batch_op.drop_column("cancelled_by")
    with op.batch_alter_table("calendarchannel", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_calendarchannel_recruiter_id"))
    op.drop_table("calendarchannel")
