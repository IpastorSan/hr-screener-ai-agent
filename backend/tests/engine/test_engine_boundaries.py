import ast
from pathlib import Path

import sazon.engine

ENGINE_DIR = Path(sazon.engine.__file__).parent
FORBIDDEN_MODULES = (
    "pydantic_ai",
    "openai",
    "anthropic",
    "httpx",
    "httpx2",
    "requests",
    "sazon.llm",
)
CLOCK_CALLS = ("now", "today", "utcnow", "time")


def engine_sources() -> list[tuple[Path, ast.Module]]:
    """Every module of the engine, its sub-packages (`states/`) included."""
    return [(path, ast.parse(path.read_text())) for path in sorted(ENGINE_DIR.rglob("*.py"))]


def imported_modules(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def forbidden_imports(tree: ast.Module) -> list[str]:
    return [
        module
        for module in imported_modules(tree)
        if any(module == name or module.startswith(name + ".") for name in FORBIDDEN_MODULES)
    ]


def test_engine_does_not_import_llm_or_network_clients():
    for path, tree in engine_sources():
        name = path.relative_to(ENGINE_DIR)
        assert not forbidden_imports(tree), f"{name} imports {forbidden_imports(tree)}"


def test_the_boundary_check_catches_the_project_http_client_and_llm_layer():
    source = "import httpx2\nfrom pydantic_ai import Agent\nfrom sazon.llm.interface import LLM\n"
    assert forbidden_imports(ast.parse(source)) == ["httpx2", "pydantic_ai", "sazon.llm.interface"]


def test_engine_does_not_read_the_clock():
    for path, tree in engine_sources():
        name = path.relative_to(ENGINE_DIR)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in CLOCK_CALLS, f"{name} calls .{node.func.attr}()"


def test_the_engine_sources_include_the_state_handlers():
    names = {path.relative_to(ENGINE_DIR).as_posix() for path, _ in engine_sources()}
    assert {"machine.py", "states/asking.py", "states/interview.py"} <= names
