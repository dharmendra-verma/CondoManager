# CondoManager Python base image — CM-20
# Pinned to python:3.12-slim per CM-20 AC #2. The weekly cron in
# .github/workflows/base-image.yml rebuilds this image so the apt
# security patches stay current with upstream Docker Hub.
#
# Downstream Dockerfiles should:
#   FROM acrcondomanager<env>.azurecr.io/base/python:3.12-slim-latest
# and set their own CMD/ENTRYPOINT. WORKDIR + non-root user are inherited.

FROM python:3.12-slim

# Apply upstream security patches and install ca-certificates (needed for
# DefaultAzureCredential's HTTPS calls to AAD + KV). --no-install-recommends
# keeps the layer thin; rm -rf /var/lib/apt/lists/* keeps the cache out of
# the published image.
RUN apt-get update \
 && apt-get -y upgrade \
 && apt-get -y install --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. Downstream Dockerfiles inherit `USER app` and should
# not switch back to root unless absolutely necessary.
RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --create-home --home-dir /home/app app

WORKDIR /app
USER app

# No CMD/ENTRYPOINT — this is a base image; downstream images set their own.
