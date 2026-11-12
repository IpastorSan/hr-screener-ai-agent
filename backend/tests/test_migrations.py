import gzip
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib import import_module
from itertools import product
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, Index, inspect, text
from sqlmodel import SQLModel, select

from sazon.api.seed import reset_db
from sazon.cli import main
from sazon.engine.models import Sentiment
from sazon.engine.state import ConversationState
from sazon.sentiment import SentimentStats
from sazon.storage.backup import BackupError, backup
from sazon.storage.migrate import (
    SchemaMismatch,
    alembic_config,
    current_revision,
    head_revision,
    migrate,
    schema_diff,
)
from sazon.storage.models import Candidate, Conversation, Recruiter, Turn
from sazon.storage.repo import Repo, init_db, make_engine
from test_unit_of_work import fresh_postgres

LABELS = [label.value for label in Sentiment]


def file_engine(tmp_path: Path, name: str = "sazon.db"):
    return make_engine(f"sqlite:///{tmp_path / name}")


def upgrade_to(engine: Engine, revision: str) -> None:
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade_to(engine: Engine, revision: str) -> None:
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


def test_migrations_match_the_models(tmp_path: Path) -> None:
    """A new model field needs a revision: `uv run alembic revision --autogenerate`."""
    engine = file_engine(tmp_path)
    assert migrate(engine) == head_revision()
    with engine.connect() as connection:
        assert schema_diff(connection) == []


def test_every_table_and_new_column_is_created(tmp_path: Path) -> None:
    engine = file_engine(tmp_path)
    init_db(engine)
    inspector = inspect(engine)
    assert set(SQLModel.metadata.tables) <= set(inspector.get_table_names())
    turn = {column["name"] for column in inspector.get_columns("turn")}
    conversation = {column["name"] for column in inspector.get_columns("conversation")}
    assert {"sources", "nudge"} <= turn
    assert "recovered_by_nudge" in conversation
    booking = {index["name"] for index in inspector.get_indexes("booking")}
    assert "ix_booking_one_active" in booking


def test_downgrade_to_base_and_back(tmp_path: Path) -> None:
    engine = file_engine(tmp_path)
    migrate(engine)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert "turn" not in inspect(engine).get_table_names()
    assert migrate(engine) == head_revision()


def test_a_database_made_by_create_all_is_adopted(tmp_path: Path) -> None:
    engine = file_engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    created_at = next(i for i in Turn.__table__.indexes if i.name == "ix_turn_created_at")  # pyright: ignore[reportAttributeAccessIssue]
    with engine.begin() as connection:
        created_at.drop(connection)
    assert current_revision(engine) is None
    assert migrate(engine) == head_revision()
    with engine.connect() as connection:
        assert schema_diff(connection) == []


def test_an_adopted_database_skips_the_revisions_and_their_backfills(tmp_path: Path) -> None:
    """It already matches the models, so it is stamped at head: 0003 does not recount moods."""
    engine = file_engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    repo = Repo(engine)
    now = datetime(2026, 10, 19, 10, tzinfo=UTC)
    chat = repo.save(Conversation(channel="web", state={}, started_at=now, last_activity_at=now))
    repo.save(
        Turn(
            conversation_id=chat.id,
            role="candidate",
            extraction={"sentiment": "frustrated"},
            model="m",
            created_at=now,
        )
    )

    assert migrate(engine) == head_revision()

    with repo.session() as session:
        assert session.exec(select(Conversation)).one().sentiment is None


def test_a_database_missing_columns_is_refused(tmp_path: Path) -> None:
    engine = file_engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        for index in Turn.__table__.indexes:  # pyright: ignore[reportAttributeAccessIssue]
            assert isinstance(index, Index)
            index.drop(connection)
        connection.exec_driver_sql("ALTER TABLE turn DROP COLUMN sources")
    with pytest.raises(SchemaMismatch):
        migrate(engine)


def test_reset_keeps_the_database_migrated(tmp_path: Path) -> None:
    engine = file_engine(tmp_path)
    init_db(engine)
    reset_db(engine)
    assert current_revision(engine) == head_revision()
    assert "turn" in inspect(engine).get_table_names()


def test_backup_copies_a_live_database(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'live.db'}"
    engine = make_engine(url)
    init_db(engine)
    repo = Repo(engine)
    moment = datetime(2026, 10, 19, tzinfo=UTC)
    repo.save(Conversation(channel="web", state={}, started_at=moment, last_activity_at=moment))
    dest = tmp_path / "backups" / "copy.db"
    assert main(["--db", url, "backup", str(dest)]) == 0
    copy = make_engine(f"sqlite:///{dest}")
    assert current_revision(copy) == head_revision()
    with Repo(copy).session() as session:
        assert len(session.exec(select(Conversation)).all()) == 1
    with pytest.raises(BackupError):
        backup("sqlite://", dest)


def test_upgrading_fills_the_sentiment_stats_from_the_turns_a_model_read(tmp_path: Path) -> None:
    """0003: a tap (no model) and a fallback extraction carry no label."""
    engine = file_engine(tmp_path)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
    moment = "2026-10-19 10:00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO candidate (id, profile, status, quality_flags, sentiment, channel, "
            "created_at, updated_at) VALUES ('c1', '{}', 'in_progress', '[]', 'neutral', 'web', "
            "?, ?)",
            (moment, moment),
        )
        connection.exec_driver_sql(
            "INSERT INTO conversation (id, candidate_id, channel, state, context, quality_flags, "
            "recovered_by_nudge, started_at, last_activity_at) "
            "VALUES ('v1', 'c1', 'web', '{}', '{}', '[]', 0, ?, ?)",
            (moment, moment),
        )
        rows = [
            ('{"sentiment": "frustrated"}', "m", 0),
            ('{"sentiment": "frustrated"}', "m", 0),
            ('{"sentiment": "neutral"}', None, 0),
            ('{"sentiment": "neutral"}', "offline", 1),
        ]
        for extraction, model, fallback in rows:
            connection.exec_driver_sql(
                "INSERT INTO turn (conversation_id, role, extraction, model, fallback_used, "
                "nudge, created_at) VALUES ('v1', 'candidate', ?, ?, ?, 0, ?)",
                (extraction, model, fallback, moment),
            )

    assert migrate(engine) == head_revision()

    with Repo(engine).session() as session:
        conversation = session.exec(select(Conversation)).one()
        assert conversation.sentiment == {
            "counts": {"frustrated": 2},
            "peak": "frustrated",
            "last_notable": "frustrated",
        }
        assert session.exec(select(Candidate)).one().sentiment == "frustrated"


CONFIRM_STATE = '{"stage": "confirm", "consented": true, "profile": {"schedule": "afternoon"}}'


def test_upgrading_turns_a_stored_schedule_into_a_list(tmp_path: Path) -> None:
    """0007 (SAZ-41): one stored block becomes a one-element list; downgrade keeps the first."""
    engine = file_engine(tmp_path)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0006")
    moment = "2026-10-19 10:00:00"
    with engine.begin() as connection:
        for row_id, profile in (
            ("c1", '{"schedule": "morning", "full_name": "Ana"}'),
            ("c2", '{"schedule": ["evening"]}'),
            ("c3", "{}"),
        ):
            connection.exec_driver_sql(
                "INSERT INTO candidate (id, profile, status, quality_flags, channel, created_at, "
                "updated_at) VALUES (?, ?, 'in_progress', '[]', 'web', ?, ?)",
                (row_id, profile, moment, moment),
            )
        connection.exec_driver_sql(
            "INSERT INTO conversation (id, candidate_id, channel, state, context, quality_flags, "
            "recovered_by_nudge, started_at, last_activity_at) "
            "VALUES ('v1', 'c1', 'web', ?, '{}', '[]', 0, ?, ?)",
            (CONFIRM_STATE, moment, moment),
        )

    assert migrate(engine) == head_revision()

    with Repo(engine).session() as session:
        profiles = {c.id: c.profile for c in session.exec(select(Candidate))}
        assert profiles["c1"] == {"schedule": ["morning"], "full_name": "Ana"}
        assert profiles["c2"] == {"schedule": ["evening"]}
        assert profiles["c3"] == {}
        state = session.exec(select(Conversation)).one().state
        assert state["profile"] == {"schedule": ["afternoon"]}

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0006")
        rows = connection.exec_driver_sql("SELECT id, profile FROM candidate").all()
    profiles = {row_id: json.loads(profile) for row_id, profile in rows}
    assert profiles["c1"]["schedule"] == "morning"
    assert profiles["c2"]["schedule"] == "evening"


OUT_OF_AREA = {"stage": "done", "consented": True, "outcome": "disqualified"} | {
    "disqualify_reason": "out_of_area"
}
SAVED = {"stage": "done", "consented": True, "outcome": "qualified"}


def test_upgrading_puts_the_out_of_area_opt_ins_in_the_talent_pool(tmp_path: Path) -> None:
    """0008 (SAZ-42): only a yes to the out-of-area question; the old "new interview dates"
    yes asked something else."""
    engine = file_engine(tmp_path)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0007")
    moment = "2026-10-19 10:00:00"
    states = {
        "c1": OUT_OF_AREA | {"talent_pool_opt_in": True},
        "c2": OUT_OF_AREA | {"talent_pool_opt_in": False},
        "c3": SAVED | {"slots_declined": True, "talent_pool_opt_in": True},
        "c4": [],
    }
    with engine.begin() as connection:
        for row_id, state in states.items():
            connection.exec_driver_sql(
                "INSERT INTO candidate (id, profile, status, quality_flags, channel, created_at, "
                "updated_at) VALUES (?, '{}', 'disqualified', '[]', 'web', ?, ?)",
                (row_id, moment, moment),
            )
            connection.exec_driver_sql(
                "INSERT INTO conversation (id, candidate_id, channel, state, context, "
                "quality_flags, recovered_by_nudge, started_at, last_activity_at) "
                "VALUES (?, ?, 'web', ?, '{}', '[]', 0, ?, ?)",
                (f"v{row_id}", row_id, json.dumps(state), moment, moment),
            )
        connection.exec_driver_sql(
            "INSERT INTO conversation (id, candidate_id, channel, state, context, quality_flags, "
            "recovered_by_nudge, started_at, last_activity_at) "
            "VALUES ('anon', NULL, 'web', ?, '{}', '[]', 0, ?, ?)",
            (json.dumps(states["c1"]), moment, moment),
        )

    assert migrate(engine) == head_revision()

    with Repo(engine).session() as session:
        pool = {c.id: c.talent_pool for c in session.exec(select(Candidate))}
    assert pool == {"c1": True, "c2": False, "c3": False, "c4": False}

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0007")
    columns = {column["name"] for column in inspect(engine).get_columns("candidate")}
    assert "talent_pool" not in columns


def test_upgrading_leaves_recruiters_video_only_with_the_new_daily_maximum(tmp_path: Path) -> None:
    """0009 (SAZ-64): in person moves to the location managers; the old default of 8 a day
    becomes 15, other values stay."""
    engine = file_engine(tmp_path)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0008")
    moment = "2026-10-19 10:00:00"
    rows = {"r1": (["onsite", "video"], 8), "r2": (["onsite"], 6), "r3": (["video"], 10)}
    with engine.begin() as connection:
        for row_id, (modes, most) in rows.items():
            connection.exec_driver_sql(
                "INSERT INTO recruiter (id, name, email, is_admin, active, country, timezone, "
                "location_ids, interview_modes, working_hours, max_interviews_per_day, "
                "session_version, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, 1, 'ES', 'Europe/Madrid', '[]', ?, '{}', ?, 0, ?, ?)",
                (row_id, row_id, f"{row_id}@example.com", json.dumps(modes), most, moment, moment),
            )

    assert migrate(engine) == head_revision()

    with Repo(engine).session() as session:
        found = {
            r.id: (r.interview_modes, r.max_interviews_per_day)
            for r in session.exec(select(Recruiter))
        }
    assert found == {"r1": (["video"], 15), "r2": (["video"], 6), "r3": (["video"], 10)}
    booking = {column["name"] for column in inspect(engine).get_columns("booking")}
    assert "interviewer_email" in booking

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0008")
    booking = {column["name"] for column in inspect(engine).get_columns("booking")}
    assert "interviewer_email" not in booking


@pytest.mark.parametrize("labels", [*product(LABELS, repeat=3), (), ("positive",)])
def test_the_frozen_sentiment_backfill_matches_the_app(labels: tuple[str, ...]) -> None:
    """0003 carries its own copy of SentimentStats; it must write what the app would have."""
    frozen = import_module("sazon.migrations.versions.0003_conversation_sentiment")
    stats, app = frozen.empty(), SentimentStats()
    for label in labels:
        stats, app = frozen.record(stats, label), app.record(Sentiment(label))
    assert json.dumps(stats) == json.dumps(app.dump())
    assert frozen.mood(stats) == (app.mood.value if app.mood else None)


# --- 0011: the state carries its phase (SAZ-91) ---

CONVERSATION = (
    "INSERT INTO conversation (id, channel, state, context, quality_flags, recovered_by_nudge, "
    "started_at, last_activity_at) VALUES (:id, 'web', :state, '{}', '[]', false, :at, :at)"
)
BARE_DECLINED = {"stage": "done", "outcome": "declined_consent"}
CORPUS = Path(__file__).parents[2] / "evals" / "replay" / "corpus.jsonl.gz"


def corpus_states() -> list[dict[str, Any]]:
    """Every distinct state the engine reached in the replay corpus (`evals/replay`), as version
    1 stored it."""
    seen: set[str] = set()
    with gzip.open(CORPUS, "rt", encoding="utf-8") as lines:
        for line in lines:
            case = json.loads(line)
            out = case["out"] if isinstance(case["out"], list) else [case["out"]]
            for value in [*case["args"].values(), *out]:
                if isinstance(value, dict) and "country_source" in value:
                    seen.add(json.dumps(value, sort_keys=True))
    return [json.loads(state) for state in sorted(seen)]


def stored_states(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, state FROM conversation")).all()
    return {
        row_id: json.loads(state) if isinstance(state, str) else state for row_id, state in rows
    }


def insert_states(engine: Engine, states: dict[str, Any]) -> None:
    at = "2026-10-31 10:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(CONVERSATION),
            [{"id": row_id, "state": json.dumps(s), "at": at} for row_id, s in states.items()],
        )


@pytest.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.xdist_group("postgres"))]
)
def at_0010(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    """A database migrated up to the revision before 0011, on SQLite and, when
    `SAZON_TEST_POSTGRES_URL` names one it may wipe, Postgres."""
    url = f"sqlite:///{tmp_path / 'sazon.db'}" if request.param == "sqlite" else fresh_postgres()
    engine = make_engine(url)
    upgrade_to(engine, "0010")
    yield engine
    engine.dispose()


def test_upgrading_rewrites_every_stored_state_with_its_phase(at_0010: Engine) -> None:
    """0011 on every state of the replay corpus, a bare declined consent and an empty one: each
    is stored in version 2 and reads as it did; downgrade writes the flags back."""
    states = {f"s{i}": state for i, state in enumerate(corpus_states())}
    states |= {"bare": BARE_DECLINED, "empty": {}}
    insert_states(at_0010, states)

    assert migrate(at_0010) == head_revision()

    upgraded = stored_states(at_0010)
    for row_id, state in states.items():
        assert upgraded[row_id]["state_version"] == 2
        assert "stage" not in upgraded[row_id]
        stored = ConversationState.model_validate(upgraded[row_id])
        assert stored == ConversationState.model_validate(state), row_id
    # The bare record keeps nothing but its outcome.
    assert upgraded["bare"] == {
        "state_version": 2,
        "phase": {"kind": "closed", "variant": {"kind": "declined_consent"}},
    }

    downgrade_to(at_0010, "0010")

    downgraded = stored_states(at_0010)
    for row_id, state in states.items():
        assert downgraded[row_id]["stage"] == state.get("stage", "consent")
        back = ConversationState.model_validate(downgraded[row_id])
        assert back == ConversationState.model_validate(state), row_id


def test_a_stored_state_no_phase_holds_stops_the_upgrade_and_is_named(at_0010: Engine) -> None:
    amending_unbooked = {"stage": "done", "consented": True, "outcome": "qualified"}
    amending_unbooked |= {"amending": "email", "awaiting": "email"}
    asking_closed = {"stage": "city", "consented": True, "awaiting": "city", "outcome": "abandoned"}
    states = {"good": corpus_states()[0], "bad": amending_unbooked, "worse": asking_closed}
    insert_states(at_0010, states)

    with pytest.raises(Exception, match=r"2 stored states no phase holds:\n.*bad: .*amending"):
        migrate(at_0010)

    assert stored_states(at_0010) == states  # nothing written
