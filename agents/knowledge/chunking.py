"""Document chunking for the knowledge pipeline (CM-34, reusable by CM-33).

Jira: CM-34  | Epic: CM-Epic 9  | Phase 1

Wraps LangChain's ``RecursiveCharacterTextSplitter`` behind a tiny,
dependency-light surface so both the Drive sync job (CM-34) and the
Knowledge Agent's RAG ingestion (CM-33) chunk identically — same chunk
boundaries in, same boundaries out, so a doc embedded by the sync job and
one embedded ad-hoc by the agent are byte-for-byte comparable.

The splitter is character-based; the AC is expressed in *words* (~300),
so we convert via a coarse average characters-per-word factor. The exact
factor doesn't need to be precise — it only sets the target chunk size;
the splitter then breaks on natural boundaries (paragraph, sentence, word)
near that size.
"""

from __future__ import annotations

#: AC target: ~300 words per chunk with a modest overlap to preserve context
#: across chunk boundaries (a sentence split mid-chunk still appears whole in
#: one of the two neighbours).
DEFAULT_CHUNK_WORDS = 300
DEFAULT_OVERLAP_WORDS = 50

#: Coarse English average chars-per-word (incl. the trailing space). Used only
#: to translate the word-denominated AC into the splitter's char-denominated
#: knobs — not a correctness-critical constant.
_CHARS_PER_WORD = 6


def chunk_text(
    text: str,
    *,
    chunk_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[str]:
    """Split ``text`` into ~``chunk_words``-word chunks with overlap.

    Returns an empty list for empty / whitespace-only input (an empty doc
    produces no chunks — the caller deletes any stale chunks instead).
    Blank chunks the splitter might emit are filtered out.
    """
    if not text or not text.strip():
        return []

    # Lazy import — keeps `import agents.knowledge` cheap for callers (e.g.
    # the Cosmos store) that never chunk.
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: PLC0415

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_words * _CHARS_PER_WORD,
        chunk_overlap=overlap_words * _CHARS_PER_WORD,
        length_function=len,
    )
    chunks: list[str] = splitter.split_text(text)
    return [c for c in chunks if c.strip()]
