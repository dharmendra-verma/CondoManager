#!/usr/bin/env bash
# seed-swa-cosmos-setting.sh — push the real Cosmos connection string from Key
# Vault into the Static Web App's COSMOS_CONNECTION_STRING app setting.
#
# Jira: CM-64  | Epic: Tenant/Manager portal  | Phase 2  | Follow-up to CM-61
#
# WHY a script and not a Key Vault reference:
#   SWA *Free* managed Functions don't support Managed Identity, so a
#   `@Microsoft.KeyVault(...)` app-setting reference is unavailable (see
#   infra/bicep/modules/static-web-app.bicep and portal/api/src/cosmos.ts).
#   The connection string must therefore be pushed as a PLAIN app setting,
#   seeded out-of-band from KV — mirroring seed-keyvault-secrets.sh (CM-18)
#   and seed-app-insights-secret.sh (CM-22/CM-57). No secret value ever passes
#   through IaC or git.
#
# WHY it must be repeatable:
#   CM-61 did this once by hand. A fresh SWA deploy / recreate, or anyone
#   clearing app settings, drops COSMOS_CONNECTION_STRING — at which point the
#   portal API (portal/api/src/cosmos.ts, tenantRepo.ts) treats it as
#   unconfigured and silently falls back to a per-instance in-memory store, so
#   tenant writes + /api/ticket?code= lookups quietly break. Re-run this script
#   after any such event.
#
# Fail-closed: refuses to push when the KV secret is still the REPLACE-ME
# placeholder or empty, so the placeholder can never be promoted into the live
# app setting (exactly what would re-trigger the silent in-memory fallback).
#
# Idempotent: if the SWA app setting already equals the KV value, this is a
# no-op (no new app-setting revision). Safe to re-run on every bring-up /
# rotation.
#
# Usage:
#   bash infra/scripts/seed-swa-cosmos-setting.sh <env: dev|prod>
#   bash infra/scripts/seed-swa-cosmos-setting.sh prod
#
# Prereqs:
#   * az CLI logged in (`az login`)
#   * Caller can READ the KV secret (Key Vault Secrets User/Officer) AND write
#     SWA app settings (e.g. Contributor on the Static Web App). The OIDC CI SP
#     intentionally has neither — this is an operator-run step, not a CI step.

set -euo pipefail

ENV_NAME="${1:-}"
if [ -z "$ENV_NAME" ]; then
  echo "usage: $0 <env: dev|prod>" >&2
  exit 1
fi
case "$ENV_NAME" in
  dev|prod) ;;
  *) echo "env must be 'dev' or 'prod' (got '$ENV_NAME')" >&2; exit 1 ;;
esac

VAULT_NAME="kv-condomanager-${ENV_NAME}"
SWA_NAME="swa-condomanager-${ENV_NAME}"
RESOURCE_GROUP="rg-condomanager"
SECRET_NAME="cosmos-connection-string"
SETTING_NAME="COSMOS_CONNECTION_STRING"
PLACEHOLDER="REPLACE-ME"

if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found on PATH." >&2
  exit 1
fi

# Trim leading/trailing whitespace (Cosmos connection strings contain no
# internal spaces, so whitespace can only be accidental padding).
trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# 1. Read the real connection string from Key Vault.
echo "Reading $SECRET_NAME from $VAULT_NAME"
CONN=$(az keyvault secret show \
  --vault-name "$VAULT_NAME" --name "$SECRET_NAME" \
  --query value -o tsv 2>/dev/null || true)
CONN="$(trim "$CONN")"

# 2. Fail-closed: never promote the placeholder (or an empty/missing secret)
#    into the live app setting.
if [ -z "$CONN" ] || [ "$CONN" = "$PLACEHOLDER" ]; then
  echo "FAIL: $VAULT_NAME/$SECRET_NAME is still the placeholder ('$PLACEHOLDER') or empty/missing." >&2
  echo "      Seed the real Cosmos connection string first, then re-run this script:" >&2
  echo "        CONN=\$(az cosmosdb keys list --type connection-strings \\" >&2
  echo "                 --name cosmos-condomanager-$ENV_NAME --resource-group $RESOURCE_GROUP \\" >&2
  echo "                 --query 'connectionStrings[0].connectionString' -o tsv)" >&2
  echo "        az keyvault secret set --vault-name $VAULT_NAME --name $SECRET_NAME --value \"\$CONN\"" >&2
  exit 1
fi

# 3. Idempotency: skip if the SWA app setting already matches (no churn).
CURRENT=$(az staticwebapp appsettings list \
  --name "$SWA_NAME" --resource-group "$RESOURCE_GROUP" \
  --query "properties.$SETTING_NAME" -o tsv 2>/dev/null || true)
CURRENT="$(trim "$CURRENT")"
if [ -n "$CURRENT" ] && [ "$CURRENT" = "$CONN" ]; then
  echo "  . $SETTING_NAME on $SWA_NAME already matches KV — skipping"
  exit 0
fi

# 4. Push the value. `--setting-names KEY=VALUE` upserts just this one key,
#    leaving TENANT_ADMIN_ENABLED and any other settings intact. az splits on
#    the first '=', so the connection string's own '='/';' are preserved.
az staticwebapp appsettings set \
  --name "$SWA_NAME" --resource-group "$RESOURCE_GROUP" \
  --setting-names "$SETTING_NAME=$CONN" --output none
echo "  + $SETTING_NAME set on $SWA_NAME (length=${#CONN})"
echo ""
echo "Done. The SWA managed Functions API now persists to Cosmos."
echo "NOTE: recreating/redeploying the Static Web App resets its app settings —"
echo "      re-run this script if persistence reverts to the in-memory fallback."
