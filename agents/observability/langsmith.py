"""LangSmith helpers — thin wrappers around the ``langsmith.Client`` SDK.

Jira: CM-23  | Epic: Observability  | Phase 0

The contract every other module uses is :func:`is_langsmith_enabled` — it
returns True only when:

  * ``LANGCHAIN_TRACING_V2`` is set to the literal string ``"true"`` (the
    LangChain SDK's standard toggle), AND
  * ``LANGCHAIN_API_KEY`` holds a non-empty, non-placeholder value.

The CM-18 KV placeholder ``REPLACE-ME`` is treated as if-unset so the
Container App boots cleanly before the post-deploy secret seed step
(same pattern as CM-22's ``APPLICATIONINSIGHTS_CONNECTION_STRING``).

LangChain's native callback ships traces to LangSmith automatically when
those env vars are set — there is no programmatic ``configure_langsmith()``
call to make from the SDK side. This module is therefore mostly about
dataset CRUD for the CM-30 eval workflow.

LangSmith and Traceloop coexist by hooking different layers of LangChain:
Traceloop instruments the ``Runnable`` layer (-> OTel spans -> App Insights
via CM-22); the LangChain native callback handler ships its own runs to
LangSmith. Tested for non-interference in
``tests/observability/test_langsmith.py``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from typing import Any

_log = logging.getLogger(__name__)

#: The CM-18 KV seed-script writes this when populating a secret name with
#: no real value. Treated as if-unset so the app boots cleanly.
SECRET_PLACEHOLDER: str = "REPLACE-ME"


def is_langsmith_enabled() -> bool:
    """Return True iff the LangChain SDK will ship traces to LangSmith.

    Mirrors the SDK's own env-var sniffing: the flag is case-insensitive
    (``"true"`` / ``"True"`` / ``"TRUE"`` all enable), but ``"false"``
    / ``"0"`` / unset all disable.
    """
    if os.environ.get("LANGCHAIN_TRACING_V2", "").lower() != "true":
        return False
    key = os.environ.get("LANGCHAIN_API_KEY", "").strip()
    if not key or key == SECRET_PLACEHOLDER:
        return False
    return True


def client() -> Any:
    """Construct a ``langsmith.Client`` from env. Lazy-imports the SDK.

    Lazy import keeps the ``langsmith`` package off the cold-start path for
    workloads that don't use LangSmith at all (e.g. prod with
    ``langsmithEnabled=false``).
    """
    from langsmith import Client  # noqa: PLC0415  (lazy by design)

    return Client()


def _example_fingerprint(inputs: dict[str, Any]) -> str:
    """Stable hash of an example's inputs for dedup on re-upload."""
    # `sort_keys` keeps the hash stable when callers vary key order.
    return json.dumps(inputs, sort_keys=True)


def ensure_dataset(
    name: str,
    examples: Iterable[dict[str, Any]],
    *,
    description: str | None = None,
) -> Any:
    """Create the dataset if missing, add only the examples not already present.

    Idempotency strategy: example identity = JSON of ``inputs`` (sorted keys).
    Re-uploading the same JSONL is a no-op — never duplicates examples or
    creates a second dataset.

    Args:
        name: Dataset name in LangSmith (e.g. ``"condomanager-triage-seed-dev"``).
        examples: Iterable of ``{"inputs": {...}, "outputs": {...}}`` dicts.
        description: Optional description to set on the dataset at creation.

    Returns:
        The ``langsmith.Dataset`` object — either pre-existing or freshly
        created.
    """
    ls = client()

    try:
        dataset = ls.read_dataset(dataset_name=name)
        _log.info("ensure_dataset: '%s' already exists; reconciling examples", name)
    except Exception:  # noqa: BLE001  (langsmith raises a typed NotFound, but we keep this loose)
        dataset = ls.create_dataset(
            dataset_name=name,
            description=description or f"CM-23 seed dataset ({name})",
        )
        _log.info("ensure_dataset: created '%s'", name)

    # Build a fingerprint set of existing examples to dedup against.
    existing_fingerprints: set[str] = set()
    for ex in ls.list_examples(dataset_id=dataset.id):
        existing_fingerprints.add(_example_fingerprint(ex.inputs))

    added = 0
    for ex in examples:
        fp = _example_fingerprint(ex["inputs"])
        if fp in existing_fingerprints:
            continue
        ls.create_example(
            inputs=ex["inputs"],
            outputs=ex.get("outputs"),
            dataset_id=dataset.id,
        )
        existing_fingerprints.add(fp)
        added += 1

    _log.info(
        "ensure_dataset: '%s' now holds %d examples (added %d this run)",
        name,
        len(existing_fingerprints),
        added,
    )
    return dataset
