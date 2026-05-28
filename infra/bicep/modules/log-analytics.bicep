// log-analytics.bicep — Log Analytics Workspace for Container Apps logging.
// Jira: CM-16  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// The Container Apps Managed Environment requires a Log Analytics workspace as
// its `appLogsConfiguration` destination. PerGB2018 is the modern pay-as-you-go
// SKU and is covered by the 5 GB/month free ingestion grant. 30-day retention
// is the free default; raising it incurs charges.
//
// The customerId + primary shared key are exposed as outputs so the Container
// Apps env module can wire them up. `listKeys()` is a deployment-time function
// — no secret is stored in source.

targetScope = 'resourceGroup'

@description('Environment short name (dev or prod). Used in resource names and tags.')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep — applied verbatim.')
param tags object

@description('Retention in days. 30 is the free-tier default; do not raise without budget approval.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

var workspaceName = 'law-condomanager-${env}'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output workspaceCustomerId string = workspace.properties.customerId
@secure()
output workspaceSharedKey string = workspace.listKeys().primarySharedKey
