"""Shared in-memory fakes for ``tests/knowledge/`` (CM-34).

The sync orchestrator (`agents.knowledge.run_sync`) depends only on three
Protocols, so these fakes let every behavior be asserted offline — no
Google API, no Cosmos, no OpenAI.
"""

from __future__ import annotations

from agents.knowledge.models import DriveChange, SyncState, VectorChunk


class FakeDrive:
    """Scriptable Drive double.

    ``folder`` is returned by ``list_folder`` (bootstrap). ``change_batches``
    is a queue: each ``list_changes`` call pops the next (changes, token)
    pair. ``texts`` maps file_id → exported text; a missing id (or an id in
    ``raise_on``) drives the export-failure path.
    """

    def __init__(
        self,
        *,
        folder: list[DriveChange] | None = None,
        change_batches: list[tuple[list[DriveChange], str]] | None = None,
        texts: dict[str, str] | None = None,
        raise_on: set[str] | None = None,
        start_token: str = "token-0",
    ) -> None:
        self.folder = folder or []
        self.change_batches = list(change_batches or [])
        self.texts = texts or {}
        self.raise_on = raise_on or set()
        self.start_token = start_token
        self.export_calls: list[str] = []

    def get_start_page_token(self) -> str:
        return self.start_token

    def list_folder(self, folder_id: str) -> list[DriveChange]:
        return list(self.folder)

    def list_changes(self, page_token: str) -> tuple[list[DriveChange], str]:
        if self.change_batches:
            return self.change_batches.pop(0)
        return [], page_token

    def export_text(self, change: DriveChange) -> str:
        self.export_calls.append(change.file_id)
        if change.file_id in self.raise_on:
            raise RuntimeError(f"export boom for {change.file_id}")
        return self.texts.get(change.file_id, "")


class FakeStore:
    """In-memory stand-in for ``CosmosVectorStore``.

    ``chunks`` maps chunk id → :class:`VectorChunk`. ``state`` maps source →
    :class:`SyncState`. Counters expose how often the orchestrator wrote, so
    idempotency can be asserted directly.
    """

    def __init__(self) -> None:
        self.chunks: dict[str, VectorChunk] = {}
        self.state: dict[str, SyncState] = {}
        self.upsert_calls = 0

    def load_state(self, source: str) -> SyncState:
        existing = self.state.get(source)
        # Return a copy so the orchestrator mutating it doesn't retroactively
        # change what a prior run "persisted" until save_state is called.
        return existing.model_copy(deep=True) if existing else SyncState(source=source)

    def save_state(self, state: SyncState) -> None:
        self.state[state.source] = state.model_copy(deep=True)

    def upsert_chunks(self, chunks: list[VectorChunk]) -> None:
        self.upsert_calls += 1
        for chunk in chunks:
            self.chunks[chunk.id] = chunk

    def delete_stale_chunks(
        self, tenant_id: str, doc_id: str, *, valid_count: int
    ) -> int:
        return self._delete(tenant_id, doc_id, min_index=valid_count)

    def delete_doc_chunks(self, tenant_id: str, doc_id: str) -> int:
        return self._delete(tenant_id, doc_id, min_index=0)

    def _delete(self, tenant_id: str, doc_id: str, *, min_index: int) -> int:
        victims = [
            cid
            for cid, c in self.chunks.items()
            if c.tenantId == tenant_id
            and c.doc_id == doc_id
            and c.chunk_index >= min_index
        ]
        for cid in victims:
            del self.chunks[cid]
        return len(victims)

    # ---- test helpers ----
    def chunks_for(self, doc_id: str) -> list[VectorChunk]:
        return sorted(
            (c for c in self.chunks.values() if c.doc_id == doc_id),
            key=lambda c: c.chunk_index,
        )


class FakeEmbedder:
    """1:1 embedder returning a tiny deterministic vector per chunk.

    ``vectors_per_text`` lets a test force a count mismatch (the orchestrator
    must raise rather than misalign).
    """

    def __init__(self, *, vectors_per_text: int = 1) -> None:
        self.vectors_per_text = vectors_per_text
        self.embed_calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        out: list[list[float]] = []
        for i, _ in enumerate(texts):
            out.extend([[float(i), 0.5]] * self.vectors_per_text)
        return out
