"""The screening agents: each a directory with its spec (`agent.yaml`), its text packs and the
Python its spec names (`loader`). `SAZON_AGENT` picks the one the service screens for."""

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from sazon.agents.loader import AGENT_FILE, load_agent
from sazon.config import get_settings
from sazon.engine.spec import AgentSpec

AGENTS = files(__name__)


def available() -> list[str]:
    """The agents shipped here, by name."""
    return sorted(item.name for item in AGENTS.iterdir() if (item / AGENT_FILE).is_file())


def agent_dir(name: str) -> Traversable:
    """An agent shipped here by its name (`riders`), or the directory of one elsewhere."""
    for directory in (AGENTS / name, Path(name)):
        if (directory / AGENT_FILE).is_file():
            return directory
    raise ValueError(f"no agent {name!r}: one of {available()}, or a directory with {AGENT_FILE}")


def selected() -> AgentSpec:
    """The agent `SAZON_AGENT` names."""
    return load_agent(agent_dir(get_settings().sazon_agent))
