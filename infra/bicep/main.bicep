// main.bicep — entry point for resources deployed INTO rg-condomanager.
// Jira: CM-15 (RG + scoped SP)  | CM-17 (Cosmos DB)
// Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// The shared resource group (rg-condomanager) is pre-created as a one-time
// bootstrap (see infra/scripts/setup-azure-oidc.sh + docs/INFRA.md) so that
// the CI service principal holds Contributor on the RG only — never on the
// subscription. That keeps blast-radius scoped to a single RG.
//
// Per-env resources live in this single RG and are named
// `<resource>-condomanager-<env>` (e.g. cosmos-condomanager-dev). Tags on
// each downstream resource come from tags.bicep so the schema stays
// consistent.
//
// Deployment (dev):
//   az deployment group create \
//     --resource-group rg-condomanager \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/main.parameters.json \
//     --parameters env=dev

targetScope = 'resourceGroup'

@description('Deployment environment. Drives per-resource naming (cosmos-condomanager-<env>, kv-condomanager-<env>, …).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Primary Azure region for downstream resources. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Enable the Cosmos DB free tier. Only ONE free-tier account is allowed per subscription — set to false for prod if the subscription already has one.')
param cosmosEnableFreeTier bool = true

@description('Embedding vector dimensions for the policies-vector container. 1536 matches OpenAI text-embedding-ada-002 / text-embedding-3-small.')
@minValue(2)
@maxValue(4096)
param cosmosVectorDimensions int = 1536

module tags 'tags.bicep' = {
  name: 'tags-${env}'
  params: {
    env: env
    costCenter: 'cc-condomanager'
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos-${env}'
  params: {
    env: env
    location: location
    tags: tags.outputs.tags
    enableFreeTier: cosmosEnableFreeTier
    vectorDimensions: cosmosVectorDimensions
  }
}

// Smoke-test outputs — confirm the deployment authenticated and targeted
// the expected RG, plus surface the Cosmos endpoint for the post-deploy
// smoke-test (infra/scripts/cosmos-smoke-test.py).
output resourceGroupId string = resourceGroup().id
output resourceGroupName string = resourceGroup().name
output resourceGroupLocation string = resourceGroup().location
output resourceGroupTags object = resourceGroup().tags

output cosmosAccountName string = cosmos.outputs.accountName
output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabaseName string = cosmos.outputs.databaseName
