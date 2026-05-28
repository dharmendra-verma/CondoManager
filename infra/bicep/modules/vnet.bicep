// vnet.bicep — Virtual Network for Container Apps VNet integration.
// Jira: CM-16  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// Provides a single /23 subnet delegated to Microsoft.App/environments for the
// Container Apps Managed Environment. /23 is the Azure minimum for a Container
// Apps infrastructure subnet on the Consumption plan. The /16 address space
// leaves room for future subnets (Cosmos private endpoint, Key Vault PE, etc.).
//
// No NSG is attached: Container Apps on Consumption manages its own networking
// rules. If/when a workload-profile environment is introduced, an NSG with the
// platform-required rules should be added here.

targetScope = 'resourceGroup'

@description('Environment short name (dev or prod). Used in resource names and tags.')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep so all resources stay co-located.')
param location string

@description('Tag map produced by tags.bicep — applied verbatim to every resource here.')
param tags object

@description('VNet IPv4 address space. /16 is intentional to leave room for many subnets.')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Container Apps infrastructure subnet prefix. Must be at least /23.')
param containerAppsSubnetPrefix string = '10.0.0.0/23'

var vnetName = 'vnet-condomanager-${env}'
var subnetName = 'snet-containerapps-${env}'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: subnetName
        properties: {
          addressPrefix: containerAppsSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output containerAppsSubnetId string = '${vnet.id}/subnets/${subnetName}'
output containerAppsSubnetName string = subnetName
