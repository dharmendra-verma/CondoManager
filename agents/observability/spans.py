"""Manual span helpers for code that auto-instrumentation can't reach.

Jira: CM-21  | Epic: Observability  | Phase 0

The OTel + openinference + Traceloop auto-instrumentation covers FastAPI,
httpx, openai, and LangChain. It does NOT cover bare LangGraph node
functions — those need to open a span themselves. ``langgraph_node_span``
is the contract for that:

* Span name is ``langgraph.node.<node_name>`` so backends can group by node.
* The current ``request_id`` (from the correlation ContextVar) is set as a
  span attribute, so node-level spans correlate with logs and parent spans.
* Arbitrary kwargs become span attributes — convenient for ``tenant_id``,
  ``model``, ``intent``, etc. without needing a separate set_attribute call.

This module is import-light on purpose: CM-28 (the LangGraph spine) wraps
every node body with ``with langgraph_node_span(...): ...`` and we don't
want that to drag in LangChain on import.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

from .correlation import REQUEST_ID_KEY, get_request_id

#: Tracer scope name — backends can filter spans by `scope.name == "agents.observability"`.
TRACER_NAME: str = "agents.observability"


def _tracer() -> trace.Tracer:
    """Resolve the tracer at call time, not at import time.

    Necessary because tests install a fresh TracerProvider per test (see
    ``tests/observability/conftest.py``); a module-level
    ``trace.get_tracer(...)`` would bind to the first provider it sees and
    keep emitting into it after the fixture swaps in a new one.
    """
    return trace.get_tracer(TRACER_NAME)


@contextmanager
def langgraph_node_span(node_name: str, **attrs: Any) -> Iterator[Span]:
    """Open a span around a LangGraph node body.

    Args:
        node_name: The LangGraph node identifier (e.g. ``"triage"``,
            ``"knowledge.retrieve"``). Span name will be
            ``"langgraph.node.<node_name>"``.
        **attrs: Extra span attributes. Common ones: ``tenant_id``, ``model``,
            ``intent``, ``cost``. Keys with ``None`` values are dropped so
            callers can pass optionals without an ``if``.

    Yields:
        The active span. Useful when the caller wants to add events or set
        an error status before exit.

    Example::

        with langgraph_node_span("triage", tenant_id=state.tenant_id) as span:
            classification = await classify(state.message)
            span.set_attribute("intent", classification.intent)
            return state.update(intent=classification.intent)
    """
    with _tracer().start_as_current_span(f"langgraph.node.{node_name}") as span:
        # Always set request_id, even when it's "unknown" — backends can
        # then filter on "request_id != unknown" to find well-correlated spans.
        span.set_attribute(REQUEST_ID_KEY, get_request_id())
        for key, value in attrs.items():
            if value is None:
                continue
            span.set_attribute(key, value)
        yield span
