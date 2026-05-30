// Cosmos read access for the portal API (CM-37).
//
// SWA Free managed Functions don't reliably support Managed Identity (that's a
// Standard-tier feature), so we read a `COSMOS_CONNECTION_STRING` application
// setting (operator-seeded from KV `cosmos-connection-string`). The string
// lives only as an Azure app setting — never in code or IaC. Upgrade to MI +
// DefaultAzureCredential when the SWA moves to Standard.

import { type Container, CosmosClient } from "@azure/cosmos";

const DATABASE = "condomanager";
const CONTAINER = "tickets";
const PLACEHOLDER = "REPLACE-ME";

let container: Container | null = null;

function getContainer(): Container {
  if (container !== null) {
    return container;
  }
  const conn = (process.env.COSMOS_CONNECTION_STRING ?? "").trim();
  if (conn === "" || conn === PLACEHOLDER) {
    throw new Error("COSMOS_CONNECTION_STRING is not configured");
  }
  const client = new CosmosClient(conn);
  container = client.database(DATABASE).container(CONTAINER);
  return container;
}

/**
 * Look a ticket up by its confirmation code (the doc `id`). Cross-partition
 * (tenants don't know their `tenantId`); cheap at MVP volume. Returns the raw
 * doc or `null` — the caller projects it through `toPublicTicket`.
 */
export async function lookupTicketByCode(
  code: string,
): Promise<Record<string, unknown> | null> {
  const { resources } = await getContainer()
    .items.query<Record<string, unknown>>({
      query: "SELECT * FROM c WHERE c.id = @code",
      parameters: [{ name: "@code", value: code }],
    })
    .fetchAll();
  return resources.length > 0 ? resources[0] : null;
}
