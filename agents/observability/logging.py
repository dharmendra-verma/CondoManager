"""Structured JSON logging + PII masking for stdout.

Jira: CM-27  | Epic: Observability  | Phase 0

One JSON object per record on stdout. Container Apps captures stdout to
the Log Analytics workspace (CM-16 + CM-22), where KQL queries can join
logs to App Insights spans and Langfuse observations by ``request_id`` —
the one correlation primitive shared across all three backends.

Why stdout, not the Azure Monitor logger? CM-22's
``configure_azure_monitor(logger_name=None)`` deliberately deferred the
Python-logging channel to this story (see ``sdk.py``). Container Apps
already captures stdout, so there's no missing pipe.

Surfaces:

  configure_logging(*, service_name, environment, level="INFO")
      Idempotent. Installs JsonFormatter + PiiMaskingFilter on root.
      Replaces any prior root handlers (so a developer's basicConfig in
      a notebook doesn't double-emit).

  JsonFormatter
      One JSON object per record. Required fields always present; optional
      fields (tenant_id, agent_name, exc_info) only when populated.

  PiiMaskingFilter
      Calls ``mask_pii`` on record.msg and record.args BEFORE the formatter
      runs. Logging must never raise — internal failures are swallowed.

Resettable for tests via ``_reset_for_tests()`` — same module-level latch
pattern as ``sdk.py`` (CM-21), ``langfuse_export.py`` (CM-24).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from agents.observability.correlation import (
    get_agent_name,
    get_request_id,
    get_tenant_id,
)
from agents.observability.pii import mask_pii

# Module-level idempotency guard. Reset by ``_reset_for_tests`` in tests.
_configured: bool = False


class PiiMaskingFilter(logging.Filter):
    """Mask PII in the fully-rendered log message.

    Renders the record's ``%``-args FIRST (via ``getMessage()``), then masks the
    finished string and clears ``args`` so the formatter doesn't re-apply them.

    Rendering before masking is essential: ``mask_pii`` stringifies its input, so
    masking args individually *before* formatting would feed strings to numeric
    specifiers — ``"%d" % "0"`` raises ``TypeError`` — and break any ``%d`` /
    ``%.3f`` log line, including third-party libraries (e.g. httpx request logs).
    Masking the rendered message is also strictly more correct: it catches PII
    that spans the template + args boundary.

    Attached to the handler BEFORE the formatter runs, so JSON output contains
    masked text from the start. Logging code must never raise — any internal
    failure here is swallowed so a bad input pattern can't take down the app.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = mask_pii(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001 — logging must never raise
            pass
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record. Stable field order for KQL joins.

    Required fields are always present. Optional fields (``tenant_id``,
    ``agent_name``, ``exc_info``, ``stack_info``) appear only when set so
    background-job logs don't carry stale ``tenant_id: null`` noise.

    ``request_id`` is always emitted (even as the ``UNKNOWN_REQUEST_ID``
    sentinel) — a log line with ``request_id: "unknown"`` is a real signal
    that some code path skipped ``with_request_id``.
    """

    def __init__(self, *, service_name: str, environment: str) -> None:
        super().__init__()
        self._service_name = service_name
        self._environment = environment
        self._service_version = os.environ.get("OTEL_SERVICE_VERSION", "0.1.0")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service_name": self._service_name,
            "environment": self._environment,
            "service_version": self._service_version,
            "request_id": get_request_id(),
        }
        # Optional contextvars — emit only when set.
        tenant = get_tenant_id()
        if tenant is not None:
            payload["tenant_id"] = tenant
        agent = get_agent_name()
        if agent is not None:
            payload["agent_name"] = agent
        # Exception / stack info, rendered by the parent class.
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # ``default=str`` catches any stray non-serializable values that
        # might end up in ``record.getMessage()`` rendering — safer than
        # raising at log time.
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    *,
    service_name: str,
    environment: str,
    level: str | int = "INFO",
) -> None:
    """Install the JSON formatter + PII filter on the root logger. Idempotent.

    Replaces any pre-existing handlers on the root logger — a developer's
    ``logging.basicConfig`` call (e.g. in a notebook) gets swept aside so
    there's no double-emission.

    Args:
        service_name: Goes into every record as ``service_name``. Each
            workload uses its own short name (e.g. ``"orchestrator"``).
        environment: ``"dev"`` or ``"prod"`` — into every record as
            ``environment``.
        level: Standard logging level, name or integer.
    """
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler()  # stdout-by-default
    handler.setFormatter(
        JsonFormatter(service_name=service_name, environment=environment),
    )
    handler.addFilter(PiiMaskingFilter())

    root = logging.getLogger()
    # Replace any prior root handlers so the formatter is the single source
    # of truth (and we don't emit human-readable + JSON for the same record).
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    _configured = True


def _reset_for_tests() -> None:
    """Test-only — clears the idempotency latch AND root handlers.

    Tests that exercise ``configure_logging`` directly should call this
    in setup AND teardown so handlers don't leak between tests.
    """
    global _configured
    _configured = False
    logging.getLogger().handlers.clear()
