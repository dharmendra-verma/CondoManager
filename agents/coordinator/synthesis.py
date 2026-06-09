"""Cross-sub-task response synthesis for the Coordinator (CM-89, Track B).

Jira: CM-89  | Epic: Track B (Coordinator / multi-intent orchestration)  | Phase 1

After the CM-88 plan-execute loop has called several specialist tools, the tenant
must receive **one coherent reply** — not three disjoint agent outputs. This
module weaves the accumulated ``state.sub_results`` (the ordered
``{"tool", "result"}`` records, CM-88) into a single, well-ordered message.

Design (mirrors the planner / chat-model seams):

* :func:`synthesize` is a **pure function** over the sub-result list — no I/O, no
  state mutation — so it is unit-testable independently of the loop and the graph.
* Ordering is deterministic: held escalations and emergencies first, action
  confirmations next, FYI policy answers last, with a closing note for anything
  unresolved (a failed tool / ``no_vendor``). Nothing is silently dropped — every
  sub-result is represented either in the reply or the unresolved note, and all
  appear in :attr:`SynthesisResult.parts` for provenance.
* **Provenance preserved:** ticket confirmation codes (``TKT-…``), inline policy
  citations ``[n]`` (+ a Sources list), and the "held for manager review" notice
  all survive into the final message.
* **Legal-gate invariant (CM-32):** an escalation sub-result is reflected as
  *held* — the reply acknowledges it is under management review and the held
  empathetic draft is **never** emitted as if sent. :attr:`held_for_review` flags
  it for the caller (the ``coordinator`` node routes to ``hitl_review``).
* :func:`get_synthesizer` selects the implementation on ``OPENAI_API_KEY`` exactly
  like ``get_planner`` / ``get_chat_model`` — a deterministic
  :class:`TemplateSynthesizer` offline (CI green with no key) and an LLM weaver
  :class:`LLMSynthesizer` behind the key. The LLM path validates its output's
  codes/citations against the sub-results and **falls back to the template** if it
  drifts, so it can never invent a ``TKT-`` code or ``[n]`` citation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

#: CM-18 Key Vault seed placeholder; treated as if-unset (same convention as the
#: planner / triage / knowledge selectors).
SECRET_PLACEHOLDER: str = "REPLACE-ME"

#: Default model for the LLM weaver — GPT-4o-mini, as elsewhere in the platform.
DEFAULT_SYNTHESIS_MODEL: str = "gpt-4o-mini"

# Ordering tiers (float so emergencies can slot just after held escalations
# without a separate branch). Lower sorts first; ties keep trajectory order.
_TIER_HELD: float = 0.0  # escalation held for manager review
_TIER_EMERGENCY: float = 0.5  # P1 maintenance (active damage / safety)
_TIER_ACTION: float = 1.0  # ticket confirmations, vendor dispatch
_TIER_INFO: float = 2.0  # policy / inquiry answers (FYI)
_TIER_UNRESOLVED: float = 3.0  # failed / no_vendor / refused

#: Matches a tenant-facing ticket code, e.g. ``TKT-3F8A2C9D``.
_TKT_RE: re.Pattern[str] = re.compile(r"\bTKT-[0-9A-Z]{6,}\b")
#: Matches an inline ``[n]`` citation marker.
_CITE_RE: re.Pattern[str] = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class SynthesisResult:
    """One coherent tenant reply woven from the Coordinator's sub-results.

    ``reply`` is the tenant-facing message. ``parts`` is the per-sub-result
    provenance trail (tool, status, the codes/citations it contributed) so
    nothing is silently dropped. ``held_for_review`` is True when any sub-result
    is held behind the HITL gate (escalation) — the caller must route to
    ``hitl_review`` and must not treat the reply as sent. ``unresolved`` lists the
    tenant-visible sub-tasks that could not be completed (failed tool /
    ``no_vendor`` / refusal).
    """

    reply: str
    parts: list[dict[str, Any]] = field(default_factory=list)
    held_for_review: bool = False
    unresolved: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Segment:
    """The classified contribution of a single sub-result."""

    tier: float
    tool: str
    status: str
    text: str | None  # tenant-facing prose for the reply (None = note-only)
    unresolved: str | None  # tenant-visible "couldn't do X" note, if any
    held: bool  # held behind the HITL gate (escalation)
    codes: list[str]  # provenance tokens contributed (TKT-…, [n])


def _result_status(result: dict[str, Any]) -> str:
    """The sub-result's own status, whether top-level or nested under ``output``."""
    raw = result.get("output")
    nested = raw if isinstance(raw, dict) else {}
    return str(result.get("status") or nested.get("status") or "unknown")


def _classify(obs: dict[str, Any]) -> _Segment:
    """Map one ``{"tool", "result"}`` sub-result to a :class:`_Segment` (pure)."""
    tool = str(obs.get("tool", "unknown"))
    result = obs.get("result") if isinstance(obs.get("result"), dict) else {}
    result = result or {}

    if tool == "maintenance_agent":
        return _classify_maintenance(result)
    if tool == "vendor_agent":
        return _classify_vendor(result)
    if tool == "knowledge_agent":
        return _classify_knowledge(result)
    if tool == "escalation_agent" and "record_id" in result:
        return _classify_escalation(result)
    # Unknown / skipped (e.g. vendor with no prior ticket) — internal no-op, kept
    # in provenance but contributes nothing tenant-facing.
    return _Segment(
        tier=_TIER_UNRESOLVED,
        tool=tool,
        status=_result_status(result),
        text=None,
        unresolved=None,
        held=False,
        codes=[],
    )


def _classify_maintenance(result: dict[str, Any]) -> _Segment:
    out = result.get("output") if isinstance(result.get("output"), dict) else {}
    out = out or {}
    status = str(out.get("status", "unknown"))
    if status in ("ticket_created", "duplicate"):
        ticket_id = str(out.get("ticket_id", "")).strip()
        confirmation = str(out.get("confirmation") or "").strip()
        text = confirmation or (
            f"We've logged your maintenance request as ticket {ticket_id}."
            if ticket_id
            else "We've logged your maintenance request."
        )
        # Emergency (P1) maintenance sorts up with held items.
        tier = _TIER_EMERGENCY if str(out.get("priority")) == "P1" else _TIER_ACTION
        codes = [ticket_id] if ticket_id else []
        return _Segment(
            tier=tier,
            tool="maintenance_agent",
            status=status,
            text=text,
            unresolved=None,
            held=False,
            codes=codes,
        )
    return _Segment(
        tier=_TIER_UNRESOLVED,
        tool="maintenance_agent",
        status=status,
        text=None,
        unresolved="log your maintenance request",
        held=False,
        codes=[],
    )


def _classify_vendor(result: dict[str, Any]) -> _Segment:
    out = result.get("output") if isinstance(result.get("output"), dict) else {}
    out = out or {}
    vendor_status = str(out.get("vendor_status", "")) or "unknown"
    if vendor_status == "auto_dispatched":
        return _Segment(
            tier=_TIER_ACTION,
            tool="vendor_agent",
            status=vendor_status,
            text="A contractor has been dispatched and will be in touch to schedule the work.",
            unresolved=None,
            held=False,
            codes=[],
        )
    if vendor_status == "pending_approval":
        return _Segment(
            tier=_TIER_ACTION,
            tool="vendor_agent",
            status=vendor_status,
            text="A contractor has been identified; the assignment is awaiting manager approval.",
            unresolved=None,
            held=False,
            codes=[],
        )
    if vendor_status == "no_vendor":
        return _Segment(
            tier=_TIER_UNRESOLVED,
            tool="vendor_agent",
            status=vendor_status,
            text=None,
            unresolved="assign a contractor (none available yet)",
            held=False,
            codes=[],
        )
    # Pass-through (no dispatch decision) — nothing tenant-facing to add.
    return _Segment(
        tier=_TIER_UNRESOLVED,
        tool="vendor_agent",
        status=vendor_status,
        text=None,
        unresolved=None,
        held=False,
        codes=[],
    )


def _classify_knowledge(result: dict[str, Any]) -> _Segment:
    refused = bool(result.get("refused")) or _result_status(result) == "refused"
    if refused:
        return _Segment(
            tier=_TIER_UNRESOLVED,
            tool="knowledge_agent",
            status="refused",
            text=None,
            unresolved="answer your policy question (no grounded answer found)",
            held=False,
            codes=[],
        )
    answer = str(result.get("answer") or "").strip()
    citations = result.get("citations") if isinstance(result.get("citations"), list) else []
    text = answer
    codes = list(dict.fromkeys(_CITE_RE.findall(answer)))  # markers already in the answer
    if citations:
        # Append an explicit Sources list so the [n] -> document mapping survives
        # even if the answer omitted inline markers, and add any missing markers.
        sources: list[str] = []
        for c in citations:
            if not isinstance(c, dict):
                continue
            idx = c.get("index")
            title = str(c.get("doc_title") or c.get("doc_id") or "source")
            if idx is None:
                continue
            marker = f"[{idx}]"
            sources.append(f"{marker} {title}")
            if marker not in codes:
                codes.append(marker)
        if sources:
            text = f"{answer}\n\nSources: " + "; ".join(sources)
    return _Segment(
        tier=_TIER_INFO,
        tool="knowledge_agent",
        status="answered",
        text=text or "Here is what our policies say.",
        unresolved=None,
        held=False,
        codes=codes,
    )


def _classify_escalation(result: dict[str, Any]) -> _Segment:
    # Legal-gate invariant: reflect the held state; never emit the held draft as
    # if sent. The reply acknowledges management review only.
    legal = bool(result.get("legal_risk"))
    note = (
        "We've escalated your concern to our management team for review"
        + (" given its sensitivity" if legal else "")
        + "; a manager will follow up with you directly. "
        "(This response is held for manager review and has not been auto-sent.)"
    )
    return _Segment(
        tier=_TIER_HELD,
        tool="escalation_agent",
        status="pending_review",
        text=note,
        unresolved=None,
        held=True,
        codes=[],
    )


def _ordered_segments(sub_results: list[dict[str, Any]]) -> list[_Segment]:
    """Classify + deterministically order the sub-results (stable within a tier)."""
    indexed = [(i, _classify(obs)) for i, obs in enumerate(sub_results)]
    indexed.sort(key=lambda pair: (pair[1].tier, pair[0]))
    return [seg for _, seg in indexed]


def _compose(segments: list[_Segment]) -> SynthesisResult:
    """Weave ordered segments into the final reply + provenance (pure)."""
    body: list[str] = []
    unresolved: list[str] = []
    parts: list[dict[str, Any]] = []
    held = False

    for seg in segments:
        parts.append({"tool": seg.tool, "status": seg.status, "codes": seg.codes})
        if seg.held:
            held = True
        if seg.text:
            body.append(seg.text)
        if seg.unresolved:
            unresolved.append(seg.unresolved)

    if unresolved:
        body.append(
            "We're still working on the following and will update you shortly: "
            + "; ".join(unresolved)
            + "."
        )

    reply = "\n\n".join(body).strip()
    if not reply:
        reply = (
            "Thanks for your message — we're looking into your request "
            "and will follow up shortly."
        )
    return SynthesisResult(reply=reply, parts=parts, held_for_review=held, unresolved=unresolved)


def synthesize(sub_results: list[dict[str, Any]]) -> SynthesisResult:
    """Pure, deterministic synthesis over the Coordinator's sub-results.

    The offline/template path and the LLM fallback both route through here. Safe
    to call with no credentials and no network — the workhorse of CI.
    """
    return _compose(_ordered_segments(sub_results))


def _provenance_tokens(sub_results: list[dict[str, Any]]) -> set[str]:
    """Every TKT code + ``[n]`` citation that legitimately appears in the inputs."""
    tokens: set[str] = set()
    for seg in (_classify(obs) for obs in sub_results):
        tokens.update(seg.codes)
        if seg.text:
            tokens.update(_TKT_RE.findall(seg.text))
            tokens.update(_CITE_RE.findall(seg.text))
    return tokens


class Synthesizer(Protocol):
    """Weaves the Coordinator's sub-results into one tenant reply."""

    def synthesize(self, sub_results: list[dict[str, Any]]) -> SynthesisResult:
        """Return the single coherent reply + provenance for ``sub_results``."""
        ...


class TemplateSynthesizer:
    """Deterministic offline synthesizer — the no-credentials / CI path."""

    def synthesize(self, sub_results: list[dict[str, Any]]) -> SynthesisResult:
        return synthesize(sub_results)


class LLMSynthesizer:
    """GPT-4o-mini weaver, constrained to summarize only the provided results.

    Lazy-imports ``langchain_openai`` (the CM-23 pattern) so the offline suite
    never pays the import cost or needs a key. The LLM only re-orders/links the
    already-written segments; its output is validated — every ``TKT-`` code and
    ``[n]`` citation in the reply must come from the sub-results, and the held
    state must be honoured — else it **falls back to the deterministic template**.
    """

    def __init__(self, model: str = DEFAULT_SYNTHESIS_MODEL) -> None:
        self._model = model

    def synthesize(self, sub_results: list[dict[str, Any]]) -> SynthesisResult:
        baseline = synthesize(sub_results)
        try:
            woven = self._weave(sub_results, baseline)
        except Exception:  # noqa: BLE001 — never let synthesis break the node
            return baseline
        allowed = _provenance_tokens(sub_results)
        used = set(_TKT_RE.findall(woven)) | set(_CITE_RE.findall(woven))
        # Reject hallucinated codes/citations, or a dropped held-review notice.
        if not used <= allowed:
            return baseline
        if baseline.held_for_review and "held for manager review" not in woven.lower():
            return baseline
        return SynthesisResult(
            reply=woven.strip(),
            parts=baseline.parts,
            held_for_review=baseline.held_for_review,
            unresolved=baseline.unresolved,
        )

    def _weave(self, sub_results: list[dict[str, Any]], baseline: SynthesisResult) -> str:
        from langchain_core.messages import (  # noqa: PLC0415  (lazy by design)
            HumanMessage,
            SystemMessage,
        )
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (lazy by design)

        system = SystemMessage(
            content=(
                "You are the coordinator for a condo-management platform. Combine the "
                "pre-written sections below into ONE coherent reply to the tenant. "
                "Rules: address every section; emergencies and held items first, FYI "
                "answers last; preserve every TKT-XXXX code and [n] citation EXACTLY; "
                "do NOT invent codes, citations, or facts; if a section says it is held "
                "for manager review, keep that wording and do not imply it was sent."
            )
        )
        human = HumanMessage(content="\n\n---\n\n".join(s for s in baseline_sections(baseline)))
        llm: Any = ChatOpenAI(model=self._model, temperature=0.0)
        return str(llm.invoke([system, human]).content)


def baseline_sections(baseline: SynthesisResult) -> list[str]:
    """The reply split back into sections for the LLM weaver's input."""
    return [seg for seg in baseline.reply.split("\n\n") if seg.strip()]


def get_synthesizer() -> Synthesizer:
    """Env-driven selector — LLM weaver when ``OPENAI_API_KEY`` set, else template.

    Same convention as ``get_planner`` / ``get_chat_model``; the ``REPLACE-ME``
    placeholder counts as unset.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and key != SECRET_PLACEHOLDER:
        return LLMSynthesizer()
    return TemplateSynthesizer()
