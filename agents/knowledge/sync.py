"""Drive → Cosmos sync orchestration (CM-34).

Jira: CM-34  | Epic: CM-Epic 9  | Phase 1

:func:`run_sync` is the heart of the story. It depends only on three
Protocols (Drive client, vector store, embedder) so it is fully unit-
testable with in-memory fakes — the Function App (``functions/gdrive-sync``)
wires the real implementations.

One pass:

1. Load durable :class:`SyncState` (page token + per-doc hashes).
2. Bootstrap (no token) → enumerate the folder; else → Changes-API delta.
3. Per changed doc: export text → hash → skip if unchanged → chunk → embed
   → versioned, deterministic-id upsert → delete stale trailing chunks.
4. Removed/trashed docs → purge their chunks.
5. Persist the new page token + hashes.
6. Emit ``gdrive_sync.doc`` (per-doc) and ``gdrive_sync.run`` (summary)
   structured logs → Log Analytics (observability AC).

Idempotency: deterministic chunk ids + content-hash skip mean a retry of a
mid-run failure re-processes from the last *saved* state and upserts the
same ids — never duplicate chunks.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Protocol

from agents.knowledge.chunking import chunk_text
from agents.knowledge.embeddings import Embedder
from agents.knowledge.models import (
    DocState,
    DocSyncResult,
    DriveChange,
    SyncReport,
    SyncState,
    VectorChunk,
    chunk_id,
)

_log = logging.getLogger(__name__)


class DriveClientProto(Protocol):
    """Structural contract satisfied by ``GoogleDriveClient`` (and fakes)."""

    def get_start_page_token(self) -> str: ...
    def list_changes(self, page_token: str) -> tuple[list[DriveChange], str]: ...
    def list_folder(self, folder_id: str) -> list[DriveChange]: ...
    def export_text(self, change: DriveChange) -> str: ...


class VectorStoreProto(Protocol):
    """Structural contract satisfied by ``CosmosVectorStore`` (and fakes)."""

    def load_state(self, source: str) -> SyncState: ...
    def save_state(self, state: SyncState) -> None: ...
    def upsert_chunks(self, chunks: list[VectorChunk]) -> None: ...
    def delete_stale_chunks(
        self, tenant_id: str, doc_id: str, *, valid_count: int
    ) -> int: ...
    def delete_doc_chunks(self, tenant_id: str, doc_id: str) -> int: ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def run_sync(
    *,
    tenant_id: str,
    source_folder_id: str,
    drive: DriveClientProto,
    store: VectorStoreProto,
    embedder: Embedder,
    source: str = "gdrive",
    run_id: str | None = None,
) -> SyncReport:
    """Run one sync pass and return a :class:`SyncReport`.

    Args:
        tenant_id: Tenant the synced docs belong to (Cosmos partition key).
        source_folder_id: Drive folder id to watch (used on bootstrap).
        drive: Drive client (Changes API + text export).
        store: Cosmos vector + state store.
        embedder: Chunk → vector embedder.
        source: Logical source key for the state doc (default ``"gdrive"``).
        run_id: Correlation id for this run; generated when omitted.
    """
    rid = run_id or uuid.uuid4().hex
    started = time.monotonic()

    state = store.load_state(source)
    bootstrap = state.page_token is None
    if bootstrap:
        changes = drive.list_folder(source_folder_id)
        next_token = drive.get_start_page_token()
    else:
        # page_token is non-None here (bootstrap is False).
        changes, next_token = drive.list_changes(state.page_token or "")

    report = SyncReport(
        run_id=rid, tenant_id=tenant_id, source=source, bootstrap=bootstrap
    )
    for change in changes:
        result = _process_change(
            change,
            drive=drive,
            store=store,
            embedder=embedder,
            tenant_id=tenant_id,
            source=source,
            state=state,
        )
        report.results.append(result)
        _tally(report, result)
        _log.info(
            "gdrive_sync.doc run_id=%s source=%s doc_id=%s status=%s "
            "chunks=%d version=%d",
            rid,
            source,
            result.doc_id,
            result.status,
            result.chunks,
            result.version,
        )

    state.page_token = next_token
    state.updated_ts = _now_iso()
    store.save_state(state)

    duration_ms = (time.monotonic() - started) * 1000
    _log.info(
        "gdrive_sync.run run_id=%s source=%s bootstrap=%s changed=%d skipped=%d "
        "removed=%d failed=%d chunks=%d duration_ms=%.1f",
        rid,
        source,
        bootstrap,
        report.changed,
        report.skipped,
        report.removed,
        report.failed,
        report.chunks_written,
        duration_ms,
    )
    return report


def _process_change(
    change: DriveChange,
    *,
    drive: DriveClientProto,
    store: VectorStoreProto,
    embedder: Embedder,
    tenant_id: str,
    source: str,
    state: SyncState,
) -> DocSyncResult:
    """Process one Drive change end-to-end into a :class:`DocSyncResult`."""
    doc_id = change.file_id
    prior = state.docs.get(doc_id)

    if change.removed:
        deleted = store.delete_doc_chunks(tenant_id, doc_id)
        state.docs.pop(doc_id, None)
        return DocSyncResult(
            doc_id=doc_id, title=change.title, status="removed", chunks=deleted
        )

    # Export is the most failure-prone step (network, permissions, binary
    # files) — isolate it so one bad doc doesn't abort the whole run.
    try:
        text = drive.export_text(change)
    except Exception as exc:  # noqa: BLE001 - per-doc isolation; run continues
        _log.warning("gdrive_sync export failed doc_id=%s: %s", doc_id, exc)
        return DocSyncResult(
            doc_id=doc_id, title=change.title, status="failed", error=str(exc)
        )

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if prior is not None and prior.content_hash == content_hash:
        # Drive reported a change but the text is identical (e.g. a sharing
        # or metadata edit) — skip before spending on embeddings.
        return DocSyncResult(
            doc_id=doc_id, title=change.title, status="skipped", version=prior.version
        )

    version = prior.version + 1 if prior is not None else 1
    chunk_texts = chunk_text(text)

    if not chunk_texts:
        # Doc emptied — drop any existing chunks, record the new hash so we
        # don't re-process it next run.
        store.delete_doc_chunks(tenant_id, doc_id)
        state.docs[doc_id] = DocState(
            content_hash=content_hash, version=version, title=change.title
        )
        return DocSyncResult(
            doc_id=doc_id, title=change.title, status="indexed", chunks=0, version=version
        )

    vectors = embedder.embed(chunk_texts)
    if len(vectors) != len(chunk_texts):
        # Contract violation, not a transient error — fail loudly rather than
        # writing text/vector pairs that don't correspond.
        raise ValueError(
            f"embedding count {len(vectors)} != chunk count "
            f"{len(chunk_texts)} for doc {doc_id}"
        )

    ts = _now_iso()
    chunks = [
        VectorChunk(
            id=chunk_id(tenant_id, doc_id, i),
            tenantId=tenant_id,
            doc_id=doc_id,
            doc_title=change.title,
            chunk_index=i,
            text=text_i,
            embedding=vectors[i],
            content_hash=content_hash,
            doc_version=version,
            source=source,
            ts=ts,
        )
        for i, text_i in enumerate(chunk_texts)
    ]
    store.upsert_chunks(chunks)
    store.delete_stale_chunks(tenant_id, doc_id, valid_count=len(chunks))
    state.docs[doc_id] = DocState(
        content_hash=content_hash, version=version, title=change.title
    )
    return DocSyncResult(
        doc_id=doc_id,
        title=change.title,
        status="indexed",
        chunks=len(chunks),
        version=version,
    )


def _tally(report: SyncReport, result: DocSyncResult) -> None:
    if result.status == "indexed":
        report.changed += 1
        report.chunks_written += result.chunks
    elif result.status == "skipped":
        report.skipped += 1
    elif result.status == "removed":
        report.removed += 1
    elif result.status == "failed":
        report.failed += 1
