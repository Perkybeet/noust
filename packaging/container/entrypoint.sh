#!/bin/sh
# Start the Noust central in the foreground.
#
# Today this runs the console, over TLS, on every interface of the container.
# When the fleet release adds `noust central run`, this script becomes a single
# exec of it (it will mint the certificate and open the tunnels itself); the
# image, the volume, the port and the health check stay as they are.
#
# Environment:
#   NOUST_TLS_CERT, NOUST_TLS_KEY  a certificate the operator mounts, instead
#                                  of the self-signed one minted under
#                                  /data/tls on the first start.
#   NOUST_ALLOW_IP                 space-separated addresses or CIDRs allowed
#                                  to connect; the private ranges by default,
#                                  so the console answers the LAN and nothing
#                                  else even if a port is forwarded to it.
set -eu

data=${NOUST_DATA_DIR:-/data}
cert=${NOUST_TLS_CERT:-}
key=${NOUST_TLS_KEY:-}

if [ ! -w "$data" ]; then
    echo "noust-central: $data is not writable by uid $(id -u)." >&2
    echo "A bind-mounted directory must belong to it: chown -R $(id -u):$(id -g) <the host directory>" >&2
    exit 1
fi

if [ -z "$cert" ] || [ -z "$key" ]; then
    cert="$data/tls/console.crt"
    key="$data/tls/console.key"
    if [ ! -s "$cert" ] || [ ! -s "$key" ]; then
        umask 077
        mkdir -p "$data/tls"
        name=$(cat /proc/sys/kernel/hostname)
        openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
            -days 825 -subj "/CN=${name}" \
            -addext "subjectAltName=DNS:${name},DNS:localhost,IP:127.0.0.1" \
            -keyout "$key" -out "$cert" 2>/dev/null
        echo "noust-central: minted a self-signed certificate for ${name} under $data/tls"
    fi
fi

allow=${NOUST_ALLOW_IP:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8 fc00::/7 ::1/128}
set --
for entry in $allow; do
    set -- "$@" --allow-ip "$entry"
done

exec noust web start --host 0.0.0.0 --port 8443 \
    --tls-cert "$cert" --tls-key "$key" "$@"
