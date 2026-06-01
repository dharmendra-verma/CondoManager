"""CM-46: the web adapter emits the ack-latency PRD metric at the entry layer."""

from __future__ import annotations

import pytest
from agents.channels.web import WebAdapter
from agents.observability import METRIC_ACK_LATENCY
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _raw(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant_id": "t-ack",
        "sender_id": "user_1",
        "content": "hello",
        "received_at": "2026-05-29T10:00:00+00:00",
        "upstream_message_id": "web_ack",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_normalize_emits_one_ack_latency_span(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    msg = await WebAdapter().normalize(_raw())

    spans = [s for s in in_memory_spans.get_finished_spans() if s.name == METRIC_ACK_LATENCY]
    assert len(spans) == 1
    span = spans[0]
    # Value is the channel→us latency the message itself computes.
    assert span.attributes["metric.value"] == pytest.approx(msg.latency_ms)
    assert span.attributes["channel"] == "web"
    assert span.attributes["tenant_id"] == "t-ack"


@pytest.mark.asyncio
async def test_no_emit_when_normalization_fails(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """A rejected payload never reaches the emit — no ack-latency span."""
    from agents.channels.base import NormalizationError

    bad = _raw()
    del bad["tenant_id"]
    with pytest.raises(NormalizationError):
        await WebAdapter().normalize(bad)

    assert not [s for s in in_memory_spans.get_finished_spans() if s.name == METRIC_ACK_LATENCY]
