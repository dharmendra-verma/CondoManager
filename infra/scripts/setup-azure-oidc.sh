#!/usr/bin/env bash
# setup-azure-oidc.sh — one-time setup for CM-15, extended by CM-43
# Creates: Azure AD app + service principal + 4 federated credentials.
# Grants: Contributor at subscription scope + User Access Administrator at the
#         rg-condomanager RG scope (the latter is needed because Bicep modules
#         under infra/bicep/modules/ include Microsoft.Authorization/roleAssignments
#         resources — see CM-43).
# Outputs: the 3 PUBLIC identifiers you need for GitHub Actions secrets.
#
# Run this in Azure Cloud Shell (https://shell.azure.com) on the same account
# that owns your CondoManager Azure subscription. The "secrets" this script
# produces (AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID) are NOT
# sensitive — they're public identifiers. The actual auth happens via OIDC
# token exchange at GitHub Actions runtime; no client secret is created.
#
# Estimated runtime: 30-60 seconds.
# Requirements: az CLI (pre-installed in Cloud Shell), Owner OR User Access
# Administrator on the target subscription.

set -euo pipefail

# -----------------------------------------------------------------------------
# Config — edit only if your repo is different
# -----------------------------------------------------------------------------
GH_OWNER="dharmendra-verma"
GH_REPO="CondoManager"
SP_NAME="github-condomanager-infra"
ROLE="Contributor"

# -----------------------------------------------------------------------------
# 1. Detect subscription + tenant (no input needed)
# -----------------------------------------------------------------------------
echo "▶  Detecting active subscription..."
SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
SUB_NAME="$(az account show --query name -o tsv)"
echo "   ✓ Subscription: $SUB_NAME ($SUBSCRIPTION_ID)"
echo "   ✓ Tenant:       $TENANT_ID"

# Confirm with user
echo ""
read -r -p "Continue with this subscription? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }

# -----------------------------------------------------------------------------
# 2. Create the Azure AD app + service principal (idempotent)
# -----------------------------------------------------------------------------
echo ""
echo "▶  Creating Azure AD app '$SP_NAME' (idempotent)..."
APP_ID="$(az ad app list --display-name "$SP_NAME" --query '[0].appId' -o tsv)"
if [ -z "$APP_ID" ]; then
  APP_ID="$(az ad app create --display-name "$SP_NAME" --query appId -o tsv)"
  echo "   ✓ Created app, appId: $APP_ID"
else
  echo "   ✓ Reusing existing app, appId: $APP_ID"
fi

OBJECT_ID="$(az ad app show --id "$APP_ID" --query id -o tsv)"

# Service principal in this tenant
SP_OBJECT_ID="$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv)"
if [ -z "$SP_OBJECT_ID" ]; then
  SP_OBJECT_ID="$(az ad sp create --id "$APP_ID" --query id -o tsv)"
  echo "   ✓ Created service principal: $SP_OBJECT_ID"
else
  echo "   ✓ Service principal already exists: $SP_OBJECT_ID"
fi

# -----------------------------------------------------------------------------
# 3. Grant Contributor at subscription scope (idempotent)
# -----------------------------------------------------------------------------
echo ""
echo "▶  Assigning $ROLE role at subscription scope..."
if az role assignment list \
     --assignee "$APP_ID" \
     --scope "/subscriptions/$SUBSCRIPTION_ID" \
     --role "$ROLE" --query '[0].id' -o tsv | grep -q .; then
  echo "   ✓ Role already assigned"
else
  az role assignment create \
    --assignee "$APP_ID" \
    --role "$ROLE" \
    --scope "/subscriptions/$SUBSCRIPTION_ID" \
    --only-show-errors > /dev/null
  echo "   ✓ $ROLE granted at /subscriptions/$SUBSCRIPTION_ID"
fi

# -----------------------------------------------------------------------------
# 3b. Grant User Access Administrator at the rg-condomanager scope (CM-43)
#     Bicep modules under infra/bicep/modules/ include
#     Microsoft.Authorization/roleAssignments resources (e.g. keyvault.bicep
#     grants the shared MI Key Vault Secrets User on kv-condomanager-<env>).
#     Contributor does NOT include roleAssignments/write — only User Access
#     Administrator or Owner do. Scoping UAA to the single RG limits the
#     blast radius compared to subscription scope (where Contributor sits).
# -----------------------------------------------------------------------------
UAA_ROLE="User Access Administrator"
RG_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/rg-condomanager"

echo ""
echo "▶  Assigning '$UAA_ROLE' role at $RG_SCOPE..."
if az role assignment list \
     --assignee "$APP_ID" \
     --scope "$RG_SCOPE" \
     --role "$UAA_ROLE" --query '[0].id' -o tsv | grep -q .; then
  echo "   ✓ $UAA_ROLE already assigned at RG scope"
else
  az role assignment create \
    --assignee "$APP_ID" \
    --role "$UAA_ROLE" \
    --scope "$RG_SCOPE" \
    --only-show-errors > /dev/null
  echo "   ✓ $UAA_ROLE granted at $RG_SCOPE"
fi

# -----------------------------------------------------------------------------
# 4. Federated credentials (OIDC) — main branch + pull_request + dev / prod
#    These let GitHub Actions exchange its OIDC token for an Azure AAD token
#    without any long-lived secret.
# -----------------------------------------------------------------------------
echo ""
echo "▶  Creating federated credentials..."

add_fic () {
  local name="$1" subject="$2"
  if az ad app federated-credential list --id "$APP_ID" --query "[?name=='$name'] | [0].name" -o tsv 2>/dev/null | grep -q .; then
    echo "   ✓ Federated credential '$name' already exists"
  else
    az ad app federated-credential create --id "$APP_ID" --parameters "$(cat <<JSON
{
  "name": "$name",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "audiences": ["api://AzureADTokenExchange"]
}
JSON
)" --only-show-errors > /dev/null
    echo "   ✓ Created '$name'  →  $subject"
  fi
}

add_fic "github-main"        "repo:$GH_OWNER/$GH_REPO:ref:refs/heads/main"
add_fic "github-pull-request" "repo:$GH_OWNER/$GH_REPO:pull_request"
add_fic "github-env-dev"     "repo:$GH_OWNER/$GH_REPO:environment:dev"
add_fic "github-env-prod"    "repo:$GH_OWNER/$GH_REPO:environment:prod"

# -----------------------------------------------------------------------------
# 5. Emit the 3 GitHub-secrets values
# -----------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  ✅ Done. Copy these 3 values into GitHub repo secrets."
echo "  All three are PUBLIC identifiers — no credential exposure."
echo "============================================================"
echo ""
echo "  AZURE_CLIENT_ID:       $APP_ID"
echo "  AZURE_TENANT_ID:       $TENANT_ID"
echo "  AZURE_SUBSCRIPTION_ID: $SUBSCRIPTION_ID"
echo ""
echo "Next steps:"
echo "  1. Paste these three values into Claude — it will drive Chrome to"
echo "     add them under Settings → Secrets and variables → Actions."
echo "  2. Create two GitHub Environments (Settings → Environments):"
echo "       dev   — no approvers"
echo "       prod  — yourself as approver"
echo "  3. Push a tiny commit that touches infra/ to retrigger the workflow."
echo "     The 'Deploy → rg-condomanager' job should now go green."
