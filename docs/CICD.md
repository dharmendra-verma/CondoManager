# CI/CD — operator guide

Two GitHub Actions workflows handle the build / test / deploy cycle for the
CondoManager repo. This doc is for the human operator (release captain, on-call
engineer) — for the workflow YAML itself see `.github/workflows/build.yml` and
`.github/workflows/deploy.yml`.

For the high-level topology (single RG, dev + prod, no staging) see
[`INFRA.md`](INFRA.md).

---

## Workflow overview

| Workflow | Trigger | Purpose | Cancel-in-progress |
|---|---|---|---|
| `build.yml`  | `pull_request`, `push:main`, `workflow_dispatch` | Per-area lint + tests + what-if + PR summary comment | yes (PR pushes coalesce) |
| `deploy.yml` | `push:main` (paths: `infra/**`, `portal/**`, `deploy.yml`), `release:published`, `workflow_dispatch` | Prod-only Azure deploys into `rg-condomanager` | **no** (never cancel mid-deploy) |

> **Prod-only, no approval gate.** The `dev` jobs were intentionally removed and
> the `prod` GitHub Environment has **no required reviewers** (see the header of
> `.github/workflows/deploy.yml` and `infra/scripts/setup-github-environments.sh`).
> A push to `main` therefore deploys straight to prod with no approval prompt. To
> restore an approval gate, add the repo owner back to the prod `reviewers` array
> in that script and re-run it. To re-enable `dev`, restore the `deploy-dev` /
> `deploy-portal-dev` jobs from git history.

### Trigger → action matrix

| Trigger                                 | Workflow      | Job(s) that run                                          | Environment | Approver gate |
|-----------------------------------------|---------------|----------------------------------------------------------|-------------|---------------|
| PR opened / updated                     | `build.yml`   | `detect`, `lint-infra` (if infra changed), `what-if-infra` (if infra changed), `summary` | none        | none          |
| Push to `main` touching `infra/`        | `build.yml`   | `detect`, `lint-infra`                                   | none        | none          |
| Push to `main` touching `infra/`/`portal/` | `deploy.yml`  | `deploy-prod` (+ `deploy-portal-prod` if `PORTAL_DEPLOY_ENABLED=true`) | `prod`      | none          |
| GitHub Release published                | `deploy.yml`  | `deploy-prod` (+ `deploy-portal-prod` if enabled)        | `prod`      | none          |
| `workflow_dispatch` (`target_env=prod`) | `deploy.yml`  | `deploy-prod` (+ `deploy-portal-prod` if enabled)        | `prod`      | none          |

> `deploy-portal-prod` is additionally gated on the repo variable
> `PORTAL_DEPLOY_ENABLED == 'true'` (CM-37) so `main` stays green until the
> operator provisions the Static Web App. It fetches the SWA deployment token
> just-in-time via OIDC (`az staticwebapp secrets list`) — no stored token secret.

---

## OIDC, not PATs

All Azure auth uses **OIDC federated credentials** — `azure/login@v2` exchanges
the workflow's GitHub OIDC token for an Azure AAD token at runtime. The three
"secrets" the workflows reference are public identifiers, not credentials:

| Secret name             | What it is                                                            |
|-------------------------|-----------------------------------------------------------------------|
| `AZURE_CLIENT_ID`       | Azure AD application (client) ID — public                             |
| `AZURE_TENANT_ID`       | Azure AD tenant ID — public                                           |
| `AZURE_SUBSCRIPTION_ID` | Target subscription ID — public                                       |

The PR summary comment uses the built-in `GITHUB_TOKEN`. No personal access
tokens (PATs) anywhere — the lint test (`tests/infra/test_bicep_lint.sh`) greps
for `secrets.*_TOKEN` references and fails the build if any non-`GITHUB_TOKEN`
match shows up.

### OIDC subject scoping

The federated credentials provisioned by `infra/scripts/setup-azure-oidc.sh`
cover four subjects:

| Subject                                                                | Used by                                  |
|------------------------------------------------------------------------|------------------------------------------|
| `repo:dharmendra-verma/CondoManager:ref:refs/heads/main`               | `deploy.yml` → push-to-`main` prod deploys |
| `repo:dharmendra-verma/CondoManager:pull_request`                      | `build.yml` → `what-if-infra` job        |
| `repo:dharmendra-verma/CondoManager:environment:dev`                   | (legacy — kept in case `dev` is restored) |
| `repo:dharmendra-verma/CondoManager:environment:prod`                  | `deploy.yml` → `deploy-prod` / `deploy-portal-prod` jobs |

The `environment:prod` and `ref:refs/heads/main` subjects both authorize the
prod deploy jobs (the job runs in environment `prod`, on a push to `main`).
The prod environment currently has **no required reviewers**, so OIDC token
exchange is not gated behind an approval — the gate is purely a GitHub
Environment protection rule, independent of how the job authenticates.

### Pre-flight: verify the OIDC plumbing

```bash
# 1. Check the three GitHub secrets exist (values are NOT shown — that's fine)
gh secret list --repo dharmendra-verma/CondoManager

# 2. Check the four federated credentials exist on the Azure AD app
APP_ID=$(az ad app list --display-name github-condomanager-infra --query '[0].appId' -o tsv)
az ad app federated-credential list --id "$APP_ID" --query '[].{name:name, subject:subject}' -o table

# 3. Check the prod GitHub Environment exists (currently NO required reviewers — prod-only, no gate)
gh api repos/dharmendra-verma/CondoManager/environments --jq '.environments[] | {name, protection_rules}'
```

If any of these are missing, re-run `bash infra/scripts/setup-azure-oidc.sh` in
Azure Cloud Shell. The script is idempotent.

---

## How to cut a prod release

```bash
# 1. From an up-to-date main, tag the commit you want to ship
git checkout main && git pull --ff-only origin main
git tag -a v0.1.0 -m "Foundation phase: Container Apps + Cosmos + Key Vault wired"

# 2. Push the tag
git push origin v0.1.0

# 3. Publish a GitHub Release from that tag
gh release create v0.1.0 \
  --title "v0.1.0 — Foundation phase ready" \
  --notes-from-tag

# 4. Watch the deploy
gh run watch
```

The `gh release create` command fires `deploy.yml` with event `release` and
type `published`. The `deploy-prod` job enters the `prod` environment and —
because that environment has no required reviewers — runs immediately:

```bash
az deployment group create \
  --resource-group rg-condomanager \
  --name cm-rg-prod-<run-number> \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json \
  --parameters env=prod
```

### Re-deploying without cutting a new release

Use `workflow_dispatch`. From the GitHub Actions UI: **Actions → deploy → Run
workflow → target_env: prod** (prod is the only option). Or via CLI:

```bash
gh workflow run deploy.yml -f target_env=prod
```

The run executes immediately — `environment: prod` currently has no required
reviewers, so there is no approval prompt regardless of trigger.

---

## Debugging a failed pipeline

### `build.yml` → `lint-infra` failed
Run the lint test locally first; CI reproduces it exactly.
```bash
bash tests/infra/test_bicep_lint.sh
```

### `build.yml` → `what-if-infra` failed with `401 Unauthorized`
Federated credential mismatch. Check that `repo:dharmendra-verma/CondoManager:pull_request`
exists on the Azure AD app — see the pre-flight section above.

### `deploy.yml` shows "Invalid workflow file" and nothing runs
The whole workflow is rejected at parse time before any job runs — usually a YAML
schema error (e.g. a step with an empty `run:` body). GitHub annotates the
offending line. Fix the YAML and push; validate locally first with
`python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))"`.

### `deploy.yml` → `deploy-prod` failed with `403 Forbidden`
Two common causes:
1. The service principal lost its `Contributor` role on `rg-condomanager`. Re-run
   `infra/scripts/setup-azure-oidc.sh` to re-grant.
2. The `prod` GitHub Environment doesn't exist. Create it under
   **Settings → Environments → New environment** (no approver needed — prod-only,
   no gate; see `infra/scripts/setup-github-environments.sh`).

### `deploy.yml` → `deploy-portal-prod` skipped unexpectedly
This job only runs when the repo variable `PORTAL_DEPLOY_ENABLED == 'true'` (CM-37).
If the portal isn't deploying, check **Settings → Secrets and variables → Actions →
Variables** and confirm the SWA (`swa-condomanager-prod`) has been provisioned.

### Sticky PR comment shows stale data
`marocchino/sticky-pull-request-comment@v2` keys on header `ci-summary`. If a
manual edit broke the comment, just push an empty commit to the PR — the next
`summary` job recomposes it from scratch.

---

## Adding a new area's CI later

When a future story lands application code (e.g. Python under `agents/`,
TypeScript under `portal/`), add the corresponding lint/test jobs to `build.yml`
alongside `lint-infra`. Pattern:

1. Extend the `detect` job's `paths-filter` with a new key (e.g. `python`).
2. Add `lint-<area>` and `test-<area>` jobs gated by
   `if: needs.detect.outputs.<area> == 'true'`.
3. Add the new jobs to the `summary` job's `needs:` list and to the markdown
   table the inline `actions/github-script` step composes.

No changes to `deploy.yml` are needed unless the new area introduces its own
Azure resources that need a separate deployment step.
