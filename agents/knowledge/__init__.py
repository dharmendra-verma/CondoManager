"""``agents.knowledge`` — Google Drive → Cosmos vector ingestion (CM-34).

Jira: CM-34  | Epic: CM-Epic 9 (Knowledge ingestion)  | Phase 1

Keeps the Cosmos ``policies-vector`` container in lockstep with a Google
Drive folder of policy/SOP docs. The package is import-cheap (all heavy
SDKs — google-*, azure-cosmos, langchain — are lazy-imported in the module
that uses them) and side-effect free, so ``import agents.knowledge`` works
in the test env without the deploy-time extras installed.

Public surface:

* :func:`run_sync` — one sync pass (the orchestrator).
* :func:`chunk_text` / :class:`Embedder` / :func:`default_embedder` —
  chunking + embedding, reused by the Knowledge Agent (CM-33).
* :class:`GoogleDriveClient` — service-account Drive client.
* :class:`CosmosVectorStore` / :func:`get_vector_store` — persistence.
* Models: :class:`VectorChunk`, :class:`SyncState`, :class:`SyncReport`, …

CM-33 (Knowledge Agent / RAG read side) adds:

* :func:`retrieve` — hybrid vector + keyword retrieval with RRF rerank.
* :func:`answer_question` — grounded answer with citations + confidence.
* :func:`get_chat_model` / :class:`ChatModel` — env-driven answerer (+ stub).
* Models: :class:`KnowledgeAnswer`, :class:`RetrievedChunk`, :class:`Citation`.

CM-83 (Track A) wraps the RAG flow in a bounded reasoning loop:

* :func:`get_knowledge_planner` / :class:`KnowledgePlanner` — env-driven loop
  scaffold (real LLM loop vs deterministic ``StubKnowledgePlanner``), single-pass
  by default. :class:`PlannerResult` carries the answer + search/cost accounting.
"""

from __future__ import annotations

from agents.knowledge.chunking import chunk_text
from agents.knowledge.cosmos_store import CosmosVectorStore, get_vector_store
from agents.knowledge.embeddings import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    AzureOpenAIEmbedder,
    Embedder,
    default_embedder,
)
from agents.knowledge.gdrive_client import GoogleDriveClient
from agents.knowledge.llm import ChatModel, get_chat_model
from agents.knowledge.local_source import (
    SUPPORTED_EXTENSIONS,
    LocalFolderClient,
    default_source,
    ingest_folder,
)
from agents.knowledge.models import (
    CONFIDENCE_THRESHOLD,
    Citation,
    DocState,
    DocSyncResult,
    DriveChange,
    KnowledgeAnswer,
    KnowledgeDecision,
    RetrievedChunk,
    SyncReport,
    SyncState,
    VectorChunk,
    chunk_id,
)
from agents.knowledge.planner import (
    KnowledgePlanner,
    PlannerResult,
    get_knowledge_planner,
)
from agents.knowledge.rag import answer_question
from agents.knowledge.retrieval import retrieve
from agents.knowledge.sync import run_sync

__all__ = [
    "CONFIDENCE_THRESHOLD",
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "AzureOpenAIEmbedder",
    "ChatModel",
    "Citation",
    "CosmosVectorStore",
    "DocState",
    "DocSyncResult",
    "DriveChange",
    "Embedder",
    "GoogleDriveClient",
    "KnowledgeAnswer",
    "KnowledgeDecision",
    "KnowledgePlanner",
    "SUPPORTED_EXTENSIONS",
    "LocalFolderClient",
    "PlannerResult",
    "RetrievedChunk",
    "SyncReport",
    "SyncState",
    "VectorChunk",
    "answer_question",
    "chunk_id",
    "chunk_text",
    "default_embedder",
    "default_source",
    "get_chat_model",
    "get_knowledge_planner",
    "get_vector_store",
    "ingest_folder",
    "retrieve",
    "run_sync",
]
