// analytics-functions.bicep — Azure Functions app (Linux Consumption) for the
// weekly analytics digest job.
// Jira: CM-36  | Epic: CM-Epic 11 (Analytics)  | Phase 1
//
// Provisions a Y1 (Consumption) Linux Python 3.12 Function App + its own
// storage account. The function code (functions/analytics-digest/) is deployed
// out-of-band via `func azure functionapp publish` — see docs/ANALYTICS.md.
// This module only provisions the resource + wires its configuration.
//
// Auth: the shared CM-18 User-Assigned MI is attached so the app reads the
// Cosmos `tickets` container via DefaultAzureCredential. No secret material in
// app settings — the digest job needs no API keys today (logging delivery).
//
// Cost: Y1 Consumption is pay-per-execution with scale-to-zero — a once-weekly
// timer is a handful of invocations/month, trivially inside the free grant.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (func-condomanager-<env>-analytics).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the shared User-Assigned MI (CM-18). Attached to the Function App for Cosmos data-plane access.')
param userAssignedIdentityId string

@description('Application Insights connection string (CM-22). @secure() keeps the embedded InstrumentationKey out of deployment-history plaintext.')
@secure()
param appInsightsConnectionString string

@description('Cosmos DB account endpoint (CM-17). The digest job reads the tickets container via DefaultAzureCredential.')
param cosmosEndpoint string

@description('Comma-separated board digest recipients. Empty until an operator supplies it; the logging delivery default ignores it.')
param digestRecipients string = ''

// Storage account names: 3-24 chars, lowercase alphanumeric only, globally
// unique. Deterministic so docs/tooling can find it. "an" = analytics.
var storageAccountName = 'stcondomanager${env}an'
var functionAppName = 'func-condomanager-${env}-analytics'
var planName = 'plan-condomanager-${env}-an'

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
          name: 'DIGEST_RECIPIENTS'
          value: digestRecipients
        }
      ]
    }
  }
}

output functionAppName string = functionApp.name
output functionAppId string = functionApp.id
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output storageAccountName string = storage.name
