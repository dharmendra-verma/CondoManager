"""``agents.coordinator`` — specialist agents as callable LangChain tools (CM-86).

Track B story 1: wraps each specialist agent as a typed
:class:`~langchain_core.tools.StructuredTool` so the future Coordinator
(CM-87/88) can select and call it. The tools are thin, deterministic adapters
over the existing agents — no specialist behaviour changes.

Public API:

* :data:`maintenance_tool`, :data:`vendor_tool`, :data:`knowledge_tool`,
  :data:`escalation_tool` — the four specialist tools.
* :data:`ALL_TOOLS` — all four, in routing order, for the Coordinator to bind.
* :class:`MaintenanceToolArgs`, :class:`VendorToolArgs`,
  :class:`KnowledgeToolArgs`, :class:`EscalationToolArgs` — their args schemas.
"""

from __future__ import annotations

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
    "EscalationToolArgs",
    "KnowledgeToolArgs",
    "MaintenanceToolArgs",
    "VendorToolArgs",
    "escalation_tool",
    "knowledge_tool",
    "maintenance_tool",
    "vendor_tool",
]
