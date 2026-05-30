#!/usr/bin/env bash
# Bicep lint test — CM-15 + CM-16 + CM-17 + CM-18 + CM-19 + CM-20 + CM-22 + CM-23 + CM-25 + CM-26 + CM-28
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
#  18. (CM-20) acr.bicep defaults SKU=Basic + adminUserEnabled=false; uses
#      hyphen-free name pattern; main.bicep wires it; compiled ARM contains
#      Microsoft.ContainerRegistry/registries; base Dockerfile pins
#      python:3.12-slim + runs apt upgrade; base-image.yml references
#      Trivy + az acr login + docker push; acr-prune.sh exists.
#  19. (CM-22) app-insights.bicep uses workspace-based mode (Flow_Type
#      Bluefield + IngestionMode LogAnalytics + WorkspaceResourceId);
#      main.bicep wires it; compiled ARM contains Microsoft.Insights/components;
#      keyvault.bicep secretNames includes app-insights-connection-string;
#      container-app.bicep accepts appInsightsKvSecretUri; seed-app-insights-secret.sh exists.
#  20. (CM-23) container-app.bicep accepts langsmithKvSecretUri + langsmithProjectName
#      + langsmithEndpoint; declares all four LANGCHAIN_* env-var names;
#      main.bicep wires langsmithEnabled (default dev-only) + the
#      condomanager-<env> project name; seed-langsmith-dataset.py exists.
#  21. (CM-25) workbook.bicep wires Microsoft.Insights/workbooks with
#      sourceId = App Insights component ID; main.bicep wires the module;
#      workbook-payload.json parses cleanly and contains all four AC
#      panel keywords (cost/latency/error/hitl) + the gen_ai.* namespace
#      (regression for the openinference/gen_ai semconv coalesce).
#  22. (CM-26) action-group.bicep + budget.bicep + alert-rules.bicep all
#      compile; main.bicep wires all three; ARM contains
#      Microsoft.Insights/actionGroups, Microsoft.Consumption/budgets,
#      Microsoft.Insights/scheduledQueryRules; budget declares 50/80/100%
#      thresholds; alert-rules references guardrail.cost_cap /
#      guardrail.loop_cap / llm.refused customEvents (the contracts CM-28+
#      emits); useCommonAlertSchema regression on Action Group receivers;
#      @secure() regression on the slack webhook URL param in main.bicep.
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

# CM-43: capture stdout+stderr so the caller can inspect warnings without
# silencing them. Same engine as bicep_build, just no redirection.
bicep_build_capture() {
  local src="$1"
  local out="$2"
  if [ "$BICEP_BIN" = "standalone" ]; then
    bicep build "$src" --outfile "$out" 2>&1
  else
    az bicep build --file "$src" --outfile "$out" 2>&1
  fi
}

echo "▶  Compiling main.bicep → ARM"
bicep_build "$BICEP_DIR/main.bicep" /tmp/main.json
echo "   ✓ main.bicep compiles cleanly"

echo "▶  Compiling tags.bicep → ARM"
bicep_build "$BICEP_DIR/tags.bicep" /tmp/tags.json
echo "   ✓ tags.bicep compiles cleanly"

echo "▶  Compiling per-resource modules in $MODULES_DIR"
MODULES=("vnet" "log-analytics" "container-apps-env" "container-app" "cosmos" "cosmos-rbac" "managed-identity" "keyvault" "acr" "app-insights" "workbook" "action-group" "budget" "alert-rules" "functions" "analytics")
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
for f in "$BICEP_DIR/main.bicep" "$MODULES_DIR/vnet.bicep" "$MODULES_DIR/log-analytics.bicep" "$MODULES_DIR/container-apps-env.bicep" "$MODULES_DIR/container-app.bicep" "$MODULES_DIR/cosmos.bicep" "$MODULES_DIR/cosmos-rbac.bicep" "$MODULES_DIR/managed-identity.bicep" "$MODULES_DIR/keyvault.bicep" "$MODULES_DIR/acr.bicep" "$MODULES_DIR/app-insights.bicep" "$MODULES_DIR/workbook.bicep" "$MODULES_DIR/action-group.bicep" "$MODULES_DIR/budget.bicep" "$MODULES_DIR/alert-rules.bicep" "$MODULES_DIR/functions.bicep" "$MODULES_DIR/analytics.bicep"; do
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

echo "▶  Verifying cosmos.bicep declares all five required containers (CM-28 adds 'checkpoints')"
REQUIRED_CONTAINERS=("tenants" "tickets" "conversations" "policies-vector" "checkpoints")
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

# CM-43: regression guard. Bicep "Warning <code>: <msg>" lines reference a
# .bicep file at (line,col). We filter to those specifically so the unrelated
# "WARNING: A new Bicep release is available" CLI banner doesn't trip the
# check.
echo "▶  Verifying keyvault.bicep produces no Bicep warnings (CM-43 regression guard)"
KV_WARNINGS=$(bicep_build_capture "$KV" /tmp/keyvault-warncheck.json \
                | grep -E '\.bicep\([0-9]+,[0-9]+\) : Warning ' || true)
if [ -n "$KV_WARNINGS" ]; then
  echo "   ✗ keyvault.bicep emitted Bicep warnings:"
  printf "%s\n" "$KV_WARNINGS" | sed 's/^/       /'
  FAIL=1
else
  echo "   ✓ keyvault.bicep is warning-clean"
fi

# ---------------------------------------------------------------------------
# CM-20 — Azure Container Registry + base image pipeline
# ---------------------------------------------------------------------------

ACR_BICEP="$MODULES_DIR/acr.bicep"
BASE_DF="$ROOT/infra/docker/base/Dockerfile"
BASE_WF="$ROOT/.github/workflows/base-image.yml"
PRUNE_SH="$ROOT/infra/scripts/acr-prune.sh"

echo "▶  Verifying acr.bicep defaults SKU to Basic (AC #1)"
if grep -Eq "param sku string = 'Basic'" "$ACR_BICEP"; then
  echo "   ✓ Basic SKU default"
else
  echo "   ✗ Basic SKU default missing from acr.bicep"
  FAIL=1
fi

echo "▶  Verifying acr.bicep disables admin user (no static creds)"
if grep -Eq "param adminUserEnabled bool = false" "$ACR_BICEP"; then
  echo "   ✓ adminUserEnabled defaults to false"
else
  echo "   ✗ adminUserEnabled is NOT false — use OIDC + MI, not username/password"
  FAIL=1
fi

echo "▶  Verifying acr.bicep uses hyphen-free name pattern 'acrcondomanager\${env}'"
if grep -Fq "var acrName = 'acrcondomanager\${env}'" "$ACR_BICEP"; then
  echo "   ✓ ACR name pattern correct (ACR forbids hyphens)"
else
  echo "   ✗ ACR name pattern unexpected — expected: var acrName = 'acrcondomanager\${env}'"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires the acr module"
if grep -Fq "modules/acr.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ acr module referenced"
else
  echo "   ✗ main.bicep does NOT reference modules/acr.bicep"
  FAIL=1
fi

echo "▶  Verifying compiled ARM contains Microsoft.ContainerRegistry/registries"
if grep -Fq "Microsoft.ContainerRegistry/registries" /tmp/main.json; then
  echo "   ✓ Microsoft.ContainerRegistry/registries present in compiled ARM"
else
  echo "   ✗ Microsoft.ContainerRegistry/registries MISSING from compiled ARM"
  FAIL=1
fi

echo "▶  Verifying base Dockerfile pins python:3.12-slim (AC #2)"
if [ -s "$BASE_DF" ] && grep -Eq '^FROM[[:space:]]+python:3\.12-slim' "$BASE_DF"; then
  echo "   ✓ FROM python:3.12-slim present"
else
  echo "   ✗ Dockerfile missing or does NOT FROM python:3.12-slim"
  FAIL=1
fi

echo "▶  Verifying base Dockerfile applies apt security patches"
if grep -Eq "apt-get .*upgrade" "$BASE_DF" 2>/dev/null; then
  echo "   ✓ apt-get upgrade present"
else
  echo "   ✗ Dockerfile missing 'apt-get upgrade' — won't pick up upstream security patches"
  FAIL=1
fi

echo "▶  Verifying base-image.yml exists and wires build + Trivy + ACR push (AC #3)"
if [ -s "$BASE_WF" ] \
   && grep -Fq "aquasecurity/trivy-action" "$BASE_WF" \
   && grep -Fq "az acr login" "$BASE_WF" \
   && grep -Fq "docker push" "$BASE_WF" \
   && grep -Fq "docker build" "$BASE_WF"; then
  echo "   ✓ base-image.yml wired (build + Trivy + az acr login + docker push)"
else
  echo "   ✗ base-image.yml missing or incomplete (need docker build, Trivy, az acr login, docker push)"
  FAIL=1
fi

echo "▶  Verifying acr-prune.sh exists (Basic-SKU retention substitute, AC #4)"
if [ -s "$PRUNE_SH" ]; then
  echo "   ✓ acr-prune.sh present"
else
  echo "   ✗ infra/scripts/acr-prune.sh missing — no retention policy in place"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-22 — Application Insights as OTLP backend
# ---------------------------------------------------------------------------

APPI_BICEP="$MODULES_DIR/app-insights.bicep"
CA_BICEP="$MODULES_DIR/container-app.bicep"
APPI_SEED="$ROOT/infra/scripts/seed-app-insights-secret.sh"

echo "▶  Verifying app-insights.bicep uses workspace-based mode (AC #1)"
# All three together = workspace-based; classic mode lacks Flow_Type=Bluefield + IngestionMode=LogAnalytics.
if grep -Fq "Flow_Type: 'Bluefield'" "$APPI_BICEP" \
   && grep -Fq "IngestionMode: 'LogAnalytics'" "$APPI_BICEP" \
   && grep -Fq "WorkspaceResourceId: logAnalyticsWorkspaceId" "$APPI_BICEP"; then
  echo "   ✓ workspace-based mode wired (Bluefield + LogAnalytics ingestion + WorkspaceResourceId)"
else
  echo "   ✗ app-insights.bicep is NOT workspace-based — classic App Insights is deprecated"
  FAIL=1
fi

echo "▶  Verifying app-insights.bicep accepts a samplingPercentage param (AC #3)"
if grep -Eq "param samplingPercentage int" "$APPI_BICEP" \
   && grep -Fq "SamplingPercentage: samplingPercentage" "$APPI_BICEP"; then
  echo "   ✓ samplingPercentage param wired into SamplingPercentage property"
else
  echo "   ✗ samplingPercentage param NOT wired — server-side adaptive sampling unavailable"
  FAIL=1
fi

echo "▶  Verifying app-insights.bicep emits a @secure connectionString output"
# `@secure()` on a string output is the supported syntax for keeping the conn
# string out of deployment-history plaintext.
if grep -E -A1 "^@secure\(\)" "$APPI_BICEP" | grep -Fq "output connectionString string"; then
  echo "   ✓ connectionString output is @secure"
else
  echo "   ✗ connectionString output is NOT @secure — would expose the InstrumentationKey in deployment history"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires the app-insights module"
if grep -Fq "modules/app-insights.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ app-insights module referenced"
else
  echo "   ✗ main.bicep does NOT reference modules/app-insights.bicep"
  FAIL=1
fi

echo "▶  Verifying main.bicep links App Insights to the existing LAW (CM-16)"
if grep -Fq "logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ App Insights linked to logAnalytics.outputs.workspaceId"
else
  echo "   ✗ main.bicep does NOT pass the LAW workspace ID to App Insights"
  FAIL=1
fi

echo "▶  Verifying compiled ARM contains Microsoft.Insights/components"
if grep -Fq "Microsoft.Insights/components" /tmp/main.json; then
  echo "   ✓ Microsoft.Insights/components present in compiled ARM"
else
  echo "   ✗ Microsoft.Insights/components MISSING from compiled ARM"
  FAIL=1
fi

echo "▶  Verifying container-app.bicep accepts appInsightsKvSecretUri param"
if grep -Eq "param[[:space:]]+appInsightsKvSecretUri[[:space:]]+string" "$CA_BICEP"; then
  echo "   ✓ appInsightsKvSecretUri param present on container-app.bicep"
else
  echo "   ✗ appInsightsKvSecretUri param MISSING — Container App cannot mount the conn string from KV"
  FAIL=1
fi

echo "▶  Verifying container-app.bicep mounts APPLICATIONINSIGHTS_CONNECTION_STRING via secretRef"
# The platform mounts a KV secret by name + secretRef in env. Both must coexist.
if grep -Fq "name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'" "$CA_BICEP" \
   && grep -Fq "secretRef: 'appinsights-conn'" "$CA_BICEP"; then
  echo "   ✓ APPLICATIONINSIGHTS_CONNECTION_STRING wired via secretRef"
else
  echo "   ✗ container-app.bicep does NOT mount APPLICATIONINSIGHTS_CONNECTION_STRING via secretRef"
  FAIL=1
fi

echo "▶  Verifying keyvault.bicep includes app-insights-connection-string in secretNames"
if grep -Fq "'app-insights-connection-string'" "$KV"; then
  echo "   ✓ app-insights-connection-string declared in secretNames"
else
  echo "   ✗ app-insights-connection-string MISSING from keyvault.bicep secretNames default"
  FAIL=1
fi

echo "▶  Verifying seed-app-insights-secret.sh exists"
if [ -s "$APPI_SEED" ]; then
  echo "   ✓ seed-app-insights-secret.sh present"
else
  echo "   ✗ infra/scripts/seed-app-insights-secret.sh missing"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-23 — LangSmith tracing wiring
# ---------------------------------------------------------------------------

LS_SEED="$ROOT/infra/scripts/seed-langsmith-dataset.py"
LS_FIXTURE="$ROOT/tests/eval/triage_seed.jsonl"

echo "▶  Verifying container-app.bicep accepts the three LangSmith params"
if grep -Eq "param[[:space:]]+langsmithKvSecretUri[[:space:]]+string" "$CA_BICEP" \
   && grep -Eq "param[[:space:]]+langsmithProjectName[[:space:]]+string" "$CA_BICEP" \
   && grep -Eq "param[[:space:]]+langsmithEndpoint[[:space:]]+string" "$CA_BICEP"; then
  echo "   ✓ langsmithKvSecretUri + langsmithProjectName + langsmithEndpoint all present"
else
  echo "   ✗ container-app.bicep missing one or more LangSmith params"
  FAIL=1
fi

echo "▶  Verifying container-app.bicep declares all four LANGCHAIN_* env vars"
LANGCHAIN_VARS=("LANGCHAIN_API_KEY" "LANGCHAIN_TRACING_V2" "LANGCHAIN_PROJECT" "LANGCHAIN_ENDPOINT")
for var in "${LANGCHAIN_VARS[@]}"; do
  if grep -Fq "name: '$var'" "$CA_BICEP"; then
    echo "   ✓ $var present"
  else
    echo "   ✗ $var MISSING from container-app.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying main.bicep wires langsmithEnabled with a dev-default"
# Default must be `env == 'dev'` so prod opts in only when the operator overrides.
if grep -Eq "param[[:space:]]+langsmithEnabled[[:space:]]+bool[[:space:]]*=[[:space:]]*env[[:space:]]*==[[:space:]]*'dev'" \
   "$BICEP_DIR/main.bicep"; then
  echo "   ✓ langsmithEnabled defaults to dev-only"
else
  echo "   ✗ langsmithEnabled param missing or default isn't 'env == dev'"
  FAIL=1
fi

echo "▶  Verifying main.bicep uses the condomanager-\${env} LangSmith project pattern"
if grep -Fq "var langsmithProject = 'condomanager-\${env}'" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ langsmithProject naming pattern correct"
else
  echo "   ✗ main.bicep does NOT use the condomanager-\${env} project pattern"
  FAIL=1
fi

echo "▶  Verifying main.bicep computes the langsmith-api-key KV URI"
if grep -Fq "secrets/langsmith-api-key" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ KV URI for langsmith-api-key present"
else
  echo "   ✗ main.bicep doesn't compute the langsmith-api-key KV URI"
  FAIL=1
fi

echo "▶  Verifying seed-langsmith-dataset.py exists"
if [ -s "$LS_SEED" ]; then
  echo "   ✓ seed-langsmith-dataset.py present"
else
  echo "   ✗ infra/scripts/seed-langsmith-dataset.py missing"
  FAIL=1
fi

echo "▶  Verifying tests/eval/triage_seed.jsonl exists and is non-empty"
if [ -s "$LS_FIXTURE" ]; then
  # Sanity: count non-empty lines so an empty-file regression surfaces.
  EXAMPLE_COUNT=$(grep -c . "$LS_FIXTURE" || true)
  if [ "$EXAMPLE_COUNT" -ge 5 ]; then
    echo "   ✓ triage_seed.jsonl present with $EXAMPLE_COUNT examples"
  else
    echo "   ✗ triage_seed.jsonl has only $EXAMPLE_COUNT examples (expected >=5)"
    FAIL=1
  fi
else
  echo "   ✗ tests/eval/triage_seed.jsonl missing"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-25 — Operations workbook over App Insights
# ---------------------------------------------------------------------------

WB_BICEP="$MODULES_DIR/workbook.bicep"
WB_JSON="$MODULES_DIR/workbook-payload.json"

echo "▶  Verifying main.bicep wires the workbook module"
if grep -Fq "modules/workbook.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ workbook module referenced"
else
  echo "   ✗ main.bicep does NOT reference modules/workbook.bicep"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires sourceId = App Insights component ID (regression)"
# Wrong sourceId means workbook queries fail at runtime against the wrong scope.
# Asserts the literal Bicep wiring, not just any non-empty appInsightsId.
if grep -Fq "appInsightsId: appInsights.outputs.appInsightsId" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ workbook sourceId wired to appInsights.outputs.appInsightsId"
else
  echo "   ✗ main.bicep does NOT pass appInsights.outputs.appInsightsId to workbook — queries will fail at runtime"
  FAIL=1
fi

echo "▶  Verifying compiled ARM contains Microsoft.Insights/workbooks"
if grep -Fq "Microsoft.Insights/workbooks" /tmp/main.json; then
  echo "   ✓ Microsoft.Insights/workbooks present in compiled ARM"
else
  echo "   ✗ Microsoft.Insights/workbooks MISSING from compiled ARM"
  FAIL=1
fi

echo "▶  Verifying workbook-payload.json parses cleanly"
if [ -s "$WB_JSON" ]; then
  if "$PY" -m json.tool "$WB_JSON" >/dev/null 2>&1; then
    echo "   ✓ workbook-payload.json is valid JSON"
  else
    echo "   ✗ workbook-payload.json is INVALID JSON"
    FAIL=1
  fi
else
  echo "   ✗ infra/bicep/modules/workbook-payload.json missing"
  FAIL=1
fi

echo "▶  Verifying workbook payload contains the four AC panel name slugs"
# Each panel carries an explicit `"name": "<slug>"` field — checking those
# slugs (rather than free-text keywords) is unambiguous AND avoids the
# Git Bash GNU grep 3.0 SIGABRT on `-i` + multi-byte UTF-8 (em-dash in
# the title strings tripped it).
PANEL_SLUGS=("cost-per-day" "latency-percentiles" "error-rate-per-agent" "hitl-queue-depth")
for slug in "${PANEL_SLUGS[@]}"; do
  if grep -Fq "$slug" "$WB_JSON"; then
    echo "   ✓ panel '$slug' present"
  else
    echo "   ✗ panel '$slug' MISSING from workbook-payload.json"
    FAIL=1
  fi
done

echo "▶  Verifying workbook payload uses OTel gen_ai.* semantic conventions (regression)"
# Guards the openinference + gen_ai coalesce in the cost KQL. If someone
# strips the gen_ai branch (e.g. "openinference is enough"), this fires —
# the OTel SDK is the future, openinference may drop the LLM kind.
if grep -Fq "gen_ai." "$WB_JSON"; then
  echo "   ✓ gen_ai.* attribute family present in payload"
else
  echo "   ✗ gen_ai.* attribute family MISSING — workbook will break when OTel gen_ai semconv lands"
  FAIL=1
fi

echo "▶  Verifying workbook.bicep uses a deterministic guid() name (idempotent redeploys)"
if grep -Eq "guid\\(resourceGroup\\(\\)\\.id," "$WB_BICEP"; then
  echo "   ✓ workbook name is guid()-derived (redeploy-safe)"
else
  echo "   ✗ workbook.bicep does NOT use guid() for the resource name — redeploys may produce duplicates or fail"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-26 — Azure Monitor alerts (Action Group + Budget + 3 query rules)
# ---------------------------------------------------------------------------

AG_BICEP="$MODULES_DIR/action-group.bicep"
BUDGET_BICEP="$MODULES_DIR/budget.bicep"
ALERTS_BICEP="$MODULES_DIR/alert-rules.bicep"

echo "▶  Verifying main.bicep wires the action-group, budget, and alert-rules modules"
for m in action-group budget alert-rules; do
  if grep -Fq "modules/$m.bicep" "$BICEP_DIR/main.bicep"; then
    echo "   ✓ $m module referenced"
  else
    echo "   ✗ main.bicep does NOT reference modules/$m.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying compiled ARM contains the three CM-26 resource types"
# actionGroups + budgets + scheduledQueryRules together = a complete alerts pipeline.
for t in "Microsoft.Insights/actionGroups" "Microsoft.Consumption/budgets" "Microsoft.Insights/scheduledQueryRules"; do
  if grep -Fq "$t" /tmp/main.json; then
    echo "   ✓ $t present in compiled ARM"
  else
    echo "   ✗ $t MISSING from compiled ARM"
    FAIL=1
  fi
done

echo "▶  Verifying budget.bicep declares all three threshold percentages (AC #1)"
for pct in 50 80 100; do
  # Match `threshold: 50` with optional whitespace + word boundary so 50
  # doesn't accidentally match e.g. "500" if someone bumps the budget amount.
  if grep -Eq "threshold:[[:space:]]+${pct}\$" "$BUDGET_BICEP"; then
    echo "   ✓ budget threshold ${pct}% declared"
  else
    echo "   ✗ budget.bicep MISSING threshold: ${pct}"
    FAIL=1
  fi
done

echo "▶  Verifying alert-rules.bicep references the expected customEvents contracts (ACs #3 #4)"
# CM-28 will emit guardrail.cost_cap / guardrail.loop_cap; CM-30 will emit
# llm.refused. Forgetting any one means the corresponding alert silently
# never fires when the producer story lands.
for evt in "guardrail.cost_cap" "guardrail.loop_cap" "llm.refused"; do
  if grep -Fq "$evt" "$ALERTS_BICEP"; then
    echo "   ✓ event contract '$evt' wired into alert KQL"
  else
    echo "   ✗ alert-rules.bicep does NOT reference '$evt' — alert won't fire when the future story emits it"
    FAIL=1
  fi
done

echo "▶  Verifying action-group.bicep uses common alert schema (consistent receiver payload)"
# useCommonAlertSchema: true normalises the JSON shape across all receivers
# so operators write one Slack formatter once. The Bicep default is false.
if grep -Eq "useCommonAlertSchema:[[:space:]]+true" "$AG_BICEP"; then
  echo "   ✓ useCommonAlertSchema: true (consistent payload across all receivers)"
else
  echo "   ✗ action-group.bicep does NOT enable useCommonAlertSchema — receivers will get inconsistent JSON"
  FAIL=1
fi

echo "▶  Verifying alertSlackWebhookUrl is @secure() in main.bicep (regression)"
# Webhook URLs are auth tokens — anyone with one can post to your Slack.
# A plain param would leak into deployment-history plaintext.
# Use awk to find the line preceding the param declaration, since `grep -B1`
# behaves inconsistently across grep versions on Git Bash.
SECURE_LINE=$(awk '/^@secure\(\)/ {prev="@secure()"; next} {if (prev=="@secure()" && /param[[:space:]]+alertSlackWebhookUrl/) print "OK"; prev=""}' "$BICEP_DIR/main.bicep")
if [ "$SECURE_LINE" = "OK" ]; then
  echo "   ✓ alertSlackWebhookUrl declared @secure()"
else
  echo "   ✗ alertSlackWebhookUrl is NOT @secure() — webhook URL would leak into deployment history"
  FAIL=1
fi

echo "▶  Verifying alert-rules.bicep declares all three rule names"
# Each AC maps to one of these — losing one means an AC is silently unmet.
for rule in "alert-latency-slo" "alert-guardrail-trip" "alert-hallucination-spike"; do
  if grep -Fq "$rule" "$ALERTS_BICEP"; then
    echo "   ✓ rule '$rule' declared"
  else
    echo "   ✗ rule '$rule' MISSING from alert-rules.bicep"
    FAIL=1
  fi
done

# ---------------------------------------------------------------------------
# CM-28 — LangGraph spine (orchestrator package + checkpoints container +
#         dual-side guardrail event-name regression with CM-26)
# ---------------------------------------------------------------------------

ORCH_DIR="$ROOT/agents/orchestrator"

echo "▶  Verifying cosmos.bicep declares the 'checkpoints' container with a TTL (CM-28)"
if grep -Eq "id:[[:space:]]+'checkpoints'" "$COSMOS" \
   && grep -Eq "defaultTtl:[[:space:]]+[0-9]+" "$COSMOS"; then
  echo "   ✓ checkpoints container with defaultTtl present"
else
  echo "   ✗ cosmos.bicep MISSING checkpoints container or defaultTtl — LangGraph state will not persist / not auto-purge"
  FAIL=1
fi

echo "▶  Verifying compiled ARM declares five sqlDatabases/containers (CM-28 adds the 5th)"
CONTAINER_COUNT=$(grep -Fo "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers" /tmp/main.json | wc -l | tr -d ' ')
if [ "$CONTAINER_COUNT" -ge 5 ]; then
  echo "   ✓ compiled ARM has $CONTAINER_COUNT Cosmos container references (>=5)"
else
  echo "   ✗ compiled ARM has only $CONTAINER_COUNT Cosmos container references (expected >=5 after CM-28)"
  FAIL=1
fi

echo "▶  Verifying agents/orchestrator/ package files exist (CM-28)"
ORCH_FILES=("__init__.py" "state.py" "guardrails.py" "nodes.py" "graph.py" "checkpointer.py" "demo.py")
for f in "${ORCH_FILES[@]}"; do
  if [ -s "$ORCH_DIR/$f" ]; then
    echo "   ✓ agents/orchestrator/$f present"
  else
    echo "   ✗ agents/orchestrator/$f MISSING or empty"
    FAIL=1
  fi
done

# DUAL-SIDE REGRESSION (CM-26 <-> CM-28): the guardrail-trip span names
# emitted by guardrails.py MUST literally match the customEvents names
# in alert-rules.bicep. Renaming one without the other silently breaks
# the cost-cap / loop-cap alerts.
echo "▶  Verifying guardrails.py literally emits 'guardrail.cost_cap' (CM-26 dual-side regression)"
if grep -Fq "guardrail.cost_cap" "$ORCH_DIR/guardrails.py"; then
  echo "   ✓ 'guardrail.cost_cap' literal present in guardrails.py"
else
  echo "   ✗ guardrails.py MISSING 'guardrail.cost_cap' — CM-26 alert KQL will never match"
  FAIL=1
fi

echo "▶  Verifying guardrails.py literally emits 'guardrail.loop_cap' (CM-26 dual-side regression)"
if grep -Fq "guardrail.loop_cap" "$ORCH_DIR/guardrails.py"; then
  echo "   ✓ 'guardrail.loop_cap' literal present in guardrails.py"
else
  echo "   ✗ guardrails.py MISSING 'guardrail.loop_cap' — CM-26 alert KQL will never match"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-34 — Google Drive → Cosmos vector sync job (Functions app + sync container
#         + KV secret + agents.knowledge package + function code)
# ---------------------------------------------------------------------------

FUNCTIONS="$MODULES_DIR/functions.bicep"
KNOW_DIR="$ROOT/agents/knowledge"
FN_DIR="$ROOT/functions/gdrive-sync"
GDRIVE_SMOKE="$ROOT/infra/scripts/gdrive-sync-smoke-test.py"

echo "▶  Verifying cosmos.bicep declares the 'knowledge_sync' container (CM-34)"
if grep -Eq "id:[[:space:]]+'knowledge_sync'" "$COSMOS" \
   && grep -Eq "paths:[[:space:]]+\[[[:space:]]*'/source'" "$COSMOS"; then
  echo "   ✓ knowledge_sync container (partition /source) present"
else
  echo "   ✗ cosmos.bicep MISSING knowledge_sync container or /source partition key"
  FAIL=1
fi

echo "▶  Verifying keyvault.bicep includes google-drive-sa-key in secretNames (CM-34)"
if grep -Fq "'google-drive-sa-key'" "$KV"; then
  echo "   ✓ google-drive-sa-key declared in secretNames"
else
  echo "   ✗ google-drive-sa-key MISSING from keyvault.bicep secretNames default"
  FAIL=1
fi

echo "▶  Verifying functions.bicep targets Linux Consumption Python + attaches the MI"
if grep -Fq "linuxFxVersion: 'Python|3.12'" "$FUNCTIONS" \
   && grep -Fq "name: 'Y1'" "$FUNCTIONS" \
   && grep -Fq "keyVaultReferenceIdentity: userAssignedIdentityId" "$FUNCTIONS"; then
  echo "   ✓ Python 3.12 + Y1 Consumption + KV-reference identity wired"
else
  echo "   ✗ functions.bicep missing Python 3.12 / Y1 SKU / keyVaultReferenceIdentity"
  FAIL=1
fi

echo "▶  Verifying functions.bicep mounts secrets as Key Vault references (no literals)"
if grep -Fq "@Microsoft.KeyVault(SecretUri=" "$FUNCTIONS" \
   && grep -Fq "secrets/google-drive-sa-key" "$FUNCTIONS" \
   && grep -Fq "secrets/azure-openai-key" "$FUNCTIONS"; then
  echo "   ✓ google-drive-sa-key + azure-openai-key mounted via KV references"
else
  echo "   ✗ functions.bicep does NOT mount both secrets via @Microsoft.KeyVault references"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires the functions module"
if grep -Fq "modules/functions.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ functions module referenced"
else
  echo "   ✗ main.bicep does NOT reference modules/functions.bicep"
  FAIL=1
fi

echo "▶  Verifying compiled ARM contains the Function App + plan + storage types"
for type in "Microsoft.Web/sites" "Microsoft.Web/serverfarms" "Microsoft.Storage/storageAccounts"; do
  if grep -Fq "$type" /tmp/main.json; then
    echo "   ✓ $type present in compiled ARM"
  else
    echo "   ✗ $type MISSING from compiled ARM"
    FAIL=1
  fi
done

echo "▶  Verifying agents/knowledge/ package files exist (CM-34)"
KNOW_FILES=("__init__.py" "models.py" "chunking.py" "embeddings.py" "gdrive_client.py" "cosmos_store.py" "sync.py")
for f in "${KNOW_FILES[@]}"; do
  if [ -s "$KNOW_DIR/$f" ]; then
    echo "   ✓ agents/knowledge/$f present"
  else
    echo "   ✗ agents/knowledge/$f MISSING or empty"
    FAIL=1
  fi
done

echo "▶  Verifying functions/gdrive-sync/ deploy package files exist (CM-34)"
FN_FILES=("function_app.py" "host.json" "requirements.txt")
for f in "${FN_FILES[@]}"; do
  if [ -s "$FN_DIR/$f" ]; then
    echo "   ✓ functions/gdrive-sync/$f present"
  else
    echo "   ✗ functions/gdrive-sync/$f MISSING or empty"
    FAIL=1
  fi
done

echo "▶  Verifying function_app.py declares a 30-minute timer trigger (AC)"
if grep -Fq "0 */30 * * * *" "$FN_DIR/function_app.py" \
   && grep -Fq "timer_trigger" "$FN_DIR/function_app.py"; then
  echo "   ✓ timer_trigger with 30-minute NCRONTAB schedule present"
else
  echo "   ✗ function_app.py missing the 30-minute timer_trigger schedule"
  FAIL=1
fi

echo "▶  Verifying gdrive-sync-smoke-test.py exists (CM-34)"
if [ -s "$GDRIVE_SMOKE" ]; then
  echo "   ✓ gdrive-sync-smoke-test.py present"
else
  echo "   ✗ infra/scripts/gdrive-sync-smoke-test.py missing"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-36 — Analytics & Forecasting weekly digest (Function App + digests container)
# ---------------------------------------------------------------------------

ANALYTICS="$MODULES_DIR/analytics.bicep"
ANALYTICS_PKG="$ROOT/agents/analytics"
ANALYTICS_FN_DIR="$ROOT/functions/analytics-digest"

echo "▶  Verifying cosmos.bicep declares the 'digests' container with a TTL (CM-36)"
if grep -Eq "id:[[:space:]]+'digests'" "$COSMOS" \
   && grep -Eq "paths:[[:space:]]+\[[[:space:]]*'/tenantId'" "$COSMOS"; then
  echo "   ✓ digests container (partition /tenantId) present"
else
  echo "   ✗ cosmos.bicep MISSING digests container or /tenantId partition key"
  FAIL=1
fi

echo "▶  Verifying analytics.bicep targets Linux Consumption Python + attaches the MI"
if grep -Fq "linuxFxVersion: 'Python|3.12'" "$ANALYTICS" \
   && grep -Fq "name: 'Y1'" "$ANALYTICS" \
   && grep -Fq "keyVaultReferenceIdentity: userAssignedIdentityId" "$ANALYTICS"; then
  echo "   ✓ Python 3.12 + Y1 Consumption + KV-reference identity wired"
else
  echo "   ✗ analytics.bicep missing Python 3.12 / Y1 SKU / keyVaultReferenceIdentity"
  FAIL=1
fi

echo "▶  Verifying analytics.bicep mounts the Slack webhook as a Key Vault reference"
if grep -Fq "@Microsoft.KeyVault(SecretUri=" "$ANALYTICS" \
   && grep -Fq "secrets/slack-webhook-url" "$ANALYTICS"; then
  echo "   ✓ slack-webhook-url mounted via KV reference"
else
  echo "   ✗ analytics.bicep does NOT mount slack-webhook-url via @Microsoft.KeyVault"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires the analytics module"
if grep -Fq "modules/analytics.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ analytics module referenced"
else
  echo "   ✗ main.bicep does NOT reference modules/analytics.bicep"
  FAIL=1
fi

echo "▶  Verifying agents/analytics/ package files exist (CM-36)"
ANALYTICS_FILES=("__init__.py" "models.py" "analyze.py" "digest.py" "repository.py" "run.py")
for f in "${ANALYTICS_FILES[@]}"; do
  if [ -s "$ANALYTICS_PKG/$f" ]; then
    echo "   ✓ agents/analytics/$f present"
  else
    echo "   ✗ agents/analytics/$f MISSING or empty"
    FAIL=1
  fi
done

echo "▶  Verifying functions/analytics-digest/ declares a weekly timer trigger (AC #6)"
if grep -Fq "0 0 8 * * 1" "$ANALYTICS_FN_DIR/function_app.py" \
   && grep -Fq "timer_trigger" "$ANALYTICS_FN_DIR/function_app.py"; then
  echo "   ✓ timer_trigger with weekly NCRONTAB schedule present"
else
  echo "   ✗ analytics-digest function_app.py missing the weekly timer_trigger schedule"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-41 — pre-register Azure resource providers in the bootstrap script
#
# The bootstrap script (setup-azure-oidc.sh) can't run in CI — the CI principal
# is intentionally RG-scoped (CM-15) and provider registration is a sub-level
# action. So these checks statically guard the namespace list + the wait logic
# against typo/regression drift, the same way the CM-18 secret-name-sync check
# guards keyvault.bicep ↔ seed-keyvault-secrets.sh.
# ---------------------------------------------------------------------------

OIDC_SH="$ROOT/infra/scripts/setup-azure-oidc.sh"

echo "▶  Verifying setup-azure-oidc.sh exists (CM-41)"
if [ -s "$OIDC_SH" ]; then
  echo "   ✓ setup-azure-oidc.sh present"
else
  echo "   ✗ infra/scripts/setup-azure-oidc.sh missing or empty"
  FAIL=1
fi

echo "▶  Verifying setup-azure-oidc.sh registers every required provider (CM-41 AC1)"
# Keep this list identical to the namespaces main.bicep deploys.
REQUIRED_PROVIDERS=("Microsoft.Network" "Microsoft.OperationalInsights" "Microsoft.App" \
                    "Microsoft.DocumentDB" "Microsoft.KeyVault" "Microsoft.ContainerRegistry" \
                    "Microsoft.Insights")
for ns in "${REQUIRED_PROVIDERS[@]}"; do
  if grep -Fq "$ns" "$OIDC_SH"; then
    echo "   ✓ provider '$ns' referenced in bootstrap script"
  else
    echo "   ✗ provider '$ns' MISSING from setup-azure-oidc.sh — its first deploy will hit MissingSubscriptionRegistration"
    FAIL=1
  fi
done

echo "▶  Verifying setup-azure-oidc.sh registers AND waits for 'Registered' (CM-41 AC2/AC3)"
# `az provider register` makes it register; polling registrationState is how it
# blocks until done (AC3) and short-circuits already-registered namespaces (AC2).
if grep -Fq "az provider register" "$OIDC_SH" \
   && grep -Fq "registrationState" "$OIDC_SH"; then
  echo "   ✓ provider register + registrationState wait present"
else
  echo "   ✗ setup-azure-oidc.sh missing 'az provider register' or the 'registrationState' wait"
  FAIL=1
fi

# ---------------------------------------------------------------------------
# CM-38 — PII detection/masking + audit logging (audit container + data-plane
#         RBAC module + agents/security package)
# ---------------------------------------------------------------------------

COSMOS_RBAC="$MODULES_DIR/cosmos-rbac.bicep"
SECURITY_PKG="$ROOT/agents/security"

echo "▶  Verifying cosmos.bicep declares the 'audit' container that never expires (CM-38 AC4)"
# defaultTtl: -1 = TTL enabled, no default expiry → audit records never auto-purge.
if grep -Eq "id:[[:space:]]+'audit'" "$COSMOS" \
   && grep -Eq "defaultTtl:[[:space:]]+-1" "$COSMOS"; then
  echo "   ✓ audit container present with defaultTtl: -1 (never auto-purges)"
else
  echo "   ✗ cosmos.bicep MISSING the audit container or its defaultTtl: -1 — audit trail would expire"
  FAIL=1
fi

echo "▶  Verifying cosmos-rbac.bicep grants the Cosmos Data Contributor data-plane role (CM-38 AC3)"
# Built-in "Cosmos DB Data Contributor" role id + the sqlRoleAssignments type.
if grep -Fq "00000000-0000-0000-0000-000000000002" "$COSMOS_RBAC" \
   && grep -Fq "sqlRoleAssignments" "$COSMOS_RBAC"; then
  echo "   ✓ data-plane Data Contributor role assignment present"
else
  echo "   ✗ cosmos-rbac.bicep MISSING the Data Contributor role id or sqlRoleAssignments resource"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires the cosmos-rbac module with the MI principalId (CM-38 AC3)"
if grep -Fq "modules/cosmos-rbac.bicep" "$BICEP_DIR/main.bicep" \
   && grep -Fq "principalId: managedIdentity.outputs.principalId" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ cosmos-rbac wired with managedIdentity.outputs.principalId"
else
  echo "   ✗ main.bicep does NOT wire cosmos-rbac with the MI principalId"
  FAIL=1
fi

echo "▶  Verifying compiled ARM contains Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments (CM-38)"
if grep -Fq "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments" /tmp/main.json; then
  echo "   ✓ sqlRoleAssignments present in compiled ARM"
else
  echo "   ✗ sqlRoleAssignments MISSING from compiled ARM — data-plane RBAC not deployed"
  FAIL=1
fi

echo "▶  Verifying agents/security/ package files exist (CM-38)"
SECURITY_FILES=("__init__.py" "models.py" "detection.py" "masking.py" "field_access.py" "audit.py" "retention.py")
for f in "${SECURITY_FILES[@]}"; do
  if [ -s "$SECURITY_PKG/$f" ]; then
    echo "   ✓ agents/security/$f present"
  else
    echo "   ✗ agents/security/$f MISSING or empty"
    FAIL=1
  fi
done

echo "▶  Verifying tests/eval/security_pii_seed.jsonl exists and is non-empty (CM-38 AC1)"
PII_SEED="$ROOT/tests/eval/security_pii_seed.jsonl"
if [ -s "$PII_SEED" ]; then
  SEED_COUNT=$(grep -c . "$PII_SEED" || true)
  if [ "$SEED_COUNT" -ge 10 ]; then
    echo "   ✓ security_pii_seed.jsonl present with $SEED_COUNT examples"
  else
    echo "   ✗ security_pii_seed.jsonl has only $SEED_COUNT examples (expected >=10)"
    FAIL=1
  fi
else
  echo "   ✗ tests/eval/security_pii_seed.jsonl missing"
  FAIL=1
fi

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "❌ Lint test FAILED"
  exit 1
fi
echo ""
echo "✅ All Bicep lint checks passed"
