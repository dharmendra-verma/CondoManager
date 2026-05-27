# How to push CM-15 to GitHub

These are the two ways to land the work on `dharmendra-verma/CondoManager`.
Pick whichever is easier — both produce the same result on `origin`.

> Both options assume your repo is empty (no `main` branch yet) OR has a `main`
> branch and you want to add this feature branch. Adjust as needed.

---

## Option A — Fast path: init here and push (no git history from sandbox)

```powershell
cd "C:\Users\to_dh\OneDrive\Working\Condo Manager\repo"
git init -b main
git add .
git commit -m "feat(infra): CM-15 provision Azure RGs for dev/staging/prod"
git remote add origin https://github.com/dharmendra-verma/CondoManager.git
# If the repo already has commits on main:
git pull --rebase origin main
git checkout -b feature/CM-15-azure-resource-groups
git push -u origin feature/CM-15-azure-resource-groups
```

Then open a PR from `feature/CM-15-azure-resource-groups` → `main` on GitHub.

---

## Option B — Preserve the sandbox commit (`11debca`) verbatim

A git bundle was generated for you:
**`outputs/CM-15.bundle`** — contains the `feature/CM-15-azure-resource-groups`
branch with the exact commit message and timestamp.

```powershell
# Somewhere outside OneDrive (so .git is happy):
cd "C:\Users\to_dh\source\repos"
git clone https://github.com/dharmendra-verma/CondoManager.git
cd CondoManager
# Fetch the prepared branch from the bundle:
git fetch "C:\path\to\outputs\CM-15.bundle" feature/CM-15-azure-resource-groups:feature/CM-15-azure-resource-groups
git push -u origin feature/CM-15-azure-resource-groups
```

> **Why not OneDrive?** OneDrive's filesystem doesn't grant Linux POSIX locks,
> which breaks `git` inside the sandbox. Put your local working clone in a
> plain folder (e.g. `C:\Users\to_dh\source\repos\`).

---

## Required GitHub setup BEFORE the CI workflow can deploy

The pipeline (`.github/workflows/infra-deploy.yml`) uses Azure OIDC. You have
to do these manual steps once (they involve credentials, so they must come
from your terminal, not the agent):

1. **Create an Azure service principal + federated credential** —
   see `docs/INFRA.md`, section _One-time setup_.

2. **Add GitHub Actions secrets** in
   `Settings → Secrets and variables → Actions`:
   - `AZURE_CLIENT_ID`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`

3. **Create GitHub environments** in `Settings → Environments`:
   - `dev` — no approvers
   - `staging` — at least one approver
   - `prod` — at least one approver

Until those are in place, the workflow's `lint` job will pass (no Azure
needed), but the `what-if` and `deploy-*` jobs will fail on `azure/login@v2`.

---

## After you push

1. The `lint` job runs immediately on the feature branch.
2. When you open the PR, `what-if` runs for all three envs.
3. When the PR merges to `main`, the workflow chains `deploy-dev` →
   (manual approval) → `deploy-staging` → (manual approval) → `deploy-prod`.
4. Update Jira CM-15 to `Done` once `deploy-prod` is green — or let me do
   that next session once you confirm the pipeline went through.
