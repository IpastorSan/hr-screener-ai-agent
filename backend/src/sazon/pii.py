"""Masking of personal data in logs and traces."""

import json
import re
from collections.abc import Iterable

from sazon.llm.interface import persona_name

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Not inside a word or an id: "1234-5678" in a conversation UUID is not a phone number.
PHONE_PATTERN = re.compile(r"(?<![\w-])\+?\d[\d\s().-]{6,}\d(?![\w-])")
# Spanish numbers have 9 digits and Mexican ones 10; a date such as 2026-10-19 has 8.
MIN_PHONE_DIGITS = 9
# A client address in an access log ("66.249.93.12:54321") has the digits and dots of a phone.
IPV4_PATTERN = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
# `str(datetime)` ("2026-10-20 12:34:56") puts the hour's digits right after the date's.
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
# A Unix time with a fraction ("1760955296.123"), as some libraries log it.
EPOCH_PATTERN = re.compile(r"\d{10}\.\d{1,9}")
# E.164 allows 15 digits at most: an unbroken run longer than that is an id.
MAX_PHONE_DIGITS = 15


def _is_ipv4(text: str) -> bool:
    return IPV4_PATTERN.fullmatch(text) is not None and all(
        int(octet) <= 255 for octet in text.split(".")
    )


def _phone(match: re.Match[str]) -> str:
    text = match.group(0)
    if date := DATE_PATTERN.match(text):
        # What follows the date may still hold a number: mask that on its own.
        return date.group(0) + PHONE_PATTERN.sub(_phone, text[date.end() :])
    digits = sum(c.isdigit() for c in text)
    if (
        _is_ipv4(text)
        or digits < MIN_PHONE_DIGITS
        or EPOCH_PATTERN.fullmatch(text)
        or (text.isdigit() and digits > MAX_PHONE_DIGITS)
    ):
        return text
    return "[phone]"


def redact(text: str, known: Iterable[str] = ()) -> str:
    """Mask emails, phone-like numbers and any known personal values (names, for example).

    Known values are matched as whole words, whatever their case ("ROSA" too, never the "rosa"
    in "Barrosa"), and also in their JSON-escaped form (`P\\u00e9rez`), as they appear in
    serialized model messages. The assistant's own name is never a known value: a candidate
    called Ana would otherwise mask every line where Ana speaks. Their full name still is.
    """
    own = persona_name().casefold()
    values = {v for v in known if v and v.casefold() != own}
    for value in sorted(values, key=len, reverse=True):
        text = _known(value).sub("[redacted]", text)
        escaped = json.dumps(value)[1:-1]
        if escaped != value:
            text = _known(escaped).sub("[redacted]", text)
    text = EMAIL_PATTERN.sub("[email]", text)
    return PHONE_PATTERN.sub(_phone, text)


def _known(value: str) -> re.Pattern[str]:
    """`value` as a whole word, in any case."""
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)
