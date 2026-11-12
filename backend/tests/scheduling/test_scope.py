"""Who interviews whom beyond the location (`Recruiter.scope`, SAZ-97): a recruiter scoped on
a field's answers interviews only candidates who share one of them, one not scoped on it
anyone at their locations."""

from datetime import UTC, datetime, timedelta

import pytest

from sazon import recruiters
from sazon.api.schemas import RecruiterCreate
from sazon.scheduling.service import ANYONE, FIXED, Scope, interviewers
from sazon.storage.models import InterviewMode
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
LOCATION = "es-bcn-gracia"


@pytest.fixture
def repo() -> Repo:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    for name, scope in (("Maths", {"subjects": ["maths"]}), ("Anyone", None)):
        recruiters.create(
            repo,
            NOW,
            name=name,
            email=f"{name.lower()}@gruposazon.example",
            country="ES",
            location_ids=[LOCATION],
            scope=scope,
        )
    return repo


@pytest.mark.parametrize(
    ("scope", "names"),
    [
        (ANYONE, ["Anyone", "Maths"]),
        ({"subjects": ["maths", "music"]}, ["Anyone", "Maths"]),
        ({"subjects": ["music"]}, ["Anyone"]),
        ({"levels": ["eso"]}, ["Anyone", "Maths"]),
    ],
)
def test_a_scoped_recruiter_interviews_only_candidates_who_share_an_answer(
    repo: Repo, scope: Scope, names: list[str]
):
    found = interviewers(
        repo, LOCATION, InterviewMode.VIDEO, NOW, NOW + timedelta(days=1), FIXED, scope=scope
    )
    assert sorted(person.name for person in found) == names


def test_a_recruiters_scope_names_fields_the_agent_scopes_on():
    with pytest.raises(ValueError, match="no interview scope on subjects"):
        RecruiterCreate(name="Ana", email="ana@example.com", country="ES", scope={"subjects": []})
