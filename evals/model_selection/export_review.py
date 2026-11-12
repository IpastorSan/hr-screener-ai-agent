"""Write the extraction gold labels as a Markdown review sheet.

uv run --project backend python -m evals.model_selection.export_review (--out PATH | --split DIR) \
    [--results evals/results/<extraction run>.json ...]

With a results file, each case lists the models that got it wrong and how, which separates a
doubtful label (most models disagree) from a hard case (a few miss it). Several results files
add up (a re-scored run plus the new cases run on their own). `--split DIR` writes one note per
category and an index (an Obsidian vault's wiki links), and carries over every comment
already written in DIR by case id.
"""

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.model_selection.cases import EXTRACTION_CASES, Case, load_cases

FRONTMATTER = """---
type: review
project: "[[sazon-screener]]"
date: 2026-10-19
---
"""


def _value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "/".join(_value(v) for v in value) if value else "[]"
    return str(value)


def gold_line(gold: dict[str, Any]) -> str:
    parts = [f"intent={_value(gold['intent'])}"]
    parts += [f"{name}={_value(value)}" for name, value in (gold.get("fields") or {}).items()]
    parts += [
        f"lang={_value(gold['language'])}",
        f"matrix={_value(gold['matrix_language'])}",
        f"explicit_lang={_value(gold.get('explicit_language_request'))}",
    ]
    if "code_switched" in gold:
        parts.append(f"switched={_value(gold['code_switched'])}")
    parts.append(f"sentiment={_value(gold['sentiment'])}")
    if "question" in gold:
        parts.append(f"question={_value(gold['question'])}")
    return " · ".join(parts)


def _escape(text: str) -> str:
    """Show invisible and lookalike characters as code points."""
    out = []
    for char in text:
        code = ord(char)
        hidden = not char.isprintable() or unicodedata.category(char) == "Cf"
        if hidden or 0x0400 <= code <= 0x04FF or 0xFF00 <= code <= 0xFFEF:
            out.append(f"<U+{code:04X}>")
        else:
            out.append(char)
    return "".join(out).replace("`", "'")


Misses = dict[str, list[str]]


def short_model(label: str) -> str:
    return label.split("/", 1)[-1]


def miss_reason(call: dict[str, Any]) -> str:
    if call["output"] is None:
        return "no output"
    score = call["score"]
    wrong = sorted(set(score["fp"]) | set(score["fn"]))
    checks = ("intent", "matrix_language", "explicit_language_request")
    wrong += [name for name in checks if not score["checks"].get(name, True)]
    return ", ".join(wrong)


def load_misses(*paths: Path) -> tuple[Misses, dict[str, int], int]:
    """Per case id, `model (what it got wrong)` for every model that failed the case, and how
    many models ran it."""
    misses: Misses = defaultdict(list)
    runs: dict[str, int] = defaultdict(int)
    models = 0
    for path in paths:
        results = json.loads(path.read_text())
        models = max(models, len(results["models"]))
        for call in results["calls"]:
            runs[call["case_id"]] += 1
            if not call["score"]["pass"]:
                misses[call["case_id"]].append(
                    f"{short_model(call['model'])} ({miss_reason(call)})"
                )
    return misses, runs, models


def case_section(
    case: Case,
    misses: Misses | None = None,
    runs: dict[str, int] | None = None,
    comment: str | None = None,
) -> str:
    country = f" ({case['country']})" if case.get("country") else ""
    lines = [
        f"### {case['id']}",
        f"{case['locale']}{country} · stage `{case['stage']}` · today {case['today']} · "
        f"{case.get('source', 'seed')}",
        "",
    ]
    for turn in case.get("prior") or ():
        who = "Ana" if turn["role"] == "assistant" else "Candidate"
        lines.append(f"> {who}: {turn['text']}")
    lines.append(f"> **Candidate:** `{_escape(case['message'])}`")
    if "raw" in case:
        lines.append(f"> raw: `{_escape(case['raw'])}`")
    lines += ["", f"**Gold:** `{gold_line(case['gold'])}`"]
    if case.get("notes"):
        lines.append(f"*Note:* {case['notes']}")
    if misses is not None:
        missed = misses.get(case["id"], [])
        ran = (runs or {}).get(case["id"], 0)
        summary = f"{len(missed)}/{ran}: " + "; ".join(missed) if missed else f"none of {ran}"
        lines.append(f"*Missed by:* {summary}")
    if comment is None:
        lines += ["", "Comment:", ""]
    else:
        lines += ["", f"Comment: {comment}".rstrip(), ""]
    return "\n".join(lines)


def render(
    cases: list[Case],
    misses: Misses | None = None,
    runs: dict[str, int] | None = None,
    models: int = 0,
) -> str:
    groups: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        groups[case["category"]].append(case)
    out = [
        FRONTMATTER,
        "# Extraction gold labels: review",
        "",
        f"{len(cases)} cases from `evals/model_selection/extraction_cases.yaml`. Each gold line "
        "lists only the fields the candidate stated; `a/b` means either label is accepted. "
        "Write corrections after **Comment:**."
        + (
            f" *Missed by* lists the benchmark models (of {models}) whose extraction failed the "
            "case and what they got wrong: when most models miss a case, check the label first."
            if misses is not None
            else ""
        ),
        "",
        " · ".join(f"[[#{cat}]] ({len(items)})" for cat, items in groups.items()),
        "",
    ]
    for category, items in groups.items():
        out += [f"## {category}", ""]
        out += [case_section(case, misses, runs) for case in items]
    return "\n".join(out).rstrip() + "\n"


# --- split notes ---

INDEX = "sazon-screener-gold-labels-index"
_SECTION = re.compile(r"^### (\S+)\s*$", re.MULTILINE)


def read_comments(folder: Path) -> dict[str, str]:
    """The text after `Comment:` in every case section of the notes in `folder`, by case id.
    Empty comments are left out."""
    comments: dict[str, str] = {}
    for note in sorted(folder.glob("*.md")):
        text = note.read_text(encoding="utf-8")
        heads = list(_SECTION.finditer(text))
        if not heads:
            continue
        ends = [head.start() for head in heads[1:]] + [len(text)]
        for head, end in zip(heads, ends, strict=True):
            body = text[head.end() : end]
            _, found, after = body.partition("\nComment:")
            comment = after.strip()
            if found and comment:
                comments[head.group(1)] = comment
    return comments


def note_name(position: int, category: str) -> str:
    return f"{position:02d}-{category.replace('_', '-')}"


def render_split(
    cases: list[Case],
    comments: dict[str, str],
    misses: Misses | None = None,
    runs: dict[str, int] | None = None,
    models: int = 0,
) -> dict[str, str]:
    """One note per category plus the index, keyed by file name."""
    groups: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        groups[case["category"]].append(case)
    notes: dict[str, str] = {}
    entries: list[str] = []
    for position, (category, items) in enumerate(groups.items(), start=1):
        name = note_name(position, category)
        entries.append(f"- [[{name}]] ({len(items)} cases)")
        head = [
            "---",
            "type: review",
            'project: "[[sazon-screener]]"',
            f"category: {category}",
            "---",
            "",
            f"# Gold labels: {category}",
            "",
            f"Part of [[{INDEX}]]. Write corrections after **Comment:**.",
            "",
        ]
        sections = [case_section(c, misses, runs, comments.get(c["id"], "")) for c in items]
        notes[f"{name}.md"] = "\n".join(head + sections).rstrip() + "\n"
    index = [
        "---",
        "type: review",
        'project: "[[sazon-screener]]"',
        "---",
        "",
        "# Gold labels: review index",
        "",
        "The review is split into one note per category so Obsidian stays responsive. Write "
        "corrections after **Comment:** in each case; comments are merged back into the dataset "
        "by case id.",
        "",
        f"*Missed by* lists the benchmark models (of {models}) whose extraction failed the case: "
        "when most models miss a case, check the label first. City labels are the candidate's "
        "words; code maps them to a location (district, city, country).",
        "",
        *entries,
    ]
    notes[f"{INDEX}.md"] = "\n".join(index) + "\n"
    return notes


def main() -> None:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--out", type=Path, help="one Markdown review sheet")
    parser.add_argument(
        "--results", type=Path, action="append", default=[], help="extraction results JSON"
    )
    target.add_argument("--split", type=Path, help="folder of per-category notes")
    args = parser.parse_args()
    misses, runs, models = load_misses(*args.results) if args.results else (None, None, 0)
    cases = load_cases(EXTRACTION_CASES)
    if args.split is None:
        args.out.write_text(render(cases, misses, runs, models))
        print(f"wrote {args.out}")
        return
    comments = read_comments(args.split)
    notes = render_split(cases, comments, misses, runs, models)
    kept = {case_id for case_id in comments if any(c["id"] == case_id for c in cases)}
    lost = sorted(set(comments) - kept)
    if lost:
        raise SystemExit(f"comments for cases no longer in the dataset: {', '.join(lost)}")
    for name, text in notes.items():
        (args.split / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(notes)} notes to {args.split}, {len(kept)} comments kept")


if __name__ == "__main__":
    main()
