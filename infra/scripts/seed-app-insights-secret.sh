#!/usr/bin/env bash
# seed-app-insights-secret.sh — populate kv-condomanager-<env> with the
# App Insights connection string from the latest deployment outputs.
#
# Jira: CM-22  | Epic: Observability  | Phase 0
#
# The Bicep deployment surfaces the App Insights ConnectionString as a
# @secure output named `appInsightsConnectionString`. This script reads
# that output and writes it into Key Vault under the secret name
# `app-insights-connection-string` (declared in keyvault.bicep's
# secretNames default + seed-keyvault-secrets.sh).
#
# Once the secret is set, Container Apps' native KV `secretRef`
# resolution picks it up on the next revision and exposes it to the
# container as APPLICATIONINSIGHTS_CONNECTION_STRING — at which point
# the Python `configure_otel` flips into Azure Monitor mode.
#
# Idempotent: refuses to overwrite a value that is neither REPLACE-ME
# nor identical to the latest deployment output (mirrors the CM-18
# seed-keyvault-secrets.sh posture). Re-running is therefore safe.
#
# Usage:
#   bash infra/scripts/seed-app-insights-secret.sh <env> [deployment-name]
#   bash infra/scripts/seed-app-insights-secret.sh dev
#
# Prereqs:
#   * az CLI logged in (`az login`)
#   * Caller has data-plane Set permission on the vault (Key Vault Secrets
#     Officer; the OIDC SP intentionally does NOT have this — operator step).

set -euo pipefail

ENV_NAME="${1:-}"
if [ -z "$ENV_NAME" ]; then
  echo "usage: $0 <env: dev|prod> [deployment-name]" >&2
  exit 1
fi
case "$ENV_NAME" in
  dev|prod) ;;
  *) echo "env must be 'dev' or 'prod' (got '$ENV_NAME')" >&2; exit 1 ;;
esac

VAULT_NAME="kv-condomanager-${ENV_NAME}"
SECRET_NAME="app-insights-connection-string"
PLACEHOLDER="REPLACE-ME"
RESOURCE_GROUP="rg-condomanager"

if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found on PATH." >&2
  exit 1
fi

# Resolve the deployment name. Either explicit arg, or "latest by start time".
DEPLOY_NAME="${2:-}"
if [ -z "$DEPLOY_NAME" ]; then
  DEPLOY_NAME=$(az deployment group list \
    --resource-group "$RESOURCE_GROUP" \
    --query 'sort_by([?properties.provisioningState==`Succeeded`], &properties.timestamp)[-1].name' \
    -o tsv 2>/dev/null || true)
fi
if [ -z "$DEPLOY_NAME" ]; then
  echo "Could not determine latest deployment name on $RESOURCE_GROUP." >&2
  echo "Pass an explicit deployment name as the second argument." >&2
  exit 1
fi
echo "Reading App Insights conn string from deployment '$DEPLOY_NAME' in '$RESOURCE_GROUP'"

CONN=$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DEPLOY_NAME" \
  --query 'properties.outputs.appInsightsConnectionString.value' -o tsv 2>/dev/null || true)
if [ -z "$CONN" ]; then
  echo "FAIL: no 'appInsightsConnectionString' output on deployment '$DEPLOY_NAME'." >&2
  echo "      Make sure main.bicep was deployed with the CM-22 changes." >&2
  exit 1
fi

# Idempotency: skip if the vault already holds the same conn string (so re-runs
# don't accumulate noop secret versions in KV history). Skip with a notice if
# someone has rotated the value to something neither REPLACE-ME nor the current
# deployment output (defensive — never trample an operator's manual rotation).
EXISTING=$(az keyvault secret show \
  --vault-name "$VAULT_NAME" --name "$SECRET_NAME" \
  --query value -o tsv 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
  if [ "$EXISTING" = "$CONN" ]; then
    echo "  . $SECRET_NAME — already matches latest deployment output, skipping"
    exit 0
  elif [ "$EXISTING" != "$PLACEHOLDER" ]; then
    echo "  . $SECRET_NAME — non-placeholder value differs from deployment output:" >&2
    echo "    refusing to overwrite. Inspect with:" >&2
    echo "      az keyvault secret show --vault-name $VAULT_NAME --name $SECRET_NAME" >&2
    exit 1
  fi
fi

az keyvault secret set \
  --vault-name "$VAULT_NAME" --name "$SECRET_NAME" \
  --value "$CONN" --output none
echo "  + $SECRET_NAME populated from $DEPLOY_NAME (length=${#CONN})"
echo ""
echo "Next: the Container App's next revision will pick this up via secretRef."
echo "  az containerapp update --name ca-hello-condomanager-$ENV_NAME --resource-group $RESOURCE_GROUP"
