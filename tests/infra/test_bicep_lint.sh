#!/usr/bin/env bash
# Bicep lint test — CM-15 (single-RG topology)
# Verifies:
#   1. Bicep templates compile cleanly (no syntax/type errors)
#   2. All required tags appear in main output (env, owner, cost-center, project, managed-by)
#   3. RG naming convention is enforced (rg-condomanager)
#   4. main.bicep is subscription-scoped
#   5. tags.bicep restricts env to dev / prod / shared (no rogue env names)
#   6. main.parameters.json exists and is valid JSON
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

echo "▶  Verifying required tags exist in main.bicep"
REQUIRED_TAGS=("env" "owner" "cost-center" "project" "managed-by")
for tag in "${REQUIRED_TAGS[@]}"; do
  if grep -Fq "$tag" "$BICEP_DIR/main.bicep"; then
    echo "   ✓ tag '$tag' present"
  else
    echo "   ✗ tag '$tag' MISSING from main.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying required tags exist in tags.bicep (for downstream resources)"
for tag in "${REQUIRED_TAGS[@]}"; do
  if grep -Fq "$tag" "$BICEP_DIR/tags.bicep"; then
    echo "   ✓ tag '$tag' present"
  else
    echo "   ✗ tag '$tag' MISSING from tags.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying RG naming convention"
if grep -Fq "'rg-condomanager'" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ default RG name 'rg-condomanager' present"
else
  echo "   ✗ RG name 'rg-condomanager' not found in main.bicep"
  FAIL=1
fi

echo "▶  Verifying targetScope is subscription in main.bicep"
if grep -Fq "targetScope = 'subscription'" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ main.bicep is subscription-scoped"
else
  echo "   ✗ main.bicep is NOT subscription-scoped — RG creation will fail"
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
  if python3 -c "import json,sys; json.load(open('$pfile'))" 2>/dev/null; then
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

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "❌ Lint test FAILED"
  exit 1
fi
echo ""
echo "✅ All Bicep lint checks passed"
