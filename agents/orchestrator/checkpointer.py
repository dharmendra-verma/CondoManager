"""Cosmos-backed LangGraph checkpointer + env-var selector.

Jira: CM-28  | Epic: CM-Epic 4  | Phase 0

LangGraph ships ``MemorySaver``, ``SqliteSaver``, and ``PostgresSaver``
out of the box — none of them is Cosmos. This module ships the minimal
viable ``BaseCheckpointSaver`` implementation needed by
``graph.invoke()`` end-to-end:

* :meth:`CosmosCheckpointSaver.put` — upserts the latest checkpoint for
  a thread.
* :meth:`CosmosCheckpointSaver.get_tuple` — reads the latest checkpoint
  for a thread.
* :meth:`CosmosCheckpointSaver.list` — lists checkpoints for a thread.

Async variants (``aput``, ``aget_tuple``, ``alist``) raise
``NotImplementedError`` — the CM-28 hello-world path uses the sync
surface; CM-31 / CM-32 may add async if their workloads need it.

Auth: ``DefaultAzureCredential`` resolves through the CM-18 User-Assigned
MI in prod (the same MI Container Apps mounts via ``secretRef`` for
other secrets). Local dev uses ``az login``. No connection-string
secret to wrangle.

Selector: :func:`get_checkpointer` returns ``CosmosCheckpointSaver``
when ``COSMOS_ENDPOINT`` is set to a real value; otherwise (unset, blank,
or the CM-18 ``REPLACE-ME`` placeholder) falls back to
``MemorySaver``. Tests stay offline; demo runs without Cosmos access.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.memory import MemorySaver

_log = logging.getLogger(__name__)

#: Cosmos DB database name from CM-17.
DATABASE_NAME = "condomanager"

#: Cosmos DB container name added by CM-28's cosmos.bicep change.
CONTAINER_NAME = "checkpoints"

#: CM-18 placeholder. Treated as if-unset.
SECRET_PLACEHOLDER = "REPLACE-ME"


def get_checkpointer() -> BaseCheckpointSaver[Any]:
    """Return ``CosmosCheckpointSaver`` if configured, else ``MemorySaver``.

    Selector logic:
      * ``COSMOS_ENDPOINT`` unset / blank / ``"REPLACE-ME"`` -> ``MemorySaver``.
      * Otherwise -> ``CosmosCheckpointSaver(endpoint=...)``.

    Same posture as CM-22's connection-string placeholder semantics —
    Container App boots cleanly before the operator populates the env.
    """
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info(
            "COSMOS_ENDPOINT not set; using LangGraph MemorySaver "
            "(checkpoints will not survive process restart)"
        )
        return MemorySaver()
    return CosmosCheckpointSaver(endpoint=endpoint)


class CosmosCheckpointSaver(BaseCheckpointSaver[str]):
    """Minimal Cosmos-backed ``BaseCheckpointSaver``.

    Document shape::

        {
            "id": "<thread_id>:<checkpoint_id>",
            "thread_id": "<thread_id>",           # partition key
            "checkpoint_id": "<checkpoint_id>",
            "parent_checkpoint_id": "<id> | null",
            "ts": "<iso8601 UTC>",
            "checkpoint": <serialized Checkpoint>,
            "metadata": <serialized CheckpointMetadata>,
        }

    ``thread_id`` is the partition key (see CM-28 cosmos.bicep change);
    all checkpoints for one graph run colocate, so the read-most-recent
    hot path during HITL resume is a single point-read.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str = DATABASE_NAME,
        container_name: str = CONTAINER_NAME,
    ) -> None:
        super().__init__()
        # Lazy-import azure.cosmos so processes that fall back to MemorySaver
        # don't pay the import cost.
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
        self._client = CosmosClient(url=endpoint, credential=credential)
        self._container = (
            self._client.get_database_client(database_name)
            .get_container_client(container_name)
        )

    # ---- sync writes -----------------------------------------------------

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> RunnableConfig:
        """Upsert a checkpoint document for the (thread_id, checkpoint_id) pair."""
        thread_id = _thread_id(config)
        checkpoint_id = checkpoint["id"]
        parent_id = config.get("configurable", {}).get("checkpoint_id")
        doc = {
            "id": f"{thread_id}:{checkpoint_id}",
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_id,
            "ts": datetime.now(UTC).isoformat(),
            # Cosmos rejects raw bytes/objects; serialize through JSON.
            # LangGraph's Checkpoint/CheckpointMetadata are TypedDict
            # of JSON-compatible primitives, so this is a clean dump.
            "checkpoint": json.loads(json.dumps(checkpoint, default=str)),
            "metadata": json.loads(json.dumps(metadata, default=str)),
        }
        self._container.upsert_item(doc)
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Intermediate writes — required by BaseCheckpointSaver.

        For the CM-28 hello-world path we don't need intermediate write
        durability (every node returns synchronously). Stub as no-op;
        CM-31 / CM-32 may add real handling.
        """
        # Documented as "future work" in the plan; the hello-world path
        # uses end-of-step put() so this isn't on the hot path.
        _log.debug("put_writes (no-op stub) for task=%s", task_id)

    # ---- sync reads ------------------------------------------------------

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Read the most recent checkpoint for the configured thread."""
        thread_id = _thread_id(config)
        # Cosmos SQL is the simplest way to filter to one partition + order.
        query = (
            "SELECT TOP 1 * FROM c WHERE c.thread_id = @tid ORDER BY c.ts DESC"
        )
        params: list[dict[str, object]] = [{"name": "@tid", "value": thread_id}]
        try:
            results = list(
                self._container.query_items(
                    query=query,
                    parameters=params,
                    partition_key=thread_id,
                )
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("get_tuple Cosmos query failed: %s", e)
            return None
        if not results:
            return None
        doc = results[0]
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": doc["thread_id"],
                    "checkpoint_id": doc["checkpoint_id"],
                }
            },
            checkpoint=doc["checkpoint"],
            metadata=doc["metadata"],
            parent_config=(
                {
                    "configurable": {
                        "thread_id": doc["thread_id"],
                        "checkpoint_id": doc["parent_checkpoint_id"],
                    }
                }
                if doc.get("parent_checkpoint_id")
                else None
            ),
        )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints for a thread, newest first.

        ``filter`` and ``before`` arguments are part of the
        ``BaseCheckpointSaver`` interface but not used by the CM-28
        hello-world path; ignoring them is documented and tested.
        """
        if config is None:
            return iter([])
        thread_id = _thread_id(config)
        sql_limit = f"TOP {int(limit)} " if limit else ""
        query = (
            f"SELECT {sql_limit}* FROM c WHERE c.thread_id = @tid "
            "ORDER BY c.ts DESC"
        )
        params: list[dict[str, object]] = [{"name": "@tid", "value": thread_id}]
        try:
            results = list(
                self._container.query_items(
                    query=query,
                    parameters=params,
                    partition_key=thread_id,
                )
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("list Cosmos query failed: %s", e)
            return iter([])
        return iter(
            CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": doc["thread_id"],
                        "checkpoint_id": doc["checkpoint_id"],
                    }
                },
                checkpoint=doc["checkpoint"],
                metadata=doc["metadata"],
                parent_config=(
                    {
                        "configurable": {
                            "thread_id": doc["thread_id"],
                            "checkpoint_id": doc["parent_checkpoint_id"],
                        }
                    }
                    if doc.get("parent_checkpoint_id")
                    else None
                ),
            )
            for doc in results
        )

    # ---- async variants — out of scope for CM-28 ------------------------

    async def aput(self, *args: Any, **kwargs: Any) -> RunnableConfig:
        raise NotImplementedError(
            "Async checkpointing not implemented for CM-28; use sync put()."
        )

    async def aget_tuple(self, *args: Any, **kwargs: Any) -> CheckpointTuple | None:
        raise NotImplementedError(
            "Async checkpointing not implemented for CM-28; use sync get_tuple()."
        )

    def alist(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[CheckpointTuple]:
        raise NotImplementedError(
            "Async checkpointing not implemented for CM-28; use sync list()."
        )


def _thread_id(config: RunnableConfig) -> str:
    """Extract ``thread_id`` from a LangGraph runtime config dict.

    Raises ``KeyError`` if missing — the caller forgot to pass
    ``config={'configurable': {'thread_id': ...}}`` to ``graph.invoke()``.
    Surfacing this loudly is better than silently using a default that
    would mix unrelated checkpoint streams.
    """
    return str(config["configurable"]["thread_id"])
