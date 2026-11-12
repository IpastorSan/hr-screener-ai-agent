from pathlib import Path

from evals.model_selection.cases import EXTRACTION_CASES, load_cases
from evals.model_selection.export_review import read_comments, render_split


def test_split_notes_keep_the_comments_by_case_id(tmp_path: Path) -> None:
    cases = load_cases(EXTRACTION_CASES)
    first, second = cases[0]["id"], cases[1]["id"]
    notes = render_split(cases, {first: "check this label", second: "two\nlines"})
    for name, text in notes.items():
        (tmp_path / name).write_text(text, encoding="utf-8")

    comments = read_comments(tmp_path)

    assert comments == {first: "check this label", second: "two\nlines"}
    assert "sazon-screener-gold-labels-index.md" in notes
    assert sum(text.count("\n### ") for text in notes.values()) == len(cases)
