"""Recruiter accounts: create, update and look up, and the demo team from the seed fixture."""

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, and_, or_
from sqlmodel import col, select

from sazon.auth import hash_password, normalize_email
from sazon.engine.service_areas import get_location
from sazon.engine.spec import agent
from sazon.engine.validators import Country
from sazon.storage.models import WEEKDAY_KEYS, Candidate, InterviewMode, Recruiter
from sazon.storage.repo import Repo, to_utc


def seed_fixture() -> Path:
    """The demo team and its demo passwords (`sazon seed`): the agent's `recruiters` file."""
    path = agent().recruiters
    if path is None:
        raise ValueError(f"the {agent().agent} agent names no recruiters file")
    return Path(path)


WORKING_HOURS: dict[str, list[list[str]]] = {
    "ES": [["09:00", "14:00"], ["16:00", "19:00"]],
    "MX": [["09:00", "18:00"]],
}
"""Default weekday hours per country, local time."""
MAX_PER_DAY = 15
"""A recruiter's default daily maximum of (video) interviews."""
TIMEZONES: dict[Country, str] = {"ES": "Europe/Madrid", "MX": "America/Mexico_City"}


def weekday_hours(blocks: Sequence[Sequence[str]]) -> dict[str, list[list[str]]]:
    """The same blocks Monday to Friday, nothing at the weekend."""
    return {day: [list(block) for block in blocks] for day in WEEKDAY_KEYS[:5]}


def location_scope(recruiter: Recruiter) -> frozenset[str] | None:
    """The locations whose candidates a recruiter sees on the dashboard and through the
    assistant (`in_scope`): their own, or every one (None) for an admin. A recruiter without
    locations sees none, like their in-person agenda."""
    return None if recruiter.is_admin else frozenset(recruiter.location_ids)


def scope_countries(locations: frozenset[str]) -> frozenset[str]:
    """The countries of a scope's locations."""
    found = (get_location(location_id) for location_id in locations)
    return frozenset(location.country for location in found if location is not None)


def in_scope(locations: frozenset[str]) -> ColumnElement[bool]:
    """SQL condition: the candidates a scope of locations covers. A candidate is placed at their
    own location; one without it (closed before the city question, or out of area) at the
    location of the posting they applied from, or, when that named none, anywhere in their
    country. `candidate_in_scope` is the same rule for one loaded candidate."""
    no_location = col(Candidate.location_id).is_(None)
    no_posting = col(Candidate.posting_location_id).is_(None)
    return or_(
        col(Candidate.location_id).in_(locations),
        and_(no_location, col(Candidate.posting_location_id).in_(locations)),
        and_(no_location, no_posting, col(Candidate.country).in_(scope_countries(locations))),
    )


def candidate_in_scope(candidate: Candidate, locations: frozenset[str] | None) -> bool:
    """`in_scope` for one candidate; every candidate is in an admin's scope (None)."""
    if locations is None:
        return True
    if candidate.location_id is not None:
        return candidate.location_id in locations
    if candidate.posting_location_id is not None:
        return candidate.posting_location_id in locations
    return candidate.country in scope_countries(locations)


def by_email(repo: Repo, email: str) -> Recruiter | None:
    with repo.session() as session:
        query = select(Recruiter).where(col(Recruiter.email) == normalize_email(email))
        return session.exec(query).first()


def get(repo: Repo, recruiter_id: str) -> Recruiter | None:
    with repo.session() as session:
        return session.get(Recruiter, recruiter_id)


def all_recruiters(repo: Repo) -> list[Recruiter]:
    with repo.session() as session:
        return list(session.exec(select(Recruiter).order_by(col(Recruiter.name))))


def names(repo: Repo) -> dict[str, str]:
    with repo.session() as session:
        rows = session.exec(select(Recruiter.id, Recruiter.name))
        return dict(rows.all())


def create(
    repo: Repo,
    now: datetime,
    *,
    name: str,
    email: str,
    country: str,
    password: str | None = None,
    is_admin: bool = False,
    timezone: str | None = None,
    location_ids: Sequence[str] = (),
    interview_modes: Sequence[str] = (InterviewMode.VIDEO,),
    working_hours: dict[str, list[list[str]]] | None = None,
    max_interviews_per_day: int = MAX_PER_DAY,
    scope: dict[str, list[str]] | None = None,
) -> Recruiter:
    """Raises `ValueError` when the email is taken."""
    if by_email(repo, email) is not None:
        raise ValueError("email taken")
    moment = to_utc(now)
    return repo.save(
        Recruiter(
            name=name.strip(),
            email=normalize_email(email),
            password_hash=hash_password(password) if password else None,
            is_admin=is_admin,
            country=country,
            timezone=timezone or TIMEZONES["MX" if country == "MX" else "ES"],
            location_ids=list(location_ids),
            interview_modes=[str(mode) for mode in interview_modes],
            working_hours=working_hours or weekday_hours(WORKING_HOURS.get(country, [])),
            max_interviews_per_day=max_interviews_per_day,
            scope=scope or {},
            created_at=moment,
            updated_at=moment,
        )
    )


def update(repo: Repo, recruiter_id: str, now: datetime, **changes: Any) -> Recruiter:
    """Apply `changes` (model fields, or `password` to set a new one). A new password or a
    deactivation ends the recruiter's open sessions. Raises `LookupError` when not found."""
    with repo.session() as session:
        recruiter = session.get(Recruiter, recruiter_id)
        if recruiter is None:
            raise LookupError(recruiter_id)
        password = changes.pop("password", None)
        if password:
            recruiter.password_hash = hash_password(password)
            recruiter.session_version += 1
        if changes.get("active") is False and recruiter.active:
            recruiter.session_version += 1
        for key, value in changes.items():
            setattr(recruiter, key, value)
        recruiter.updated_at = to_utc(now)
        session.add(recruiter)
        session.commit()
        session.refresh(recruiter)
        return recruiter


def load_fixture(path: Path | None = None) -> list[dict[str, Any]]:
    return json.loads((path or seed_fixture()).read_text(encoding="utf-8"))["recruiters"]


def seed_team(
    repo: Repo, now: datetime, *, with_passwords: bool = True, path: Path | None = None
) -> int:
    """Create the fixture's recruiters that do not exist yet (matched by email). Returns how
    many it added. `with_passwords=False` leaves them without one (`sazon set-password`)."""
    created = 0
    for entry in load_fixture(path):
        hours = entry.get("working_hours")
        try:
            create(
                repo,
                now,
                name=entry["name"],
                email=entry["email"],
                country=entry["country"],
                password=entry.get("password") if with_passwords else None,
                is_admin=bool(entry.get("is_admin", False)),
                timezone=entry.get("timezone"),
                location_ids=entry["location_ids"],
                interview_modes=entry.get("interview_modes", [InterviewMode.VIDEO]),
                working_hours=weekday_hours(hours) if hours else None,
                max_interviews_per_day=entry.get("max_interviews_per_day", MAX_PER_DAY),
                scope=entry.get("scope"),
            )
        except ValueError:  # already there
            continue
        created += 1
    return created
