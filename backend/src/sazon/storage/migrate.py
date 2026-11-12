"""Schema migrations with Alembic. Revisions live in `sazon/migrations/versions`.

The API and `sazon migrate` bring a database to the latest revision on start. A database made
before migrations existed (by `create_all`) is adopted: stamped at the latest revision when it
matches the models, after creating any index it lacks. (Databases adopted before SAZ-55 were
stamped at 0001 and walked up, which is why 0002 to 0008 check for what already exists.)

New revision after a model change: `cd backend && uv run alembic revision --autogenerate -m "..."`.
"""

from functools import cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, Index, inspect
from sqlmodel import SQLModel

import sazon.storage.models  # noqa: F401  (registers the tables)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
VERSION_TABLE = "alembic_version"


class SchemaMismatch(RuntimeError):
    """The database was not made by these migrations and does not match the models."""


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


@cache  # the scripts do not change while the process runs; /readyz asks on every probe
def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def schema_diff(connection: Connection) -> list[Any]:
    """What autogenerate would change to make the database match the models; empty when equal."""
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return compare_metadata(context, SQLModel.metadata)


def migrate(engine: Engine, revision: str = "head") -> str | None:
    """Upgrade to `revision`. Returns the revision the database is at afterwards."""
    config = alembic_config()
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        if VERSION_TABLE not in tables and "conversation" in tables:
            _adopt(connection)
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
    return current_revision(engine)


def _adopt(connection: Connection) -> None:
    """Stamp a pre-migration database at head if it only lacks indexes: it then matches the
    models, so there is nothing to upgrade (and no backfill to run)."""
    diff = schema_diff(connection)
    missing = [entry[1] for entry in diff if entry[0] == "add_index"]
    other = [entry for entry in diff if entry[0] != "add_index"]
    if other:
        raise SchemaMismatch(
            "This database predates migrations and differs from the models "
            f"({len(other)} differences, e.g. {other[0]!r}). Back it up and recreate it "
            "(`sazon seed --reset` for demo data)."
        )
    for index in missing:
        if isinstance(index, Index):
            index.create(connection)
    config = alembic_config()
    config.attributes["connection"] = connection
    command.stamp(config, "head")
