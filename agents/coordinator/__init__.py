"""``agents.coordinator`` — specialist tools (CM-86) + the plan-execute loop (CM-88).

Track B: CM-86 wraps each specialist agent as a typed
:class:`~langchain_core.tools.StructuredTool` so the Coordinator can select and
call it; CM-88 adds the bounded plan-execute reasoning loop that decomposes a
compound message and drives those tools. The tools are thin, deterministic
adapters over the existing agents — no specialist behaviour changes.

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
"""

from __future__ import annotations

from .planner import (
    COORDINATOR_MAX_STEPS,
    CoordinatorPlanner,
    CoordinatorResult,
    get_planner,
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
    "CoordinatorPlanner",
    "CoordinatorResult",
    "EscalationToolArgs",
    "KnowledgeToolArgs",
    "MaintenanceToolArgs",
    "VendorToolArgs",
    "escalation_tool",
    "get_planner",
    "knowledge_tool",
    "maintenance_tool",
    "vendor_tool",
]
