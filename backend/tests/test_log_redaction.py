import io
import logging
import sys
from collections.abc import Iterator

import pytest

from sazon.logs import PiiFilter, configure, install_pii_filter
from sazon.pii import redact as mask_contacts

UUID = "c0a8e1f2-1234-5678-9abc-def012345678"


@pytest.fixture
def streams() -> Iterator[dict[str, io.StringIO]]:
    """A plain handler on the root logger and on uvicorn's access logger, as in production."""
    found: dict[str, io.StringIO] = {}
    added: list[tuple[logging.Logger, logging.Handler]] = []
    for name in ("", "uvicorn.access"):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        added.append((logger, handler))
        found[name or "root"] = stream
    yield found
    for logger, handler in added:
        logger.removeHandler(handler)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            for pii in [f for f in handler.filters if isinstance(f, PiiFilter)]:
                handler.removeFilter(pii)
    logging.getLogger("uvicorn.access").propagate = True


def test_every_handler_masks_contact_details(streams):
    install_pii_filter()
    install_pii_filter()  # idempotent

    logging.getLogger("sazon.api.voice").warning(
        "Turn for %s failed: llámame al +34 612 345 678 o a ana.lopez@example.com", UUID
    )
    access = logging.getLogger("uvicorn.access")
    access.propagate = False
    access.warning(
        '%s - "%s %s HTTP/%s" %d', "10.0.0.2", "GET", "/api/x?email=ana@example.com", "1.1", 200
    )

    root = streams["root"].getvalue()
    assert "612 345 678" not in root
    assert "ana.lopez@example.com" not in root
    assert "[phone]" in root
    assert "[email]" in root
    assert UUID in root
    assert "ana@example.com" not in streams["uvicorn.access"].getvalue()
    assert "/api/x?email=[email]" in streams["uvicorn.access"].getvalue()
    for name in ("", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            assert sum(isinstance(f, PiiFilter) for f in handler.filters) == 1


def test_exception_text_is_masked(streams):
    install_pii_filter()
    try:
        raise ValueError("bad phone 612345678")
    except ValueError:
        logging.getLogger("sazon.service").exception("Turn failed")

    text = streams["root"].getvalue()
    assert "612345678" not in text
    assert "ValueError" in text


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("+52 55 1234 5602", "[phone]"),
        ("tel 612-345-678.", "tel [phone]."),
        ("cost 0.0004 usd, 1234 ms", "cost 0.0004 usd, 1234 ms"),
        (UUID, UUID),
        ("66.249.93.12:54321", "66.249.93.12:54321"),
        ("from 172.217.168.110", "from 172.217.168.110"),
        ("612.345.678", "[phone]"),
        ("612.34.56.78", "[phone]"),
        # str(datetime) used to come out as "[phone]:34:56+00:00".
        ("at 2026-10-20 12:34:56+00:00", "at 2026-10-20 12:34:56+00:00"),
        ("at 2026-10-20 12:34:56.123456", "at 2026-10-20 12:34:56.123456"),
        ("on 2026-10-20 612 345 678", "on 2026-10-20 [phone]"),
        ("ts=1760955296.123", "ts=1760955296.123"),
        ("id 1234567890123456", "id 1234567890123456"),
    ],
)
def test_mask_contacts(text, masked):
    assert mask_contacts(text) == masked


CALLBACK = "/api/auth/google/callback?code=4/0Abc-xyz&state=s3cr3t"
ACCESS_ARGS = ("10.0.0.2:5123", "GET", CALLBACK, "1.1", 302)


@pytest.fixture
def access_stream() -> Iterator[io.StringIO]:
    """uvicorn's access logger with its own `AccessFormatter`, as `uvicorn.config` sets it up."""
    from uvicorn.logging import AccessFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    )
    access = logging.getLogger("uvicorn.access")
    access.addHandler(handler)
    access.propagate = False
    level = access.level
    access.setLevel(logging.INFO)
    yield stream
    access.removeHandler(handler)
    access.propagate = True
    access.setLevel(level)


@pytest.mark.parametrize("json_logs", [False, True])
def test_access_records_with_a_query_string_still_format(access_stream, json_logs):
    """SAZ-42: the filter used to set `record.args` to None, and uvicorn's AccessFormatter
    raised unpacking it, so the OAuth callback's access line was lost."""
    configure(json_logs)
    raised: list[BaseException] = []
    handler = logging.getLogger("uvicorn.access").handlers[-1]
    original = handler.handleError
    handler.handleError = lambda record: raised.append(sys.exc_info()[1])  # type: ignore[method-assign]
    try:
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', *ACCESS_ARGS)
    finally:
        handler.handleError = original  # type: ignore[method-assign]
        for pii in [f for f in handler.filters if isinstance(f, PiiFilter)]:
            handler.removeFilter(pii)

    line = access_stream.getvalue()
    assert raised == []
    assert "4/0Abc-xyz" not in line
    assert "s3cr3t" not in line
    assert "/api/auth/google/callback?code=[redacted]&state=[redacted]" in line
    assert "302" in line


WEBHOOK_ARGS = ("66.249.93.12:54321", "POST", "/api/webhooks/google/calendar", "1.1", 200)


def test_client_ip_in_an_access_line_is_kept(access_stream):
    """A Google webhook's client address used to come out as `[phone]`."""
    configure(json_logs=False)
    handler = logging.getLogger("uvicorn.access").handlers[-1]
    try:
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', *WEBHOOK_ARGS)
    finally:
        for pii in [f for f in handler.filters if isinstance(f, PiiFilter)]:
            handler.removeFilter(pii)

    line = access_stream.getvalue()
    assert line.startswith("66.249.93.12:54321 - ")
    assert "[phone]" not in line


def test_split_contact_details_fall_back_to_the_masked_message(streams):
    """A number split across two arguments is masked by replacing the whole message."""
    install_pii_filter()
    logging.getLogger("sazon.api").warning("call %s %s", "+34 612", "345 678")

    text = streams["root"].getvalue()
    assert "612" not in text
    assert "[phone]" in text


def test_arguments_without_contact_details_are_kept(streams):
    install_pii_filter()
    record = logging.LogRecord("sazon", logging.INFO, __file__, 1, "%s at %s", ("ok", 3), None)
    assert PiiFilter().filter(record)
    assert record.args == ("ok", 3)


def test_known_names_are_masked_as_whole_words_in_any_case():
    known = ["Rosa Barros", "Rosa", "Barros", "Pérez"]
    line = 'ROSA Barros; rosa dijo "P\\u00e9rez"; Barrosa, Barroso y Rosales no son ella'
    assert mask_contacts(line, known) == (
        '[redacted]; [redacted] dijo "[redacted]"; Barrosa, Barroso y Rosales no son ella'
    )


def test_the_assistants_own_name_is_never_masked():
    # A candidate called Ana: her full name and surname are masked, the persona's name is not.
    known = ["Ana López", "Ana", "López"]
    line = "Ana: ¿me confirmas tu nombre? Candidata: ana lópez, ANA LÓPEZ"
    assert mask_contacts(line, known) == (
        "Ana: ¿me confirmas tu nombre? Candidata: [redacted], [redacted]"
    )
