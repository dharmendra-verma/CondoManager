"""Hello-world end-to-end demo (CM-28 AC #6).

Jira: CM-28  | Epic: CM-Epic 4  | Phase 0

Runs the StateGraph spine once with stub nodes. The same run produces:

* An App Insights trace via the CM-22 Azure Monitor exporter when
  ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set (Container App env
  via secretRef from CM-22 wires this automatically in prod).
* A LangSmith run via the CM-23 LangChain native callback when
  ``LANGCHAIN_TRACING_V2=true`` AND ``LANGCHAIN_API_KEY`` are set
  (CM-23 wires both via the Container App env in dev).

With both backends configured, the same ``request_id`` lands in each;
the App Insights ``customDimensions`` show the ``langgraph.node.<name>``
span names from CM-21's :func:`langgraph_node_span`.

Usage::

    # Local (all defaults — emits to console exporter, no LangSmith):
    python -m agents.orchestrator.demo

    # Dev container (everything wired):
    APPLICATIONINSIGHTS_CONNECTION_STRING="<from KV>" \\
    LANGCHAIN_TRACING_V2=true LANGCHAIN_API_KEY="<key>" \\
    LANGCHAIN_PROJECT=condomanager-dev \\
        python -m agents.orchestrator.demo

The demo runs WITHOUT OpenAI credentials — stub nodes do no LLM calls.
"""

from __future__ import annotations

import sys

from langchain_core.runnables import RunnableConfig

from agents.observability import configure_otel, new_request_id, with_request_id

from . import AgentState, Channel, build_graph


def main() -> int:
    configure_otel(service_name="orchestrator-demo", environment="dev")
    request_id = new_request_id()

    with with_request_id(request_id):
        graph = build_graph()
        initial = AgentState(
            tenant_id="t-demo-001",
            request_id=request_id,
            channel=Channel.WEB,
            raw_message="The kitchen sink is leaking",
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": f"demo-{request_id}"}
        }
        final = graph.invoke(initial, config=config)

    print(f"request_id:  {request_id}")
    print(f"final state: {final}")
    print(
        "\nTrace landed in: App Insights (if APPLICATIONINSIGHTS_CONNECTION_STRING set)"
        " AND LangSmith (if LANGCHAIN_TRACING_V2=true + LANGCHAIN_API_KEY set)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover  (manual run)
    sys.exit(main())
