"""Grounded answer assembly for the Knowledge Agent (CM-33).

Jira: CM-33  | Epic: CM-Epic 6  | Phase 1

Takes the RRF-ranked :class:`~agents.knowledge.models.RetrievedChunk`s and a
:class:`~agents.knowledge.llm.ChatModel`, produces a
:class:`~agents.knowledge.models.KnowledgeAnswer` with inline citations and a
confidence score, and **refuses** (handing off to Maintenance) when confidence
falls below :data:`~agents.knowledge.models.CONFIDENCE_THRESHOLD` or the model
can't ground an answer in the retrieved context.

Confidence is driven by the top vector cosine similarity — explainable and
tunable — rather than a model self-report.
"""

from __future__ import annotations

from agents.knowledge.llm import ChatModel
from agents.knowledge.models import (
    CONFIDENCE_THRESHOLD,
    Citation,
    KnowledgeAnswer,
    RetrievedChunk,
)

#: Tenant-facing text used whenever the agent refuses. Kept generic so it
#: reads sensibly whether the cause was low confidence or an empty corpus.
REFUSAL_TEXT = (
    "I don't have enough information in our policy documents to answer that "
    "confidently, so I've passed it to the building management team to follow up."
)


def answer_question(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    model: ChatModel,
) -> KnowledgeAnswer:
    """Produce a grounded answer or a refusal from the retrieved context."""
    # Nothing retrieved → can't ground anything → refuse (no model call).
    if not retrieved:
        return KnowledgeAnswer(answer=REFUSAL_TEXT, confidence=0.0, refused=True)

    context_blocks = [f"[{i + 1}] {rc.chunk.text}" for i, rc in enumerate(retrieved)]
    out = model.answer(question, context_blocks)

    top_similarity = max((rc.similarity for rc in retrieved), default=0.0)
    confidence = top_similarity if out.can_answer else 0.0
    refused = (not out.can_answer) or confidence < CONFIDENCE_THRESHOLD
    if refused:
        return KnowledgeAnswer(
            answer=REFUSAL_TEXT, confidence=round(confidence, 4), refused=True
        )

    # Map the model's cited passage numbers back to source docs; ignore any
    # out-of-range / duplicate indices the model might emit.
    citations: list[Citation] = []
    seen: set[int] = set()
    for idx in out.used_chunks:
        if 1 <= idx <= len(retrieved) and idx not in seen:
            seen.add(idx)
            rc = retrieved[idx - 1]
            citations.append(
                Citation(index=idx, doc_id=rc.chunk.doc_id, doc_title=rc.chunk.doc_title)
            )
    # A confident answer must cite something — fall back to the top chunk.
    if not citations:
        top = retrieved[0]
        citations.append(
            Citation(index=1, doc_id=top.chunk.doc_id, doc_title=top.chunk.doc_title)
        )

    return KnowledgeAnswer(
        answer=out.answer.strip() or REFUSAL_TEXT,
        citations=citations,
        confidence=round(confidence, 4),
        refused=False,
    )
