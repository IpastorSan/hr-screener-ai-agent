"""conversation.elevenlabs_conversation_id for voice calls

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Adoption stamps head now (`storage/migrate.py`); a database adopted at 0001 before
    # SAZ-55 may still come through here with the column, made by `create_all`.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("conversation")}
    if "elevenlabs_conversation_id" in columns:
        return
    with op.batch_alter_table("conversation", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "elevenlabs_conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation", schema=None) as batch_op:
        batch_op.drop_column("elevenlabs_conversation_id")
