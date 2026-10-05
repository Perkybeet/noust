# The Noust central as a container: the console and the SSH tunnels to every
# server it manages. It deploys nothing where it runs: no nginx, no systemd,
# no certbot (it is a hub: central.role = hub). Meant for a NAS (UGREEN UGOS
# Pro, Synology, TrueNAS) and any Docker host. docs/CENTRAL.md is the guide.
#
# Build context: this directory, with the noust wheel of the same version
# under dist/. The Release workflow builds the wheel from the tag and then
# this image for linux/amd64 and linux/arm64:
#
#   python -m build --wheel --outdir packaging/container/dist
#   docker build -t noust packaging/container
#
# Installing the wheel built from the tag, not `pip install noust` from PyPI,
# means the image is exactly the release and does not depend on PyPI having
# published it first.

FROM python:3.12-slim-bookworm

# scripts/release.py keeps this in step with pyproject.toml.
ARG NOUST_VERSION=3.3.1

LABEL org.opencontainers.image.title="Noust central" \
      org.opencontainers.image.description="Noust's console and fleet tunnels, for a NAS or any Docker host" \
      org.opencontainers.image.version="${NOUST_VERSION}" \
      org.opencontainers.image.source="https://github.com/Perkybeet/noust" \
      org.opencontainers.image.url="https://github.com/Perkybeet/noust" \
      org.opencontainers.image.documentation="https://github.com/Perkybeet/noust/blob/main/docs/CENTRAL.md" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.vendor="Perkybeet"

# tini is PID 1: it reaps the SSH tunnels the central keeps open and forwards
# SIGTERM, so `docker stop` is a clean shutdown. openssh-client opens the
# tunnels; openssl mints the console's self-signed certificate and seals the
# secrets; git is what noust's source handling calls.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini openssh-client openssl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY dist/noust-${NOUST_VERSION}-py3-none-any.whl /tmp/
RUN pip install --no-cache-dir --no-compile "/tmp/noust-${NOUST_VERSION}-py3-none-any.whl[web]" \
    && rm /tmp/noust-*.whl

# An unprivileged user owns everything the central keeps. Nothing it does
# needs root: it listens on an unprivileged port and only dials out.
RUN groupadd --system --gid 10001 noust \
    && useradd --system --uid 10001 --gid noust --home-dir /data --shell /usr/sbin/nologin noust \
    && install -d -o noust -g noust -m 0700 /data

# Everything the central keeps lives under /data (NOUST_DATA_DIR):
#   config/   config.yaml, the console's signing key, token hash, sessions,
#             two-factor state, audit log and its TLS pair (panel-tls/)
#   state/    the store (servers, audit), secrets/ (node keys and tokens,
#             sealed if you chose to), known_hosts
#   backups/  log/
# HOME is /data too, so anything that reads ~ lands on the volume. The image
# is a hub: it refuses local deployments with a message instead of failing
# on a missing nginx.
ENV HOME=/data \
    NOUST_DATA_DIR=/data \
    NOUST_CENTRAL_ROLE=hub \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER noust
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8443

# /health answers without a token. The certificate is self-signed unless the
# operator mounts one, so the probe does not verify it: it asks whether the
# console answers, not who it is.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python3", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"]

# `docker run IMAGE` runs the central; `docker exec -it noust noust ...`
# runs any other command against the same /data (token, 2fa, central unlock).
ENTRYPOINT ["/usr/bin/tini", "--", "noust"]
CMD ["central", "run"]
