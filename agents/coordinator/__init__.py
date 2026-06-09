"""``agents.coordinator`` — tools (CM-86) + loop (CM-88) + synthesis (CM-89) + eval (CM-90).

Track B: CM-86 wraps each specialist agent as a typed
:class:`~langchain_core.tools.StructuredTool` so the Coordinator can select and
call it; CM-88 adds the bounded plan-execute reasoning loop that decomposes a
compound message and drives those tools; CM-89 weaves the loop's accumulated
sub-results into one coherent tenant reply; CM-90 scores sub-task coverage over a
compound-request golden set. The tools are thin, deterministic adapters over the
existing agents — no specialist behaviour changes.

Public API:

* :data:`maintenance_tool`, :data:`vendor_tool`, :data:`knowledge_tool`,
  :data:`escalation_tool` — the four specialist tools.
* :data:`ALL_TOOLS` — all four, in routing order, for the Coordinator to bind.
* :class:`MaintenanceToolArgs`, :class:`VendorToolArgs`,
  :class:`KnowledgeToolArgs`, :class:`EscalationToolArgs` — their args schemas.
* :func:`get_planner` — env-driven selector for the Coordinator planner
  (deterministic stub offline, real LLM ReAct loop behind ``OPENAI_API_KEY``).
* :class:`CoordinatorPlanner`, :class:`CoordinatorResult`,
  :data:`COORDINATOR_MAX_STEPS` — the planner protocol, its result, and the bound.
* :func:`synthesize`, :func:`get_synthesizer`, :class:`SynthesisResult` — the
  CM-89 cross-sub-task response synthesis (pure function + env-driven selector).
* :func:`subtask_coverage`, :func:`run_coverage_eval`, :class:`CoverageReport`,
  :data:`COVERAGE_TARGET` — the CM-90 sub-task-coverage eval (pure scorer).
"""

from __future__ import annotations

from .eval import (
    COVERAGE_TARGET,
    CoverageReport,
    run_coverage_eval,
    subtask_coverage,
)
from .planner import (
    COORDINATOR_MAX_STEPS,
    CoordinatorPlanner,
    CoordinatorResult,
    get_planner,
)
from .synthesis import (
    SynthesisResult,
    get_synthesizer,
    synthesize,
)
from .tools import (
    ALL_TOOLS,
    EscalationToolArgs,
    KnowledgeToolArgs,
    MaintenanceToolArgs,
    VendorToolArgs,
    escalation_tool,
    knowledge_tool,
    maintenance_tool,
    vendor_tool,
)

__all__ = [
    "ALL_TOOLS",
    "COORDINATOR_MAX_STEPS",
    "COVERAGE_TARGET",
    "CoordinatorPlanner",
    "CoordinatorResult",
    "CoverageReport",
    "EscalationToolArgs",
    "KnowledgeToolArgs",
    "MaintenanceToolArgs",
    "SynthesisResult",
    "VendorToolArgs",
    "escalation_tool",
    "get_planner",
    "get_synthesizer",
    "knowledge_tool",
    "maintenance_tool",
    "run_coverage_eval",
    "subtask_coverage",
    "synthesize",
    "vendor_tool",
]
