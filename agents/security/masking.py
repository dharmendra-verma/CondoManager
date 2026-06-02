"""Masking middleware for the trace layer (CM-38 AC2).

AC2 asks for masking at **both** the log and trace layers. The log layer is
already covered by CM-27's ``PiiMaskingFilter`` (installed by
``configure_logging``). This module adds:

* ``mask_text`` — the single masking facade for the whole codebase. It
  delegates to CM-27 ``mask_pii`` so there is exactly one masking
  implementation; log filter, span processor, audit ``detail``, and the
  notifier seams all go through the same regexes (no drift between layers).
* :class:`PiiMaskingSpanProcessor` — an OpenTelemetry ``SpanProcessor`` that
  masks string-valued span attributes so a tenant's email/phone never reaches
  App Insights / OTLP inside a span attribute. Wired into
  ``setup_tracer_provider`` (CM-21) so every workload gets it.

Why mask in ``on_end`` (not ``on_start``): auto-instrumentation (FastAPI,
httpx, OpenAI) sets most attributes *after* the span starts — by ``on_end``
they are all present. The processor mutates the span's attribute store in
place; it is registered **before** the exporting processor so its synchronous
``on_end`` runs first. (With ``BatchSpanProcessor`` export happens later on a
worker thread; the in-place mutation has already completed synchronously, so
the batch exports masked values. The only theoretical race is a flush firing
mid-dispatch, which the 5s batch timer makes practically impossible — noted in
docs/SECURITY.md.)
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import cast

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from agents.observability.pii import mask_pii

_log = logging.getLogger(__name__)


def mask_text(text: str) -> str:
    """Mask PII in ``text``. The one masking entry point for the codebase.

    Delegates to CM-27 ``mask_pii`` (conservative regexes: email, E.164 phone,
    Luhn-valid card, known key prefixes). Pure, dependency-free, offline-safe —
    deliberately *not* the Azure detector, so masking never depends on a
    network call or makes a log line block.
    """
    return mask_pii(text)


class PiiMaskingSpanProcessor(SpanProcessor):
    """Mask string-valued span attributes before export (AC2, trace layer).

    Only ``str`` attribute values are touched (ints, bools, and sequences pass
    through untouched — they don't carry free-text PII). Masking must never
    break tracing, so every failure is swallowed (same contract as CM-27's
    log filter).
    """

    def on_start(self, span: object, parent_context: object | None = None) -> None:
        # Attributes are typically incomplete at start; we mask at on_end.
        return None

    def on_end(self, span: ReadableSpan) -> None:
        try:
            attributes = span.attributes
            if not attributes:
                return
            # ``span._attributes`` is the live BoundedAttributes store backing
            # the read-only ``.attributes`` view. Mutating it here (before the
            # exporting processor's on_end) is what makes the exported span
            # carry masked values — there is no public setter on an ended span.
            # The cast reflects that BoundedAttributes is a MutableMapping at
            # runtime even though ``.attributes`` is typed read-only.
            store = cast("MutableMapping[str, object]", span._attributes)
            for key, value in list(attributes.items()):
                if isinstance(value, str):
                    masked = mask_pii(value)
                    if masked != value:
                        store[key] = masked
        except Exception:  # noqa: BLE001 — masking must never break tracing
            _log.debug("span attribute masking skipped due to internal error", exc_info=True)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True
