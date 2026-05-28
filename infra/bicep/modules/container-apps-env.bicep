// container-apps-env.bicep — Container Apps Managed Environment (Consumption).
// Jira: CM-16  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// Consumption is the only workload profile that participates in the
// 180K vCPU-sec / 400K GiB-sec free grant. Workload-profile-based environments
// (Dedicated D4, E4, etc.) bill from the first second and are NOT used here.
//
// VNet integration is enabled by passing an infrastructure subnet that has
// been delegated to Microsoft.App/environments (see vnet.bicep). `internal:
// false` keeps the ingress LB public so the hello-world app is reachable for
// smoke testing. A later story will flip this to true once Front Door / API
// gateway is in place.

targetScope = 'resourceGroup'

@description('Environment short name (dev or prod). Used in resource names and tags.')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the VNet subnet delegated to Microsoft.App/environments.')
param infrastructureSubnetId string

@description('Log Analytics workspace customerId for appLogsConfiguration.')
param logAnalyticsCustomerId string

@description('Log Analytics workspace primary shared key. Marked @secure so it never appears in deployment outputs.')
@secure()
param logAnalyticsSharedKey string

@description('When true, the environment ingress is internal-only (private IP). When false, the ingress is publicly reachable.')
param internal bool = false

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
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      internal: internal
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
