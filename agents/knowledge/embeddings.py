"""Embedding generation for the knowledge pipeline (CM-34, reusable by CM-33).

Jira: CM-34  | Epic: CM-Epic 9  | Phase 1

The :class:`Embedder` Protocol is the seam the sync orchestrator depends
on — tests inject a deterministic fake; production wires
:class:`AzureOpenAIEmbedder`. The default model is ``text-embedding-3-small``
(1536 dims), matching the Cosmos ``policies-vector`` DiskANN index
dimensions configured in CM-17 — a mismatch there would be rejected at
query time.

Config (Azure OpenAI) is read from env, treating the CM-18 ``REPLACE-ME``
placeholder as if-unset so the Function App boots cleanly before an
operator populates ``azure-openai-key``. :func:`default_embedder` returns
``None`` when unconfigured; the caller decides whether that's fatal.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from agents.knowledge.models import SECRET_PLACEHOLDER

#: Default embedding model + its vector dimensionality. 1536 must match the
#: cosmosVectorDimensions used by CM-17's policies-vector container.
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

#: Azure OpenAI data-plane API version. Stable surface for embeddings; bump
#: in its own change if a newer embedding model needs it.
DEFAULT_API_VERSION = "2024-02-01"


class Embedder(Protocol):
    """Turns a batch of chunk texts into a batch of embedding vectors.

    The returned list MUST be 1:1 with ``texts`` (same order, same length)
    — the orchestrator treats any mismatch as a hard error rather than
    silently misaligning text and vectors.
    """

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _env_or_none(name: str) -> str | None:
    """Env var value, treating empty + ``REPLACE-ME`` as unset (CM-18)."""
    val = os.environ.get(name, "").strip()
    if not val or val == SECRET_PLACEHOLDER:
        return None
    return val


class AzureOpenAIEmbedder:
    """:class:`Embedder` backed by Azure OpenAI via langchain-openai."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str = EMBEDDING_MODEL,
        api_version: str = DEFAULT_API_VERSION,
    ) -> None:
        # Lazy import — only the deployed Function App needs langchain-openai's
        # Azure client; tests inject a fake and never touch this path.
        from langchain_openai import AzureOpenAIEmbeddings  # noqa: PLC0415

        # Bind through an Any alias so mypy doesn't couple us to the exact
        # constructor signature of a fast-moving third-party SDK.
        embeddings_cls: Any = AzureOpenAIEmbeddings
        self._client: Any = embeddings_cls(
            azure_endpoint=endpoint,
            api_key=api_key,
            azure_deployment=deployment,
            api_version=api_version,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = self._client.embed_documents(texts)
        return vectors


def default_embedder() -> Embedder | None:
    """Build an :class:`AzureOpenAIEmbedder` from env, or ``None`` if unset.

    Requires both ``AZURE_OPENAI_ENDPOINT`` and ``AZURE_OPENAI_API_KEY``
    (the latter sourced from KV ``azure-openai-key``). Deployment + API
    version fall back to sane defaults.
    """
    endpoint = _env_or_none("AZURE_OPENAI_ENDPOINT")
    api_key = _env_or_none("AZURE_OPENAI_API_KEY")
    if endpoint is None or api_key is None:
        return None
    return AzureOpenAIEmbedder(
        endpoint=endpoint,
        api_key=api_key,
        deployment=_env_or_none("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or EMBEDDING_MODEL,
        api_version=_env_or_none("AZURE_OPENAI_API_VERSION") or DEFAULT_API_VERSION,
    )
