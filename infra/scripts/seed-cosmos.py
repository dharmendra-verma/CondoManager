#!/usr/bin/env python3
"""Seed vendor roster and test tenant into Cosmos DB.

Jira: CM-54  | Phase 0

Upserts all vendors from agents.vendor.seed.seed_vendors() into the `vendors`
container and writes one test tenant into the `tenants` container. Idempotent —
safe to re-run on every deploy; existing records are updated in place.

Usage:
    # Required — set the Cosmos endpoint (no key needed; uses DefaultAzureCredential):
    export COSMOS_ENDPOINT="https://cosmos-condomanager-dev.documents.azure.com:443/"
    python infra/scripts/seed-cosmos.py

    # Optional — override the database name (default: condomanager):
    export COSMOS_DATABASE="condomanager"
    python infra/scripts/seed-cosmos.py

Auth: DefaultAzureCredential — resolves in order:
  1. Environment variables (AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID)
  2. Workload Identity (Container Apps / Functions running as Managed Identity)
  3. Azure CLI (run `az login` locally)

Exit codes: 0 = all upserts succeeded, 1 = any failure.

Dependencies:
    pip install "azure-cosmos>=4.7.0" "azure-identity>=1.15.0"

Run from the repository root so the `agents` package is importable:
    python infra/scripts/seed-cosmos.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from azure.cosmos import CosmosClient, exceptions
    from azure.identity import DefaultAzureCredential
except ImportError:
    sys.stderr.write(
        "azure-cosmos and azure-identity are required.\n"
        "Install with: pip install 'azure-cosmos>=4.7.0' 'azure-identity>=1.15.0'\n"
    )
    sys.exit(1)

try:
    from agents.vendor.seed import seed_vendors
except ImportError as e:
    sys.stderr.write(
        f"Cannot import agents.vendor.seed: {e}\n"
        "Run this script from the repository root directory.\n"
    )
    sys.exit(1)


DATABASE_NAME = os.getenv("COSMOS_DATABASE", "condomanager")

TEST_TENANT = {
    "id": "tenant-smoke-test",
    "name": "Smoke Test Building",
    "contactEmail": "smoke@example.com",
    "tier": "standard",
    "unitCount": 10,
}


def main() -> int:
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    if not endpoint:
        sys.stderr.write(
            "ERROR: COSMOS_ENDPOINT is required.\n"
            "  export COSMOS_ENDPOINT='https://cosmos-condomanager-dev.documents.azure.com:443/'\n"
        )
        return 1

    print(f"-> Endpoint: {endpoint}")
    print(f"-> Database: {DATABASE_NAME}")

    try:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        client = CosmosClient(endpoint, credential=credential)
        db = client.get_database_client(DATABASE_NAME)
    except Exception as e:
        sys.stderr.write(f"ERROR: could not build Cosmos client: {e}\n")
        return 1

    vendors_container = db.get_container_client("vendors")
    tenants_container = db.get_container_client("tenants")

    vendor_count = 0
    try:
        for vendor in seed_vendors():
            doc = vendor.model_dump()
            doc["id"] = vendor.id
            vendors_container.upsert_item(doc)
            print(f"  [upsert] vendor {vendor.id} ({vendor.name})")
            vendor_count += 1
    except exceptions.CosmosResourceNotFoundError as e:
        sys.stderr.write(
            f"ERROR: `vendors` container not found — is the Bicep deploy complete? {e}\n"
        )
        return 1
    except exceptions.CosmosHttpResponseError as e:
        sys.stderr.write(f"ERROR: upsert vendor failed: {e.message}\n")
        return 1

    try:
        tenants_container.upsert_item(TEST_TENANT)
        print(f"  [upsert] tenant {TEST_TENANT['id']}")
    except exceptions.CosmosResourceNotFoundError as e:
        sys.stderr.write(
            f"ERROR: `tenants` container not found — is the Bicep deploy complete? {e}\n"
        )
        return 1
    except exceptions.CosmosHttpResponseError as e:
        sys.stderr.write(f"ERROR: upsert tenant failed: {e.message}\n")
        return 1

    print(f"\nPASS: seeded {vendor_count} vendors, 1 test tenant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
