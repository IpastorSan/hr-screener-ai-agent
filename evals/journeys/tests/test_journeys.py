"""The journey runner offline: the seed journeys, the checks, and that a run leaves no trace."""

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.e2e import run as e2e_run
from evals.e2e.conversation import MeteredLLM
from evals.e2e.personas import EVAL_NOW
from evals.e2e.simulator import ScriptedSimulator
from evals.journeys.model import (
    DEFAULT_AGENT,
    Expect,
    Journey,
    Scheduling,
    Step,
    load_group,
    load_journeys,
    parse_duration,
)
from evals.journeys.run import main, summary
from evals.journeys.runner import JourneyRun, _faq_problem, scheduling_world
from sazon.agents.scaffold import JOURNEYS_FILE, new_agent
from sazon.config import get_settings
from sazon.llm.offline import OfflineLLM
from sazon.rag.index import NoRetriever, RagIndex, Vectors, load_corpus
from sazon.recruiters import seed_team
from sazon.scheduling.service import available_modes, offer
from sazon.storage.models import InterviewMode
from sazon.storage.repo import Repo, init_db, make_engine, to_utc


def test_seed_journeys_pass_offline_and_leave_only_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{configured}")
    get_settings.cache_clear()
    monkeypatch.chdir(tmp_path)  # anything written to a relative path would land here

    code = main(["--offline", "--group", "seed", "--out", "out"])

    assert code == 0
    assert not configured.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["out"]
    (run_dir,) = (tmp_path / "out").iterdir()
    names = sorted(p.name for p in run_dir.iterdir())
    assert names == [
        "meta.json",
        "seed.happy_es_booked.json",
        "seed.mx_contact_question_no_license.json",
        "seed.reapply_after_no_license.json",
        "summary.md",
    ]
    results = {
        name: json.loads((run_dir / name).read_text()) for name in names if name.startswith("seed")
    }
    assert {r["id"]: r["status"] for r in results.values()} == {
        "happy_es_booked": "pass",
        "mx_contact_question_no_license": "pass",
        "reapply_after_no_license": "pass",
    }
    reapply = results["seed.reapply_after_no_license.json"]
    sessions = {t["session"] for t in reapply["turns"]}
    assert sessions == {1, 2}
    last = reapply["turns"][-1]
    assert last["state"]["booking"] is not None
    assert not last["resumed"]
    assert any(t["speaker"] == "candidate" and not t["scripted"] for t in reapply["turns"])
    assert (
        "| [seed.happy_es_booked](seed.happy_es_booked.json) | PASS |"
        in (run_dir / "summary.md").read_text()
    )


def test_another_agents_journeys_pass_offline_screening_for_it(tmp_path: Path) -> None:
    """The teachers' journeys (`cases/teachers/`), each with the teachers' spec, campuses and
    heads of department, named by the agent."""
    journeys = load_journeys(agent="teachers")
    assert journeys
    assert {journey.agent for journey in journeys} == {"teachers"}
    riders = {journey.id for journey in load_journeys(agent=DEFAULT_AGENT)}
    assert not riders & {journey.id for journey in journeys}

    assert main(["--offline", "--agent", "teachers", "--out", str(tmp_path)]) == 0

    (run_dir,) = tmp_path.iterdir()
    results = [p.name for p in run_dir.iterdir() if p.suffix == ".json" and p.stem != "meta"]
    assert len(results) == len(journeys)
    assert all(name.startswith("teachers.") for name in results)


def test_keep_db_writes_the_database_into_the_run_folder(tmp_path: Path) -> None:
    code = main(["--offline", "--id", "happy_es_booked", "--keep-db", "--out", str(tmp_path)])
    assert code == 0
    (run_dir,) = tmp_path.iterdir()
    assert (run_dir / "seed.happy_es_booked.db").exists()


class _NoModel:
    name = "missing"

    def embed(self, texts: Sequence[str]) -> Vectors:
        raise FileNotFoundError("no embedding model in the cache")


def test_a_real_model_run_refuses_without_faq_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """QA-B6: without the embedding cache every question gets the unknown answer, which a run
    with real models would blame on them. It stops before opening a client; offline goes on."""
    missing = RagIndex(load_corpus(), _NoModel())
    monkeypatch.setattr(e2e_run, "default_index", lambda: missing)

    code = main(["--id", "happy_es_booked", "--out", str(tmp_path)])

    assert code == 2
    assert "sazon rag-index" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())
    assert e2e_run.load_faq(offline=True) is missing
    assert "WARNING: FAQ retrieval is unavailable" in capsys.readouterr().err


def _journey(steps: list[dict[str, object]], **extra: object) -> Journey:
    return Journey.model_validate(
        {"id": "probe", "description": "probe", "country": "ES", "steps": steps, **extra}
    )


async def _run(journey: Journey) -> dict[str, object]:
    llm = MeteredLLM(OfflineLLM())
    return await JourneyRun(journey, llm, ScriptedSimulator(), NoRetriever(), live=False).run()


async def test_a_wrong_expectation_fails_and_live_ones_are_skipped_offline() -> None:
    journey = _journey(
        [
            {"tap": "yes", "expect": {"asks": "email", "live": {"faq_source": "privacy"}}},
            {"tap": "maybe", "expect": {"asks": "full_name", "register": "tu"}},
        ]
    )
    result = await _run(journey)

    assert result["status"] == "fail"
    assert result["first_failure"] == {
        "session": 1,
        "step": 1,
        "check": "asks",
        "problem": "expected email, got full_name",
        "reply": result["turns"][2]["text"],  # pyright: ignore[reportIndexIssue]
    }
    failures = result["failures"]
    assert [f["check"] for f in failures] == ["asks", "tap"]  # pyright: ignore[reportGeneralTypeIssues]
    assert result["expectations_skipped_offline"] == 1


@pytest.mark.parametrize(
    ("want", "sources", "ok"),
    [
        (["pay-es", "helmet"], ["pay-es", "helmet", "fuel"], True),
        (["pay-es", "helmet"], ["pay-es"], False),
        ("any", ["pay-es"], True),
        ("none", ["pay-es"], False),
    ],
)
def test_faq_source_takes_a_list_of_ids_each_cited(
    want: str | list[str], sources: list[str], ok: bool
) -> None:
    assert (_faq_problem(want, sources) is None) is ok


async def test_xfail_counts_a_failure_without_failing_the_run() -> None:
    result = await _run(_journey([{"tap": "yes", "expect": {"closed": True}}], xfail="SAZ-0"))
    assert result["status"] == "xfail"


async def test_an_xfail_that_passes_is_an_xpass() -> None:
    result = await _run(_journey([{"tap": "yes", "expect": {"closed": False}}], xfail="SAZ-0"))
    assert result["status"] == "xpass"


async def test_a_register_slip_in_a_reply_fails_the_invariant() -> None:
    result = await _run(_journey([{"tap": "yes"}], register="usted"))
    assert result["status"] == "fail"
    assert result["first_failure"]["check"] == "register"  # pyright: ignore[reportIndexIssue]


def test_journey_cards() -> None:
    assert load_journeys(group="seed")
    assert parse_duration("3d") == timedelta(days=3)
    assert parse_duration("90m") == timedelta(minutes=90)
    with pytest.raises(ValueError, match="bad duration"):
        parse_duration("tomorrow")
    with pytest.raises(ValidationError):
        Step.model_validate({"say": "hola", "tap": "yes"})
    with pytest.raises(ValidationError):
        Step.model_validate({"persona": "brief"})  # no until
    with pytest.raises(ValidationError):
        _journey([{"say": "hola", "expect": {"asked": "email"}}])  # a typo is an error
    journey = _journey([{"say": "hola"}], location="es-mad-centro")
    assert len(journey.sessions) == 1
    assert journey.chat_context.location_id == "es-mad-centro"
    assert journey.expected_register == "tu"


@pytest.mark.parametrize(
    ("setup", "modes", "free"),
    [
        ({}, [InterviewMode.ONSITE, InterviewMode.VIDEO], True),
        ({"modes": ["onsite"]}, [InterviewMode.ONSITE], True),
        ({"modes": ["video"]}, [InterviewMode.VIDEO], True),
        ({"modes": ["video"], "busy": "all"}, [InterviewMode.VIDEO], False),
    ],
)
def test_a_journey_can_limit_the_interview_modes_and_fill_the_calendars(
    setup: dict[str, object], modes: list[InterviewMode], free: bool
) -> None:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    seed_team(repo, EVAL_NOW, with_passwords=False)
    config = scheduling_world(repo, Scheduling.model_validate(setup))

    assert available_modes(repo, "es-mad-centro", config=config) == modes
    found = offer(repo, "es-mad-centro", None, None, EVAL_NOW, mode=modes[-1], config=config)
    assert bool(found) is free


def test_busy_days_take_only_those_days_out():
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    seed_team(repo, EVAL_NOW, with_passwords=False)
    tuesday = EVAL_NOW.date() + timedelta(days=1)
    config = scheduling_world(repo, Scheduling(modes=["video"], busy=[tuesday]))

    found = offer(repo, "es-mad-centro", None, None, EVAL_NOW, n=50, config=config)

    days = {to_utc(slot.starts_at).date() for slot in found}
    assert found
    assert tuesday not in days


def test_an_offline_xpass_that_skipped_live_checks_is_not_proof() -> None:
    row = {
        "name": "g.j",
        "status": "xpass",
        "xfail": "batch 9: ...",
        "candidate_turns": 3,
        "cost_usd": 0.0,
        "simulator_cost_usd": 0.0,
        "first_failure": None,
        "expectations_skipped_offline": 2,
    }
    meta = {"started": "now", "mode": "offline"}
    assert "run the journey with real models" in summary([row], meta)
    live = row | {"expectations_skipped_offline": 0}
    assert "run the journey with real models" not in summary([live], meta)


def test_a_new_agent_has_a_journey_card_per_field_type_and_gate_reading(tmp_path: Path) -> None:
    journeys = load_group(new_agent("school", tmp_path / "school") / JOURNEYS_FILE)
    assert [j.id for j in journeys] == [
        "school_person_name",
        "school_contact",
        "school_credential",
        "school_date",
        "school_certificate_no",
    ]
    assert journeys[-1].sessions[0].steps[-1].expect == Expect(
        outcome="disqualified", reason="no_license"
    )
