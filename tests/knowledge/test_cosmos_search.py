"""Tests for the CM-33 read path on ``CosmosVectorStore`` (search + keyword).

Cosmos SDK is mocked at construction time (same approach as
``test_cosmos_store.py``) so these run offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agents.knowledge import CosmosVectorStore, VectorChunk


def _make_store() -> tuple[CosmosVectorStore, MagicMock, MagicMock]:
    vector = MagicMock()
    state = MagicMock()
    database = MagicMock()
    database.get_container_client.side_effect = [vector, state]
    client = MagicMock()
    client.get_database_client.return_value = database
    with (
        patch("azure.cosmos.CosmosClient", return_value=client),
        patch("azure.identity.DefaultAzureCredential"),
    ):
        store = CosmosVectorStore(endpoint="https://example.documents.azure.net/")
    return store, vector, state


def _chunk_doc(doc_id: str, idx: int, text: str = "policy text") -> dict[str, object]:
    return VectorChunk(
        id=f"t1:{doc_id}:{idx}",
        tenantId="t1",
        doc_id=doc_id,
        doc_title="Doc",
        chunk_index=idx,
        text=text,
        embedding=[0.1, 0.2],
        content_hash="h",
        doc_version=1,
        source="gdrive",
        ts="2026-05-29T00:00:00+00:00",
    ).model_dump()


def test_search_chunks_builds_vector_query_and_maps_distance() -> None:
    store, vector, _state = _make_store()
    vector.query_items.return_value = iter(
        [{"chunk": _chunk_doc("d1", 0), "_distance": 0.25}]
    )

    out = store.search_chunks("t1", [0.1, 0.2, 0.3], top_k=3)

    assert len(out) == 1
    chunk, distance = out[0]
    assert isinstance(chunk, VectorChunk)
    assert chunk.doc_id == "d1"
    assert distance == 0.25

    call = vector.query_items.call_args
    assert "VectorDistance(c.embedding, @qv)" in call.kwargs["query"]
    assert "TOP 3" in call.kwargs["query"]
    assert call.kwargs["partition_key"] == "t1"


def test_keyword_search_builds_contains_clauses_lowercased() -> None:
    store, vector, _state = _make_store()
    vector.query_items.return_value = iter([{"chunk": _chunk_doc("d2", 0)}])

    out = store.keyword_search("t1", ["Gym", "HOURS"], top_k=2)

    assert len(out) == 1 and out[0].doc_id == "d2"
    call = vector.query_items.call_args
    assert "CONTAINS(LOWER(c.text)" in call.kwargs["query"]
    values = {p["name"]: p["value"] for p in call.kwargs["parameters"]}
    assert values["@kw0"] == "gym"  # lowercased
    assert values["@kw1"] == "hours"
    assert call.kwargs["partition_key"] == "t1"


def test_keyword_search_empty_terms_short_circuits() -> None:
    store, vector, _state = _make_store()
    assert store.keyword_search("t1", [], top_k=5) == []
    vector.query_items.assert_not_called()
