"""Pydantic models + shared constants for the Drive → Cosmos sync (CM-34).

Jira: CM-34  | Epic: CM-Epic 9 (Knowledge ingestion)  | Phase 1

These models are the data contract between the four collaborators in the
sync pipeline (Drive client, chunker/embedder, Cosmos store, orchestrator).
They are deliberately JSON-clean so :class:`VectorChunk` and
:class:`SyncState` can be ``upsert_item``-ed into Cosmos without further
massaging — Cosmos rejects raw bytes / non-JSON objects.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: Cosmos DB database name from CM-17.
DATABASE_NAME = "condomanager"
#: RAG vector container from CM-17 (DiskANN index on /embedding).
VECTOR_CONTAINER = "policies-vector"
#: Sync-state container added by CM-34's cosmos.bicep change (partition /source).
STATE_CONTAINER = "knowledge_sync"
#: CM-18 Key Vault seed placeholder — treated as if-unset everywhere.
SECRET_PLACEHOLDER = "REPLACE-ME"

#: CM-33: knowledge-agent confidence floor. An answer scoring below this is
#: refused and the request is handed off to the Maintenance agent (AC).
#: CM-78: lowered 0.5->0.40 (was 0.6 pre-CM-74). text-embedding-3-small relevant
#: matches sit ~0.43-0.46; the LLM can_answer guard still blocks hallucination.
CONFIDENCE_THRESHOLD = 0.40

#: Per-document outcome of one sync pass.
DocStatus = Literal["indexed", "skipped", "removed", "failed"]


def chunk_id(tenant_id: str, doc_id: str, index: int) -> str:
    """Deterministic Cosmos ``id`` for one chunk.

    The id is a pure function of (tenant, doc, chunk index) — the keystone
    of CM-34's idempotency AC. Re-running the sync on unchanged content
    upserts the *same* ids, so retries never create duplicate chunks.
    """
    return f"{tenant_id}:{doc_id}:{index}"


class DriveChange(BaseModel):
    """A single file surfaced by the Drive Changes API (or folder listing)."""

    file_id: str
    title: str = ""
    mime_type: str = ""
    removed: bool = False


class VectorChunk(BaseModel):
    """One embedded chunk, shaped exactly as it lands in ``policies-vector``."""

    id: str
    # camelCase to match the Cosmos partition-key path /tenantId (CM-17).
    tenantId: str  # noqa: N815 - Cosmos field name, not a Python convention
    doc_id: str
    doc_title: str
    chunk_index: int
    text: str
    embedding: list[float]
    content_hash: str
    doc_version: int
    source: str
    ts: str


class DocState(BaseModel):
    """Per-document bookkeeping persisted inside :class:`SyncState`."""

    content_hash: str
    version: int
    title: str = ""


class SyncState(BaseModel):
    """Durable cursor for one sync source.

    Stored as a single Cosmos doc in ``knowledge_sync`` keyed by ``source``
    (which is also the partition key). Holds the Drive ``startPageToken``
    so the next run only pulls changes since the last pass, plus a per-doc
    content-hash map so even Drive-reported changes that didn't alter text
    are skipped before any embedding spend.
    """

    source: str
    page_token: str | None = None
    docs: dict[str, DocState] = Field(default_factory=dict)
    updated_ts: str | None = None

    def to_cosmos(self) -> dict[str, Any]:
        """Serialize to a Cosmos doc — adds the required ``id`` (== source)."""
        data = self.model_dump()
        data["id"] = self.source
        return data

    @classmethod
    def from_cosmos(cls, doc: dict[str, Any]) -> SyncState:
        """Rebuild from a Cosmos doc; ignores Cosmos system fields (_etag…)."""
        return cls.model_validate(doc)


class DocSyncResult(BaseModel):
    """Outcome for one document in a sync run (feeds per-doc Log Analytics)."""

    doc_id: str
    title: str
    status: DocStatus
    chunks: int = 0
    version: int = 0
    error: str | None = None


class SyncReport(BaseModel):
    """Aggregate outcome of one sync run (feeds the run-history log line)."""

    run_id: str
    tenant_id: str
    source: str
    bootstrap: bool
    changed: int = 0
    skipped: int = 0
    removed: int = 0
    failed: int = 0
    chunks_written: int = 0
    results: list[DocSyncResult] = Field(default_factory=list)


# --- CM-33: Knowledge Agent (RAG read side) ----------------------------------


class RetrievedChunk(BaseModel):
    """A chunk returned by hybrid retrieval, with ranking metadata.

    ``score`` is the fused Reciprocal-Rank-Fusion score (higher = better
    rank across the vector + keyword lists). ``similarity`` is the vector
    cosine similarity — the raw ``VectorDistance`` cosine score (``1.0`` =
    identical), clamped to [0, 1] — of the chunk to the query; it drives the
    answer confidence.
    """

    chunk: VectorChunk
    score: float
    similarity: float


class Citation(BaseModel):
    """An inline ``[index]`` citation pointing at a retrieved source chunk."""

    index: int
    doc_id: str
    doc_title: str


class KnowledgeAnswer(BaseModel):
    """The Knowledge Agent's grounded answer (or a refusal).

    ``refused=True`` means confidence fell below
    :data:`CONFIDENCE_THRESHOLD` (or the model couldn't answer from the
    retrieved context) — the node hands off to Maintenance in that case.
    """

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    refused: bool = False


#: The actions the CM-84 reasoning loop can take on each iteration.
KnowledgeAction = Literal["answer", "reformulate", "search_more", "give_up"]


class KnowledgeDecision(BaseModel):
    """CM-84: the loop's per-iteration decision (LLM-validated structured output).

    Given the question plus the passages accumulated so far, the decision model
    picks an :data:`KnowledgeAction`:

    * ``answer`` — the evidence is sufficient; synthesize the final answer and stop.
    * ``reformulate`` — re-phrase the query (synonyms / decomposition) and retrieve again.
    * ``search_more`` — issue a follow-up sub-question (multi-hop) and retrieve again.
    * ``give_up`` — nothing will ground an answer; refuse.

    ``query`` carries the next query for ``reformulate`` / ``search_more`` (ignored
    otherwise). ``rationale`` is a short free-text reason, useful for the per-step
    trace CM-85 will surface.
    """

    action: KnowledgeAction
    query: str = ""
    rationale: str = ""
