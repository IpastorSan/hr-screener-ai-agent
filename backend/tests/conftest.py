import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import loop_guard
import sazon.service
from sazon import recruiters
from sazon.rag.index import NoRetriever
from sazon.storage.models import Recruiter


@pytest.fixture(autouse=True)
def no_database_on_the_event_loop() -> Iterator[None]:
    """Every test fails if a turn ran a statement on the event loop instead of a thread
    (SAZ-84)."""
    loop_guard.install()
    loop_guard.LOOP_STATEMENTS.clear()
    yield
    caught = list(loop_guard.LOOP_STATEMENTS)
    assert not caught, f"database calls on the event loop during a turn: {caught}"


@pytest.fixture(autouse=True)
def no_default_faq(monkeypatch: pytest.MonkeyPatch) -> None:
    """Services built without `faq` know nothing, whatever model cache this machine has.

    `test_rag` passes real indexes explicitly.
    """
    monkeypatch.setattr(sazon.service, "default_index", NoRetriever)


@pytest.fixture(autouse=True)
def no_local_env_file(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Unit tests never read the developer's `.env`: a real key there would reach real APIs.

    Live tests (`-m live`) are the exception; they exist to use those keys.
    """
    if request.node.get_closest_marker("live"):
        yield
        return
    from sazon.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    # Every setting, not a chosen few: a shell that exports DATABASE_URL, ENV or LLM_* (direnv,
    # a deploy shell) must not reach the tests either. Names match case-insensitively.
    fields = set(Settings.model_fields)
    for name in list(os.environ):
        if name.lower() in fields:
            monkeypatch.delenv(name)
    # Code that builds a client needs some key; this one can't authenticate anywhere.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-valid")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


SignIn = Callable[..., Recruiter]


@pytest.fixture
def sign_in() -> SignIn:
    """`sign_in(client, admin=True, **fields)`: add a recruiter to the client's app and sign the
    client in as them (the development sign-in), for the dashboard routes."""
    count = 0

    def sign(client: TestClient, *, admin: bool = True, **fields: Any) -> Recruiter:
        nonlocal count
        count += 1
        app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
        repo = app.state.service.repo
        fields.setdefault("name", f"Recruiter {count}")
        fields.setdefault("email", f"recruiter{count}@gruposazon.example")
        fields.setdefault("country", "ES")
        recruiter = recruiters.create(repo, datetime.now(UTC), is_admin=admin, **fields)
        response = client.post("/api/auth/dev-login", json={"email": recruiter.email})
        assert response.status_code == 200, response.text
        return recruiter

    return sign
