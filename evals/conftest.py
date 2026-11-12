import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def no_local_env_file(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """The eval tests never read the developer's `.env` either (see `backend/tests/conftest.py`):
    a real key or `SCHEDULING_SOURCE=google` there would change what they run against.

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
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-valid")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
