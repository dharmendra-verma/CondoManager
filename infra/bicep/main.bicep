// main.bicep — entry point for resources deployed INTO rg-condomanager.
// Jira: CM-15 (RG + scoped SP)  | CM-16 (Container Apps env)  | CM-17 (Cosmos DB)
// Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// The shared resource group (rg-condomanager) is pre-created as a one-time
// bootstrap (see infra/scripts/setup-azure-oidc.sh + docs/INFRA.md) so that
// the CI service principal holds Contributor on the RG only — never on the
// subscription. That keeps blast-radius scoped to a single RG.
//
// Per-env resources live in this single RG and are named
// `<resource>-condomanager-<env>` (e.g. cosmos-condomanager-dev,
// cae-condomanager-dev). Tags on each downstream resource come from
// tags.bicep so the schema stays consistent.
//
// Deployment (dev):
//   az deployment group create \
//     --resource-group rg-condomanager \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/main.parameters.json \
//     --parameters env=dev

targetScope = 'resourceGroup'

@description('Deployment environment. Drives per-resource naming (cosmos-condomanager-<env>, cae-condomanager-<env>, …).')
@allowed([ 'dev', 'prod' ])
param env string = 'dev'

@description('Azure region. Defaults to the resource group location so dev and prod stay co-located.')
param location string = resourceGroup().location

@description('Enable the Cosmos DB free tier. Only ONE free-tier account is allowed per subscription — set to false for prod if the subscription already has one.')
param cosmosEnableFreeTier bool = true

@description('Embedding vector dimensions for the policies-vector container. 1536 matches OpenAI text-embedding-ada-002 / text-embedding-3-small.')
@minValue(2)
@maxValue(4096)
param cosmosVectorDimensions int = 1536

// Tag schema — every downstream resource carries the same five tags.
module tagsModule './tags.bicep' = {
  name: 'tags-${env}'
  params: {
    env: env
    costCenter: 'cc-condomanager'
  }
}

// VNet + delegated /23 subnet for Container Apps. (CM-16)
module vnet './modules/vnet.bicep' = {
  name: 'vnet-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Log Analytics workspace — required by Container Apps appLogsConfiguration. (CM-16)
module logAnalytics './modules/log-analytics.bicep' = {
  name: 'law-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Container Apps Managed Environment (Consumption, VNet-integrated). (CM-16)
module containerAppsEnv './modules/container-apps-env.bicep' = {
  name: 'cae-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    infrastructureSubnetId: vnet.outputs.containerAppsSubnetId
    logAnalyticsCustomerId: logAnalytics.outputs.workspaceCustomerId
    logAnalyticsSharedKey: logAnalytics.outputs.workspaceSharedKey
  }
}

// Hello-world Container App — smoke-test surface for CM-16 AC.
module containerApp './modules/container-app.bicep' = {
  name: 'ca-hello-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    environmentId: containerAppsEnv.outputs.environmentId
  }
}

// Cosmos DB account + condomanager database + 4 containers (tenants,
// tickets, conversations, policies-vector with DiskANN vector search). (CM-17)
module cosmos './modules/cosmos.bicep' = {
  name: 'cosmos-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    enableFreeTier: cosmosEnableFreeTier
    vectorDimensions: cosmosVectorDimensions
  }
}

// Resource group identity outputs (kept from CM-15 for OIDC + scope smoke test).
output resourceGroupId string = resourceGroup().id
output resourceGroupName string = resourceGroup().name
output resourceGroupLocation string = resourceGroup().location
output resourceGroupTags object = resourceGroup().tags

// CM-16 outputs — used by the smoke test to curl the hello-world app.
output vnetName string = vnet.outputs.vnetName
output logAnalyticsWorkspaceName string = logAnalytics.outputs.workspaceName
output containerAppsEnvironmentName string = containerAppsEnv.outputs.environmentName
output containerAppsEnvironmentDefaultDomain string = containerAppsEnv.outputs.defaultDomain
output containerAppName string = containerApp.outputs.containerAppName
output containerAppFqdn string = containerApp.outputs.fqdn

// CM-17 outputs — surface Cosmos endpoint for the post-deploy smoke-test
// (infra/scripts/cosmos-smoke-test.py).
output cosmosAccountName string = cosmos.outputs.accountName
output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabaseName string = cosmos.outputs.databaseName
