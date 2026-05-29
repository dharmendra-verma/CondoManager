"""LangSmith smoke-test demo (CM-23).

Fires one ``ChatOpenAI`` call so the trace appears in the LangSmith UI.
This is the AC #3 "Sample run visible end-to-end" deliverable — manual
because a CI assertion would need a real LangSmith key.

Usage::

    # All four env vars must be set (the CM-23 Container App wiring sets
    # them automatically once the KV secrets are seeded):
    LANGCHAIN_TRACING_V2=true \\
    LANGCHAIN_API_KEY=<langsmith-key> \\
    LANGCHAIN_PROJECT=condomanager-dev \\
    OPENAI_API_KEY=<openai-key> \\
        python -m agents.observability.langchain_demo --message "kitchen sink is leaking"

The trace shows up in https://smith.langchain.com under the
``condomanager-dev`` project within ~30s.

If ``LANGCHAIN_API_KEY`` is missing or ``REPLACE-ME``, the script prints
a clear notice and exits 1 — it never silently succeeds with no trace.
"""

from __future__ import annotations

import argparse
import os
import sys

from .langsmith import is_langsmith_enabled


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LangSmith smoke test for CM-23.",
    )
    p.add_argument(
        "--message",
        default="The kitchen sink is leaking under the cabinet.",
        help="Tenant message to classify. Defaults to a sample maintenance request.",
    )
    p.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="OpenAI model id. Default: gpt-4o-mini.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not is_langsmith_enabled():
        sys.stderr.write(
            "LangSmith env vars are missing or hold REPLACE-ME.\n"
            "  LANGCHAIN_TRACING_V2 (must be 'true'): "
            f"{os.environ.get('LANGCHAIN_TRACING_V2', '<unset>')}\n"
            "  LANGCHAIN_API_KEY: "
            f"{'<unset>' if not os.environ.get('LANGCHAIN_API_KEY') else '<set>'}\n"
            "  LANGCHAIN_PROJECT: "
            f"{os.environ.get('LANGCHAIN_PROJECT', '<unset>')}\n"
            "\n"
            "Seed the KV secret (`langsmith-api-key`) and redeploy the Container "
            "App revision, OR set these locally before running the demo.\n"
        )
        return 1

    # Import lazily so the script fails fast on missing env without paying
    # the LangChain import cost.
    from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415
    from langchain_openai import ChatOpenAI  # noqa: PLC0415

    llm = ChatOpenAI(model=args.model, temperature=0)
    messages = [
        SystemMessage(
            content=(
                "You are a condo property manager. Classify the tenant message "
                "into one of: maintenance, inquiry, escalation, follow-up. "
                "Respond with ONLY the label, lowercase, no punctuation."
            )
        ),
        HumanMessage(content=args.message),
    ]
    response = llm.invoke(messages)
    print(f"message:  {args.message!r}")
    print(f"intent:   {response.content!r}")
    project = os.environ.get("LANGCHAIN_PROJECT", "default")
    print(f"\nTrace should appear at https://smith.langchain.com under '{project}' (~30s).")
    return 0


if __name__ == "__main__":  # pragma: no cover  (manual run only)
    sys.exit(main())
