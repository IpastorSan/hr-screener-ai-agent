"""conversation.state carries its phase: the stage and its flags become `phase` (SAZ-91)

The engine's state lives in JSON, so there is no column to change: every stored state is
rewritten in version 2 (`engine/upgrade.py`), a booked interview as `Interview.Booked`. The
app reads version 1 as well until the reader goes, so the order of deploy and migration does not
matter. A state whose flags no phase holds stops the migration, every such row named, before
anything is written. Downgrade writes the phases back as flags.

Uses the version 1 reader: once it is removed, a database still below this revision upgrades
through a release that has it.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-31 12:00:00.000000
"""

from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from pydantic_core import to_jsonable_python

from sazon.engine.state import ConversationState
from sazon.engine.upgrade import downgrade as v1_state
from sazon.engine.upgrade import is_v1
from sazon.engine.upgrade import upgrade as v2_state

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

conversation = sa.table("conversation", sa.column("id", sa.String), sa.column("state", sa.JSON))


def upgrade() -> None:
    _rewrite(op.get_bind(), is_v1, _to_v2)


def downgrade() -> None:
    _rewrite(op.get_bind(), lambda state: not is_v1(state), v1_state)


def _to_v2(state: dict[str, Any]) -> dict[str, Any]:
    upgraded = to_jsonable_python(v2_state(state))
    ConversationState.model_validate(upgraded)  # the rest of it must read too
    return upgraded


class StoredStateError(ValueError):
    """Stored states the migration cannot rewrite, by conversation id."""


def _rewrite(
    bind: sa.Connection,
    due: Callable[[dict[str, Any]], bool],
    convert: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    rewritten: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for row_id, state in bind.execute(sa.select(conversation.c.id, conversation.c.state)).all():
        if not isinstance(state, dict) or not due(state):
            continue  # not a state any version reads, or already rewritten
        try:
            rewritten[row_id] = convert(state)
        except ValueError as exc:
            problems.append(f"{row_id}: {exc}")
    if problems:
        raise StoredStateError(
            f"{len(problems)} stored states no phase holds:\n" + "\n".join(problems)
        )
    for row_id, state in rewritten.items():
        bind.execute(conversation.update().where(conversation.c.id == row_id).values(state=state))
