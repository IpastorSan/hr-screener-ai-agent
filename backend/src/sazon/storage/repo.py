import contextlib
import threading
from collections.abc import Callable, Iterator, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, Executable, event, func, text
from sqlalchemy.pool import QueuePool, StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, delete, or_, select

from sazon.config import get_settings
from sazon.engine.state import ConversationState, DisqualifyReason
from sazon.storage.models import (
    RESTARTED,
    Candidate,
    CandidateStatus,
    Conversation,
    FaqMiss,
    Summary,
    Turn,
    TurnRole,
)

REAPPLY_AFTER = timedelta(days=30)
LOCK_TIMEOUT = "30s"
"""How long a turn waits on Postgres for another turn of its conversation (`Unit.begin`)."""
UNIT_HEADROOM = 3
"""Pooled connections a process keeps out of its units' reach on Postgres (`Repo.max_units`)."""
UNIT_OPEN: ContextVar[bool] = ContextVar("sazon_unit_open", default=False)
"""True while a `Unit` is open in this context: every database call must then run in a thread,
never on the event loop. The tests fail any statement that breaks this."""
OPEN_STATUSES = (CandidateStatus.IN_PROGRESS, CandidateStatus.QUALIFIED, CandidateStatus.ABANDONED)


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)
    if url in ("sqlite://", "sqlite:///:memory:"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})
    event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def _sqlite_pragmas(connection: object, _record: object) -> None:
    """WAL lets the dashboards read while a chat turn writes; online backups work with it."""
    cursor = connection.cursor()  # pyright: ignore[reportAttributeAccessIssue]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def is_in_memory(engine: Engine) -> bool:
    return engine.url.get_backend_name() == "sqlite" and engine.url.database in (
        None,
        "",
        ":memory:",
    )


def init_db(engine: Engine) -> None:
    """Bring the schema up to date: Alembic migrations, or `create_all` for in-memory test
    databases."""
    if is_in_memory(engine):
        SQLModel.metadata.create_all(engine)
        return
    from sazon.storage.migrate import migrate

    migrate(engine)


def to_utc(moment: datetime) -> datetime:
    """Aware UTC; a naive datetime is taken as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def state_of(conversation: Conversation) -> ConversationState:
    """The engine's state of a conversation, parsed from its JSON column."""
    return ConversationState.model_validate(conversation.state)


class Repo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # An in-memory database is one connection shared by every thread (`StaticPool`): units
        # take turns on it, or one unit's rollback would undo another's writes.
        self.serial: contextlib.AbstractContextManager[Any] = (
            threading.RLock() if is_in_memory(engine) else contextlib.nullcontext()
        )

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    @property
    def max_units(self) -> int | None:
        """How many units one process may hold open at once on Postgres, where a unit keeps
        its connection for the whole turn: the pool less `UNIT_HEADROOM`, kept for the
        scheduling writes that commit on their own while their unit waits, and for requests
        outside a turn. Without it, units holding every connection would wait on each other's
        bookings until the pool timed out. None (no limit) on SQLite, where a unit holds a
        connection only while it reads or writes."""
        pool = self.engine.pool
        if self.engine.dialect.name != "postgresql" or not isinstance(pool, QueuePool):
            return None
        return max(1, pool.size() + getattr(pool, "_max_overflow", 0) - UNIT_HEADROOM)

    @contextlib.contextmanager
    def _using(self, session: Session | None) -> Iterator[Session]:
        """`session` when given (a unit's), else a new one closed afterwards."""
        if session is not None:
            yield session
            return
        with self.session() as own:
            yield own

    def unit(self, conversation_id: str | None = None) -> "Unit":
        """A unit of work for one turn of `conversation_id` (None for a new conversation).
        Nothing is read until `Unit.begin`."""
        return Unit(self, conversation_id)

    def save[T: SQLModel](self, row: T) -> T:
        """Insert or update `row`. A row loaded in another session is re-attached and only the
        attributes changed since are written, so a save outside a lock does not overwrite
        columns someone else changed meanwhile."""
        with self.session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self.session() as session:
            return session.get(Conversation, conversation_id)

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        with self.session() as session:
            return session.get(Candidate, candidate_id)

    def turns(self, conversation_id: str, *, session: Session | None = None) -> Sequence[Turn]:
        with self._using(session) as session:
            query = (
                select(Turn).where(Turn.conversation_id == conversation_id).order_by(col(Turn.id))
            )
            return session.exec(query).all()

    def shown_turns(self, conversation_id: str) -> list[Turn]:
        """The turns a reader sees: those with text, without the engine's own steps (system turns:
        slot lookups, bookings, duplicate checks)."""
        return [
            turn
            for turn in self.turns(conversation_id)
            if turn.text is not None and turn.role != TurnRole.SYSTEM
        ]

    def last_turn(self, conversation_id: str, role: str) -> Turn | None:
        """The conversation's latest turn by `role`, without loading the others."""
        with self.session() as session:
            query = (
                select(Turn)
                .where(Turn.conversation_id == conversation_id, Turn.role == role)
                .order_by(col(Turn.id).desc())
                .limit(1)
            )
            return session.exec(query).first()

    def faq_misses(self, conversation_id: str) -> Sequence[FaqMiss]:
        """Questions in this conversation the FAQ did not cover, oldest first."""
        with self.session() as session:
            query = (
                select(FaqMiss)
                .where(FaqMiss.conversation_id == conversation_id)
                .order_by(col(FaqMiss.id))
            )
            return session.exec(query).all()

    def summary(self, conversation_id: str, *, session: Session | None = None) -> Summary | None:
        with self._using(session) as session:
            query = select(Summary).where(Summary.conversation_id == conversation_id)
            return session.exec(query).first()

    def stale_candidates(self, cutoff: datetime) -> Sequence[Conversation]:
        """Conversations without an outcome whose last activity is before `cutoff`."""
        with self.session() as session:
            query = (
                select(Conversation)
                .where(col(Conversation.outcome).is_(None))
                .where(col(Conversation.last_activity_at) < cutoff)
            )
            return session.exec(query).all()

    def latest_conversation(self, candidate_id: str) -> Conversation | None:
        with self.session() as session:
            query = (
                select(Conversation)
                .where(Conversation.candidate_id == candidate_id)
                .order_by(col(Conversation.started_at).desc())
            )
            return session.exec(query).first()

    def application_conversation(
        self, candidate_id: str, *, session: Session | None = None
    ) -> Conversation | None:
        """The conversation that holds the application's state: the latest one that is not a
        stub, a later conversation linked to the application when it matched it (SAZ-72)."""
        with self._using(session) as session:
            query = (
                select(Conversation)
                .where(Conversation.candidate_id == candidate_id)
                .order_by(col(Conversation.started_at).desc())
            )
            conversations = session.exec(query).all()
        holders = [c for c in conversations if state_of(c).duplicate_of is None]
        return holders[0] if holders else None

    def count_policy_closes(
        self,
        phone: str | None,
        email: str | None,
        *,
        exclude: Sequence[str | None] = (),
        session: Session | None = None,
    ) -> int:
        """Applications with this phone or email closed for a manipulation attempt (D11),
        whenever they were, as long as they are kept. Derived from the applications themselves:
        no counter to keep in step."""
        matches = []
        if phone:
            matches.append(col(Candidate.phone) == phone)
        if email:
            matches.append(col(Candidate.email) == email.lower())
        if not matches:
            return 0
        query = (
            select(func.count())
            .select_from(Candidate)
            .where(or_(*matches))
            .where(col(Candidate.status) == CandidateStatus.DISQUALIFIED)
            .where(col(Candidate.outcome_reason) == DisqualifyReason.POLICY_VIOLATION)
        )
        if ids := [candidate_id for candidate_id in exclude if candidate_id is not None]:
            query = query.where(col(Candidate.id).not_in(ids))
        with self._using(session) as session:
            return session.exec(query).one()

    def find_open_application(
        self,
        phone: str | None,
        email: str | None,
        now: datetime,
        *,
        exclude: Sequence[str | None] = (),
    ) -> Candidate | None:
        """The latest of `find_open_applications`."""
        found = self.find_open_applications(phone, email, now, exclude=exclude)
        return found[0] if found else None

    def find_open_applications(
        self,
        phone: str | None,
        email: str | None,
        now: datetime,
        *,
        exclude: Sequence[str | None] = (),
        session: Session | None = None,
    ) -> list[Candidate]:
        """In-progress, qualified or abandoned applications with this phone or email, the
        latest updated first.

        One closed (or idle) more than 30 days ago does not count, so the candidate starts fresh;
        an abandoned one under 30 days old is resumed where it stopped. Disqualified and withdrawn
        applications never count, nor one closed by the restart button, nor the ids in `exclude`
        (this conversation's own, those the candidate said are not theirs).
        """
        matches = []
        if phone:
            matches.append(col(Candidate.phone) == phone)
        if email:
            matches.append(col(Candidate.email) == email.lower())
        if not matches:
            return []
        cutoff = to_utc(now) - REAPPLY_AFTER
        reason = Candidate.outcome_reason
        last_touched = func.coalesce(col(Candidate.closed_at), col(Candidate.updated_at))
        query = (
            select(Candidate)
            .where(or_(*matches))
            .where(col(Candidate.status).in_(OPEN_STATUSES))
            .where(last_touched >= cutoff)
            .where(or_(col(reason).is_(None), col(reason) != RESTARTED))
            .order_by(col(Candidate.updated_at).desc())
        )
        if ids := [candidate_id for candidate_id in exclude if candidate_id is not None]:
            query = query.where(col(Candidate.id).not_in(ids))
        with self._using(session) as session:
            return list(session.exec(query).all())


type Key = tuple[type[SQLModel], object]
# Writes go out in foreign key order: a new candidate before the conversation that points at
# it, a conversation before its turns, misses and summary.
WRITE_ORDER: dict[type[SQLModel], int] = {Candidate: 0, Conversation: 1}
LAST = len(WRITE_ORDER)
# Rows a unit keeps one copy of, so the change made through any reference is the one written.
TRACKED = (Conversation, Candidate)


class Unit:
    """The database work of one turn (SAZ-84): it reads through one session and holds every
    write until `commit`, which writes them all in one transaction.

    A row read is detached from the session at once, so a change to it reaches the database at
    `commit` only, with only the attributes changed, as `Repo.save` writes them. Conversations
    and candidates are kept once per unit: reading one again returns the same object. Writes
    go out in the order they were made; a bulk delete first writes the rows saved before it,
    so a reopened application's summary can be deleted and written again in one turn.

    Transactions. On SQLite each read ends its transaction and returns the connection, so
    nothing is held while the models are called, and the write transaction lasts as long as
    `commit`. On Postgres the transaction opens with the conversation's advisory lock
    (`pg_advisory_xact_lock`, waiting at most `LOCK_TIMEOUT`) and stays open, idle, until the
    commit or the rollback releases the lock: a turn of the same conversation in another
    process waits for it. Reads see what other transactions committed (read committed).

    Every method that reads or writes blocks: call it in a thread, never on the event loop. A
    lock makes them take turns, so a `close` from a cancelled turn waits for a read still
    running in its thread."""

    def __init__(self, repo: Repo, conversation_id: str | None) -> None:
        self.repo = repo
        self.conversation_id = conversation_id
        self.root: Conversation | None = None
        """The conversation the unit was opened for, read by `begin`."""
        self.session = Session(repo.engine, expire_on_commit=False, autoflush=False)
        self._postgres = repo.engine.dialect.name == "postgresql"
        self._mutex = threading.Lock()
        self._rows: dict[Key, SQLModel | None] = {}
        self._turns: dict[str, list[Turn]] = {}
        self._ops: list[SQLModel | Executable] = []
        self._deleted: set[Key] = set()
        self._summaries: dict[str, bool] = {}  # conversation id -> has one, once this unit wrote
        self._after: list[Callable[[], object]] = []

    # --- reads ---

    def begin(self) -> Conversation | None:
        """Take the conversation's lock (Postgres only) and read the turn's snapshot: the
        conversation, its turns and its candidate. Sets and returns `root`: None for an unknown
        conversation, or a unit opened for a new one."""
        with self.repo.serial, self._mutex:
            if self.conversation_id is None:
                return None
            if self._postgres:
                self.session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
                self.session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": self.conversation_id},
                )
            query = (
                select(Conversation, Candidate)
                .outerjoin(Candidate, col(Conversation.candidate_id) == col(Candidate.id))
                .where(Conversation.id == self.conversation_id)
            )
            found = self.session.exec(query).first()
            self.root, candidate = found if found is not None else (None, None)
            self._rows[(Conversation, self.conversation_id)] = self.root
            if self.root is not None:
                if self.root.candidate_id is not None:
                    self._rows[(Candidate, self.root.candidate_id)] = candidate
                self._load_turns(self.root.id)
            self._end_read()
            return self.root

    def candidate(self, candidate_id: str | None) -> Candidate | None:
        if candidate_id is None:
            return None
        return self._cached(Candidate, candidate_id)

    def turns(self, conversation_id: str) -> list[Turn]:
        """The conversation's turns, oldest first, with those saved in this unit."""
        if conversation_id not in self._turns:
            with self.repo.serial, self._mutex:
                self._load_turns(conversation_id)
                self._end_read()
        return self._turns[conversation_id]

    def has_summary(self, conversation_id: str) -> bool:
        if conversation_id in self._summaries:
            return self._summaries[conversation_id]
        found = self.read(lambda session: self.repo.summary(conversation_id, session=session))
        return found is not None

    def application_conversation(self, candidate_id: str) -> Conversation | None:
        """`Repo.application_conversation`, with its candidate read along."""
        found = self.read(
            lambda session: self.repo.application_conversation(candidate_id, session=session)
        )
        if found is not None:
            self.candidate(found.candidate_id)
        return found

    def read[T](self, lookup: Callable[[Session], T]) -> T:
        """Run a query on the unit's session. Conversations and candidates in what it returns
        are the unit's own copies."""
        with self.repo.serial, self._mutex:
            found = lookup(self.session)
            self._end_read()
        if isinstance(found, list):
            return [self._adopt(row) for row in found]  # pyright: ignore[reportReturnType]
        return self._adopt(found)

    # --- writes ---

    def save[R: SQLModel](self, row: R) -> R:
        """Insert or update `row` at `commit`. A turn joins `turns` at once."""
        turns = self._turns.get(row.conversation_id) if isinstance(row, Turn) else None
        if turns is not None and not any(turn is row for turn in turns):
            turns.append(row)  # pyright: ignore[reportArgumentType]
        if isinstance(row, Summary):
            self._summaries[row.conversation_id] = True
        if isinstance(row, TRACKED):
            self._rows.setdefault(_key(row), row)
        self._ops.append(row)
        return row

    def delete_candidate(self, candidate_id: str) -> None:
        self._deleted.add((Candidate, candidate_id))
        self._rows[(Candidate, candidate_id)] = None
        self._ops.append(delete(Candidate).where(col(Candidate.id) == candidate_id))

    def delete_turns(self, conversation_id: str) -> None:
        self._turns[conversation_id] = []
        self._ops.append(delete(Turn).where(col(Turn.conversation_id) == conversation_id))

    def delete_faq_misses(self, conversation_id: str) -> None:
        self._ops.append(delete(FaqMiss).where(col(FaqMiss.conversation_id) == conversation_id))

    def delete_summary(self, conversation_id: str) -> None:
        self._summaries[conversation_id] = False
        self._ops.append(delete(Summary).where(col(Summary.conversation_id) == conversation_id))

    def after_commit(self, callback: Callable[[], object]) -> None:
        """Run `callback` once the writes are committed, never when they are rolled back
        (`ScreeningService._unit` runs them)."""
        self._after.append(callback)

    @property
    def callbacks(self) -> tuple[Callable[[], object], ...]:
        return tuple(self._after)

    def commit(self) -> None:
        """Write everything in one transaction, in the order it was done."""
        with self.repo.serial, self._mutex:
            written: set[int] = set()
            pending: list[SQLModel] = []
            for op in self._ops:
                if not isinstance(op, SQLModel):
                    self._flush(pending)
                    pending = []
                    self.session.execute(op, execution_options={"synchronize_session": False})
                elif id(op) not in written and not self._doomed(op):
                    written.add(id(op))
                    pending.append(op)
            self._flush(pending)
            self.session.commit()

    def close(self) -> None:
        """Roll back what was not committed and release the connection (and, on Postgres,
        the lock)."""
        with self.repo.serial, self._mutex:
            self.session.close()

    # --- internals ---

    def _get[M: SQLModel](self, model: type[M], key: object) -> M | None:
        if (model, key) in self._rows:
            return self._rows[(model, key)]  # pyright: ignore[reportReturnType]
        row = self.session.get(model, key)
        self._rows[(model, key)] = row
        return row

    def _cached[M: SQLModel](self, model: type[M], key: object) -> M | None:
        if (model, key) in self._rows:
            return self._rows[(model, key)]  # pyright: ignore[reportReturnType]
        with self.repo.serial, self._mutex:
            row = self._get(model, key)
            self._end_read()
        return row

    def _load_turns(self, conversation_id: str) -> None:
        loaded = list(self.repo.turns(conversation_id, session=self.session))
        saved = [op for op in self._ops if isinstance(op, Turn)]
        self._turns[conversation_id] = loaded + [
            turn for turn in saved if turn.conversation_id == conversation_id
        ]

    def _adopt[R](self, row: R) -> R:
        if not isinstance(row, TRACKED):
            return row
        return self._rows.setdefault(_key(row), row) or row  # pyright: ignore[reportReturnType]

    def _end_read(self) -> None:
        """Detach what was read. On SQLite also end the transaction, so no snapshot or
        connection is held until the next read; on Postgres the lock's transaction stays."""
        self.session.expunge_all()
        if not self._postgres:
            self.session.rollback()

    def _doomed(self, row: SQLModel) -> bool:
        return isinstance(row, TRACKED) and _key(row) in self._deleted

    def _flush(self, rows: list[SQLModel]) -> None:
        for rank in sorted({WRITE_ORDER.get(type(row), LAST) for row in rows}):
            self.session.add_all([row for row in rows if WRITE_ORDER.get(type(row), LAST) == rank])
            self.session.flush()


def _key(row: SQLModel) -> Key:
    return (type(row), row.id)  # pyright: ignore[reportAttributeAccessIssue]
