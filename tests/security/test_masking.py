"""Masking tests (CM-38 AC2) — facade + trace-layer span processor."""

from __future__ import annotations

from agents.security.masking import PiiMaskingSpanProcessor, mask_text
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


def test_mask_text_redacts_known_categories() -> None:
    out = mask_text("email a@b.com phone +14155552671 key sk-ABCDEFGHIJKLMNOP1234")
    assert "a@b.com" not in out
    assert "+14155552671" not in out
    assert "sk-ABCDEFGHIJKLMNOP1234" not in out
    assert "***@***.***" in out


def test_mask_text_is_noop_on_clean_text() -> None:
    assert mask_text("no pii here") == "no pii here"


def _provider_with_masking() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    # Masking processor registered FIRST so its on_end runs before export.
    provider.add_span_processor(PiiMaskingSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_span_processor_masks_string_attributes() -> None:
    provider, exporter = _provider_with_masking()
    tracer = provider.get_tracer("cm38-test")
    with tracer.start_as_current_span("op") as span:
        span.set_attribute("tenant.contact", "tenant jane@example.com called")
        span.set_attribute("sender", "+14155552671")

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    attrs = finished[0].attributes or {}
    assert "jane@example.com" not in attrs["tenant.contact"]
    assert "***@***.***" in attrs["tenant.contact"]
    assert attrs["sender"] == "+***"


def test_span_processor_leaves_non_string_attributes_untouched() -> None:
    provider, exporter = _provider_with_masking()
    tracer = provider.get_tracer("cm38-test")
    with tracer.start_as_current_span("op") as span:
        span.set_attribute("count", 42)
        span.set_attribute("ok", True)
        span.set_attribute("clean", "orchestrator")

    attrs = exporter.get_finished_spans()[0].attributes or {}
    assert attrs["count"] == 42
    assert attrs["ok"] is True
    assert attrs["clean"] == "orchestrator"  # no PII shape -> unchanged


def test_span_processor_on_end_never_raises_on_attributeless_span() -> None:
    # A defensive unit check: feeding a minimal object that mimics a span with
    # no attributes must be swallowed, not raised (logging-must-never-raise).
    class _FakeSpan:
        attributes: dict[str, object] = {}

    PiiMaskingSpanProcessor().on_end(_FakeSpan())  # type: ignore[arg-type]


def test_span_processor_force_flush_and_shutdown() -> None:
    proc = PiiMaskingSpanProcessor()
    assert proc.force_flush() is True
    proc.shutdown()  # no-op, must not raise
