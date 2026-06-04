# CondoManager agent-runtime image — CM-59
#
# Packages the Python agent runtime (LangGraph orchestrator + channel adapters)
# and serves the CM-55 web-chat FastAPI app as the prod inbound entry point.
# This image replaces the mcr.microsoft.com/azuredocs/containerapps-helloworld
# placeholder on the `ca-hello-condomanager-prod` Container App.
#
# Build (server-side, no local Docker needed — see .github/workflows/deploy.yml):
#   az acr build --registry acrcondomanagerprod \
#     --image agent:<git-sha> --image agent:latest \
#     --file infra/docker/agent/Dockerfile .
#
# NOTE on the base image: CM-20's curated base (acrcondomanager<env>.azurecr.io/
# base/python:3.12-slim-latest) is NOT yet published to ACR (base-image.yml has
# never pushed), so we FROM the upstream python:3.12-slim directly — the same
# pin CM-20's base uses. Switch the FROM line to the ACR base once it exists; the
# apt-upgrade + non-root-user setup below mirrors that base so the swap is a
# one-line change.
FROM python:3.12-slim

# Upstream security patches + ca-certificates (DefaultAzureCredential makes HTTPS
# calls to AAD / Key Vault / Cosmos). --no-install-recommends keeps the layer
# thin; clearing the apt cache keeps it out of the published image.
RUN apt-get update \
 && apt-get -y upgrade \
 && apt-get -y install --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root runtime user (uid/gid 1001), matching the CM-20 base-image convention.
RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --create-home --home-dir /home/app app

WORKDIR /app

# Dependencies first so the layer caches across source-only changes. The
# committed lockfile pins every runtime dependency for reproducible builds.
# uvicorn is the ASGI server — a DEPLOY-time dependency intentionally kept out of
# pyproject's runtime set / requirements-lock.txt (the library code never imports
# it), so it is installed explicitly here.
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt \
 && pip install --no-cache-dir "uvicorn[standard]>=0.30,<1"

# Application source. Only the agent package is needed at runtime (tests, infra,
# and the portal are excluded via .dockerignore).
COPY agents/ ./agents/

# PYTHONUNBUFFERED so logs stream to the Container App console without buffering;
# PORT is the ingress targetPort the Container App routes to (see container-app.bicep).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

USER app
EXPOSE 8000

# Serve the CM-55 web-chat FastAPI app. The endpoints 404 unless
# WEBCHAT_TEST_ENABLED is set — the Container App supplies that env var in prod
# (container-app.bicep), so the channel is live in prod and "not deployed"
# everywhere the flag is absent. /healthz is always 200 (ungated) for the
# ingress TCP/HTTP probe + the prod smoke test.
CMD ["sh", "-c", "exec uvicorn agents.webchat.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
