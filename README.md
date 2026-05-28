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

## For human engineers

Start with [`docs/INFRA.md`](docs/INFRA.md) for the one-time Azure
service-principal, GitHub secrets, and GitHub Environments setup.

## For AI coding agents (Claude Code, Cursor, etc.)

**Read these two files first** — they encode every decision already made so
you don't have to re-ask the human:

* [`CLAUDE.md`](CLAUDE.md) — project context, conventions, tech stack, naming,
  tagging, story workflow, Jira IDs.
* [`AGENT_RULES.md`](AGENT_RULES.md) — autonomy contract. Lists what to do
  without asking and the (short) list of things that still need human OK.

In short: **act, then report**. Don't ask permission for every step.

## Repository layout

```
.
├── CLAUDE.md, AGENT_RULES.md     # Agent briefing (read these first)
├── infra/                        # Bicep IaC
├── .github/workflows/            # GitHub Actions: lint, what-if, deploy
├── tests/                        # Per-area test scripts
└── docs/                         # Operator docs
```

## Conventions (the short version)

* **Branch naming:** `feature/<JIRA-KEY>-<short-slug>`
* **Commit messages:** Conventional Commits, prefixed with the Jira key,
  e.g. `feat(infra): CM-15 provision shared RG (dev+prod)`.
* **PR titles:** include the Jira key so smart commits sync status to Jira.

See [`CLAUDE.md`](CLAUDE.md) §3 for the full set of conventions.
