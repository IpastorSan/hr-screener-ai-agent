"""booking.interviewer_email; recruiters run video interviews only, up to 15 a day (SAZ-64)

In-person interviews move to the location managers: a booking keeps the manager's email, and
`onsite` leaves every recruiter's interview modes (one left with none gets `video`). The old
default daily maximum, 8, becomes the new one, 15; other values stay. Existing in-person
bookings are moved by `sazon migrate-onsite-bookings`, which needs the calendars. Downgrade
drops the column and leaves the recruiters as they are.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

recruiter = sa.table(
    "recruiter",
    sa.column("id", sa.String),
    sa.column("interview_modes", sa.JSON),
    sa.column("max_interviews_per_day", sa.Integer),
)
OLD_MAX, NEW_MAX = 8, 15


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("booking")}
    if "interviewer_email" not in columns:
        with op.batch_alter_table("booking") as batch:
            batch.add_column(
                sa.Column("interviewer_email", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
            )
    rows = bind.execute(
        sa.select(recruiter.c.id, recruiter.c.interview_modes, recruiter.c.max_interviews_per_day)
    ).all()
    for row_id, modes, most in rows:
        changes: dict[str, object] = {}
        if isinstance(modes, list) and "onsite" in modes:
            changes["interview_modes"] = [m for m in modes if m != "onsite"] or ["video"]
        if most == OLD_MAX:
            changes["max_interviews_per_day"] = NEW_MAX
        if changes:
            bind.execute(recruiter.update().where(recruiter.c.id == row_id).values(**changes))


def downgrade() -> None:
    with op.batch_alter_table("booking") as batch:
        batch.drop_column("interviewer_email")
