"""Escalation Agent — sub-classification, legal-risk flag, draft + alert.

Jira: CM-32  | Epic: CM-7 (Agent 4 — Escalation Manager Agent)  | Phase 1

CM-28 left the ``escalation`` node a stub that already routes
``escalation -> hitl_review -> END``, and CM-30 routes ``intent=escalation``
into it. This module is the brain the node calls:

* :class:`EscalationClassification` — Pydantic structured output (AC #1/#2):
  the :class:`~agents.orchestrator.state.EscalationCategory` plus a **semantic**
  ``legal_risk`` flag. The LLM impl raises the flag on meaning ("I've contacted
  someone about my rights"), not just keywords — that's the AC #2 distinction.
* :class:`EscalationClassifier` Protocol + :class:`LLMEscalationClassifier`
  (GPT-4o-mini) + :class:`HeuristicEscalationClassifier` (keyword fallback) +
  :func:`get_escalation_classifier` selector. Same pattern as CM-30 Triage's
  ``get_triage_classifier`` / CM-28's ``get_checkpointer`` so the suite + demo
  run with no ``OPENAI_API_KEY``.
* :func:`build_empathetic_draft` / :func:`compose_manager_alert` /
  :func:`build_record` — deterministic, template-based helpers (no LLM) that
  assemble the held tenant draft (AC #5), the manager alert text (AC #4), and
  the :class:`~agents.orchestrator.state.EscalationRecord` (AC #3).

The empathetic draft is intentionally **template-based**, not LLM-generated:
it is held behind the HITL gate for a human to edit/approve anyway, and a
deterministic acknowledgement avoids the LLM over-promising before review.
LLM-authored drafts are a possible follow-up.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .state import EscalationCategory, EscalationRecord, Tone, Urgency

#: CM-18 Key Vault placeholder; treated as if-unset (shared convention).
SECRET_PLACEHOLDER: str = "REPLACE-ME"

#: Default model — GPT-4o-mini, consistent with CM-30 Triage.
DEFAULT_ESCALATION_MODEL: str = "gpt-4o-mini"

# Per-call USD estimate (same gpt-4o-mini rates as CM-25 workbook / CM-30).
_PROMPT_PER_TOKEN: float = 0.00000015
_COMPLETION_PER_TOKEN: float = 0.00000060
LLM_ESCALATION_COST_ESTIMATE_USD: float = (
    600 * _PROMPT_PER_TOKEN + 60 * _COMPLETION_PER_TOKEN
)


class EscalationClassification(BaseModel):
    """Structured output the Escalation LLM is forced to emit (AC #1/#2)."""

    category: EscalationCategory
    # Semantic legal-risk flag (AC #2): True when the message implies legal
    # exposure (lawyer/sue/court), a health/injury claim, or liability —
    # even when phrased without those exact words.
    legal_risk: bool = False
    rationale: str = Field(
        default="",
        description="One short sentence explaining the classification, for audit.",
    )


# --- Prompt ------------------------------------------------------------------

_CATEGORY_CHOICES = " / ".join(e.value for e in EscalationCategory)

#: System prompt for the Escalation LLM. ``{history}`` is filled per-request.
ESCALATION_SYSTEM_PROMPT: str = f"""\
You are the escalation manager for a condominium platform. The message has \
already been triaged as needing escalation. Classify it.

category — {_CATEGORY_CHOICES}
  - repeat: the tenant is raising an issue they have reported before.
  - service_failure: a promised service/repair did not happen or failed.
  - safety: a safety hazard to people (not just property).
  - communication_breakdown: the tenant feels ignored / not responded to.
  - multi_issue: several distinct problems raised at once.
  - legal: explicit legal threat or demand.

legal_risk — true/false
  Set true whenever the message implies LEGAL EXPOSURE or a HEALTH/INJURY \
claim, based on MEANING not keywords. Examples that are true even without the \
words "lawyer"/"sue": "I've spoken to someone about my options", "my doctor \
says the mold made me ill", "I'll be holding you liable", "this violates my \
rights". When in doubt, set legal_risk = true (false negatives are costly).

Tenant's recent ticket history:
{{history}}

Respond ONLY via the structured schema; do not add prose.\
"""


def _format_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return "  (no prior tickets on file for this tenant)"
    lines = []
    for t in history:
        lines.append(
            f"  - #{t.get('ticket_id', '?')} [{t.get('status', '?')}] "
            f"{t.get('summary', '')}".rstrip()
        )
    return "\n".join(lines)


# --- Classifier protocol + implementations -----------------------------------


@runtime_checkable
class EscalationClassifier(Protocol):
    """Classifies an escalation message into an :class:`EscalationClassification`."""

    cost_per_call_usd: float

    def classify(
        self, message: str, history: list[dict[str, Any]]
    ) -> EscalationClassification:
        """Return the escalation classification for ``message``."""
        ...


class LLMEscalationClassifier:
    """GPT-4o-mini classifier — the semantic legal-risk flag (AC #2)."""

    cost_per_call_usd: float = LLM_ESCALATION_COST_ESTIMATE_USD

    def __init__(
        self, model: str = DEFAULT_ESCALATION_MODEL, *, temperature: float = 0.0
    ) -> None:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (lazy by design)

        self._model = model
        self._llm = ChatOpenAI(
            model=model, temperature=temperature
        ).with_structured_output(EscalationClassification)

    def classify(
        self, message: str, history: list[dict[str, Any]]
    ) -> EscalationClassification:
        from langchain_core.messages import (  # noqa: PLC0415  (lazy by design)
            HumanMessage,
            SystemMessage,
        )

        messages = [
            SystemMessage(
                content=ESCALATION_SYSTEM_PROMPT.format(history=_format_history(history))
            ),
            HumanMessage(content=message),
        ]
        result = self._llm.invoke(messages)
        assert isinstance(result, EscalationClassification)
        return result


def _has_cue(text: str, cues: tuple[str, ...]) -> bool:
    """Whole-word cue match. Word boundaries matter: substring matching would
    flag "sue" inside "issue" or "ill" inside "still" — exactly the kind of
    false positive a keyword heuristic must avoid."""
    return any(re.search(rf"\b{re.escape(cue)}\b", text) for cue in cues)


class HeuristicEscalationClassifier:
    """Deterministic keyword classifier — offline / no-credentials path.

    Keyword-based by design (the semantic flag is the LLM impl's job, AC #2).
    Conservative on ``legal_risk``: any whole-word legal/health cue trips it.
    Will NOT catch paraphrased legal exposure — that's the documented LLM
    upgrade. Matching is word-boundary (see :func:`_has_cue`) so "issue" does
    not read as "sue".
    """

    cost_per_call_usd: float = 0.0

    _LEGAL_CUES = (
        "lawyer", "sue", "suing", "lawsuit", "attorney", "court", "legal",
        "liable", "liability", "rights", "injured", "injury", "sick", "ill",
        "health", "doctor", "negligence",
    )
    _LEGAL_CATEGORY_CUES = ("lawyer", "sue", "suing", "lawsuit", "attorney", "court")
    _SAFETY_CUES = (
        "safety", "hazard", "danger", "dangerous", "fire", "gas",
        "carbon monoxide", "fall",
    )
    _REPEAT_CUES = (
        "again", "third time", "fourth time", "repeatedly", "every time",
        "recurring", "keeps", "keep",
    )
    _COMM_CUES = ("ignored", "no response", "nobody", "no one", "unanswered", "unheard")
    _SERVICE_FAIL_CUES = ("promised", "no-show", "cancelled", "failed")

    def classify(
        self, message: str, history: list[dict[str, Any]]
    ) -> EscalationClassification:
        m = (message or "").lower()
        legal_risk = _has_cue(m, self._LEGAL_CUES)

        if legal_risk and _has_cue(m, self._LEGAL_CATEGORY_CUES):
            category = EscalationCategory.LEGAL
        elif _has_cue(m, self._SAFETY_CUES):
            category = EscalationCategory.SAFETY
        elif _has_cue(m, self._REPEAT_CUES):
            category = EscalationCategory.REPEAT
        elif _has_cue(m, self._COMM_CUES):
            category = EscalationCategory.COMMUNICATION_BREAKDOWN
        else:
            category = EscalationCategory.SERVICE_FAILURE

        return EscalationClassification(
            category=category,
            legal_risk=legal_risk,
            rationale="heuristic keyword match (no LLM)",
        )


def get_escalation_classifier() -> EscalationClassifier:
    """LLM when ``OPENAI_API_KEY`` is set, else the deterministic heuristic.

    Mirrors CM-30's ``get_triage_classifier`` and CM-28's ``get_checkpointer``
    so tests + ``python -m agents.orchestrator.demo`` run with no credentials.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and key != SECRET_PLACEHOLDER:
        return LLMEscalationClassifier()
    return HeuristicEscalationClassifier()


# --- Record assembly (AC #3/#4/#5) -------------------------------------------

_CATEGORY_LABELS: dict[EscalationCategory, str] = {
    EscalationCategory.REPEAT: "a repeated, still-unresolved issue",
    EscalationCategory.SERVICE_FAILURE: "a service that did not happen as expected",
    EscalationCategory.SAFETY: "a safety concern",
    EscalationCategory.COMMUNICATION_BREAKDOWN: "feeling unheard after reaching out",
    EscalationCategory.MULTI_ISSUE: "several issues at once",
    EscalationCategory.LEGAL: "a serious concern",
}


def severity_for(classification: EscalationClassification) -> str:
    """``critical`` for legal/safety, else ``high``."""
    if classification.legal_risk or classification.category in (
        EscalationCategory.LEGAL,
        EscalationCategory.SAFETY,
    ):
        return "critical"
    return "high"


def build_empathetic_draft(
    message: str, classification: EscalationClassification, tone: Tone | None
) -> str:
    """Deterministic empathetic acknowledgement, HELD behind HITL (AC #5).

    Template-based on purpose: it is reviewed/edited by a human before any
    send, so it must not over-promise. References the situation without
    committing to specific remedies or timelines.
    """
    label = _CATEGORY_LABELS.get(classification.category, "your concern")
    opener = "I'm sorry this has been frustrating" if tone in (
        Tone.ANGRY, Tone.FRUSTRATED
    ) else "Thank you for reaching out"
    return (
        f"{opener}. I understand you're dealing with {label}, and I want you to "
        "know it's being taken seriously. A member of our management team is "
        "personally reviewing your message now and will follow up with you "
        "directly. We appreciate your patience while we make this right."
    )


def compose_manager_alert(
    *,
    tenant_id: str,
    classification: EscalationClassification,
    urgency: Urgency | None,
    tone: Tone | None,
    message: str,
) -> str:
    """Compose the manager alert text posted by the notifier (AC #4)."""
    flag = "  ⚠️ LEGAL RISK FLAGGED" if classification.legal_risk else ""
    snippet = message.strip().replace("\n", " ")
    if len(snippet) > 240:
        snippet = snippet[:237] + "..."
    return (
        f"[ESCALATION — {severity_for(classification).upper()}]{flag}\n"
        f"tenant: {tenant_id}\n"
        f"category: {classification.category.value}\n"
        f"urgency: {urgency.value if urgency else 'unknown'} | "
        f"tone: {tone.value if tone else 'unknown'}\n"
        f"message: \"{snippet}\"\n"
        f"An empathetic draft reply is prepared and HELD for your approval."
    )


def build_record(
    *,
    record_id: str,
    tenant_id: str,
    request_id: str,
    classification: EscalationClassification,
    urgency: Urgency | None,
    tone: Tone | None,
    message: str,
) -> EscalationRecord:
    """Assemble the full :class:`EscalationRecord` (status ``pending_review``)."""
    draft = build_empathetic_draft(message, classification, tone)
    alert = compose_manager_alert(
        tenant_id=tenant_id,
        classification=classification,
        urgency=urgency,
        tone=tone,
        message=message,
    )
    internal = (
        f"{classification.category.value} escalation for tenant {tenant_id}; "
        f"legal_risk={classification.legal_risk}; "
        f"rationale={classification.rationale or 'n/a'}"
    )
    return EscalationRecord(
        record_id=record_id,
        tenant_id=tenant_id,
        request_id=request_id,
        category=classification.category,
        legal_risk=classification.legal_risk,
        severity=severity_for(classification),  # type: ignore[arg-type]
        internal_summary=internal,
        manager_alert=alert,
        tenant_draft=draft,
        status="pending_review",
        hitl_required=True,
    )
