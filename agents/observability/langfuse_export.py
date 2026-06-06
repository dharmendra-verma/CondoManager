"""Langfuse Cloud Hobby integration for production LLM tracing.

Jira: CM-24  | Epic: Observability  | Phase 0

Two functions and one decorator:

  is_langfuse_enabled() -> bool
      True iff LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are both set
      to non-placeholder values AND the LANGFUSE_ENABLED killswitch is
      not "0"/"false"/"no". Container Apps sources both keys from Key
      Vault via Managed Identity in prod (CM-26 owns the secretRef
      wiring). Dev / tests leave them unset so the SDK is never
      initialised.

  init_langfuse() -> Langfuse | None
      Idempotent. First call constructs the client; subsequent calls
      return the cached instance. Returns None when disabled — callers
      MUST check before using.

  observe_node(name=None) -> decorator
      Wraps langfuse.decorators.observe(name=...) when enabled,
      transparent no-op otherwise. Binds the current request_id (from
      agents.observability.correlation) as observation metadata so
      Langfuse dashboards can join to logs / spans by request_id.

Env vars (read at call time, not import time, so tests can monkeypatch):
  LANGFUSE_PUBLIC_KEY   — from KV `langfuse-public-key`
  LANGFUSE_SECRET_KEY   — from KV `langfuse-secret-key`
  LANGFUSE_HOST         — default https://cloud.langfuse.com
  LANGFUSE_ENABLED      — optional killswitch ("0"/"false"/"no" force off
                          even when both keys are present)

The placeholder sentinel ``"REPLACE-ME"`` (CM-18 KV seed) is treated as
if-unset, so Container Apps boots cleanly before the operator runs
``az keyvault secret set`` with real Langfuse keys.

Coexists with CM-22's Azure Monitor pipeline — Langfuse uses its own
HTTP client and trace context, NOT a 4th OTel exporter. The
openinference openai instrumentor (loaded in instrumentation.py) emits
``gen_ai.usage.*`` attributes that Langfuse correlates via the shared
trace context, so cost + token attributes are captured automatically
without explicit per-call wiring.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from agents.observability.sdk import SECRET_PLACEHOLDER

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from langfuse import Langfuse

_log = logging.getLogger(__name__)

# Module-level cache. Reset by `_reset_for_tests` in the test fixture.
_client: Langfuse | None = None
_initialized: bool = False

F = TypeVar("F", bound=Callable[..., Any])

#: Killswitch values that force Langfuse off even when keys are set.
_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no"})


def _env_or_none(name: str) -> str | None:
    """Return env var value, treating empty + ``REPLACE-ME`` as unset."""
    val = os.environ.get(name, "").strip()
    if not val or val == SECRET_PLACEHOLDER:
        return None
    return val


def is_langfuse_enabled() -> bool:
    """True iff the Langfuse SDK should run.

    Both keys must be set to non-placeholder values, and the killswitch
    env var must NOT be set to a falsey value. Read at call time so tests
    can monkeypatch ``os.environ`` per-test.
    """
    if os.environ.get("LANGFUSE_ENABLED", "").strip().lower() in _DISABLED_VALUES:
        return False
    return (
        _env_or_none("LANGFUSE_PUBLIC_KEY") is not None
        and _env_or_none("LANGFUSE_SECRET_KEY") is not None
    )


def init_langfuse() -> Langfuse | None:
    """Construct (once) the Langfuse client. Returns None when disabled.

    Idempotent — subsequent calls return the cached instance, even
    after env-var changes (matches CM-21 / CM-22 pattern). Tests call
    ``_reset_for_tests()`` between cases to clear the cache.
    """
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True
    if not is_langfuse_enabled():
        _log.info("Langfuse disabled — keys unset, placeholder, or killswitch on.")
        return None
    # Lazy import — the SDK transitively pulls httpx; pay only when used.
    from langfuse import Langfuse  # noqa: PLC0415

    # Resolve the host once and reuse it for both the ctor and the log line.
    # Do NOT read it back off the client: langfuse>=2.40 (we pin 2.60.10) does
    # not expose a public ``.host`` attribute, so ``_client.host`` raises
    # AttributeError and crashes app startup the moment real keys make this
    # path run (CM-68 — the failure that ActivationFailed the prod revision).
    host = _env_or_none("LANGFUSE_HOST") or "https://cloud.langfuse.com"
    _client = Langfuse(
        public_key=_env_or_none("LANGFUSE_PUBLIC_KEY"),
        secret_key=_env_or_none("LANGFUSE_SECRET_KEY"),
        host=host,
    )
    # Never log key material; host is fine (operator-visible URL).
    _log.info("Langfuse client initialised, host=%s", host)
    return _client


def flush_langfuse() -> None:
    """Flush buffered Langfuse observations to the backend. No-op when disabled.

    REQUIRED in scale-to-zero deployments (Azure Container Apps
    ``minReplicas=0``): the ``@observe_node`` decorators enqueue observations
    to ``langfuse_context``'s background consumer, which only flushes on a
    timer / at interpreter exit. When the container is torn down right after a
    request (idle scale-down), that flush never runs and the trace is silently
    dropped (CM-69). Call this after handling a request — and on shutdown — to
    force the send before the container can stop.

    Never raises: a telemetry flush must not break the request path.
    """
    if not is_langfuse_enabled():
        return
    try:
        from langfuse.decorators import langfuse_context  # noqa: PLC0415

        langfuse_context.flush()
    except Exception:  # noqa: BLE001 - telemetry must never break the caller
        _log.warning("Langfuse flush failed", exc_info=True)


def observe_node(name: str | None = None) -> Callable[[F], F]:
    """Decorator: wrap ``langfuse.decorators.observe`` when enabled, else no-op.

    When Langfuse is enabled, the wrapped function emits a Langfuse
    observation. The current ``request_id`` (from
    ``agents.observability.correlation``) is attached as observation
    metadata so dashboards can join Langfuse observations to App
    Insights spans and structured logs.

    When Langfuse is disabled (dev / tests / killswitch on), the
    decorator is a transparent pass-through: zero overhead, no imports
    of ``langfuse.*`` triggered.

    Decision is made at *decoration time* (not call time). Modules
    decorated at import are bound to the state at that moment — which
    matches every production deployment, since env vars don't change
    after Container Apps starts.
    """

    def decorator(fn: F) -> F:
        obs_name = name or fn.__name__

        if not is_langfuse_enabled():
            # Transparent pass-through, but still tag the function with its
            # intended observation name so the wiring is assertable in tests
            # regardless of whether Langfuse is enabled at decoration time.
            fn.__langfuse_observed__ = obs_name  # type: ignore[attr-defined]
            return fn

        # Lazy imports — only loaded on the enabled path.
        from langfuse.decorators import langfuse_context, observe  # noqa: PLC0415

        from agents.observability.correlation import (  # noqa: PLC0415
            UNKNOWN_REQUEST_ID,
            get_request_id,
        )

        observed = observe(name=obs_name)(fn)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Attach request_id to the active Langfuse observation so logs
            # and Langfuse traces can be joined by request_id (single
            # correlation primitive, set by CM-21's with_request_id).
            rid = get_request_id()
            if rid and rid != UNKNOWN_REQUEST_ID:
                langfuse_context.update_current_observation(
                    metadata={"request_id": rid},
                )
            return observed(*args, **kwargs)

        wrapper.__langfuse_observed__ = obs_name  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def _reset_for_tests() -> None:
    """Test-only — clears module state. Do not call from app code."""
    global _client, _initialized
    _client = None
    _initialized = False
