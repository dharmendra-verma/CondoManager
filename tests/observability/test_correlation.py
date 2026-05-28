"""request_id ContextVar + OTel baggage tests."""

from __future__ import annotations

import asyncio

import pytest
from agents.observability import (
    REQUEST_ID_KEY,
    UNKNOWN_REQUEST_ID,
    get_request_id,
    new_request_id,
    with_request_id,
)
from opentelemetry import baggage, context


def test_default_outside_scope_is_unknown() -> None:
    """Outside any `with_request_id`, `get_request_id` returns the sentinel."""
    assert get_request_id() == UNKNOWN_REQUEST_ID


def test_with_request_id_sets_and_restores() -> None:
    """Setting inside the block; back to default outside."""
    assert get_request_id() == UNKNOWN_REQUEST_ID
    with with_request_id("req_abc") as rid:
        assert rid == "req_abc"
        assert get_request_id() == "req_abc"
    assert get_request_id() == UNKNOWN_REQUEST_ID


def test_with_request_id_auto_generates() -> None:
    """`with_request_id()` (no arg) mints a fresh id."""
    with with_request_id() as rid:
        assert rid.startswith("req_")
        # 4 prefix chars + 12 hex chars
        assert len(rid) == len("req_") + 12


def test_nested_scopes_inner_wins_then_outer_restored() -> None:
    with with_request_id("req_outer"):
        with with_request_id("req_inner"):
            assert get_request_id() == "req_inner"
        assert get_request_id() == "req_outer"
    assert get_request_id() == UNKNOWN_REQUEST_ID


def test_baggage_carries_request_id_inside_scope() -> None:
    """The id is attached to OTel baggage so HTTP propagators pick it up."""
    with with_request_id("req_baggage_test"):
        assert baggage.get_baggage(REQUEST_ID_KEY) == "req_baggage_test"
    # Outside the scope, baggage is detached.
    assert baggage.get_baggage(REQUEST_ID_KEY, context.get_current()) is None


@pytest.mark.asyncio
async def test_request_id_survives_asyncio_gather() -> None:
    """Each coroutine started under its own `with_request_id` sees its own id.

    This is the canonical "request isolation under FastAPI concurrency"
    test — two requests in flight at the same time must not bleed ids.
    """
    results: list[str] = []

    async def child(rid: str) -> None:
        # Each child gets its own contextvar copy because asyncio.create_task /
        # asyncio.gather both `copy_context()` per task.
        with with_request_id(rid):
            await asyncio.sleep(0)
            results.append(get_request_id())

    await asyncio.gather(child("req_one"), child("req_two"))
    assert sorted(results) == ["req_one", "req_two"]


def test_new_request_id_format() -> None:
    rid = new_request_id()
    assert rid.startswith("req_")
    assert len(rid) == len("req_") + 12
