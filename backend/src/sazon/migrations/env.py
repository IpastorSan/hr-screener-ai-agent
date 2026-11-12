"""Alembic environment. `sazon.storage.migrate` passes a connection in `config.attributes`;
the `alembic` CLI connects to `DATABASE_URL`.

SQLite alters run in batch mode (copy and swap the table), so one migration works on SQLite
now and on Postgres later.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine
from sqlmodel import SQLModel

import sazon.storage.models  # noqa: F401  (registers the tables on SQLModel.metadata)
from sazon.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )


def run_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection: Connection | None = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = create_engine(url)
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
