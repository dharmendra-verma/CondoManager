# CondoManager Infrastructure

This directory tracks the Azure infrastructure for the CondoManager platform.

## Topology — single shared Resource Group

CM-15 provisions **one** Resource Group (`rg-condomanager`) that hosts both
`dev` and `prod` workloads. The dev/prod distinction is applied at the
**resource** level (e.g. `cosmos-condomanager-dev` vs `cosmos-condomanager-prod`)
in later stories under Epic CM-1, not at the RG level.

This keeps management overhead and Azure cost low while still giving us two
deployment stages via GitHub Environments.

## Naming convention

| Resource type   | Pattern                       | Example                  |
|-----------------|-------------------------------|--------------------------|
| Resource Group  | `rg-condomanager` (shared)    | `rg-condomanager`        |
| Per-env resource| `<resource>-condomanager-<env>` | `cosmos-condomanager-dev`|
| Region          | Same region for all resources | `eastus2`                |

## Tagging convention (required)

Every resource provisioned by Bicep MUST carry these tags. The schema lives in
`infra/bicep/tags.bicep` (resource-group scope) and is inlined in
`infra/bicep/main.bicep` for the RG itself.

| Tag           | RG value          | Per-env resource value    |
|---------------|-------------------|---------------------------|
| `env`         | `shared`          | `dev` or `prod`           |
| `owner`       | `platform-team`   | `platform-team`           |
| `cost-center` | `cc-condomanager` | `cc-condomanager`         |
| `project`     | `condo-manager`   | `condo-manager`           |
| `managed-by`  | `bicep`           | `bicep`                   |

## Files

```
infra/
├── bicep/
│   ├── main.bicep                            # RG-scoped: orchestrates all modules
│   ├── tags.bicep                            # Reusable tag schema (env: dev|prod|shared)
│   ├── main.parameters.json                  # Single parameters file (env=dev today)
│   └── modules/                              # Per-resource Bicep modules
│       ├── vnet.bicep                        # VNet + /23 subnet delegated to Container Apps (CM-16)
│       ├── log-analytics.bicep               # Log Analytics workspace for app logs (CM-16)
│       ├── container-apps-env.bicep          # Container Apps Managed Environment (CM-16)
│       ├── container-app.bicep               # Hello-world Container App + MI attachment (CM-16, CM-18)
│       ├── cosmos.bicep                      # Cosmos DB account + db + 8 containers (CM-17; +checkpoints CM-28; +knowledge_sync CM-34; +escalations CM-32; +digests CM-36)
│       ├── managed-identity.bicep            # User-Assigned MI shared by workloads (CM-18)
│       ├── keyvault.bicep                    # Key Vault (RBAC) + MI role assignment (CM-18)
│       ├── acr.bicep                         # Azure Container Registry (Basic SKU) (CM-20)
│       ├── app-insights.bicep                # Workspace-based App Insights, OTLP backend (CM-22)
│       ├── workbook.bicep                    # Operations workbook over App Insights — cost/latency/errors/HITL (CM-25)
│       ├── workbook-payload.json             # Serialized workbook payload (4 KQL panels + time-range parameter) (CM-25)
│       ├── action-group.bicep                # Shared Action Group (Slack + email receivers, conditional) (CM-26)
│       ├── budget.bicep                      # Consumption Budget with 50/80/100% Actual thresholds (CM-26)
│       └── alert-rules.bicep                 # 3 scheduled-query rules: latency SLO, guardrail trip, hallucination spike (CM-26)
├── docker/
│   └── base/Dockerfile                       # Curated python:3.12-slim base image (CM-20)
└── scripts/
    ├── cosmos-smoke-test.py                  # Post-deploy validation for Cosmos vector search (CM-17)
    ├── seed-keyvault-secrets.sh              # Seed the 9 initial secret names with REPLACE-ME (CM-18, +1 in CM-22)
    ├── keyvault-smoke-test.py                # Post-deploy validation: MI → KV read (CM-18)
    ├── acr-prune.sh                          # Basic-SKU equivalent of ACR retention policy (CM-20)
    ├── seed-app-insights-secret.sh           # Post-deploy: populate KV with the AppI conn string (CM-22)
    └── seed-langsmith-dataset.py             # Post-deploy: upload tests/eval/triage_seed.jsonl to LangSmith (CM-23)
agents/
├── observability/                            # OTel SDK + auto-instrumentation + request_id (CM-21, AppI in CM-22, LangSmith in CM-23)
└── orchestrator/                             # LangGraph spine: AgentState + stub nodes + guardrails + Cosmos checkpointer (CM-28)
.github/
└── workflows/
    ├── build.yml                             # PR + push:main · per-area lint/what-if + summary comment (CM-19, + python area in CM-21)
    ├── deploy.yml                            # push:main → deploy-dev, release:published → deploy-prod (CM-19)
    └── base-image.yml                        # Base image build/scan/push (CM-20)
tests/
├── infra/
│   └── test_bicep_lint.sh                    # Bicep lint runs in CI on every PR touching infra
├── observability/                            # pytest suite for agents/observability/ (CM-21, + AppI in CM-22, + LangSmith in CM-23)
├── orchestrator/                             # pytest suite for agents/orchestrator/ (CM-28)
└── eval/                                     # Eval dataset fixtures — CM-30 extends triage_seed.jsonl to 200 (CM-23)
docs/
├── INFRA.md                                  # this file
├── CICD.md                                   # CI/CD operator guide (CM-19)
├── OBSERVABILITY.md                          # OTel + request_id + manual span helpers (CM-21, + AppI in CM-22, + LangSmith in CM-23)
└── AGENTS.md                                 # LangGraph spine reference: AgentState, nodes, HITL contract, guardrails (CM-28)
pyproject.toml                                # Python project metadata (CM-21)
requirements-lock.txt                         # pinned transitive Python deps (CM-21)
```

## How CI/CD works (CM-19)

Two workflows of single responsibility each. Detailed operator guide is in [`docs/CICD.md`](CICD.md).

| Event                                     | Workflow       | Job                       | Environment | Approval     |
|-------------------------------------------|----------------|---------------------------|-------------|--------------|
| `pull_request` (any path)                 | `build.yml`    | `detect` → `lint-infra` → `what-if-infra` → `summary` | _none_ | _none_ |
| `push:main` (touches `infra/`)            | `build.yml`    | `detect` → `lint-infra`   | _none_      | _none_       |
| `push:main` (touches `infra/`)            | `deploy.yml`   | `deploy-dev`              | `dev`       | none         |
| `release:published`                       | `deploy.yml`   | `deploy-prod`             | `prod`      | manual       |
| `workflow_dispatch (target_env=dev)`      | `deploy.yml`   | `deploy-dev`              | `dev`       | none         |
| `workflow_dispatch (target_env=prod)`     | `deploy.yml`   | `deploy-prod`             | `prod`      | manual       |

All Azure auth uses **OIDC federated credentials** — no long-lived secrets, no PATs.
The PR summary comment uses the built-in `GITHUB_TOKEN`. `marocchino/sticky-pull-request-comment@v2`
posts a single comment that updates in-place across PR pushes (header `ci-summary`).

### Why no staging?

The Jira AC for CM-19 mentioned a `tag → staging` deploy, but the project has only
**dev and prod** ([`CLAUDE.md §3`](../CLAUDE.md)). The mapping above implements the
spirit of the AC within the agreed topology: `push:main → dev`, `release:published → prod`.
The `workflow_dispatch` input is the escape hatch for any out-of-band re-deploy.

### Cutting a prod release

```bash
# 1. Tag the commit you want to ship
git tag -a v0.1.0 -m "Foundation phase: Container Apps + Cosmos + Key Vault wired"

# 2. Push the tag, then publish a release (this fires deploy.yml → deploy-prod)
git push origin v0.1.0
gh release create v0.1.0 --notes-from-tag
```

After publish, `deploy-prod` enters the `prod` environment and waits for an approver
before running `az deployment group create --parameters env=prod` against `rg-condomanager`.

## One-time setup (manual, by repo owner)

The bootstrap is captured in **`infra/scripts/setup-azure-oidc.sh`**. Run it
once in Azure Cloud Shell on the account that owns the target subscription —
the script is **idempotent**, so re-running it (e.g. after CM-43 added the
new RG-scoped role grant, or after CM-41 added provider registration) only
applies what's missing.

```bash
# In https://shell.azure.com (or any az-CLI environment with Owner / UAA on the sub)
bash infra/scripts/setup-azure-oidc.sh
```

What it does:

| Step | Action |
|------|--------|
| 1 | Create / reuse Azure AD app + service principal `github-condomanager-infra` |
| 2 | Grant **`Contributor`** at **subscription scope** (broad deploy permissions) |
| 3 | (CM-43) Grant **`User Access Administrator`** at **`rg-condomanager` scope** — needed because Bicep modules under `infra/bicep/modules/` declare `Microsoft.Authorization/roleAssignments` resources (e.g. `keyvault.bicep` grants the shared MI `Key Vault Secrets User`). `Contributor` does not include `roleAssignments/write`. UAA is scoped to the single RG to limit blast radius. |
| 4 | (CM-41) **Register the Azure resource providers** every deploy needs (`Microsoft.Network`, `Microsoft.OperationalInsights`, `Microsoft.App`, `Microsoft.DocumentDB`, `Microsoft.KeyVault`, `Microsoft.ContainerRegistry`, `Microsoft.Insights`) and **wait** until each reaches `Registered` (≤ 5 min/provider). Idempotent — already-registered namespaces are skipped instantly. |
| 5 | Create / reuse 4 federated credentials (`github-main`, `github-pull-request`, `github-env-dev`, `github-env-prod`) — used by `build.yml` and `deploy.yml` for OIDC token exchange. |
| 6 | Print the three public identifiers to paste into GitHub Actions secrets. |

> **Operator note for CM-43:** if you've already run this script before
> CM-43 landed, re-run it once after this PR merges to pick up the new
> `User Access Administrator` grant. Without it, `deploy.yml → deploy-dev`
> fails with `Authorization failed ... 'Microsoft.Authorization/roleAssignments/write'`.

> **Why the CI principal can't register providers itself (CM-41):**
> `az provider register` is a **subscription-level** operation, but the GitHub
> Actions service principal is deliberately scoped to `Contributor` on
> `rg-condomanager` **only** (CM-15, to bound its blast radius). So the deploy
> workflow cannot self-heal a missing provider — the first deploy of any new
> resource type would fail with
> `MissingSubscriptionRegistration: The subscription is not registered to use
> namespace 'Microsoft.<X>'` (this is exactly how CM-16's first run died on
> `Microsoft.Network`). Registering the providers here — once, at subscription
> scope, as the owner — closes that gap without widening the CI principal's
> permissions. Re-run this script after CM-41 merges to pick up any providers
> your subscription hasn't registered yet.

### Required GitHub secrets

In `Settings → Secrets and variables → Actions → New repository secret`:

| Secret name             | Source                                         |
|-------------------------|------------------------------------------------|
| `AZURE_CLIENT_ID`       | App registration `Application (client) ID`     |
| `AZURE_TENANT_ID`       | Azure AD tenant ID                             |
| `AZURE_SUBSCRIPTION_ID` | Target subscription ID                         |

### Required GitHub environments

In `Settings → Environments → New environment`:

| Environment | Used by                       | Reviewers required           |
|-------------|-------------------------------|------------------------------|
| `dev`       | future per-env resource jobs  | none                         |
| `prod`      | shared RG + per-env resources | at least one approver        |

## Deploying manually (smoke test)

The shared RG (`rg-condomanager`) is bootstrapped out-of-band — see
`infra/scripts/setup-azure-oidc.sh`. Once it exists, all subsequent
deployments are RG-scoped. `main.bicep` is resource-group scoped and
requires the `env` parameter (controls naming of per-env resources like
`cosmos-condomanager-<env>`):

```bash
az login
az account set --subscription <SUBSCRIPTION_ID>
az deployment group create \
  --resource-group rg-condomanager \
  --name cm-manual-$(date +%s) \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json \
  --parameters env=dev
```

## Container Apps environment (CM-16)

```
VNet  vnet-condomanager-dev      10.0.0.0/16
 └── snet-containerapps-dev      10.0.0.0/23   (delegated to Microsoft.App/environments)

Log Analytics  law-condomanager-dev     PerGB2018, 30-day retention

Container Apps env  cae-condomanager-dev
 ├── Workload profile: Consumption  (free tier: 180K vCPU-sec/mo)
 ├── VNet integration: snet-containerapps-dev
 └── App logs: → law-condomanager-dev

Container App  ca-hello-condomanager-dev
 ├── Image:  mcr.microsoft.com/k8s/demo/hello-app:1.0
 ├── Resources: 0.25 vCPU / 0.5 Gi memory
 ├── Scale:  0–1 replicas (scale-to-zero when idle)
 └── Ingress: external, targetPort 8080
```

### Free-tier cost guard

- **Container Apps Consumption** grant: 180,000 vCPU-seconds and 400,000 GiB-seconds per month per subscription.
- The hello-world app uses 0.25 vCPU / 0.5 GiB with `minReplicas: 0`, so it consumes **zero vCPU-seconds while idle** and roughly 900 vCPU-sec per hour of continuous traffic — well under the monthly grant.
- **Log Analytics** grant: 5 GB ingestion per month, 31-day retention free. The hello-world generates minimal logs.

### Smoke test after deploy

After the CI deploy or a manual deploy lands:

```bash
# Grab the FQDN from the deployment outputs
FQDN=$(az deployment group show \
  --resource-group rg-condomanager \
  --name cm-manual \
  --query 'properties.outputs.containerAppFqdn.value' -o tsv)

# Curl it (first hit may take ~10s as the app scales from 0 to 1)
curl -i "https://${FQDN}/"
# expected: HTTP/2 200 + a "Hello, world!" body from mcr.microsoft.com/k8s/demo/hello-app
```

## Cosmos DB (CM-17)

A single Cosmos DB account per environment hosts both transactional data
and RAG vector embeddings.

| Aspect            | Value                                                            |
|-------------------|------------------------------------------------------------------|
| Account name      | `cosmos-condomanager-<env>`                                      |
| API               | NoSQL (Core SQL)                                                 |
| Capabilities      | `EnableNoSQLVectorSearch`                                        |
| Consistency       | Session                                                          |
| Free tier         | Enabled by default (25 GB + 1000 RU/s). One per subscription.    |
| Database          | `condomanager` — shared throughput, 1000 RU/s                    |
| Region            | `eastus2`                                                        |

### Containers

| Container         | Partition key  | Notes                                          |
|-------------------|----------------|------------------------------------------------|
| `tenants`         | `/id`          | One doc per tenant; point-reads by tenant ID   |
| `tickets`         | `/tenantId`    | Tenant-scoped ticket queries stay single-part. |
| `conversations`   | `/ticketId`    | All messages for a ticket live in one partition|
| `policies-vector` | `/tenantId`    | RAG embeddings + DiskANN index on `/embedding` |

### Vector search

`policies-vector` carries a `vectorEmbeddingPolicy` and a DiskANN
`vectorIndex` on `/embedding`. The raw embedding floats are excluded
from the standard index (saves RUs) — `VectorDistance()` SQL queries
hit the DiskANN index instead.

Default embedding dimensions: **1536** (matches OpenAI
`text-embedding-ada-002` and `text-embedding-3-small`). Switch to 3072
for `text-embedding-3-large` by overriding `cosmosVectorDimensions` in
`main.parameters.json`.

### Post-deploy smoke-test

`infra/scripts/cosmos-smoke-test.py` validates AC #4 ("Sample insert +
vector query validated"). Requires `azure-cosmos>=4.7.0`. Run AFTER
`az deployment group create` succeeds:

```bash
pip install "azure-cosmos>=4.7.0"

# Pull endpoint + key from the deployed account
COSMOS_ENDPOINT=$(az cosmosdb show \
  --resource-group rg-condomanager \
  --name cosmos-condomanager-dev \
  --query documentEndpoint -o tsv)
COSMOS_KEY=$(az cosmosdb keys list \
  --resource-group rg-condomanager \
  --name cosmos-condomanager-dev \
  --query primaryMasterKey -o tsv)

export COSMOS_ENDPOINT COSMOS_KEY
python infra/scripts/cosmos-smoke-test.py
```

The script inserts a dummy 1536-dim vector, queries it back with
`VectorDistance()`, asserts the inserted doc is the nearest neighbour,
and cleans up. Exit 0 means the account is wired correctly.

## Key Vault & Managed Identity (CM-18)

Key Vault is the single secret store. The only principal that can read
secret values is a User-Assigned Managed Identity attached to every
CondoManager workload.

| Aspect          | Value                                                       |
|-----------------|-------------------------------------------------------------|
| Vault name      | `kv-condomanager-<env>`                                     |
| Mode            | RBAC (no access policies)                                   |
| SKU             | Standard                                                    |
| Soft-delete     | 90 days                                                     |
| Purge protect.  | Enabled (irreversible — vault name reserved 90d after delete) |
| Public network  | `Enabled` for now; private endpoint in a later story        |
| MI name         | `id-condomanager-<env>` (User-Assigned, shared by all apps) |
| Role on vault   | `Key Vault Secrets User` (read-only on values)              |

User-Assigned (not System-Assigned) so the MI survives Container App
re-creation, can be RBAC-granted before any app exists, and is shared
by multiple workloads with a single role assignment per resource.

### Initial secret schema

`infra/bicep/modules/keyvault.bicep` declares the names (`secretNames`
param default); `infra/scripts/seed-keyvault-secrets.sh` populates each
with the literal placeholder `REPLACE-ME` so the schema exists. Real
values are NEVER written through IaC or the seed script — they're set
out-of-band by operators with `az keyvault secret set`. The lint test
diffs the two name lists to prevent drift.

| Secret name                 | Source                                              | Rotation                       |
|-----------------------------|-----------------------------------------------------|--------------------------------|
| `azure-openai-key`          | Azure OpenAI portal                                 | Every 90d; key1 ↔ key2         |
| `twilio-account-sid`        | Twilio console                                      | Rarely changes                 |
| `twilio-auth-token`         | Twilio console                                      | Quarterly                      |
| `twilio-whatsapp-number`    | Twilio console (phone number, not a secret per se)  | On reassignment                |
| `langsmith-api-key`         | LangSmith UI                                        | Quarterly                      |
| `langfuse-public-key`       | Langfuse UI (public, low-risk)                      | On project reset               |
| `langfuse-secret-key`       | Langfuse UI                                         | Quarterly                      |
| `cosmos-connection-string`  | `az cosmosdb keys list --type connection-strings`   | Primary ↔ secondary swap       |
| `slack-webhook-url`         | Slack incoming-webhook (CM-32 manager alerts)       | On channel/app reconfig        |

### First-time setup after deploy

```bash
# 1. Seed the schema with REPLACE-ME placeholders
bash infra/scripts/seed-keyvault-secrets.sh kv-condomanager-dev

# 2. Replace placeholders with real values, one at a time
az keyvault secret set --vault-name kv-condomanager-dev \
    --name azure-openai-key --value "<real-value>"
# ... repeat for the other 7 secrets

# 3. Smoke-test that the MI can actually read a secret
pip install "azure-identity>=1.15.0" "azure-keyvault-secrets>=4.7.0"
python infra/scripts/keyvault-smoke-test.py --vault kv-condomanager-dev
```

The smoke-test uses `DefaultAzureCredential`, so it works both locally
(via `az login`) and from inside the Container App (via the attached
User-Assigned MI). Either succeeds once the role assignment has
propagated through Azure AD (usually under a minute).

### Rotation policy

- **Quarterly cadence** for app credentials (Twilio, LangSmith, Langfuse).
  Calendar reminder is owned by `platform-team`.
- **On-demand** for Azure OpenAI and Cosmos: rotate using the key1 → key2
  swap pattern (update the KV secret to key2, regenerate key1, then
  optionally swap back next rotation).
- **Never delete** an old secret version — Key Vault keeps history, and
  running Container App revisions may still reference older versions
  until they're replaced.
- **Always smoke-test after a rotation** with `keyvault-smoke-test.py`.

### Why values aren't in IaC

A Bicep `Microsoft.KeyVault/vaults/secrets` resource requires a
`value` property. That value would then live in source, in deployment
history, and in any exported ARM template — none acceptable. So Bicep
declares the vault + the MI binding only; values come from out-of-band
`az keyvault secret set` calls (manual today, possibly an Azure DevOps
release pipeline later, but never from this repo).

## Azure Container Registry & base image (CM-20)

ACR holds the curated Python base image that future agent-runtime services
build FROM. The hello-world Container App still pulls from MCR; ACR's
first real consumers are later stories.

| Aspect          | Value                                                                |
|-----------------|----------------------------------------------------------------------|
| Registry name   | `acrcondomanager<env>` (no hyphens — ACR forbids them; see below)    |
| SKU             | Basic                                                                |
| Admin user      | Disabled (AAD / OIDC + Managed Identity only)                        |
| Public network  | Enabled (private endpoint requires Premium SKU)                      |
| Anonymous pull  | Disabled                                                             |
| Base image      | `acrcondomanager<env>.azurecr.io/base/python:3.12-slim-<YYYYMMDD>` + `…-latest` |
| Build pipeline  | `.github/workflows/base-image.yml` (PR / push / Sun 06:00 UTC cron / dispatch) |
| CVE scanner     | Trivy — fails on HIGH/CRITICAL with an upstream fix; ignores unfixed |
| Retention       | `infra/scripts/acr-prune.sh` — keeps last 5 tagged per repo + deletes untagged |

### Naming exception
ACR registry names must be alphanumeric only, 5–50 chars, and globally
unique. Hyphens are forbidden, so `acr.bicep` is the *one* module that
deviates from the `<resource>-condomanager-<env>` convention. The tokens
are otherwise identical, just concatenated: `acrcondomanagerdev`,
`acrcondomanagerprod`.

### One-time setup after first deploy
Grant the GitHub Actions OIDC SP push permission on the registry —
RG-Contributor (from CM-15) is control-plane only and does NOT include
data-plane push:

```bash
SP_OBJECT_ID=$(az ad sp show --id "$AZURE_CLIENT_ID" --query id -o tsv)
ACR_ID=$(az acr show --name acrcondomanagerdev --query id -o tsv)
az role assignment create --assignee "$SP_OBJECT_ID" --role AcrPush --scope "$ACR_ID"
```

Future Container Apps that pull this image also need `AcrPull`, granted
to the User-Assigned MI from CM-18. That's a per-consumer-story step.

### CVE policy
The Trivy scan fails on `HIGH,CRITICAL` severities **only when an upstream
fix exists** (`ignore-unfixed: true`). Options when a fix-available
CRITICAL surfaces:

1. **Preferred:** the weekly cron rebuilds from `python:3.12-slim`, which
   picks up the upstream apt patch automatically. Just re-run the workflow.
2. **Override:** add the CVE to `.trivyignore` at the repo root with a
   one-line justification + an expiry date. Re-evaluate at the next rotation.

### Why Basic SKU + a script instead of ACR's retention policy
ACR's built-in `policies.retentionPolicy` is a Premium-only feature. AC #1
fixes the SKU at Basic, so `acr-prune.sh` substitutes — invoked from the
weekly cron of `base-image.yml`, it keeps the last 5 tagged manifests per
repo and deletes untagged ones. Upgrade to Premium gives back the built-in
policy plus geo-replication and private endpoint — defer until a real
consumer (or a compliance requirement) demands it.

### Trigger semantics for `base-image.yml`
| Event | Build | Trivy | Push | Prune |
|---|---|---|---|---|
| Pull request (any) | ✓ | ✓ | — | — |
| Push to `main` (Dockerfile or workflow changes) | ✓ | ✓ | ✓ | — |
| `schedule` (Sun 06:00 UTC) | ✓ | ✓ | ✓ | ✓ |
| `workflow_dispatch` | ✓ | ✓ | ✓ | — |

PR runs intentionally skip Azure login + push so fork PRs work and tentative
builds don't pollute the registry. The Sunday cron is the canonical
retention cadence — manual / push-triggered builds just push and let the
next cron prune.

### Manual prune
```bash
az login
bash infra/scripts/acr-prune.sh acrcondomanagerdev base/python 5
```
Idempotent; second run does zero deletes if the repo is already at <= N tagged.

## LLM trace observability — Langfuse Cloud (CM-24)

Production-only LLM observability via [Langfuse Cloud Hobby](https://cloud.langfuse.com)
(free tier — 50K observations/month). Coexists with App Insights (CM-22):
App Insights is engineering observability (HTTP / DB / queue spans);
Langfuse is the LLM-specific overlay (cost per call, token counts, latency
distributions per LangGraph node, hallucination signals).

| Aspect            | Value                                                            |
|-------------------|------------------------------------------------------------------|
| Backend           | Langfuse Cloud Hobby, region: EU                                 |
| Tier              | Hobby (free, 50K observations/mo)                                |
| SDK               | `langfuse>=2.40,<3` (Python)                                     |
| Primary surface   | `@observe_node` decorator (see `agents/observability/langfuse_export.py`) |
| Trigger           | both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set (non-`REPLACE-ME`) |
| Killswitch        | `LANGFUSE_ENABLED=false` forces off even with keys set           |

### Env-var contract

| Env var                 | Source                                       | Default                          |
|-------------------------|----------------------------------------------|----------------------------------|
| `LANGFUSE_PUBLIC_KEY`   | KV secret `langfuse-public-key` via CM-26    | unset → Langfuse disabled        |
| `LANGFUSE_SECRET_KEY`   | KV secret `langfuse-secret-key` via CM-26    | unset → Langfuse disabled        |
| `LANGFUSE_HOST`         | literal in Bicep (no secret)                 | `https://cloud.langfuse.com`     |
| `LANGFUSE_ENABLED`      | optional override                            | unset (use key presence)         |

`agents/observability/langfuse_export.py` reads these at call time, so
operators can toggle the killswitch by patching the Container App
revision without redeploying the image.

### One-time operator setup

These steps are out-of-band — the Dev Agent cannot do them. Run after the
PR for CM-24 merges AND after CM-43's `User Access Administrator` grant
has been applied (else step 3 errors with `roleAssignments/write`):

1. **Sign up + create project.** [cloud.langfuse.com](https://cloud.langfuse.com) →
   create the `condomanager-prod` project on the Hobby tier (free).
2. **Generate API keys.** Project → Settings → API Keys → Create. Save
   the public key (`pk_…`) and secret key (`sk_…`) — these are sensitive,
   treat them like the Twilio auth token.
3. **Set the two KV secrets** in `kv-condomanager-prod`:
   ```bash
   az keyvault secret set --vault-name kv-condomanager-prod \
       --name langfuse-public-key --value <pk_…>
   az keyvault secret set --vault-name kv-condomanager-prod \
       --name langfuse-secret-key --value <sk_…>
   ```
4. **Build three dashboards** in the Langfuse UI under Dashboards → New
   Dashboard. Queries (Langfuse query builder, all filter `env=prod`):

   | Dashboard | Chart | Metric | Group by | Why |
   |-----------|-------|--------|----------|-----|
   | Cost/day | Bar | `total_cost` | `day` | Detect budget creep before the CM-26 Azure-Monitor alert fires |
   | p95 latency | Line | `latency_ms` percentile 95 | `hour` | Plan-mode tier degradation, vendor incident detection |
   | Error rate | Line | `error_rate` | `agent_name` (from metadata) | Stuck nodes surface before tenant tickets arrive |

   These are starting templates — the UI lets the operator tweak time
   windows and add per-tenant filters (`tenant_id` is attached as
   observation metadata once that contextvar lands in a later story).

### Wiring future LangGraph nodes

When orchestrator nodes land (CM-28+), decorate each "key" node with
`@observe_node("triage.classify")` etc. so the dashboard's per-agent
breakdown shows real data. The decorator is a transparent no-op
locally (Langfuse keys unset) — no test changes required.

## Google Drive → Cosmos vector sync (CM-34)

A scheduled Azure Functions Timer job keeps the Cosmos `policies-vector`
container in lockstep with a Google Drive folder of policy/SOP documents, so
the Knowledge Agent (CM-33) always retrieves current rules.

| Aspect            | Value                                                            |
|-------------------|------------------------------------------------------------------|
| Function App      | `func-condomanager-<env>` (Linux, Python 3.12)                   |
| Plan              | `plan-condomanager-<env>-fn` — Y1 Consumption (scale-to-zero)    |
| Storage           | `stcondomanager<env>fn` (Standard_LRS, required by the runtime)  |
| Schedule          | every 30 min — NCRONTAB `0 */30 * * * *`                         |
| Identity          | shared User-Assigned MI `id-condomanager-<env>` (CM-18)          |
| Code              | `functions/gdrive-sync/` (logic in `agents/knowledge/`)          |
| State container   | `knowledge_sync` (partition `/source`) — Drive page token + hashes |
| Vector target     | `policies-vector` (CM-17), `text-embedding-3-small` @ 1536 dims  |

### How it works

1. **Delta detection** rides the Drive **Changes API**: the `startPageToken`
   is persisted in a `knowledge_sync` doc and replayed each run, so only
   modified docs come back. The first run has no token, so it enumerates the
   folder once and captures a token for next time.
2. A **content-hash guard** skips even Drive-reported changes whose text is
   unchanged — no wasted embedding spend.
3. Changed docs are **chunked** (`RecursiveCharacterTextSplitter`, ~300 words)
   and **re-embedded**, then upserted with **deterministic chunk ids**
   (`{tenantId}:{doc_id}:{chunk_index}`). A re-run on identical content writes
   the same ids — **idempotent, no duplicate chunks**. After a re-index,
   trailing chunks from a now-shorter doc are deleted.
4. **Observability**: each run emits structured `gdrive_sync.run` (summary) and
   per-doc `gdrive_sync.doc` log lines via the CM-27 JSON logger, which flow to
   App Insights / Log Analytics — query run history + per-doc status in KQL.

### Config (Function App settings, wired by `functions.bicep`)

Non-secret app settings are set at deploy time; secrets mount as Key Vault
references resolved by the MI:

| Setting | Source |
|---|---|
| `COSMOS_ENDPOINT` | cosmos module output |
| `GDRIVE_FOLDER_ID` | `--parameters gdriveFolderId=...` (operator) |
| `GDRIVE_TENANT_ID` | `--parameters gdriveTenantId=...` (default `default`) |
| `AZURE_OPENAI_ENDPOINT` | `--parameters azureOpenAiEndpoint=...` (operator) |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | default `text-embedding-3-small` |
| `GOOGLE_DRIVE_SA_KEY` | KV reference → `google-drive-sa-key` |
| `AZURE_OPENAI_API_KEY` | KV reference → `azure-openai-key` |

The function **skips cleanly** (logs `skipped — unconfigured`) until the Drive
folder, SA key, and Azure OpenAI settings are all present — so provisioning
before the operator finishes setup is safe.

### One-time operator setup (out-of-band — the Dev Agent cannot do these)

1. **Google service account.** In Google Cloud, create a service account, enable
   the **Drive API**, and create a JSON key. Share the target Drive folder with
   the SA's email (Viewer is enough). Store the JSON key in Key Vault:
   ```bash
   az keyvault secret set --vault-name kv-condomanager-dev \
       --name google-drive-sa-key --file ./sa-key.json
   ```
2. **Azure OpenAI.** Once an Azure OpenAI resource + a `text-embedding-3-small`
   deployment exist, set `azureOpenAiEndpoint` at deploy time and seed
   `azure-openai-key` (CM-18 secret).
3. **Cosmos data-plane role.** The MI needs the **Cosmos DB Built-in Data
   Contributor** SQL role to read/write via `DefaultAzureCredential` (control-
   plane Contributor is not enough — same dependency as CM-28's checkpointer):
   ```bash
   MI_PRINCIPAL=$(az identity show -g rg-condomanager -n id-condomanager-dev --query principalId -o tsv)
   az cosmosdb sql role assignment create -g rg-condomanager \
       --account-name cosmos-condomanager-dev \
       --role-definition-id 00000000-0000-0000-0000-000000000002 \
       --principal-id "$MI_PRINCIPAL" --scope "/"
   ```
4. **Set the watched folder:** redeploy `main.bicep` with `gdriveFolderId=<id>`.

### Deploying the function code

Bicep provisions the Function App; the **code** is published out-of-band (a CI
zip-deploy is a planned follow-up). The deploy package bundles the repo's
`agents/` package next to the function:

```bash
# From the repo root, stage agents/ alongside the function and publish.
cp -r agents functions/gdrive-sync/agents
cd functions/gdrive-sync
func azure functionapp publish func-condomanager-dev --python
```

### Post-deploy smoke test

`infra/scripts/gdrive-sync-smoke-test.py` exercises the real Cosmos store with
an in-process fake Drive + stub embedder — validating the Cosmos wiring and the
idempotency property without needing a real Google service account:

```bash
pip install "azure-cosmos>=4.7" "azure-identity>=1.15" "pydantic>=2.7" "langchain-text-splitters>=0.3"
COSMOS_ENDPOINT=$(az cosmosdb show -g rg-condomanager -n cosmos-condomanager-dev --query documentEndpoint -o tsv)
export COSMOS_ENDPOINT
python infra/scripts/gdrive-sync-smoke-test.py
```

A bootstrap run indexes one doc; an immediate re-run is asserted to be a clean
idempotent no-op. Exit 0 == healthy.

## Analytics digest job (CM-36)

A second Azure Functions Timer app — **`func-condomanager-analytics-<env>`**
(`infra/bicep/modules/analytics.bicep`, code in `functions/analytics-digest/`,
logic in `agents/analytics/`) — runs **weekly** (Mondays 08:00 UTC). It reads
the `tickets` (CM-31) and `escalations` (CM-32) containers, computes recurring
issues / contractor scores / sentiment trend / predictive flags, writes a
`WeeklyDigest` to the new **`digests`** container (partition `/tenantId`,
90-day TTL — a rolling, rebuildable view for the CM-37 portal), and posts the
digest to the manager Slack channel via the `slack-webhook-url` KV reference
(no new secret). Same MI + KV-reference + out-of-band `func publish` model as
the gdrive-sync app. Config: `COSMOS_ENDPOINT`, `SLACK_WEBHOOK_URL` (KV),
`ANALYTICS_TENANT_ID` (default `default`), `ENVIRONMENT`.

> Data-availability notes: contractor performance is scored over the ticket
> `owner` (no Vendor entity / `resolved_at` yet — CM-35 + a schema add upgrade
> it); sentiment is the escalation-volume proxy (tickets don't persist tone);
> email delivery is a follow-up (Slack + the `digests` container today).

## Tenant status portal — Azure Static Web Apps (CM-37)

A read-only portal where a tenant looks up their maintenance ticket by its
confirmation code (`TKT-XXXXXXXX`) and sees a status timeline + ETA + assigned
vendor. First portal story; lives in `portal/`.

| Aspect          | Value                                                          |
|-----------------|----------------------------------------------------------------|
| Resource        | `swa-condomanager-<env>` — `Microsoft.Web/staticSites`, Free SKU |
| Frontend        | Vite + vanilla TypeScript SPA (`portal/src`) → `portal/dist`    |
| API             | SWA managed Functions, TypeScript (`portal/api`), `GET /api/ticket?code=` |
| Data            | reads the CM-17 `tickets` container by `id` (cross-partition)   |
| Auth            | none (code-only lookup); API returns a **non-PII** projection   |
| Deploy          | SWA deployment token via `Azure/static-web-apps-deploy` (CI)    |

### Architecture notes

- **One TypeScript stack.** Frontend + the managed-Functions API are both
  TS/Node (the first-class SWA Free runtime), tested with vitest. Pure logic
  (`portal/src/ticket.ts`, `portal/api/src/shape.ts`) is unit-tested; the
  DOM glue + Cosmos query are thin.
- **Non-PII projection.** The lookup is unauthenticated, so
  `toPublicTicket` whitelists only `id`, `status`, `eta`, `owner` (vendor),
  `created_at`, `updated_at` — never `issue_text` / `unit` / `tenant_id`.
- **Cosmos via connection string.** SWA Free managed Functions don't reliably
  support Managed Identity (a Standard-tier feature), so the API reads a
  `COSMOS_CONNECTION_STRING` **app setting** seeded from KV
  `cosmos-connection-string`. The string lives only as an Azure app setting —
  never in code/IaC. Upgrade to MI when the SWA moves to Standard.

### One-time operator setup (out-of-band)

1. **Deploy the infra** (`main.bicep`) — provisions `swa-condomanager-<env>`.
2. **Set the Cosmos app setting** on the SWA (from KV `cosmos-connection-string`):
   ```bash
   az staticwebapp appsettings set --name swa-condomanager-dev \
       --setting-names COSMOS_CONNECTION_STRING="<from kv>"
   ```
3. **Enable CI deploy:** set repo variable `PORTAL_DEPLOY_ENABLED=true`. Until
   then the `deploy-portal-dev` job is skipped (keeps `main` green pre-setup).

No deployment-token secret is stored: `deploy-portal-dev` logs in via OIDC
(the CM-15 federated credentials) and fetches the SWA token just-in-time with
`az staticwebapp secrets list`, preserving the repo's OIDC-only posture.

### Local development

```bash
cd portal && npm ci && npm run dev          # SPA on :5173 (proxy /api in prod)
cd portal/api && npm ci && npm start        # func host for the API
```

`npm run lint && npm test && npm run build` runs in CI's `portal` area for both
the frontend and the API.

## Adding per-env resources in later stories

Each new resource type gets its own module under `infra/bicep/modules/`,
following the CM-16 / CM-17 / CM-18 / CM-20 pattern: accept `env`,
`location`, and `tags` params, emit any resource IDs downstream modules
need as outputs, and let `main.bicep` chain them in dependency order.
