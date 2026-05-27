// main.bicep — provisions THE single Azure resource group for CondoManager.
// Jira: CM-15  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// One RG (rg-condomanager) hosts BOTH dev and prod workloads.
// The dev/prod distinction is applied per-resource in later stories
// (e.g. cosmos-condomanager-dev vs cosmos-condomanager-prod), via the
// `env` parameter on tags.bicep when that resource is created.
//
// Deployment is subscription-scoped because Resource Groups themselves
// live at the subscription level. Run with:
//   az deployment sub create \
//     --location eastus2 \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/main.parameters.json

targetScope = 'subscription'

@description('Azure region for the resource group. All resources in the RG inherit this region by default.')
param location string = 'eastus2'

@description('Team / individual responsible for the resources.')
param owner string = 'platform-team'

@description('Cost-center identifier charged for this RG (shared by dev + prod workloads).')
param costCenter string

@description('Optional override for the RG name. Default follows the project naming convention.')
param resourceGroupName string = 'rg-condomanager'

// Centralised tag schema for the RG itself.
// env='shared' because the RG hosts BOTH dev and prod resources; individual
// resources inside the RG carry env='dev' or env='prod' via tags.bicep.
var tags = {
  env: 'shared'
  owner: owner
  'cost-center': costCenter
  project: 'condo-manager'
  'managed-by': 'bicep'
}

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

output resourceGroupId string = rg.id
output resourceGroupName_output string = rg.name
output appliedTags object = tags
