"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-19 22:24:37.881727
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("phone", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("outcome_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("quality_flags", sa.JSON(), nullable=False),
        sa.Column("sentiment", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("language", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("country", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("channel", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("location_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("late_start_days", sa.Integer(), nullable=True),
        sa.Column("closed_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("candidate", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_candidate_email"), ["email"], unique=False)
        batch_op.create_index(batch_op.f("ix_candidate_location_id"), ["location_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_candidate_phone"), ["phone"], unique=False)
        batch_op.create_index(batch_op.f("ix_candidate_status"), ["status"], unique=False)

    op.create_table(
        "interviewslot",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("location_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("starts_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("ends_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("booked_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "starts_at"),
    )
    with op.batch_alter_table("interviewslot", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_interviewslot_location_id"), ["location_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_interviewslot_starts_at"), ["starts_at"], unique=False)

    op.create_table(
        "candidatereview",
        sa.Column("candidate_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidate.id"],
        ),
        sa.PrimaryKeyConstraint("candidate_id"),
    )
    with op.batch_alter_table("candidatereview", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_candidatereview_status"), ["status"], unique=False)

    op.create_table(
        "conversation",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("candidate_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("channel", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("outcome", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("quality_flags", sa.JSON(), nullable=False),
        sa.Column("drop_off_stage", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("recovered_by_nudge", sa.Boolean(), nullable=False),
        sa.Column("started_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("ended_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("last_activity_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidate.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("conversation", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conversation_candidate_id"), ["candidate_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_conversation_started_at"), ["started_at"], unique=False
        )

    op.create_table(
        "booking",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("candidate_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("slot_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidate.id"],
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation.id"],
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"],
            ["interviewslot.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("booking", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_booking_candidate_id"), ["candidate_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_booking_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(
            "ix_booking_one_active",
            ["candidate_id"],
            unique=True,
            sqlite_where=sa.text("status = 'booked'"),
            postgresql_where=sa.text("status = 'booked'"),
        )
        batch_op.create_index(batch_op.f("ix_booking_slot_id"), ["slot_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_booking_status"), ["status"], unique=False)

    op.create_table(
        "faqmiss",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("question", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("language", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("country", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("faqmiss", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_faqmiss_conversation_id"), ["conversation_id"], unique=False
        )

    op.create_table(
        "summary",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("summary", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_summary_conversation_id"), ["conversation_id"], unique=True
        )

    op.create_table(
        "turn",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("ui", sa.JSON(), nullable=True),
        sa.Column("extraction", sa.JSON(), nullable=True),
        sa.Column("action", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("nudge", sa.Boolean(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("turn", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_turn_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_turn_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("turn", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_turn_created_at"))
        batch_op.drop_index(batch_op.f("ix_turn_conversation_id"))

    op.drop_table("turn")
    with op.batch_alter_table("summary", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_summary_conversation_id"))

    op.drop_table("summary")
    with op.batch_alter_table("faqmiss", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_faqmiss_conversation_id"))

    op.drop_table("faqmiss")
    with op.batch_alter_table("booking", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_booking_status"))
        batch_op.drop_index(batch_op.f("ix_booking_slot_id"))
        batch_op.drop_index(
            "ix_booking_one_active",
            sqlite_where=sa.text("status = 'booked'"),
            postgresql_where=sa.text("status = 'booked'"),
        )
        batch_op.drop_index(batch_op.f("ix_booking_conversation_id"))
        batch_op.drop_index(batch_op.f("ix_booking_candidate_id"))

    op.drop_table("booking")
    with op.batch_alter_table("conversation", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_conversation_started_at"))
        batch_op.drop_index(batch_op.f("ix_conversation_candidate_id"))

    op.drop_table("conversation")
    with op.batch_alter_table("candidatereview", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidatereview_status"))

    op.drop_table("candidatereview")
    with op.batch_alter_table("interviewslot", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_interviewslot_starts_at"))
        batch_op.drop_index(batch_op.f("ix_interviewslot_location_id"))

    op.drop_table("interviewslot")
    with op.batch_alter_table("candidate", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidate_status"))
        batch_op.drop_index(batch_op.f("ix_candidate_phone"))
        batch_op.drop_index(batch_op.f("ix_candidate_location_id"))
        batch_op.drop_index(batch_op.f("ix_candidate_email"))

    op.drop_table("candidate")
