"""Tests for the Coordinator cross-sub-task response synthesis (CM-89, Track B).

Synthesis is a pure function over the CM-88 ``{"tool", "result"}`` sub-result
records, so the whole suite runs offline with no ``OPENAI_API_KEY``. Coverage:

* multi-result ordering — held escalation / emergency first, FYI answers last;
* provenance preservation — ``TKT-…`` codes and inline ``[n]`` citations survive;
* held-draft wording — an escalation reflects the held state, never "sent", and
  the held empathetic draft is not leaked into the reply;
* partial-failure messaging — a refused answer / ``no_vendor`` is surfaced as an
  unresolved note while the successful parts are still delivered;
* the env-driven :func:`get_synthesizer` selector (template vs LLM, REPLACE-ME);
* JSON-serializable provenance + the deterministic empty case.
"""

from __future__ import annotations

import json

import pytest
from agents.coordinator.synthesis import (
    LLMSynthesizer,
    SynthesisResult,
    TemplateSynthesizer,
    _provenance_tokens,
    get_synthesizer,
    synthesize,
)

# --- sub-result fixtures (the exact CM-88 tool-result shapes) -----------------


def _maintenance(ticket_id: str = "TKT-ABC12345", priority: str = "P2") -> dict:
    return {
        "tool": "maintenance_agent",
        "result": {
            "output": {
                "status": "ticket_created",
                "ticket_id": ticket_id,
                "unit": "4B",
                "category": "plumbing",
                "priority": priority,
                "eta": "24h",
                "confirmation": (
                    f"Thanks for reporting your plumbing issue for unit 4B. "
                    f"We've logged it as ticket {ticket_id} (priority {priority})."
                ),
            }
        },
    }


def _knowledge_answered() -> dict:
    return {
        "tool": "knowledge_agent",
        "result": {
            "status": "answered",
            "answer": "Pets are allowed with a refundable deposit [1].",
            "citations": [{"index": 1, "doc_id": "pol-1", "doc_title": "Pet Policy"}],
            "confidence": 0.51,
            "refused": False,
        },
    }


def _knowledge_refused() -> dict:
    return {
        "tool": "knowledge_agent",
        "result": {
            "status": "refused",
            "answer": "",
            "citations": [],
            "confidence": 0.12,
            "refused": True,
        },
    }


def _escalation(*, legal_risk: bool = True, draft: str = "SECRET_HELD_DRAFT_TEXT") -> dict:
    return {
        "tool": "escalation_agent",
        "result": {
            "record_id": "esc-deadbeef",
            "tenant_id": "t-1",
            "request_id": "r-1",
            "category": "legal" if legal_risk else "service_failure",
            "legal_risk": legal_risk,
            "severity": "critical" if legal_risk else "high",
            "internal_summary": "tenant threatened legal action",
            "manager_alert": "ALERT",
            "tenant_draft": draft,
            "status": "pending_review",
            "hitl_required": True,
        },
    }


def _vendor_no_vendor() -> dict:
    return {
        "tool": "vendor_agent",
        "result": {
            "output": {"status": "ticket_created", "vendor_status": "no_vendor"},
            "routes": ["vendor_done"],
        },
    }


# --- ordering ----------------------------------------------------------------


def test_orders_escalation_first_knowledge_last() -> None:
    # Deliberately scrambled input order; synthesis must re-order deterministically.
    result = synthesize([_knowledge_answered(), _maintenance(), _escalation()])
    reply = result.reply
    pos_esc = reply.index("escalated")
    pos_ticket = reply.index("TKT-ABC12345")
    pos_know = reply.index("Pets are allowed")
    assert pos_esc < pos_ticket < pos_know


def test_emergency_maintenance_sorts_before_fyi_answer() -> None:
    result = synthesize([_knowledge_answered(), _maintenance(priority="P1")])
    assert result.reply.index("TKT-ABC12345") < result.reply.index("Pets are allowed")


# --- provenance --------------------------------------------------------------


def test_preserves_ticket_code_and_citation() -> None:
    result = synthesize([_maintenance(), _knowledge_answered()])
    assert "TKT-ABC12345" in result.reply
    assert "[1]" in result.reply  # inline citation marker survives
    assert "Pet Policy" in result.reply  # Sources list maps [n] -> document


def test_provenance_tokens_collects_codes_and_citations() -> None:
    tokens = _provenance_tokens([_maintenance(), _knowledge_answered()])
    assert "TKT-ABC12345" in tokens
    assert "[1]" in tokens


def test_parts_trail_covers_every_subresult_and_is_json_serializable() -> None:
    subs = [_maintenance(), _knowledge_answered(), _escalation()]
    result = synthesize(subs)
    # Every sub-result is represented in the provenance trail — nothing silently
    # dropped. (Order follows the reply, not the input, so compare as a set.)
    assert len(result.parts) == 3
    assert {p["tool"] for p in result.parts} == {
        "maintenance_agent",
        "knowledge_agent",
        "escalation_agent",
    }
    json.dumps(result.parts)  # provenance survives the Cosmos checkpointer


# --- held-draft wording (legal-gate invariant) -------------------------------


def test_escalation_reflects_held_state_never_sent() -> None:
    result = synthesize([_maintenance(), _escalation(legal_risk=True)])
    assert result.held_for_review is True
    assert "held for manager review" in result.reply.lower()
    # The held empathetic draft is NOT leaked into the immediate reply.
    assert "SECRET_HELD_DRAFT_TEXT" not in result.reply
    # Nothing implies the response was already sent.
    assert "has been sent" not in result.reply.lower()


def test_non_legal_escalation_still_held() -> None:
    result = synthesize([_escalation(legal_risk=False)])
    assert result.held_for_review is True


def test_no_escalation_is_not_held() -> None:
    result = synthesize([_maintenance(), _knowledge_answered()])
    assert result.held_for_review is False


# --- partial failure ---------------------------------------------------------


def test_refused_answer_is_unresolved_but_ticket_still_delivered() -> None:
    result = synthesize([_maintenance(), _knowledge_refused()])
    assert "TKT-ABC12345" in result.reply  # success still delivered
    assert any("policy question" in u for u in result.unresolved)
    assert "still working on" in result.reply.lower()  # unresolved noted, not dropped


def test_no_vendor_surfaced_as_unresolved() -> None:
    result = synthesize([_maintenance(), _vendor_no_vendor()])
    assert any("contractor" in u for u in result.unresolved)


def test_empty_subresults_yields_safe_default() -> None:
    result = synthesize([])
    assert isinstance(result, SynthesisResult)
    assert result.reply  # non-empty, safe fallback
    assert result.held_for_review is False


# --- selector ----------------------------------------------------------------


def test_get_synthesizer_template_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_synthesizer(), TemplateSynthesizer)


def test_get_synthesizer_treats_placeholder_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE-ME")
    assert isinstance(get_synthesizer(), TemplateSynthesizer)


def test_get_synthesizer_llm_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Constructing the selector doesn't touch the network (langchain is lazy-
    # imported only inside _weave), so this needs no key/network.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")
    assert isinstance(get_synthesizer(), LLMSynthesizer)


def test_template_synthesizer_matches_pure_function() -> None:
    subs = [_maintenance(), _knowledge_answered(), _escalation()]
    assert TemplateSynthesizer().synthesize(subs).reply == synthesize(subs).reply
