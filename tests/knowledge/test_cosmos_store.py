"""Tests for ``agents.knowledge.cosmos_store`` (CM-34).

The Cosmos SDK is mocked at construction time (same approach as
``tests/orchestrator/test_checkpointer.py``) so these run offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agents.knowledge import CosmosVectorStore, VectorChunk, chunk_id
from agents.knowledge.cosmos_store import get_vector_store
from agents.knowledge.models import SECRET_PLACEHOLDER, SyncState


def _make_store() -> tuple[CosmosVectorStore, MagicMock, MagicMock]:
    """Build a store with distinct vector + state container mocks."""
    vector = MagicMock()
    state = MagicMock()
    database = MagicMock()
    # __init__ calls get_container_client(vector) then (state), in that order.
    database.get_container_client.side_effect = [vector, state]
    client = MagicMock()
    client.get_database_client.return_value = database

    with (
        patch("azure.cosmos.CosmosClient", return_value=client),
        patch("azure.identity.DefaultAzureCredential"),
    ):
        store = CosmosVectorStore(endpoint="https://example.documents.azure.net/")
    return store, vector, state


def _chunk(doc_id: str, index: int) -> VectorChunk:
    return VectorChunk(
        id=chunk_id("t1", doc_id, index),
        tenantId="t1",
        doc_id=doc_id,
        doc_title="Doc",
        chunk_index=index,
        text="body",
        embedding=[0.1, 0.2],
        content_hash="h",
        doc_version=1,
        source="gdrive",
        ts="2026-05-29T00:00:00+00:00",
    )


def test_chunk_id_is_deterministic() -> None:
    assert chunk_id("t1", "doc9", 3) == "t1:doc9:3"


def test_save_state_upserts_doc_with_id_equal_source() -> None:
    store, _vector, state = _make_store()
    store.save_state(SyncState(source="gdrive", page_token="tok-1"))

    state.upsert_item.assert_called_once()
    doc = state.upsert_item.call_args.args[0]
    assert doc["id"] == "gdrive"
    assert doc["source"] == "gdrive"
    assert doc["page_token"] == "tok-1"


def test_load_state_missing_returns_empty_state() -> None:
    from azure.cosmos import exceptions

    store, _vector, state = _make_store()
    state.read_item.side_effect = exceptions.CosmosResourceNotFoundError()

    out = store.load_state("gdrive")
    assert out.source == "gdrive"
    assert out.page_token is None
    assert out.docs == {}


def test_load_state_existing_round_trips_docs() -> None:
    store, _vector, state = _make_store()
    state.read_item.return_value = {
        "id": "gdrive",
        "source": "gdrive",
        "page_token": "tok-7",
        "docs": {"d1": {"content_hash": "abc", "version": 2, "title": "T"}},
        "_etag": "\"system-field\"",
    }
    out = store.load_state("gdrive")
    assert out.page_token == "tok-7"
    assert out.docs["d1"].version == 2
    assert out.docs["d1"].content_hash == "abc"


def test_upsert_chunks_writes_each_chunk() -> None:
    store, vector, _state = _make_store()
    store.upsert_chunks([_chunk("d1", 0), _chunk("d1", 1)])
    assert vector.upsert_item.call_count == 2


def test_delete_stale_chunks_queries_and_deletes() -> None:
    store, vector, _state = _make_store()
    vector.query_items.return_value = iter([{"id": "t1:d1:2"}, {"id": "t1:d1:3"}])

    deleted = store.delete_stale_chunks("t1", "d1", valid_count=2)

    assert deleted == 2
    assert vector.delete_item.call_count == 2
    # Deletes must target the tenant partition.
    for call in vector.delete_item.call_args_list:
        assert call.kwargs["partition_key"] == "t1"


def test_get_vector_store_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    assert get_vector_store() is None


def test_get_vector_store_placeholder_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", SECRET_PLACEHOLDER)
    assert get_vector_store() is None


def test_get_vector_store_real_endpoint_builds_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://example.documents.azure.net/")
    with (
        patch("azure.cosmos.CosmosClient"),
        patch("azure.identity.DefaultAzureCredential"),
    ):
        store = get_vector_store()
    assert isinstance(store, CosmosVectorStore)
