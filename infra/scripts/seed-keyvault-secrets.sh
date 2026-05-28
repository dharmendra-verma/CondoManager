#!/usr/bin/env bash
# seed-keyvault-secrets.sh — populate the eight initial CondoManager secrets
# in `kv-condomanager-<env>` with the literal placeholder `REPLACE-ME`.
#
# Jira: CM-18  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
#
# Real secret values MUST NEVER pass through this script, IaC, or git.
# After seeding, operators replace placeholders out-of-band:
#   az keyvault secret set --vault-name kv-condomanager-dev \
#       --name azure-openai-key --value "<real-value>"
#
# Idempotent: if a secret is already set to a non-placeholder value, this
# script leaves it alone. Safe to re-run on every deploy / rotation.
#
# IMPORTANT — keep the SECRETS list below in sync with the secretNames param
# default in infra/bicep/modules/keyvault.bicep. The Bicep lint test
# (tests/infra/test_bicep_lint.sh) diffs the two and fails on drift.
#
# Usage:
#   bash infra/scripts/seed-keyvault-secrets.sh <vault-name>
#   bash infra/scripts/seed-keyvault-secrets.sh kv-condomanager-dev
#
# Prereqs:
#   * az CLI logged in (`az login`)
#   * The logged-in principal has data-plane Set permission on the vault
#     (e.g. role `Key Vault Secrets Officer`). The OIDC SP used by CI does
#     NOT have this — seeding is an operator-run step, not a CI step.

set -euo pipefail

VAULT_NAME="${1:-}"
if [ -z "$VAULT_NAME" ]; then
  echo "usage: $0 <vault-name>  (e.g. kv-condomanager-dev)" >&2
  exit 1
fi

if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found on PATH. Install: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  exit 1
fi

# Confirm the vault exists and the caller can read its metadata. Catches
# typos and missing logins before we start writing.
if ! az keyvault show --name "$VAULT_NAME" --output none 2>/dev/null; then
  echo "Cannot access vault '$VAULT_NAME'. Check the name and run 'az login'." >&2
  exit 1
fi

SECRETS=(
  azure-openai-key
  twilio-account-sid
  twilio-auth-token
  twilio-whatsapp-number
  langsmith-api-key
  langfuse-public-key
  langfuse-secret-key
  cosmos-connection-string
)

PLACEHOLDER="REPLACE-ME"
SEEDED=0
SKIPPED=0

echo "Seeding ${#SECRETS[@]} secret names into $VAULT_NAME (placeholder=$PLACEHOLDER)"
for s in "${SECRETS[@]}"; do
  # `az keyvault secret show` exits non-zero when the secret doesn't exist;
  # `|| true` lets us treat that as the empty-string case uniformly.
  existing=$(az keyvault secret show --vault-name "$VAULT_NAME" --name "$s" --query value -o tsv 2>/dev/null || true)
  if [ -n "$existing" ] && [ "$existing" != "$PLACEHOLDER" ]; then
    echo "  . $s — non-placeholder value already set, skipping"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  az keyvault secret set --vault-name "$VAULT_NAME" --name "$s" --value "$PLACEHOLDER" --output none
  echo "  + $s = $PLACEHOLDER"
  SEEDED=$((SEEDED + 1))
done

echo ""
echo "Done. seeded=$SEEDED skipped=$SKIPPED"
echo "Replace placeholders with real values via:"
echo "  az keyvault secret set --vault-name $VAULT_NAME --name <secret-name> --value <real-value>"
