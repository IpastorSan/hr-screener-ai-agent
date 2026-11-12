"""Agents load from YAML (`sazon.agents.loader`): the rider agent's `agent.yaml` gives the spec
it had in Python, recorded in `snapshots/riders_spec.json` before the Python went; `SAZON_AGENT`
picks the agent."""

import json
import shutil
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml

from riders import SPEC
from sazon.agents import AGENTS, agent_dir, selected
from sazon.agents.loader import DATA, AgentError, load_agent
from sazon.agents.scaffold import new_agent
from sazon.cli import main
from sazon.config import get_settings
from sazon.engine import spec
from sazon.engine.service_areas import branch_name, load_locations
from sazon.engine.spec import AgentSpec, agent, use
from sazon.engine.texts import REGISTERS, needed_lines
from sazon.recruiters import load_fixture

RIDERS = files("sazon.agents.riders")
SNAPSHOT = Path(__file__).parent / "snapshots" / "riders_spec.json"


def _dump(spec: AgentSpec) -> dict[str, Any]:
    """The spec as JSON, its hooks by the name of the function each is."""
    data = spec.model_dump(mode="json", exclude={"hooks"})
    for key in DATA:  # the data files, wherever the checkout is
        data[key] = Path(data[key]).relative_to(Path(str(AGENTS))).as_posix()
    data["hooks"] = {
        kind: {name: f"{hook.__module__}.{hook.__qualname__}" for name, hook in hooks.items()}
        for kind, hooks in dict(spec.hooks).items()
    }
    return data


def test_the_riders_load_from_yaml_as_the_python_spec_built_them():
    assert _dump(SPEC) == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_sazon_agent_picks_the_agent_by_name_or_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    directory = _riders(tmp_path, "agent.yaml", _set("agent", value="couriers"))
    monkeypatch.setattr(spec, "_current", None)
    monkeypatch.setenv("SAZON_AGENT", str(directory))
    get_settings.cache_clear()
    try:
        assert agent().agent == "couriers"
        assert agent() is agent()
        monkeypatch.setenv("SAZON_AGENT", "riders")
        get_settings.cache_clear()
        assert selected() == SPEC
        monkeypatch.setenv("SAZON_AGENT", "teachers")
        get_settings.cache_clear()
        assert selected().agent == "teachers"
        monkeypatch.setenv("SAZON_AGENT", "nurses")
        get_settings.cache_clear()
        with pytest.raises(
            ValueError, match="no agent 'nurses': one of \\['riders', 'teachers'\\]"
        ):
            selected()
    finally:
        get_settings.cache_clear()


def test_the_teachers_load_with_only_the_registers_of_spain():
    """The second agent: its own places, team and countries, no Mexican pack."""
    teachers = load_agent(agent_dir("teachers"))
    assert teachers.countries == ("ES",)
    assert set(teachers.texts) == {"es-ES", "en"}
    with use(teachers):
        assert [loc.location_id for loc in load_locations().values()] == [
            "alb-aravaca",
            "alb-las-rozas",
            "alb-pozuelo",
        ]
        assert branch_name("alb-las-rozas") == "Las Rozas"
        assert {entry["name"] for entry in load_fixture()} >= {"Elena Robles"}


# --- what the loader rejects, and where it says the mistake is ---


def _riders(tmp_path: Path, file: str, change: Callable[[Any], None]) -> Path:
    """A copy of the rider agent with `change` made to the data of one of its files."""
    directory = tmp_path / "riders"
    shutil.copytree(Path(str(RIDERS)), directory, ignore=shutil.ignore_patterns("*.py", "__*"))
    path = directory / file
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return directory


def _set(*path: str | int, value: Any = None, drop: bool = False) -> Callable[[Any], None]:
    """Set the key at `path` to `value`, or `drop` it."""

    def change(data: Any) -> None:
        for key in path[:-1]:
            data = data[key]
        if drop:
            del data[path[-1]]
        else:
            data[path[-1]] = value

    return change


def _drop(*path: str | int) -> Callable[[Any], None]:
    return _set(*path, drop=True)


def _gate_on_no(data: Any) -> None:
    gate = data["fields"][4]["gate"]
    gate["readings"]["no"] = "close"
    gate["reasons"]["no"] = "no_license"


@pytest.mark.parametrize(
    ("file", "change", "error"),
    [
        # a line the spec needs, missing from a pack
        ("texts/en.yaml", _drop("fields", "email", "refusal"),
         "riders/texts/en.yaml: fields.email.refusal: texts en: email has no ['refusal']"),
        # `ask_if` on a field the agent has not
        ("agent.yaml", _set("fields", 8, "ask_if", value={"experience_yeras": {"gt": 0}}),
         "riders/agent.yaml: fields[8].ask_if.experience_yeras: platforms: ask_if on "
         "experience_yeras, which is not asked before it"),
        # a hook the agent's module has not
        ("agent.yaml", _set("fields", 7, "checks", value=["platform_ages"]),
         "riders/agent.yaml: fields[7].checks[0]: experience_years: no check hook `platform_ages`"),
        ("agent.yaml", _set("fields", 5, "reader", value="availabilty"),
         "riders/agent.yaml: fields[5].reader: availability: no reader hook `availabilty`"),
        ("agent.yaml", _set("hooks", value="sazon.agents.drivers.hooks"),
         "riders/agent.yaml: hooks: no module sazon.agents.drivers.hooks"),
        # a gate on a reading the type never has
        ("agent.yaml", _gate_on_no,
         "riders/agent.yaml: fields[4].gate.readings.no: city: a location is never read as ['no']"),
        # labels and prompt lines for no field
        ("texts/labels.yaml", _set("fields", "campus", value={"label": {"es": "Campus"}}),
         "riders/texts/labels.yaml: fields.campus: no field campus"),
        ("texts/labels.yaml", _set("dashboard", "columns", "city", value="Zona"),
         "riders/texts/labels.yaml: dashboard.columns.city: no column city"),
        ("texts/prompts.yaml", _set("fields", "campus", value="the campus"),
         "riders/texts/prompts.yaml: fields.campus: no field campus"),
        ("texts/labels.yaml", _set("fix_words", "campus", value=["campus"]),
         "riders/texts/labels.yaml: fix_words.campus: no field campus"),
        # a skeleton line the engine has not
        ("texts/es-MX.yaml", _set("skeleton", value={"confirm_bookin": "Listo."}),
         "riders/texts/es-MX.yaml: skeleton.confirm_bookin: no skeleton line confirm_bookin"),
        # a key no agent has
        ("agent.yaml", _set("regions", value=["ES"]),
         "riders/agent.yaml: regions: no such key"),
        # what pydantic finds on its own, and a placeholder a line is never given
        ("agent.yaml", _set("fields", 0, "type", value="person"),
         "riders/agent.yaml: fields[0].type: Input should be"),
        ("texts/es-ES.yaml", _set("fields", "start_date", "read_back", value="El {date}."),
         "riders/texts/es-ES.yaml: fields.start_date.read_back: start_date.read_back: no "
         "placeholder ['date']"),
        ("texts/labels.yaml", _drop("fields", "email", "label", "en"),
         "riders/texts/labels.yaml: fields.email.label: labels: email has no name in ['en']"),
    ],
)  # fmt: skip
def test_a_mistake_is_rejected_with_its_file_and_key(
    tmp_path: Path, file: str, change: Callable[[Any], None], error: str
):
    directory = _riders(tmp_path, file, change)
    with pytest.raises(AgentError) as raised:
        load_agent(directory)
    assert str(raised.value).startswith(error)


def test_a_missing_file_is_named(tmp_path: Path):
    directory = _riders(tmp_path, "agent.yaml", lambda _: None)
    (directory / "texts" / "en.yaml").unlink()
    with pytest.raises(AgentError, match=r"riders/texts/en\.yaml: no such file"):
        load_agent(directory)


# --- sazon new-agent ---


def test_a_new_agent_loads_with_a_line_for_every_line_its_fields_need(tmp_path: Path):
    assert main(["new-agent", "school", "--dir", str(tmp_path / "school")]) == 0
    school = load_agent(tmp_path / "school")
    assert school.agent == "school"
    kinds = {field.type for field in school.fields}
    assert len(kinds) > 2
    assert any(field.gate is not None for field in school.fields)
    for register in REGISTERS:
        given = school.texts[register].fields
        assert {f: set(dict(given[f].model_dump(exclude_none=True))) for f in given} == (
            needed_lines(school)
        )


def test_a_new_agent_needs_a_free_directory_and_a_plain_name(tmp_path: Path):
    new_agent("school", tmp_path / "school")
    with pytest.raises(FileExistsError):
        new_agent("school", tmp_path / "school")
    with pytest.raises(ValueError, match="lowercase"):
        new_agent("Colegio Albéniz", tmp_path / "albeniz")
