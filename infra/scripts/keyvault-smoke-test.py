#!/usr/bin/env python3
"""Post-deploy smoke-test for CM-18 — Key Vault + Managed Identity round-trip.

Validates that the User-Assigned MI created by managed-identity.bicep and
granted Key Vault Secrets User on the vault by keyvault.bicep can actually
read a secret via DefaultAzureCredential. Targets the `cosmos-connection-string`
secret because seed-keyvault-secrets.sh creates it with the placeholder
`REPLACE-ME` — the test only asserts the read succeeds with a non-empty value,
so it passes against placeholders OR real secrets.

Not run in CI — needs a live KV. Run locally (via `az login`) or from inside
the Container App (via the attached MI). Both paths use the same code; the
credential chain figures out which auth method works in the current env.

Usage:
    # Env-var form (preferred for scripts):
    export KEYVAULT_NAME=kv-condomanager-dev
    python infra/scripts/keyvault-smoke-test.py

    # CLI flag form:
    python infra/scripts/keyvault-smoke-test.py --vault kv-condomanager-dev

    # Test a specific secret instead of the default:
    python infra/scripts/keyvault-smoke-test.py --vault kv-condomanager-dev --secret azure-openai-key

Exit codes: 0 = pass, 1 = fail.

Dependencies:
    pip install "azure-identity>=1.15.0" "azure-keyvault-secrets>=4.7.0"
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
except ImportError:
    sys.stderr.write(
        "azure-identity + azure-keyvault-secrets are required.\n"
        "Install with: pip install 'azure-identity>=1.15.0' 'azure-keyvault-secrets>=4.7.0'\n"
    )
    sys.exit(1)


DEFAULT_SECRET = "cosmos-connection-string"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Key Vault + MI smoke-test (CM-18).")
    p.add_argument(
        "--vault",
        default=os.environ.get("KEYVAULT_NAME"),
        help="Key Vault name (or KEYVAULT_NAME env var). e.g. kv-condomanager-dev",
    )
    p.add_argument(
        "--secret",
        default=DEFAULT_SECRET,
        help=f"Secret name to read. Default: {DEFAULT_SECRET}",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.vault:
        sys.stderr.write("Vault name required (--vault or KEYVAULT_NAME env var).\n")
        return 1

    vault_url = f"https://{args.vault}.vault.azure.net/"
    print(f"-> Vault:  {vault_url}")
    print(f"-> Secret: {args.secret}")

    try:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        client = SecretClient(vault_url=vault_url, credential=credential)
        secret = client.get_secret(args.secret)
    except ClientAuthenticationError as e:
        sys.stderr.write(
            "FAIL: DefaultAzureCredential could not authenticate.\n"
            "  - Locally: run 'az login'.\n"
            "  - In Container App: confirm id-condomanager-<env> is attached.\n"
            f"  Underlying error: {e}\n"
        )
        return 1
    except HttpResponseError as e:
        sys.stderr.write(
            f"FAIL: Key Vault returned an HTTP error.\n"
            f"  Status: {e.status_code}\n"
            f"  Message: {e.message}\n"
            "  - 403/Forbidden: the calling principal lacks 'Key Vault Secrets User' on this vault.\n"
            "  - 404/NotFound:  the secret has not been seeded yet — run seed-keyvault-secrets.sh.\n"
        )
        return 1

    if not secret.value:
        sys.stderr.write(f"FAIL: secret '{args.secret}' exists but value is empty.\n")
        return 1

    # Mask real values completely — even printing the length can fingerprint
    # well-known credential formats. Placeholders are public-knowledge so the
    # length is fine to surface (and confirms the test actually read something).
    if secret.value == "REPLACE-ME":
        print(f"OK   Read '{args.secret}' = REPLACE-ME (placeholder; length={len(secret.value)})")
    else:
        print(f"OK   Read '{args.secret}' = *** (non-placeholder value, masked)")
    print("PASS Key Vault + Managed Identity round-trip works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
