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
// Throughput layout (free-tier 1000 RU/s ceiling):
//   * database (shared) ........... 600 RU/s — tenants, tickets, conversations
//   * policies-vector (dedicated) . 400 RU/s — Cosmos rejects vector indexing
//                                              on shared-throughput containers
// Total = 1000 RU/s, fully covered by the free tier.

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

@description('Shared database throughput in RU/s, used by tenants/tickets/conversations. Default 600 leaves 400 RU/s of the free-tier 1000 RU/s ceiling for the dedicated policies-vector container.')
@minValue(400)
@maxValue(1000000)
param databaseThroughput int = 600

@description('Dedicated throughput in RU/s for the policies-vector container. Vector indexing is not supported on shared-throughput containers, so this container must carry its own throughput.')
@minValue(400)
@maxValue(1000000)
param vectorContainerThroughput int = 400

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
            // CM-42: with 'cosine', VectorDistance() returns a *similarity score*
            // (despite the name), in [-1, 1] where 1.0 = identical and 0 =
            // orthogonal — NOT a 1−cos distance. Query with a bare
            // `ORDER BY VectorDistance(...)` (most-similar first); never add DESC.
            // See docs/INFRA.md "VectorDistance semantics".
            distanceFunction: 'cosine'
          }
        ]
      }
    }
    // Cosmos rejects vector indexing on containers that inherit the database's
    // shared throughput — this container must have its own dedicated RU/s.
    options: {
      throughput: vectorContainerThroughput
    }
  }
}

// CM-28: LangGraph spine checkpoint store. Each row is one BaseCheckpointSaver
// checkpoint for a graph thread. Small docs, low fan-out, no vector indexing
// — shared throughput is sufficient. 30-day TTL auto-purges old checkpoints
// to keep storage bounded (longest HITL workflow expected to need history is
// a few days; 30d is generous backstop).
resource checkpointsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'checkpoints'
  properties: {
    resource: {
      id: 'checkpoints'
      partitionKey: {
        // Partition by thread_id so all checkpoints for a single graph run
        // colocate. LangGraph keys writes/reads by (thread_id, checkpoint_id),
        // and read-most-recent is the hot path during HITL resume.
        paths: [ '/thread_id' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [ { path: '/*' } ]
        excludedPaths: [ { path: '/"_etag"/?' } ]
      }
      // 30 days = 2,592,000 seconds. Auto-purge on every doc; container-level
      // default so producers don't have to set it per-write.
      defaultTtl: 2592000
    }
  }
}

// CM-32: Escalation Agent internal records. One doc per escalation
// (category, legal_risk, manager alert, held tenant draft, HITL status).
// Partitioned by /tenantId like tickets/conversations; small docs on shared
// throughput. NO defaultTtl — escalation records are an audit/compliance
// surface (legal-flagged cases especially) and must not auto-purge.
resource escalationsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'escalations'
  properties: {
    resource: {
      id: 'escalations'
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

// CM-34: knowledge-sync cursor store for the Google Drive → Cosmos sync job.
// One small doc per sync source (id == source), holding the Drive Changes-API
// startPageToken + a per-document content-hash map for delta detection. Tiny,
// low-fan-out, no vector indexing — shared database throughput is sufficient.
// Partitioned by /source so each source's cursor is a single point-read.
resource knowledgeSyncContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'knowledge_sync'
  properties: {
    resource: {
      id: 'knowledge_sync'
      partitionKey: {
        paths: [ '/source' ]
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

// CM-36: weekly Analytics digests. One doc per (tenant, week) — recurring
// issues, contractor scores, sentiment trend, predictive flags — read by the
// CM-37 tenant portal. Partitioned by /tenantId; small docs on shared
// throughput. 90-day TTL: digests are a rolling, rebuildable view (re-running
// the job regenerates them), so old weeks auto-purge.
resource digestsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'digests'
  properties: {
    resource: {
      id: 'digests'
      partitionKey: {
        paths: [ '/tenantId' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [ { path: '/*' } ]
        excludedPaths: [ { path: '/"_etag"/?' } ]
      }
      // 90 days = 7,776,000 seconds. Rolling view; old weeks auto-purge.
      defaultTtl: 7776000
    }
  }
}

output accountName string = account.name
output accountId string = account.id
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
