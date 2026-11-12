from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SESSION_SECRET = 32
BACKEND_DIR = Path(__file__).resolve().parents[2]
BACKEND_ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ENV_FILE, ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Per-role defaults from the SAZ-24 benchmark (evals/results/model_selection.md).
    llm_extractor: str = "openrouter:google/gemini-3.5-flash-lite@minimal"
    llm_responder: str = "openrouter:anthropic/claude-haiku-5.5"
    llm_summarizer: str = "openrouter:anthropic/claude-haiku-5.5"
    llm_assistant: str = ""  # the recruiter assistant; empty means the responder's model
    llm_judge: str = "openrouter:anthropic/claude-opus-5.5"
    llm_simulator: str = ""
    llm_extract_timeout_s: float = 15.0
    llm_respond_timeout_s: float = 10.0
    llm_summarize_timeout_s: float = 30.0
    llm_assistant_timeout_s: float = 45.0

    database_url: str = "sqlite:///./sazon.db"
    sazon_cache_dir: Path = BACKEND_DIR / ".cache"
    """The embedding model and the retrieval vectors, written by `sazon rag-index`."""
    web_origin: str = "http://localhost:3000"
    sazon_fake_llm: bool = False
    sazon_agent: str = "riders"
    """The agent the service screens for: one shipped in `sazon/agents/` by name, or the path of
    an agent's directory (`sazon new-agent`). Read once, at the first turn or model client."""
    # Voice channel (SAZ-16): ElevenLabs Agents calls /v1/chat/completions as its custom LLM.
    elevenlabs_agent_id: str = ""
    elevenlabs_api_key: SecretStr = SecretStr("")
    """Only for `sazon voice-agent` and conversation tokens for a private agent."""
    elevenlabs_api_url: str = "https://api.elevenlabs.io"
    """`https://api.eu.residency.elevenlabs.io` for the EU residency environment (Enterprise)."""
    voice_llm_secret: SecretStr = SecretStr("")
    """Shared with the agent's custom LLM API key; empty turns the voice endpoint off."""
    voice_llm_url: str = ""
    """Public base URL ElevenLabs calls, e.g. `https://sazon.example.com/v1`."""
    voice_filler_after_s: float = 0.0
    """Say a short "Vale... " when a voice reply is not ready after this many seconds; 0 is off."""
    elevenlabs_voice_es: str = ""
    elevenlabs_voice_mx: str = ""
    elevenlabs_voice_en: str = ""
    max_eval_usd: float | None = None

    # Operations (SAZ-31). `ENV=production` refuses to boot on a demo setting.
    env: str = "development"
    max_daily_llm_usd: float | None = None
    """Model spend per UTC day; past it new chats are turned away and open ones use templates."""
    rate_limit_conversations_per_hour: int = 20
    """New chats per client IP; 0 turns the limit off."""
    rate_limit_messages_per_minute: int = 60
    """Messages per client IP; 0 turns the limit off."""
    retention_days: int = 365
    """`sazon purge` deletes older conversations; the privacy notice promises 12 months."""

    # Recruiter sign-in (SAZ-37).
    session_secret: SecretStr = SecretStr("")
    """Signs the recruiter session cookie; at least 32 characters. Required in production;
    elsewhere an empty one means a random key per process (sessions end on restart)."""
    session_ttl_hours: float = 12.0
    """A session ends after this long without a request; each request extends it."""
    login_attempts_per_ip: int = 20
    """Sign-in attempts per client IP per 15 minutes; 0 turns the limit off."""
    login_attempts_per_email: int = 5
    """Sign-in attempts per email address per 15 minutes; 0 turns the limit off."""

    # Google sign-in and Calendar (SAZ-38). Both stay off without a client id and secret.
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"
    """Must match an authorized redirect URI of the OAuth client exactly."""
    google_workspace_domain: str = ""
    """Only Google accounts of this Workspace (the ID token's `hd` claim) may sign in. Google
    stays off without it, so a personal account (no `hd`) can never match an empty one."""
    token_encryption_key: SecretStr = SecretStr("")
    """Fernet key for the stored Google refresh tokens. Required in production with Google on;
    elsewhere an empty one means a random key per process (connections end on restart)."""
    scheduling_source: str = "fixed"
    """`google`: recruiters' working hours minus their Google Calendar free/busy, and an event
    per booking. `fixed`: working hours only, no external calendar. `fake`: like `google` with
    an in-memory calendar (demos, offline), every active recruiter counted as connected."""
    interview_buffer_minutes: int = 10
    """Free time a recruiter keeps before and after each interview."""
    scheduling_days: int = 14
    """How many days ahead (from tomorrow) interviews are offered."""
    onsite_organizer_email: str = ""
    """The recruiter account whose calendar holds the in-person interviews' events, with the
    candidate and the location's manager as guests (SAZ-65). With `SCHEDULING_SOURCE=google`
    in-person interviews are offered only while this account exists, is active and has its
    calendar connected; `fixed` and `fake` offer them without it (no event then)."""
    public_url: str = ""
    """The API's public HTTPS origin, e.g. `https://sazon.example.com` (a cloudflared tunnel
    locally). With `SCHEDULING_SOURCE=google` it turns on Google Calendar push notifications
    (SAZ-40) to `$PUBLIC_URL/api/webhooks/google/calendar`; empty leaves them off (channels
    opened before keep being processed until they expire)."""
    calendar_watch_ttl_hours: int = 168
    """How long a push channel lives before `sazon calendar-watch renew` replaces it."""
    rate_limit_calendar_webhooks_per_minute: int = 300
    """Calendar notifications per client IP; 0 turns the limit off."""

    # Observability (SAZ-32). Tracing is off unless an OTLP endpoint is set.
    otel_exporter_otlp_endpoint: str = ""
    """OTLP/HTTP base URL, e.g. `http://langfuse-web:3000/api/public/otel` (`/v1/traces` added)."""
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    """With the public key, sent as basic auth to the OTLP endpoint (Langfuse's project keys)."""
    otel_service_name: str = "sazon-api"
    trace_content: bool = True
    """Prompts, model output and candidate text in spans (PII masked). Off keeps only metadata."""
    log_format: str = ""
    """`json` or `text`; empty means JSON in production and text elsewhere."""

    @property
    def json_logs(self) -> bool:
        fmt = self.log_format.strip().lower()
        return fmt == "json" if fmt else self.production

    @property
    def google_configured(self) -> bool:
        return bool(
            self.google_client_id.strip()
            and self.google_client_secret.get_secret_value().strip()
            and self.google_workspace_domain.strip()
        )

    @property
    def calendar_push_url(self) -> str | None:
        """Where Google posts calendar notifications, or None while push is off."""
        base = self.public_url.strip().rstrip("/")
        if not base:
            return None
        return f"{base}/api/webhooks/google/calendar"

    @property
    def production(self) -> bool:
        return self.env.strip().lower() in ("production", "prod")

    def production_problems(self) -> list[str]:
        """Settings that must not reach production. Empty when the API may boot."""
        if not self.production:
            return []
        problems: list[str] = []
        if self.sazon_fake_llm:
            problems.append("SAZON_FAKE_LLM is on (offline templates instead of the model)")
        if not self.openrouter_api_key.get_secret_value().strip():
            problems.append("OPENROUTER_API_KEY is empty")
        if len(self.session_secret.get_secret_value().strip()) < MIN_SESSION_SECRET:
            problems.append(f"SESSION_SECRET is shorter than {MIN_SESSION_SECRET} characters")
        if self.google_client_id.strip() and not self.google_workspace_domain.strip():
            problems.append("GOOGLE_CLIENT_ID is set without GOOGLE_WORKSPACE_DOMAIN")
        key = self.token_encryption_key.get_secret_value().strip()
        if self.google_configured and not key:
            problems.append("TOKEN_ENCRYPTION_KEY is empty while Google sign-in is configured")
        elif key and not _valid_fernet_key(key):
            problems.append("TOKEN_ENCRYPTION_KEY is not a Fernet key")
        source = self.scheduling_source.strip().lower()
        if source == "fake":
            problems.append("SCHEDULING_SOURCE=fake (an in-memory calendar)")
        if source == "google" and not self.google_configured:
            problems.append(
                "SCHEDULING_SOURCE=google without GOOGLE_CLIENT_ID/SECRET/WORKSPACE_DOMAIN"
            )
        if self.public_url.strip() and not self.public_url.strip().startswith("https://"):
            problems.append("PUBLIC_URL is not https:// (Google only calls HTTPS webhooks)")
        return problems


def _valid_fernet_key(key: str) -> bool:
    from cryptography.fernet import Fernet

    try:
        Fernet(key)
    except ValueError:
        return False
    return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
