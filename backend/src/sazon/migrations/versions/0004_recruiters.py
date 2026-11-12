"""recruiter profiles; booking.recruiter_id, mode, meet_url and Google Calendar ids

Existing bookings are interviews at a kitchen, so their mode is `onsite`; they stay unassigned
until `sazon seed` (or an admin) assigns them.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20 10:01:17.265200
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Adoption stamps head now (`storage/migrate.py`); a database adopted at 0001 before
    # SAZ-55 may still come through here with all of this, made by `create_all`.
    if "recruiter" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "recruiter",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("country", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("timezone", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("location_ids", sa.JSON(), nullable=False),
        sa.Column("interview_modes", sa.JSON(), nullable=False),
        sa.Column("working_hours", sa.JSON(), nullable=False),
        sa.Column("max_interviews_per_day", sa.Integer(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("recruiter", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_recruiter_email"), ["email"], unique=True)

    with op.batch_alter_table("booking", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("recruiter_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(
            sa.Column("meet_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("google_event_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("google_calendar_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.create_index(batch_op.f("ix_booking_recruiter_id"), ["recruiter_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_booking_recruiter_id_recruiter", "recruiter", ["recruiter_id"], ["id"]
        )

    op.execute("UPDATE booking SET mode = 'onsite' WHERE mode IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("booking", schema=None) as batch_op:
        batch_op.drop_constraint("fk_booking_recruiter_id_recruiter", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_booking_recruiter_id"))
        batch_op.drop_column("google_calendar_id")
        batch_op.drop_column("google_event_id")
        batch_op.drop_column("meet_url")
        batch_op.drop_column("mode")
        batch_op.drop_column("recruiter_id")

    with op.batch_alter_table("recruiter", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_recruiter_email"))

    op.drop_table("recruiter")
