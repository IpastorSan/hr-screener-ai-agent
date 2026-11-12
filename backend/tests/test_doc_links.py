"""Every relative link and every backticked repository path in the Markdown resolves."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".venv", "node_modules", ".next", ".claude", "__pycache__", ".cache"}
# Untracked, written by runs: they need not exist in a checkout.
UNTRACKED = ("evals/results/journeys", "evals/results/cost_ledger.jsonl", "backend/.env")
# A backticked path is looked up from the repository, the note's folder and these roots, since
# the docs name package paths without the `backend/src/sazon/` prefix.
ROOTS = ("", "backend", "backend/src", "backend/src/sazon", "web", "web/src", "deploy", "evals")

LINK = re.compile(r"\]\(([^)\s]+)\)")
CODE = re.compile(r"`([^`\s]+)`")
PATHLIKE = re.compile(r"^\.{0,2}/?[\w.@-]+(/[\w.@\[\]-]+)*/?$")
EXTENSIONS = (
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".ts",
    ".tsx",
    ".toml",
    ".mmd",
    ".svg",
    ".jpg",
    ".gif",
    ".gz",
    ".sh",
    ".txt",
    ".ini",
    ".alloy",
    ".caddy",
    ".xml",
    ".env",
    ".example",
)


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in REPO.rglob("*.md")
        if not SKIP_DIRS.intersection(path.relative_to(REPO).parts)
        and not str(path.relative_to(REPO)).startswith(UNTRACKED)
    )


def is_repo_path(token: str) -> bool:
    """A token that names a file or folder of the repository, not a route or a value: two
    segments at least, so a bare `texts/` named beside its parent is left alone."""
    if "/" not in token.rstrip("/") or token.startswith(("/", "http", "~")) or "://" in token:
        return False
    if any(c in token for c in "*<>{}$…") or "..." in token:
        return False
    if not PATHLIKE.match(token):
        return False
    return token.endswith("/") or token.endswith(EXTENSIONS)


def resolves(token: str, note: Path) -> bool:
    token = token.split("#")[0].split(":")[0]
    if not token or token.startswith(UNTRACKED):
        return True
    bases = [note.parent, *(REPO / root for root in ROOTS)]
    return any((base / token).exists() for base in bases)


def broken(note: Path) -> list[str]:
    text = note.read_text(encoding="utf-8")
    out = []
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#")[0]
        if path and not (note.parent / path).exists():
            out.append(f"link {target}")
    for token in CODE.findall(text):
        if is_repo_path(token) and not resolves(token, note):
            out.append(f"path {token}")
    return out


@pytest.mark.parametrize("note", markdown_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_every_link_and_path_in_the_docs_resolves(note: Path) -> None:
    assert broken(note) == []


def test_the_checker_catches_a_missing_path(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "See [the rules](missing.md) and `engine/nowhere.py`; `engine/text.py` is fine."
    )
    assert broken(note) == ["link missing.md", "path engine/nowhere.py"]
