"""Erasure on request (SAZ-35): `sazon erase` and `DELETE /api/candidates/{id}`."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlmodel import SQLModel, select

from sazon import recruiters
from sazon.calendar.fake import FakeCalendar
from sazon.cli import main
from sazon.scheduling.service import SchedulingConfig, book
from sazon.service import ScreeningService
from sazon.storage.models import (
    Booking,
    BookingStatus,
    Candidate,
    CandidateReview,
    Conversation,
    FaqMiss,
    InterviewSlot,
    Summary,
    Turn,
)
from sazon.storage.repo import Repo, init_db, make_engine
from sazon.storage.retention import erase, find_candidates

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
PHONE = "+34612345678"
EMAIL = "lucia@example.com"


def team(repo: Repo, size: int = 3) -> None:
    """Interviewers at the slot's location, so each application gets its own."""
    for n in range(size):
        recruiters.create(
            repo,
            NOW,
            name=f"Recruiter {n}",
            email=f"interviewer{n}@gruposazon.example",
            country="ES",
            location_ids=["es-mad-centro"],
        )


def application(
    repo: Repo,
    slot: InterviewSlot,
    phone: str,
    email: str,
    config: SchedulingConfig | None = None,
) -> Candidate:
    """A booked candidate with every kind of row that refers to them."""
    candidate = repo.save(
        Candidate(phone=phone, email=email, channel="web", created_at=NOW, updated_at=NOW)
    )
    conversation = repo.save(
        Conversation(
            candidate_id=candidate.id,
            channel="web",
            state={"profile": {"full_name": "Lucía Pérez"}},
            context={"country": "ES"},
            started_at=NOW,
            last_activity_at=NOW,
        )
    )
    for turn in (
        Turn(conversation_id=conversation.id, role="candidate", text="Hola", created_at=NOW),
        Turn(conversation_id=conversation.id, role="agent", nudge=True, created_at=NOW),
        Turn(
            conversation_id=conversation.id,
            role="agent",
            sources=[{"source": "faq", "label": "pay", "score": 0.9}],
            created_at=NOW,
        ),
    ):
        repo.save(turn)
    repo.save(Summary(conversation_id=conversation.id, body={}, created_at=NOW))
    repo.save(
        FaqMiss(
            conversation_id=conversation.id,
            question="¿Hay gimnasio?",
            language="es",
            country="ES",
            created_at=NOW,
        )
    )
    repo.save(CandidateReview(candidate_id=candidate.id, updated_at=NOW))
    book(repo, candidate.id, conversation.id, slot.id, NOW, config=config or SchedulingConfig())
    return candidate


@pytest.fixture
def repo() -> Repo:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    team(repo)
    return repo


@pytest.fixture
def slot(repo: Repo) -> InterviewSlot:
    start = NOW + timedelta(days=2)
    return repo.save(
        InterviewSlot(
            location_id="es-mad-centro", starts_at=start, ends_at=start + timedelta(minutes=30)
        )
    )


def rows(repo: Repo) -> dict[str, int]:
    with repo.session() as session:
        return {
            table.__name__: len(session.exec(select(table)).all())
            for table in (Candidate, Conversation, Turn, Summary, FaqMiss, CandidateReview)
        } | {"booked": len(session.exec(select(Booking).where(Booking.status == "booked")).all())}


def erasures(via: str) -> float:
    return REGISTRY.get_sample_value("sazon_erasures_total", {"via": via}) or 0.0


def test_erase_deletes_every_row_and_frees_the_slot(
    repo: Repo, slot: InterviewSlot, caplog: pytest.LogCaptureFixture
):
    gone = application(repo, slot, PHONE, EMAIL)
    kept = application(repo, slot, "+34699999999", "otro@example.com")
    assert rows(repo)["booked"] == 2
    before = erasures("cli")

    with caplog.at_level(logging.INFO, logger="sazon.events"):
        report = erase(repo, gone.id, via="cli")

    assert report is not None
    assert (report.conversations, report.bookings, report.slots_freed) == (1, 1, 1)
    assert rows(repo) == {
        "Candidate": 1,
        "Conversation": 1,
        "Turn": 3,
        "Summary": 1,
        "FaqMiss": 1,
        "CandidateReview": 1,
        "booked": 1,
    }
    assert repo.get_candidate(kept.id) is not None
    with repo.session() as session:
        for table in SQLModel.metadata.sorted_tables:
            for row in session.exec(select(table)).all():  # type: ignore[call-overload]
                assert gone.id not in str(row)
    [line] = [r.getMessage() for r in caplog.records if "candidate_erased" in r.getMessage()]
    assert PHONE not in line
    assert EMAIL not in line
    assert "Lucía" not in line
    assert erasures("cli") == before + 1


def test_erase_an_unknown_candidate_is_none(repo: Repo):
    assert erase(repo, "nope", via="cli") is None


def test_find_candidates_matches_any_phone_format_and_email_case(repo: Repo, slot: InterviewSlot):
    candidate = application(repo, slot, PHONE, EMAIL)

    assert find_candidates(repo, phone="612 345 678") == [candidate.id]
    assert find_candidates(repo, phone="+34 612 34 56 78") == [candidate.id]
    assert find_candidates(repo, email="Lucia@Example.com") == [candidate.id]
    assert find_candidates(repo, email="nadie@example.com") == []


def test_cli_erase_by_phone_email_or_id(tmp_path, capsys: pytest.CaptureFixture[str]):
    url = f"sqlite:///{tmp_path / 'sazon.db'}"
    repo = Repo(make_engine(url))
    init_db(repo.engine)
    team(repo)
    start = NOW + timedelta(days=2)
    slot = repo.save(
        InterviewSlot(
            location_id="es-mad-centro", starts_at=start, ends_at=start + timedelta(minutes=30)
        )
    )
    by_phone = application(repo, slot, PHONE, EMAIL)
    by_email = application(repo, slot, "+34699999999", "otro@example.com")
    by_id = application(repo, slot, "+34688888888", "tercero@example.com")

    assert main(["--db", url, "erase", "--phone", "612345678", "--dry-run"]) == 0
    assert repo.get_candidate(by_phone.id) is not None
    assert main(["--db", url, "erase", "--phone", "612345678"]) == 0
    assert main(["--db", url, "erase", "--email", "OTRO@example.com"]) == 0
    assert main(["--db", url, "erase", "--candidate-id", by_id.id]) == 0
    assert main(["--db", url, "erase", "--candidate-id", by_id.id]) == 1

    out = capsys.readouterr().out
    assert PHONE not in out
    assert EMAIL not in out
    for candidate in (by_phone, by_email, by_id):
        assert repo.get_candidate(candidate.id) is None
    assert rows(repo) == {
        "Candidate": 0,
        "Conversation": 0,
        "Turn": 0,
        "Summary": 0,
        "FaqMiss": 0,
        "CandidateReview": 0,
        "booked": 0,
    }


def test_delete_endpoint_erases_the_candidate(
    client: TestClient, service: ScreeningService, slot_in: None
):
    repo = service.repo
    with repo.session() as session:
        slot = session.exec(select(InterviewSlot)).one()
    candidate = application(repo, slot, PHONE, EMAIL)
    before = erasures("api")

    response = client.delete(f"/api/candidates/{candidate.id}")

    assert response.status_code == 204
    assert client.get(f"/api/candidates/{candidate.id}").status_code == 404
    assert client.delete(f"/api/candidates/{candidate.id}").status_code == 404
    assert rows(repo)["Conversation"] == 0
    assert rows(repo)["booked"] == 0
    assert erasures("api") == before + 1


def test_only_admins_erase(
    anonymous: TestClient,
    service: ScreeningService,
    slot_in: None,
    sign_in: Callable[..., object],
):
    """Erasure cannot be undone, so a regular recruiter (a demo account, say) cannot erase."""
    repo = service.repo
    with repo.session() as session:
        slot = session.exec(select(InterviewSlot)).one()
    candidate = application(repo, slot, PHONE, EMAIL)
    before = erasures("api")

    assert anonymous.delete(f"/api/candidates/{candidate.id}").status_code == 401
    sign_in(anonymous, admin=False)
    response = anonymous.delete(f"/api/candidates/{candidate.id}")

    assert response.status_code == 403
    assert repo.get_candidate(candidate.id) is not None
    assert rows(repo)["booked"] == 1
    assert erasures("api") == before


@pytest.fixture
def slot_in(client: TestClient, service: ScreeningService) -> None:
    """A slot in the app's database (created after the app has set up its tables)."""
    team(service.repo)
    start = NOW + timedelta(days=2)
    service.repo.save(
        InterviewSlot(
            location_id="es-mad-centro", starts_at=start, ends_at=start + timedelta(minutes=30)
        )
    )


def test_erase_deletes_the_calendar_events_the_upcoming_one_with_notice(
    repo: Repo, slot: InterviewSlot
):
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    gone = application(repo, slot, PHONE, EMAIL, config)
    kept = application(repo, slot, "+34699999999", "otro@example.com", config)
    [event] = [e for e in calendar.live_events() if e.request.attendee_email == EMAIL]
    assert event.request.start == slot.starts_at.replace(tzinfo=UTC)
    # An interview the candidate moved, whose event was left behind.
    with repo.session() as session:
        past = session.exec(select(Booking).where(Booking.candidate_id == gone.id)).one()
        old = Booking(
            candidate_id=gone.id,
            conversation_id=past.conversation_id,
            slot_id=past.slot_id,
            status=BookingStatus.RESCHEDULED,
            recruiter_id=past.recruiter_id,
            google_event_id="pastevent",
            google_calendar_id="primary",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(old)
        session.commit()

    report = erase(repo, gone.id, via="cli", calendar=calendar, now=NOW)

    assert report is not None
    assert report.events_deleted == 2
    assert [e.request.attendee_email for e in calendar.live_events()] == ["otro@example.com"]
    assert calendar.calls.count("delete") == 1  # the upcoming one tells the candidate
    assert calendar.calls.count("delete-quiet") == 1  # the replaced one goes quietly
    assert repo.get_candidate(kept.id) is not None


def test_erase_counts_and_logs_an_event_the_calendar_would_not_delete(
    repo: Repo, slot: InterviewSlot, caplog: pytest.LogCaptureFixture
):
    calendar = FakeCalendar()
    gone = application(repo, slot, PHONE, EMAIL, SchedulingConfig(calendar=calendar))
    [event] = calendar.live_events()
    calendar.failing_writes.add(event.recruiter_id)

    with caplog.at_level(logging.ERROR, logger="sazon.storage.retention"):
        report = erase(repo, gone.id, via="cli", calendar=calendar, now=NOW)

    assert report is not None
    assert (report.events_deleted, report.events_failed) == (0, 1)
    assert repo.get_candidate(gone.id) is None  # the erasure itself still happens
    assert event.event.event_id in caplog.text
    assert EMAIL not in caplog.text
