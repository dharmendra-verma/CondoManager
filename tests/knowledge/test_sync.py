"""Tests for the ``agents.knowledge.run_sync`` orchestrator (CM-34).

Covers the acceptance criteria that live in the algorithm: delta detection,
skip-unchanged, versioned + idempotent upsert, stale-chunk cleanup, removal,
per-doc failure isolation, and the embed/chunk count contract.
"""

from __future__ import annotations

import pytest
from agents.knowledge import DriveChange, run_sync

from tests.knowledge.conftest import FakeDrive, FakeEmbedder, FakeStore

TENANT = "t1"
SOURCE = "gdrive"


def _doc(file_id: str, *, removed: bool = False) -> DriveChange:
    return DriveChange(
        file_id=file_id, title=f"Title {file_id}", mime_type="text/plain", removed=removed
    )


def _text(n_words: int) -> str:
    return "alpha bravo charlie delta echo foxtrot " * n_words


def _run(drive: FakeDrive, store: FakeStore, embedder: FakeEmbedder, run_id: str):
    return run_sync(
        tenant_id=TENANT,
        source_folder_id="folder-1",
        drive=drive,
        store=store,
        embedder=embedder,
        source=SOURCE,
        run_id=run_id,
    )


def _bootstrap(store: FakeStore, embedder: FakeEmbedder, **drive_kwargs: object):
    """Run a bootstrap pass over a one-doc folder and return the drive used."""
    drive = FakeDrive(start_token="tok-1", **drive_kwargs)  # type: ignore[arg-type]
    report = _run(drive, store, embedder, "boot")
    return drive, report


def test_bootstrap_indexes_folder_and_saves_token() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    drive, report = _bootstrap(
        store,
        embedder,
        folder=[_doc("d1")],
        texts={"d1": _text(40)},
    )

    assert report.bootstrap is True
    assert report.changed == 1
    assert report.chunks_written == len(store.chunks_for("d1")) > 0
    # Page token captured + per-doc state recorded for next run.
    assert store.state[SOURCE].page_token == "tok-1"
    assert "d1" in store.state[SOURCE].docs
    # Deterministic ids: t1:d1:0, t1:d1:1, …
    expected = {f"{TENANT}:d1:{i}" for i in range(len(store.chunks_for("d1")))}
    assert set(store.chunks) == expected


def test_no_changes_second_run_does_nothing() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    _bootstrap(store, embedder, folder=[_doc("d1")], texts={"d1": _text(40)})

    # No change batches → list_changes returns empty.
    drive2 = FakeDrive(texts={"d1": _text(40)})
    report = _run(drive2, store, embedder, "r2")

    assert report.bootstrap is False
    assert report.changed == 0 and report.skipped == 0
    assert drive2.export_calls == []  # nothing even exported


def test_changed_but_identical_content_is_skipped() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    _bootstrap(store, embedder, folder=[_doc("d1")], texts={"d1": _text(40)})
    embeds_after_boot = embedder.embed_calls

    # Drive reports d1 changed, but the text is byte-identical.
    drive2 = FakeDrive(
        change_batches=[([_doc("d1")], "tok-2")],
        texts={"d1": _text(40)},
    )
    report = _run(drive2, store, embedder, "r2")

    assert report.skipped == 1 and report.changed == 0
    # No new embedding spend on an unchanged doc.
    assert embedder.embed_calls == embeds_after_boot


def test_changed_content_reindexes_and_bumps_version() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    _bootstrap(store, embedder, folder=[_doc("d1")], texts={"d1": _text(40)})

    drive2 = FakeDrive(
        change_batches=[([_doc("d1")], "tok-2")],
        texts={"d1": _text(40) + " amended clause"},
    )
    report = _run(drive2, store, embedder, "r2")

    assert report.changed == 1
    assert store.state[SOURCE].docs["d1"].version == 2
    # Same deterministic ids — re-index overwrites, never duplicates.
    assert all(c.doc_version == 2 for c in store.chunks_for("d1"))


def test_shrunk_doc_deletes_stale_trailing_chunks() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    # Big doc → many chunks.
    _bootstrap(store, embedder, folder=[_doc("d1")], texts={"d1": _text(400)})
    assert len(store.chunks_for("d1")) > 1

    # Edited down to a single chunk's worth of text.
    drive2 = FakeDrive(
        change_batches=[([_doc("d1")], "tok-2")],
        texts={"d1": "just one short line now"},
    )
    _run(drive2, store, embedder, "r2")

    chunks = store.chunks_for("d1")
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0


def test_removed_doc_purges_chunks_and_state() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    _bootstrap(store, embedder, folder=[_doc("d1")], texts={"d1": _text(40)})
    assert store.chunks_for("d1")

    drive2 = FakeDrive(change_batches=[([_doc("d1", removed=True)], "tok-2")])
    report = _run(drive2, store, embedder, "r2")

    assert report.removed == 1
    assert store.chunks_for("d1") == []
    assert "d1" not in store.state[SOURCE].docs


def test_export_failure_marks_doc_failed_and_continues() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    drive, report = _bootstrap(
        store,
        embedder,
        folder=[_doc("d1"), _doc("d2")],
        texts={"d1": _text(40)},  # d2 missing/raises
        raise_on={"d2"},
    )

    assert report.changed == 1  # d1
    assert report.failed == 1  # d2
    assert "d1" in store.state[SOURCE].docs
    assert "d2" not in store.state[SOURCE].docs  # failed doc not recorded


def test_embedding_count_mismatch_raises() -> None:
    store = FakeStore()
    embedder = FakeEmbedder(vectors_per_text=2)  # returns 2 vectors per chunk
    drive = FakeDrive(start_token="tok-1", folder=[_doc("d1")], texts={"d1": _text(40)})

    with pytest.raises(ValueError, match="embedding count"):
        _run(drive, store, embedder, "boot")


def test_rerun_is_idempotent_no_duplicate_chunks() -> None:
    store, embedder = FakeStore(), FakeEmbedder()
    _bootstrap(store, embedder, folder=[_doc("d1")], texts={"d1": _text(40)})
    count_after_boot = len(store.chunks)
    upserts_after_boot = store.upsert_calls

    # Drive re-reports the same doc with identical content.
    drive2 = FakeDrive(
        change_batches=[([_doc("d1")], "tok-2")],
        texts={"d1": _text(40)},
    )
    _run(drive2, store, embedder, "r2")

    assert len(store.chunks) == count_after_boot  # no growth
    assert store.upsert_calls == upserts_after_boot  # skip → no upsert at all
