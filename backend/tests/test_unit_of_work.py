"""One read and one write per turn (SAZ-84): statement counts, nothing stored by a turn that
fails, no database call on the event loop, many conversations at once, and the advisory lock
on Postgres.

The Postgres cases run when `SAZON_TEST_POSTGRES_URL` names a database they may wipe, e.g.
`postgresql+psycopg://postgres:pw@127.0.0.1:5432/sazon`; they are skipped otherwise."""

import asyncio
import logging
import os
import threading
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel

import sazon.storage.repo
from loop_guard import LOOP_STATEMENTS
from sazon.calendar.fake import FakeCalendar
from sazon.engine import ConversationContext
from sazon.engine.models import Outcome, PhoneInput
from sazon.llm.interface import Channel
from sazon.llm.offline import OfflineLLM
from sazon.recruiters import seed_team
from sazon.scheduling.service import SchedulingConfig
from sazon.service import ScreeningService, WidgetAnswer
from sazon.storage.models import Conversation, Summary
from sazon.storage.repo import UNIT_OPEN, Repo, init_db, make_engine, state_of

NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
ES = ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES")
POSTGRES_URL = os.environ.get("SAZON_TEST_POSTGRES_URL", "")
FIRST = ["Lucía", "Marta", "Jorge", "Nuria", "Pablo", "Elena", "Diego", "Sara", "Raúl", "Irene"]
LAST = ["García", "Ruiz", "Campos", "Vidal", "Ortega", "Navarro", "Molina", "Serrano", "Blanco"]
BACKENDS = ["memory", "file", pytest.param("postgres", marks=pytest.mark.xdist_group("postgres"))]


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def name(i: int) -> str:
    return f"{FIRST[i % 10]} {LAST[(i // 10) % 9]} {LAST[(i // 90) % 9]}"


def journey(i: int) -> list[str | WidgetAnswer]:
    """Consent to the summary's yes: qualified, asked for the interview mode."""
    return [
        WidgetAnswer(value="yes", kind="quick_replies"),
        name(i),
        WidgetAnswer(value=PhoneInput(country="ES", number=f"61234{i:04d}"), kind="phone"),
        f"persona{i}@example.com",
        "sí",
        "Lavapiés",
        "tiempo completo",
        "tardes",
        "2",
        "Glovo",
        "2026-10-26",
        WidgetAnswer(value="yes", kind="confirm"),
    ]


def engine_for(backend: str, tmp_path: Path) -> Engine:
    if backend == "memory":
        engine = make_engine("sqlite://")
    elif backend == "file":
        engine = make_engine(f"sqlite:///{tmp_path / 'sazon.db'}")
    else:
        engine = make_engine(fresh_postgres())
    init_db(engine)
    return engine


def fresh_postgres() -> str:
    if not POSTGRES_URL:
        pytest.skip("SAZON_TEST_POSTGRES_URL is not set")
    admin = create_engine(POSTGRES_URL)
    with admin.begin() as connection:
        # A failed test can leave a unit's transaction open, holding its locks.
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    admin.dispose()
    return POSTGRES_URL


@pytest.fixture(params=BACKENDS)
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    engine = engine_for(request.param, tmp_path)
    yield engine
    engine.dispose()


def service_on(engine: Engine, clock: Clock | None = None, llm: Any = None) -> ScreeningService:
    repo = Repo(engine)
    seed_team(repo, NOW, with_passwords=False)
    return ScreeningService(
        llm or OfflineLLM(),
        repo,
        clock=clock or Clock(),
        scheduling_config=SchedulingConfig(calendar=FakeCalendar()),
    )


class Meter:
    """Statements by verb and commits on one engine, and the threads they ran on."""

    def __init__(self, engine: Engine) -> None:
        self.counts: Counter[str] = Counter()
        self.threads: set[int] = set()
        event.listen(engine, "before_cursor_execute", self._statement)
        event.listen(engine, "commit", self._commit)

    def _statement(self, *args: Any, **_kwargs: Any) -> None:
        statement: str = args[2]
        self.counts[statement.split()[0].upper()] += 1
        self.counts["statements"] += 1
        self.threads.add(threading.get_ident())

    def _commit(self, *_args: Any) -> None:
        self.counts["commits"] += 1

    def reset(self) -> None:
        self.counts.clear()
        self.threads.clear()


def dump(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table, in key order."""
    with engine.connect() as connection:
        return {
            table.name: [
                tuple(row)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).all()
            ]
            for table in SQLModel.metadata.sorted_tables
        }


# --- one read, one write ---


async def test_a_field_turn_reads_once_and_writes_once():
    """The acceptance criterion: at most 6 statements and 1 commit for a typical field turn,
    on an in-memory engine. Before SAZ-84: 13 statements in 4 commits."""
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = service_on(engine)
    meter = Meter(engine)

    reply = await svc.start(Channel.WEB, ES)
    assert meter.counts == {"INSERT": 2, "statements": 2, "commits": 1}  # was 4 and 2
    cid = reply.conversation_id

    meter.reset()
    await svc.handle_turn(cid, WidgetAnswer(value="yes", kind="quick_replies"))
    assert meter.counts["statements"] <= 6  # was 14
    assert meter.counts["commits"] == 1  # was 5

    meter.reset()
    await svc.handle_turn(cid, name(0))
    assert meter.counts == {
        "SELECT": 2,  # the conversation with its candidate, and its turns
        "INSERT": 2,  # the candidate's turn and Ana's
        "UPDATE": 2,  # the conversation and the candidate
        "statements": 6,  # was 13
        "commits": 1,  # was 4
    }

    meter.reset()
    phone = WidgetAnswer(value=PhoneInput(country="ES", number="612340000"), kind="phone")
    await svc.handle_turn(cid, phone)
    assert meter.counts["SELECT"] == 4  # plus the duplicate check's two lookups
    assert meter.counts["statements"] <= 9  # was 16
    assert meter.counts["commits"] == 1  # was 5

    meter.reset()
    await svc.nudge(cid, 1)
    assert meter.counts["statements"] <= 4  # was 6
    assert meter.counts["commits"] == 1  # was 2


async def test_a_voice_turn_reads_once_and_writes_once():
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = service_on(engine)
    reply = await svc.start(Channel.VOICE, ES)
    cid = reply.conversation_id
    await svc.handle_turn(cid, "Sí", channel=Channel.VOICE)
    meter = Meter(engine)

    await svc.handle_turn(cid, name(0), channel=Channel.VOICE)

    assert meter.counts["statements"] <= 6  # was 13
    assert meter.counts["commits"] == 1  # was 4


async def test_no_turn_runs_a_statement_on_the_event_loop(engine):
    """Every statement of a whole journey, nudges, restart and resume included, runs in a
    worker thread. (The suite-wide guard in conftest checks the same for every test.)"""
    svc = service_on(engine)
    meter = Meter(engine)
    loop_thread = threading.get_ident()

    reply = await svc.start(Channel.WEB, ES)
    cid = reply.conversation_id
    for message in journey(1)[:4]:
        await svc.handle_turn(cid, message)
    await svc.nudge(cid, 1)
    for message in journey(1)[4:]:
        await svc.handle_turn(cid, message)
    await svc.resume_if_idle(cid)
    await svc.reoffer_if_pending(cid)
    await svc.restart(cid)
    await svc.close_stale()

    assert meter.counts["statements"] > 50
    assert loop_thread not in meter.threads


async def test_the_guard_catches_a_statement_on_the_event_loop():
    """The suite-wide guard (conftest) would fail a test that queried on the loop in a unit."""
    engine = make_engine("sqlite://")
    init_db(engine)
    repo = Repo(engine)
    token = UNIT_OPEN.set(True)
    try:
        repo.get_conversation("nope")  # on the loop, in a unit: caught
    finally:
        UNIT_OPEN.reset(token)
    assert len(LOOP_STATEMENTS) == 1
    assert LOOP_STATEMENTS[0].startswith("SELECT conversation.id")
    LOOP_STATEMENTS.clear()


# --- a turn that fails stores nothing ---


class FailingLLM(OfflineLLM):
    """Raises in one model call, once."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_in: str | None = None
        self.delay = 0.0

    def _maybe_fail(self, where: str) -> None:
        if self.fail_in == where:
            self.fail_in = None
            raise RuntimeError(f"model down in {where}")

    async def extract(self, history, state, message, *, today=None):  # type: ignore[override]
        self._maybe_fail("extract")
        return await super().extract(history, state, message, today=today)

    async def respond(self, action, ctx):  # type: ignore[override]
        if self.delay:
            await asyncio.sleep(self.delay)
        self._maybe_fail("respond")
        return await super().respond(action, ctx)

    async def summarize(self, *args, **kwargs):  # type: ignore[override]
        self._maybe_fail("summarize")
        return await super().summarize(*args, **kwargs)


async def at_turn(svc: ScreeningService, i: int, turns: int) -> str:
    reply = await svc.start(Channel.WEB, ES)
    for message in journey(i)[:turns]:
        reply = await svc.handle_turn(reply.conversation_id, message)
    return reply.conversation_id


@pytest.mark.parametrize(
    ("where", "turns"),
    [
        ("extract", 1),  # a typed answer: the extractor fails
        ("respond", 0),  # consent: the candidate is created, then the reply fails
        ("respond", 3),  # a typed email: the duplicate check ran, then the reply fails
        ("summarize", 11),  # the summary's yes: qualified, then the summary fails
    ],
)
async def test_a_model_failure_leaves_the_database_as_it_was(engine, where, turns):
    llm = FailingLLM()
    svc = service_on(engine, llm=llm)
    cid = await at_turn(svc, 2, turns)
    before = dump(engine)

    llm.fail_in = where
    reply = await svc.handle_turn(cid, journey(2)[turns])

    assert llm.fail_in is None  # it did fail
    assert dump(engine) == before
    assert reply.text  # the pending question again, from templates
    # The candidate sends it again, and this time it goes through.
    again = await svc.handle_turn(cid, journey(2)[turns])
    assert dump(engine) != before
    assert again.text != reply.text


async def test_a_failure_between_the_read_and_the_write_leaves_the_database_as_it_was(
    engine, monkeypatch
):
    svc = service_on(engine)
    cid = await at_turn(svc, 3, 2)
    before = dump(engine)

    def broken(self):  # the snapshot is read, the models answered, nothing written yet
        raise RuntimeError("process died")

    monkeypatch.setattr(sazon.storage.repo.Unit, "commit", broken)
    await svc.handle_turn(cid, journey(3)[2])

    assert dump(engine) == before


@pytest.mark.parametrize(
    "statement", ["UPDATE conversation", "UPDATE candidate", "INSERT INTO turn"]
)
async def test_a_failure_in_the_middle_of_the_write_leaves_the_database_as_it_was(
    engine, statement
):
    """The write fails after some of its statements went out: the transaction rolls back."""
    svc = service_on(engine)
    cid = await at_turn(svc, 4, 4)
    before = dump(engine)
    seen: list[str] = []

    def fail_on(_conn, _cursor, sql: str, *_args: Any) -> None:
        seen.append(sql)
        if sql.startswith(statement):
            raise RuntimeError("disk full")

    event.listen(engine, "before_cursor_execute", fail_on)
    try:
        reply = await svc.handle_turn(cid, journey(4)[4])
    finally:
        event.remove(engine, "before_cursor_execute", fail_on)

    writes = [sql for sql in seen if sql.split()[0] in ("INSERT", "UPDATE")]
    assert writes
    assert writes[-1].startswith(statement)
    assert dump(engine) == before
    assert reply.text


async def test_a_turn_cancelled_mid_flight_leaves_the_database_as_it_was(engine):
    """A client that goes away cancels the turn (SSE): nothing is stored, and the lock and
    the connection are free for the next one."""
    llm = FailingLLM()
    svc = service_on(engine, llm=llm)
    cid = await at_turn(svc, 5, 2)
    before = dump(engine)

    llm.delay = 0.5
    task = asyncio.create_task(svc.handle_turn(cid, journey(5)[2]))
    await asyncio.sleep(0.1)  # in the responder
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert dump(engine) == before
    llm.delay = 0.0
    reply = await asyncio.wait_for(svc.handle_turn(cid, journey(5)[2]), timeout=10)
    assert "correo" in reply.text


async def test_a_declined_consent_deletes_and_rewrites_in_order(engine):
    """Writes go out in the order they were made, the conversation before the candidate it no
    longer points at: Postgres checks the foreign key on each statement."""
    svc = service_on(engine)
    reply = await svc.start(Channel.WEB, ES)
    reply = await svc.handle_turn(reply.conversation_id, WidgetAnswer(value="no"))

    assert reply.outcome == Outcome.DECLINED_CONSENT
    data = dump(engine)
    assert data["turn"] == []
    assert data["candidate"] == []


async def test_a_reopened_application_gets_its_summary_written_again_in_one_unit(engine):
    repo = Repo(engine)
    svc = service_on(engine)
    cid = await at_turn(svc, 6, 12)  # qualified: a summary exists
    assert repo.summary(cid) is not None
    old = repo.summary(cid)

    unit = repo.unit(cid)
    unit.begin()
    unit.delete_summary(cid)
    assert unit.has_summary(cid) is False
    unit.save(Summary(conversation_id=cid, body={"new": True}, created_at=NOW))
    assert unit.has_summary(cid) is True
    unit.commit()
    unit.close()

    new = repo.summary(cid)
    assert new is not None
    assert old is not None
    assert new.body == {"new": True}


# --- many at once ---


async def test_many_conversations_at_once_lose_nothing(engine, caplog):
    """50 full journeys at once on one service: every turn is stored once, in order, in its
    own conversation, with no failed turn (no "database is locked" on a SQLite file)."""
    svc = service_on(engine)
    count = 50

    async def play(i: int) -> str:
        reply = await svc.start(Channel.WEB, ES)
        for message in journey(i):
            reply = await svc.handle_turn(reply.conversation_id, message)
        return reply.conversation_id

    with caplog.at_level(logging.ERROR, logger="sazon.service"):
        ids = await asyncio.gather(*(play(i) for i in range(count)))

    assert not [r for r in caplog.records if "turn failed" in r.getMessage()]
    repo = Repo(engine)
    for i, cid in enumerate(ids):
        conversation = repo.get_conversation(cid)
        assert conversation is not None
        state = state_of(conversation)
        assert state.outcome == Outcome.QUALIFIED, i
        assert state.profile.full_name == name(i)
        assert state.profile.email == f"persona{i}@example.com"
        candidate = repo.get_candidate(conversation.candidate_id or "")
        assert candidate is not None
        assert candidate.email == f"persona{i}@example.com"
        shown = [turn.role for turn in repo.shown_turns(cid)]
        assert shown == ["agent"] + ["candidate", "agent"] * len(journey(i))
        assert repo.summary(cid) is not None


class SlowReplies(OfflineLLM):
    async def respond(self, action, ctx):  # type: ignore[override]
        await asyncio.sleep(0.05)
        return await super().respond(action, ctx)


async def test_two_turns_of_one_conversation_never_interleave(engine):
    svc = service_on(engine, llm=SlowReplies())
    cid = await at_turn(svc, 7, 1)

    await asyncio.gather(svc.handle_turn(cid, name(7)), svc.handle_turn(cid, "612347777"))

    turns = list(Repo(engine).shown_turns(cid))
    assert [t.role for t in turns] == ["agent"] + ["candidate", "agent"] * 3
    conversation = Repo(engine).get_conversation(cid)
    assert conversation is not None
    profile = state_of(conversation).profile
    assert profile.full_name == name(7)
    assert profile.phone == "+34612347777"


# --- Postgres: the advisory lock ---


pg = pytest.mark.xdist_group("postgres")


@pg
async def test_the_advisory_lock_serializes_one_conversation_across_processes():
    """Two services on two engines are two API instances: their in-process locks don't see
    each other, the advisory lock does. Two turns of one conversation, one on each, at once:
    they run one after the other, and the second reads what the first stored."""
    url = fresh_postgres()
    first_engine, second_engine = make_engine(url), make_engine(url)
    init_db(first_engine)
    first = service_on(first_engine, llm=SlowReplies())
    second = ScreeningService(
        SlowReplies(),
        Repo(second_engine),
        clock=first.clock,
        scheduling_config=first.scheduling,
    )
    cid = await at_turn(first, 8, 1)

    await asyncio.gather(first.handle_turn(cid, name(8)), second.handle_turn(cid, "612348888"))

    repo = Repo(first_engine)
    assert [t.role for t in repo.shown_turns(cid)] == [
        "agent",
        "candidate",
        "agent",
        "candidate",
        "agent",
        "candidate",
        "agent",
    ]
    conversation = repo.get_conversation(cid)
    assert conversation is not None
    profile = state_of(conversation).profile
    assert profile.full_name == name(8)
    assert profile.phone == "+34612348888"
    first_engine.dispose()
    second_engine.dispose()


@pg
def test_a_second_unit_waits_for_the_first_to_commit():
    url = fresh_postgres()
    first_engine, second_engine = make_engine(url), make_engine(url)
    init_db(first_engine)
    now = NOW
    conversation = Repo(first_engine).save(
        Conversation(channel="web", state={}, started_at=now, last_activity_at=now)
    )
    other = Repo(first_engine).save(
        Conversation(channel="web", state={}, started_at=now, last_activity_at=now)
    )
    holder = Repo(first_engine).unit(conversation.id)
    assert holder.begin() is not None
    seen: dict[str, Any] = {}

    def second_turn() -> None:
        started = time.monotonic()
        unit = Repo(second_engine).unit(conversation.id)
        root = unit.begin()
        seen["waited"] = time.monotonic() - started
        seen["channel"] = root.channel if root else None
        unit.close()

    waiting = threading.Thread(target=second_turn)
    waiting.start()
    # Another conversation is not held up.
    started = time.monotonic()
    free = Repo(second_engine).unit(other.id)
    assert free.begin() is not None
    assert time.monotonic() - started < 0.5
    free.close()

    time.sleep(0.6)
    assert waiting.is_alive()  # still waiting on the lock
    assert holder.root is not None
    holder.root.channel = "voice"
    holder.save(holder.root)
    holder.commit()
    holder.close()
    waiting.join(10)

    assert seen["waited"] >= 0.6
    assert seen["channel"] == "voice"  # it read what the first committed
    first_engine.dispose()
    second_engine.dispose()


@pg
def test_a_turn_stuck_behind_another_gives_up_at_the_lock_timeout(monkeypatch):
    url = fresh_postgres()
    first_engine, second_engine = make_engine(url), make_engine(url)
    init_db(first_engine)
    conversation = Repo(first_engine).save(
        Conversation(channel="web", state={}, started_at=NOW, last_activity_at=NOW)
    )
    monkeypatch.setattr(sazon.storage.repo, "LOCK_TIMEOUT", "300ms")
    holder = Repo(first_engine).unit(conversation.id)
    holder.begin()

    stuck = Repo(second_engine).unit(conversation.id)
    started = time.monotonic()
    with pytest.raises(OperationalError, match="lock timeout"):
        stuck.begin()
    assert 0.25 < time.monotonic() - started < 5
    stuck.close()
    holder.close()
    # The lock goes with the transaction: a new unit gets it at once.
    again = Repo(second_engine).unit(conversation.id)
    assert again.begin() is not None
    again.close()
    first_engine.dispose()
    second_engine.dispose()


@pg
async def test_a_process_that_dies_mid_turn_releases_the_lock():
    """Its connection closes and Postgres ends the transaction: the next turn proceeds and
    finds nothing of the dead one."""
    url = fresh_postgres()
    engine = make_engine(url)
    init_db(engine)
    svc = service_on(engine)
    cid = await at_turn(svc, 9, 1)
    before = dump(engine)

    dead = make_engine(url)
    unit = Repo(dead).unit(cid)
    root = unit.begin()
    assert root is not None
    root.channel = "voice"
    unit.save(root)
    unit.session.add(root)
    unit.session.flush()  # a write sent, never committed
    pid = unit.session.execute(text("SELECT pg_backend_pid()")).scalar_one()
    with engine.connect() as connection:  # the process dies: its connection drops
        connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    unit.session.invalidate()  # what is left of the dead process's session

    reply = await asyncio.wait_for(svc.handle_turn(cid, name(9)), timeout=10)
    assert "móvil" in reply.text
    after = dump(engine)
    assert after["conversation"] != before["conversation"]
    conversation = Repo(engine).get_conversation(cid)
    assert conversation is not None
    assert conversation.channel == "web"
    dead.dispose()
    engine.dispose()
