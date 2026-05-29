"""Tests for AC #5: median normalization latency < 500ms on the fast path.

This measures the no-preprocessor path — Pydantic validation + dict
construction. Real Azure AI Speech / Vision round-trips add their own
latency, measured by CM-34 / CM-35 with their own budgets.

The total wall-clock for 1000 ``WebAdapter.normalize`` calls must stay
under 500ms total — that's a per-message budget of 0.5ms which Pydantic
v2 beats by 2+ orders of magnitude on simple models. The assertion is
generous so the test doesn't flake under CI noise.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from agents.channels.web import WebAdapter


def _raw(i: int) -> dict[str, object]:
    return {
        "tenant_id": f"t-{i}",
        "sender_id": f"sender_{i}",
        "content": f"message body number {i}",
        "received_at": "2026-05-29T10:00:00+00:00",
        "upstream_message_id": f"web_{i:06d}",
    }


@pytest.mark.asyncio
async def test_normalization_throughput_under_budget() -> None:
    """1000 sequential normalizations finish well under the AC #5 budget.

    AC #5 reads "median normalization latency <500ms" — interpreted as the
    per-message median, so 1000 calls comfortably fit under 500ms TOTAL
    (per-call median ~ 0.5ms). We assert 500ms total wall-clock — gives
    ~1000x headroom on a typical CI runner; flaky-CI safe.
    """
    adapter = WebAdapter()
    n = 1000
    start = time.perf_counter()
    for i in range(n):
        await adapter.normalize(_raw(i))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    # 500ms total budget = per-call median ~0.5ms. Pydantic v2 typically
    # delivers ~5-30µs per simple model — well under budget. We're really
    # asserting "no pathological regression" here.
    assert elapsed_ms < 500.0, (
        f"1000 WebAdapter.normalize() calls took {elapsed_ms:.1f}ms "
        f"(budget: 500ms). Per-call median ~ {elapsed_ms / n:.3f}ms."
    )


@pytest.mark.asyncio
async def test_normalization_concurrent_throughput() -> None:
    """Concurrent gather() over 1000 normalizations also fits the budget.

    This is the realistic shape (FastAPI handles requests concurrently).
    A regression that introduces a global lock would flag here.
    """
    adapter = WebAdapter()
    n = 1000
    start = time.perf_counter()
    await asyncio.gather(*(adapter.normalize(_raw(i)) for i in range(n)))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert elapsed_ms < 500.0, (
        f"Concurrent gather() of {n} normalizations took {elapsed_ms:.1f}ms "
        f"(budget: 500ms)."
    )
