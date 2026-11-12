"""`sazon voice-agent create|update|show`: the ElevenLabs agent as code (SAZ-16).

The agent's settings live in `deploy/elevenlabs/agent.json`; this fills in the deployment's
values (custom LLM URL and secret, voices, allowed host) and calls the ElevenLabs API with
`ELEVENLABS_API_KEY`. Nothing is sent without that key; `--dry-run` prints the body instead.
"""

import copy
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sazon.config import Settings

DEFAULT_CONFIG = Path(__file__).resolve().parents[4] / "deploy" / "elevenlabs" / "agent.json"
SECRET_NAME = "sazon-voice-llm-secret"
TIMEOUT_S = 30.0


class VoiceAgentError(RuntimeError):
    """A missing setting or an ElevenLabs API error, with a message meant for the operator."""


def agent_body(
    settings: Settings, template: dict[str, Any], secret_id: str | None
) -> dict[str, Any]:
    """The template with this deployment's values. `secret_id` None leaves the stored API key
    of an existing agent alone (an update without `--rotate-secret`)."""
    url = settings.voice_llm_url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or not url.endswith("/v1"):
        raise VoiceAgentError(
            "VOICE_LLM_URL must be the public HTTPS base ElevenLabs calls, ending in /v1, "
            "e.g. https://sazon.example.com/v1 (a cloudflared tunnel URL for local tests)."
        )
    body = copy.deepcopy(template)
    config = body["conversation_config"]
    llm = config["agent"]["prompt"]["custom_llm"]
    llm["url"] = url
    if secret_id is None:
        llm.pop("api_key", None)
    else:
        llm["api_key"] = {"secret_id": secret_id}
    _set_voice(config, settings.elevenlabs_voice_es)
    english = config.get("language_presets", {}).get("en", {}).get("overrides", {})
    if settings.elevenlabs_voice_en.strip():
        english.setdefault("tts", {})["voice_id"] = settings.elevenlabs_voice_en.strip()
    else:
        english.pop("tts", None)
    auth = body.get("platform_settings", {}).get("auth")
    if auth is not None:
        auth["allowlist"] = [{"hostname": parsed.hostname}]
    return body


def _set_voice(config: dict[str, Any], voice_id: str) -> None:
    tts = config.setdefault("tts", {})
    if voice_id.strip():
        tts["voice_id"] = voice_id.strip()
    else:
        tts.pop("voice_id", None)  # the ElevenLabs default voice


def load_template(path: Path | None = None) -> dict[str, Any]:
    path = path or DEFAULT_CONFIG
    if not path.is_file():
        raise VoiceAgentError(f"No agent config at {path}; pass --config.")
    return json.loads(path.read_text(encoding="utf-8"))


class ElevenLabsClient:
    def __init__(self, settings: Settings) -> None:
        self.key = settings.elevenlabs_api_key.get_secret_value().strip()
        if not self.key:
            raise VoiceAgentError("ELEVENLABS_API_KEY is not set: nothing was sent.")
        self.base = settings.elevenlabs_api_url.rstrip("/")

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"xi-api-key": self.key, "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise VoiceAgentError(f"ElevenLabs {method} {path}: {exc.code} {detail}") from exc
        return json.loads(raw) if raw else {}

    def create_secret(self, value: str) -> str:
        body = {"type": "new", "name": SECRET_NAME, "value": value}
        stored = self.call("POST", "/v1/convai/secrets", body)
        return stored["secret_id"]


def run(
    command: str,
    settings: Settings,
    *,
    config: Path | None = None,
    agent_id: str | None = None,
    rotate_secret: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create, update or show the agent. Returns what to print."""
    agent_id = (agent_id or settings.elevenlabs_agent_id).strip() or None
    if command == "show":
        if agent_id is None:
            raise VoiceAgentError("Set ELEVENLABS_AGENT_ID or pass --agent-id.")
        return ElevenLabsClient(settings).call("GET", f"/v1/convai/agents/{agent_id}")
    if command == "update" and agent_id is None:
        raise VoiceAgentError("Set ELEVENLABS_AGENT_ID or pass --agent-id to update.")
    secret = settings.voice_llm_secret.get_secret_value().strip()
    sends_secret = command == "create" or rotate_secret
    if sends_secret and not secret:
        raise VoiceAgentError("VOICE_LLM_SECRET is not set: the agent would have no way in.")
    template = load_template(config)
    agent_body(settings, template, None)  # check the settings before anything is sent
    if dry_run:
        body = agent_body(settings, template, "<new secret>" if sends_secret else None)
        return {"dry_run": command, "agent_id": agent_id, "body": body}
    client = ElevenLabsClient(settings)
    secret_id = client.create_secret(secret) if sends_secret else None
    body = agent_body(settings, template, secret_id)
    if command == "create":
        created = client.call("POST", "/v1/convai/agents/create", body)
        return {
            "agent_id": created.get("agent_id"),
            "next": "Set ELEVENLABS_AGENT_ID (API) and NEXT_PUBLIC_ELEVENLABS_AGENT_ID (web).",
        }
    client.call("PATCH", f"/v1/convai/agents/{agent_id}", body)
    return {"agent_id": agent_id, "updated": True}
