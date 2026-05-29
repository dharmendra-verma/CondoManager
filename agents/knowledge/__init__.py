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
from agents.knowledge.models import (
    DocState,
    DocSyncResult,
    DriveChange,
    SyncReport,
    SyncState,
    VectorChunk,
    chunk_id,
)
from agents.knowledge.sync import run_sync

__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "AzureOpenAIEmbedder",
    "CosmosVectorStore",
    "DocState",
    "DocSyncResult",
    "DriveChange",
    "Embedder",
    "GoogleDriveClient",
    "SyncReport",
    "SyncState",
    "VectorChunk",
    "chunk_id",
    "chunk_text",
    "default_embedder",
    "get_vector_store",
    "run_sync",
]
