"""Tests for the CM-33 read path on ``CosmosVectorStore`` (search + keyword).

Cosmos SDK is mocked at construction time (same approach as
``test_cosmos_store.py``) so these run offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agents.knowledge import CosmosVectorStore, VectorChunk
from agents.knowledge.cosmos_store import _policy_partitions_from_env


def _make_store(
    policy_tenant_ids: list[str] | None = None,
) -> tuple[CosmosVectorStore, MagicMock, MagicMock]:
    # Default to an explicit [] so tests are isolated from any POLICY_TENANT_ID
    # in the developer's environment (single-partition behaviour).
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
        store = CosmosVectorStore(
            endpoint="https://example.documents.azure.net/",
            policy_tenant_ids=policy_tenant_ids if policy_tenant_ids is not None else [],
        )
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


def test_search_chunks_builds_vector_query_and_maps_score() -> None:
    store, vector, _state = _make_store()
    # CM-47: the projected value is the cosine *similarity* (VectorDistance),
    # passed through verbatim by search_chunks — 0.9 is a strong match.
    vector.query_items.return_value = iter(
        [{"chunk": _chunk_doc("d1", 0), "_score": 0.9}]
    )

    out = store.search_chunks("t1", [0.1, 0.2, 0.3], top_k=3)

    assert len(out) == 1
    chunk, score = out[0]
    assert isinstance(chunk, VectorChunk)
    assert chunk.doc_id == "d1"
    assert score == 0.9

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


# --- CM-97: shared building-wide policy partitions -------------------------


def test_policy_partitions_from_env_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICY_TENANT_ID", " shared-a , shared-b ")
    assert _policy_partitions_from_env() == ["shared-a", "shared-b"]


def test_policy_partitions_from_env_empty_and_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLICY_TENANT_ID", raising=False)
    assert _policy_partitions_from_env() == []
    monkeypatch.setenv("POLICY_TENANT_ID", "REPLACE-ME")
    assert _policy_partitions_from_env() == []


def test_search_chunks_merges_caller_and_shared_partitions() -> None:
    store, vector, _state = _make_store(policy_tenant_ids=["policies"])
    # Caller partition returns one weak hit; the shared policy partition returns
    # a stronger hit plus a duplicate of the caller's chunk at a lower score.
    vector.query_items.side_effect = [
        iter([{"chunk": _chunk_doc("dA", 0), "_score": 0.50}]),
        iter(
            [
                {"chunk": _chunk_doc("dB", 0), "_score": 0.80},
                {"chunk": _chunk_doc("dA", 0), "_score": 0.40},
            ]
        ),
    ]

    out = store.search_chunks("t1", [0.1, 0.2], top_k=3)

    # Merged + de-duped (dA kept at its best 0.50), sorted by similarity desc.
    assert [(c.doc_id, s) for c, s in out] == [("dB", 0.80), ("dA", 0.50)]
    # One query per partition, caller first then the shared policy partition.
    partition_keys = [c.kwargs["partition_key"] for c in vector.query_items.call_args_list]
    assert partition_keys == ["t1", "policies"]


def test_search_chunks_single_partition_when_no_shared() -> None:
    store, vector, _state = _make_store()  # no shared partitions
    vector.query_items.return_value = iter([{"chunk": _chunk_doc("d1", 0), "_score": 0.9}])
    out = store.search_chunks("t1", [0.1], top_k=3)
    assert [(c.doc_id, s) for c, s in out] == [("d1", 0.9)]
    assert vector.query_items.call_count == 1


def test_keyword_search_unions_shared_partition() -> None:
    store, vector, _state = _make_store(policy_tenant_ids=["policies"])
    vector.query_items.side_effect = [
        iter([{"chunk": _chunk_doc("dA", 0)}]),
        iter([{"chunk": _chunk_doc("dB", 0)}, {"chunk": _chunk_doc("dA", 0)}]),
    ]
    out = store.keyword_search("t1", ["gym"], top_k=5)
    assert sorted(c.doc_id for c in out) == ["dA", "dB"]  # union, de-duped
    partition_keys = [c.kwargs["partition_key"] for c in vector.query_items.call_args_list]
    assert partition_keys == ["t1", "policies"]
