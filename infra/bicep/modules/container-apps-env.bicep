// container-apps-env.bicep — Container Apps Managed Environment (Consumption).
// Jira: CM-16  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// Consumption is the only workload profile that participates in the
// 180K vCPU-sec / 400K GiB-sec free grant. Workload-profile-based environments
// (Dedicated D4, E4, etc.) bill from the first second and are NOT used here.
//
// CM-102: this environment deliberately uses DEFAULT networking — there is no
// `vnetConfiguration` block. A VNet-injected environment provisions a Standard
// Load Balancer + Standard public IP in a managed resource group, and both bill
// 24x7 whether or not a single request is served (~Rs. 2,025/month, 55% of the
// entire Azure bill). The original CM-16 injection bought nothing: no private
// endpoints existed anywhere in the subscription, and both Cosmos DB and Key
// Vault ran with `publicNetworkAccess: Enabled` and no network ACLs, so all
// traffic already traversed public endpoints.
//
// Do NOT re-add `vnetConfiguration` for its own sake. It is only worth the cost
// alongside real private endpoints for Cosmos/Key Vault and public network
// access disabled on both — otherwise it is a premium networking bill for zero
// isolation. Note that networking is immutable on an existing environment, so
// re-adding it means recreating the environment and changing the app FQDN again.

targetScope = 'resourceGroup'

@description('Environment short name (dev or prod). Used in resource names and tags.')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Log Analytics workspace customerId for appLogsConfiguration.')
param logAnalyticsCustomerId string

@description('Log Analytics workspace primary shared key. Marked @secure so it never appears in deployment outputs.')
@secure()
param logAnalyticsSharedKey string

var environmentName = 'cae-condomanager-${env}'

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output environmentId string = managedEnvironment.id
output environmentName string = managedEnvironment.name
output defaultDomain string = managedEnvironment.properties.defaultDomain
output staticIp string = managedEnvironment.properties.staticIp
