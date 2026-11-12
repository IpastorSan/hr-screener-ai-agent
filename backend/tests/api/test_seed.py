from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from sazon import recruiters
from sazon.api.analytics import Filters, compute_analytics
from sazon.api.seed import reset_db, seed
from sazon.engine.models import Sentiment
from sazon.engine.service_areas import load_locations
from sazon.engine.spec import agent
from sazon.storage.models import Booking, Candidate, Conversation
from sazon.storage.repo import Repo, init_db, make_engine


async def test_seed_hits_the_target_distribution(now: datetime):
    engine = make_engine("sqlite://")
    init_db(engine)
    repo = Repo(engine)

    report = await seed(repo, 60, now=now)

    assert report.conversations == 60
    team = recruiters.all_recruiters(repo)
    assert report.recruiters == len(team) == 6
    assert Counter(r.country for r in team) == {"ES": 3, "MX": 3}
    assert sum(r.is_admin for r in team) == 1
    assert all(r.email.endswith("@gruposazon.example") and r.password_hash for r in team)
    covered = {location for r in team for location in r.location_ids}
    assert covered == set(load_locations())
    with repo.session() as session:
        bookings = session.exec(select(Booking)).all()
    assert bookings
    # Video interviews are a recruiter's; in-person ones the location manager's.
    assert all(b.recruiter_id for b in bookings if b.mode == "video")
    assert all(b.interviewer_email for b in bookings if b.mode == "onsite")
    assert {b.mode for b in bookings} == {"onsite", "video"}
    assert 0.5 <= report.completion_rate <= 0.7
    assert set(report.outcomes) == {
        "qualified",
        "disqualified",
        "abandoned",
        "declined_consent",
        "withdrawn",
    }
    assert report.outcomes["withdrawn"] == 2
    late_drop_offs = report.drop_off["experience"] + report.drop_off["start_date"]
    assert late_drop_offs >= 0.6 * report.outcomes["abandoned"]

    with repo.session() as session:
        candidates = session.exec(select(Candidate)).all()
        conversations = session.exec(select(Conversation)).all()
    assert len(candidates) == report.candidates
    assert {c.status for c in candidates} == {
        "qualified",
        "disqualified",
        "abandoned",
        "in_progress",
        "withdrawn",
    }
    assert {c.sentiment for c in candidates} == set(Sentiment)
    flags = {flag for c in candidates for flag in c.quality_flags}
    assert flags == set(agent().quality_flags)
    assert {c.country for c in candidates} == {"ES", "MX"}
    assert Counter(c.language for c in candidates)["en"] >= 1
    code_switched = [
        turn
        for conversation in conversations
        for turn in repo.turns(conversation.id)
        if turn.extraction and turn.extraction["code_switched"]
    ]
    assert code_switched

    started = [c.started_at.replace(tzinfo=now.tzinfo) for c in conversations]
    assert now - min(started) > timedelta(days=14)
    assert now - min(started) <= timedelta(days=21)
    assert max(started) <= now


async def test_seed_with_reset_starts_clean(now: datetime):
    engine = make_engine("sqlite://")
    init_db(engine)
    repo = Repo(engine)
    await seed(repo, 8, now=now)

    reset_db(engine)
    report = await seed(repo, 8, now=now, rng_seed=1)

    with repo.session() as session:
        assert len(session.exec(select(Conversation)).all()) == report.conversations == 8


async def test_the_same_rng_seed_and_now_give_identical_analytics(now: datetime):
    """Nothing in the seed depends on the wall clock: nudged and recovered counts and the
    durations come out the same on every run."""

    async def seeded() -> dict[str, Any]:
        repo = Repo(make_engine("sqlite://"))
        init_db(repo.engine)
        await seed(repo, 12, now=now, rng_seed=5)
        return compute_analytics(repo, Filters(), now).model_dump()

    first, second = await seeded(), await seeded()

    assert first["reengagement"]["nudged"] > 0
    assert first == second


async def test_nudges_do_not_depend_on_when_the_seed_runs(now: datetime):
    """`sazon seed` runs at the wall-clock time; which personas get nudged must not."""

    async def seeded(at: datetime) -> tuple[Any, Any]:
        repo = Repo(make_engine("sqlite://"))
        init_db(repo.engine)
        await seed(repo, 12, now=at, rng_seed=5)
        analytics = compute_analytics(repo, Filters(), at)
        return analytics.reengagement, analytics.durations

    first = await seeded(now)
    assert first[0].nudged > 0
    assert first == await seeded(now + timedelta(minutes=37, seconds=11))
