"""Catches a database statement run on the event loop's thread while a unit of work is open
(SAZ-84). `conftest.no_database_on_the_event_loop` turns it on for every test."""

import asyncio

from sqlalchemy import Engine, event

from sazon.storage.repo import UNIT_OPEN

LOOP_STATEMENTS: list[str] = []
"""The statements caught since the last `clear`, first line, cut to 120 characters."""


def _on_loop_in_unit(
    _conn: object, _cursor: object, statement: str, *_args: object, **_kwargs: object
) -> None:
    if not UNIT_OPEN.get():
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # a worker thread: no loop runs here
    LOOP_STATEMENTS.append(statement.split("\n")[0][:120])


def install() -> None:
    """Listen on every engine, once."""
    if not event.contains(Engine, "before_cursor_execute", _on_loop_in_unit):
        event.listen(Engine, "before_cursor_execute", _on_loop_in_unit)
