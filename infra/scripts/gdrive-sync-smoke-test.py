#!/usr/bin/env python3
"""Post-deploy smoke test for the Drive → Cosmos sync (CM-34).

Exercises the real ``CosmosVectorStore`` against a deployed Cosmos account
using an in-process fake Drive client + a stub embedder, so it validates the
Cosmos wiring (policies-vector + knowledge_sync containers, MI data-plane
access) AND the end-to-end sync algorithm WITHOUT needing a real Google
service account.

Asserts the two CM-34 properties that are hard to unit-test against the real
store:
  * a bootstrap run writes chunks + persists a page token, and
  * an immediate re-run is idempotent (unchanged doc → skipped, no duplicate
    chunks).

Run AFTER `az deployment group create` succeeds (mirrors cosmos-smoke-test.py):

    pip install "azure-cosmos>=4.7.0" "azure-identity>=1.15.0" "pydantic>=2.7"
    COSMOS_ENDPOINT=$(az cosmosdb show -g rg-condomanager \
        -n cosmos-condomanager-dev --query documentEndpoint -o tsv)
    export COSMOS_ENDPOINT
    python infra/scripts/gdrive-sync-smoke-test.py

Auth uses DefaultAzureCredential, so it works locally (az login) and from any
principal granted the Cosmos data-plane role. Exit 0 == healthy.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Make the repo's `agents` package importable when run from a clone.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.knowledge import CosmosVectorStore, DriveChange, run_sync  # noqa: E402

SMOKE_TENANT = f"smoke-{uuid.uuid4().hex[:8]}"
SMOKE_SOURCE = f"gdrive-smoke-{uuid.uuid4().hex[:8]}"
SMOKE_DOC_ID = f"doc-{uuid.uuid4().hex[:8]}"
SMOKE_TEXT = (
    "CondoManager smoke-test policy document. " * 80
)  # long enough to chunk


class _FakeDrive:
    """Minimal Drive double: one doc on bootstrap, no changes afterwards."""

    def __init__(self) -> None:
        self._doc = DriveChange(
            file_id=SMOKE_DOC_ID, title="Smoke Policy", mime_type="text/plain"
        )

    def get_start_page_token(self) -> str:
        return "smoke-token-1"

    def list_folder(self, folder_id: str) -> list[DriveChange]:
        return [self._doc]

    def list_changes(self, page_token: str) -> tuple[list[DriveChange], str]:
        return [], "smoke-token-2"

    def export_text(self, change: DriveChange) -> str:
        return SMOKE_TEXT


class _StubEmbedder:
    """Returns deterministic 1536-dim vectors (matches policies-vector dims)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.001 * (i + 1)] * 1536 for i, _ in enumerate(texts)]


def main() -> int:
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint:
        print("COSMOS_ENDPOINT not set — export it before running.", file=sys.stderr)
        return 2

    store = CosmosVectorStore(endpoint=endpoint)
    drive = _FakeDrive()
    embedder = _StubEmbedder()

    try:
        print(f">  Bootstrap run (tenant={SMOKE_TENANT}, source={SMOKE_SOURCE})")
        first = run_sync(
            tenant_id=SMOKE_TENANT,
            source_folder_id="smoke-folder",
            drive=drive,
            store=store,
            embedder=embedder,
            source=SMOKE_SOURCE,
            run_id="smoke-1",
        )
        assert first.bootstrap is True, "first run should be a bootstrap"
        assert first.changed == 1, f"expected 1 changed, got {first.changed}"
        assert first.chunks_written > 0, "bootstrap wrote no chunks"
        print(f"   OK bootstrap indexed 1 doc, {first.chunks_written} chunks")

        print(">  Idempotent re-run (no changes)")
        second = run_sync(
            tenant_id=SMOKE_TENANT,
            source_folder_id="smoke-folder",
            drive=drive,
            store=store,
            embedder=embedder,
            source=SMOKE_SOURCE,
            run_id="smoke-2",
        )
        assert second.bootstrap is False, "second run should use the saved token"
        assert second.changed == 0, f"re-run re-indexed (changed={second.changed})"
        assert second.chunks_written == 0, "re-run wrote chunks (not idempotent)"
        print("   OK re-run was a clean no-op (idempotent)")
    finally:
        # Best-effort cleanup so the smoke run leaves no residue.
        try:
            removed = store.delete_doc_chunks(SMOKE_TENANT, SMOKE_DOC_ID)
            print(f">  Cleanup: removed {removed} smoke chunks")
        except Exception as exc:  # noqa: BLE001
            print(f"   (cleanup warning: {exc})", file=sys.stderr)

    print("PASS: Drive → Cosmos sync smoke-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
