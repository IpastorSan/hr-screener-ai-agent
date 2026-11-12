"""The interview mode read from the candidate's own words (SAZ-39)."""

import pytest

from sazon.engine.models import InterviewMode
from sazon.engine.modes import allowed_mode, read_mode, resolve_mode

ONSITE, VIDEO = InterviewMode.ONSITE, InterviewMode.VIDEO


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("presencial", ONSITE),
        ("Presencial, por favor", ONSITE),
        ("en persona", ONSITE),
        ("Prefiero ir en persona", ONSITE),
        ("en la oficina", ONSITE),
        ("en el local mejor", ONSITE),
        ("in person", ONSITE),
        ("In-person please", ONSITE),
        ("por video", VIDEO),
        ("Por vídeo", VIDEO),
        ("videollamada", VIDEO),
        ("mejor por video", VIDEO),
        ("meet", VIDEO),
        ("Meet", VIDEO),
        ("por Google Meet", VIDEO),
        ("online", VIDEO),
        ("en línea", VIDEO),
        ("video call", VIDEO),
        ("a videocall please", VIDEO),
        ("I'd rather do a video call", VIDEO),
        ("No, mejor por video", VIDEO),
        ("presencial no, mejor online", VIDEO),
        ("prefiero en persona, no por video", ONSITE),
        ("not online please, in person", ONSITE),
    ],
)
def test_words_that_name_one_mode(text: str, mode: InterviewMode):
    assert resolve_mode(text) == mode


@pytest.mark.parametrize(
    "text",
    [
        "",
        "me da igual",
        "el martes por la tarde",
        "video o presencial",
        "no quiero videollamada",
        "nada de presencial",
        "nice to meet you",
        "can we meet on Tuesday?",
        "¿Cómo es la entrevista?",
    ],
)
def test_words_that_do_not_choose_one_mode(text: str):
    assert resolve_mode(text) is None


def test_the_extractor_hint_is_read_as_a_value_or_as_words():
    assert read_mode("video") == VIDEO
    assert read_mode(" ONSITE ") == ONSITE
    assert read_mode("en persona") == ONSITE
    assert read_mode("phone") is None
    assert read_mode(None) is None


def test_a_hint_counts_only_for_a_mode_on_offer():
    assert allowed_mode("video", (ONSITE, VIDEO)) == VIDEO
    assert allowed_mode("video", (ONSITE,)) is None
    assert allowed_mode("onsite", ()) is None
