"""Stop-Rule guardrails (cost cap + loop cap) with CM-26 alert emissions.

Jira: CM-28 (this file)  | CM-26 (the alert KQL that queries the events here)
Epic: CM-Epic 4  | Phase 0

The two event names emitted by :func:`check` MUST match the literal
strings in :file:`infra/bicep/modules/alert-rules.bicep`. The CM-26
``alert-guardrail-trip-<env>`` scheduled-query rule queries::

    customEvents
    | where name in ('guardrail.cost_cap', 'guardrail.loop_cap')
    | ...

Changing either string here without changing the Bicep KQL silently
breaks the alert (it would no longer fire). The
:file:`tests/orchestrator/test_guardrails.py` suite asserts the literal
strings; the :file:`tests/infra/test_bicep_lint.sh` suite asserts they
appear in ``guardrails.py``. Two-sided regression closes the loop.

Stop Rules from the CM-28 AC:
* Cost cap = $5 per request (cumulative ``state.cost_so_far``).
* Loop cap = 50 (``state.search_count``).

Boundaries: ``>``, not ``>=``. ``cost_so_far == 5.0`` does NOT trip;
``cost_so_far > 5.0`` does. Tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import trace

from .state import AgentState

#: Cost cap in USD per request. Tripping emits ``guardrail.cost_cap``.
COST_CAP_USD: float = 5.0

#: Loop / search-step cap. Tripping emits ``guardrail.loop_cap``.
LOOP_CAP: int = 50

#: Event name that CM-26's ``alert-guardrail-trip-<env>`` rule queries.
#: DO NOT change without also updating ``alert-rules.bicep``.
EVENT_COST_CAP: str = "guardrail.cost_cap"

#: Event name that CM-26's ``alert-guardrail-trip-<env>`` rule queries.
#: DO NOT change without also updating ``alert-rules.bicep``.
EVENT_LOOP_CAP: str = "guardrail.loop_cap"


@dataclass(frozen=True)
class GuardrailResult:
    """Result of a single guardrail check at the start of a node body."""

    tripped: bool
    reason: str | None = None


def check(state: AgentState) -> GuardrailResult:
    """Stop-Rule check — call as the first statement in every node body.

    When a rule trips, emits the corresponding ``customEvents`` span so
    the CM-26 alert fires AND returns a result the caller uses to skip
    its own work + route the graph to the terminal
    ``guardrail_terminated`` node.

    Args:
        state: Current ``AgentState``; only ``cost_so_far``,
            ``search_count``, ``request_id``, and ``tenant_id`` are read.

    Returns:
        :class:`GuardrailResult` with ``tripped=True`` and a
        human-readable ``reason`` when a Stop Rule fired; ``tripped=False``
        and ``reason=None`` otherwise.
    """
    if state.cost_so_far > COST_CAP_USD:
        _emit_trip(EVENT_COST_CAP, state)
        return GuardrailResult(
            True, f"cost {state.cost_so_far} > {COST_CAP_USD}"
        )
    if state.search_count > LOOP_CAP:
        _emit_trip(EVENT_LOOP_CAP, state)
        return GuardrailResult(
            True, f"search_count {state.search_count} > {LOOP_CAP}"
        )
    return GuardrailResult(False, None)


def _emit_trip(event_name: str, state: AgentState) -> None:
    """Emit the CM-26 ``customEvents`` span the alert KQL queries for.

    The span is short-lived (start / set_attribute / end) — it does no
    work other than landing a single row in App Insights ``customEvents``
    with the right ``name`` and a handful of context attributes the
    alert payload includes when paging.
    """
    tracer = trace.get_tracer("agents.orchestrator.guardrails")
    span = tracer.start_span(event_name)
    try:
        span.set_attribute("request_id", state.request_id)
        span.set_attribute("tenant_id", state.tenant_id)
        span.set_attribute("guardrail.cost_so_far_usd", state.cost_so_far)
        span.set_attribute("guardrail.search_count", state.search_count)
    finally:
        # Always end the span — leaking an open span would leave the
        # `customEvents` row missing from the workbook + alert.
        span.end()
