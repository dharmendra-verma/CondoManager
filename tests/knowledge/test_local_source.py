"""Tests for local-folder policy ingestion (CM-66).

Drives the real ``agents.knowledge.ingest_folder`` / ``LocalFolderClient`` over
``tmp_path`` folders with the in-memory ``FakeStore`` / ``FakeEmbedder`` — no
Cosmos, no OpenAI, no pypdf. Covers the ACs that live in the adapter: full-scan
ingest, content-hash idempotency + versioning, mirror-prune (default) vs
additive, unsupported-file skip (incl. .pdf without pypdf), empty folder, and
the CLI's offline guard + dry-run.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from agents.knowledge import default_source, ingest_folder

from tests.knowledge.conftest import FakeEmbedder, FakeStore

TENANT = "t-acme"


def _text(n_words: int) -> str:
    return "alpha bravo charlie delta echo foxtrot " * n_words


def _write(folder: Path, rel: str, body: str) -> Path:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _ingest(folder: Path, store: FakeStore, embedder: FakeEmbedder, *, prune: bool = True):
    return ingest_folder(
        tenant_id=TENANT, folder=folder, store=store, embedder=embedder, prune=prune
    )


# --- happy path ---------------------------------------------------------------


def test_ingests_txt_and_md_through_the_pipeline(tmp_path: Path) -> None:
    _write(tmp_path, "bylaws.txt", _text(40))
    _write(tmp_path, "rules/pets.md", _text(20))
    store, embedder = FakeStore(), FakeEmbedder()

    report, client = _ingest(tmp_path, store, embedder)

    assert report.changed == 2
    assert report.failed == 0
    assert report.chunks_written > 0
    # doc_id is the path relative to the folder root (stable, human-readable).
    assert {c.doc_id for c in store.chunks.values()} == {"bylaws.txt", "rules/pets.md"}
    # Chunks carry deterministic ids, source, and version 1.
    sample = store.chunks_for("bylaws.txt")[0]
    assert sample.id == "t-acme:bylaws.txt:0"
    assert sample.tenantId == TENANT
    assert sample.doc_version == 1
    assert sample.source == default_source(tmp_path)
    assert client.skipped_unsupported == []


# --- idempotency + versioning -------------------------------------------------


def test_rerun_unchanged_writes_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "bylaws.txt", _text(40))
    store, embedder = FakeStore(), FakeEmbedder()

    _ingest(tmp_path, store, embedder)
    writes_after_first = store.upsert_calls
    embed_after_first = embedder.embed_calls

    report2, _ = _ingest(tmp_path, store, embedder)

    assert report2.changed == 0
    assert report2.skipped == 1
    # Content-hash skip means no new upserts and no embedding spend on re-run.
    assert store.upsert_calls == writes_after_first
    assert embedder.embed_calls == embed_after_first


def test_editing_a_file_bumps_version(tmp_path: Path) -> None:
    f = _write(tmp_path, "bylaws.txt", _text(40))
    store, embedder = FakeStore(), FakeEmbedder()
    _ingest(tmp_path, store, embedder)

    f.write_text(_text(60), encoding="utf-8")
    report2, _ = _ingest(tmp_path, store, embedder)

    assert report2.changed == 1
    assert store.chunks_for("bylaws.txt")[0].doc_version == 2


# --- mirror prune (default) vs additive --------------------------------------


def test_removed_file_is_pruned_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "keep.txt", _text(30))
    drop = _write(tmp_path, "drop.txt", _text(30))
    store, embedder = FakeStore(), FakeEmbedder()
    _ingest(tmp_path, store, embedder)
    assert store.chunks_for("drop.txt")  # present after first run

    drop.unlink()
    report2, client2 = _ingest(tmp_path, store, embedder)

    assert report2.removed == 1
    assert client2.pruned_doc_ids == ["drop.txt"]
    assert store.chunks_for("drop.txt") == []  # purged — folder mirrored exactly
    assert store.chunks_for("keep.txt")  # survivor untouched


def test_additive_mode_keeps_removed_file(tmp_path: Path) -> None:
    drop = _write(tmp_path, "drop.txt", _text(30))
    store, embedder = FakeStore(), FakeEmbedder()
    _ingest(tmp_path, store, embedder, prune=False)

    drop.unlink()
    report2, client2 = _ingest(tmp_path, store, embedder, prune=False)

    assert report2.removed == 0
    assert client2.pruned_doc_ids == []
    assert store.chunks_for("drop.txt")  # left in place in additive mode


# --- unsupported files --------------------------------------------------------


def test_unsupported_extension_skipped_not_crashed(tmp_path: Path) -> None:
    _write(tmp_path, "ok.md", _text(20))
    _write(tmp_path, "notes.docx", "binary-ish content")
    store, embedder = FakeStore(), FakeEmbedder()

    report, client = _ingest(tmp_path, store, embedder)

    assert report.changed == 1
    assert report.failed == 0
    assert client.skipped_unsupported == ["notes.docx"]


def test_pdf_skipped_when_pypdf_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "policy.pdf", "%PDF-1.4 fake")
    monkeypatch.setattr("agents.knowledge.local_source._pypdf_available", lambda: False)
    store, embedder = FakeStore(), FakeEmbedder()

    report, client = _ingest(tmp_path, store, embedder)

    assert report.changed == 0
    assert report.failed == 0
    assert client.skipped_unsupported == ["policy.pdf (pypdf not installed)"]


def test_pdf_ingested_when_pypdf_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "policy.pdf", "ignored-bytes")
    monkeypatch.setattr("agents.knowledge.local_source._pypdf_available", lambda: True)
    monkeypatch.setattr(
        "agents.knowledge.local_source._extract_pdf", lambda path: _text(25)
    )
    store, embedder = FakeStore(), FakeEmbedder()

    report, _ = _ingest(tmp_path, store, embedder)

    assert report.changed == 1
    assert store.chunks_for("policy.pdf")


# --- edge cases ---------------------------------------------------------------


def test_empty_folder_is_a_clean_noop(tmp_path: Path) -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    report, client = _ingest(tmp_path, store, embedder)

    assert (report.changed, report.skipped, report.removed, report.failed) == (0, 0, 0, 0)
    assert client.skipped_unsupported == []


def test_non_utf8_bytes_do_not_crash(tmp_path: Path) -> None:
    (tmp_path / "weird.txt").write_bytes(b"valid text \xff\xfe more")
    store, embedder = FakeStore(), FakeEmbedder()

    report, _ = _ingest(tmp_path, store, embedder)

    assert report.failed == 0  # errors="replace" — no UnicodeDecodeError


def test_default_source_is_stable_and_drive_distinct(tmp_path: Path) -> None:
    src = default_source(tmp_path)
    assert src.startswith("local:")
    assert src != "gdrive"
    assert default_source(tmp_path) == src  # stable across calls


# --- CLI: offline guard + dry-run --------------------------------------------


def _load_cli() -> ModuleType:
    """Load the hyphenated operator script as a module."""
    script = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "ingest-local-folder.py"
    spec = importlib.util.spec_from_file_location("cm66_ingest_cli", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_offline_guard_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    cli = _load_cli()

    rc = cli.main(["--tenant", TENANT, "--folder", str(tmp_path)])

    assert rc == 1
    assert "COSMOS_ENDPOINT" in capsys.readouterr().err  # clear message, no traceback


def test_cli_bad_folder_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    rc = cli.main(["--tenant", TENANT, "--folder", "/no/such/folder/cm66"])
    assert rc == 1
    assert "not a directory" in capsys.readouterr().err


def test_cli_dry_run_previews_without_cosmos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "bylaws.txt", _text(40))
    _write(tmp_path, "skip.docx", "x")
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    cli = _load_cli()

    rc = cli.main(["--tenant", TENANT, "--folder", str(tmp_path), "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out
    assert "bylaws.txt" in out
    assert "skip.docx" in out  # listed as unsupported
    assert "Prune preview unavailable" in out  # COSMOS unset
