#!/usr/bin/env bash
# Bicep lint test — CM-15 (single-RG topology) + CM-16 (Container Apps modules) + CM-17 (Cosmos DB) + CM-18 (Key Vault + MI) + CM-19 (CI/CD split)
# Verifies:
#   1. Bicep templates compile cleanly (no syntax/type errors)
#   2. main.bicep is resource-group scoped (RG is bootstrapped out-of-band)
#   3. tags.bicep declares the full 5-tag schema for downstream resources
#   4. tags.bicep restricts env to dev / prod / shared (no rogue env names)
#   5. main.parameters.json exists, is valid JSON, and declares env
#   6. (CM-19) deploy.yml targets rg-condomanager via `az deployment group create`
#       and has explicit deploy-dev + deploy-prod jobs gated by GitHub Environments
#   7. (CM-19) build.yml runs `az deployment group what-if` against rg-condomanager
#   8. (CM-19) no stale infra-deploy.yml workflow (split into build.yml + deploy.yml)
#   9. (CM-19) no PAT-style secrets referenced in any workflow (OIDC only)
#  10. (CM-16) all module files in infra/bicep/modules/ compile and are RG-scoped
#  11. (CM-16) per-env modules restrict env to dev / prod
#  12. (CM-16) Container Apps env uses the Consumption workload profile
#  13. (CM-16) VNet subnet is delegated to Microsoft.App/environments
#  14. (CM-16) Container App defaults to minReplicas: 0 (free-tier guard)
#  15. (CM-16) compiled ARM contains the Container Apps + VNet resource types
#  16. (CM-17) cosmos.bicep enables EnableNoSQLVectorSearch + diskANN on
#      policies-vector, declares the four required containers, defaults
#      enableFreeTier to true, and is wired into main.bicep
#  17. (CM-18) keyvault.bicep uses RBAC mode + soft-delete + purge protection,
#      grants the MI principalId `Key Vault Secrets User`, is wired into
#      main.bicep; container-app.bicep accepts userAssignedIdentityId;
#      compiled ARM includes KV + MI + roleAssignments resource types;
#      seed-keyvault-secrets.sh secret list matches keyvault.bicep
#
# Run locally:  bash tests/infra/test_bicep_lint.sh
# Run in CI:    invoked by .github/workflows/build.yml (lint-infra job)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BICEP_DIR="$ROOT/infra/bicep"
MODULES_DIR="$BICEP_DIR/modules"
FAIL=0

# Resolve the Bicep CLI. CI installs the standalone binary at /usr/local/bin/bicep
# (see .github/workflows/infra-deploy.yml). On dev machines without the standalone
# binary we fall back to `az bicep`, which uses the same engine.
if command -v bicep >/dev/null 2>&1; then
  BICEP_BIN="standalone"
  echo "▶  Bicep CLI version: $(bicep --version)"
elif command -v az >/dev/null 2>&1 && az bicep version >/dev/null 2>&1; then
  BICEP_BIN="az"
  echo "▶  Bicep CLI (via az): $(az bicep version 2>&1 | tail -n1)"
else
  echo "❌ Neither standalone 'bicep' nor 'az bicep' is available. Install one before running the lint."
  exit 1
fi

bicep_build() {
  local src="$1"
  local out="$2"
  if [ "$BICEP_BIN" = "standalone" ]; then
    bicep build "$src" --outfile "$out"
  else
    az bicep build --file "$src" --outfile "$out" >/dev/null
  fi
}

echo "▶  Compiling main.bicep → ARM"
bicep_build "$BICEP_DIR/main.bicep" /tmp/main.json
echo "   ✓ main.bicep compiles cleanly"

echo "▶  Compiling tags.bicep → ARM"
bicep_build "$BICEP_DIR/tags.bicep" /tmp/tags.json
echo "   ✓ tags.bicep compiles cleanly"

echo "▶  Compiling per-resource modules in $MODULES_DIR"
MODULES=("vnet" "log-analytics" "container-apps-env" "container-app" "cosmos" "managed-identity" "keyvault")
for m in "${MODULES[@]}"; do
  if [ ! -f "$MODULES_DIR/$m.bicep" ]; then
    echo "   ✗ module $m.bicep MISSING"
    FAIL=1
    continue
  fi
  bicep_build "$MODULES_DIR/$m.bicep" "/tmp/$m.json"
  echo "   ✓ $m.bicep compiles cleanly"
done

REQUIRED_TAGS=("env" "owner" "cost-center" "project" "managed-by")
echo "▶  Verifying required tags exist in tags.bicep (for downstream resources)"
for tag in "${REQUIRED_TAGS[@]}"; do
  if grep -Fq "$tag" "$BICEP_DIR/tags.bicep"; then
    echo "   ✓ tag '$tag' present"
  else
    echo "   ✗ tag '$tag' MISSING from tags.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying targetScope is resourceGroup in main.bicep and all modules"
for f in "$BICEP_DIR/main.bicep" "$MODULES_DIR/vnet.bicep" "$MODULES_DIR/log-analytics.bicep" "$MODULES_DIR/container-apps-env.bicep" "$MODULES_DIR/container-app.bicep" "$MODULES_DIR/cosmos.bicep" "$MODULES_DIR/managed-identity.bicep" "$MODULES_DIR/keyvault.bicep"; do
  if grep -Fq "targetScope = 'resourceGroup'" "$f"; then
    echo "   ✓ $(basename "$f") is resource-group scoped"
  else
    echo "   ✗ $(basename "$f") is NOT resource-group scoped — RG-scoped SP cannot deploy it"
    FAIL=1
  fi
done

echo "▶  Verifying deploy.yml targets rg-condomanager via 'az deployment group create'"
DEPLOY_WF="$ROOT/.github/workflows/deploy.yml"
BUILD_WF="$ROOT/.github/workflows/build.yml"
if [ ! -f "$DEPLOY_WF" ]; then
  echo "   ✗ .github/workflows/deploy.yml MISSING (CM-19 split)"
  FAIL=1
elif grep -Fq "az deployment group create" "$DEPLOY_WF" && grep -Fq "rg-condomanager" "$DEPLOY_WF"; then
  echo "   ✓ deploy.yml uses RG-scoped deployment commands"
else
  echo "   ✗ deploy.yml does not target rg-condomanager via 'az deployment group create'"
  FAIL=1
fi

echo "▶  Verifying build.yml runs what-if against rg-condomanager"
if [ ! -f "$BUILD_WF" ]; then
  echo "   ✗ .github/workflows/build.yml MISSING (CM-19 split)"
  FAIL=1
elif grep -Fq "az deployment group what-if" "$BUILD_WF" && grep -Fq "rg-condomanager" "$BUILD_WF"; then
  echo "   ✓ build.yml runs what-if against rg-condomanager"
else
  echo "   ✗ build.yml does not run what-if against rg-condomanager"
  FAIL=1
fi

echo "▶  Verifying no stale infra-deploy.yml workflow"
if [ -f "$ROOT/.github/workflows/infra-deploy.yml" ]; then
  echo "   ✗ infra-deploy.yml still present (CM-19 split it into build.yml + deploy.yml)"
  FAIL=1
else
  echo "   ✓ infra-deploy.yml removed"
fi

echo "▶  Verifying deploy.yml defines deploy-dev and deploy-prod jobs"
if grep -Eq "^[[:space:]]+deploy-dev:" "$DEPLOY_WF" \
   && grep -Eq "^[[:space:]]+deploy-prod:" "$DEPLOY_WF"; then
  echo "   ✓ deploy-dev + deploy-prod jobs present"
else
  echo "   ✗ deploy.yml missing deploy-dev or deploy-prod job"
  FAIL=1
fi

echo "▶  Verifying deploy.yml gates per-env deploys via GitHub Environments"
if grep -Eq "environment:[[:space:]]+dev" "$DEPLOY_WF" \
   && grep -Eq "environment:[[:space:]]+prod" "$DEPLOY_WF"; then
  echo "   ✓ deploy-dev uses environment: dev, deploy-prod uses environment: prod"
else
  echo "   ✗ deploy.yml does not gate per-env deploys with GitHub Environments"
  FAIL=1
fi

echo "▶  Verifying deploy.yml passes 'env=prod' for prod (no staging in CLAUDE.md topology)"
if grep -Fq "env=prod" "$DEPLOY_WF" && ! grep -Fq "env=staging" "$DEPLOY_WF"; then
  echo "   ✓ deploy.yml has env=prod, no env=staging"
else
  echo "   ✗ deploy.yml is missing env=prod or contains env=staging"
  FAIL=1
fi

echo "▶  Verifying workflows reference OIDC only (no PAT-style secrets)"
PAT_HITS=$(grep -rE "secrets\.[A-Z_]+_TOKEN" "$ROOT/.github/workflows" 2>/dev/null \
            | grep -v "GITHUB_TOKEN" || true)
if [ -n "$PAT_HITS" ]; then
  echo "   ✗ Found PAT-like secret references in workflows:"
  echo "$PAT_HITS"
  FAIL=1
else
  echo "   ✓ no PAT-style secrets referenced in workflows"
fi

echo "▶  Verifying tags.bicep restricts env to dev/prod/shared"
if grep -Eq "@allowed\\(\\s*\\[\\s*'dev',\\s*'prod',\\s*'shared'\\s*\\]" "$BICEP_DIR/tags.bicep"; then
  echo "   ✓ env in tags.bicep is constrained to dev/prod/shared"
else
  echo "   ✗ env @allowed list in tags.bicep does not match dev/prod/shared"
  FAIL=1
fi

echo "▶  Verifying per-env modules restrict env to dev/prod (no 'shared')"
for m in "${MODULES[@]}"; do
  if grep -Eq "@allowed\\(\\s*\\[\\s*'dev',\\s*'prod'\\s*\\]" "$MODULES_DIR/$m.bicep"; then
    echo "   ✓ env in $m.bicep is constrained to dev/prod"
  else
    echo "   ✗ env @allowed list in $m.bicep does not match dev/prod"
    FAIL=1
  fi
done

echo "▶  Verifying Container Apps env uses Consumption workload profile"
if grep -Fq "'Consumption'" "$MODULES_DIR/container-apps-env.bicep" \
   && grep -Fq "workloadProfileType: 'Consumption'" "$MODULES_DIR/container-apps-env.bicep"; then
  echo "   ✓ Consumption workload profile present (free-tier eligible)"
else
  echo "   ✗ Consumption workload profile missing from container-apps-env.bicep"
  FAIL=1
fi

echo "▶  Verifying VNet subnet is delegated to Microsoft.App/environments"
if grep -Fq "serviceName: 'Microsoft.App/environments'" "$MODULES_DIR/vnet.bicep"; then
  echo "   ✓ subnet delegation to Microsoft.App/environments present"
else
  echo "   ✗ VNet subnet is NOT delegated to Microsoft.App/environments — Container Apps will fail to deploy"
  FAIL=1
fi

echo "▶  Verifying Container App defaults to minReplicas 0 (free-tier guard)"
if grep -Eq "param\\s+minReplicas\\s+int\\s*=\\s*0" "$MODULES_DIR/container-app.bicep"; then
  echo "   ✓ minReplicas defaults to 0 (scale-to-zero when idle)"
else
  echo "   ✗ minReplicas does NOT default to 0 in container-app.bicep — could burn vCPU-seconds"
  FAIL=1
fi

echo "▶  Verifying compiled main.json includes Container Apps, VNet, and Cosmos resource types"
for type in "Microsoft.Network/virtualNetworks" "Microsoft.OperationalInsights/workspaces" "Microsoft.App/managedEnvironments" "Microsoft.App/containerApps" "Microsoft.DocumentDB/databaseAccounts"; do
  if grep -Fq "$type" /tmp/main.json; then
    echo "   ✓ $type present in compiled ARM"
  else
    echo "   ✗ $type MISSING from compiled ARM"
    FAIL=1
  fi
done

echo "▶  Verifying main.parameters.json exists, is valid JSON, and declares env"
pfile="$BICEP_DIR/main.parameters.json"
if [ -f "$pfile" ]; then
  echo "   ✓ $pfile present"
  # Prefer python3 (Linux/CI); fall back to python (Windows Git Bash).
  PY=""
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import json" >/dev/null 2>&1; then
      PY="$cand"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "   ✗ no working python interpreter found to validate JSON"
    FAIL=1
  elif "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$pfile" 2>/dev/null; then
    echo "   ✓ valid JSON"
  else
    echo "   ✗ INVALID JSON"
    FAIL=1
  fi
  if "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if 'env' in d.get('parameters', {}) else 1)" "$pfile" 2>/dev/null; then
    echo "   ✓ 'env' parameter declared"
  else
    echo "   ✗ 'env' parameter missing from main.parameters.json"
    FAIL=1
  fi
else
  echo "   ✗ $pfile missing"
  FAIL=1
fi

echo "▶  Verifying no leftover per-env parameter files"
for env in dev staging prod; do
  if [ -f "$BICEP_DIR/main.parameters.$env.json" ]; then
    echo "   ✗ stale file $BICEP_DIR/main.parameters.$env.json still present (single-RG topology uses one main.parameters.json)"
    FAIL=1
  fi
done
echo "   ✓ no stale per-env parameter files"

# ---------------------------------------------------------------------------
# CM-17 — Cosmos DB module checks
# ---------------------------------------------------------------------------

COSMOS="$MODULES_DIR/cosmos.bicep"

echo "▶  Verifying cosmos.bicep enables the NoSQL vector-search capability"
if grep -Fq "EnableNoSQLVectorSearch" "$COSMOS"; then
  echo "   ✓ EnableNoSQLVectorSearch capability declared"
else
  echo "   ✗ EnableNoSQLVectorSearch capability MISSING — vector indexes won't be accepted"
  FAIL=1
fi

echo "▶  Verifying cosmos.bicep defines a diskANN vector index"
if grep -Fq "diskANN" "$COSMOS"; then
  echo "   ✓ diskANN vector index present"
else
  echo "   ✗ diskANN vector index MISSING from cosmos.bicep"
  FAIL=1
fi

echo "▶  Verifying cosmos.bicep declares all four required containers"
REQUIRED_CONTAINERS=("tenants" "tickets" "conversations" "policies-vector")
for c in "${REQUIRED_CONTAINERS[@]}"; do
  # Match the container resource id: `id: 'tenants'`, `id: 'policies-vector'`, …
  if grep -Eq "id:[[:space:]]+'$c'" "$COSMOS"; then
    echo "   ✓ container '$c' declared"
  else
    echo "   ✗ container '$c' MISSING from cosmos.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying cosmos.bicep enables the free tier by default"
if grep -Eq "param enableFreeTier bool = true" "$COSMOS"; then
  echo "   ✓ enableFreeTier defaults to true"
else
  echo "   ✗ enableFreeTier default is not true — AC requires free-tier-by-default"
  FAIL=1
fi

echo "▶  Verifying policies-vector container has dedicated throughput (vector indexing forbids shared throughput)"
if grep -Eq "param vectorContainerThroughput" "$COSMOS" \
   && grep -Eq "throughput:[[:space:]]+vectorContainerThroughput" "$COSMOS"; then
  echo "   ✓ vectorContainerThroughput param wired into a container's options.throughput"
else
  echo "   ✗ policies-vector lacks dedicated throughput — Cosmos rejects vector indexing on shared-throughput containers"
  FAIL=1
fi

echo "▶  Verifying combined throughput (database shared + vector dedicated) stays within free-tier 1000 RU/s"
DB_RU=$(grep -Eo "param databaseThroughput int = [0-9]+" "$COSMOS" | grep -Eo "[0-9]+$" || echo "0")
VEC_RU=$(grep -Eo "param vectorContainerThroughput int = [0-9]+" "$COSMOS" | grep -Eo "[0-9]+$" || echo "0")
TOTAL_RU=$((DB_RU + VEC_RU))
if [ "$TOTAL_RU" -le 1000 ] && [ "$DB_RU" -gt 0 ] && [ "$VEC_RU" -gt 0 ]; then
  echo "   ✓ databaseThroughput=$DB_RU + vectorContainerThroughput=$VEC_RU = ${TOTAL_RU} RU/s (≤ 1000 free-tier ceiling)"
else
  echo "   ✗ combined defaults exceed free-tier ceiling: databaseThroughput=$DB_RU + vectorContainerThroughput=$VEC_RU = ${TOTAL_RU} RU/s"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires in the cosmos module"
if grep -Fq "modules/cosmos.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ main.bicep references modules/cosmos.bicep"
else
  echo "   ✗ main.bicep does NOT reference modules/cosmos.bicep"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-18 — Key Vault + User-Assigned Managed Identity
# ---------------------------------------------------------------------------

KV="$MODULES_DIR/keyvault.bicep"
MI="$MODULES_DIR/managed-identity.bicep"
CA="$MODULES_DIR/container-app.bicep"
SEED_SH="$ROOT/infra/scripts/seed-keyvault-secrets.sh"

echo "▶  Verifying keyvault.bicep enables RBAC authorization (AC #1)"
if grep -Fq "enableRbacAuthorization: true" "$KV"; then
  echo "   ✓ RBAC mode enabled"
else
  echo "   ✗ enableRbacAuthorization: true MISSING — Key Vault not in RBAC mode"
  FAIL=1
fi

echo "▶  Verifying keyvault.bicep has soft-delete + purge protection"
if grep -Fq "enableSoftDelete: true" "$KV" && grep -Fq "enablePurgeProtection: true" "$KV"; then
  echo "   ✓ soft-delete + purge protection enabled"
else
  echo "   ✗ soft-delete or purge protection MISSING from keyvault.bicep"
  FAIL=1
fi

echo "▶  Verifying MI principalId is granted Key Vault Secrets User (role 4633458b-…)"
if grep -Fq "4633458b-17de-408a-b874-0445c86b69e6" "$KV"; then
  echo "   ✓ Key Vault Secrets User role GUID present"
else
  echo "   ✗ Key Vault Secrets User role GUID 4633458b-… MISSING — MI cannot read secrets"
  FAIL=1
fi

echo "▶  Verifying container-app.bicep accepts userAssignedIdentityId param"
if grep -Eq "param[[:space:]]+userAssignedIdentityId[[:space:]]+string" "$CA"; then
  echo "   ✓ userAssignedIdentityId param present on container-app.bicep"
else
  echo "   ✗ userAssignedIdentityId param MISSING — Container App cannot attach the MI"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires managed-identity and keyvault modules"
if grep -Fq "modules/managed-identity.bicep" "$BICEP_DIR/main.bicep" \
   && grep -Fq "modules/keyvault.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ MI + KV modules wired into main.bicep"
else
  echo "   ✗ MI or KV module NOT referenced from main.bicep"
  FAIL=1
fi

echo "▶  Verifying compiled main.json includes KV + MI + roleAssignments resource types"
for type in "Microsoft.KeyVault/vaults" "Microsoft.ManagedIdentity/userAssignedIdentities" "Microsoft.Authorization/roleAssignments"; do
  if grep -Fq "$type" /tmp/main.json; then
    echo "   ✓ $type present in compiled ARM"
  else
    echo "   ✗ $type MISSING from compiled ARM"
    FAIL=1
  fi
done

echo "▶  Verifying seed-keyvault-secrets.sh exists and is non-empty"
if [ -s "$SEED_SH" ]; then
  echo "   ✓ seed script present"
else
  echo "   ✗ infra/scripts/seed-keyvault-secrets.sh missing or empty"
  FAIL=1
fi

echo "▶  Verifying secret-name list in keyvault.bicep matches seed-keyvault-secrets.sh"
# Extract from keyvault.bicep: every single-quoted token inside the
# `param secretNames array = [ ... ]` block.
KV_SECRETS=$(awk "/param secretNames array = \\[/,/^\\]/" "$KV" \
             | grep -oE "'[a-z][a-z0-9-]*'" | tr -d "'" | sort -u)
# Extract from seed script: every bare token inside the `SECRETS=( ... )` block.
SH_SECRETS=$(awk "/^SECRETS=\\(/,/^\\)/" "$SEED_SH" \
             | grep -oE "^[[:space:]]+[a-z][a-z0-9-]+" | tr -d ' ' | sort -u)
if [ -n "$KV_SECRETS" ] && [ "$KV_SECRETS" = "$SH_SECRETS" ]; then
  COUNT=$(printf "%s\n" "$KV_SECRETS" | wc -l | tr -d ' ')
  echo "   ✓ secret-name lists in sync ($COUNT names)"
else
  echo "   ✗ keyvault.bicep secretNames and seed-keyvault-secrets.sh SECRETS differ"
  echo "     --- keyvault.bicep ---"
  printf "%s\n" "$KV_SECRETS" | sed 's/^/       /'
  echo "     --- seed-keyvault-secrets.sh ---"
  printf "%s\n" "$SH_SECRETS" | sed 's/^/       /'
  FAIL=1
fi

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "❌ Lint test FAILED"
  exit 1
fi
echo ""
echo "✅ All Bicep lint checks passed"
