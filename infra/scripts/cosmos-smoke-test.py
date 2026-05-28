#!/usr/bin/env python3
"""Post-deploy smoke-test for CM-17 — Cosmos DB vector search.

Validates the last acceptance criterion of CM-17 ("Sample insert + vector
query validated") by:

  1. Inserting a single policy document with a dummy 1536-dimension
     embedding into the `policies-vector` container.
  2. Issuing a `VectorDistance()` query against the same container.
  3. Asserting the inserted document is returned as the nearest neighbour.
  4. Deleting the document so the smoke-test leaves no residue.

Not run in CI — needs a live Cosmos account. Run locally or from a
deployment pipeline AFTER `az deployment group create` succeeds.

Usage:
    # Recommended — env vars (no secrets on the command line / in history):
    export COSMOS_ENDPOINT="https://cosmos-condomanager-dev.documents.azure.com:443/"
    export COSMOS_KEY="<primary-key>"
    python infra/scripts/cosmos-smoke-test.py

    # Or pass explicitly:
    python infra/scripts/cosmos-smoke-test.py \\
        --endpoint https://cosmos-condomanager-dev.documents.azure.com:443/ \\
        --key <primary-key>

Exit codes: 0 = pass, 1 = fail.

Dependencies:
    pip install "azure-cosmos>=4.7.0"
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

try:
    from azure.cosmos import CosmosClient, PartitionKey, exceptions
except ImportError:
    sys.stderr.write(
        "azure-cosmos is required. Install with: pip install 'azure-cosmos>=4.7.0'\n"
    )
    sys.exit(1)


DATABASE_NAME = "condomanager"
CONTAINER_NAME = "policies-vector"
VECTOR_DIM = 1536


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cosmos DB vector-search smoke-test (CM-17).")
    p.add_argument(
        "--endpoint",
        default=os.environ.get("COSMOS_ENDPOINT"),
        help="Cosmos account endpoint URL (or COSMOS_ENDPOINT env var).",
    )
    p.add_argument(
        "--key",
        default=os.environ.get("COSMOS_KEY"),
        help="Cosmos account primary key (or COSMOS_KEY env var).",
    )
    p.add_argument(
        "--database",
        default=DATABASE_NAME,
        help=f"Cosmos database name (default: {DATABASE_NAME}).",
    )
    p.add_argument(
        "--container",
        default=CONTAINER_NAME,
        help=f"Cosmos container name (default: {CONTAINER_NAME}).",
    )
    return p.parse_args()


def make_embedding(seed: int) -> list[float]:
    """Deterministic-but-not-uniform embedding so the inserted doc beats other
    rows (if any) under cosine distance. Uses a tiny LCG to avoid pulling in
    numpy as a dependency."""
    out: list[float] = []
    state = seed
    for _ in range(VECTOR_DIM):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((state / 0x7FFFFFFF) - 0.5)  # spread roughly in [-0.5, 0.5]
    return out


def main() -> int:
    args = parse_args()
    if not args.endpoint or not args.key:
        sys.stderr.write(
            "ERROR: endpoint and key are required (pass --endpoint/--key or set "
            "COSMOS_ENDPOINT/COSMOS_KEY).\n"
        )
        return 1

    client = CosmosClient(args.endpoint, credential=args.key)
    try:
        database = client.get_database_client(args.database)
        container = database.get_container_client(args.container)
    except exceptions.CosmosResourceNotFoundError as e:
        sys.stderr.write(f"ERROR: database/container not found: {e}\n")
        return 1

    tenant_id = "smoke-test-tenant"
    doc_id = f"smoke-{uuid.uuid4()}"
    embedding = make_embedding(seed=42)

    sample = {
        "id": doc_id,
        "tenantId": tenant_id,
        "title": "Smoke-test policy",
        "body": "Inserted by infra/scripts/cosmos-smoke-test.py — safe to delete.",
        "embedding": embedding,
    }

    print(f">  Inserting smoke-test doc id={doc_id} into {args.database}/{args.container}")
    try:
        container.create_item(body=sample)
    except exceptions.CosmosHttpResponseError as e:
        sys.stderr.write(f"ERROR: insert failed: {e.message}\n")
        return 1

    try:
        # Vector similarity query against the same embedding — the inserted
        # doc must come back as the top match (distance ≈ 0).
        query = (
            "SELECT TOP 1 c.id, c.title, "
            "VectorDistance(c.embedding, @qv) AS distance "
            "FROM c "
            "WHERE c.tenantId = @tenantId "
            "ORDER BY VectorDistance(c.embedding, @qv)"
        )
        params = [
            {"name": "@qv", "value": embedding},
            {"name": "@tenantId", "value": tenant_id},
        ]
        print(">  Running VectorDistance() query")
        results = list(
            container.query_items(
                query=query,
                parameters=params,
                partition_key=tenant_id,
            )
        )

        if not results:
            sys.stderr.write("ERROR: query returned 0 rows — expected the inserted doc.\n")
            return 1

        top = results[0]
        if top["id"] != doc_id:
            sys.stderr.write(
                f"ERROR: nearest-neighbour was id={top['id']}, expected id={doc_id}.\n"
            )
            return 1

        print(f"   OK nearest match: id={top['id']} distance={top.get('distance')}")
        print("PASS: Cosmos vector-search smoke-test")
        return 0
    finally:
        # Clean up regardless of query outcome so reruns don't accumulate junk.
        try:
            container.delete_item(item=doc_id, partition_key=tenant_id)
            print(f">  Deleted smoke-test doc id={doc_id}")
        except exceptions.CosmosResourceNotFoundError:
            pass


if __name__ == "__main__":
    sys.exit(main())
