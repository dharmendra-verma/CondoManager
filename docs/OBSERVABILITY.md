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

## LangSmith (dev only) — CM-23

LangSmith is the **developer-facing** trace backend for prompt iteration
and offline evals. Enabled by default on dev (Bicep param
`langsmithEnabled` defaults to `env == 'dev'`); off on prod by default
because CM-24 will wire Langfuse for prod LLM observations and running
both would double-pay per call.

| Env var | Set by | Effect |
|---|---|---|
| `LANGCHAIN_TRACING_V2` | Container App env block (`'true'`) | LangChain's native callback ships traces to LangSmith |
| `LANGCHAIN_API_KEY` | KV `secretRef` → `langsmith-api-key` | API key minted in the LangSmith UI |
| `LANGCHAIN_PROJECT` | `condomanager-<env>` (set in main.bicep) | Project routing in the LangSmith UI |
| `LANGCHAIN_ENDPOINT` | Bicep param, default `https://api.smith.langchain.com` | US default; override to `eu.api.smith.langchain.com` for EU |

### Dual emission with App Insights — by design

In dev, the same LangChain call emits to **both** backends:

| Pipeline | Layer hooked | Audience |
|---|---|---|
| Traceloop (CM-21) -> OTel -> App Insights (CM-22) | LangChain `Runnable` instrumentation | Ops view — latency / error / cost in App Insights workbooks (CM-25) |
| LangChain native callback (CM-23) -> LangSmith | LangChain `BaseCallbackHandler` | Dev view — prompt iteration, trace replay, offline evals |

The two pipelines hook **different** layers of LangChain — they don't fight
over span ownership. Tested for non-interference in
`tests/observability/test_langsmith.py::test_appi_branch_wins_when_langsmith_env_also_set`.

**Cost note.** LangSmith Hobby includes 5K traces/mo; expect to stay well
under in dev. Prod is intentionally off so the team doesn't double-pay
for LLM observability — Langfuse owns prod (CM-24). If LangSmith
ingestion ever becomes painful in dev, knob is the SDK env var
`LANGCHAIN_TRACING_SAMPLING_RATE` (0.0-1.0).

### Post-deploy one-time setup

```bash
# 1. Mint a LangSmith API key + create the project `condomanager-dev` in
#    the LangSmith UI (auto-creates on first trace; explicit creation lets
#    you set retention + sharing first).
# 2. Populate the KV secret:
az keyvault secret set --vault-name kv-condomanager-dev \
    --name langsmith-api-key --value "<langsmith-key>"
# 3. Force a Container App revision so it picks up the seeded secret:
az containerapp update --name ca-hello-condomanager-dev --resource-group rg-condomanager
# 4. Seed the eval dataset stub from tests/eval/triage_seed.jsonl:
LANGCHAIN_API_KEY="<key>" \
    python infra/scripts/seed-langsmith-dataset.py --env dev
```

Re-runs of the seed script are idempotent: it skips example uploads whose
`inputs` fingerprint already exists in the dataset, and skips dataset
creation if the dataset already exists.

### Manual smoke test (AC #3)

```bash
LANGCHAIN_TRACING_V2=true \
LANGCHAIN_API_KEY=<key> \
LANGCHAIN_PROJECT=condomanager-dev \
OPENAI_API_KEY=<openai-key> \
python -m agents.observability.langchain_demo --message "kitchen sink is leaking"
```

The trace should appear in the LangSmith UI under `condomanager-dev`
within ~30s. If env vars are missing or hold `REPLACE-ME`, the demo
exits 1 with a clear notice — it never silently runs without producing a
trace.

### Eval dataset workflow (CM-30 prep)

`tests/eval/triage_seed.jsonl` carries 10 stub examples covering the four
target Triage intents (maintenance / inquiry / escalation / follow-up).
CM-30 expands this to 200. The on-disk fixture is the source of truth:

```bash
# Edit the JSONL, then re-run the seed script.
# Already-uploaded examples are skipped (fingerprint = JSON of `inputs`).
python infra/scripts/seed-langsmith-dataset.py --env dev
```

## Operations workbook (CM-25)

Azure Workbook `CondoManager Ops — <env>` lives under
**Azure Portal → `appi-condomanager-<env>` → Workbooks → My workbooks**.
Backed by the CM-22 App Insights component (workspace-based, queries land
in the CM-16 Log Analytics workspace under the hood). Provisioned by
`infra/bicep/modules/workbook.bicep`; the serialized payload (5 sections:
header + time-range parameter + 4 KQL panels) is hand-written and lives
beside the Bicep at `workbook-payload.json`.

**Most panels will be empty until CM-28 (LangGraph spine) and CM-30
(Triage Agent) ship live LLM traffic.** The workbook is built now to
lock the OTel attribute schema and the `hitl.queued` / `hitl.resolved`
custom-events contract; future stories drop spans into these panels
without changing the workbook.

### Panels

| Panel | What it shows | Source rows / events |
|---|---|---|
| **Cost per day (USD)** | Daily LLM spend modeled from token counts × model-rate table | `dependencies` where `gen_ai.system == 'openai'` or `openinference.span.kind == 'LLM'` |
| **Latency p50 / p95 / p99** | Per-operation latency percentiles, 5-minute buckets | `requests` (FastAPI handlers — CM-21 OTel auto-instrumentation) |
| **Error rate per LangGraph node** | Per-node error percentage, hourly | `dependencies` where `customDimensions['langgraph.node']` is set |
| **HITL queue depth** | Cumulative `hitl.queued` − `hitl.resolved`, 5-minute buckets | `customEvents` named `hitl.queued` / `hitl.resolved` (contract — see below) |

### KQL queries — canonical source

The queries in `infra/bicep/modules/workbook-payload.json` are the **deployed**
source of truth; the same KQL is mirrored here so reviewers don't have to
unpack the JSON.

#### Cost per day

```kql
// Coalesces openinference and OTel gen_ai semantic conventions for token
// + model. Pricing table — update when adopting a new model.
let model_rates = datatable(model: string, prompt_per_token: real, completion_per_token: real) [
    'gpt-4o-mini',  0.00000015, 0.00000060,
    'gpt-4o',       0.00000250, 0.00001000,
    'gpt-4-turbo',  0.00001000, 0.00003000
];
dependencies
| where customDimensions['gen_ai.system'] == 'openai'
    or customDimensions['openinference.span.kind'] == 'LLM'
| extend
    model = coalesce(
        tostring(customDimensions['gen_ai.request.model']),
        tostring(customDimensions['llm.model_name'])
    ),
    prompt_tokens = toint(coalesce(
        customDimensions['gen_ai.usage.prompt_tokens'],
        customDimensions['llm.token_count.prompt']
    )),
    completion_tokens = toint(coalesce(
        customDimensions['gen_ai.usage.completion_tokens'],
        customDimensions['llm.token_count.completion']
    ))
| join kind=leftouter (model_rates) on model
| extend cost_usd =
    (prompt_tokens * coalesce(prompt_per_token, 0.0))
    + (completion_tokens * coalesce(completion_per_token, 0.0))
| summarize daily_cost_usd = sum(cost_usd) by bin(timestamp, 1d)
| order by timestamp asc
```

#### Latency p50 / p95 / p99

```kql
requests
| where success in (true, false)
| summarize
    p50_ms = percentile(duration, 50),
    p95_ms = percentile(duration, 95),
    p99_ms = percentile(duration, 99)
  by bin(timestamp, 5m), operation_Name
| order by timestamp desc
```

#### Error rate per LangGraph node

```kql
dependencies
| where isnotempty(tostring(customDimensions['langgraph.node']))
| extend agent = tostring(customDimensions['langgraph.node'])
| summarize
    total = count(),
    errors = countif(success == false)
  by bin(timestamp, 1h), agent
| extend error_rate_pct = todouble(errors) / total * 100.0
| project timestamp, agent, error_rate_pct
| order by timestamp desc
```

#### HITL queue depth

```kql
customEvents
| where name in ('hitl.queued', 'hitl.resolved')
| summarize
    queued = countif(name == 'hitl.queued'),
    resolved = countif(name == 'hitl.resolved')
  by bin(timestamp, 5m)
| order by timestamp asc
| extend running_queued = row_cumsum(queued)
| extend running_resolved = row_cumsum(resolved)
| extend queue_depth = running_queued - running_resolved
| project timestamp, queue_depth
```

### HITL events contract

The HITL queue panel reads `customEvents`. The future HITL story emits:

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

# When a task is pushed to a human reviewer:
with tracer.start_as_current_span("hitl.queued") as span:
    span.set_attribute("hitl.task_id", task_id)
    span.set_attribute("hitl.reason", reason)

# When the reviewer signs off:
with tracer.start_as_current_span("hitl.resolved") as span:
    span.set_attribute("hitl.task_id", task_id)
    span.set_attribute("hitl.outcome", outcome)
```

Stick to those literal event names so the workbook displays without changes.

### One-time pin to dashboard (operator step — AC #3)

Programmatic dashboard pinning is intentionally out of scope:
`Microsoft.Portal/dashboards` would pre-empt each operator's per-user
dashboard customization and the dashboard JSON breaks when Azure renames
the workbook part. The pin step is operator-driven:

1. Open the workbook in the Azure Portal (App Insights → Workbooks → "CondoManager Ops — dev").
2. Click **Edit**, then **Done editing** to enter the workbook view.
3. From the workbook header click **Pin** → **Pin all** (or select specific panels).
4. Pick the dashboard. Create a new one named `condomanager-ops-<env>` the first time.

### Why model-rate KQL, not Azure Cost Management?

* Cost Management's REST API surfaces *billed* spend with hours of latency
  and doesn't attribute per-request / per-agent. The workbook needs
  per-span granularity so the latency / errors / cost panels share the
  same span universe.
* The model-rate `datatable` is a 10-line block that updates rarely.
  Real-billing reconciliation lands in CM-26 (budget alerts) — that
  story can layer Cost Management on top of the same span scope.

### Why coalesce openinference + OTel `gen_ai.*`?

CM-21's `openinference-instrumentation-openai` emits
`llm.token_count.prompt` / `llm.token_count.completion` /
`llm.model_name`. The OTel semantic conventions for `gen_ai.*` are
stabilising and will gradually replace these. The KQL `coalesce(...)`
keeps the workbook working through that transition — neither side
needs to ship before the other.

## What comes next (forward references)

* **CM-26** reuses these KQL queries as Azure Monitor alert rules
  (`Microsoft.Insights/scheduledQueryRules`) for cost-budget, latency
  SLO, and guardrail-trip alerting. Same queries, different surface.
* **CM-27** adds structured JSON logging that reads `get_request_id()` so
  log lines join spans by `request_id` in the App Insights query language.
* **CM-28** is the first consumer of `langgraph_node_span` — the per-agent
  error panel lights up the moment CM-28 lands.
* **CM-30** extends `tests/eval/triage_seed.jsonl` to 200 examples and
  runs the Triage Agent eval against the LangSmith dataset seeded
  in CM-23.
