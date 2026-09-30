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

The console's own **Fleet** page (a central's, never a plain server's) is every server it
manages side by side - whether it answers, what it runs, what needs attention on each - and a
server selector in the top bar switches the whole console to one of them; see
[console.md](console.md#fleet) for its pages.

![Fleet](assets/console/fleet.png)

What the central cannot do matters as much. It reaches each server as **`noust-tunnel`**, an
account `noust fleet authorize` creates for it there: a system account with no home, `nologin`
as its shell and `*` as its password (no password ever matches it). Its keys live in a file of
root's, `/etc/ssh/noust/noust-tunnel.keys`, which it cannot edit, and sshd itself restricts it
with a block Noust writes to `/etc/ssh/sshd_config.d/00-noust-tunnel.conf` (or, when sshd
includes no such directory, at the end of `/etc/ssh/sshd_config`, between marker lines):

```
Match User noust-tunnel
    AuthorizedKeysFile /etc/ssh/noust/%u.keys
    PubkeyAuthentication yes
    AuthenticationMethods publickey
    DisableForwarding no
    AllowTcpForwarding local
    AllowStreamLocalForwarding no
    PermitOpen 127.0.0.1:<console port>
    PermitListen none
    GatewayPorts no
    PermitTunnel no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PermitUserRC no
    ForceCommand /usr/bin/false
```

The key line says the same thing again, so a line copied without its options is still
contained:

```
restrict,port-forwarding,permitopen="127.0.0.1:<console port>",permitlisten="127.0.0.1:1",command="/usr/bin/false" ssh-ed25519 AAAA... noust-central:<central>
```

The central's key can open `ssh -L` to the console's port on loopback and nothing else: no
terminal, no command, no agent, no remote forward, no Unix-socket forward, no other port.
Before sshd is reloaded the change is checked with `sshd -t`, and afterwards
`sshd -T -C user=noust-tunnel,...` must show every one of these settings in force; if another
file or `Match` block wins, the change is put back and `authorize` says which setting differs.
This works on servers with `PermitRootLogin no`, and even with `DisableForwarding yes` for
everyone else. It needs OpenSSH 7.8 or later on the server (Debian 12, Ubuntu 22.04 and later,
Fedora and openSUSE Leap 15 all ship newer).

**Servers enrolled by 3.0.** Up to 3.0 the key went into **root's** `authorized_keys`, and
this page said it could only forward one port. That was not true: `permitlisten` limits TCP
listeners only, so the same key could ask for a remote forward to a *path*
(`ssh -R /etc/nologin:...`) and sshd created that Unix socket **as root**, anywhere - including
`/etc/nologin`, which stops every other account from logging in. Move each such server to the
tunnel account (see [Moving a 3.0 server to the tunnel account](#moving-a-30-server-to-the-tunnel-account)).
`noust node list` flags the ones still reached as root.

The central never gets a shell on a server, each server decides how far the central may go
there ([the access ceiling](#how-far-a-central-may-go-on-a-server)), and each server can revoke
it on its own.

The central's tunnels also pin their algorithms: ed25519 host keys only (the one the join
code carries), `sntrup761x25519-sha512@openssh.com` (post-quantum hybrid) or
`curve25519-sha256` for the key exchange, `chacha20-poly1305@openssh.com` or
`aes256-gcm@openssh.com` as ciphers, and encrypt-then-MAC MACs only. The central asks its own
`ssh -Q` once and drops what an older client lacks.

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
| `NOUST_TLS_CERT`, `NOUST_TLS_KEY` | a self-signed pair minted at the first start | Your own certificate, both on the volume. The recommended way: see [The console's certificate](#the-consoles-certificate). |
| `NOUST_TLS_NAMES` | none | Names and addresses to add to the self-signed certificate, space- or comma-separated (`central.example.com 192.168.1.50`): a container only sees its own addresses, not the NAS's. |
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

   It shows each step as it runs, then:

   - keeps `vps1`'s console on `127.0.0.1` as a service. A console you started in the
     background (`noust web start -d`) is moved to the service on the same port, after asking
     (`--yes` accepts); if the service does not come up, the background console is started
     again as it was. **Your console's access token keeps working**: `authorize` issues one
     only when the server never had one, and then prints it once, after the join code;
   - creates `noust-tunnel` and its sshd block, and installs the key for it;
   - creates a `fleet` token and prints a **join code** on one line.

   Options: `--access read|deploy|admin` and `--allow-host-access` set the ceiling (below);
   `--ssh-user root --i-understand` installs the key for root as 3.0 did, with a warning.
   Run by mistake on the central that issued the key (the key names it, or it holds that
   very key), `authorize` refuses and says so.
3. Back on the central, paste the join code:

   ```bash
   docker exec -it noust noust node add vps1 --ssh vps1.example.com --join-code -
   ```

   The account comes from the code. The central pins `vps1`'s host key from it, opens the
   tunnel, checks the token and reads the server's ceiling. From then on a changed host key
   cuts the tunnel instead of being accepted.

**Settings > Servers** lists every server the same way, with its reachability, version and
when it was last seen, and starts the same add flow (step 1 above) without the CLI:

![Settings > Servers](assets/console/settings-servers.png)

To take a server out: `noust node remove vps1` on the central, or revoke the central from the
server itself (`noust fleet deauthorize --name nas` removes its key lines, from the tunnel
account's file and from root's, and revokes its tokens). The `noust-tunnel` account and its
sshd block stay for the next central; with no key left in its file, nothing can log in as it.

## How far a central may go on a server

Each server sets a **ceiling** for every central it trusts, and enforces it itself where the
central's token is admitted, whatever the central claims for its operator:

| Ceiling | A central may |
|---------|---------------|
| `read` | read everything, change nothing |
| `deploy` | also start, stop, restart, update and roll back the applications already there, and take backups |
| `admin` (default) | everything, except the server's own accounts, security settings and audit log |

`host access` (off by default) additionally lets a central with `admin` change how the server
is reached: SSH keys, sshd, the firewall, system accounts. A compromised central is held to
the ceiling of each server; the server's own accounts, tokens and two-factor sign-in are never
a central's to change.

```bash
noust fleet access                           # on the server: the ceiling in force
noust fleet access --level read              # takes effect on the next request
noust fleet access --host-access on
```

It is set only on the server's command line (or with `noust fleet authorize --access ...
--allow-host-access`); there is no API that raises it. The server publishes it at
`GET /api/auth/fleet/self`, and the central records it when it adds or tests the server
(`noust node show` and `noust node list` show it) to grey out what the server would refuse. A
3.0 server publishes nothing and shows as `unknown`.

## Rotating a server's key

```bash
noust node rekey vps1              # on the central: a new key pair, and the command for vps1
# on vps1, as root: the printed 'noust fleet authorize ...' (replaces the old key's line)
noust node rekey vps1 --join-code -   # on the central: paste the new code
```

The central switches to the new key and token only once `vps1` answers with them; otherwise
it keeps the old ones and the same code can be pasted again. Running `noust node rekey vps1`
again before finishing prints the same command.

## Moving a 3.0 server to the tunnel account

Upgrade Noust on the server to 3.1 first, then:

```bash
noust node migrate-tunnel vps1              # on the central: the command for vps1
# on vps1, as root: the printed 'noust fleet authorize ...'
noust node migrate-tunnel vps1 --join-code -   # on the central: paste the new code
```

On `vps1`, `authorize` creates `noust-tunnel`, installs the same key for it and takes the
central's line out of root's `authorized_keys`, then issues a new token (revoking the old
one). The central switches to the new account once `vps1` answers as it, and puts back what it
had if it does not; the code can be pasted again. After that the server no longer needs
`PermitRootLogin` for the fleet.

## The console's certificate

The central holds the keys of the whole fleet, so its console is served over TLS with a
certificate your browser really trusts. The way to get that is your own: a certificate from your
organisation's CA, or from Let's Encrypt for a name of yours, with `NOUST_TLS_CERT` and
`NOUST_TLS_KEY` (or `--tls-cert`/`--tls-key` outside the container). Chrome and Firefox also
refuse passkeys on a certificate they do not trust, so this is the setup they need.

Without one the first start mints a self-signed certificate: ECDSA P-256, valid 825 days, with
`subjectAltName` for the host name, the machine's FQDN, `localhost`, the addresses of its
interfaces and whatever `NOUST_TLS_NAMES` adds (every current browser ignores the common name,
so a certificate without names could not be trusted by any means). Its fingerprint and expiry
are in `noust central status` and in the first lines of `docker logs`. A pair from an earlier
version (RSA, no names) is replaced once, on the first start of 3.1, and the browser asks about
the new fingerprint once. A self-signed certificate that expires is replaced at the next start.

The console accepts TLS 1.2 at the least, and of the TLS 1.2 suites only ECDHE key exchange with
AES-GCM or ChaCha20-Poly1305. `web.allowed_hosts` (a list of names, `*.example.com` allowed)
makes it answer only the names you give, plus loopback and the host of `web.public_url`; empty
answers any host. Neither a NAS in a cupboard nor a container on a home network is where a
company's central should live: put it on the company's infrastructure, in a management network,
with `NOUST_ALLOW_IP` naming who may open it.

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

The console's form (`POST /api/central/unlock`) asks for a signed-in admin in sudo mode
("Confirm it's you") as well as the passphrase, so an unattended browser tab is not enough.
A wrong passphrase counts towards the same lockout as a wrong token (five in a row lock
that address out for fifteen minutes), and every attempt is in the audit log, without the
passphrase. While locked, anything that needs a node answers `423 central_locked`.

Once unlocked, ssh is handed a decrypted copy of a node's key and pinned host key in a
private, memory-backed directory (`$XDG_RUNTIME_DIR`, else the temporary directory) and the
copy is deleted as soon as the tunnel is up; any left over go when the central stops or is
locked. New node keys on a sealed central are generated there and sealed at once.

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
- **A server shows as refused**: it no longer accepts this central's token (revoked or
  deauthorized there). The central stops presenting it, so the server's audit log does not
  fill up and its loopback address is never locked out. Authorize the central on the server
  again, then `noust node test <name>` (or Test in the console) to resume, or remove and add
  it with the new join code.
- **A command says it is "not available on this central, which is a hub"**: it deploys on the
  machine it runs on; run it on the server instead.
- **"sshd would refuse the central's tunnel"** on `authorize`: the reasons are listed, one per
  line. The usual one is `AllowUsers` or `AllowGroups` without `noust-tunnel`: add it, reload
  sshd, and authorize again. `sshd -T -C user=noust-tunnel,host=localhost,addr=127.0.0.1` shows
  what sshd applies to the account.
- **"sshd does not apply Noust's restrictions to noust-tunnel"**: an earlier file in
  `/etc/ssh/sshd_config.d/` or a `Match` block in `sshd_config` sets one of the settings first.
  The message names each one; nothing was changed.
- **The tunnel to a server is refused after many failed attempts**: fail2ban on the server may
  have banned the central's address. Add it to `ignoreip` there when it is fixed.
- **"This server is the central that issued this key"**: `noust fleet authorize` was run on
  the central (or in its container) instead of on the server to add. Run it on the server.
