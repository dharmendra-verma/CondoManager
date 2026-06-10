#!/usr/bin/env python
"""Migrate the policy RAG corpus from Cosmos ``policies-vector`` to Azure AI Search (CM-100).

One-shot/idempotent operator tool: reads every chunk for the configured policy
partition(s) out of Cosmos and upserts them into the AI Search ``condo-policies``
index (creating the index first). This is the ingestion path that populates the
new store from the data that already exists — distinct from the future Drive→sync
dual-write. Re-runnable: ``ensure_policy_index`` is create-or-update and
``upsert_chunks`` merges by a deterministic key.

Usage (operator, with `az login` for Cosmos data-plane RBAC)::

    export COSMOS_ENDPOINT="https://cosmos-condomanager-prod.documents.azure.com:443/"
    export AZURE_SEARCH_ENDPOINT="https://comossearch.search.windows.net"
    export AZURE_SEARCH_KEY="<admin-key>"            # from KV azure-search-key
    export AZURE_SEARCH_INDEX="condo-policies"       # optional, this is the default
    export POLICY_TENANT_ID="TEN-6404bdef-..."       # partition(s) holding policies
    python infra/scripts/migrate-policies-to-ai-search.py
"""

from __future__ import annotations

import os
import sys

from agents.knowledge.ai_search_store import (
    DEFAULT_SEARCH_INDEX,
    AISearchStore,
    ensure_policy_index,
)
from agents.knowledge.cosmos_store import _policy_partitions_from_env
from agents.knowledge.models import DATABASE_NAME, VECTOR_CONTAINER, VectorChunk


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val or val == "REPLACE-ME":
        sys.exit(f"ERROR: {name} is required (unset or placeholder).")
    return val


def _read_cosmos_chunks(endpoint: str, partitions: list[str]) -> list[VectorChunk]:
    """Read all chunks for the given tenant partition(s) from policies-vector."""
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential

    cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    container = (
        CosmosClient(url=endpoint, credential=cred)
        .get_database_client(DATABASE_NAME)
        .get_container_client(VECTOR_CONTAINER)
    )
    chunks: list[VectorChunk] = []
    for tenant in partitions:
        rows = container.query_items(
            query="SELECT * FROM c WHERE c.tenantId = @t",
            parameters=[{"name": "@t", "value": tenant}],
            partition_key=tenant,
        )
        for row in rows:
            chunks.append(VectorChunk.model_validate(row))
    return chunks


def main() -> None:
    cosmos_endpoint = _require("COSMOS_ENDPOINT")
    search_endpoint = _require("AZURE_SEARCH_ENDPOINT")
    search_key = _require("AZURE_SEARCH_KEY")
    index_name = os.environ.get("AZURE_SEARCH_INDEX", "").strip() or DEFAULT_SEARCH_INDEX

    partitions = _policy_partitions_from_env()
    if not partitions:
        sys.exit("ERROR: POLICY_TENANT_ID must name the policy partition(s) to migrate.")

    print(f"Reading chunks from Cosmos {VECTOR_CONTAINER} for partitions: {partitions}")
    chunks = _read_cosmos_chunks(cosmos_endpoint, partitions)
    print(f"  read {len(chunks)} chunk(s)")
    if not chunks:
        sys.exit("Nothing to migrate.")

    print(f"Ensuring AI Search index '{index_name}' on {search_endpoint}")
    ensure_policy_index(
        endpoint=search_endpoint, api_key=search_key, index_name=index_name
    )

    store = AISearchStore(
        endpoint=search_endpoint, api_key=search_key, index_name=index_name
    )
    store.upsert_chunks(chunks)
    print(f"Upserted {len(chunks)} chunk(s) into '{index_name}'. Done.")


if __name__ == "__main__":
    main()
