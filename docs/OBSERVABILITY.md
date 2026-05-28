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
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | _(unset)_ | **CM-22.** When set to a real value, delegates to `azure-monitor-opentelemetry` (Azure Monitor exporter + Live Metrics + sampler). Takes precedence over `OTEL_TRACES_EXPORTER`. The CM-18 placeholder `REPLACE-ME` is treated as if-unset so the Container App boots before the post-deploy seed step. |
| `OTEL_SAMPLER_RATIO` | `1.0` | **CM-22.** Client-side sampling ratio (Parent-based). `1.0` -> ParentBased(AlwaysOn); fractional values -> ParentBased(TraceIdRatioBased). Clamped to [0,1] with a warning on out-of-range. App Insights applies a second, server-side adaptive sampler on top (`SamplingPercentage` on the Bicep resource). |
| `OTEL_TRACES_EXPORTER` | `console` | `console` -> ConsoleSpanExporter; `otlp` -> OTLP-HTTP exporter (requires endpoint, see below); any other value -> no-op. **Ignored when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set.** |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(unset)_ | OTLP-HTTP target for non-App-Insights backends. When unset, `OTEL_TRACES_EXPORTER=otlp` falls back to ConsoleSpanExporter rather than crashing. |
| `OTEL_EXPORTER_OTLP_HEADERS` | _(unset)_ | OTLP exporter headers (e.g. `Authorization=Bearer …`). Read by `OTLPSpanExporter` automatically. |
| `OTEL_SERVICE_VERSION` | `0.1.0` | Goes into the `service.version` resource attribute. |
| `OTEL_SERVICE_NAME` | _(set in code)_ | Overrides the `service_name` argument to `configure_otel`. Useful when the same image runs in multiple roles. |

### Exporter precedence
1. `APPLICATIONINSIGHTS_CONNECTION_STRING` set (and not `REPLACE-ME`) -> Azure Monitor distro
2. `OTEL_TRACES_EXPORTER=otlp` AND `OTEL_EXPORTER_OTLP_ENDPOINT` set -> OTLP-HTTP
3. Otherwise -> ConsoleSpanExporter (default; what tests + local dev use)

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

## Application Insights backend (CM-22)

Workspace-based App Insights (`appi-condomanager-<env>`, linked to the
existing LAW from CM-16) is the OTLP backend. The connection string is
held in Key Vault as `app-insights-connection-string` (CM-18 schema) and
mounted into the Container App as `APPLICATIONINSIGHTS_CONNECTION_STRING`
via native `secretRef` resolved through the User-Assigned MI. Once that
env var has a real value, `configure_otel` flips into the Azure Monitor
branch automatically.

### Post-deploy one-time setup

```bash
# 1. Populate the KV secret from the deployment output (idempotent):
bash infra/scripts/seed-app-insights-secret.sh dev

# 2. Force a new revision so the Container App picks up the seeded value:
az containerapp update --name ca-hello-condomanager-dev --resource-group rg-condomanager

# 3. Hit the app; spans should appear in App Insights UI within ~30s.
```

The seed script reads `properties.outputs.appInsightsConnectionString.value`
from the latest deployment and writes it into KV. It refuses to overwrite
a real value that differs from the deployment output — operator rotations
are preserved.

### Layered sampling (AC #3)

* **SDK-side (Python).** `OTEL_SAMPLER_RATIO` (default 1.0) becomes a
  `ParentBased` sampler — `ParentBased(AlwaysOn)` at 1.0, otherwise
  `ParentBased(TraceIdRatioBased(r))`. Dev keeps 1.0; prod is typically
  0.5 — set per env by the operator (Container App `env` or `secretRef`).
* **Server-side (App Insights).** The `SamplingPercentage` property on
  the Bicep resource (`appInsightsSamplingPercentage` param on `main.bicep`,
  default 100 dev / 50 prod) drives App Insights' adaptive sampler at
  ingestion. Halves prod cost without changing client behavior.

Live Metrics is **unsampled** and rides above both samplers — operators
get a real-time view of cost / latency / errors even when traces are
heavily sampled.

### Troubleshooting

* **No spans in App Insights.** Verify the conn string was seeded:
  `az keyvault secret show --vault-name kv-condomanager-dev --name app-insights-connection-string`.
  If it's still `REPLACE-ME`, run `seed-app-insights-secret.sh dev`.
* **`configure_azure_monitor` API change.** Pin in `requirements-lock.txt`
  protects CI; if a bump breaks the `enable_live_metrics` or `sampler`
  kwarg, fall back to constructing `AzureMonitorTraceExporter` + Live
  Metrics manually (see `azure.monitor.opentelemetry.exporter`).
* **Server-side sampling masks issues.** Temporarily flip
  `SamplingPercentage` to 100 by redeploying with
  `--parameters appInsightsSamplingPercentage=100`. Live Metrics stays
  unsampled regardless.

## What comes next (forward references)

* **CM-23** turns on `LANGCHAIN_TRACING_V2` for LangSmith in dev.
* **CM-24** ships Langfuse Cloud Hobby for production LLM observations.
* **CM-25** builds Log Analytics workbooks over the spans flowing into
  App Insights from this story.
* **CM-27** adds structured JSON logging that reads `get_request_id()` so
  log lines join spans by `request_id` in the App Insights query language.
* **CM-28** is the first consumer of `langgraph_node_span`.
