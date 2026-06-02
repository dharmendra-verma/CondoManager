# CondoManager

Multi-agent platform for condominium maintenance & inquiry management. Tenants
reach the platform over **WhatsApp / Telegram / email / web**; a **LangGraph
orchestrator** classifies each message and routes it to a specialist agent:

| Agent | What it does |
|---|---|
| **Triage** | Classifies intent, urgency, and tone; routes to the right agent |
| **Maintenance** | Creates & deduplicates maintenance tickets; assigns priority + ETA |
| **Vendor** | Matches contractors and auto-dispatches or escalates for approval |
| **Knowledge** | Answers policy questions via vector RAG over Google Drive docs |
| **Escalation** | Handles sensitive cases with a legal-risk gate and HITL approval |
| **Analytics** | Produces weekly manager digests with trends and predictions |

Built on **Azure** (Cosmos DB with DiskANN vector search, Container Apps, Azure
Functions, Static Web Apps) with full **OpenTelemetry** observability (App
Insights, LangSmith, Langfuse) and a **TypeScript** tenant status portal.
PII masking, field-level access control, an append-only audit trail, and a
right-to-erasure routine back the SOC2 compliance posture.

Phased delivery is tracked in Jira project **CM**
([projecttracking.atlassian.net](https://projecttracking.atlassian.net/browse/CM-1)).

## Topology

* **One shared Azure resource group** (`rg-condomanager`) hosts both dev and
  prod workloads. Dev/prod are applied at the resource level via tags.
* **Two deployment stages** — `dev` and `prod` — implemented as GitHub
  Environments. Prod requires manual approval.

## For human engineers

**New to the project? Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** —
a ~15-minute bird's-eye tour of the whole system with diagrams, then dive into
the area you need below.

For first-time Azure setup (service-principal, GitHub secrets, GitHub
Environments), go to [`docs/INFRA.md`](docs/INFRA.md).

### Documentation map (`docs/`)

| Doc | Covers |
|---|---|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | **Start here.** End-to-end overview + diagrams; links to everything below. |
| [`AGENTS.md`](docs/AGENTS.md) | LangGraph orchestrator spine, `AgentState`, node contract, HITL, Maintenance / Knowledge / Vendor agents. |
| [`TRIAGE.md`](docs/TRIAGE.md) | Triage agent — intent / urgency / tone classification + routing. |
| [`ESCALATION.md`](docs/ESCALATION.md) | Escalation agent — legal gate, HITL, manager ratings. |
| [`ANALYTICS.md`](docs/ANALYTICS.md) | Analytics agent — weekly digest analyzers. |
| [`CHANNELS.md`](docs/CHANNELS.md) | Channel adapters — normalizing WhatsApp / Telegram / email / web into one shape. |
| [`FUNCTIONS.md`](docs/FUNCTIONS.md) | Background timer jobs — knowledge sync + analytics digest. |
| [`PORTAL.md`](docs/PORTAL.md) | Read-only tenant status portal (TypeScript SWA). |
| [`OBSERVABILITY.md`](docs/OBSERVABILITY.md) | Traces, structured logs, PII masking, PRD metrics, dashboards, alerts. |
| [`SECURITY.md`](docs/SECURITY.md) | PII detection/masking, field RBAC, audit trail, retention — SOC2 control matrix. |
| [`PRODUCTION-READINESS.md`](docs/PRODUCTION-READINESS.md) | The private-beta go/no-go gate + eval suite. |
| [`INFRA.md`](docs/INFRA.md) | Azure resources (Bicep), Cosmos, Key Vault, one-time setup. |
| [`CICD.md`](docs/CICD.md) | GitHub Actions pipelines, OIDC, deploy flow. |

## For AI coding agents (Claude Code, Cursor, etc.)

**Read these two files first** — they encode every decision already made so
you don't have to re-ask the human:

* [`CLAUDE.md`](CLAUDE.md) — project context, conventions, tech stack, naming,
  tagging, story workflow, Jira IDs.
* [`AGENT_RULES.md`](AGENT_RULES.md) — autonomy contract. Lists what to do
  without asking and the (short) list of things that still need human OK.

In short: **act, then report**. Don't ask permission for every step.

## Repository layout

```
.
├── CLAUDE.md, AGENT_RULES.md     # Agent briefing (read these first)
├── agents/                       # Python: orchestrator + specialist agents
│   ├── orchestrator/             #   LangGraph spine + nodes (CM-28)
│   ├── channels/                 #   channel adapters → NormalizedMessage (CM-29)
│   ├── knowledge/                #   RAG read + Drive→Cosmos write (CM-33/34)
│   ├── maintenance/ vendor/      #   ticketing + vendor dispatch (CM-31/35)
│   ├── analytics/                #   weekly digest analyzers (CM-36)
│   ├── observability/            #   OTel, logging, PII, metrics (CM-21..45)
│   └── eval/                     #   offline eval suite (CM-39)
├── functions/                    # Azure Functions timer jobs (gdrive-sync, analytics-digest)
├── portal/                       # TypeScript tenant status portal (CM-37)
├── infra/                        # Bicep IaC
├── .github/workflows/            # GitHub Actions: lint, what-if, deploy
├── tests/                        # Per-area test scripts
└── docs/                         # Operator + onboarding docs (start: ARCHITECTURE.md)
```

## Conventions (the short version)

* **Branch naming:** `feature/<JIRA-KEY>-<short-slug>`
* **Commit messages:** Conventional Commits, prefixed with the Jira key,
  e.g. `feat(infra): CM-15 provision shared RG (dev+prod)`.
* **PR titles:** include the Jira key so smart commits sync status to Jira.

See [`CLAUDE.md`](CLAUDE.md) §3 for the full set of conventions.
