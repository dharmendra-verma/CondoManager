"""Plan-execute reasoning loop for the Coordinator (CM-88, Track B).

Jira: CM-88  | Epic: Track B (Coordinator / multi-intent orchestration)  | Phase 1

This is the heart of the "true agent": a bounded plan-execute / ReAct loop that
**decomposes** a compound tenant message into sub-tasks, **plans** which B1
specialist tool to call for each, and **calls the tools observing each result
before deciding the next step** — a non-deterministic trajectory where the same
input can take a different number of steps because the policy decides the path.

It deliberately mirrors the already-merged Knowledge planner
(:mod:`agents.knowledge.planner`, CM-83/84/85) — the proven template for a
bounded, guarded, env-seamed reasoning loop:

* a :class:`CoordinatorPolicy` *seam* chooses the next action each step;
* a shared :class:`_LoopPlanner` owns the loop body — the per-iteration
  :func:`agents.orchestrator.guardrails.check`, the cost / loop-step accrual, the
  hard :data:`COORDINATOR_MAX_STEPS` bound, the per-step
  ``langgraph.node.coordinator.step`` span, and **dynamic termination** (the loop
  ends when the policy judges every sub-task satisfied, not after a fixed hop
  count);
* :func:`get_planner` selects the implementation on ``OPENAI_API_KEY`` exactly
  like ``get_chat_model`` / ``get_triage_classifier`` / ``get_knowledge_planner``
  — a deterministic :class:`StubCoordinatorPlanner` offline (CI green with no
  key) and the real LLM :class:`LLMCoordinatorPlanner` behind the key.

The policy only ever *selects* among the CM-86 B1 tools (by name); the loop
*invokes* them through :mod:`agents.coordinator.tools`, accumulating each result
on :class:`CoordinatorResult.sub_results` for B4 synthesis (CM-89). The
legal-gate invariant is preserved by the loop's caller (the ``coordinator``
node): an escalation sub-result routes the whole trajectory through
``hitl_review`` and nothing is auto-sent.

Import hygiene mirrors the knowledge planner: ``AgentState`` is referenced for
typing only and ``guardrails`` / ``langgraph_node_span`` are imported lazily
inside the loop, so there is no ``coordinator -> orchestrator -> coordinator``
import cycle at module load.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

from agents.coordinator.tools import (
    escalation_tool,
    knowledge_tool,
    maintenance_tool,
    vendor_tool,
)
from agents.orchestrator.state import EscalationRecord, Intent

if TYPE_CHECKING:  # typing-only — avoid the import cycle at module load
    from langchain_core.tools import StructuredTool

    from agents.orchestrator.state import AgentState

#: CM-18 Key Vault seed placeholder; treated as if-unset (same convention as the
#: triage / knowledge selectors).
SECRET_PLACEHOLDER: str = "REPLACE-ME"

#: Default model for the LLM policy — GPT-4o-mini, as elsewhere in the platform.
DEFAULT_COORDINATOR_MODEL: str = "gpt-4o-mini"

#: Hard upper bound on loop iterations (AC #3). Overridable via the
#: ``COORDINATOR_MAX_STEPS`` env var; exceeding it terminates the trajectory with
#: ``termination="bound"`` over whatever was gathered so far. A misconfigured
#: (non-positive / non-integer) value falls back to this default so the bound can
#: never be disabled.
COORDINATOR_MAX_STEPS: int = 8

#: Flat per-tool-call cost estimate (USD). Negligible, but accrued so the CM-26
#: cost cap applies to the *whole* trajectory (AC #3) even on the offline path
#: where the specialist stubs themselves bill nothing.
COORDINATOR_STEP_COST_USD: float = 0.000001

# GPT-4o-mini per-token USD rates (same table as triage / knowledge). The policy
# call is a short prompt + a tiny structured output; billed per step so the cost
# cap stays honest across the trajectory.
_PROMPT_PER_TOKEN: float = 0.00000015
_COMPLETION_PER_TOKEN: float = 0.00000060
LLM_COORDINATOR_COST_ESTIMATE_USD: float = (
    700 * _PROMPT_PER_TOKEN + 60 * _COMPLETION_PER_TOKEN
)

#: Route the loop returns when the trajectory is complete and nothing is gated —
#: the ``coordinator`` graph edge maps this to ``END``.
ROUTE_COORDINATOR_DONE: str = "coordinator_done"

#: Route the loop returns when a sub-result must be held for manager review —
#: maps to the ``hitl_review`` node (the legal gate, AC #5).
ROUTE_HITL_REVIEW: str = "hitl_review"

#: Route the loop returns when a Stop Rule trips mid-trajectory — the node turns
#: this into a ``guardrail_terminated`` result exactly as its own first-statement
#: guardrail check would.
ROUTE_GUARDRAIL_TERMINATED: str = "guardrail_terminated"

#: Intent -> the B1 tool the Coordinator calls for it. Mirrors the single-intent
#: routing matrix (``orchestrator.triage._ROUTE_BY_INTENT``) but maps to the
#: *tool* invoked inline, not a downstream graph node.
_TOOL_BY_INTENT: dict[Intent, str] = {
    Intent.MAINTENANCE: "maintenance_agent",
    Intent.FOLLOW_UP: "maintenance_agent",
    Intent.INQUIRY: "knowledge_agent",
    Intent.ESCALATION: "escalation_agent",
    Intent.UNKNOWN: "knowledge_agent",
}

#: The four B1 tools (CM-86) keyed by name, for the loop to select + invoke.
_TOOLS_BY_NAME: dict[str, StructuredTool] = {
    t.name: t
    for t in (maintenance_tool, vendor_tool, knowledge_tool, escalation_tool)
}


def _resolve_max_steps() -> int:
    """``COORDINATOR_MAX_STEPS`` env override, falling back to the default.

    A non-positive or non-integer value falls back to
    :data:`COORDINATOR_MAX_STEPS` so a misconfigured env can never disable the
    loop bound (mirrors the knowledge planner's ``_resolve_max_steps``).
    """
    raw = os.environ.get("COORDINATOR_MAX_STEPS", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return COORDINATOR_MAX_STEPS
        if value > 0:
            return value
    return COORDINATOR_MAX_STEPS


@dataclass(frozen=True)
class CoordinatorAction:
    """One observe -> think step's decision.

    Either call ``tool`` next (an ALL_TOOLS name) or ``finish`` the trajectory
    because every sub-task is judged satisfied.
    """

    finish: bool = False
    tool: str | None = None


@dataclass(frozen=True)
class CoordinatorResult:
    """Outcome of one Coordinator trajectory.

    ``sub_results`` is the ordered list of ``{"tool", "result"}`` observations
    accumulated across the loop — the raw material the B4 synthesis step (CM-89)
    consumes. ``route`` is the next graph route: ``coordinator_done`` (-> END),
    ``hitl_review`` (the legal gate), or ``guardrail_terminated``.
    ``guardrail_reason`` is set only when a Stop Rule tripped mid-loop, and
    ``escalation`` carries the held record when the trajectory gates to HITL.
    ``termination`` is why the loop ended: ``satisfied`` / ``bound`` /
    ``guardrail``.
    """

    sub_results: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    termination: str = "satisfied"
    cost_usd: float = 0.0
    searches: int = 0
    route: str = ROUTE_COORDINATOR_DONE
    guardrail_reason: str | None = None
    escalation: EscalationRecord | None = None


class _NextStep(BaseModel):
    """Structured output the LLM policy is forced to emit each step."""

    finish: bool = Field(
        default=False,
        description="True when every sub-task in the message is satisfied; stop.",
    )
    tool: str | None = Field(
        default=None,
        description=(
            "Name of the next tool to call (maintenance_agent / vendor_agent / "
            "knowledge_agent / escalation_agent); null when finishing."
        ),
    )


class CoordinatorPolicy(Protocol):
    """Chooses the trajectory's next action over the observations so far."""

    #: Estimated USD billed per :meth:`next_action` call (0.0 for the stub).
    cost_per_call_usd: float

    def next_action(
        self,
        *,
        message: str,
        plan: list[str],
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> CoordinatorAction:
        """Pick the next :class:`CoordinatorAction` given the gathered evidence."""
        ...


class StubCoordinatorPolicy:
    """Deterministic offline policy — walk ``plan`` in order, then finish.

    ``plan`` is the ordered, de-duplicated list of tool names derived from the
    triage-detected ``sub_intents`` (see :meth:`_LoopPlanner._plan`). Running it
    in a fixed sequence makes the offline trajectory byte-stable, so CI asserts
    the exact step sequence with no key.
    """

    cost_per_call_usd: float = 0.0

    def next_action(
        self,
        *,
        message: str,
        plan: list[str],
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> CoordinatorAction:
        if step < len(plan):
            return CoordinatorAction(tool=plan[step])
        return CoordinatorAction(finish=True)


#: System prompt for the LLM policy. ``{plan}`` is the triage-suggested tool
#: sequence (advisory), ``{observations}`` the results gathered so far.
COORDINATOR_SYSTEM_PROMPT: str = """\
You are the coordinator for a condominium management platform. A tenant's \
message needs more than one specialist. Decide the NEXT step, given the results \
gathered so far. This is step {step} of at most {max_steps}.

Available tools:
- maintenance_agent: create/de-duplicate a maintenance ticket from a repair request.
- vendor_agent: dispatch a contractor for a ticket already created this trajectory.
- knowledge_agent: answer a policy / building-inquiry question with citations.
- escalation_agent: classify an escalation, flag legal/safety risk, build a held draft.

Suggested plan from triage (advisory): {plan}

Choose exactly one action:
- call a tool: set tool to one of the names above to run it next.
- finish: set finish=true when every distinct sub-task in the message has been \
handled — do not over-call.

Rules:
- Do not repeat a tool whose sub-task is already satisfied by the results below.
- Only call vendor_agent after a maintenance ticket has been created this run.
- As the step budget runs low, prefer finishing.

Results gathered so far:
{observations}
"""


def _summarize_observations(observations: list[dict[str, Any]]) -> str:
    """Render the accumulated tool results for the LLM policy prompt."""
    if not observations:
        return "(nothing called yet)"
    lines: list[str] = []
    for i, obs in enumerate(observations):
        result = obs.get("result", {})
        status = result.get("status") or (result.get("output") or {}).get("status")
        lines.append(f"  {i + 1}. {obs.get('tool')} -> status={status}")
    return "\n".join(lines)


def _tool_args(
    tool_name: str,
    state: AgentState,
    observations: list[dict[str, Any]],
    message: str,
) -> dict[str, Any] | None:
    """Build the args for ``tool_name`` from state + prior observations.

    Returns ``None`` when the tool is not applicable yet (e.g. ``vendor_agent``
    with no maintenance ticket created earlier in the trajectory) so the loop can
    record a skipped observation rather than calling with empty fields.
    """
    if tool_name == "maintenance_agent":
        return {
            "tenant_id": state.tenant_id,
            "message": message,
            "urgency": state.urgency,
            "tone": state.tone,
            "request_id": state.request_id,
        }
    if tool_name == "knowledge_agent":
        return {
            "tenant_id": state.tenant_id,
            "question": message,
            "request_id": state.request_id,
        }
    if tool_name == "escalation_agent":
        return {
            "tenant_id": state.tenant_id,
            "message": message,
            "urgency": state.urgency,
            "tone": state.tone,
            "history": state.history,
            "request_id": state.request_id,
        }
    if tool_name == "vendor_agent":
        return _vendor_args(state, observations)
    return None


def _vendor_args(
    state: AgentState, observations: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Vendor-tool args from the most recent ``ticket_created`` observation."""
    for obs in reversed(observations):
        if obs.get("tool") != "maintenance_agent":
            continue
        out = obs.get("result", {}).get("output") or {}
        if out.get("status") == "ticket_created":
            return {
                "tenant_id": state.tenant_id,
                "category": out.get("category", ""),
                "priority": out.get("priority", ""),
                "ticket_id": out.get("ticket_id", ""),
                "unit": out.get("unit", ""),
                "status": "ticket_created",
                "intent": state.intent,
                "request_id": state.request_id,
            }
    return None


def _invoke_tool(
    tool_name: str,
    state: AgentState,
    observations: list[dict[str, Any]],
    message: str,
) -> dict[str, Any]:
    """Invoke the selected B1 tool, returning a ``{"tool", "result"}`` record."""
    tool = _TOOLS_BY_NAME.get(tool_name)
    args = _tool_args(tool_name, state, observations, message)
    if tool is None or args is None:
        return {
            "tool": tool_name,
            "result": {"status": "skipped", "reason": "no_applicable_input"},
        }
    return {"tool": tool_name, "result": tool.invoke(args)}


def _first_escalation(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The escalation observation that gates the trajectory to HITL, if any.

    Prefers a legal-flagged record (the most important to hold) but any
    escalation result gates — every escalation is HITL-reviewed (CM-32).
    """
    escalations = [
        obs
        for obs in observations
        if obs.get("tool") == "escalation_agent"
        and isinstance(obs.get("result"), dict)
        and "record_id" in obs["result"]
    ]
    if not escalations:
        return None
    for obs in escalations:
        if obs["result"].get("legal_risk"):
            return obs
    return escalations[0]


class _LoopPlanner:
    """Shared observe -> think -> act loop body.

    Subclasses supply the :class:`CoordinatorPolicy` via :meth:`_make_policy`.
    The loop owns the per-iteration guardrail check, the cost / loop-step accrual,
    the :data:`COORDINATOR_MAX_STEPS` bound, the per-step span, and the dynamic
    termination + legal-gate routing — mirroring
    :class:`agents.knowledge.planner._LoopPlanner`.
    """

    def __init__(self, *, max_steps: int | None = None) -> None:
        self._max_steps = max_steps if max_steps is not None else _resolve_max_steps()

    # -- seam the subclasses fill in -------------------------------------------

    def _make_policy(self) -> CoordinatorPolicy:
        """Return the decision policy. Created lazily, once per run."""
        raise NotImplementedError

    # -- decomposition ---------------------------------------------------------

    def _plan(self, state: AgentState) -> list[str]:
        """Decompose the message into an ordered list of tool names.

        Uses the triage-detected ``sub_intents`` (the decomposition CM-87 already
        produced); falls back to the primary intent when none are present (a
        degenerate single-step trajectory). De-duplicates by tool so two
        sub-intents that share a specialist (e.g. maintenance + follow-up) don't
        call it twice.
        """
        intents: list[Intent] = []
        for raw in state.sub_intents:
            try:
                intents.append(Intent(raw))
            except ValueError:
                continue
        if not intents:
            intents = [state.intent or Intent.UNKNOWN]

        seen: set[str] = set()
        plan: list[str] = []
        for intent in intents:
            tool = _TOOL_BY_INTENT.get(intent)
            if tool is not None and tool not in seen:
                seen.add(tool)
                plan.append(tool)
        return plan

    # -- the loop --------------------------------------------------------------

    def run(self, state: AgentState) -> CoordinatorResult:
        # Imported lazily so the coordinator package has no import-time dependency
        # on the orchestrator (which imports this package via the node) — no cycle.
        from agents.observability import langgraph_node_span  # noqa: PLC0415
        from agents.orchestrator import guardrails  # noqa: PLC0415

        plan = self._plan(state)
        policy = self._make_policy()
        message = (
            state.normalized.content
            if state.normalized is not None
            else state.raw_message
        )

        sub_results: list[dict[str, Any]] = []
        cost = 0.0
        searches = 0
        steps = 0
        termination = "satisfied"

        for step in range(self._max_steps):
            # Guardrail-first, every iteration: check the running counters BEFORE
            # any further policy/tool call so the CM-26 cost + loop caps bound the
            # whole trajectory just as they bound the node spine (AC #3).
            loop_state = state.merge(
                {
                    "cost_so_far": state.cost_so_far + cost,
                    "search_count": state.search_count + searches,
                }
            )
            gate = guardrails.check(loop_state)
            if gate.tripped:
                return CoordinatorResult(
                    sub_results=sub_results,
                    steps=steps,
                    termination="guardrail",
                    cost_usd=cost,
                    searches=searches,
                    route=ROUTE_GUARDRAIL_TERMINATED,
                    guardrail_reason=gate.reason,
                )

            # Think: choose the next action from the evidence gathered so far.
            action = policy.next_action(
                message=message,
                plan=plan,
                observations=sub_results,
                step=step,
                max_steps=self._max_steps,
            )
            cost += policy.cost_per_call_usd
            if action.finish or action.tool is None:
                termination = "satisfied"
                break

            # Act: invoke the selected tool, observe, accumulate. One span per
            # iteration (AC #6), nested under the node span. A tool call counts a
            # loop step + accrues cost so both CM-26 caps apply across the loop.
            with langgraph_node_span(
                "coordinator.step",
                step_index=step,
                tool=action.tool,
                request_id=state.request_id,
            ) as span:
                observation = _invoke_tool(action.tool, state, sub_results, message)
                searches += 1
                cost += COORDINATOR_STEP_COST_USD
                steps += 1
                sub_results.append(observation)
                result = observation.get("result", {})
                status = result.get("status") or (result.get("output") or {}).get(
                    "status"
                )
                span.set_attribute("status", str(status))
        else:
            # Step budget exhausted while the policy still wanted to act.
            termination = "bound"

        # Legal gate (AC #5): an escalation sub-result holds the whole trajectory
        # for HITL review — never auto-sent. Any other completion ends the graph.
        route = ROUTE_COORDINATOR_DONE
        escalation: EscalationRecord | None = None
        esc_obs = _first_escalation(sub_results)
        if esc_obs is not None:
            escalation = EscalationRecord.model_validate(esc_obs["result"])
            route = ROUTE_HITL_REVIEW

        return CoordinatorResult(
            sub_results=sub_results,
            steps=steps,
            termination=termination,
            cost_usd=cost,
            searches=searches,
            route=route,
            escalation=escalation,
        )


class StubCoordinatorPlanner(_LoopPlanner):
    """Deterministic offline planner — the no-credentials / CI path.

    Pairs the loop with :class:`StubCoordinatorPolicy`, so the full decompose ->
    call-tool -> observe -> terminate trajectory runs over the triage sub-intents
    with no key and a byte-stable step sequence.
    """

    def _make_policy(self) -> CoordinatorPolicy:
        return StubCoordinatorPolicy()


class LLMCoordinatorPlanner(_LoopPlanner):
    """Real LLM-driven planner — GPT-4o-mini ReAct policy over the B1 tools."""

    def _make_policy(self) -> CoordinatorPolicy:
        return LLMCoordinatorPolicy()


class LLMCoordinatorPolicy:
    """Real GPT-4o-mini policy with Pydantic-validated structured output.

    Lazy-imports ``langchain_openai`` (the CM-23 pattern) so the offline suite
    never pays the import cost or needs a key. Picks the next tool — or finishes —
    from the message + the observations gathered so far (a genuine ReAct step).
    """

    cost_per_call_usd: float = LLM_COORDINATOR_COST_ESTIMATE_USD

    def __init__(
        self, model: str = DEFAULT_COORDINATOR_MODEL, *, temperature: float = 0.0
    ) -> None:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (lazy by design)

        chat_cls: Any = ChatOpenAI
        self._llm: Any = chat_cls(
            model=model, temperature=temperature
        ).with_structured_output(_NextStep)

    def next_action(
        self,
        *,
        message: str,
        plan: list[str],
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> CoordinatorAction:
        from langchain_core.messages import (  # noqa: PLC0415  (lazy by design)
            HumanMessage,
            SystemMessage,
        )

        messages = [
            SystemMessage(
                content=COORDINATOR_SYSTEM_PROMPT.format(
                    plan=", ".join(plan) or "(none)",
                    observations=_summarize_observations(observations),
                    step=step + 1,
                    max_steps=max_steps,
                )
            ),
            HumanMessage(content=message),
        ]
        result = self._llm.invoke(messages)
        assert isinstance(result, _NextStep)
        tool = result.tool if result.tool in _TOOLS_BY_NAME else None
        # A bad/empty tool selection on a non-finish step would loop forever;
        # coerce it to a finish so the trajectory always terminates.
        return CoordinatorAction(finish=result.finish or tool is None, tool=tool)


def get_planner() -> CoordinatorPlanner:
    """Env-driven selector — real LLM loop when ``OPENAI_API_KEY`` set, else stub.

    Same convention as ``get_chat_model`` / ``get_triage_classifier`` /
    ``get_knowledge_planner``; the ``REPLACE-ME`` placeholder counts as unset.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and key != SECRET_PLACEHOLDER:
        return LLMCoordinatorPlanner()
    return StubCoordinatorPlanner()


class CoordinatorPlanner(Protocol):
    """Runs the Coordinator's plan-execute flow as a bounded reasoning loop."""

    def run(self, state: AgentState) -> CoordinatorResult:
        """Decompose ``state``'s message and run the observe -> think -> act loop."""
        ...
