#!/usr/bin/env bash
# Bicep lint test — CM-15 (single-RG topology, RG-scoped SP), CM-17 (Cosmos)
# Verifies:
#   1. Bicep templates compile cleanly (no syntax/type errors)
#   2. main.bicep is resource-group scoped (RG is bootstrapped out-of-band)
#   3. tags.bicep declares the full 5-tag schema for downstream resources
#   4. tags.bicep restricts env to dev / prod / shared (no rogue env names)
#   5. main.parameters.json exists and is valid JSON
#   6. workflow targets rg-condomanager via `az deployment group …`
#   7. cosmos.bicep enables EnableNoSQLVectorSearch + diskANN on the
#      policies-vector container and main.bicep wires the module in
#
# Run locally:  bash tests/infra/test_bicep_lint.sh
# Run in CI:    invoked by .github/workflows/infra-deploy.yml

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BICEP_DIR="$ROOT/infra/bicep"
FAIL=0

echo "▶  Bicep CLI version: $(bicep --version)"

echo "▶  Compiling main.bicep → ARM"
bicep build "$BICEP_DIR/main.bicep" --outfile /tmp/main.json
echo "   ✓ main.bicep compiles cleanly"

echo "▶  Compiling tags.bicep → ARM"
bicep build "$BICEP_DIR/tags.bicep" --outfile /tmp/tags.json
echo "   ✓ tags.bicep compiles cleanly"

echo "▶  Compiling modules/cosmos.bicep → ARM"
bicep build "$BICEP_DIR/modules/cosmos.bicep" --outfile /tmp/cosmos.json
echo "   ✓ modules/cosmos.bicep compiles cleanly"

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

echo "▶  Verifying targetScope is resourceGroup in main.bicep"
if grep -Fq "targetScope = 'resourceGroup'" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ main.bicep is resource-group scoped"
else
  echo "   ✗ main.bicep is NOT resource-group scoped — RG-scoped SP cannot deploy it"
  FAIL=1
fi

echo "▶  Verifying workflow targets rg-condomanager via 'az deployment group'"
WF="$ROOT/.github/workflows/infra-deploy.yml"
if grep -Fq "az deployment group" "$WF" && grep -Fq "rg-condomanager" "$WF"; then
  echo "   ✓ workflow uses RG-scoped deployment commands"
else
  echo "   ✗ workflow does not target rg-condomanager via 'az deployment group'"
  FAIL=1
fi

echo "▶  Verifying tags.bicep restricts env to dev/prod/shared"
if grep -Eq "@allowed\\(\\s*\\[\\s*'dev',\\s*'prod',\\s*'shared'\\s*\\]" "$BICEP_DIR/tags.bicep"; then
  echo "   ✓ env in tags.bicep is constrained to dev/prod/shared"
else
  echo "   ✗ env @allowed list in tags.bicep does not match dev/prod/shared"
  FAIL=1
fi

echo "▶  Verifying main.parameters.json exists and is valid JSON"
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

COSMOS="$BICEP_DIR/modules/cosmos.bicep"

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

echo "▶  Verifying main.bicep wires in the cosmos module"
if grep -Fq "modules/cosmos.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ main.bicep references modules/cosmos.bicep"
else
  echo "   ✗ main.bicep does NOT reference modules/cosmos.bicep"
  FAIL=1
fi

echo "▶  Verifying main.parameters.json supplies the env parameter"
if grep -Fq '"env"' "$BICEP_DIR/main.parameters.json"; then
  echo "   ✓ env parameter present in main.parameters.json"
else
  echo "   ✗ env parameter MISSING from main.parameters.json"
  FAIL=1
fi

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "❌ Lint test FAILED"
  exit 1
fi
echo ""
echo "✅ All Bicep lint checks passed"
