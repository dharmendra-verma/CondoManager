"""Tests for ``agents.observability.logging``.

Covers ``configure_logging`` idempotency, the JSON formatter shape, the
PII masking filter integration, and the contextvar plumbing.

Per the CM-27 plan, the reset fixture lives in THIS file (not in
``conftest.py``) so concurrent stories (CM-26) don't conflict on
``conftest.py``.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Generator

import pytest
from agents.observability import configure_logging, correlation
from agents.observability import logging as obs_logging
from agents.observability.logging import (
    JsonFormatter,
    PiiMaskingFilter,
)


@pytest.fixture(autouse=True)
def _reset_logging() -> Generator[None, None, None]:
    """Clear handlers + idempotency latch around every test in this file.

    NOT in conftest.py — keeping it here keeps the file diff disjoint from
    other observability test stories that may also touch conftest.py.
    """
    obs_logging._reset_for_tests()
    yield
    obs_logging._reset_for_tests()


@pytest.fixture
def logged_lines() -> Generator[list[str], None, None]:
    """Install configure_logging redirected to an in-memory stream.

    Yields the list of JSON strings written to the stream so tests can
    assert on the parsed shape.
    """
    obs_logging._reset_for_tests()
    stream = io.StringIO()

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="test-svc", environment="test"))
    handler.addFilter(PiiMaskingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    obs_logging._configured = True  # match what configure_logging would set

    lines: list[str] = []

    class _Reader:
        def __init__(self, s: io.StringIO) -> None:
            self.s = s

        def read(self) -> list[str]:
            v = self.s.getvalue()
            return [ln for ln in v.splitlines() if ln.strip()]

    reader = _Reader(stream)

    # Hand the consumer a callable; recompute on each access.
    yield lines  # type: ignore[misc]
    # Refresh lines after the test ran.
    lines[:] = reader.read()


def _decode_last(stream: io.StringIO) -> dict[str, object]:
    """Helper — parse the last JSON line emitted to ``stream``."""
    raw = stream.getvalue().strip().splitlines()[-1]
    return json.loads(raw)


def test_configure_logging_is_idempotent() -> None:
    """Calling configure_logging twice does NOT double-add handlers."""
    configure_logging(service_name="svc", environment="dev")
    handlers_after_first = len(logging.getLogger().handlers)
    configure_logging(service_name="svc", environment="dev")
    handlers_after_second = len(logging.getLogger().handlers)
    assert handlers_after_first == handlers_after_second == 1


def test_configure_logging_replaces_basicconfig() -> None:
    """If a developer called basicConfig() earlier, configure_logging
    sweeps those handlers so we don't double-emit (human + JSON)."""
    logging.basicConfig()
    pre = len(logging.getLogger().handlers)
    assert pre >= 1
    configure_logging(service_name="svc", environment="dev")
    assert len(logging.getLogger().handlers) == 1
    formatter = logging.getLogger().handlers[0].formatter
    assert isinstance(formatter, JsonFormatter)


def test_emits_required_fields() -> None:
    """Every JSON record has the documented required fields."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc-x", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("demo").info("hello")
    record = _decode_last(stream)
    required = {
        "ts", "level", "logger", "msg",
        "service_name", "environment", "service_version",
        "request_id",
    }
    assert required <= set(record.keys())
    assert record["level"] == "INFO"
    assert record["logger"] == "demo"
    assert record["msg"] == "hello"
    assert record["service_name"] == "svc-x"
    assert record["environment"] == "dev"


def test_omits_optional_fields_when_unset() -> None:
    """Without with_tenant / with_agent, those keys must NOT appear."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("demo").info("hi")
    record = _decode_last(stream)
    assert "tenant_id" not in record
    assert "agent_name" not in record


def test_emits_contextvar_values_when_scoped() -> None:
    """request_id (from with_request_id) + tenant_id (with_tenant) +
    agent_name (with_agent) all flow into the JSON line."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    with correlation.with_request_id("req_abcdef123456"), \
            correlation.with_tenant("tenant-7"), \
            correlation.with_agent("triage"):
        logging.getLogger("demo").info("scoped event")

    record = _decode_last(stream)
    assert record["request_id"] == "req_abcdef123456"
    assert record["tenant_id"] == "tenant-7"
    assert record["agent_name"] == "triage"


def test_pii_filter_masks_msg() -> None:
    """An email in the log message is masked in the JSON output."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("demo").info("user user@example.com signed in")
    record = _decode_last(stream)
    assert "user@example.com" not in record["msg"]
    assert "***@***.***" in record["msg"]


def test_pii_filter_masks_tuple_args() -> None:
    """Args passed in % style get masked too — the filter walks them."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("demo").info("contact %s phone %s", "user@example.com", "+919876543210")
    record = _decode_last(stream)
    assert "user@example.com" not in record["msg"]
    assert "+919876543210" not in record["msg"]
    assert "***@***.***" in record["msg"]
    assert "+***" in record["msg"]


def test_pii_filter_does_not_raise_on_non_string_args() -> None:
    """Mixed-type args (None, int, str-with-PII) must not crash the filter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("demo").info("vals=%s %s %s", None, 42, "to.dh@example.com")
    record = _decode_last(stream)
    assert "to.dh@example.com" not in record["msg"]
    assert "***@***.***" in record["msg"]


def test_exception_populates_exc_info() -> None:
    """logger.exception(...) yields a record with exc_info in the JSON."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    try:
        raise ValueError("bad input")
    except ValueError:
        logging.getLogger("demo").exception("crashed")

    record = _decode_last(stream)
    assert "exc_info" in record
    assert "ValueError" in str(record["exc_info"])
    assert "bad input" in str(record["exc_info"])


def test_request_id_unknown_when_no_scope() -> None:
    """Outside any with_request_id, the field is still present, value 'unknown'."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service_name="svc", environment="dev"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("demo").info("no scope")
    record = _decode_last(stream)
    assert record["request_id"] == "unknown"
