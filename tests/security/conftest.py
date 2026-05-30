"""Shared fixtures for ``tests/security/`` (CM-38).

Resets the cached detector + audit-sink singletons around every test so the
offline defaults never leak state, and clears the relevant env vars so the
seam selection is deterministic regardless of the developer's shell.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from agents.security import audit as audit_mod
from agents.security import detection as detection_mod


@pytest.fixture(autouse=True)
def reset_seams(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    # Force the offline path: no Azure AI Language, no Cosmos.
    monkeypatch.delenv("AI_LANGUAGE_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    detection_mod._reset_for_tests()
    audit_mod._reset_for_tests()
    yield
    detection_mod._reset_for_tests()
    audit_mod._reset_for_tests()


@pytest.fixture
def now() -> datetime:
    """A fixed 'now' so audit timestamps are deterministic."""
    return datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
