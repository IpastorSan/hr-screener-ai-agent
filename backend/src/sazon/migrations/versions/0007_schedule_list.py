"""profile.schedule becomes a list: "morning" -> ["morning"] (SAZ-41)

The schedule lives in JSON (candidate.profile and conversation.state.profile), so there is no
column to change: a single stored value becomes a one-element list. Reads tolerate old rows too
(`CandidateProfile` wraps a string), so the order of deploy and migration does not matter.
Downgrade keeps the first block.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20 18:00:00.000000
"""

from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

candidate = sa.table("candidate", sa.column("id", sa.String), sa.column("profile", sa.JSON))
conversation = sa.table("conversation", sa.column("id", sa.String), sa.column("state", sa.JSON))


def upgrade() -> None:
    _rewrite(op.get_bind(), _to_list)


def downgrade() -> None:
    _rewrite(op.get_bind(), _to_single)


def _to_list(value: Any) -> Any:
    return [value] if isinstance(value, str) else value


def _to_single(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _profile(profile: Any, convert: Callable[[Any], Any]) -> Any:
    if not isinstance(profile, dict) or profile.get("schedule") is None:
        return None
    changed = convert(profile["schedule"])
    if changed == profile["schedule"]:
        return None
    return {**profile, "schedule": changed}


def _rewrite(bind: sa.Connection, convert: Callable[[Any], Any]) -> None:
    for row_id, profile in bind.execute(sa.select(candidate.c.id, candidate.c.profile)).all():
        updated = _profile(profile, convert)
        if updated is not None:
            bind.execute(candidate.update().where(candidate.c.id == row_id).values(profile=updated))
    for row_id, state in bind.execute(sa.select(conversation.c.id, conversation.c.state)).all():
        if not isinstance(state, dict):
            continue
        updated = _profile(state.get("profile"), convert)
        if updated is not None:
            bind.execute(
                conversation.update()
                .where(conversation.c.id == row_id)
                .values(state={**state, "profile": updated})
            )
