"""Mutation audit: check that the tests catch deliberate bugs in the rules that matter.

Each mutant patches one line of `backend/src/sazon` in a throwaway copy of the repo and runs
only the tests that guard its rule; the mutant is killed when those tests fail. The unmutated
copy runs the whole suite first, and nothing else runs unless it passes. The working tree is
never touched. Exit status 0 means every mutant was killed.

A pre-release check, not a per-change gate. From the repo root:

    make mutation-audit                     # the whole audit
    CHECK=1 make mutation-audit             # check every anchor occurs once; runs nothing
    ONLY=W1,Y14 make mutation-audit         # some mutants, by id

Environment:
    MUT_ROOT       where the copies go (default: a new temporary directory)
    MUT_PYTHON     the interpreter (default: backend/.venv/bin/python)
    MUT_WORKERS    mutants run in parallel (default 8)
    MUT_TIMEOUT_S  per-mutant limit; its process group is killed and it counts as killed
                   (default 120)
"""

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SOURCE = "src/sazon"
TIMEOUT_S = int(os.environ.get("MUT_TIMEOUT_S", "120"))
# Kept out of every copy. The embedding model in `.cache` is read from the checkout instead.
IGNORE = shutil.ignore_patterns(
    ".venv",
    "node_modules",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".env",
    "*.db",
)


class Mutant(NamedTuple):
    name: str  # "<id> <what the bug is>"
    file: str  # under backend/src/sazon
    old: str  # must occur exactly once
    new: str

    @property
    def id(self) -> str:
        return self.name.split()[0]


class Rule(NamedTuple):
    tests: list[str]  # under backend/, the tests that guard the rule
    mutants: list[Mutant]


class Result(NamedTuple):
    name: str
    status: str  # PASSED or BROKEN (baseline); KILLED or SURVIVED (mutant); BADPATCH
    detail: list[str]


ENGINE = "tests/engine"
SERVICE = "tests/test_service.py"
VOICE = "tests/api/test_voice.py"
RESUME = "tests/test_resume_and_overview.py"
AUTH = "tests/api/test_auth.py"

RULES: dict[str, Rule] = {
    "injection auto-rejects": Rule(
        [ENGINE, SERVICE, "tests/llm/test_llm.py"],
        [
            Mutant(
                "W1 no reject after consent",
                "engine/interrupts.py",
                "    if turn.injected:\n        return _reject(state)",
                "    if False:\n        return _reject(state)",
            ),
            Mutant(
                "W2 an injection with the consent is not rejected",
                "engine/states/consent.py",
                "        if turn.injected:\n            return _reject(state)",
                "        if False:\n            return _reject(state)",
            ),
            Mutant(
                "W4 the booking is kept on reject",
                "engine/turn.py",
                "    state = state.at(Closed(variant=Disqualified(reason=DisqualifyReason.POLICY",
                "    state = state if state.booking else state.at(Closed(variant=Disqualified("
                "reason=DisqualifyReason.POLICY",
            ),
            Mutant(
                "W5 an injection after qualifying is ignored",
                "engine/interrupts.py",
                "    if turn.injected and state.outcome == Outcome.QUALIFIED:\n        return "
                "_reject(state)",
                "    if False:\n        return _reject(state)",
            ),
            Mutant(
                "W6 unknown field names do not count",
                "engine/machine.py",
                "    return suspicious or bool(_unknown_fields(extraction))",
                "    return suspicious",
            ),
            Mutant(
                "W9 an opt-out with an injection is rejected",
                "engine/interrupts.py",
                (
                    '    Interrupt("opt-out", OPEN, Placement.BEFORE, _opting_out, OPT_OUT),\n'
                    '    Interrupt("injection", CONSENTED, Placement.BEFORE, _injection,'
                    " INJECTION),\n"
                ),
                (
                    '    Interrupt("injection", CONSENTED, Placement.BEFORE, _injection,'
                    " INJECTION),\n"
                    '    Interrupt("opt-out", OPEN, Placement.BEFORE, _opting_out, OPT_OUT),\n'
                ),
            ),
            Mutant(
                "W10 a refused extraction skips the engine even when flagged",
                "service.py",
                "and not understood and not read.suspicious:",
                "and not understood:",
            ),
            Mutant(
                "W11 fixed closes go to the model",
                "llm/pydantic_ai_impl.py",
                (
                    "        if is_fixed(action) or is_unknown_answer(action, ctx):\n"
                    "            call = "
                ),
                "        if False:\n            call = ",
            ),
            Mutant(
                "W12 no close is fixed",
                "llm/fallback_templates.py",
                "and not agent().talent_pool(action.reason)",
                "and False",
            ),
        ],
    ),
    "detector vectors": Rule(
        ["tests/engine/test_text.py"],
        [
            Mutant(
                "W16 runes not flagged",
                "engine/text.py",
                "        or has_runes(text)\n",
                "",
            ),
            Mutant(
                "W17 Morse not checked",
                "engine/text.py",
                "        or morse_runs(text)\n",
                "",
            ),
            Mutant(
                "W20 look-alike letters not mapped",
                "engine/text.py",
                "    folded = fold(text.translate(_HOMOGLYPHS))",
                "    folded = fold(text)",
            ),
            Mutant(
                "W22 base64 not checked",
                "engine/text.py",
                "        or any(_phrases(decoded) for decoded in _base64_texts(text))\n",
                "",
            ),
        ],
    ),
    "strict license": Rule(
        [ENGINE, SERVICE],
        [
            Mutant(
                "W24 not-valid words ignored",
                "engine/validators.py",
                "for match in not_valid.finditer(clause):",
                'for match in not_valid.finditer(""):',
            ),
            Mutant(
                "W25 negation ignored ('no está vencida')",
                "engine/validators.py",
                "            if not _LICENSE_NEGATED.search(clause[: match.start()]):",
                "            if True:",
            ),
            Mutant(
                "W26 a learner's permit is a yes",
                "engine/validators.py",
                '    "permiso de aprendiz",\n',
                "",
            ),
            Mutant(
                "W27 not valid recorded as no license",
                "engine/screening.py",
                '    ReasonCode.LICENSE_NOT_VALID: "not_valid",\n',
                "",
            ),
            Mutant(
                "W28 no license backstop from the words",
                "service.py",
                "extraction = with_credential_reading(state, extraction, read.text)",
                "pass",
            ),
            Mutant(
                "X5 a denied problem is not a yes (the bug)",
                "engine/validators.py",
                "if denied or _LICENSE_YES.search(rest):",
                "if _LICENSE_YES.search(rest):",
            ),
            Mutant(
                "M9 'en tramite'/'in progress' strings no longer count as in-progress",
                "engine/validators.py",
                "        if in_progress.search(clause):",
                "        if False:",
            ),
        ],
    ),
    "gates and out-of-area read-back": Rule(
        [ENGINE],
        [
            Mutant(
                "N1 out of area closes without confirming",
                "engine/states/asking.py",
                (
                    "    if _confirms_place(place, confidence, read_back):\n        return "
                    "_close(state, Outcome.DISQUALIFIED, reason)"
                ),
                "    if True:\n        return _close(state, Outcome.DISQUALIFIED, reason)",
            ),
            Mutant(
                "N2 a no to the place read-back closes anyway",
                "engine/states/asking.py",
                (
                    "    if extraction.intent != Intent.CONFIRM or pending.field in "
                    "extraction.fields:\n        if pending.location_id"
                ),
                "    if pending.field in extraction.fields:\n        if pending.location_id",
            ),
            Mutant(
                "N3 city confidence ignored in reply to the place read-back",
                "engine/states/asking.py",
                "fold(read_back.value) or confidence >= MIN_GATE_CONFIDENCE",
                "fold(read_back.value) or True",
            ),
            Mutant(
                "N4 license confidence ignored",
                "engine/states/asking.py",
                "unsure_no = result.value is False and confidence < spec.min_confidence",
                "unsure_no = False",
            ),
            Mutant(
                "N12 the place read-back yes does not close",
                "engine/states/asking.py",
                (
                    "    if spec.type == FieldType.LOCATION:\n        return _close(state, "
                    "Outcome.DISQUALIFIED, _out_of_area_reason(spec)), pending\n"
                ),
                "    if False:\n        return state, pending\n",
            ),
            Mutant(
                "M4 required data left unresolved carries on instead of closing",
                "engine/states/asking.py",
                (
                    "    if spec.required is not None:\n"
                    "        return _close(state, Outcome.DISQUALIFIED, spec.required)\n"
                ),
                "",
            ),
        ],
    ),
    "reopening after a close": Rule(
        [ENGINE],
        [
            Mutant(
                "Y1 a gate close is never reopenable",
                "engine/states/asking.py",
                "reopenable = gate is not None and gate.reopenable",
                "reopenable = False",
            ),
            Mutant(
                "Y2 an injection may reopen",
                "engine/machine.py",
                "and not injected and _reopens(",
                "and _reopens(",
            ),
            Mutant(
                "Y3 no voice license check",
                "engine/states/asking.py",
                "if closes is not None and read_back_no and field not in state.read_back:",
                "if False:",
            ),
        ],
    ),
    "talent pool consent only": Rule(
        [ENGINE, SERVICE, "tests/api/test_api.py::test_the_talent_pool_filter"],
        [
            Mutant(
                "M5 talent pool opt-in always true",
                "engine/states/closed.py",
                "opt_in = extraction.intent == Intent.CONFIRM",
                "opt_in = True",
            ),
            Mutant(
                "K1 the opt-in is not stored on the candidate",
                "service.py",
                "        candidate.talent_pool = state.talent_pool_opt_in is True\n",
                "",
            ),
            Mutant(
                "K3 the saved application still waits for an answer",
                "engine/states/closed.py",
                "    offered = agent().talent_pool(state.disqualify_reason)\n",
                (
                    "    offered = agent().talent_pool(state.disqualify_reason) or "
                    "state.slots_declined\n"
                ),
            ),
        ],
    ),
    "slots and booking": Rule(
        ["tests/scheduling", "tests/engine/test_scheduling_flow.py"],
        [
            Mutant(
                "S1 a slot id that was not offered gets booked",
                "engine/states/interview.py",
                "if choice is not None and any(slot.id == choice for slot in offering.slots):",
                "if choice is not None and choice not in (NONE_WORK, CHANGE):",
            ),
            Mutant(
                "S4 booked slot offered again on reschedule",
                "engine/states/interview.py",
                "exclude = (*exclude, state.booking.slot_id)",
                "exclude = exclude",
            ),
            Mutant(
                "S5 opt-out after booking keeps the booking",
                "engine/interrupts.py",
                "        return _withdraw(state), _opt_out_end(state, booking)",
                "        return state, _opt_out_end(state, booking)",
            ),
            Mutant(
                "S14 opt-out while new times are on offer keeps the application qualified",
                "engine/interrupts.py",
                "_withdraw(state) if booking is not None else",
                "_withdraw(state) if state.booked is not None else",
            ),
            Mutant(
                "S16 a cancelled interview leaves the application qualified",
                "engine/interrupts.py",
                "    return state.at(Closed(variant=Withdrawn()))",
                "    return state.at(Closed(variant=Qualified()))",
            ),
            Mutant(
                "S17 a withdrawn application is offered again to a returning candidate",
                "storage/repo.py",
                "CandidateStatus.QUALIFIED, CandidateStatus.ABANDONED)",
                "CandidateStatus.QUALIFIED, CandidateStatus.ABANDONED, CandidateStatus.WITHDRAWN)",
            ),
            Mutant(
                "S15 a cancel request is read as a request to move the interview",
                "engine/interrupts.py",
                "    if not cancel.requested:\n        return None\n",
                "    if True:\n        return None\n",
            ),
            Mutant(
                "S6 taken slot offered again",
                "engine/states/interview.py",
                "exclude=(request.slot_id,)",
                "exclude=()",
            ),
            Mutant(
                "S12 booking allows a started time",
                "scheduling/service.py",
                "if slot is None or to_utc(slot.starts_at) <= to_utc(now):",
                "if slot is None:",
            ),
            Mutant(
                "A5 daily maximum ignored",
                "scheduling/availability.py",
                "return interviews_on(person, person.local_day(start)) < person.max_per_day",
                "return True",
            ),
            Mutant(
                "A12 no free/busy re-check at booking",
                "scheduling/service.py",
                (
                    "free = assignment_order([p for p in people if is_free(p, start, "
                    "config.rules)], start)"
                ),
                "free = assignment_order(people, start)",
            ),
            Mutant(
                "A13 no database re-check under the lock (race)",
                "scheduling/service.py",
                (
                    "        if not is_free(fresh, start, config.rules):\n            "
                    "session.rollback()"
                ),
                "        if False:\n            session.rollback()",
            ),
            Mutant(
                "A14 unreadable or unconnected calendars count as free",
                "scheduling/service.py",
                "        eligible = [r for r in eligible if r.id in found]\n",
                "",
            ),
            Mutant(
                "A16 interview mode ignored",
                "scheduling/service.py",
                "        and mode in recruiter.interview_modes",
                "        and True",
            ),
            Mutant(
                "K5 no wider window",
                "scheduling/service.py",
                "    if not times and config.days < WIDER_DAYS:",
                "    if False:",
            ),
            Mutant(
                "O1 an in-person slot holds two interviews",
                "scheduling/onsite.py",
                (
                    "    if overlapping(venue, start, rules) >= venue.per_slot:\n"
                    "        return False\n"
                ),
                "",
            ),
            Mutant(
                "O2 a location's daily maximum ignored",
                "scheduling/onsite.py",
                "    return interviews_on(venue, venue.local_day(start)) < venue.max_per_day",
                "    return True",
            ),
            Mutant(
                "O3 the organizer's in-person events count as their video interviews",
                "scheduling/service.py",
                (
                    "        .where(col(Booking.mode) == InterviewMode.VIDEO)\n"
                    "        .where(col(Booking.recruiter_id).in_("
                ),
                "        .where(col(Booking.recruiter_id).in_(",
            ),
        ],
    ),
    "calendar push": Rule(
        ["tests/scheduling/test_calendar_push.py"],
        [
            Mutant(
                "X1 a moved booking's card loses the mode and Meet link (the bug)",
                "scheduling/push.py",
                "view = scheduling.booked_view(slot, state.language, booking)",
                "view = scheduling.booked_view(slot, state.language)",
            ),
            Mutant(
                "X2 a moved video interview gets the in-person notice",
                "scheduling/push.py",
                (
                    "notice = MOVED_VIDEO_NOTICE if moved.mode == InterviewMode.VIDEO else "
                    "MOVED_NOTICE"
                ),
                "notice = MOVED_NOTICE",
            ),
            Mutant(
                "O4 a manager's decline is ignored",
                "scheduling/push.py",
                "        return Change.MANAGER_DECLINED",
                "        return Change.NONE",
            ),
            Mutant(
                "Y15 any push token verifies",
                "scheduling/push.py",
                (
                    "token_ok = hmac.compare_digest(channel.token_hash.encode(), "
                    "hash_token(token).encode())"
                ),
                "token_ok = True",
            ),
        ],
    ),
    "auth, SSO, CSRF": Rule(
        [AUTH, "tests/api/test_google_auth.py", "tests/api/test_ops.py", "tests/api/test_api.py"],
        [
            Mutant(
                "A24 Workspace (hd) not checked",
                "calendar/oauth.py",
                "if hosted != self.domain:",
                "if False:",
            ),
            Mutant(
                "A25 unverified email accepted",
                "calendar/oauth.py",
                'if claims.get("email_verified") is not True:',
                "if False:",
            ),
            Mutant(
                "A26 nonce not checked",
                "calendar/oauth.py",
                (
                    'if not hmac.compare_digest(str(claims.get("nonce") or "").encode(), '
                    "flow.nonce.encode()):"
                ),
                "if False:",
            ),
            Mutant(
                "A27 state not checked",
                "api/google_auth.py",
                "if flow is None or not state or not hmac.compare_digest(flow.state, state):",
                "if flow is None:",
            ),
            Mutant(
                "A28 connecting another account's calendar accepted",
                "api/google_auth.py",
                "if recruiter.email != identity.email:",
                "if False:",
            ),
            Mutant(
                "X11 any recruiter reads another's agenda (the bug)",
                "api/interviews.py",
                "    if owner != me.id and not me.is_admin:",
                "    if False:",
            ),
            Mutant(
                "X13 no rate limit on nudges (the bug)",
                "api/candidate.py",
                (
                    '    responses={**NOT_FOUND, 409: {"model": ErrorOut}},\n    '
                    "dependencies=[Depends(limit_messages)],\n"
                ),
                '    responses={**NOT_FOUND, 409: {"model": ErrorOut}},\n',
            ),
        ],
    ),
    "allowed origins": Rule(
        [AUTH],
        [
            Mutant(
                "Y14 any origin may write",
                "api/deps.py",
                'if origin is not None and origin.rstrip("/") not in allowed_origins():',
                "if False:",
            ),
        ],
    ),
    "PII in logs": Rule(
        ["tests/test_log_redaction.py", "tests/test_observability.py"],
        [
            Mutant(
                "L1 access-log arguments not masked in place",
                "logs.py",
                (
                    "                record.args = tuple(_mask(a) if isinstance(a, str) else a for "
                    "a in record.args)\n"
                ),
                "                pass\n",
            ),
        ],
    ),
    "ID numbers masked": Rule(
        [SERVICE, "tests/engine/test_text.py"],
        [
            Mutant(
                "Y13 ID numbers reach the model",
                "engine/text.py",
                "    return _ID_NUMBERS.sub(ID_MASK, text)\n",
                "    return text\n",
            ),
        ],
    ),
    "voice turns and hang-up": Rule(
        [VOICE],
        [
            Mutant(
                "E1 '...' read as words",
                "channels/voice.py",
                'return len(users), words if any(c.isalnum() for c in words) else ""',
                "return len(users), words",
            ),
            Mutant(
                "E2 silence runs a turn",
                "api/voice.py",
                '        if not words:\n            record("voice_silence")',
                '        if False:\n            record("voice_silence")',
            ),
            Mutant(
                "G1 the window is ignored",
                "channels/voice.py",
                "now - at <= self.window_s",
                "True",
            ),
            Mutant(
                "G2 the message count is not compared",
                "channels/voice.py",
                "and earlier_count == count\n",
                "\n",
            ),
            Mutant(
                "G3 any words replace the early ones",
                "channels/voice.py",
                "and continues(earlier_words, words)",
                "and True",
            ),
            Mutant(
                "R1 a running turn is not awaited",
                "channels/voice.py",
                "    if running is not None:\n",
                "    if False:\n",
            ),
            Mutant(
                "R4 a failed turn leaves its waiters hanging",
                "channels/voice.py",
                "        cache.drop(key)\n        return\n",
                "        return\n",
            ),
            Mutant(
                "R5 waiters never get the reply",
                "channels/voice.py",
                "            future.set_result(text)",
                "            pass",
            ),
            Mutant(
                "Y6 end_call without its reason",
                "channels/voice.py",
                "END_CALL_TOOL, json.dumps(END_CALL_ARGS)",
                'END_CALL_TOOL, "{}"',
            ),
            Mutant(
                "Y7 after end_call the goodbye is said again",
                "api/voice.py",
                "after_tool_call(body) or turn_cache(request).hung_up(conversation.id, count)",
                "False",
            ),
        ],
    ),
    "call closing and field fixes": Rule(
        [RESUME, VOICE],
        [
            Mutant(
                "Y4 a booked call never hangs up",
                "service.py",
                "return kind in CALL_CLOSING_ACTIONS",
                "return False",
            ),
            Mutant(
                "Y5 the out-of-area close hangs up before the talent-pool answer",
                "service.py",
                'return not agent().talent_pool(action.get("reason"))',
                "return True",
            ),
            Mutant(
                "Y9 a field fix is ignored",
                "service.py",
                "wrong = field_to_fix(text, bare=state.stage == Stage.CONFIRM)",
                "wrong = None",
            ),
        ],
    ),
    "corrections by name": Rule(
        [ENGINE, "tests/test_fix_reading.py", "tests/llm/test_llm.py"],
        [
            Mutant(
                "C1 only the name, phone and email can be fixed by name",
                "engine/states/asking.py",
                "    if fix is not None and fix in agent().by_id:",
                '    if fix is not None and fix in ("full_name", "phone", "email"):',
            ),
            Mutant(
                "C2 the same place is applied again, not explained",
                "engine/states/asking.py",
                "explaining = state.pending_confirmation is not pending",
                "explaining = False",
            ),
            Mutant(
                "C3 the voice summary reads no values",
                "llm/fallback_templates.py",
                "return action.voice and not action.correcting",
                "return False",
            ),
            Mutant(
                "C4 a field named alone at the summary is not a correction",
                "engine/text.py",
                "            if pattern.fullmatch(folded):",
                "            if False:",
            ),
            Mutant(
                "C5 a detail corrected after booking is not kept",
                "engine/states/interview.py",
                'state.model_copy(update={"profile": profile}), _updated(',
                "state, _updated(",
            ),
        ],
    ),
    "repeat requests": Rule(
        ["tests/test_repeat_request.py", VOICE],
        [
            Mutant(
                "Y8 a repeat request goes to the model",
                "service.py",
                "repeat = not suspicious and asks_to_repeat(text)",
                "repeat = False",
            ),
        ],
    ),
    "reply checks": Rule(
        ["tests/llm"],
        [
            Mutant(
                "Y10 digits allowed by voice",
                "llm/reply_checks.py",
                'if voice and (digits := spoken_digits(text, (reference or "", *quotes))):',
                "if False:",
            ),
            Mutant(
                "Y11 meta talk allowed",
                "llm/reply_checks.py",
                "if meta := meta_talk(text, reference):",
                "if False:",
            ),
        ],
    ),
    "spoken dates": Rule(
        ["tests/engine/test_spoken.py", "tests/llm/test_voice_times.py"],
        [
            Mutant(
                "Y12 days back in words",
                "engine/spoken.py",
                'return f"{_ES_WEEKDAYS[day.weekday()]} {day.day}"',
                'return f"{_ES_WEEKDAYS[day.weekday()]} {es_number(day.day)}"',
            ),
        ],
    ),
}


def anchor_count(root: Path, mutant: Mutant) -> int:
    path = root / SOURCE / mutant.file
    return path.read_text().count(mutant.old) if path.exists() else 0


def prepare(base: Path) -> None:
    """A repo-shaped copy: backend, deploy and docs copied; evals and web, read only, linked."""
    shutil.copytree(BACKEND, base / "backend", symlinks=True, ignore=IGNORE)
    for sibling in ("deploy", "docs"):
        shutil.copytree(REPO / sibling, base / sibling, symlinks=True, ignore=IGNORE)
    for sibling in ("evals", "web"):
        (base / sibling).symlink_to(REPO / sibling)


def environment(work: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    # The copy's src comes before the checkout's editable install.
    env["PYTHONPATH"] = os.pathsep.join([str(work / "src"), str(work.parent)])
    env.setdefault("SAZON_CACHE_DIR", str(BACKEND / ".cache"))
    # The copy has no .env; the assistant endpoint builds its client before validating input.
    env["OPENROUTER_API_KEY"] = "sk-or-dummy-for-mutation-tests"
    return env


def run_tests(python: str, work: Path, tests: list[str]) -> tuple[int, str] | None:
    """Run the tests in their own process group. On timeout the group is killed and the result
    is None; after a normal exit whatever the tests left running is killed too. Output goes to
    a file, so a leftover child holding it open cannot stall the wait."""
    cmd = [python, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"]
    cmd += ["-m", "not live", *tests]
    log = work.parent / "pytest.log"
    with log.open("w") as out:
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=work,
            env=environment(work),
            start_new_session=True,
        )
        try:
            proc.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return None
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    return proc.returncode, log.read_text()


def run_one(python: str, base: Path, slot: Path, mutant: Mutant | None, tests: list[str]) -> Result:
    shutil.copytree(base, slot, symlinks=True)
    try:
        work = slot / "backend"
        name = "baseline (no mutation)"
        if mutant is not None:
            name = mutant.name
            path = work / SOURCE / mutant.file
            text = path.read_text()
            if text.count(mutant.old) != 1:
                return Result(name, "BADPATCH", [f"anchor found {text.count(mutant.old)} times"])
            path.write_text(text.replace(mutant.old, mutant.new))
        outcome = run_tests(python, work, tests)
    finally:
        shutil.rmtree(slot, ignore_errors=True)
    if outcome is None:
        return Result(name, "KILLED", [f"timeout after {TIMEOUT_S}s"])
    code, output = outcome
    failing = [line for line in output.splitlines() if line.startswith(("FAILED", "ERROR"))]
    if mutant is None:
        if code == 0:
            return Result(name, "PASSED", [])
        return Result(name, "BROKEN", failing[:2] or output.splitlines()[-5:])
    return Result(name, "KILLED" if code != 0 else "SURVIVED", failing[:2])


def report(result: Result) -> None:
    print(f"{result.status:9} {result.name}  {result.detail}", flush=True)


def check(mutants: list[tuple[Mutant, list[str]]]) -> int:
    bad = 0
    for mutant, _ in mutants:
        if (count := anchor_count(BACKEND, mutant)) != 1:
            bad += 1
            print(f"BADPATCH  {mutant.name}  anchor found {count} times")
    print(f"mutants: {len(mutants)} | bad anchors: {bad}")
    return 1 if bad else 0


def main() -> int:
    mutants = [(m, rule.tests) for rule in RULES.values() for m in rule.mutants]
    if os.environ.get("CHECK"):
        return check(mutants)
    if only := os.environ.get("ONLY"):
        wanted = set(only.split(","))
        if unknown := wanted - {m.id for m, _ in mutants}:
            print("unknown mutant ids:", ", ".join(sorted(unknown)))
            return 1
        mutants = [(m, tests) for m, tests in mutants if m.id in wanted]
    python = os.environ.get("MUT_PYTHON", str(BACKEND / ".venv/bin/python"))
    if not Path(python).exists():
        print(f"no interpreter at {python}: run `uv sync` in backend/ or set MUT_PYTHON")
        return 1
    workers = int(os.environ.get("MUT_WORKERS", "8"))
    owned = "MUT_ROOT" not in os.environ
    root = Path(os.environ["MUT_ROOT"]) if not owned else Path(tempfile.mkdtemp("-mutation"))
    base = root / "base"
    try:
        shutil.rmtree(base, ignore_errors=True)
        prepare(base)
        baseline = run_one(python, base, root / "work0", None, ["tests"])
        report(baseline)
        if baseline.status != "PASSED":
            return 2

        def run(item: tuple[int, tuple[Mutant, list[str]]]) -> Result:
            i, (mutant, tests) = item
            return run_one(python, base, root / f"work{i}", mutant, tests)

        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(run, enumerate(mutants, start=1)))
    finally:
        shutil.rmtree(root if owned else base, ignore_errors=True)
    for result in results:
        report(result)
    killed = sum(result.status == "KILLED" for result in results)
    print(f"baseline: PASSED | killed: {killed} / {len(results)}")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
