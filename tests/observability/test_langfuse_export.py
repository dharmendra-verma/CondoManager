"""Unit tests for ``agents.observability.langfuse_export``.

Jira: CM-24  | Epic: Observability  | Phase 0

Covers the public contract:
  * is_langfuse_enabled — env / placeholder / killswitch combinations
  * init_langfuse        — disabled returns None; enabled idempotent
  * observe_node         — transparent pass-through when disabled

The decorator's enabled-path behavior (attaches request_id metadata
to the Langfuse observation) is not unit-tested here — that requires
either a live Langfuse Cloud account or a mocked SDK, neither of which
adds signal over the integration tests CM-26 will land alongside the
real KV→secretRef wiring. The disabled-path test below verifies the
no-op contract that the rest of the suite (and prod startup before
keys are set) relies on.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agents.observability import langfuse_export as lfe


@pytest.fixture(autouse=True)
def _reset_lfe() -> None:
    """Reset module-level state around each test."""
    lfe._reset_for_tests()
    yield
    lfe._reset_for_tests()


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    assert lfe.is_langfuse_enabled() is False
    assert lfe.init_langfuse() is None


def test_placeholder_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CM-18 ``REPLACE-ME`` seed must NOT enable Langfuse."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "REPLACE-ME")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "REPLACE-ME")
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    assert lfe.is_langfuse_enabled() is False


def test_killswitch_overrides_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LANGFUSE_ENABLED=false`` forces off even when keys are real."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk_real")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk_real")
    for off_value in ("0", "false", "no", "False", "NO"):
        monkeypatch.setenv("LANGFUSE_ENABLED", off_value)
        assert lfe.is_langfuse_enabled() is False, off_value


def test_enabled_when_both_keys_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk_real")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk_real")
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    assert lfe.is_langfuse_enabled() is True


def test_one_key_unset_is_not_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both keys are required — a half-config is not enabled."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk_real")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    assert lfe.is_langfuse_enabled() is False


def test_decorator_is_passthrough_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When disabled, the decorator must NOT import or call any langfuse.* code."""
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_ENABLED"):
        monkeypatch.delenv(k, raising=False)

    @lfe.observe_node("test.add")
    def add(a: int, b: int) -> int:
        return a + b

    @lfe.observe_node()
    def raises() -> None:
        raise ValueError("propagated")

    # Value preserved.
    assert add(2, 3) == 5
    # Kwargs preserved.
    assert add(a=10, b=32) == 42
    # Exceptions propagate unchanged (not swallowed).
    with pytest.raises(ValueError, match="propagated"):
        raises()


def test_init_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeat ``init_langfuse()`` calls reuse the first client; SDK ctor once."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk_real")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk_real")
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)

    fake_client = MagicMock(host="https://cloud.langfuse.com")
    fake_module = MagicMock()
    fake_module.Langfuse.return_value = fake_client

    # `init_langfuse` does ``from langfuse import Langfuse`` lazily, so we
    # patch the import target in ``sys.modules`` rather than the attribute
    # in our module.
    with patch.dict("sys.modules", {"langfuse": fake_module}):
        first = lfe.init_langfuse()
        second = lfe.init_langfuse()
        assert first is fake_client
        assert second is fake_client
        assert fake_module.Langfuse.call_count == 1
