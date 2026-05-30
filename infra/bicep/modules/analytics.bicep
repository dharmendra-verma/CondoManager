// analytics.bicep — Azure Functions app (Linux Consumption) for the weekly
// Analytics & Forecasting digest job.
// Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3
//
// Provisions a Y1 (Consumption) Linux Python 3.12 Function App + its own
// storage account, on a dedicated app (separate from the CM-34 gdrive-sync
// app) so the weekly schedule + failures are isolated. The function code
// (functions/analytics-digest/) is deployed out-of-band via
// `func azure functionapp publish` — see docs/INFRA.md.
//
// Auth: the shared CM-18 User-Assigned MI is attached so the app reads the
// Cosmos tickets/escalations containers (DefaultAzureCredential) and resolves
// the slack-webhook-url Key Vault reference. No secret material in app settings.
//
// Cost: Y1 Consumption with scale-to-zero — a once-weekly timer is a handful
// of invocations/month, comfortably inside the free grant.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (func-condomanager-analytics-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the shared User-Assigned MI (CM-18). Attached for Cosmos data-plane access + Key Vault reference resolution.')
param userAssignedIdentityId string

@description('Application Insights connection string (CM-22). @secure() keeps the embedded InstrumentationKey out of deployment-history plaintext.')
@secure()
param appInsightsConnectionString string

@description('Cosmos DB account endpoint (CM-17). The digest job reads tickets + escalations via DefaultAzureCredential.')
param cosmosEndpoint string

@description('Key Vault URI (CM-18), ending in "/". Used to build the slack-webhook-url Key Vault reference.')
param keyVaultUri string

@description('Tenant id whose weekly digest is built. Empty/default until multi-tenant iteration lands.')
param analyticsTenantId string = 'default'

// Storage account names: 3–24 chars, lowercase alphanumeric only, globally
// unique. Distinct from the gdrive-sync app's storage (…fn) via the …an suffix.
var storageAccountName = 'stcondomanager${env}an'
var functionAppName = 'func-condomanager-analytics-${env}'
var planName = 'plan-condomanager-${env}-an'

var slackWebhookRef = '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/slack-webhook-url)'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: plan.id
    reserved: true
    httpsOnly: true
    keyVaultReferenceIdentity: userAssignedIdentityId
    siteConfig: {
      linuxFxVersion: 'Python|3.12'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'ENVIRONMENT'
          value: env
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmosEndpoint
        }
        {
          name: 'ANALYTICS_TENANT_ID'
          value: analyticsTenantId
        }
        {
          name: 'SLACK_WEBHOOK_URL'
          value: slackWebhookRef
        }
      ]
    }
  }
}

output functionAppName string = functionApp.name
output functionAppId string = functionApp.id
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output storageAccountName string = storage.name
