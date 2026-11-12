"""`sazon migrate-onsite-bookings` (SAZ-65): in-person bookings made before SAZ-64, held by a
recruiter, move to the organizer's calendar with the location's manager invited."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sazon import recruiters
from sazon.calendar.fake import FakeCalendar
from sazon.cli import main
from sazon.config import get_settings
from sazon.scheduling import service as scheduling
from sazon.scheduling.service import OrganizerMissing, SchedulingConfig, migrate_onsite_bookings
from sazon.storage.models import Booking, Candidate, Conversation, InterviewMode, Recruiter
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # Monday
TUESDAY_10 = datetime(2026, 10, 20, 8, tzinfo=UTC)  # 10:00 in Madrid
GRACIA = "es-bcn-gracia"
MANAGER = "gerencia.es-bcn-gracia@gruposazon.example"


@pytest.fixture
def repo() -> Repo:
    engine = make_engine("sqlite://")
    init_db(engine)
    return Repo(engine)


def recruiter(repo: Repo, name: str, *modes: str) -> Recruiter:
    return recruiters.create(
        repo,
        NOW,
        name=name,
        email=f"{name.lower()}@gruposazon.example",
        country="ES",
        location_ids=[GRACIA],
        interview_modes=modes or (InterviewMode.VIDEO,),
    )


def legacy(
    repo: Repo,
    calendar: FakeCalendar | None,
    recruiter_id: str,
    start: datetime = TUESDAY_10,
    mode: str | None = InterviewMode.ONSITE,
) -> Booking:
    """An in-person booking as SAZ-64 found it: on the recruiter's calendar, no manager."""
    candidate = repo.save(
        Candidate(
            channel="web",
            email="lucia@example.com",
            profile={"full_name": "Lucía García"},
            location_id=GRACIA,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    chat = repo.save(
        Conversation(
            candidate_id=candidate.id,
            channel="web",
            state={},
            started_at=NOW,
            last_activity_at=NOW,
        )
    )
    [slot] = scheduling.materialize(repo, GRACIA, [start])
    booking = repo.save(
        Booking(
            candidate_id=candidate.id,
            conversation_id=chat.id,
            slot_id=slot.id,
            recruiter_id=recruiter_id,
            mode=mode,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    if calendar is None:
        return booking
    request = scheduling.event_request(repo, booking)
    assert request is not None
    event = calendar.create_event(recruiter_id, replace(request, guests=()))
    booking.google_event_id = event.event_id
    booking.google_calendar_id = event.calendar_id
    return repo.save(booking)


def get(repo: Repo, booking_id: str) -> Booking:
    with repo.session() as session:
        found = session.get(Booking, booking_id)
    assert found is not None
    return found


def config_for(calendar: FakeCalendar, organizer: Recruiter | None) -> SchedulingConfig:
    return SchedulingConfig(
        calendar=calendar,
        organizer_email=organizer.email if organizer else None,
        organizer_required=True,
    )


def test_an_old_booking_moves_to_the_organizer_with_the_manager_invited(repo: Repo):
    calendar = FakeCalendar()
    jordi = recruiter(repo, "Jordi", InterviewMode.ONSITE)
    pablo = recruiter(repo, "Pablo")
    booking = legacy(repo, calendar, jordi.id)
    config = config_for(calendar, pablo)

    report = migrate_onsite_bookings(repo, NOW, config)

    assert (report.found, report.moved, report.events, report.failed) == (1, 1, 1, 0)
    moved = get(repo, booking.id)
    assert (moved.recruiter_id, moved.interviewer_email) == (pablo.id, MANAGER)
    assert moved.status == "booked"
    assert calendar.live_events(jordi.id) == []
    assert "delete-quiet" in calendar.calls  # the new invitation is the candidate's notice
    [event] = calendar.live_events(pablo.id)
    assert moved.google_event_id == event.event.event_id
    assert event.request.attendee_email == "lucia@example.com"
    assert [guest.email for guest in event.request.guests] == [MANAGER]

    calls = len(calendar.calls)
    again = migrate_onsite_bookings(repo, NOW, config)
    assert (again.found, again.moved) == (0, 0)
    assert len(calendar.calls) == calls


def test_a_dry_run_changes_nothing(repo: Repo):
    calendar = FakeCalendar()
    jordi = recruiter(repo, "Jordi", InterviewMode.ONSITE)
    pablo = recruiter(repo, "Pablo")
    booking = legacy(repo, calendar, jordi.id, mode=None)  # from before video interviews

    report = migrate_onsite_bookings(repo, NOW, config_for(calendar, pablo), dry_run=True)

    assert (report.found, report.moved) == (1, 0)
    assert get(repo, booking.id) == booking
    assert len(calendar.live_events(jordi.id)) == 1


def test_an_event_already_on_the_organizers_calendar_is_rewritten(repo: Repo):
    calendar = FakeCalendar()
    pablo = recruiter(repo, "Pablo", InterviewMode.ONSITE)
    booking = legacy(repo, calendar, pablo.id)

    report = migrate_onsite_bookings(repo, NOW, config_for(calendar, pablo))

    assert (report.moved, report.events) == (1, 1)
    assert "update" in calendar.calls
    [event] = calendar.live_events(pablo.id)
    assert event.event.event_id == booking.google_event_id
    assert [guest.email for guest in event.request.guests] == [MANAGER]
    assert get(repo, booking.id).interviewer_email == MANAGER


def test_a_refused_delete_leaves_the_booking_for_the_next_run(repo: Repo):
    calendar = FakeCalendar()
    jordi = recruiter(repo, "Jordi", InterviewMode.ONSITE)
    pablo = recruiter(repo, "Pablo")
    booking = legacy(repo, calendar, jordi.id)
    calendar.failing_writes.add(jordi.id)
    config = config_for(calendar, pablo)

    report = migrate_onsite_bookings(repo, NOW, config)

    assert (report.found, report.moved, report.failed) == (1, 0, 1)
    kept = get(repo, booking.id)
    assert (kept.recruiter_id, kept.google_event_id) == (jordi.id, booking.google_event_id)
    assert kept.interviewer_email is None
    assert calendar.live_events(pablo.id) == []

    calendar.failing_writes.clear()
    assert migrate_onsite_bookings(repo, NOW, config).moved == 1
    assert get(repo, booking.id).recruiter_id == pablo.id


def test_past_and_video_bookings_are_left_alone(repo: Repo):
    calendar = FakeCalendar()
    jordi = recruiter(repo, "Jordi", InterviewMode.ONSITE)
    pablo = recruiter(repo, "Pablo")
    past = legacy(repo, calendar, jordi.id, start=NOW - timedelta(days=1))
    video = legacy(repo, calendar, jordi.id, mode=InterviewMode.VIDEO)

    report = migrate_onsite_bookings(repo, NOW, config_for(calendar, pablo))

    assert report.found == 0
    assert get(repo, past.id) == past
    assert get(repo, video.id) == video


def test_the_organizer_must_be_there_and_connected(repo: Repo):
    calendar = FakeCalendar(connected_ids=set())
    jordi = recruiter(repo, "Jordi", InterviewMode.ONSITE)
    pablo = recruiter(repo, "Pablo")
    legacy(repo, None, jordi.id)
    with pytest.raises(OrganizerMissing):
        migrate_onsite_bookings(repo, NOW, config_for(calendar, pablo))
    with pytest.raises(OrganizerMissing):
        migrate_onsite_bookings(repo, NOW, config_for(calendar, None))
    # Without Google an unset organizer is fine: the manager is set, nobody holds an event.
    fixed = replace(config_for(calendar, None), calendar=None, organizer_required=False)
    assert migrate_onsite_bookings(repo, NOW, fixed).moved == 1


def test_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    url = f"sqlite:///{tmp_path / 'sazon.db'}"
    engine = make_engine(url)
    init_db(engine)
    repo = Repo(engine)
    jordi = recruiter(repo, "Jordi", InterviewMode.ONSITE)
    pablo = recruiter(repo, "Pablo")
    future = datetime.now(UTC).replace(microsecond=0, second=0, minute=0) + timedelta(days=30)
    booking = legacy(repo, None, jordi.id, start=future)
    monkeypatch.setenv("SCHEDULING_SOURCE", "fake")
    monkeypatch.setenv("ONSITE_ORGANIZER_EMAIL", "nobody@gruposazon.example")
    get_settings.cache_clear()
    assert main(["--db", url, "migrate-onsite-bookings"]) == 1
    assert "ONSITE_ORGANIZER_EMAIL" in capsys.readouterr().err

    monkeypatch.setenv("ONSITE_ORGANIZER_EMAIL", pablo.email.upper())
    get_settings.cache_clear()
    assert main(["--db", url, "migrate-onsite-bookings", "--dry-run"]) == 0
    assert "Would move 1 upcoming" in capsys.readouterr().out
    assert main(["--db", url, "migrate-onsite-bookings"]) == 0
    assert "Moved 1 of 1 upcoming in-person interviews; 1 have their new event" in (
        capsys.readouterr().out
    )
    assert get(repo, booking.id).recruiter_id == pablo.id
    assert main(["--db", url, "migrate-onsite-bookings"]) == 0
    assert "Moved 0 of 0" in capsys.readouterr().out
    get_settings.cache_clear()
