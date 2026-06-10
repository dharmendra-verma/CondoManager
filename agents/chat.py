"""Shared chat-model factory + provider predicate (CM-79).

Every specialist LLM seam (Triage, Knowledge answer/decision, Coordinator policy,
Synthesis weaver, Escalation classifier) needs the *same* two things: a way to
ask "is a real chat LLM configured?" (so the ``get_*`` selector can fall back to
its deterministic offline stub) and a way to *build* the LangChain chat client.
Before CM-79 each seam hardcoded ``ChatOpenAI`` gated on ``OPENAI_API_KEY`` —
which prod never set, so every seam silently ran its stub/heuristic. This module
centralises both concerns so prod can run on **Azure OpenAI**, reusing the
``AZURE_OPENAI_ENDPOINT`` + ``azure-openai-key`` already wired into the container
app for embeddings (CM-75) — no new secret, no second vendor.

Provider selection order:

1. **Azure OpenAI** — when ``AZURE_OPENAI_ENDPOINT`` *and* ``AZURE_OPENAI_API_KEY``
   are set (the prod config). The chat *deployment* name defaults to the model
   name (the prod deployment is named ``gpt-4o-mini``); override with
   ``AZURE_OPENAI_CHAT_DEPLOYMENT``. The data-plane API version defaults to a
   chat-capable GA version; override with ``AZURE_OPENAI_API_VERSION``.
2. **OpenAI direct** — when ``OPENAI_API_KEY`` is set (local dev / eval scripts /
   the existing tests that opt into the LLM path).
3. **Neither** — :func:`llm_configured` is ``False``; callers use their stub.

The CM-18 ``REPLACE-ME`` placeholder counts as unset everywhere, matching the
embeddings selector (:func:`agents.knowledge.embeddings.default_embedder`) and
the per-seam selectors this replaces.
"""

from __future__ import annotations

import os
from typing import Any

#: CM-18 Key Vault seed placeholder; treated as if-unset (shared convention).
SECRET_PLACEHOLDER: str = "REPLACE-ME"

#: Default chat model id — GPT-4o-mini, as everywhere else in the platform. Also
#: the default Azure *deployment* name (prod deploys the model under this name).
DEFAULT_CHAT_MODEL: str = "gpt-4o-mini"

#: Default Azure OpenAI data-plane API version for chat. A GA version that
#: supports tool/function calling — required for ``with_structured_output`` and
#: JSON structured outputs. Overridable via ``AZURE_OPENAI_API_VERSION`` so a
#: newer surface can be adopted without a code change.
DEFAULT_AZURE_CHAT_API_VERSION: str = "2024-10-21"


def _env_or_none(name: str) -> str | None:
    """Env var value, treating empty + ``REPLACE-ME`` as unset (CM-18)."""
    val = os.environ.get(name, "").strip()
    if not val or val == SECRET_PLACEHOLDER:
        return None
    return val


def _azure_configured() -> bool:
    """True when both Azure OpenAI env vars are populated (prod config)."""
    return (
        _env_or_none("AZURE_OPENAI_ENDPOINT") is not None
        and _env_or_none("AZURE_OPENAI_API_KEY") is not None
    )


def llm_configured() -> bool:
    """True when a real chat LLM can be built (Azure OpenAI or OpenAI direct).

    The ``get_*`` selectors gate on this: ``True`` → the real LLM implementation,
    ``False`` → the deterministic offline stub (so CI / the credential-free demo
    stay offline). Replaces the per-seam ``OPENAI_API_KEY`` checks so prod's
    Azure config now flips every seam to the real model.
    """
    return _azure_configured() or _env_or_none("OPENAI_API_KEY") is not None


def build_chat_model(model: str = DEFAULT_CHAT_MODEL, *, temperature: float = 0.0) -> Any:
    """Build the LangChain chat client for the configured provider.

    Returns an ``AzureChatOpenAI`` when Azure is configured (prod), else a
    ``ChatOpenAI`` when ``OPENAI_API_KEY`` is set. Callers must gate on
    :func:`llm_configured` and use their offline stub otherwise; calling this
    with no provider configured raises :class:`RuntimeError` rather than
    returning a client that would fail on first use.

    Lazy-imports ``langchain_openai`` (the CM-23 pattern) so the offline suite
    never pays the import cost. The return type is ``Any`` so mypy ``--strict``
    isn't coupled to the fast-moving langchain-openai constructor/Runnable
    signatures (matching :class:`agents.knowledge.embeddings.AzureOpenAIEmbedder`).
    """
    endpoint = _env_or_none("AZURE_OPENAI_ENDPOINT")
    azure_key = _env_or_none("AZURE_OPENAI_API_KEY")
    if endpoint is not None and azure_key is not None:
        from langchain_openai import AzureChatOpenAI  # noqa: PLC0415  (lazy by design)

        azure_cls: Any = AzureChatOpenAI
        return azure_cls(
            azure_endpoint=endpoint,
            api_key=azure_key,
            azure_deployment=_env_or_none("AZURE_OPENAI_CHAT_DEPLOYMENT") or model,
            api_version=_env_or_none("AZURE_OPENAI_API_VERSION")
            or DEFAULT_AZURE_CHAT_API_VERSION,
            temperature=temperature,
        )

    if _env_or_none("OPENAI_API_KEY") is not None:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (lazy by design)

        openai_cls: Any = ChatOpenAI
        return openai_cls(model=model, temperature=temperature)

    raise RuntimeError(
        "build_chat_model() called with no LLM provider configured. Gate on "
        "agents.chat.llm_configured() and use the offline stub instead."
    )
