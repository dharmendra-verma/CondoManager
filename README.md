# CondoManager

Multi-agent platform for condominium maintenance & inquiry management.
Tenants reach the platform over WhatsApp / Telegram / email; a LangGraph
orchestrator routes each request to a specialist agent (Triage → Maintenance /
Knowledge / Escalation / Vendor / Analytics).

Phased delivery is tracked in Jira project **CM**
([projecttracking.atlassian.net](https://projecttracking.atlassian.net/browse/CM-1)).

## Topology

* **One shared Azure resource group** (`rg-condomanager`) hosts both dev and
  prod workloads. Dev/prod are applied at the resource level via tags.
* **Two deployment stages** — `dev` and `prod` — implemented as GitHub
  Environments. Prod requires manual approval.

## Repository layout

```
.
├── infra/                # Bicep IaC (this is where CM-15 lives)
├── .github/workflows/    # GitHub Actions: lint, what-if, deploy
├── tests/                # Per-area test scripts
└── docs/                 # Operator docs (start with docs/INFRA.md)
```

## First-time setup

See [`docs/INFRA.md`](docs/INFRA.md) for the one-time Azure service-principal,
GitHub secrets, and GitHub Environments setup.

## Conventions

* **Branch naming:** `feature/<JIRA-KEY>-<short-slug>` (e.g. `feature/CM-15-azure-resource-groups`).
* **Commit messages:** [Conventional Commits](https://www.conventionalcommits.org/), prefixed with the Jira key, e.g. `feat(infra): CM-15 provision shared RG (dev+prod)`.
* **PR title:** include the Jira key so smart commits sync status back to Jira.
