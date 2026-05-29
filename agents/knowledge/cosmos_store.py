"""Cosmos persistence for vectors + sync state (CM-34).

Jira: CM-34  | Epic: CM-Epic 9  | Phase 1

Two containers, one client:

* ``policies-vector`` (CM-17) — RAG chunks with the DiskANN-indexed
  ``/embedding``. Partition key ``/tenantId``.
* ``knowledge_sync`` (CM-34) — one :class:`SyncState` doc per source.
  Partition key ``/source``.

Auth mirrors CM-28's ``CosmosCheckpointSaver``: ``DefaultAzureCredential``
resolves the CM-18 User-Assigned MI in prod and ``az login`` locally — no
connection-string secret to manage. The MI needs the Cosmos data-plane
role (``Cosmos DB Built-in Data Contributor``); see ``docs/INFRA.md``.

Idempotency lives here too: :meth:`upsert_chunks` writes deterministic ids
(no duplicates on retry) and :meth:`delete_stale_chunks` removes trailing
chunks left behind when an edited doc shrinks.
"""

from __future__ import annotations

import logging
import os

from agents.knowledge.models import (
    DATABASE_NAME,
    SECRET_PLACEHOLDER,
    STATE_CONTAINER,
    VECTOR_CONTAINER,
    SyncState,
    VectorChunk,
)

_log = logging.getLogger(__name__)


class CosmosVectorStore:
    """Reads/writes ``policies-vector`` chunks and ``knowledge_sync`` state."""

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str = DATABASE_NAME,
        vector_container: str = VECTOR_CONTAINER,
        state_container: str = STATE_CONTAINER,
    ) -> None:
        # Lazy-import azure.cosmos so callers that only need the env selector
        # (and get None) don't pay the import cost.
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        client = CosmosClient(url=endpoint, credential=credential)
        database = client.get_database_client(database_name)
        self._vector = database.get_container_client(vector_container)
        self._state = database.get_container_client(state_container)

    # ---- sync state ------------------------------------------------------

    def load_state(self, source: str) -> SyncState:
        """Point-read the state doc for ``source``; empty state if absent."""
        from azure.cosmos import exceptions  # noqa: PLC0415

        try:
            doc = self._state.read_item(item=source, partition_key=source)
        except exceptions.CosmosResourceNotFoundError:
            return SyncState(source=source)
        return SyncState.from_cosmos(doc)

    def save_state(self, state: SyncState) -> None:
        """Upsert the state doc (id == source)."""
        self._state.upsert_item(state.to_cosmos())

    # ---- vector chunks ---------------------------------------------------

    def upsert_chunks(self, chunks: list[VectorChunk]) -> None:
        """Upsert each chunk by its deterministic id (idempotent on retry)."""
        for chunk in chunks:
            self._vector.upsert_item(chunk.model_dump())

    def delete_stale_chunks(
        self, tenant_id: str, doc_id: str, *, valid_count: int
    ) -> int:
        """Delete chunks for ``doc_id`` whose index is >= ``valid_count``.

        Called after a re-index so a doc that shrank from N to M chunks
        doesn't leave chunks ``[M, N)`` orphaned in the container.
        """
        return self._delete_where(tenant_id, doc_id, min_index=valid_count)

    def delete_doc_chunks(self, tenant_id: str, doc_id: str) -> int:
        """Delete all chunks for ``doc_id`` (doc removed / emptied)."""
        return self._delete_where(tenant_id, doc_id, min_index=0)

    # ---- vector + keyword search (CM-33 read path) -----------------------

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        """Vector-similarity search via the DiskANN ``VectorDistance()`` index.

        Returns up to ``top_k`` ``(chunk, distance)`` pairs nearest to
        ``embedding``, ascending by distance (nearest first), scoped to the
        tenant partition. ``top_k`` is interpolated as an int (Cosmos ``TOP``
        does not accept a bound parameter) — it is code-supplied, never user
        input, so this is not an injection vector.
        """
        query = (
            f"SELECT TOP {int(top_k)} c AS chunk, "
            "VectorDistance(c.embedding, @qv) AS _distance "
            "FROM c WHERE c.tenantId = @t "
            "ORDER BY VectorDistance(c.embedding, @qv)"
        )
        params: list[dict[str, object]] = [
            {"name": "@qv", "value": embedding},
            {"name": "@t", "value": tenant_id},
        ]
        rows = list(
            self._vector.query_items(
                query=query, parameters=params, partition_key=tenant_id
            )
        )
        return [
            (VectorChunk.model_validate(row["chunk"]), float(row["_distance"]))
            for row in rows
        ]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        """Case-insensitive keyword search over chunk text (``CONTAINS``).

        OR-matches any of ``terms`` against ``LOWER(c.text)``; empty ``terms``
        returns nothing. Substring matching is coarse but cheap — it is the
        lexical half of hybrid retrieval that RRF fuses with the vector hits,
        not a standalone ranker.
        """
        if not terms:
            return []
        params: list[dict[str, object]] = [{"name": "@t", "value": tenant_id}]
        clauses: list[str] = []
        for i, term in enumerate(terms):
            name = f"@kw{i}"
            clauses.append(f"CONTAINS(LOWER(c.text), {name})")
            params.append({"name": name, "value": term.lower()})
        query = (
            f"SELECT TOP {int(top_k)} c AS chunk FROM c "
            f"WHERE c.tenantId = @t AND ({' OR '.join(clauses)})"
        )
        rows = list(
            self._vector.query_items(
                query=query, parameters=params, partition_key=tenant_id
            )
        )
        return [VectorChunk.model_validate(row["chunk"]) for row in rows]

    def _delete_where(self, tenant_id: str, doc_id: str, *, min_index: int) -> int:
        query = (
            "SELECT c.id FROM c WHERE c.tenantId = @t AND c.doc_id = @d "
            "AND c.chunk_index >= @min"
        )
        params: list[dict[str, object]] = [
            {"name": "@t", "value": tenant_id},
            {"name": "@d", "value": doc_id},
            {"name": "@min", "value": min_index},
        ]
        items = list(
            self._vector.query_items(
                query=query, parameters=params, partition_key=tenant_id
            )
        )
        for item in items:
            self._vector.delete_item(item=item["id"], partition_key=tenant_id)
        return len(items)


def get_vector_store() -> CosmosVectorStore | None:
    """Build a store from ``COSMOS_ENDPOINT``, or ``None`` when unset.

    Same placeholder-aware posture as CM-28's ``get_checkpointer()`` — the
    Function App boots cleanly before the endpoint is configured.
    """
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info("COSMOS_ENDPOINT not set; Drive→Cosmos sync store unavailable.")
        return None
    return CosmosVectorStore(endpoint=endpoint)
