#!/usr/bin/env python3
"""Manually ingest a local folder of policy files into Cosmos policies-vector.

Jira: CM-66  | Epic: CM-Epic 9 (Knowledge ingestion)  | Phase 1

Walks a local folder and ingests every supported file (.txt / .md / .pdf) as a
policy document through the EXISTING knowledge pipeline (chunk -> embed ->
upsert into `policies-vector`), reusing agents.knowledge.run_sync via a
filesystem DriveClientProto. No parallel write path.

The folder is the authoritative policy set: by default a run MIRRORS it
exactly -- files removed from the folder have their chunks purged from Cosmos.
Use --additive to disable pruning (upsert/update only).

Usage:
    # Required env (no secrets in this script; Cosmos uses DefaultAzureCredential):
    export COSMOS_ENDPOINT="https://cosmos-condomanager-dev.documents.azure.com:443/"
    export AZURE_OPENAI_ENDPOINT="https://<aoai>.openai.azure.com/"
    export AZURE_OPENAI_API_KEY="<key>"            # sourced from KV azure-openai-key
    # optional: AZURE_OPENAI_EMBEDDING_DEPLOYMENT (default text-embedding-3-small)

    # Mirror a folder into a tenant's knowledge base:
    python infra/scripts/ingest-local-folder.py --tenant t-acme --folder ./policies

    # Preview first (no writes, no embedding spend) -- recommended for a new folder:
    python infra/scripts/ingest-local-folder.py --tenant t-acme --folder ./policies --dry-run

    # Additive top-up (do not delete docs missing from the folder):
    python infra/scripts/ingest-local-folder.py --tenant t-acme --folder ./policies --additive

Supported formats: .txt, .md, .pdf -- all work out of the box (CM-67 made PDF
text extraction a core dependency; no extra install step). OCR of scanned /
image-only PDFs is out of scope (text-extractable PDFs only).

Auth: DefaultAzureCredential (env vars -> Managed Identity -> `az login`).
Exit codes: 0 = success (no failed docs), 1 = bad args / unconfigured / a doc failed.

Run from the repository root so the `agents` package is importable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.knowledge import (  # noqa: E402  (after sys.path bootstrap)
    chunk_text,
    default_embedder,
    default_source,
    get_vector_store,
)
from agents.knowledge.local_source import LocalFolderClient, ingest_folder  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a local folder of policy files into Cosmos policies-vector.",
    )
    parser.add_argument("--tenant", required=True, help="Tenant id (Cosmos partition key).")
    parser.add_argument("--folder", required=True, help="Local folder to ingest.")
    parser.add_argument(
        "--source",
        default=None,
        help="State key (default: local:<resolved-folder>). Keep stable per folder.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files, chunk counts, and the would-prune set; no Cosmos/embedder writes.",
    )
    parser.add_argument(
        "--additive",
        action="store_true",
        help="Do NOT prune docs missing from the folder (upsert/update only).",
    )
    return parser.parse_args(argv)


def _resolve_folder(raw: str) -> Path | None:
    folder = Path(raw).expanduser()
    if not folder.exists() or not folder.is_dir():
        sys.stderr.write(f"ERROR: --folder is not a directory: {folder}\n")
        return None
    return folder.resolve()


def _dry_run(tenant: str, folder: Path, source: str, prune: bool) -> int:
    """Preview ingestion without touching the embedder or writing to Cosmos."""
    # Prune preview needs prior state; reading it needs COSMOS_ENDPOINT. If the
    # store is unavailable we still preview the to-ingest set (no traceback).
    store = get_vector_store()
    prior_doc_ids: set[str] = set()
    prune_preview_available = store is not None and prune
    if store is not None:
        prior_doc_ids = set(store.load_state(source).docs)

    client = LocalFolderClient(folder, prior_doc_ids=prior_doc_ids, prune=prune)
    present = client.scan_present()

    print("DRY RUN -- no Cosmos writes, no embedding calls")
    print(f"-> Tenant:    {tenant}")
    print(f"-> Folder:    {folder}")
    print(f"-> Source:    {source}")
    print("-> Container: policies-vector (db condomanager)")
    print(f"-> Mode:      {'mirror (prune)' if prune else 'additive'}")
    print()

    total_chunks = 0
    print(f"Would ingest {len(present)} file(s):")
    for change in present:
        text = client.export_text(change)
        n = len(chunk_text(text))
        total_chunks += n
        print(f"  [ingest] {change.file_id}  ({n} chunk(s), {change.mime_type})")
    print(f"  -> {total_chunks} chunk(s) total")

    if client.skipped_unsupported:
        print(f"\nWould skip {len(client.skipped_unsupported)} unsupported file(s):")
        for rel in client.skipped_unsupported:
            print(f"  [skip]   {rel}")

    if prune:
        present_ids = {c.file_id for c in present}
        would_prune = sorted(prior_doc_ids - present_ids)
        if not prune_preview_available:
            print(
                "\nPrune preview unavailable (COSMOS_ENDPOINT unset) -- set it to see "
                "which previously-ingested docs would be deleted."
            )
        elif would_prune:
            print(f"\nWould PRUNE {len(would_prune)} doc(s) no longer in the folder:")
            for doc_id in would_prune:
                print(f"  [prune]  {doc_id}")
        else:
            print("\nWould prune 0 docs (folder matches prior state).")

    return 0


def _live_run(tenant: str, folder: Path, source: str, prune: bool) -> int:
    store = get_vector_store()
    if store is None:
        sys.stderr.write(
            "ERROR: COSMOS_ENDPOINT is not set (or is REPLACE-ME).\n"
            "  export COSMOS_ENDPOINT="
            "'https://cosmos-condomanager-dev.documents.azure.com:443/'\n"
            "  (or use --dry-run to preview without Cosmos)\n"
        )
        return 1
    embedder = default_embedder()
    if embedder is None:
        sys.stderr.write(
            "ERROR: Azure OpenAI embedding is not configured.\n"
            "  Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY "
            "(and optionally AZURE_OPENAI_EMBEDDING_DEPLOYMENT).\n"
        )
        return 1

    print(f"-> Tenant:    {tenant}")
    print(f"-> Folder:    {folder}")
    print(f"-> Source:    {source}")
    print(f"-> Mode:      {'mirror (prune)' if prune else 'additive'}")
    print()

    try:
        report, client = ingest_folder(
            tenant_id=tenant,
            folder=folder,
            store=store,
            embedder=embedder,
            source=source,
            prune=prune,
        )
    except Exception as exc:  # noqa: BLE001 - operator CLI: report, don't traceback
        sys.stderr.write(f"ERROR: ingestion failed: {exc}\n")
        return 1

    if prune and client.pruned_doc_ids:
        print(f"Pruned {len(client.pruned_doc_ids)} doc(s) no longer in the folder:")
        for doc_id in client.pruned_doc_ids:
            print(f"  [prune]  {doc_id}")

    seen = report.changed + report.skipped + len(client.skipped_unsupported)
    print(
        "\nSummary:\n"
        f"  files seen:           {seen}\n"
        f"  ingested (new/changed): {report.changed}  ({report.chunks_written} chunk(s))\n"
        f"  skipped (unchanged):  {report.skipped}\n"
        f"  skipped (unsupported): {len(client.skipped_unsupported)}\n"
        f"  pruned (removed):     {report.removed}\n"
        f"  failed:               {report.failed}"
    )
    if report.failed:
        sys.stderr.write(f"\nFAIL: {report.failed} document(s) failed to ingest.\n")
        return 1
    print("\nPASS: folder ingested.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    folder = _resolve_folder(args.folder)
    if folder is None:
        return 1
    source = args.source or default_source(folder)
    prune = not args.additive

    if args.dry_run:
        return _dry_run(args.tenant, folder, source, prune)
    return _live_run(args.tenant, folder, source, prune)


if __name__ == "__main__":
    sys.exit(main())
