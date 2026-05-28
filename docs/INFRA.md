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
│   ├── main.bicep                 # RG-scope entry point: wires tags + per-env resource modules
│   ├── tags.bicep                 # Reusable tag schema (env: dev|prod|shared)
│   ├── main.parameters.json       # Single parameters file (`env` selects the deploy target)
│   └── modules/
│       └── cosmos.bicep           # Cosmos DB account + db + 4 containers (CM-17)
└── scripts/
    └── cosmos-smoke-test.py       # Post-deploy validation for Cosmos vector search (CM-17)
.github/
└── workflows/
    └── infra-deploy.yml           # CI: lint → what-if → deploy (single RG)
tests/
└── infra/
    └── test_bicep_lint.sh         # Lint test runs in CI on every PR
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

## Deploying manually (smoke test before CI works)

`main.bicep` is resource-group scoped and requires the `env` parameter
(controls naming of per-env resources like `cosmos-condomanager-<env>`).
The shared RG itself is bootstrapped out-of-band (see CM-15 / the OIDC
setup script) — `az deployment group create` deploys INTO it.

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

## Adding per-env resources in later stories

Inside any resource module that deploys *into* `rg-condomanager`, import
`tags.bicep` with `env: 'dev'` or `env: 'prod'`:

```bicep
module devTags 'tags.bicep' = {
  name: 'tags-dev'
  scope: resourceGroup('rg-condomanager')
  params: { env: 'dev', costCenter: 'cc-condomanager' }
}

resource cosmosDev 'Microsoft.DocumentDB/databaseAccounts@2024-08-15' = {
  name: 'cosmos-condomanager-dev'
  location: 'eastus2'
  tags: devTags.outputs.tags
  // ...
}
```
