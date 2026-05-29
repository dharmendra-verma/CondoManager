"""`request_id` correlation: ContextVar + OTel baggage + span attribute,
plus ``tenant_id`` and ``agent_name`` correlation contextvars for the
JSON log formatter (CM-27).

Jira: CM-21 (request_id)  | CM-27 (tenant_id + agent_name)
Epic: Observability  | Phase 0

Every inbound boundary (HTTP handler, queue consumer, scheduled job)
must wrap its work in ``with_request_id()`` so that:

* In-process async code sees the same ``request_id`` via the ContextVar
  — survives ``asyncio.gather``, ``asyncio.create_task``, and FastAPI's
  per-request task isolation.
* Outbound HTTP calls (httpx) carry the ``request_id`` in OTel baggage,
  so downstream services see it after extracting baggage from incoming
  headers (the OTel BaggagePropagator does this automatically).
* The currently-active span gets a ``request_id`` attribute, so traces
  in App Insights / LangSmith / Langfuse can be filtered or joined to
  log lines.

If a caller skips ``with_request_id`` and asks ``get_request_id()``, the
default sentinel ``"unknown"`` is returned — never raises. That keeps
spans well-formed even when correlation is misconfigured upstream.

CM-27 adds ``tenant_id`` and ``agent_name`` as separate contextvars.
They default to ``None`` rather than a sentinel because absence is the
honest signal at most call sites (background jobs, init code) — the
JSON log formatter omits the field entirely when unset, so log lines
don't carry stale "tenant=None" noise.
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import baggage, context, trace

#: Span attribute key + baggage key. Single source of truth.
REQUEST_ID_KEY: str = "request_id"

#: Sentinel returned by `get_request_id()` outside any `with_request_id()` scope.
UNKNOWN_REQUEST_ID: str = "unknown"

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default=UNKNOWN_REQUEST_ID,
)

# CM-27: tenant + agent contextvars. Default ``None`` (not a sentinel) so the
# JSON formatter can omit the field cleanly when no scope is active.
_tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_id",
    default=None,
)

_agent_name_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agent_name",
    default=None,
)


def new_request_id() -> str:
    """Generate a fresh request_id (``req_<12 hex chars>``).

    Used by ``with_request_id()`` when no id is provided. The short prefix
    makes IDs easy to grep in log streams.
    """
    return f"req_{uuid.uuid4().hex[:12]}"


def set_request_id(request_id: str) -> contextvars.Token[str]:
    """Set the request_id directly without scoping. Prefer ``with_request_id``.

    Returns the Token from ``ContextVar.set`` so the caller can reset later.
    Useful when integrating with frameworks (e.g. FastAPI middleware) that
    own the lifecycle and prefer explicit teardown to a context manager.
    """
    return _request_id_var.set(request_id)


def get_request_id() -> str:
    """Return the current request_id, or ``"unknown"`` if none is set."""
    return _request_id_var.get()


@contextmanager
def with_request_id(request_id: str | None = None) -> Iterator[str]:
    """Scope a request_id for the duration of the `with` block.

    Sets the ContextVar, attaches it to OTel baggage, and tags the currently
    active span with ``request_id=<id>`` so trace/log correlation works in
    any OTel-aware backend.

    Args:
        request_id: Override id. If ``None`` (the typical case at the
            inbound boundary), a fresh id is generated via ``new_request_id()``.

    Yields:
        The request_id (either passed in or generated) — convenient when
        the caller wants to log it or echo it back to the client.

    Example::

        @app.get("/echo/{msg}")
        async def echo(msg: str) -> dict[str, str]:
            with with_request_id() as rid:
                return {"request_id": rid, "msg": msg}
    """
    rid = request_id or new_request_id()
    var_token = _request_id_var.set(rid)
    ctx = baggage.set_baggage(REQUEST_ID_KEY, rid)
    ctx_token = context.attach(ctx)
    span = trace.get_current_span()
    if span.is_recording():
        # Tag the current span (if any) so traces filter / join cleanly with
        # logs. Nested spans inherit through the contextvar in `langgraph_node_span`.
        span.set_attribute(REQUEST_ID_KEY, rid)
    try:
        yield rid
    finally:
        # Order matters: detach the OTel context first (uses the same
        # underlying machinery as ContextVar), then reset the var.
        context.detach(ctx_token)
        _request_id_var.reset(var_token)


# ---------------------------------------------------------------------------
# CM-27: tenant_id + agent_name correlation
# ---------------------------------------------------------------------------


def get_tenant_id() -> str | None:
    """Return the current tenant_id, or ``None`` if no scope is active."""
    return _tenant_id_var.get()


def set_tenant_id(tenant_id: str | None) -> contextvars.Token[str | None]:
    """Set the tenant_id directly. Prefer ``with_tenant`` when scoping is OK."""
    return _tenant_id_var.set(tenant_id)


def get_agent_name() -> str | None:
    """Return the current agent_name, or ``None`` if no scope is active."""
    return _agent_name_var.get()


def set_agent_name(agent_name: str | None) -> contextvars.Token[str | None]:
    """Set the agent_name directly. Prefer ``with_agent`` when scoping is OK."""
    return _agent_name_var.set(agent_name)


@contextmanager
def with_tenant(tenant_id: str) -> Iterator[str]:
    """Scope a tenant_id for the duration of the `with` block.

    Used by inbound channel adapters (WhatsApp, Telegram, email) once they
    identify which tenant owns the incoming message — wrap the downstream
    work so logs and observations carry the tenant_id automatically.

    Yields the tenant_id (convenient for the caller to echo).
    """
    token = _tenant_id_var.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _tenant_id_var.reset(token)


@contextmanager
def with_agent(agent_name: str) -> Iterator[str]:
    """Scope an agent_name for the duration of the `with` block.

    LangGraph nodes use this to tag their work — combined with the
    structured-logging filter, each log line is attributed to the
    specific agent (``triage``, ``maintenance``, ``escalation``, …) that
    produced it.

    Yields the agent_name (convenient for the caller to echo).
    """
    token = _agent_name_var.set(agent_name)
    try:
        yield agent_name
    finally:
        _agent_name_var.reset(token)
