# The Noust central

A **central** is a Noust whose job is to manage your other servers. It opens an SSH tunnel to
each of them and drives them through their own API, from one console. It can be:

- **a container on a NAS or any Docker host** (the recommended home for it: it only dials
  out, so nothing is opened on your router), or
- **a VPS**, either one that only manages the fleet or a server that also deploys its own
  applications.

A central in a container is a **hub** (`central.role = hub`): it deploys nothing itself.
Applications, sites, certificates, databases and the rest are refused there with a message,
and the console hides them. Everything else (the console, tokens, two-factor, the fleet)
works as on any Noust.

What the central cannot do matters as much: its key on each server can only forward that
server's console port (`restrict,port-forwarding,permitopen=...,command="/usr/bin/false"`).
It never gets a shell on a server, and each server can revoke it on its own.

## Install on a UGREEN NAS (UGOS Pro)

1. Open **App Center** and install **Docker** if it is not there.
2. Open **Docker > Project > Create**. Name it `noust`, keep the suggested path, and paste
   [`packaging/container/compose.yaml`](../packaging/container/compose.yaml) as the compose
   file. Press **Deploy**.
3. Open **Container > noust > Log** and copy the line `Access Token: noust_...`. It is shown
   on the first start **only**.
4. Browse to `https://<the NAS address>:8443`. The certificate is self-signed, so the browser
   warns once; the log shows its SHA-256 fingerprint to compare with the one the browser
   shows. Paste the token.

The same with SSH on the NAS, in a folder holding `compose.yaml`:

```bash
sudo docker compose up -d
sudo docker logs noust          # the token and the certificate's fingerprint
```

To keep the data in a folder of the NAS instead of a Docker volume (easier to back up with
the NAS's own tools), create it for the container's user first and switch the `volumes:`
line in `compose.yaml`:

```bash
sudo mkdir -p /volume1/docker/noust
sudo chown 10001:10001 /volume1/docker/noust
```

## Install on any Docker host

```bash
docker volume create noust-data
docker run -d --name noust --restart unless-stopped \
  -p 8443:8443 -v noust-data:/data \
  --read-only --tmpfs /tmp:size=16m --cap-drop ALL \
  --security-opt no-new-privileges:true \
  ghcr.io/perkybeet/noust:latest
docker logs noust
```

The image runs as uid 10001 with a read-only root filesystem and no capabilities; everything
it keeps is under `/data`.

| Variable | Default | What it does |
|----------|---------|--------------|
| `NOUST_ALLOW_IP` | loopback, `10/8`, `172.16/12`, `192.168/16`, `fc00::/7` | Who may open the console, space- or comma-separated. Wins over `web.ip_whitelist`. |
| `NOUST_TLS_CERT`, `NOUST_TLS_KEY` | a self-signed pair minted at the first start | Your own certificate, both on the volume. |
| `NOUST_DATA_DIR` | `/data` | Where everything lives. |
| `NOUST_CENTRAL_ROLE` | `hub` | Set by the image; a container cannot deploy. |

## On a VPS

On a VPS, the central is a normal Noust installed from the packages; there is no container
and no `noust central run`.

- **A VPS that also deploys applications** stays `central.role = server` (the default). It
  manages the fleet from the same console it already has: nothing to change.
- **A VPS that only manages the fleet**: install Noust, then

  ```bash
  noust config set central.role hub
  noust web enable --host 0.0.0.0 --port 8443 --self-signed --allow-ip <your address or VPN range>
  ```

  or keep the console on loopback behind your own reverse proxy (`noust web enable` and
  `--trusted-proxy`). A central reachable from the internet is the one thing to avoid: prefer
  a VPN or an SSH tunnel to it (`ssh -L 8443:127.0.0.1:8443 root@central`).

`noust central run` works on a VPS too (as root, or with `NOUST_DATA_DIR` pointing at a
directory its user owns), but `noust web enable` is the supported way to run a console as a
service there.

## First sign-in and two-factor

1. Sign in with the token from the first start.
2. Turn on two-factor authentication in the console's settings (or
   `docker exec -it noust noust 2fa enroll`, then `noust 2fa confirm CODE`). **A central
   refuses to add its first server without it.**
3. Lost the token? `docker exec -it noust noust web token --new` issues another and retires
   the old one.

`docker exec -it noust noust central status` shows the role, where the data is, the
certificate's fingerprint, who may connect, whether the secrets are sealed, how many
servers there are and whether two-factor is on.

## Adding a server

The central never logs in to a server with your credentials. You authorize it **on the
server**, where you are already root:

1. On the central: `docker exec -it noust noust node key vps1` (or **Settings > Servers >
   Add** in the console). It prints the central's key for `vps1` and a command.
2. On `vps1`, as root, run the printed command:

   ```bash
   noust fleet authorize --central-key 'ssh-ed25519 AAAA... noust-central@nas' --name nas
   ```

   It installs the key restricted to forwarding the console port, keeps `vps1`'s console on
   `127.0.0.1` as a service, creates a `fleet` token and prints a **join code** on one line.
3. Back on the central, paste the join code:

   ```bash
   docker exec -it noust noust node add vps1 --ssh root@vps1.example.com --join-code -
   ```

   The central pins `vps1`'s host key from the code, opens the tunnel and checks the token.
   From then on a changed host key cuts the tunnel instead of being accepted.

To take a server out: `noust node remove vps1` on the central, or revoke the central from the
server itself (`noust fleet deauthorize --name nas` removes its key and revokes its tokens).

## Sealing the secrets (recommended on a NAS)

A central holds the keys to every server it manages. Sealing encrypts them at rest (node keys
and tokens, integration keys: every file under `/data/state/secrets`) under a passphrase
that is never written anywhere:

```bash
docker exec -it noust noust central seal       # asks for the passphrase twice
```

Key derivation is scrypt (n=2^15, r=8, p=1) over a random salt; each file is encrypted with
AES-256-CBC by `openssl` and authenticated with HMAC-SHA256 (encrypt-then-MAC, the secret's
name bound in, so a file altered or moved over another is refused). The key reaches openssl
through its environment, never its command line.

After every restart a sealed central is **locked**: the console works and says why, but no
tunnel opens until you unlock it, either with the **Unlock** form in the console or:

```bash
docker exec -it noust noust central unlock
printf '%s\n' "$PASSPHRASE" | docker exec -i noust noust central unlock   # from a script
```

`noust central unseal` stores them in clear again; unseal and seal to change the passphrase.

**Nobody can recover a lost passphrase**, Noust included. Without it the sealed secrets are
gone and every server must be authorized again. Keep it in your password manager, not on the
NAS.

What is not sealed, because the console needs it to let you in to unlock: the console's
signing key, the token hash, the sessions and the two-factor secret (under `/data/config`).

## Backups

Back up the whole volume. It holds:

| Path | What |
|------|------|
| `/data/config/` | `config.yaml`, the console's signing key, token hash, sessions, two-factor state, audit log, and its TLS pair (`panel-tls/`) |
| `/data/state/noust.db` | the store: servers, their pinned host keys, history |
| `/data/state/secrets/` | node keys and tokens (sealed or not) and the seal header `.seal` |
| `/data/backups/`, `/data/log/` | the central's own backups and logs |

Stop the container for a consistent copy (`docker stop noust`), or copy it running and accept
that the last minute may be missing. A sealed backup is only as useful as the passphrase you
still have: without it, restore gives you a central that cannot reach any server.

With a named volume:

```bash
docker run --rm -v noust-data:/data -v "$PWD":/backup debian:bookworm-slim \
  tar czf /backup/noust-data.tgz -C /data .
```

## Updating

```bash
docker compose pull && docker compose up -d        # on UGOS Pro: stop the project, pull the image again, start it
```

The data stays on the volume; the new image reads it where the old one left it. The first
start of a new version prints no token (one was already issued). Pin a version in
`compose.yaml` (`ghcr.io/perkybeet/noust:3.0.0`) to update when you choose, or use `latest`.

## Troubleshooting

- **"The data directory /data is not writable by uid 10001"**: a bind-mounted folder belongs
  to someone else. `chown -R 10001:10001` it on the host.
- **The browser cannot reach the console**: check the NAS firewall allows 8443 from your LAN,
  and that your address is inside the allowed networks (`noust central status`). Docker on
  some systems presents clients with the bridge's address (`172.17.0.1`), which the defaults
  already allow.
- **"The secrets are sealed and locked"**: unlock them (above).
- **A command says it is "not available on this central, which is a hub"**: it deploys on the
  machine it runs on; run it on the server instead.
