#!/bin/sh
# Starts the DNS side of the certificate scenarios: BIND for acme.test with a TSIG key it makes
# now (a different one every run), a certificate authority of its own for the stand-in's
# api.cloudflare.com, and the stand-in. The scenario reads the key and the authority's
# certificate with `docker exec`; nothing here is published on the host.
set -eu

ZONE=acme.test
KEY=/etc/bind/noust.key
CA_DIR=/srv/ca

tsig-keygen -a hmac-sha256 noust-key > "$KEY"
chown root:bind "$KEY"
chmod 0640 "$KEY"

cat > /etc/bind/named.conf <<EOF
include "$KEY";
options {
    directory "/var/cache/bind";
    listen-on port 53 { any; };
    listen-on-v6 { none; };
    allow-query { any; };
    recursion no;
    dnssec-validation no;
    minimal-responses no;
};
zone "$ZONE" {
    type primary;
    file "/var/cache/bind/$ZONE.zone";
    allow-update { key noust-key; };
};
EOF

cat > "/var/cache/bind/$ZONE.zone" <<EOF
\$TTL 60
@   IN SOA ns1.$ZONE. hostmaster.$ZONE. 1 60 60 3600 60
@   IN NS  ns1.$ZONE.
ns1 IN A   $(hostname -i | awk '{print $1}')
EOF
chown bind:bind "/var/cache/bind/$ZONE.zone"

# The stand-in speaks HTTPS at api.cloudflare.com, so the machines that use it trust this
# authority and nothing else of ours.
mkdir -p "$CA_DIR"
# Python 3.13 verifies strictly: a CA needs basicConstraints and keyUsage, a leaf its key
# identifiers, or "CA cert does not include key usage extension".
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=noust test DNS provider CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.pem" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=api.cloudflare.com" \
    -keyout "$CA_DIR/server.key" -out "$CA_DIR/server.csr" 2>/dev/null
printf 'subjectAltName=DNS:api.cloudflare.com\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature\nauthorityKeyIdentifier=keyid\nbasicConstraints=CA:FALSE\n' > "$CA_DIR/ext.cnf"
openssl x509 -req -in "$CA_DIR/server.csr" -CA "$CA_DIR/ca.pem" -CAkey "$CA_DIR/ca.key" \
    -CAcreateserial -days 2 -extfile "$CA_DIR/ext.cnf" -out "$CA_DIR/server.pem" 2>/dev/null

named -u bind -f &

# Pebble reads its configuration from where the harness puts it (the document is the harness's, not
# the image's) and asks the BIND above; its relative paths are the ones of the image's root.
(
    while [ ! -s /pebble-config.json ]; do sleep 0.5; done
    cd /
    exec pebble -config /pebble-config.json -dnsserver 127.0.0.1:53
) &

exec python3 /opt/provider.py --cert "$CA_DIR/server.pem" --key "$CA_DIR/server.key" \
    --zone "$ZONE" --tsig-key "$KEY"
