"""What Ana says for an engine action, from the fallback templates.

Flow tests (service, API, voice, scheduling) assert which action was phrased, not its words:
they compare a reply against `said(...)`. Only the template tests (`tests/llm/test_llm.py`,
`test_voice_times.py`, `test_location_naming.py`, `test_follow_up_promise.py`) pin exact copy,
so a wording change is made in one place.
"""

from datetime import date
from typing import Any, cast

from sazon.engine.actions import Action, BaseAction
from sazon.engine.models import Language
from sazon.llm.fallback_templates import Register, fallback_reply
from sazon.llm.interface import Channel, ReplyContext

REGISTER_FIELDS: dict[Register, dict[str, Any]] = {
    "es-ES": {"language": Language.ES, "country": "ES"},
    "es-MX": {"language": Language.ES, "country": "MX"},
    "en": {"language": Language.EN, "country": "ES"},
}
TODAY = date(2026, 10, 19)


def said(
    action: type[BaseAction],
    *,
    register: Register = "es-ES",
    channel: Channel = Channel.WEB,
    today: date = TODAY,
    **fields: Any,
) -> str:
    """The template reply to `action(**fields)` in `register` on `channel`, e.g.
    `said(AskField, field=FieldName.EMAIL, again=True, channel=Channel.VOICE)`."""
    built = cast(Action, action(**REGISTER_FIELDS[register], **fields))
    context = ReplyContext.for_action(built, today=today, channel=channel)
    return fallback_reply(built, context)
