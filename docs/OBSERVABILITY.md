# Observability (CM-21)

How CondoManager's Python code emits OpenTelemetry traces, how `request_id`
is propagated, and how to wire LLM spans without double-emission between
openinference and Traceloop.

The OpenTelemetry **backend** (Application Insights) lands in CM-22 — for
CM-21, leave `OTEL_EXPORTER_OTLP_ENDPOINT` unset and spans go to the
ConsoleSpanExporter so you can read them on stdout.

## Quickstart

```python
from agents.observability import configure_otel, with_request_id, langgraph_node_span

# At app startup (once per process):
configure_otel(service_name="orchestrator", environment="dev", app=fastapi_app)

# At every inbound boundary (HTTP handler, queue consumer, scheduled job):
with with_request_id() as request_id:
    ...  # all work in this block carries the request_id

# Around a LangGraph node body (CM-28 onward):
with langgraph_node_span("triage", tenant_id=state.tenant_id, model="gpt-4o-mini"):
    classification = await classify(state.message)
```

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `OTEL_TRACES_EXPORTER` | `console` | `console` -> ConsoleSpanExporter; `otlp` -> OTLP-HTTP exporter (requires endpoint, see below); any other value -> no-op |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(unset)_ | OTLP-HTTP target. CM-22 will set this to the Application Insights / Azure Monitor endpoint. When unset, `OTEL_TRACES_EXPORTER=otlp` falls back to ConsoleSpanExporter rather than crashing. |
| `OTEL_EXPORTER_OTLP_HEADERS` | _(unset)_ | OTLP exporter headers (e.g. `Authorization=Bearer …`). Read by `OTLPSpanExporter` automatically. |
| `OTEL_SERVICE_VERSION` | `0.1.0` | Goes into the `service.version` resource attribute. |
| `OTEL_SERVICE_NAME` | _(set in code)_ | Overrides the `service_name` argument to `configure_otel`. Useful when the same image runs in multiple roles. |

`configure_otel(service_name=..., environment=...)` is idempotent — call
it again on hot-reload, in tests, or from a serverless cold-start without
worrying about double-init.

## `request_id` flow

`request_id` is the single value that joins:

* OTel **spans** (attribute `request_id`)
* OTel **baggage** (propagated to downstream services via HTTP headers)
* Structured **log lines** (read from the ContextVar by the formatter
  that lands with CM-27)

The ContextVar handles in-process async — including `asyncio.gather` and
FastAPI's per-request task isolation — so two concurrent requests never
see each other's id.

```python
from agents.observability import with_request_id, get_request_id, new_request_id

# Inbound boundary — pass the client-supplied id if present, else mint one:
async def handle(req: Request):
    client_id = req.headers.get("X-Request-Id")
    with with_request_id(client_id) as rid:
        # rid is set in: ContextVar, OTel baggage, active span attribute.
        ...
        outgoing_response.headers["X-Request-Id"] = rid

# Elsewhere in the code:
log.info("doing thing", extra={"request_id": get_request_id()})
```

Outside any `with_request_id(...)` scope, `get_request_id()` returns the
sentinel `"unknown"` — never raises. Spans emitted in that situation are
still well-formed (the `request_id` attribute is just `"unknown"`).

## Why openinference + Traceloop both?

The story names both explicitly. They overlap on LangChain/openai
auto-instrumentation, so we use them with **disjoint scopes**:

| Library | Scope | Why |
|---|---|---|
| `openinference-instrumentation-openai` | Raw `openai` SDK calls | Richer LLM-specific attributes than Traceloop's openai instrumentor (matches the shape Phoenix / Arize / LangSmith expect). |
| `traceloop-sdk` | LangChain + LlamaIndex | High-level chain tracing — wraps `LCEL` / `LangGraph` execution. We initialise it with `instruments={LANGCHAIN, LLAMA_INDEX}` so its built-in openai instrumentor is NOT installed and we don't double-emit. |
| `opentelemetry-instrumentation-fastapi` | FastAPI HTTP handlers | Standard. |
| `opentelemetry-instrumentation-httpx` | httpx clients | Standard. |

The disjointness is asserted in
`tests/observability/test_instrumentation.py::test_openai_span_is_owned_by_openinference`
— it mocks the OpenAI endpoint with `respx` and verifies the produced
span's `instrumentation_scope.name` contains `openinference`.

### Troubleshooting: double-emitted OpenAI spans

If you see two spans per OpenAI call (one from `openinference.openai` and
one from `traceloop.openai`), the Traceloop `instruments` allowlist isn't
being honoured by the installed Traceloop version. Workarounds:

1. **Pin Traceloop forward** to a version where the `instruments` kwarg
   carves openai out cleanly. Update `requirements-lock.txt`.
2. **Swap order**: in `agents/observability/instrumentation.py`, call
   `Traceloop.init(...)` first, then call
   `OpenAIInstrumentor().instrument()` (openinference) second. The last
   writer wins; openinference replaces Traceloop's wrapper.
3. **Uninstrument explicitly**: import
   `from traceloop.sdk.instruments import OpenAI as TraceloopOpenAI`
   and call its `.uninstrument()` after `Traceloop.init`.

## Manual span helpers

Auto-instrumentation covers FastAPI / httpx / openai / LangChain. It does
NOT cover bare LangGraph node functions — those need to open a span
themselves. Use `langgraph_node_span`:

```python
from agents.observability import langgraph_node_span

async def triage_node(state: AgentState) -> AgentState:
    with langgraph_node_span("triage", tenant_id=state.tenant_id) as span:
        result = await classify(state.message)
        span.set_attribute("intent", result.intent)
        return state.update(intent=result.intent)
```

Conventions:

* Span name is `langgraph.node.<node_name>` so backends can group spans
  by node.
* The current `request_id` is set as an attribute automatically.
* Extra kwargs become span attributes; `None` values are dropped so you
  can pass optionals without an `if`.

## Local demo

```bash
OTEL_TRACES_EXPORTER=console python -m agents.observability.demo
# In another terminal:
curl http://localhost:8000/echo/hello
```

You'll see spans printed for the FastAPI handler, the outbound httpx
call, and the openai call. Useful as a sanity check before wiring
Application Insights (CM-22).

## What comes next (forward references)

* **CM-22** wires `OTEL_EXPORTER_OTLP_ENDPOINT` to Application Insights;
  configures sampling (100% in dev, adaptive in prod); enables Live Metrics.
* **CM-23** turns on `LANGCHAIN_TRACING_V2` for LangSmith in dev.
* **CM-24** ships Langfuse Cloud Hobby for production LLM observations.
* **CM-25** builds Log Analytics workbooks over the spans flowing into
  App Insights from this story.
* **CM-27** adds structured JSON logging that reads `get_request_id()` so
  log lines join spans by `request_id` in the App Insights query language.
* **CM-28** is the first consumer of `langgraph_node_span`.
