"""Azure Functions Timer trigger — Google Drive → Cosmos vector sync (CM-34).

Jira: CM-34  | Epic: CM-Epic 9  | Phase 1

A thin shell over ``agents.knowledge.run_sync``: it sets up structured
logging + a correlation id, wires the real Drive / Cosmos / embedder
implementations from environment config, and runs one sync pass every
30 minutes (``schedule`` below).

All business logic + tests live in ``agents/knowledge/`` — this file is
deploy-time glue and is intentionally kept trivial. The ``agents`` package
is bundled into the deployment (see ``.funcignore`` / ``requirements.txt``).

Environment (set as Function App settings / Key Vault references by
``infra/bicep/modules/functions.bicep``):

* ``COSMOS_ENDPOINT``                    Cosmos account endpoint
* ``GOOGLE_DRIVE_SA_KEY``                service-account JSON (KV)
* ``GDRIVE_FOLDER_ID``                   Drive folder to watch
* ``GDRIVE_TENANT_ID``                   tenant the docs belong to
* ``AZURE_OPENAI_ENDPOINT`` / ``AZURE_OPENAI_API_KEY`` (KV) / …
* ``ENVIRONMENT``                        ``dev`` | ``prod`` (log tagging)
"""

from __future__ import annotations

import logging
import os

import azure.functions as func
from agents.knowledge import default_embedder, get_vector_store, run_sync
from agents.knowledge.gdrive_client import GoogleDriveClient
from agents.observability import configure_logging, with_request_id, with_tenant

app = func.FunctionApp()

_log = logging.getLogger("gdrive_sync.function")

# Every 30 minutes, on the hour and half-hour (NCRONTAB: sec min hour …).
SYNC_SCHEDULE = "0 */30 * * * *"


@app.function_name(name="gdrive_sync")
@app.timer_trigger(
    arg_name="timer",
    schedule=SYNC_SCHEDULE,
    run_on_startup=False,
    use_monitor=True,
)
def gdrive_sync(timer: func.TimerRequest) -> None:
    """Run one Drive→Cosmos sync pass. Logs and skips cleanly if unconfigured."""
    environment = os.environ.get("ENVIRONMENT", "dev")
    configure_logging(service_name="gdrive-sync", environment=environment)

    tenant_id = os.environ.get("GDRIVE_TENANT_ID", "default")
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()

    store = get_vector_store()
    drive = GoogleDriveClient.from_env()
    embedder = default_embedder()

    missing = [
        name
        for name, value in (
            ("COSMOS_ENDPOINT", store),
            ("GOOGLE_DRIVE_SA_KEY", drive),
            ("AZURE_OPENAI_*", embedder),
            ("GDRIVE_FOLDER_ID", folder_id or None),
        )
        if value is None
    ]
    if missing:
        # Not an error — the app is provisioned before operators seed the
        # KV secrets / app settings. Surfaced once per tick for visibility.
        _log.warning(
            "gdrive_sync skipped — unconfigured: %s", ", ".join(missing)
        )
        return

    # mypy/readers: the guard above proves these are non-None.
    assert store is not None and drive is not None and embedder is not None

    with with_request_id(), with_tenant(tenant_id):
        report = run_sync(
            tenant_id=tenant_id,
            source_folder_id=folder_id,
            drive=drive,
            store=store,
            embedder=embedder,
        )
        _log.info(
            "gdrive_sync complete changed=%d skipped=%d removed=%d failed=%d",
            report.changed,
            report.skipped,
            report.removed,
            report.failed,
        )
