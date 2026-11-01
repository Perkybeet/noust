#!/usr/bin/env sh
set -e 

if [ -z "$${USERNAME}" ] || [ -z "$${PASSWORD}" ]; then
    echo "Error: USERNAME and PASSWORD environment variables must be set"
    exit 1
fi

apk add --no-cache apache2-utils
mkdir -p "$$(dirname "$${REGISTRY_AUTH_HTPASSWD_PATH}")"
chmod 755 "$$(dirname "$${REGISTRY_AUTH_HTPASSWD_PATH}")"
htpasswd -Bbc "$${REGISTRY_AUTH_HTPASSWD_PATH}" "$${USERNAME}" "$${PASSWORD}"
registry serve /etc/docker/registry/config.yml
