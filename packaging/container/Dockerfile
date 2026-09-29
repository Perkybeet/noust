# The Noust central as a container: the console and, from the fleet release
# on, the SSH tunnels to every node. It deploys nothing where it runs: no
# nginx, no systemd, no certbot. Meant for a NAS (UGOS Pro and any Docker).
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
ARG NOUST_VERSION=2.3.0

LABEL org.opencontainers.image.title="Noust central" \
      org.opencontainers.image.description="Noust's console and fleet tunnels, for a NAS or any Docker host" \
      org.opencontainers.image.version="${NOUST_VERSION}" \
      org.opencontainers.image.source="https://github.com/Perkybeet/noust" \
      org.opencontainers.image.url="https://github.com/Perkybeet/noust" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.vendor="Perkybeet"

# tini reaps the SSH tunnels the central keeps open and forwards signals, so
# `docker stop` is a clean shutdown. openssh-client opens the tunnels; openssl
# mints the console's self-signed certificate; git is what noust's source
# handling calls.
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

COPY --chmod=0755 entrypoint.sh /usr/local/bin/noust-central

# Everything the central keeps lives under /data: the store, the console's
# signing key and tokens, its TLS pair, the SSH keys and known_hosts. HOME is
# /data so that noust's per-user paths (~/.local/share, ~/.ssh) land there too.
ENV HOME=/data \
    NOUST_DATA_DIR=/data \
    NOUST_WEB_STATE_DIR=/data/console \
    WASM_WEB_STATE_DIR=/data/console \
    PYTHONUNBUFFERED=1

USER noust
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8443

# /health answers without a token. The certificate is self-signed unless the
# operator mounts one, so the probe does not verify it: it asks whether the
# console answers, not who it is.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python3", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/noust-central"]
