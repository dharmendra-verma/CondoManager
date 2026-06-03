#!/usr/bin/env bash
# setup-github-environments.sh - (re)create the dev + prod GitHub Environments.
#
# Jira: CM-40  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
#
# The deploy pipeline (.github/workflows/deploy.yml) runs `deploy-prod` under
# `environment: prod`. The approval prompt ("Waiting for review") comes ONLY
# from a required-reviewer rule on that environment - GitHub, not the workflow.
# `setup-azure-oidc.sh` configures the Azure side but cannot touch GitHub
# Environments, so this script is the GitHub-side counterpart.
#
# Posture (prod-only, no approval gate - see CM-54 follow-up):
#   * dev  - zero required reviewers (currently unused; CI dev jobs removed)
#   * prod - zero required reviewers (auto-deploy on push:main, deploys
#            immediately with no approval). To restore an approval gate later,
#            add the repo owner back to the prod `reviewers` array below.
#
# Idempotent: a PUT replaces the environment's protection config, so re-running
# on an already-configured repo sets the same state (no-op effect).
#
# Prereqs:
#   * `gh` CLI authenticated as a principal with ADMIN on the repo (the owner's
#     `repo` scope suffices). The CI service principal does NOT have this - and
#     should not - so this is an operator-run, one-time (re-bootstrap) step.
#
# Usage:
#   bash infra/scripts/setup-github-environments.sh [<owner>/<repo>]
#   # defaults to dharmendra-verma/CondoManager

set -euo pipefail

REPO="${1:-dharmendra-verma/CondoManager}"
OWNER="${REPO%%/*}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found on PATH. Install: https://cli.github.com/" >&2
  exit 1
fi

# Confirm we can administer the repo before mutating anything (catches a token
# without admin/repo scope, or a typo'd repo, before a half-config).
if ! gh api "repos/$REPO" --jq '.permissions.admin' 2>/dev/null | grep -q true; then
  echo "Need ADMIN on $REPO (gh token must have 'repo' scope as an admin)." >&2
  exit 1
fi

echo "Repo=$REPO  (prod approval gate: disabled)"

# dev - explicit zero required reviewers (currently unused; CI dev jobs removed).
echo ">> Configuring 'dev' environment (0 required reviewers)"
gh api --method PUT "repos/$REPO/environments/dev" --input - >/dev/null <<JSON
{ "reviewers": [], "deployment_branch_policy": null }
JSON
echo "   [ok] dev configured"

# prod - zero required reviewers (auto-deploy on push:main; no approval gate).
echo ">> Configuring 'prod' environment (0 required reviewers - no approval gate)"
gh api --method PUT "repos/$REPO/environments/prod" --input - >/dev/null <<JSON
{ "reviewers": [], "deployment_branch_policy": null }
JSON
echo "   [ok] prod configured"

# Verify: prod must NOT carry a required_reviewers rule (proves the approval
# gate is gone, so push:main -> deploy-prod runs immediately).
echo ">> Verifying 'prod' has NO required_reviewers rule"
RULE_TYPES=$(gh api "repos/$REPO/environments/prod" --jq '.protection_rules[].type')
if printf '%s\n' "$RULE_TYPES" | grep -qx "required_reviewers"; then
  echo "   [x] prod STILL has required_reviewers - approval gate not removed (rules: ${RULE_TYPES})" >&2
  exit 1
else
  echo "   [ok] prod has no approval gate"
fi

echo ""
echo "Done. push:main -> deploy-prod (no gate - deploys immediately)."
