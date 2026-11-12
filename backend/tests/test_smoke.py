import sazon
from sazon.config import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert sazon.__name__ == "sazon"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"


def test_empty_env_values_fall_back_to_defaults(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MAX_EVAL_USD=\nRETENTION_DAYS=\n")
    monkeypatch.delenv("MAX_EVAL_USD", raising=False)
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    settings = Settings(_env_file=env)  # pyright: ignore[reportCallIssue]
    assert settings.max_eval_usd is None
    assert settings.retention_days == 365
