"""Azure AI Search read/write store for the policy RAG corpus (CM-100).

Jira: CM-100  | Epic: Knowledge Agent / RAG  | Phase 1

Cosmos DiskANN vector search + the hand-rolled keyword/RRF fusion ranks
boilerplate (headers, refund tables) above the rule/eligibility chunk that
answers a question (CM-99) — pure vector similarity can't fix it. Azure AI Search
adds real **BM25** lexical scoring and native vector search; fused (RRF) they
surface the keyword-bearing rule chunk ("tenant / owner / NOC") that vector-only
missed.

:class:`AISearchStore` implements the existing
:class:`~agents.knowledge.retrieval.SearchStore` Protocol, so it drops in behind
``retrieve()`` with no caller change:

* :meth:`search_chunks` — a vector kNN query (the query embedding is supplied by
  ``retrieve()`` via the CM-34 :class:`~agents.knowledge.embeddings.Embedder`);
* :meth:`keyword_search` — a BM25 full-text query over the chunk ``text``.

``retrieve()._fuse`` then RRF-fuses the two lists exactly as for Cosmos — the
difference is the keyword half is now real BM25, not a crude ``CONTAINS``. (A
single server-side hybrid query + the semantic ranker is a clean future upgrade,
deferred here: the shared ``comossearch`` service is Free tier, which has no
semantic ranker.)

Both reads are scoped by a ``tenant_id`` filter covering the caller's tenant
**plus** any shared policy partitions (``POLICY_TENANT_ID``), preserving the
CM-97 building-wide-policy semantics and per-tenant isolation.

:func:`get_search_store` selects this store when ``AZURE_SEARCH_ENDPOINT`` +
``AZURE_SEARCH_INDEX`` (+ key) are configured, else returns ``None`` so the
caller falls back to the Cosmos store — keeping CI/offline unchanged (same
env-seam convention as ``get_vector_store`` / the CM-79 chat factory).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from agents.knowledge.cosmos_store import _policy_partitions_from_env
from agents.knowledge.embeddings import EMBEDDING_DIM
from agents.knowledge.models import SECRET_PLACEHOLDER, VectorChunk

_log = logging.getLogger(__name__)

#: Default index name for the policy corpus (per the CM-100 decision — a NEW
#: index, isolated from the test RAG index on the shared service).
DEFAULT_SEARCH_INDEX = "condo-policies"

#: AI Search REST/data-plane API version (stable, supports vector queries).
DEFAULT_SEARCH_API_VERSION = "2024-07-01"

#: The chunk fields projected back from a search hit — everything needed to
#: reconstruct a :class:`VectorChunk` (the embedding is not retrieved: it's large
#: and unused on the read path).
_SELECT_FIELDS = (
    "id",
    "tenant_id",
    "doc_id",
    "doc_title",
    "chunk_index",
    "text",
    "content_hash",
    "doc_version",
    "source",
    "ts",
)


def _env_or_none(name: str) -> str | None:
    """Env var value, treating empty + ``REPLACE-ME`` as unset (CM-18)."""
    val = os.environ.get(name, "").strip()
    if not val or val == SECRET_PLACEHOLDER:
        return None
    return val


def _encode_key(chunk_id: str) -> str:
    """Encode a ``VectorChunk.id`` into a valid AI Search document key.

    Search keys allow only letters, digits, ``_``, ``-``, ``=`` — but chunk ids
    are ``"<tenant>:<doc>:<index>"`` (colons are illegal). url-safe base64
    (alphabet ``A-Za-z0-9-_``) is reversible and always valid; the original id is
    also stored in the retrievable ``id`` field, so reads never need to decode.
    """
    return base64.urlsafe_b64encode(chunk_id.encode("utf-8")).decode("ascii")


def _odata_quote(value: str) -> str:
    """Escape a string literal for an OData filter (single-quote → doubled)."""
    return value.replace("'", "''")


class AISearchStore:
    """:class:`~agents.knowledge.retrieval.SearchStore` backed by Azure AI Search."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        index_name: str = DEFAULT_SEARCH_INDEX,
        policy_tenant_ids: list[str] | None = None,
    ) -> None:
        # Lazy import (CM-23 pattern) so the offline path / Cosmos installs don't
        # pay the azure-search-documents import cost. Any-aliased so mypy --strict
        # isn't coupled to the SDK's fast-moving signatures (as cosmos_store does).
        from azure.core.credentials import AzureKeyCredential  # noqa: PLC0415
        from azure.search.documents import SearchClient  # noqa: PLC0415

        client_cls: Any = SearchClient
        self._index_name = index_name
        self._client: Any = client_cls(
            endpoint=endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(api_key),
            api_version=DEFAULT_SEARCH_API_VERSION,
        )
        self._policy_partitions = (
            policy_tenant_ids if policy_tenant_ids is not None
            else _policy_partitions_from_env()
        )

    # -- read surface (the SearchStore Protocol) -------------------------------

    def _tenant_filter(self, tenant_id: str) -> str:
        """OData filter for the caller's tenant + shared policy partitions.

        De-duplicates, caller first. The same building-wide-policy union as
        CM-97's Cosmos path, enforced server-side as a filter on every query so a
        tenant only ever sees its own chunks + the shared policy corpus.
        """
        seen: set[str] = set()
        clauses: list[str] = []
        for candidate in (tenant_id, *self._policy_partitions):
            if candidate and candidate not in seen:
                seen.add(candidate)
                clauses.append(f"tenant_id eq '{_odata_quote(candidate)}'")
        return " or ".join(clauses)

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        """Vector kNN search, tenant-filtered. Returns ``(chunk, score)`` pairs.

        ``score`` is AI Search's ``@search.score`` for a pure vector query — the
        cosine similarity — passed through for the CM-47 answer-confidence path,
        clamped to [0, 1].
        """
        from azure.search.documents.models import VectorizedQuery  # noqa: PLC0415

        vector_query: Any = VectorizedQuery(
            vector=embedding, k_nearest_neighbors=top_k, fields="embedding"
        )
        results = self._client.search(
            search_text=None,
            vector_queries=[vector_query],
            filter=self._tenant_filter(tenant_id),
            select=list(_SELECT_FIELDS),
            top=top_k,
        )
        out: list[tuple[VectorChunk, float]] = []
        for doc in results:
            score = float(doc.get("@search.score", 0.0))
            out.append((self._to_chunk(doc), max(0.0, min(1.0, score))))
        return out

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        """BM25 full-text search over ``text``, tenant-filtered.

        Empty ``terms`` returns nothing (matches the Cosmos store). Results come
        ordered by BM25 relevance, so their position is the rank ``retrieve._fuse``
        uses — the real lexical signal that lifts keyword-bearing rule chunks
        above boilerplate (the CM-99 fix).
        """
        if not terms:
            return []
        results = self._client.search(
            search_text=" ".join(terms),
            search_fields=["text"],
            filter=self._tenant_filter(tenant_id),
            select=list(_SELECT_FIELDS),
            top=top_k,
        )
        return [self._to_chunk(doc) for doc in results]

    @staticmethod
    def _to_chunk(doc: dict[str, Any]) -> VectorChunk:
        """Project a search hit back into a :class:`VectorChunk`.

        The embedding is not retrieved (unused on the read path), so it's filled
        with an empty list — downstream retrieval/answer never reads it.
        """
        return VectorChunk(
            id=str(doc["id"]),
            tenantId=str(doc["tenant_id"]),
            doc_id=str(doc.get("doc_id", "")),
            doc_title=str(doc.get("doc_title", "")),
            chunk_index=int(doc.get("chunk_index", 0)),
            text=str(doc.get("text", "")),
            embedding=[],
            content_hash=str(doc.get("content_hash", "")),
            doc_version=int(doc.get("doc_version", 0)),
            source=str(doc.get("source", "")),
            ts=str(doc.get("ts", "")),
        )

    # -- write surface (ingestion / migration) ---------------------------------

    def upsert_chunks(self, chunks: list[VectorChunk]) -> None:
        """Upload/merge chunks into the index (idempotent by encoded key)."""
        if not chunks:
            return
        self._client.merge_or_upload_documents(
            documents=[self._to_doc(c) for c in chunks]
        )

    @staticmethod
    def _to_doc(chunk: VectorChunk) -> dict[str, Any]:
        """Shape a :class:`VectorChunk` as an AI Search document."""
        return {
            "key": _encode_key(chunk.id),
            "id": chunk.id,
            "tenant_id": chunk.tenantId,
            "doc_id": chunk.doc_id,
            "doc_title": chunk.doc_title,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "embedding": chunk.embedding,
            "content_hash": chunk.content_hash,
            "doc_version": chunk.doc_version,
            "source": chunk.source,
            "ts": chunk.ts,
        }


def ensure_policy_index(
    *,
    endpoint: str,
    api_key: str,
    index_name: str = DEFAULT_SEARCH_INDEX,
    dimensions: int = EMBEDDING_DIM,
) -> None:
    """Create the policy index if it doesn't exist (idempotent).

    Used by the one-shot migration / ingestion tooling, not the runtime read
    path. Defines an HNSW vector field on ``embedding`` (cosine), a BM25-searchable
    ``text`` field, and ``tenant_id`` as filterable (per-tenant scoping). Lazy
    imports the management SDK so the runtime read path never loads it.
    """
    from azure.core.credentials import AzureKeyCredential  # noqa: PLC0415
    from azure.search.documents.indexes import SearchIndexClient  # noqa: PLC0415
    from azure.search.documents.indexes.models import (  # noqa: PLC0415
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="key", type=SearchFieldDataType.String, key=True),
        SimpleField(name="id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="tenant_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="doc_title", type=SearchFieldDataType.String),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32),
        SearchField(
            name="text",
            type=SearchFieldDataType.String,
            searchable=True,  # BM25
        ),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="policy-hnsw",
        ),
        SimpleField(name="content_hash", type=SearchFieldDataType.String),
        SimpleField(name="doc_version", type=SearchFieldDataType.Int32),
        SimpleField(name="source", type=SearchFieldDataType.String),
        SimpleField(name="ts", type=SearchFieldDataType.String),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="policy-hnsw-algo")],
        profiles=[
            VectorSearchProfile(
                name="policy-hnsw", algorithm_configuration_name="policy-hnsw-algo"
            )
        ],
    )
    index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)

    cred: Any = AzureKeyCredential(api_key)
    admin: Any = SearchIndexClient(endpoint=endpoint, credential=cred)
    admin.create_or_update_index(index)


def get_search_store() -> AISearchStore | None:
    """Build an :class:`AISearchStore` from env, or ``None`` when unconfigured.

    Requires ``AZURE_SEARCH_ENDPOINT`` + ``AZURE_SEARCH_KEY`` (index name defaults
    to ``condo-policies`` via ``AZURE_SEARCH_INDEX``). ``None`` lets the caller
    fall back to the Cosmos store (``get_vector_store``), so CI / offline / any
    install without AI Search configured is unchanged. ``REPLACE-ME`` counts as
    unset (CM-18).
    """
    endpoint = _env_or_none("AZURE_SEARCH_ENDPOINT")
    api_key = _env_or_none("AZURE_SEARCH_KEY")
    if endpoint is None or api_key is None:
        return None
    index_name = _env_or_none("AZURE_SEARCH_INDEX") or DEFAULT_SEARCH_INDEX
    try:
        return AISearchStore(endpoint=endpoint, api_key=api_key, index_name=index_name)
    except Exception as exc:  # noqa: BLE001 — degrade to Cosmos rather than crash the node
        _log.warning("could not build AISearchStore; falling back: %s", exc)
        return None
