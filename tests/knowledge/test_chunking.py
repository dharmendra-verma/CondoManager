"""Tests for ``agents.knowledge.chunking`` (CM-34)."""

from __future__ import annotations

import pytest
from agents.knowledge import chunk_text


@pytest.mark.parametrize("text", ["", "   ", "\n\t  \n"])
def test_empty_or_whitespace_yields_no_chunks(text: str) -> None:
    assert chunk_text(text) == []


def test_short_text_is_a_single_chunk() -> None:
    chunks = chunk_text("A short policy note about quiet hours.")
    assert len(chunks) == 1
    assert "quiet hours" in chunks[0]


def test_long_text_splits_into_multiple_nonempty_chunks() -> None:
    # ~2000 words → well past the ~300-word target, so several chunks.
    text = "alpha bravo charlie delta echo foxtrot golf hotel " * 250
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)


def test_smaller_chunk_size_produces_more_chunks() -> None:
    text = "lorem ipsum dolor sit amet " * 200
    coarse = chunk_text(text, chunk_words=300)
    fine = chunk_text(text, chunk_words=50)
    assert len(fine) > len(coarse)
