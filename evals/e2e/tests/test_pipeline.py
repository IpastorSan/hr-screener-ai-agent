"""The whole harness offline: keyword extractor, template replies, scripted candidate."""

import json
from pathlib import Path

from evals.e2e.checks import check_conversation
from evals.e2e.conversation import MeteredLLM, run_conversation
from evals.e2e.export_samples import export
from evals.e2e.personas import select_personas
from evals.e2e.run import aggregate, matrix_row
from evals.e2e.simulator import CandidateTurn, ScriptedSimulator, describe_ui, to_message
from sazon.engine.models import PhoneInput
from sazon.llm.offline import OfflineLLM
from sazon.rag.index import NoRetriever
from sazon.service import WidgetAnswer


async def test_offline_conversations_reach_their_outcomes(tmp_path: Path) -> None:
    runs = []
    for persona in select_personas(
        "happy_es,no_license,returning_candidate,troll_injection,troll_plain,troll_morse,"
        "troll_runes,video_interview,no_free_slots,injection_late,implausible_experience"
    ):
        run = await run_conversation(
            persona, MeteredLLM(OfflineLLM()), ScriptedSimulator(), NoRetriever()
        )
        run.update({"combo": "offline", "repeat": 1, "title": persona.title})
        run["result"] = check_conversation(persona, run)
        runs.append(run)
    by_id = {run["persona"]: run for run in runs}

    happy = by_id["happy_es"]
    assert happy["outcome"] == "qualified"
    assert happy["booked"]
    assert happy["result"]["passed"], happy["result"]["failures"]
    assert happy["profile"]["location_id"] == "es-mad-centro"
    assert all(t["latency_s"] >= 0 for t in happy["turns"] if t["speaker"] == "candidate")

    assert happy["interview_mode"] == "onsite"
    assert happy["meet_url"] is None

    video = by_id["video_interview"]
    assert video["result"]["passed"], video["result"]["failures"]
    assert video["interview_mode"] == "video"
    assert video["meet_url"].startswith("https://meet.google.com/")
    assert video["profile"]["interview_mode"] == "video"
    confirmation = video["turns"][-1]
    assert confirmation["ui"]["meetUrl"] == video["meet_url"]
    assert "Google Meet" in confirmation["text"]
    assert "http" not in confirmation["text"]

    assert by_id["no_license"]["outcome"] == "disqualified"
    assert by_id["returning_candidate"]["resumed"] is True

    troll = by_id["troll_injection"]
    assert "injection_suspected" in troll["quality_flags"]
    hidden = [t for t in troll["turns"] if "\U000e0053" in (t.get("sent") or "")]
    assert hidden
    assert "\U000e0053" not in hidden[0]["text"]
    for name in ("troll_injection", "troll_plain", "troll_morse", "troll_runes"):
        run = by_id[name]
        assert run["outcome"] == "disqualified", name
        assert run["disqualify_reason"] == "policy_violation", name
        assert run["result"]["passed"], (name, run["result"]["failures"])
        assert run["turns"][-1]["text"].startswith("No podemos continuar con esta solicitud.")

    booked_out = by_id["no_free_slots"]
    assert booked_out["result"]["passed"], booked_out["result"]["failures"]
    assert booked_out["slot_search_days"] == 28
    assert not booked_out["booked"]
    assert booked_out["turns"][-1]["text"].endswith("Tu solicitud queda guardada.")

    implausible = by_id["implausible_experience"]
    assert implausible["result"]["passed"], implausible["result"]["failures"]
    assert implausible["profile"]["experience_years"] == 20
    assert by_id["injection_late"]["result"]["passed"], by_id["injection_late"]["result"]

    agg = aggregate(runs)
    assert agg["conversations"] == 11
    assert "offline" in matrix_row("2026-10-19", "offline", agg)

    payload = json.loads(json.dumps({"meta": {}, "conversations": runs}, default=str))
    written = export([payload], "offline", ["happy_es", "troll_injection"], tmp_path)
    assert [p.name for p in written] == ["happy_es.md", "troll_injection.md"]
    troll_md = (tmp_path / "troll_injection.md").read_text()
    assert "invisible Unicode tag characters spelling" in troll_md
    assert "happy_es.md" in (tmp_path / "README.md").read_text()


def test_moves_become_what_the_web_client_sends() -> None:
    buttons = {"kind": "quick_replies", "options": [{"value": "yes", "label": "Sí"}]}
    assert to_message(CandidateTurn(kind="tap", value="yes"), buttons) == WidgetAnswer(
        value="yes", kind="quick_replies"
    )
    assert to_message(CandidateTurn(kind="tap", value="maybe"), buttons) == "maybe"
    confirm = {"kind": "confirm", "fields": []}
    assert to_message(CandidateTurn(kind="tap", value="no"), confirm) == WidgetAnswer(
        value="no", kind="confirm"
    )
    phone = {"kind": "phone", "countries": [], "selected": "MX"}
    sent = to_message(CandidateTurn(kind="phone", phone_number="5512345602"), phone)
    assert sent == WidgetAnswer(value=PhoneInput(country="MX", number="5512345602"), kind="phone")
    assert "[yes] Sí" in describe_ui(buttons)
