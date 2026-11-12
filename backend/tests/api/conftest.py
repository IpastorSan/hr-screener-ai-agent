from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sazon.api.app import create_app
from sazon.llm import FakeLLM
from sazon.service import ScreeningService
from sazon.storage.repo import Repo, make_engine

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def service(llm: FakeLLM) -> ScreeningService:
    return ScreeningService(llm, Repo(make_engine("sqlite://")), clock=lambda: NOW)


@pytest.fixture
def anonymous(service: ScreeningService) -> Iterator[TestClient]:
    """A client without a recruiter session: what a candidate's browser is."""
    with TestClient(create_app(service), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def client(service: ScreeningService, sign_in: Callable[..., object]) -> Iterator[TestClient]:
    """Signed in as an admin recruiter, so it reaches the dashboard routes too."""
    with TestClient(create_app(service), raise_server_exceptions=False) as test_client:
        sign_in(test_client)
        yield test_client
