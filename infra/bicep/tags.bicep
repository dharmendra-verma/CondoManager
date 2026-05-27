// tags.bicep — single source of truth for resource tagging across CondoManager.
// Imported by resources that deploy INTO the shared resource group
// (Cosmos DB, Key Vault, ACR, Container Apps env, etc.), so each resource
// can carry its own per-env tag while the schema stays consistent.
//
// Required tags (per CM-15 AC): env, owner, cost-center
// Additional tags for ops hygiene: project, managed-by

targetScope = 'resourceGroup'

@description('Environment short name. Use dev/prod for per-env resources; shared for resources that span both (e.g. the RG itself, ACR).')
@allowed([ 'dev', 'prod', 'shared' ])
param env string

@description('Team or individual that owns the resource.')
param owner string = 'platform-team'

@description('Cost-center code charged for this resource.')
param costCenter string

@description('Optional extra tags to merge in (e.g. release version).')
param extraTags object = {}

var baseTags = {
  env: env
  owner: owner
  'cost-center': costCenter
  project: 'condo-manager'
  'managed-by': 'bicep'
}

output tags object = union(baseTags, extraTags)
