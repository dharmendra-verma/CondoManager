"""Local-folder policy ingestion (CM-66).

Jira: CM-66  | Epic: CM-Epic 9 (Knowledge ingestion)  | Phase 1

Lets an operator bulk-load a folder of local policy files (bylaws, rules,
SOPs handed over as ``.txt`` / ``.md`` / ``.pdf``) into Cosmos
``policies-vector`` **without** Google Drive — reusing the exact same
ingestion pipeline as the Drive sync (CM-34).

The trick is that :func:`agents.knowledge.run_sync` depends only on the
structural :class:`~agents.knowledge.sync.DriveClientProto`, so this module
just provides a *filesystem* implementation of that contract
(:class:`LocalFolderClient`) and a thin :func:`ingest_folder` wiring it to a
vector store + embedder. No change to ``run_sync`` — content-hash
skip-unchanged, deterministic chunk ids, per-doc versioning, stale-chunk
cleanup and per-doc error isolation all come for free.

Semantics (CM-66): the folder **is** the authoritative policy set, so a run
mirrors it exactly — files removed from the folder have their chunks purged
from Cosmos (``prune=True``, the default). ``prune=False`` is the additive
top-up mode. A manual run is always a full folder re-scan; idempotency comes
from the content-hash skip in ``run_sync``, not from Drive-style delta tokens.

PDF text extraction uses ``pypdf``, a **core** runtime dependency since CM-67
(it was the optional ``[ingest]`` extra in CM-66). It is still lazy-imported,
and the defensive "``pypdf`` missing → report the ``.pdf`` as *unsupported*
(skipped, logged) rather than crash" path is kept as a safety net for broken or
partial installs — but on a normal install ``.pdf`` is a first-class format.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from agents.knowledge.embeddings import Embedder
from agents.knowledge.models import DriveChange, SyncReport
from agents.knowledge.sync import VectorStoreProto, run_sync

_log = logging.getLogger(__name__)

#: Extension → synthetic mime tag. Mirrors ``DriveChange.mime_type`` so the
#: rest of the pipeline (which only inspects the path on export) is unchanged.
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
}

#: Plain-text extensions read directly; ``.pdf`` goes through :func:`_extract_pdf`.
_TEXT_EXTENSIONS = {".txt", ".md"}


def default_source(folder: Path) -> str:
    """Stable, Drive-distinct state key for a folder (``local:<abs-path>``).

    The ``source`` is the partition key of the ``knowledge_sync`` state doc
    and MUST be identical across runs of the same folder for idempotency to
    hold; the ``local:`` prefix keeps it from ever colliding with the Drive
    sync's ``"gdrive"`` state.
    """
    return f"local:{folder.resolve().as_posix()}"


def _pypdf_available() -> bool:
    """Whether ``pypdf`` is importable (a core dep since CM-67; guarded so a
    broken/partial install degrades to skip-the-pdf rather than crashing)."""
    return importlib.util.find_spec("pypdf") is not None


def _extract_pdf(path: Path) -> str:
    """Extract text from a (text-based) PDF via ``pypdf``.

    Lazy-imports ``pypdf`` so the base runtime + test env don't need it.
    Image-only / scanned PDFs yield little or no text (OCR is out of scope);
    that surfaces as a doc with zero chunks, not an error.
    """
    from pypdf import PdfReader  # noqa: PLC0415

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class LocalFolderClient:
    """A :class:`DriveClientProto` backed by a local folder (CM-66).

    Recursively enumerates supported files under ``root``; each file becomes a
    :class:`DriveChange` keyed by its path **relative to ``root``** (a stable,
    human-readable ``doc_id``). When ``prune`` is set, any ``doc_id`` present
    in ``prior_doc_ids`` but no longer on disk is surfaced as a removal so the
    orchestrator purges its chunks — mirroring the folder exactly.

    Unsupported extensions (and ``.pdf`` when ``pypdf`` is absent) are dropped
    from the scan and recorded in :attr:`skipped_unsupported`; they never reach
    the per-doc pipeline.
    """

    def __init__(
        self,
        root: Path,
        *,
        prior_doc_ids: set[str] | None = None,
        prune: bool = True,
    ) -> None:
        self.root = root
        self.prior_doc_ids: set[str] = set(prior_doc_ids) if prior_doc_ids else set()
        self.prune = prune
        #: Relative paths skipped because the extension isn't supported (or a
        #: ``.pdf`` was found without ``pypdf``). Populated on each scan.
        self.skipped_unsupported: list[str] = []
        #: ``doc_id``s that would be / were pruned this run. Populated on scan.
        self.pruned_doc_ids: list[str] = []

    # ---- scan -----------------------------------------------------------

    def scan_present(self) -> list[DriveChange]:
        """All supported files under ``root`` as non-removed changes.

        Side effect: resets and fills :attr:`skipped_unsupported`.
        """
        self.skipped_unsupported = []
        pdf_ok = _pypdf_available()
        changes: list[DriveChange] = []
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            rel = path.relative_to(self.root).as_posix()
            ext = path.suffix.lower()
            mime = SUPPORTED_EXTENSIONS.get(ext)
            if mime is None:
                self.skipped_unsupported.append(rel)
                continue
            if ext == ".pdf" and not pdf_ok:
                _log.warning(
                    "skipping %s — pypdf is not importable; it is a core "
                    "dependency, so reinstall the package (pip install -e . or "
                    "-r requirements-lock.txt) to enable PDF ingestion",
                    rel,
                )
                self.skipped_unsupported.append(f"{rel} (pypdf not installed)")
                continue
            changes.append(
                DriveChange(file_id=rel, title=path.name, mime_type=mime, removed=False)
            )
        return changes

    def _scan(self) -> list[DriveChange]:
        """Present files, plus removal changes for vanished prior docs (prune)."""
        present = self.scan_present()
        if self.prune:
            present_ids = {c.file_id for c in present}
            self.pruned_doc_ids = sorted(self.prior_doc_ids - present_ids)
            present.extend(
                DriveChange(file_id=doc_id, title=doc_id, mime_type="", removed=True)
                for doc_id in self.pruned_doc_ids
            )
        else:
            self.pruned_doc_ids = []
        return present

    # ---- DriveClientProto ----------------------------------------------

    def get_start_page_token(self) -> str:
        # No cursor concept for a local folder; "" is non-None so subsequent
        # runs take run_sync's list_changes() path (a fresh full re-scan).
        return ""

    def list_folder(self, folder_id: str) -> list[DriveChange]:
        # Bootstrap pass. folder_id is ignored — this client owns its root.
        return self._scan()

    def list_changes(self, page_token: str) -> tuple[list[DriveChange], str]:
        # Every manual run is a full re-scan; content-hash skip handles
        # idempotency, the prune set handles removals.
        return self._scan(), ""

    def export_text(self, change: DriveChange) -> str:
        path = self.root / change.file_id
        ext = path.suffix.lower()
        if ext in _TEXT_EXTENSIONS:
            return path.read_text(encoding="utf-8", errors="replace")
        if ext == ".pdf":
            return _extract_pdf(path)
        # Defensive: scan_present() filters unsupported files, so this is only
        # reachable if a caller hand-builds a bad change. run_sync isolates it.
        raise ValueError(f"unsupported file extension for {change.file_id!r}")


def ingest_folder(
    *,
    tenant_id: str,
    folder: Path,
    store: VectorStoreProto,
    embedder: Embedder,
    source: str | None = None,
    prune: bool = True,
    run_id: str | None = None,
) -> tuple[SyncReport, LocalFolderClient]:
    """Ingest every supported file in ``folder`` into the vector store.

    Loads prior sync state to compute the prune set, then drives the standard
    :func:`run_sync` pipeline with a :class:`LocalFolderClient`. Returns the
    :class:`SyncReport` plus the client (whose ``skipped_unsupported`` /
    ``pruned_doc_ids`` feed the CLI summary).

    Args:
        tenant_id: Cosmos partition key the docs belong to.
        folder: Local directory to ingest (the authoritative policy set).
        store: Vector + state store (e.g. ``CosmosVectorStore`` or a fake).
        embedder: Chunk → vector embedder.
        source: State key; defaults to :func:`default_source` for ``folder``.
        prune: When true (default), mirror the folder exactly — purge chunks
            for docs no longer present. False = additive (upsert-only) top-up.
        run_id: Correlation id; generated when omitted.
    """
    root = folder.resolve()
    src = source or default_source(root)
    prior = store.load_state(src)
    client = LocalFolderClient(root, prior_doc_ids=set(prior.docs), prune=prune)
    report = run_sync(
        tenant_id=tenant_id,
        source_folder_id=str(root),
        drive=client,
        store=store,
        embedder=embedder,
        source=src,
        run_id=run_id,
    )
    return report, client
