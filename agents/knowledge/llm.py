"""Chat model for grounded answer generation (CM-33).

Jira: CM-33  | Epic: CM-Epic 6  | Phase 1

Mirrors CM-30's ``get_triage_classifier()`` env-driven seam: production sets
``OPENAI_API_KEY`` (sourced from Key Vault) and gets the real GPT-4o-mini
:class:`LLMChatModel`; tests + the credential-free demo get a deterministic
:class:`StubChatModel` so CI runs offline and the eval thresholds are
reproducible.

The model is asked for a strict structured output (:class:`RagModelOutput`):
it must answer ONLY from the numbered context blocks, cite the blocks it used,
and set ``can_answer=False`` when the answer isn't present — the hallucination
control the AC requires.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agents.chat import build_chat_model, llm_configured
from agents.knowledge.models import KnowledgeDecision
from agents.knowledge.retrieval import significant_terms

#: AC: GPT-4o-mini for speed/cost (same model the Triage agent uses).
DEFAULT_KNOWLEDGE_MODEL = "gpt-4o-mini"

# GPT-4o-mini per-token USD rates (same table as CM-30 triage + the CM-25
# workbook). A RAG call carries a larger context than triage, so we bill a
# higher flat estimate so the CM-26 cost cap stays meaningful.
_PROMPT_PER_TOKEN = 0.00000015
_COMPLETION_PER_TOKEN = 0.00000060
LLM_ANSWER_COST_ESTIMATE_USD = (
    1500 * _PROMPT_PER_TOKEN + 200 * _COMPLETION_PER_TOKEN
)

#: System prompt enforcing grounded, citation-bearing answers. ``{context}``
#: is filled with the numbered ``[n]`` retrieved chunks per request.
KNOWLEDGE_SYSTEM_PROMPT = """\
You are the knowledge assistant for a condominium management platform. Answer \
the tenant's question USING ONLY the numbered context passages below. Each \
passage is prefixed with a bracketed number like [1].

Rules:
- Use ONLY facts stated in the context. Do NOT use outside knowledge or guess.
- Answer the question AS ASKED. For yes/no, permission, or conditional questions \
("can X do Y?", "is X allowed?", "can X without Y?"), give the direct verdict the \
context supports, including the condition — e.g. if the context says a tenant may \
book only with the owner's written approval, answer "No — a tenant needs the \
owner's approval (NOC) to book." (CM-98)
- The context "supports" an answer whenever its stated rules let you resolve the \
question, not only when it restates the question verbatim. Do NOT refuse merely \
because the phrasing is negative or conditional.
- Set can_answer=false ONLY when the context genuinely does not address the topic \
— then leave answer empty and do not fabricate.
- Report figures only with their stated meaning. Do NOT invent status, progress, \
or completion percentages, and do NOT repurpose numbers from tables or fee \
schedules as anything other than what the passage says. (CM-98)
- When you can answer, write a concise, direct answer and list the passage \
numbers you relied on in used_chunks (e.g. [2] -> 2).

Context passages:
{context}
"""


class RagModelOutput(BaseModel):
    """Structured output the chat model is forced to emit."""

    can_answer: bool
    answer: str = ""
    #: 1-based indices of the context passages the answer relied on.
    used_chunks: list[int] = Field(default_factory=list)


class ChatModel(Protocol):
    """Grounded-answer generator over numbered context blocks."""

    #: Estimated USD billed per :meth:`answer` call (0.0 for the offline stub).
    cost_per_call_usd: float

    def answer(self, question: str, context_blocks: list[str]) -> RagModelOutput:
        """Answer ``question`` strictly from ``context_blocks`` (``"[n] text"``)."""
        ...


class LLMChatModel:
    """Real GPT-4o-mini answerer with Pydantic-validated structured output."""

    cost_per_call_usd: float = LLM_ANSWER_COST_ESTIMATE_USD

    def __init__(
        self, model: str = DEFAULT_KNOWLEDGE_MODEL, *, temperature: float = 0.0
    ) -> None:
        # CM-79: shared factory → Azure OpenAI in prod / OpenAI direct locally.
        self._llm: Any = build_chat_model(
            model, temperature=temperature
        ).with_structured_output(RagModelOutput)

    def answer(self, question: str, context_blocks: list[str]) -> RagModelOutput:
        from langchain_core.messages import (  # noqa: PLC0415  (lazy by design)
            HumanMessage,
            SystemMessage,
        )

        context = "\n\n".join(context_blocks) if context_blocks else "(no context)"
        messages = [
            SystemMessage(content=KNOWLEDGE_SYSTEM_PROMPT.format(context=context)),
            HumanMessage(content=question),
        ]
        result = self._llm.invoke(messages)
        assert isinstance(result, RagModelOutput)
        return result


class StubChatModel:
    """Deterministic offline answerer — the no-credentials / CI path.

    Picks the context block with the most question-term overlap. If nothing
    overlaps, it refuses (``can_answer=False``) — so the eval's hallucination
    metric is exercised deterministically without a live model.
    """

    cost_per_call_usd: float = 0.0

    def answer(self, question: str, context_blocks: list[str]) -> RagModelOutput:
        terms = significant_terms(question)
        best_idx = -1
        best_overlap = 0
        for i, block in enumerate(context_blocks):
            lowered = block.lower()
            overlap = sum(1 for t in terms if t in lowered)
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i
        if best_idx < 0 or best_overlap == 0:
            return RagModelOutput(can_answer=False)
        return RagModelOutput(
            can_answer=True,
            answer=_first_sentence(context_blocks[best_idx]),
            used_chunks=[best_idx + 1],
        )


def _first_sentence(block: str) -> str:
    """First sentence of a ``"[n] text"`` context block, label stripped."""
    text = re.sub(r"^\s*\[\d+\]\s*", "", block).strip()
    head = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return head.strip()


def get_chat_model() -> ChatModel:
    """Env-driven selector — real LLM when one is configured (CM-79), else stub.

    Same convention as CM-30's ``get_triage_classifier`` / CM-28's
    ``get_checkpointer``; provider detection (Azure OpenAI or OpenAI direct,
    ``REPLACE-ME`` counts as unset) lives in :func:`agents.chat.llm_configured`.
    """
    if llm_configured():
        return LLMChatModel()
    return StubChatModel()


# --- CM-84: iterative reasoning-loop decision policy -------------------------
#
# The decision model is the *policy* the CM-83 loop scaffold left as a seam. On
# each iteration it sees the question + the passages accumulated so far and picks
# the next action. A small prompt + a tiny structured output, billed per loop
# step so the CM-26 cost cap stays honest across hops.

LLM_DECISION_COST_ESTIMATE_USD = 800 * _PROMPT_PER_TOKEN + 60 * _COMPLETION_PER_TOKEN

#: Decision prompt. ``{context}`` is the numbered passages gathered so far;
#: ``{step}`` / ``{max_steps}`` let the model spend its remaining budget wisely.
KNOWLEDGE_DECISION_PROMPT = """\
You are the planner for a condominium knowledge assistant. Decide the NEXT step \
for answering the tenant's question, given only the passages gathered so far \
(numbered [1], [2], …). This is step {step} of at most {max_steps}.

Choose exactly one action:
- answer: the passages are sufficient to answer accurately — stop and answer.
- reformulate: the passages are close but the query missed; propose a better \
query (synonyms, simpler phrasing) in `query`.
- search_more: the question needs an additional fact not yet retrieved; propose \
a focused follow-up sub-question in `query`.
- give_up: the corpus clearly will not contain this; stop and refuse.

Rules:
- Prefer `answer` as soon as the evidence is sufficient; don't over-search.
- For reformulate/search_more, `query` MUST differ from earlier queries.
- As the step budget runs low, lean toward `answer` or `give_up`.

Passages gathered so far:
{context}
"""


class DecisionModel(Protocol):
    """Chooses the loop's next action over the accumulated passages."""

    #: Estimated USD billed per :meth:`decide` call (0.0 for the offline stub).
    cost_per_call_usd: float

    def decide(
        self, question: str, context_blocks: list[str], *, step: int, max_steps: int
    ) -> KnowledgeDecision:
        """Pick the next :class:`KnowledgeDecision` for ``question``."""
        ...


class LLMDecisionModel:
    """Real GPT-4o-mini decision policy with Pydantic-validated structured output."""

    cost_per_call_usd: float = LLM_DECISION_COST_ESTIMATE_USD

    def __init__(
        self, model: str = DEFAULT_KNOWLEDGE_MODEL, *, temperature: float = 0.0
    ) -> None:
        # CM-79: shared factory → Azure OpenAI in prod / OpenAI direct locally.
        self._llm: Any = build_chat_model(
            model, temperature=temperature
        ).with_structured_output(KnowledgeDecision)

    def decide(
        self, question: str, context_blocks: list[str], *, step: int, max_steps: int
    ) -> KnowledgeDecision:
        from langchain_core.messages import (  # noqa: PLC0415  (lazy by design)
            HumanMessage,
            SystemMessage,
        )

        context = "\n\n".join(context_blocks) if context_blocks else "(no passages retrieved yet)"
        messages = [
            SystemMessage(
                content=KNOWLEDGE_DECISION_PROMPT.format(
                    context=context, step=step + 1, max_steps=max_steps
                )
            ),
            HumanMessage(content=question),
        ]
        result = self._llm.invoke(messages)
        assert isinstance(result, KnowledgeDecision)
        return result


class StubDecisionModel:
    """Deterministic offline policy — a fixed 2-hop trajectory.

    Step 0 → ``reformulate`` with a query derived from the question's significant
    terms, so the reformulate → retrieve → accumulate path is exercised with no
    key; step ≥ 1 → ``answer``. The stub answerer (:class:`StubChatModel`) still
    decides grounding vs refusal inside ``answer_question``, so a no-evidence run
    still refuses. Never returns ``give_up`` (kept simple + deterministic).
    """

    cost_per_call_usd: float = 0.0

    def decide(
        self, question: str, context_blocks: list[str], *, step: int, max_steps: int
    ) -> KnowledgeDecision:
        if step == 0 and max_steps > 1:
            terms = significant_terms(question)
            follow_up = " ".join(terms) if terms else question
            return KnowledgeDecision(
                action="reformulate", query=follow_up, rationale="stub hop-0 reformulation"
            )
        return KnowledgeDecision(action="answer", rationale="stub hop-1 answer")


def get_decision_model() -> DecisionModel:
    """Env-driven selector — real LLM policy when one is configured, else stub.

    Mirrors :func:`get_chat_model`; provider detection lives in
    :func:`agents.chat.llm_configured` (CM-79).
    """
    if llm_configured():
        return LLMDecisionModel()
    return StubDecisionModel()
