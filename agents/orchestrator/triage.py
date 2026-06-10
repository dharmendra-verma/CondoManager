"""Triage Agent — intent / urgency / tone classification + routing.

Jira: CM-30  | Epic: CM-5 (Agent 1 — Enhanced Triage Agent)  | Phase 1

CM-28 shipped a keyword-heuristic ``triage`` *stub* node and documented
that *"CM-30 replaces this with real GPT-4o-mini classification; the spine
stays unchanged."* This module is that replacement's brain — the node body
in :mod:`agents.orchestrator.nodes` is a thin wrapper that calls in here.

Three public concerns:

* :class:`TriageClassification` — the Pydantic-validated structured output
  the LLM is forced to emit (AC #1). Its three classification fields reuse
  the :class:`~agents.orchestrator.state.Intent` /
  :class:`~agents.orchestrator.state.Urgency` /
  :class:`~agents.orchestrator.state.Tone` ``StrEnum``s defined by CM-28, so
  the schema can never drift from ``AgentState``.
* :class:`TriageClassifier` — a ``Protocol`` with two implementations:
  :class:`LLMTriageClassifier` (real GPT-4o-mini) and
  :class:`HeuristicTriageClassifier` (deterministic keyword fallback). The
  env-driven :func:`get_triage_classifier` selector picks between them —
  exactly mirroring CM-28's ``get_checkpointer()`` pattern. This keeps the
  CM-28 invariant true: the test suite and ``python -m
  agents.orchestrator.demo`` run with **no** ``OPENAI_API_KEY``.
* :func:`route_for` — the intent → downstream-node routing matrix (AC #6).
  The four route strings it returns match the conditional-edge keys declared
  in :mod:`agents.orchestrator.graph` exactly.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .state import Intent, Tone, Urgency

#: CM-18 Key Vault seed-script placeholder; treated as if-unset so the app
#: boots cleanly before the secret is populated (same convention as
#: ``agents.observability.langsmith.SECRET_PLACEHOLDER``).
SECRET_PLACEHOLDER: str = "REPLACE-ME"

#: Default model — AC #8 ("GPT-4o-mini for speed/cost").
DEFAULT_TRIAGE_MODEL: str = "gpt-4o-mini"

# GPT-4o-mini per-token USD rates — same table as the CM-25 App Insights
# workbook (infra/bicep/modules/workbook-payload.json). Used to produce a
# rough per-call cost estimate so the CM-26 cost-cap guardrail
# (``COST_CAP_USD = 5.0``) is meaningful for a triage-heavy request.
_GPT_4O_MINI_PROMPT_PER_TOKEN: float = 0.00000015
_GPT_4O_MINI_COMPLETION_PER_TOKEN: float = 0.00000060

# A single triage call is a short system prompt (~500 tokens incl. history)
# plus a small structured-output completion (~60 tokens). We bill a flat
# estimate per LLM call rather than threading live usage metadata through
# ``with_structured_output`` — accurate enough to make the cumulative cap
# meaningful; the CM-25 workbook remains the source of truth for real spend.
LLM_TRIAGE_COST_ESTIMATE_USD: float = (
    500 * _GPT_4O_MINI_PROMPT_PER_TOKEN + 60 * _GPT_4O_MINI_COMPLETION_PER_TOKEN
)


class TriageClassification(BaseModel):
    """Structured output the Triage LLM is forced to emit (AC #1).

    Bound to GPT-4o-mini via ``ChatOpenAI.with_structured_output`` so a
    response that doesn't validate against this schema raises at the model
    boundary rather than silently mis-routing downstream. The three
    classification fields reuse the CM-28 ``StrEnum``s — an out-of-enum
    label (e.g. the model inventing ``"complaint"``) is a validation error.
    """

    intent: Intent
    urgency: Urgency
    tone: Tone
    # Audit trail — one line on *why* this classification was chosen. Not
    # gated by the AC; useful in traces + the eval report.
    rationale: str = Field(
        default="",
        description="One short sentence explaining the classification, for audit.",
    )
    # Confidence aids future thresholding / HITL escalation; not gated by the
    # AC. Defaults to 1.0 so the heuristic classifier (which is certain about
    # its keyword matches) doesn't have to set it.
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    # CM-87 (Track B): multi-intent / ambiguity signal. ``True`` when the message
    # carries more than one actionable request (a repair AND a policy question),
    # or is genuinely ambiguous / multi-hop, so a single specialist can't fully
    # handle it — this is what flips routing to the Coordinator. Defaults
    # ``False`` so the single-intent fast path is the default and older callers
    # (and the eval golden data) don't have to set it.
    multi_intent: bool = Field(
        default=False,
        description=(
            "True only when the message has more than one actionable request or "
            "is genuinely ambiguous/multi-hop; otherwise false."
        ),
    )
    # CM-87: the distinct sub-intents detected when ``multi_intent`` is True
    # (e.g. ``[maintenance, escalation]`` for "fix my heater AND I want a
    # manager"). Empty on the single-intent path. Reuses the ``Intent`` enum so
    # it can never drift from the schema; ``intent`` remains the primary pick.
    sub_intents: list[Intent] = Field(
        default_factory=list,
        description="Distinct sub-intents when multi_intent is true; else empty.",
    )


# --- Prompt ------------------------------------------------------------------

# The label menus are built from the enums so the prompt can never drift from
# the schema. ``Intent.UNKNOWN`` is intentionally excluded from the menu — it's
# a fallback the *code* assigns, not a label we ask the model to pick.
_INTENT_CHOICES = " / ".join(
    e.value for e in Intent if e is not Intent.UNKNOWN
)
_URGENCY_CHOICES = " / ".join(e.value for e in Urgency)
_TONE_CHOICES = " / ".join(e.value for e in Tone)

#: System prompt for the Triage LLM. ``{history}`` is filled per-request with
#: a summary of the tenant's recent tickets (AC #5) so follow-ups are
#: recognised; it renders as a "no prior tickets" line when history is empty.
TRIAGE_SYSTEM_PROMPT: str = f"""\
You are the triage agent for a condominium management platform. Classify the \
tenant's inbound message into exactly one value per dimension.

intent  — {_INTENT_CHOICES}
  - maintenance: a request to fix/repair something physical (leak, broken \
appliance, no heat, pest, etc.).
  - inquiry: a question about policies, amenities, hours, fees, or general \
information.
  - escalation: the tenant is demanding a manager/human, threatening, or \
expressing strong dissatisfaction with prior handling.
  - follow-up: the tenant is chasing the status of an existing request or \
ticket they already opened.

urgency — {_URGENCY_CHOICES}
  - emergency: imminent danger or major damage in progress (flooding, gas \
smell, fire, no heat in freezing weather, security).
  - high: significant problem needing prompt attention but not life-safety.
  - medium: normal maintenance or a question that should be handled in a day \
or two.
  - low: minor, informational, or no time pressure.

tone    — {_TONE_CHOICES}
  - neutral: matter-of-fact.
  - frustrated: mild annoyance or impatience.
  - angry: explicit anger, hostility, or ALL-CAPS venting.
  - urgent: pressing/panicked language regardless of politeness.

multi_intent — true / false
  Set true ONLY when the message contains more than one distinct actionable \
request (e.g. a repair AND a separate policy question), or is genuinely \
ambiguous / multi-hop so a single specialist cannot fully resolve it. When \
true, list the distinct intents in sub_intents (drawn from the intent menu \
above) and still set intent to the single most urgent / primary one. Be \
conservative: one request — even a long, detailed one — is NOT multi_intent, \
and false is the default.

Tenant's recent ticket history:
{{history}}

Use the history to recognise follow-ups (e.g. references to a ticket number \
or "still waiting"). Respond ONLY via the structured schema; do not add prose.\
"""


def format_history(history: list[dict[str, Any]]) -> str:
    """Render recent-ticket history for the prompt (AC #5).

    Empty history renders as an explicit "none on file" line so the model
    isn't left guessing whether the absence is meaningful.
    """
    if not history:
        return "  (no prior tickets on file for this tenant)"
    lines: list[str] = []
    for t in history:
        ticket_id = t.get("ticket_id", "?")
        status = t.get("status", "?")
        summary = t.get("summary", "")
        lines.append(f"  - #{ticket_id} [{status}] {summary}".rstrip())
    return "\n".join(lines)


# --- Classifier protocol + implementations -----------------------------------


@runtime_checkable
class TriageClassifier(Protocol):
    """Classifies one tenant message into a :class:`TriageClassification`.

    Implementations expose :attr:`cost_per_call_usd` so the node can bump
    ``AgentState.cost_so_far`` for the CM-26 cost-cap guardrail without
    knowing which implementation it got.
    """

    #: Estimated USD billed per :meth:`classify` call (0.0 for offline impls).
    cost_per_call_usd: float

    def classify(self, message: str, history: list[dict[str, Any]]) -> TriageClassification:
        """Return the classification for ``message`` given ticket ``history``."""
        ...


class LLMTriageClassifier:
    """Real GPT-4o-mini classifier with Pydantic-validated structured output.

    Lazy-imports ``langchain_openai`` (CM-23 pattern) so workloads that never
    classify — and the offline test suite — don't pay the import cost or need
    an OpenAI key. ``temperature=0`` for reproducible classification.
    """

    cost_per_call_usd: float = LLM_TRIAGE_COST_ESTIMATE_USD

    def __init__(
        self,
        model: str = DEFAULT_TRIAGE_MODEL,
        *,
        temperature: float = 0.0,
    ) -> None:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (lazy by design)

        self._model = model
        self._llm = ChatOpenAI(
            model=model, temperature=temperature
        ).with_structured_output(TriageClassification)

    def classify(self, message: str, history: list[dict[str, Any]]) -> TriageClassification:
        from langchain_core.messages import (  # noqa: PLC0415  (lazy by design)
            HumanMessage,
            SystemMessage,
        )

        messages = [
            SystemMessage(
                content=TRIAGE_SYSTEM_PROMPT.format(history=format_history(history))
            ),
            HumanMessage(content=message),
        ]
        result = self._llm.invoke(messages)
        # ``with_structured_output`` returns the validated model instance.
        # Assert the type so a langchain-openai behavior change surfaces here
        # rather than as a confusing downstream AttributeError.
        assert isinstance(result, TriageClassification)
        return result


class HeuristicTriageClassifier:
    """Deterministic keyword classifier — the offline / no-credentials path.

    Preserves the exact routing of CM-28's ``triage`` stub so the
    hello-world suite and the credential-free demo keep working:

    * ``"human"`` / ``"escalat"``        -> escalation
    * ``"fix"`` / ``"broken"`` / ``"leak"`` -> maintenance
    * everything else                    -> inquiry (-> knowledge route)

    It also makes a best-effort urgency/tone guess from a few obvious cues so
    the populated ``AgentState`` is realistic in offline runs. It will NOT hit
    the AC #7 >90% accuracy bar — that target is for
    :class:`LLMTriageClassifier`, asserted by ``infra/scripts/eval-triage.py``.
    """

    cost_per_call_usd: float = 0.0

    _MAINTENANCE_CUES = ("fix", "broken", "leak", "repair", "no heat", "clog")
    _ESCALATION_CUES = ("human", "escalat", "manager", "unacceptable", "lawyer")
    _EMERGENCY_CUES = ("emergency", "flood", "fire", "gas", "pouring", "burst")
    _FOLLOWUP_CUES = ("follow up", "following up", "ticket #", "any update", "status of")
    # CM-87: question/inquiry cues, used only for multi-intent detection (the
    # primary-intent ladder below still defaults to INQUIRY). Kept narrow so a
    # plain question doesn't look like a second intent on its own.
    #
    # CM-94: broadened beyond the literal word "policy". A policy/amenity
    # question is very often phrased as a *permission* request — "can a tenant
    # book the banquet hall …", "am I allowed …", "do I need …" — which carries
    # none of the original keywords, so a compound "repair AND policy-question"
    # message slipped through to the single-intent maintenance path (the banquet
    # -hall report that prompted this). The permission phrasings below use
    # "can i" / "can a" rather than "can you", so a single repair request phrased
    # as a question ("can you fix my sink?") is NOT mistaken for an inquiry.
    _INQUIRY_CUES = (
        "policy", "rule", "hours", "allowed", "how much", "what time",
        "can i", "can a", "can tenants", "can residents", "may i",
        "am i allowed", "is it allowed", "do i need", "permitted",
        "book the", "reserve",
    )
    # CM-87: conjunction / compound cues. ``multi_intent`` requires one of these
    # PLUS evidence of a second request, so a single detailed request — even one
    # phrased as a question — never trips the slow path. This is the deliberately
    # conservative reading of the AC ("conjunction cues + multiple intent keywords").
    _CONJUNCTION_CUES = (" and ", " also ", "additionally", " plus ", "; ", " as well", " then ")

    def _detect_multi_intent(self, m: str) -> tuple[bool, list[Intent]]:
        """Detect compound / ambiguous messages from keyword cues (CM-87).

        Returns ``(multi_intent, sub_intents)``. ``multi_intent`` is True only
        when a conjunction cue co-occurs with either two distinct intent
        categories or two separate maintenance issues — conservative by design
        so simple messages stay on the single-intent fast path. ``sub_intents``
        lists the distinct categories whose cues fired, in routing priority.
        """
        detected: list[Intent] = []
        if any(cue in m for cue in self._ESCALATION_CUES):
            detected.append(Intent.ESCALATION)
        if any(cue in m for cue in self._FOLLOWUP_CUES):
            detected.append(Intent.FOLLOW_UP)
        maint_hits = sum(cue in m for cue in self._MAINTENANCE_CUES)
        if maint_hits:
            detected.append(Intent.MAINTENANCE)
        if any(cue in m for cue in self._INQUIRY_CUES):
            detected.append(Intent.INQUIRY)

        has_conjunction = any(cue in m for cue in self._CONJUNCTION_CUES)
        multi_intent = has_conjunction and (len(detected) >= 2 or maint_hits >= 2)
        return multi_intent, (detected if multi_intent else [])

    def classify(self, message: str, history: list[dict[str, Any]]) -> TriageClassification:
        m = (message or "").lower()

        if any(cue in m for cue in self._ESCALATION_CUES):
            intent = Intent.ESCALATION
        elif any(cue in m for cue in self._FOLLOWUP_CUES):
            intent = Intent.FOLLOW_UP
        elif any(cue in m for cue in self._MAINTENANCE_CUES):
            intent = Intent.MAINTENANCE
        else:
            intent = Intent.INQUIRY

        multi_intent, sub_intents = self._detect_multi_intent(m)

        if any(cue in m for cue in self._EMERGENCY_CUES):
            urgency = Urgency.EMERGENCY
        elif intent in (Intent.MAINTENANCE, Intent.ESCALATION):
            urgency = Urgency.MEDIUM
        else:
            urgency = Urgency.LOW

        # Crude tone read: ALL-CAPS shouting or anger words -> angry; emergency
        # cues -> urgent; escalation -> frustrated; else neutral.
        stripped = (message or "").strip()
        is_shouting = len(stripped) >= 8 and stripped.upper() == stripped and any(
            c.isalpha() for c in stripped
        )
        if is_shouting or "unacceptable" in m:
            tone = Tone.ANGRY
        elif urgency is Urgency.EMERGENCY:
            tone = Tone.URGENT
        elif intent is Intent.ESCALATION:
            tone = Tone.FRUSTRATED
        else:
            tone = Tone.NEUTRAL

        return TriageClassification(
            intent=intent,
            urgency=urgency,
            tone=tone,
            multi_intent=multi_intent,
            sub_intents=sub_intents,
            rationale="heuristic keyword match (no LLM)",
        )


def get_triage_classifier() -> TriageClassifier:
    """Env-driven selector — LLM when an OpenAI key is configured, else heuristic.

    Mirrors CM-28's ``get_checkpointer()``: production wiring sets
    ``OPENAI_API_KEY`` (sourced from Key Vault) and gets the real classifier;
    tests and the local demo leave it unset and get the deterministic
    heuristic, so they run with no credentials and no network. The
    ``REPLACE-ME`` placeholder is treated as unset (CM-18 convention).
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and key != SECRET_PLACEHOLDER:
        return LLMTriageClassifier()
    return HeuristicTriageClassifier()


# --- Routing (AC #6) ---------------------------------------------------------

#: CM-87 (Track B): the node name triage routes to when the multi-intent /
#: ambiguity signal fires. Matches the conditional-edge key + node name in
#: ``agents.orchestrator.graph`` and the ``coordinator`` stub node in
#: ``agents.orchestrator.nodes``.
ROUTE_COORDINATOR: str = "coordinator"

# Intent -> downstream node name. The values MUST match the conditional-edge
# keys in ``agents.orchestrator.graph.build_graph`` ("knowledge" /
# "maintenance" / "escalation"). ``follow-up`` routes to maintenance (the
# agent that owns ticket lifecycle, CM-31); ``unknown`` falls back to
# knowledge, which can ask a clarifying question.
_ROUTE_BY_INTENT: dict[Intent, str] = {
    Intent.MAINTENANCE: "maintenance",
    Intent.INQUIRY: "knowledge",
    Intent.ESCALATION: "escalation",
    Intent.FOLLOW_UP: "maintenance",
    Intent.UNKNOWN: "knowledge",
}


def route_for_intent(intent: Intent) -> str:
    """Return the single-intent downstream node name for a bare ``Intent``.

    The primary routing matrix, factored out of :func:`route_for` so the
    CM-87 ``coordinator`` stub can reuse the *exact* single-route behaviour
    when it falls back to today's path (pick the primary intent → its
    specialist), guaranteeing no regression vs the pre-Coordinator spine.
    """
    return _ROUTE_BY_INTENT[intent]


def route_for(classification: TriageClassification) -> str:
    """Return the downstream node name for a classification (AC #6).

    v1 routes purely by ``intent``; ``urgency`` and ``tone`` are persisted on
    ``AgentState`` for downstream prioritisation. A tone/urgency → escalation
    override is deferred to CM-32 (Escalation Agent) to avoid premature
    cross-agent coupling.

    This is the **single-intent** matrix only; the multi-intent / Coordinator
    decision is :func:`needs_coordinator`, applied by the triage node before
    this is consulted.
    """
    return route_for_intent(classification.intent)


def needs_coordinator(classification: TriageClassification) -> bool:
    """True when triage should hand off to the Coordinator instead of one
    specialist (CM-87, AC #2).

    Fires on the multi-intent / ambiguity signal — a compound request, or a
    genuinely ambiguous / multi-hop message (including ``intent=unknown`` with a
    multi-hop signal) that a single specialist can't fully handle; the
    classifier captures all of these in ``multi_intent``. The single-intent
    fast path (the default, ``multi_intent`` False) is left untouched.
    """
    return classification.multi_intent
