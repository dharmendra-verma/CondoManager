// main.bicep — entry point for resources deployed INTO rg-condomanager.
// Jira: CM-15  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// The shared resource group (rg-condomanager) is pre-created as a one-time
// bootstrap (see infra/scripts/setup-azure-oidc.sh + docs/INFRA.md) so that
// the CI service principal holds Contributor on the RG only — never on the
// subscription. That keeps blast-radius scoped to a single RG.
//
// Downstream stories (CM-16 Cosmos DB, CM-17 Key Vault, CM-18 ACR …) add
// resource modules below. Tags on each downstream resource come from
// tags.bicep so the schema stays consistent.
//
// Deployment:
//   az deployment group create \
//     --resource-group rg-condomanager \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/main.parameters.json

targetScope = 'resourceGroup'

// Smoke-test outputs — confirm the deployment authenticated and targeted
// the expected RG. Until downstream modules land, this template is a no-op
// that simply round-trips the RG metadata so CI can verify OIDC + scope.
output resourceGroupId string = resourceGroup().id
output resourceGroupName string = resourceGroup().name
output resourceGroupLocation string = resourceGroup().location
output resourceGroupTags object = resourceGroup().tags
