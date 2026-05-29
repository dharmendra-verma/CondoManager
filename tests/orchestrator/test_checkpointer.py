"""``CosmosCheckpointSaver`` + ``get_checkpointer()`` selector tests.

The Cosmos client is mocked at construction time so these tests run
offline. Real Cosmos hits are covered by the manual smoke recipe in
``docs/AGENTS.md``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agents.orchestrator import CosmosCheckpointSaver, get_checkpointer
from agents.orchestrator.checkpointer import SECRET_PLACEHOLDER
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata
from langgraph.checkpoint.memory import MemorySaver


def _make_saver() -> tuple[CosmosCheckpointSaver, MagicMock]:
    """Build a ``CosmosCheckpointSaver`` with the Cosmos SDK mocked out."""
    container_mock = MagicMock()
    client_mock = MagicMock()
    client_mock.get_database_client.return_value.get_container_client.return_value = (
        container_mock
    )

    with (
        patch(
            "azure.cosmos.CosmosClient", return_value=client_mock
        ) as _cosmos_patch,
        patch("azure.identity.DefaultAzureCredential"),
    ):
        saver = CosmosCheckpointSaver(endpoint="https://example.documents.azure.net/")
    return saver, container_mock


# ---- get_checkpointer() selector -----------------------------------------


def test_selector_unset_returns_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    assert isinstance(get_checkpointer(), MemorySaver)


def test_selector_blank_returns_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "   ")
    assert isinstance(get_checkpointer(), MemorySaver)


def test_selector_placeholder_returns_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """CM-18 ``REPLACE-ME`` placeholder must be treated as if-unset."""
    monkeypatch.setenv("COSMOS_ENDPOINT", SECRET_PLACEHOLDER)
    assert isinstance(get_checkpointer(), MemorySaver)


def test_selector_real_endpoint_returns_cosmos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://example.documents.azure.net/")
    with (
        patch("azure.cosmos.CosmosClient"),
        patch("azure.identity.DefaultAzureCredential"),
    ):
        saver = get_checkpointer()
    assert isinstance(saver, CosmosCheckpointSaver)


# ---- put() / get_tuple() round-trip --------------------------------------


def test_put_upserts_document_with_expected_shape() -> None:
    saver, container = _make_saver()
    config = {"configurable": {"thread_id": "thr-1"}}
    checkpoint: Checkpoint = {"id": "ck-1", "v": 1}  # type: ignore[typeddict-item]
    metadata: CheckpointMetadata = {"source": "input", "step": 0}  # type: ignore[typeddict-item]

    out = saver.put(config, checkpoint, metadata, new_versions={})

    container.upsert_item.assert_called_once()
    doc = container.upsert_item.call_args.args[0]
    assert doc["id"] == "thr-1:ck-1"
    assert doc["thread_id"] == "thr-1"
    assert doc["checkpoint_id"] == "ck-1"
    assert doc["parent_checkpoint_id"] is None
    assert "ts" in doc
    assert out == {
        "configurable": {"thread_id": "thr-1", "checkpoint_id": "ck-1"}
    }


def test_put_captures_parent_checkpoint_id() -> None:
    """Resume scenario: config carries ``checkpoint_id`` as parent."""
    saver, container = _make_saver()
    config = {
        "configurable": {"thread_id": "thr-1", "checkpoint_id": "ck-parent"}
    }
    checkpoint: Checkpoint = {"id": "ck-child", "v": 1}  # type: ignore[typeddict-item]
    metadata: CheckpointMetadata = {"source": "loop", "step": 1}  # type: ignore[typeddict-item]

    saver.put(config, checkpoint, metadata, new_versions={})
    doc = container.upsert_item.call_args.args[0]
    assert doc["parent_checkpoint_id"] == "ck-parent"


def test_get_tuple_returns_none_when_empty() -> None:
    saver, container = _make_saver()
    container.query_items.return_value = iter([])
    out = saver.get_tuple({"configurable": {"thread_id": "thr-empty"}})
    assert out is None


def test_get_tuple_reconstructs_checkpoint_tuple() -> None:
    saver, container = _make_saver()
    container.query_items.return_value = iter([
        {
            "id": "thr-1:ck-1",
            "thread_id": "thr-1",
            "checkpoint_id": "ck-1",
            "parent_checkpoint_id": None,
            "ts": "2026-05-29T10:00:00+00:00",
            "checkpoint": {"id": "ck-1", "v": 1},
            "metadata": {"source": "input", "step": 0},
        }
    ])
    out = saver.get_tuple({"configurable": {"thread_id": "thr-1"}})
    assert out is not None
    assert out.config["configurable"]["thread_id"] == "thr-1"
    assert out.config["configurable"]["checkpoint_id"] == "ck-1"
    assert out.parent_config is None
    assert out.checkpoint == {"id": "ck-1", "v": 1}


def test_get_tuple_cosmos_failure_returns_none() -> None:
    """Transient Cosmos error must not crash the graph — log + None."""
    saver, container = _make_saver()
    container.query_items.side_effect = RuntimeError("simulated 503")
    out = saver.get_tuple({"configurable": {"thread_id": "thr-1"}})
    assert out is None


# ---- async surface is the documented-unsupported ----------------------------


async def test_aput_raises_not_implemented() -> None:
    saver, _ = _make_saver()
    with pytest.raises(NotImplementedError):
        await saver.aput({"configurable": {"thread_id": "thr-1"}}, {}, {}, {})


async def test_aget_tuple_raises_not_implemented() -> None:
    saver, _ = _make_saver()
    with pytest.raises(NotImplementedError):
        await saver.aget_tuple({"configurable": {"thread_id": "thr-1"}})


# ---- thread_id resolution -----------------------------------------------


def test_missing_thread_id_raises_keyerror() -> None:
    """Caller forgot ``configurable.thread_id`` — loud failure beats silent default."""
    saver, _ = _make_saver()
    with pytest.raises(KeyError):
        saver.put({"configurable": {}}, {"id": "ck-1"}, {}, {})  # type: ignore[arg-type]
