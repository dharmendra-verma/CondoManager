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
│       ├── cosmos.bicep                      # Cosmos DB account + db + 4 containers (CM-17)
│       ├── managed-identity.bicep            # User-Assigned MI shared by workloads (CM-18)
│       ├── keyvault.bicep                    # Key Vault (RBAC) + MI role assignment (CM-18)
│       └── acr.bicep                         # Azure Container Registry (Basic SKU) (CM-20)
├── docker/
│   └── base/Dockerfile                       # Curated python:3.12-slim base image (CM-20)
└── scripts/
    ├── cosmos-smoke-test.py                  # Post-deploy validation for Cosmos vector search (CM-17)
    ├── seed-keyvault-secrets.sh              # Seed the 8 initial secret names with REPLACE-ME (CM-18)
    ├── keyvault-smoke-test.py                # Post-deploy validation: MI → KV read (CM-18)
    └── acr-prune.sh                          # Basic-SKU equivalent of ACR retention policy (CM-20)
.github/
└── workflows/
    ├── infra-deploy.yml                      # CI: lint → what-if → deploy (single RG)
    └── base-image.yml                        # Base image build/scan/push (CM-20)
tests/
└── infra/
    └── test_bicep_lint.sh                    # Lint test runs in CI on every PR
```

## How CI works

1. **PR opened touching `infra/`** → `lint` + `what-if` (no apply).
2. **Push to `main` touching `infra/`** → `lint` + `deploy-rg`. The `deploy-rg`
   job is gated by the `prod` GitHub Environment (manual approval) because
   the shared RG is a production resource.
3. All deployments use **OIDC federated credentials** (no long-lived secrets).

When per-env resource stories (Cosmos DB, Key Vault, ACR, Container Apps env)
land, their workflows will add `deploy-dev` and `deploy-prod` jobs gated by
the `dev` and `prod` GitHub Environments respectively.

## One-time setup (manual, by repo owner)

GitHub cannot create the Azure service principal for itself, and these steps
involve credentials that must never cross the chat:

```bash
# 1. Create an Azure AD application + service principal
az ad sp create-for-rbac --name "github-condomanager-infra" --skip-assignment

# 2. Grant the SP Contributor at the subscription scope
az role assignment create \
  --assignee <APP_ID> \
  --role Contributor \
  --scope /subscriptions/<SUBSCRIPTION_ID>

# 3. Configure federated credentials (OIDC) for GitHub — main branch
az ad app federated-credential create --id <APP_ID> --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:dharmendra-verma/CondoManager:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'

# 4. Federated credential for pull requests (what-if job)
az ad app federated-credential create --id <APP_ID> --parameters '{
  "name": "github-pr",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:dharmendra-verma/CondoManager:pull_request",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

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

## Adding per-env resources in later stories

Each new resource type gets its own module under `infra/bicep/modules/`,
following the CM-16 / CM-17 / CM-18 / CM-20 pattern: accept `env`,
`location`, and `tags` params, emit any resource IDs downstream modules
need as outputs, and let `main.bicep` chain them in dependency order.
