"""Write sample conversations from an e2e run to `docs/sample-conversations/`.

    uv run --project backend python -m evals.e2e.export_samples [--results <e2e json> ...]
        [--combo A] [--personas happy_es,...] [--out docs/sample-conversations]

Defaults to the newest live results file and combo A. With several results files, a persona's
conversation comes from the last file that has it (a rerun after a fix). Each conversation
becomes `<persona>.md` (transcript with widget choices, final profile, recruiter summary,
outcome and flags) and `README.md` lists them.
"""

import argparse
import json
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.e2e.personas import load_personas
from evals.model_selection.client import RESULTS_DIR

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "docs" / "sample-conversations"
DEFAULT_SAMPLES = (
    "happy_es",
    "happy_mx",
    "en_spain",
    "spanglish_mty",
    "video_interview",
    "faq_mid_flow",
    "implausible_experience",
    "no_license",
    "out_of_area_pool_mx",
    "no_free_slots",
    "slots_none_work",
    "injection_late",
    "troll_injection",
    "abusive_then_calm",
    "frustrated_pause",
)
TAG_BASE = 0xE0000


def latest_results(directory: Path = RESULTS_DIR) -> Path | None:
    files = sorted(p for p in directory.glob("e2e_*.json") if "offline" not in p.name)
    return files[-1] if files else None


def visible(text: str) -> str:
    """The text with invisible characters removed, and any smuggled tag text spelled out."""
    shown: list[str] = []
    hidden: list[str] = []
    for char in text:
        code = ord(char)
        if TAG_BASE <= code < TAG_BASE + 0x80:
            hidden.append(chr(code - TAG_BASE))
        elif unicodedata.category(char) == "Cf":
            continue
        else:
            shown.append(char)
    out = "".join(shown).strip()
    if hidden:
        out += f' *[invisible Unicode tag characters spelling: "{"".join(hidden)}"]*'
    return out


def describe_widget(ui: Mapping[str, Any] | None) -> str | None:
    if not ui:
        return None
    kind = ui.get("kind")
    if kind in ("quick_replies", "multi_select"):
        label = "Buttons" if kind == "quick_replies" else "Multi-select"
        link = ui.get("link")
        if not ui["options"]:
            return f"Link: {link['label']}" if link else None
        text = f"{label}: " + " · ".join(o["label"] for o in ui["options"])
        return text + (f" (link: {link['label']})" if link else "")
    if kind == "slots":
        return "Interview times: " + " · ".join(
            f"{s['label']} ({s['location']})" for s in ui["slots"]
        )
    if kind == "confirm":
        return "Summary card: " + "; ".join(f"{f['label']}: {f['value']}" for f in ui["fields"])
    if kind == "phone":
        return f"Phone entry with prefix picker (ES +34 / MX +52, {ui['selected']} selected)"
    if kind == "booking":
        return f"Booked: {ui['label']}, {ui['location']}, {ui['address']}"
    return None


def outcome_line(run: Mapping[str, Any]) -> str:
    outcome = run["outcome"]
    if outcome == "qualified" and run.get("booked"):
        outcome += f", interview booked ({run.get('booking')})"
    elif outcome == "qualified" and run.get("slots_declined"):
        outcome += ", no interview time free or suited; application saved"
    elif outcome == "qualified" and run.get("talent_pool_opt_in") is not None:  # before SAZ-42
        outcome += ", no interview time suited; talent pool " + (
            "accepted" if run["talent_pool_opt_in"] else "declined"
        )
    elif outcome == "disqualified":
        outcome += f" ({run.get('disqualify_reason')})"
        if run.get("talent_pool_opt_in") is not None:
            outcome += ", talent pool " + ("accepted" if run["talent_pool_opt_in"] else "declined")
    return outcome


def render(run: Mapping[str, Any], models: str) -> str:
    flags = ", ".join(run.get("quality_flags") or ()) or "none"
    result = run.get("result") or {}
    failed = [name for name, ok in (result.get("checks") or {}).items() if ok is False]
    lines = [
        f"# {run.get('title') or run['persona']}",
        "",
        f"- **Outcome:** {outcome_line(run)}",
        f"- **Quality flags:** {flags}",
        f"- **Reply language at the end:** {run.get('final_language')}",
        f"- **Turns:** {run.get('candidate_turns')} candidate messages; models {models}",
        "- **Checks:** " + ("all passed" if not failed else "failed " + ", ".join(failed)),
        "",
        "## Transcript",
        "",
    ]
    for turn in run["turns"]:
        if turn["speaker"] == "ana":
            lines.append(f"**Ana:** {turn['text']}")
            widget = describe_widget(turn.get("ui"))
            if widget:
                lines.append(f"<br>*{widget}*")
        else:
            how = " (widget)" if turn.get("widget") else ""
            said = turn.get("sent") or turn.get("text") or ""
            lines.append(f"**Candidate{how}:** {visible(said)}")
        lines.append("")
    lines += [
        "## Final profile",
        "",
        "```json",
        json.dumps(run.get("profile") or {}, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Recruiter summary",
        "",
    ]
    summary = run.get("summary") or {}
    lines += [f"- {line}" for line in summary.get("lines") or ()] or ["(none)"]
    if summary.get("flags"):
        lines += ["", "Summary flags: " + ", ".join(summary["flags"])]
    failures = result.get("failures") or []
    if failures:
        lines += ["", "## Check failures", ""]
        lines += [f"- `{f['check']}`: {visible(str(f['detail']))}" for f in failures]
    return "\n".join(lines).rstrip() + "\n"


def export(
    payloads: Sequence[Mapping[str, Any]],
    combo: str,
    persona_ids: Sequence[str],
    out_dir: Path,
) -> list[Path]:
    titles = {persona.id: persona.title for persona in load_personas()}
    runs = {
        r["persona"]: {**r, "title": titles.get(r["persona"], r.get("title"))}
        for payload in payloads
        for r in payload["conversations"]
        if r["combo"] == combo and r.get("repeat", 1) == 1
    }
    roles = (payloads[0].get("meta") or {}).get("combos", {}).get(combo) or {}
    models = " / ".join(dict.fromkeys(roles.values())) if roles else combo
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    index = [
        "# Sample conversations",
        "",
        "Simulated candidates from the end-to-end evals (`evals/e2e/personas.yaml`) talking to "
        f"the real screening service by web chat, with the production models ({models}). "
        "Widget taps are marked; each file ends with the final profile and the recruiter "
        "summary.",
        "",
    ]
    index += ["| Conversation | Outcome | Flags |", "|---|---|---|"]
    for persona_id in persona_ids:
        run = runs.get(persona_id)
        if run is None:
            print(f"no {combo} run for {persona_id}", file=sys.stderr)
            continue
        path = out_dir / f"{persona_id}.md"
        path.write_text(render(run, models), encoding="utf-8")
        written.append(path)
        flags = ", ".join(run.get("quality_flags") or ()) or "-"
        title = run.get("title") or persona_id
        index.append(f"| [{title}]({persona_id}.md) | {outcome_line(run)} | {flags} |")
    (out_dir / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.e2e.export_samples")
    parser.add_argument("--results", type=Path, nargs="+", default=None)
    parser.add_argument("--combo", default="A")
    parser.add_argument("--personas", default=",".join(DEFAULT_SAMPLES))
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    latest = latest_results()
    sources: list[Path] = args.results or ([latest] if latest else [])
    if not sources:
        print("no live e2e results yet; run python -m evals.e2e first", file=sys.stderr)
        return 1
    payloads = [json.loads(source.read_text(encoding="utf-8")) for source in sources]
    ids = [p.strip() for p in args.personas.split(",") if p.strip()]
    written = export(payloads, args.combo, ids, args.out)
    names = ", ".join(source.name for source in sources)
    print(f"wrote {len(written)} conversations from {names} to {args.out}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
