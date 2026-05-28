// main.bicep — entry point for resources deployed INTO rg-condomanager.
// Jira: CM-16 (Container Apps env)  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// The shared resource group (rg-condomanager) is pre-created as a one-time
// bootstrap (see infra/scripts/setup-azure-oidc.sh + docs/INFRA.md) so that
// the CI service principal holds Contributor on the RG only — never on the
// subscription. That keeps blast-radius scoped to a single RG.
//
// Downstream stories (CM-17 Cosmos DB, CM-18 ACR, CM-19 Key Vault …) will add
// more module references below. Tags on each downstream resource come from
// tags.bicep so the schema stays consistent.
//
// Deployment:
//   az deployment group create \
//     --resource-group rg-condomanager \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/main.parameters.json

targetScope = 'resourceGroup'

@description('Environment short name. dev or prod — drives resource names and tag values.')
@allowed([ 'dev', 'prod' ])
param env string = 'dev'

@description('Azure region. Defaults to the resource group location so dev and prod stay co-located.')
param location string = resourceGroup().location

// Tag schema — every downstream resource carries the same five tags.
module tagsModule './tags.bicep' = {
  name: 'tags-${env}'
  params: {
    env: env
    costCenter: 'cc-condomanager'
  }
}

// VNet + delegated /23 subnet for Container Apps.
module vnet './modules/vnet.bicep' = {
  name: 'vnet-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Log Analytics workspace — required by Container Apps appLogsConfiguration.
module logAnalytics './modules/log-analytics.bicep' = {
  name: 'law-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Container Apps Managed Environment (Consumption, VNet-integrated).
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
