import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import Engine

from sazon.config import get_settings
from sazon.engine import ConversationContext
from sazon.engine.models import Language, PhoneInput
from sazon.engine.slots import Booked
from sazon.engine.ui import Confirm, MultiSelect, PhoneEntry, QuickReplies, Slots, Ui
from sazon.engine.validators import Country
from sazon.llm.interface import LLM, Channel
from sazon.recruiters import TIMEZONES
from sazon.scheduling.service import scheduling_from
from sazon.service import Reply, ScreeningService, WidgetAnswer, export_conversation
from sazon.storage.repo import Repo, init_db, make_engine

PHONE_CHARS = frozenset("0123456789 ")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sazon")
    parser.add_argument("--db", help="Database URL (default: DATABASE_URL)")
    commands = parser.add_subparsers(dest="command", required=True)

    chat = commands.add_parser("chat", help="Talk to Ana in the terminal")
    chat.add_argument("--lang", choices=["es", "en"], default="es")
    chat.add_argument("--country", choices=["ES", "MX"])
    chat.add_argument("--fake", action="store_true", help="Offline: keyword extractor, templates")

    export = commands.add_parser("export", help="Dump a conversation as JSON")
    export.add_argument("conversation_id")

    commands.add_parser("init-db", help="Create or upgrade the tables (same as migrate)")
    commands.add_parser("migrate", help="Upgrade the database to the latest migration")
    purge = commands.add_parser(
        "purge", help="Delete applications idle for longer than RETENTION_DAYS (default 365)"
    )
    purge.add_argument("--days", type=int, help="Override RETENTION_DAYS")
    purge.add_argument("--dry-run", action="store_true", help="Count, delete nothing")
    erase = commands.add_parser(
        "erase", help="Delete one candidate's application and conversations on request"
    )
    who = erase.add_mutually_exclusive_group(required=True)
    who.add_argument("--phone", help="Any format; matched in E.164")
    who.add_argument("--email")
    who.add_argument("--candidate-id")
    erase.add_argument("--dry-run", action="store_true", help="List the matches, delete nothing")
    backup = commands.add_parser("backup", help="Copy the SQLite database online and check it")
    backup.add_argument("dest", help="File to write, e.g. /data/backups/sazon.db")
    close_stale = commands.add_parser(
        "close-stale",
        help="Mark conversations idle for more than 72 hours as abandoned, with their summary",
    )
    close_stale.add_argument("--fake", action="store_true", help="Offline summaries, no model")
    commands.add_parser(
        "rag-index",
        help="Download the local embedding model once and embed the FAQ and the documents",
    )

    commands.add_parser(
        "sync-events",
        help="Create the missing calendar events of upcoming interviews and delete those of "
        "cancelled ones the calendar refused to delete (SCHEDULING_SOURCE=google)",
    )
    migrate_onsite = commands.add_parser(
        "migrate-onsite-bookings",
        help="Move upcoming in-person interviews booked before SAZ-64 to the location's manager "
        "and the ONSITE_ORGANIZER_EMAIL calendar, re-creating their events (idempotent)",
    )
    migrate_onsite.add_argument("--dry-run", action="store_true", help="Count, change nothing")
    watch = commands.add_parser(
        "calendar-watch",
        help="Google Calendar push channels: open, renew (daily) or stop them (needs PUBLIC_URL)",
    )
    watch.add_argument(
        "action",
        choices=["start", "renew", "stop", "status"],
        help="start: open one where missing; renew: replace those expiring soon; stop: close",
    )
    watch.add_argument("--email", help="Only this recruiter's calendar")
    watch.add_argument(
        "--within-hours", type=int, default=48, help="renew: channels expiring within (48)"
    )

    seed = commands.add_parser("seed", help="Fill the database with demo candidates, offline")
    seed.add_argument("--n", type=int, default=60, help="Conversations to create")
    seed.add_argument("--reset", action="store_true", help="Drop all data first")
    seed.add_argument("--seed", type=int, default=78, help="Random seed")

    add_recruiter = commands.add_parser(
        "add-recruiter", help="Create a recruiter account (then `set-password`)"
    )
    add_recruiter.add_argument("--email", required=True)
    add_recruiter.add_argument("--name", required=True)
    add_recruiter.add_argument("--country", choices=["ES", "MX"], required=True)
    add_recruiter.add_argument("--admin", action="store_true", help="Can manage recruiters")
    add_recruiter.add_argument(
        "--locations", default="", help="Comma-separated location ids they interview for"
    )
    set_password = commands.add_parser(
        "set-password",
        help="Set a recruiter's password (prompted, or one line on stdin); ends their sessions",
    )
    set_password.add_argument("--email", required=True)
    set_password.add_argument(
        "--stdin", action="store_true", help="Read the password from standard input"
    )

    agent = commands.add_parser(
        "voice-agent",
        help="Create, update or show the ElevenLabs voice agent (needs ELEVENLABS_API_KEY)",
    )
    agent.add_argument("action", choices=["create", "update", "show"])
    agent.add_argument("--config", help="Agent JSON (default: deploy/elevenlabs/agent.json)")
    agent.add_argument("--agent-id", help="Default: ELEVENLABS_AGENT_ID")
    agent.add_argument(
        "--rotate-secret", action="store_true", help="update: store VOICE_LLM_SECRET again"
    )
    agent.add_argument("--dry-run", action="store_true", help="Print the body, send nothing")

    diagram = commands.add_parser(
        "engine-diagram", help="Write the engine's statechart as Mermaid (docs/engine/states.mmd)"
    )
    diagram.add_argument("path", type=Path)

    new_agent = commands.add_parser(
        "new-agent", help="Write a new agent to fill in: its spec, text packs and journeys"
    )
    new_agent.add_argument("name")
    new_agent.add_argument("--dir", type=Path, help="Default: sazon/agents/<name>")

    args = parser.parse_args(argv)
    if args.command == "new-agent":
        return _new_agent(args.name, args.dir)
    if args.command == "engine-diagram":
        from sazon.engine.diagram import render

        args.path.write_text(render(), encoding="utf-8")
        return 0
    if args.command == "voice-agent":
        return _voice_agent(args)
    if args.command == "rag-index":
        return _rag_index()
    if args.command == "backup":
        return _backup(args.db or get_settings().database_url, args.dest)
    engine = make_engine(args.db)
    init_db(engine)
    repo = Repo(engine)
    if args.command == "chat":
        llm = _llm(args.fake, Language(args.lang))
        return asyncio.run(chat_loop(ScreeningService(llm, repo), args.country, args.lang))
    if args.command == "close-stale":
        service = ScreeningService(_llm(args.fake, Language.ES), repo)
        closed = asyncio.run(service.close_stale())
        print(f"Closed {closed} idle conversations as abandoned.")
        return 0
    if args.command == "seed":
        return asyncio.run(_seed(repo, args.n, args.reset, args.seed))
    if args.command == "sync-events":
        return _sync_events(repo)
    if args.command == "calendar-watch":
        return _calendar_watch(repo, args)
    if args.command == "migrate-onsite-bookings":
        return _migrate_onsite(repo, args.dry_run)
    if args.command == "purge":
        return _purge(repo, args.days, args.dry_run)
    if args.command == "add-recruiter":
        return _add_recruiter(repo, args)
    if args.command == "set-password":
        return _set_password(repo, args.email, sys.stdin if args.stdin else None)
    if args.command == "erase":
        return _erase(repo, args)
    if args.command == "export":
        try:
            data = export_conversation(repo, args.conversation_id)
        except LookupError as exc:
            print(exc, file=sys.stderr)
            return 1
        _print_json(data)
        return 0
    from sazon.storage.migrate import current_revision

    print(f"Database at revision {current_revision(engine)} ({_redacted_url(engine)})")
    return 0


def _new_agent(name: str, directory: Path | None) -> int:
    from sazon.agents.scaffold import new_agent

    try:
        written = new_agent(name, directory)
    except (ValueError, FileExistsError) as exc:
        print(exc, file=sys.stderr)
        return 1
    agent = written if directory else name
    print(f"Wrote {written}. Fill in every TODO, then screen for it with SAZON_AGENT={agent}.")
    return 0


def _voice_agent(args: argparse.Namespace) -> int:
    from sazon.channels.elevenlabs_agent import VoiceAgentError, run

    try:
        result = run(
            args.action,
            get_settings(),
            config=Path(args.config) if args.config else None,
            agent_id=args.agent_id,
            rotate_secret=args.rotate_secret,
            dry_run=args.dry_run,
        )
    except VoiceAgentError as exc:
        print(f"voice-agent: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


def _redacted_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def _backup(url: str, dest: str) -> int:
    from sazon.storage.backup import BackupError, backup

    try:
        size = backup(url, Path(dest))
    except BackupError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Backed up to {dest} ({size / 1e6:.1f} MB), integrity ok")
    return 0


def _purge(repo: Repo, days: int | None, dry_run: bool) -> int:
    from sazon.storage.retention import purge

    retention = days if days is not None else get_settings().retention_days
    if retention < 1:
        print("Retention must be at least one day.", file=sys.stderr)
        return 2
    calendar = scheduling_from(get_settings(), repo).calendar
    report = purge(repo, datetime.now(UTC), retention, dry_run=dry_run, calendar=calendar)
    verb = "Would delete" if dry_run else "Deleted"
    print(
        f"{verb} {report.candidates} candidates and {report.conversations} conversations "
        f"idle for more than {retention} days ({report.events_deleted} calendar events)."
    )
    return _events_left(report.events_failed)


def _sync_events(repo: Repo) -> int:
    from sazon.scheduling.service import delete_stale_events, ensure_events

    config = scheduling_from(get_settings(), repo)
    if config.calendar is None:
        print("SCHEDULING_SOURCE=fixed: there is no calendar to write to.")
        return 0
    print(f"{ensure_events(repo, datetime.now(UTC), config)} interviews got their event.")
    print(f"{delete_stale_events(repo, config)} events of cancelled interviews deleted.")
    return 0


def _migrate_onsite(repo: Repo, dry_run: bool) -> int:
    """Exits 1 when the organizer is missing or some old event could not be deleted."""
    from sazon.scheduling.service import OrganizerMissing, migrate_onsite_bookings

    config = scheduling_from(get_settings(), repo)
    try:
        report = migrate_onsite_bookings(repo, datetime.now(UTC), config, dry_run=dry_run)
    except OrganizerMissing:
        print(
            "ONSITE_ORGANIZER_EMAIL must name an active recruiter whose calendar is connected.",
            file=sys.stderr,
        )
        return 1
    if dry_run:
        print(f"Would move {report.found} upcoming in-person interviews.")
        return 0
    print(
        f"Moved {report.moved} of {report.found} upcoming in-person interviews; "
        f"{report.events} have their new event, {report.failed} left as they were."
    )
    if report.moved > report.events and config.calendar and config.organizer_email:
        print("Run `sazon sync-events` to write the missing events.")
    return 1 if report.failed else 0


def _calendar_watch(repo: Repo, args: argparse.Namespace) -> int:
    """Prints counts and recruiter ids only; exits 1 when a channel could not be opened."""
    from datetime import timedelta

    from sazon import recruiters
    from sazon.scheduling import push

    settings = get_settings()
    watcher = push.watcher_of(scheduling_from(settings, repo).calendar)
    if watcher is None:
        print("SCHEDULING_SOURCE is not google: there are no calendars to watch.")
        return 0
    recruiter_id = None
    if args.email:
        recruiter = recruiters.by_email(repo, args.email)
        if recruiter is None:
            print("No recruiter with that email.", file=sys.stderr)
            return 1
        recruiter_id = recruiter.id
    now = datetime.now(UTC)
    if args.action == "status":
        print(push.channel_health(repo, now))
        return 0
    if args.action == "stop":
        ids = (
            [recruiter_id]
            if recruiter_id
            else sorted({c.recruiter_id for c in push.channels_of(repo)})
        )
        stopped = sum(push.stop_watch(repo, watcher, rid) for rid in ids)
        print(f"Stopped {stopped} channels.")
        return 0
    address = settings.calendar_push_url
    if address is None:
        print("PUBLIC_URL is empty: Google has nowhere to send notifications.", file=sys.stderr)
        return 2
    within = timedelta(0) if args.action == "start" else timedelta(hours=args.within_hours)
    ttl = timedelta(hours=max(1, settings.calendar_watch_ttl_hours))
    report = push.renew(repo, watcher, address, now, ttl, within=within, recruiter_id=recruiter_id)
    print(
        f"Channels: {report.started} started, {report.renewed} renewed, "
        f"{report.stopped} stopped, {report.failed} failed."
    )
    return 1 if report.failed else 0


def _add_recruiter(repo: Repo, args: argparse.Namespace) -> int:
    from sazon import recruiters
    from sazon.engine.service_areas import load_locations

    locations = [part.strip() for part in args.locations.split(",") if part.strip()]
    unknown = sorted(set(locations) - set(load_locations()))
    if unknown:
        print(f"Unknown location ids: {', '.join(unknown)}", file=sys.stderr)
        return 2
    try:
        recruiter = recruiters.create(
            repo,
            datetime.now(UTC),
            name=args.name,
            email=args.email,
            country=args.country,
            is_admin=args.admin,
            location_ids=locations,
        )
    except ValueError:
        print("A recruiter with that email already exists.", file=sys.stderr)
        return 1
    print(f"Added recruiter {recruiter.id}. Set a password with `sazon set-password`.")
    return 0


def _set_password(repo: Repo, email: str, stdin: TextIO | None) -> int:
    """Admin reset, no self-service: the password is prompted for (or read from stdin), never
    taken as an argument, so it stays out of the shell history and the process list."""
    from getpass import getpass

    from sazon import recruiters
    from sazon.auth import password_problem

    recruiter = recruiters.by_email(repo, email)
    if recruiter is None:
        print("No recruiter with that email.", file=sys.stderr)
        return 1
    if stdin is not None:
        password = stdin.readline().rstrip("\n")
    else:
        password = getpass("New password: ")
        if getpass("Repeat it: ") != password:
            print("The passwords do not match.", file=sys.stderr)
            return 1
    if problem := password_problem(password):
        print(problem, file=sys.stderr)
        return 2
    recruiters.update(repo, recruiter.id, datetime.now(UTC), password=password)
    print(f"Password set for recruiter {recruiter.id}; their open sessions have ended.")
    return 0


def _erase(repo: Repo, args: argparse.Namespace) -> int:
    """Every application matching the phone or email is erased (a candidate may have applied
    more than once). Prints ids and counts only."""
    from sazon.storage.retention import erase, find_candidates

    if args.candidate_id:
        ids = [args.candidate_id] if repo.get_candidate(args.candidate_id) else []
    else:
        ids = find_candidates(repo, phone=args.phone, email=args.email)
    if not ids:
        print("No candidate matches.", file=sys.stderr)
        return 1
    failed = 0
    for candidate_id in ids:
        if args.dry_run:
            print(f"Would erase candidate {candidate_id}.")
            continue
        calendar = scheduling_from(get_settings(), repo).calendar
        report = erase(repo, candidate_id, via="cli", calendar=calendar)
        if report is not None:
            print(
                f"Erased candidate {candidate_id}: {report.conversations} conversations, "
                f"{report.bookings} bookings ({report.events_deleted} calendar events)."
            )
            failed += report.events_failed
    return _events_left(failed)


def _events_left(failed: int) -> int:
    """Exit 1 when the calendar refused to delete some events (their ids are in the log)."""
    if not failed:
        return 0
    print(f"{failed} calendar events could not be deleted; the log has their ids.", file=sys.stderr)
    return 1


def _llm(fake: bool, language: Language) -> LLM:
    if fake:
        from sazon.llm.offline import OfflineLLM

        return OfflineLLM(language)
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    from sazon.llm.pydantic_ai_impl import PydanticAILLM

    return PydanticAILLM()


def _rag_index() -> int:
    from sazon.rag.index import build_cache

    try:
        index, cache_dir = build_cache()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    size = sum(
        path.stat().st_size
        for path in cache_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    print(
        f"Retrieval index ready: {index.describe()}, model {index.embedder.name}, "
        f"cache {cache_dir} ({size / 1e6:.0f} MB)"
    )
    return 0


async def _seed(repo: Repo, n: int, reset: bool, rng_seed: int) -> int:
    from sazon.api.seed import reset_db, seed

    if reset and get_settings().production:
        print("Refusing to drop the production database (ENV=production).", file=sys.stderr)
        return 2
    if reset:
        reset_db(repo.engine)
    report = await seed(repo, n, rng_seed=rng_seed)
    print(
        f"Recruiters: {report.recruiters} added (the agent's recruiters file); "
        f"{report.assigned} earlier bookings assigned."
    )
    print(f"Interviews: {report.bookings} booked.")
    print(
        f"Seeded {report.conversations} conversations, {report.candidates} candidates "
        f"({report.completion_rate:.0%} finished screening)."
    )
    print("Outcomes:", dict(report.outcomes))
    print("Drop-off stages:", dict(report.drop_off))
    return 0


async def chat_loop(
    service: ScreeningService,
    country: Country | None,
    lang: str,
    stdin: TextIO = sys.stdin,
    out: TextIO = sys.stdout,
) -> int:
    context = ConversationContext(
        country=country,
        timezone=TIMEZONES[country] if country else None,
        locale=f"{lang}-{country}" if country else lang,
    )
    reply = await service.start(Channel.WEB, context)
    print("Ana (AI assistant)  type 'quit' to stop, a number to pick an option\n", file=out)
    while True:
        _show(reply, out)
        if _finished(reply):
            break
        print("> ", end="", file=out, flush=True)
        line = stdin.readline()
        if not line or line.strip() == "quit":
            break
        if not stdin.isatty():
            print(line.rstrip(), file=out)
        message = parse_input(line.strip(), reply.ui)
        reply = await service.handle_turn(reply.conversation_id, message)

    data = export_conversation(service.repo, reply.conversation_id)
    print("\n--- profile ---", file=out)
    _print_json(data["profile"], out)
    print("--- summary ---", file=out)
    _print_json(data["summary"], out)
    print(f"--- conversation {reply.conversation_id} ---", file=out)
    _print_json(data["conversation"], out)
    return 0


def _finished(reply: Reply) -> bool:
    """Booked, or closed with nothing left to answer."""
    return isinstance(reply.ui, Booked) or (reply.ended and reply.ui is None)


def parse_input(text: str, ui: Ui | None) -> str | WidgetAnswer:
    """A number picks a widget option; '1,3' picks several; digits fill the phone widget."""
    if isinstance(ui, PhoneEntry) and text and set(text) <= PHONE_CHARS:
        return WidgetAnswer(value=PhoneInput(country=ui.selected, number=text))
    if isinstance(ui, PhoneEntry):
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[0].upper() in ("ES", "MX") and set(parts[1]) <= PHONE_CHARS:
            picked: Country = "MX" if parts[0].upper() == "MX" else "ES"
            return WidgetAnswer(value=PhoneInput(country=picked, number=parts[1]))
    if isinstance(ui, Slots) and text.isdigit() and 1 <= int(text) <= len(ui.slots):
        return WidgetAnswer(value=ui.slots[int(text) - 1].id)
    if not isinstance(ui, QuickReplies | MultiSelect):
        return text
    picks = [part.strip() for part in text.split(",")]
    if not picks or not all(p.isdigit() and 1 <= int(p) <= len(ui.options) for p in picks):
        return text
    values = [ui.options[int(p) - 1].value for p in picks]
    if isinstance(ui, MultiSelect):
        return WidgetAnswer(value=values)
    return WidgetAnswer(value=values[0])


def _show(reply: Reply, out: TextIO) -> None:
    print(f"Ana: {reply.text}", file=out)
    ui = reply.ui
    if isinstance(ui, QuickReplies | MultiSelect):
        hint = " (several: 1,3)" if isinstance(ui, MultiSelect) else ""
        options = "  ".join(f"[{i}] {o.label}" for i, o in enumerate(ui.options, 1))
        print(f"     {options}{hint}", file=out)
    elif isinstance(ui, PhoneEntry):
        dials = " / ".join(c.label for c in ui.countries)
        print(f"     phone: {dials}, selected {ui.selected} (type 'MX 55...' to switch)", file=out)
    elif isinstance(ui, Confirm):
        for row in ui.fields:
            print(f"     {row.label}: {row.value}", file=out)
    elif isinstance(ui, Slots):
        for i, slot in enumerate(ui.slots, 1):
            print(f"     [{i}] {slot.label} ({slot.location})", file=out)
    elif isinstance(ui, Booked):
        print(f"     Interview: {ui.label}, {ui.location}, {ui.address}", file=out)


def _print_json(data: Any, out: TextIO = sys.stdout) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str), file=out)


if __name__ == "__main__":
    raise SystemExit(main())
