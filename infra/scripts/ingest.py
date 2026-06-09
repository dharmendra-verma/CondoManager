#!/usr/bin/env python3
"""One-command policy ingestion into Cosmos DB.

Jira: CM-66 (local folder ingestion)

Auto-discovers every Azure credential from your deployed resources so you
only need to supply a folder path (and optionally a tenant id).

Usage:
    python infra/scripts/ingest.py --folder ./policies
    python infra/scripts/ingest.py --folder ./policies --tenant tenant-smoke-test
    python infra/scripts/ingest.py --folder ./policies --dry-run
    python infra/scripts/ingest.py --folder ./policies --additive
    python infra/scripts/ingest.py --folder ./policies --env prod

What it does automatically:
    1. Checks you are logged in via `az login`
    2. Reads COSMOS_ENDPOINT from the deployed Cosmos account
    3. Reads AZURE_OPENAI_ENDPOINT from the gdrive-sync Function App settings
    4. Reads AZURE_OPENAI_API_KEY from Key Vault (azure-openai-key)
    5. Sets those as environment variables for this process
    6. Runs the ingest pipeline (chunk -> embed -> upsert into policies-vector)

Auth: az login must have been run before this script. No secrets are stored
on disk — they are read from Azure at runtime and held only in memory.

Exit codes: 0 = success, 1 = failure (printed to stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ── repo root on sys.path so `agents.*` is importable ───────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── resource naming (mirrors main.bicep conventions) ─────────────────────────
RG = "rg-condomanager"
_COSMOS_ACCOUNT   = "cosmos-condomanager-{env}"
_KV_NAME          = "kv-condomanager-{env}"
_FUNC_NAME        = "func-condomanager-{env}"
_OPENAI_KEY_SECRET = "azure-openai-key"
_DEFAULT_TENANT   = "tenant-smoke-test"
_DEFAULT_DEPLOY   = "text-embedding-3-small"


# ── helpers ──────────────────────────────────────────────────────────────────

def _az(*args: str, error_hint: str = "") -> str:
    """Run an `az` CLI command and return stdout. Exits on failure."""
    cmd = ["az", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        _die(
            "Azure CLI (`az`) not found.\n"
            "Install it from https://aka.ms/installazurecliwindows and re-run."
        )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        _die(
            f"az command failed: {' '.join(cmd)}\n"
            f"  {msg}\n"
            + (f"  Hint: {error_hint}" if error_hint else "")
        )
    return result.stdout.strip()


def _die(msg: str) -> None:
    sys.stderr.write(f"\nERROR: {msg}\n")
    sys.exit(1)


def _step(msg: str) -> None:
    print(f"  {msg}")


# ── Azure config discovery ───────────────────────────────────────────────────

def _check_az_login() -> None:
    """Exit with a helpful message if the user is not logged in."""
    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "user.name", "-o", "tsv"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            _die(
                "You are not logged in to Azure.\n"
                "  Run:  az login\n"
                "  Then re-run this script."
            )
        _step(f"Logged in as: {result.stdout.strip()}")
    except FileNotFoundError:
        _die("Azure CLI not found. Install from https://aka.ms/installazurecliwindows")


def _get_cosmos_endpoint(env: str) -> str:
    account = _COSMOS_ACCOUNT.format(env=env)
    _step(f"Fetching Cosmos endpoint from account: {account}")
    return _az(
        "cosmosdb", "show",
        "--resource-group", RG,
        "--name", account,
        "--query", "documentEndpoint",
        "-o", "tsv",
        error_hint=f"Is the Bicep deploy complete? Expected account: {account}",
    )


def _get_openai_endpoint(env: str) -> str | None:
    """Read AZURE_OPENAI_ENDPOINT from the Function App settings (best-effort)."""
    func = _FUNC_NAME.format(env=env)
    _step(f"Fetching OpenAI endpoint from Function App: {func}")
    try:
        raw = _az(
            "functionapp", "config", "appsettings", "list",
            "--resource-group", RG,
            "--name", func,
            "--query", "[?name=='AZURE_OPENAI_ENDPOINT'].value | [0]",
            "-o", "tsv",
        )
        return raw if raw else None
    except SystemExit:
        # Function App may not exist in prod (deployGdriveSync=false by default)
        return None


def _get_openai_key(env: str) -> str:
    kv = _KV_NAME.format(env=env)
    _step(f"Fetching OpenAI API key from Key Vault: {kv} / {_OPENAI_KEY_SECRET}")
    return _az(
        "keyvault", "secret", "show",
        "--vault-name", kv,
        "--name", _OPENAI_KEY_SECRET,
        "--query", "value",
        "-o", "tsv",
        error_hint=(
            f"Secret '{_OPENAI_KEY_SECRET}' not found in '{kv}'.\n"
            "  Seed it with: az keyvault secret set "
            f"--vault-name {kv} --name {_OPENAI_KEY_SECRET} --value <your-key>"
        ),
    )


def _discover_config(env: str) -> dict[str, str]:
    """Discover all required Azure config. Returns env-var dict."""
    config: dict[str, str] = {}

    config["COSMOS_ENDPOINT"] = _get_cosmos_endpoint(env)

    openai_endpoint = _get_openai_endpoint(env)
    if openai_endpoint:
        config["AZURE_OPENAI_ENDPOINT"] = openai_endpoint
    elif "AZURE_OPENAI_ENDPOINT" in os.environ:
        _step("AZURE_OPENAI_ENDPOINT already set in environment — using it.")
        config["AZURE_OPENAI_ENDPOINT"] = os.environ["AZURE_OPENAI_ENDPOINT"]
    else:
        _die(
            "Could not discover AZURE_OPENAI_ENDPOINT.\n"
            "  The Function App may not be deployed. Set it manually:\n"
            "    set AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/"
        )

    config["AZURE_OPENAI_API_KEY"] = _get_openai_key(env)
    config["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"] = (
        os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or _DEFAULT_DEPLOY
    )
    return config


# ── ingest ───────────────────────────────────────────────────────────────────

def _run_ingest(
    *,
    folder: Path,
    tenant: str,
    dry_run: bool,
    additive: bool,
) -> int:
    """Import and invoke the ingest pipeline in-process (no subprocess overhead)."""
    # Build the argv list that ingest-local-folder.py would receive
    argv = [
        "--tenant", tenant,
        "--folder", str(folder),
        "--source", f"local:{folder.as_posix()}",
    ]
    if dry_run:
        argv.append("--dry-run")
    if additive:
        argv.append("--additive")

    # Import the existing script as a module and call its main()
    import importlib.util
    script = Path(__file__).parent / "ingest-local-folder.py"
    spec = importlib.util.spec_from_file_location("ingest_local_folder", script)
    mod = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
    spec.loader.exec_module(mod)                  # type: ignore[union-attr]
    return mod.main(argv)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest a local folder of policy files into Cosmos DB.\n"
            "Discovers all Azure credentials automatically — just provide a folder."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python infra/scripts/ingest.py --folder ./policies\n"
            "  python infra/scripts/ingest.py --folder ./policies --dry-run\n"
            "  python infra/scripts/ingest.py --folder C:/docs/bylaws --tenant t-acme\n"
            "  python infra/scripts/ingest.py --folder ./policies --env prod --additive\n"
        ),
    )
    parser.add_argument(
        "--folder", required=True,
        help="Path to the folder containing .txt / .md / .pdf policy files.",
    )
    parser.add_argument(
        "--tenant", default=_DEFAULT_TENANT,
        help=f"Tenant id (Cosmos partition key). Default: {_DEFAULT_TENANT}",
    )
    parser.add_argument(
        "--env", default="dev", choices=["dev", "prod"],
        help="Azure environment to read config from. Default: dev",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be ingested — no Cosmos writes, no embedding calls.",
    )
    parser.add_argument(
        "--additive", action="store_true",
        help=(
            "Add / update only — do NOT delete chunks for files removed from the folder. "
            "Default (mirror) mode deletes chunks for missing files."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        _die(f"--folder is not a directory: {folder}")

    print(f"\nCondoManager policy ingestion")
    print(f"  Folder : {folder}")
    print(f"  Tenant : {args.tenant}")
    print(f"  Env    : {args.env}")
    print(f"  Mode   : {'DRY RUN' if args.dry_run else 'additive' if args.additive else 'mirror (prune)'}")
    print()

    print("Discovering Azure config...")
    _check_az_login()

    config = _discover_config(args.env)

    # Inject discovered values into the current process environment so the
    # agents.knowledge modules (which read from os.environ) pick them up.
    for key, value in config.items():
        os.environ[key] = value

    print()
    print("Config resolved:")
    print(f"  COSMOS_ENDPOINT                   = {config['COSMOS_ENDPOINT']}")
    print(f"  AZURE_OPENAI_ENDPOINT             = {config['AZURE_OPENAI_ENDPOINT']}")
    print(f"  AZURE_OPENAI_EMBEDDING_DEPLOYMENT = {config['AZURE_OPENAI_EMBEDDING_DEPLOYMENT']}")
    print(f"  AZURE_OPENAI_API_KEY              = {'*' * 8}  (from Key Vault)")
    print()

    return _run_ingest(
        folder=folder,
        tenant=args.tenant,
        dry_run=args.dry_run,
        additive=args.additive,
    )


if __name__ == "__main__":
    sys.exit(main())
