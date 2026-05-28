// cosmos.bicep — Cosmos DB account (NoSQL API) + database + four containers.
// Jira: CM-17  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// One Cosmos account per environment, named `cosmos-condomanager-<env>`.
// `policies-vector` carries a DiskANN vector index and embedding policy on
// `/embedding` so RAG embeddings live next to transactional data.
//
// Free tier (25 GB + 1000 RU/s) is enabled by default — only ONE free-tier
// account is allowed per Azure subscription, so prod will need to override
// `enableFreeTier` to false if the subscription already has one.
//
// All four containers share the database-level 1000 RU/s throughput pool
// (the maximum the free tier covers) — keeps cost predictable across the
// foundation phase. Containers can be split out to dedicated throughput
// later without re-creating the database.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (cosmos-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region for the Cosmos account. Free tier + DiskANN vector search are both supported in eastus2.')
param location string

@description('Tags to apply to the Cosmos account (use the output of tags.bicep so the 5-tag schema stays consistent).')
param tags object

@description('Enable the Cosmos DB free tier (25 GB + 1000 RU/s). Only one free-tier account per subscription is allowed — set to false for prod if the subscription already has one.')
param enableFreeTier bool = true

@description('Embedding vector dimensions for the policies-vector container. 1536 matches OpenAI text-embedding-ada-002 and text-embedding-3-small; 3072 matches text-embedding-3-large.')
@minValue(2)
@maxValue(4096)
param vectorDimensions int = 1536

@description('Shared database throughput in RU/s. 1000 is the free-tier ceiling and is shared across all containers in this database.')
@minValue(400)
@maxValue(1000000)
param databaseThroughput int = 1000

var accountName = 'cosmos-condomanager-${env}'
var databaseName = 'condomanager'
var vectorPath = '/embedding'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: enableFreeTier
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    publicNetworkAccess: 'Enabled'
    minimalTlsVersion: 'Tls12'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      {
        // Required to unlock vector-search features (vectorIndexes,
        // vectorEmbeddingPolicy, VectorDistance() in the SQL dialect).
        name: 'EnableNoSQLVectorSearch'
      }
    ]
    backupPolicy: {
      type: 'Periodic'
      periodicModeProperties: {
        backupIntervalInMinutes: 240
        backupRetentionIntervalInHours: 8
        backupStorageRedundancy: 'Local'
      }
    }
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
    options: {
      throughput: databaseThroughput
    }
  }
}

resource tenantsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'tenants'
  properties: {
    resource: {
      id: 'tenants'
      partitionKey: {
        paths: [ '/id' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [ { path: '/*' } ]
        excludedPaths: [ { path: '/"_etag"/?' } ]
      }
    }
  }
}

resource ticketsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'tickets'
  properties: {
    resource: {
      id: 'tickets'
      partitionKey: {
        paths: [ '/tenantId' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [ { path: '/*' } ]
        excludedPaths: [ { path: '/"_etag"/?' } ]
      }
    }
  }
}

resource conversationsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'conversations'
  properties: {
    resource: {
      id: 'conversations'
      partitionKey: {
        paths: [ '/ticketId' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [ { path: '/*' } ]
        excludedPaths: [ { path: '/"_etag"/?' } ]
      }
    }
  }
}

resource policiesVectorContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'policies-vector'
  properties: {
    resource: {
      id: 'policies-vector'
      partitionKey: {
        paths: [ '/tenantId' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [ { path: '/*' } ]
        // Raw embedding floats are excluded from the standard index (it
        // would burn RUs to no purpose) — the diskANN vectorIndex below
        // handles similarity search on /embedding.
        excludedPaths: [
          { path: '${vectorPath}/*' }
          { path: '/"_etag"/?' }
        ]
        vectorIndexes: [
          {
            path: vectorPath
            type: 'diskANN'
          }
        ]
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: vectorPath
            dataType: 'float32'
            dimensions: vectorDimensions
            distanceFunction: 'cosine'
          }
        ]
      }
    }
  }
}

output accountName string = account.name
output accountId string = account.id
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
