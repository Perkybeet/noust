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
  A hub has no nginx sites of its own, so `noust console expose` (a name, a certificate and an
  allow-list through Noust's nginx, [docs/console.md](console.md#reaching-it)) is for a central
  that also deploys, `central.role = server`; a hub serves its own TLS, as above.

`noust central run` works on a VPS too (as root, or with `NOUST_DATA_DIR` pointing at a
directory its user owns), but `noust web enable` is the supported way to run a console as a
service there.

## First sign-in and accounts

1. Sign in with the token from the first start. A central with no accounts opens **Create the
   first account**: an administrator, and (recommended) a security officer for another person.
   Or from the command line: `docker exec -it noust noust user create alice --role admin`.
2. Sign in as that account and enrol its second factor, an authenticator app or a passkey. Every
   account must have one, and **a central refuses to add its first server without it**. From
   then on the access token is emergency access only.
3. Lost the token? `docker exec -it noust noust web token --new` issues another and retires
   the old one.

The central's accounts are the ones that matter for the fleet: operators reach every server
through the central, and each server receives the operator's name and role with every call
(`X-Noust-Actor`, `X-Noust-Actor-Role`). The server grants what its own table gives that role,
within its ceiling for the central (below). A server's own accounts, tokens and second factor are
never the central's to change.

`docker exec -it noust noust central status` shows the role, where the data is, the
certificate's fingerprint, who may connect, whether the secrets are sealed, how many
servers there are and whether two-factor is on.

## Adding a server

The central never keeps a credential of yours and never gets a shell on a server. You authorize
it **on the server**, where you are already root (below); or, when you only have an address and a
way in, the central does the same three steps once, from [a guided enrollment](#enrolling-a-server-with-an-address-and-a-credential).
Manually:

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

### Enrolling a server with an address and a credential

```bash
noust fleet enroll vps1 --host 203.0.113.9                      # asks for the password, without echo
noust fleet enroll vps1 --host 203.0.113.9 --key-file ~/.ssh/id_ed25519 --ask-passphrase
noust fleet enroll vps1 --host vps1.example.com --port 2222 --access deploy \
    --host-key-fingerprint SHA256:...                          # for a script (password on stdin)
```

Over the API it is `POST /api/fleet/enroll/host-key` (asks the address which host key
answers) and `POST /api/fleet/enroll` (a job whose log streams what happens on the server, the
installer's output included; it asks for sudo mode and `fleet.manage`); the console has no
dialog for it yet. What it does, in order, and what makes it the manual join:

1. **The host key is confirmed first.** The central shows the ed25519 fingerprint that answers
   and you compare it with the server's own (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`,
   or your provider's console). It connects with that key and no other (`StrictHostKeyChecking=yes`
   against a file holding only it), and the join code the server prints later must carry the same
   one, or nothing is registered.
2. **Noust is installed if it is missing.** The central sends *its own copy* of `install.sh`
   (the deb and rpm packages and the container carry it in `/usr/share/noust/`) over the
   connection and runs it as root with `--yes --no-console`; the server downloads no script.
   A central installed with pip has none: install Noust on the server by hand, then enroll it.
   `--no-install` refuses instead of installing. A Noust older than 3.1 is not touched.
3. **`noust fleet authorize` runs on the server**, with this central's key for it and the ceiling
   you chose (`--access read|deploy|admin`, `--allow-host-access`): the `noust-tunnel` account, the
   restricted key, the sshd block, the token. The console on that server becomes a service on
   loopback if it was not; its own access token, when authorize had to issue one, is **not**
   kept (`noust web token --new` on the server issues another).
4. **The central joins it** as `noust node add` does: it pins the host key, stores the token,
   opens the tunnel and asks the server who it is. When any step after authorize fails, the
   authorization is withdrawn (`noust fleet deauthorize`), so nothing is left half-done.

The credential is a password or a private key (with its passphrase, if it has one), used for these
few connections and kept nowhere: not in an argument list, a log line, a job's result, the audit
trail, an approval's record or a file that outlives the call. A password or a passphrase reaches ssh
through its environment and a helper script that holds no secret; a key is written to a file in a
private, memory-backed directory (`$XDG_RUNTIME_DIR`, `/run/noust` or `/dev/shm`) that is removed on
every way out. The account may be `root`, or one with passwordless `sudo` (`sudo -n`). When a
second person must approve adding servers, the approval is for everything but the credential: its
fingerprint is the call without it. Needs two-factor sign-in on this central, as `noust node add`
does; `--replace-token` lets a server that already holds a token for this central issue a new one.

### Creating a server at a cloud provider

A central can also make the machine: Hetzner Cloud, DigitalOcean and Vultr. You give it an API
token once, pick where, what size and what it starts from, and it creates the server, waits for
it, installs Noust on it and adds it to the fleet, with the log of every step streaming.

```bash
noust fleet cloud account add hetzner-prod --provider hetzner --note "production project"   # asks for the token, without echo
noust fleet cloud catalog hetzner-prod                          # locations, sizes with their monthly price, images
noust fleet cloud server create web-7 --account hetzner-prod --location fsn1 --plan cx22 \
    --image ubuntu-24.04 --script swap-2g --script unattended-security-updates
noust fleet cloud server list                                   # what this central created, and why one did not join
noust fleet cloud server delete web-7 --remove-node        # asks for the name, then destroys the machine
```

In the console it is **Settings > Cloud** (the accounts, the servers created here and the boot
scripts) and **Settings > Servers > Create at a provider**, a wizard in five steps that ends on the
job's log. Everything that spends or destroys asks for sudo mode, and a second person's approval
when your policy requires it.

- **The token is a secret of this central.** It is read without echo (or from standard input),
  never from an argument, checked with the provider before it is kept, stored in the secret store
  (sealed with the rest on a sealed central) and shown afterwards only by its last four characters.
  A provider that answers 429 is waited for; a request that creates something is never repeated
  after a network error, so a server is never made twice.
- **Nothing secret travels in the new machine's boot data**, which every local user of the machine
  can read from the provider's metadata service. The central chooses the server's SSH host key
  before the machine exists and sends it in the cloud-init document, so it pins the key it chose and
  never trusts whatever answers; it logs in once with a key pair it generates for this machine
  alone, kept only in memory and uploaded to the provider as a one-time SSH key, and then joins the
  server exactly as `noust fleet enroll` does. The one-time key is taken out of the server's
  `authorized_keys` and deleted at the provider when the server has joined. If the server does not
  join, it stays on record with the reason (billed until you delete it), unless
  `--delete-on-failure` says to delete it.
- **The boot scripts are a library** (`noust fleet cloud script`, Settings > Cloud): shell scripts
  and `#cloud-config` fragments the new server runs as root on its first boot, after Noust's own
  part. Noust's part (the SSH host key, who may log in, no password logins) cannot be overridden by
  a fragment, a script must say what runs it (`#!`), and anything that looks like a credential is
  refused. `noust fleet cloud script preview` shows the whole document a server would boot with.
  Three come with Noust: `swap-2g`, `base-packages` and `unattended-security-updates`.
- **Firewalls, networks, SSH keys and backups** of the provider's own can be chosen where the
  provider has them; the catalog says which. Noust installs on Debian 12 and 13 and Ubuntu 22.04,
  24.04 and 26.04, on x86-64 and arm64; the sizes are listed with their processor, and only the
  images that run on it.

A server you made yourself at the provider's console can be given the same standing afterwards:

```bash
noust fleet cloud server link web-2 --account hetzner-prod --id 4711   # asks the provider; changes nothing there
noust fleet cloud server unlink web-2                                  # forget it; neither the machine nor the server is touched
```

The provider is asked whether the machine exists and is at the address the server is reached on
(`--force` links it anyway, for a server that answers elsewhere). From then on `server show web-2
--live` says whether it is on, `power-on` starts it, and `delete` can destroy it, as for one this
central created. Linking and unlinking ask for sudo mode.

**Settings > Servers** lists every server the same way, with its reachability, version and
when it was last seen, and starts the same add flow (step 1 above) without the CLI:

![Settings > Servers](assets/console/settings-servers.png)

To take a server out: `noust node remove vps1` on the central, or revoke the central from the
server itself (`noust fleet deauthorize --name nas` removes its key lines, from the tunnel
account's file and from root's, and revokes its tokens). The `noust-tunnel` account and its
sshd block stay for the next central; with no key left in its file, nothing can log in as it.

## A server behind a bastion

A server in a private network is reached through a host that has an address the central can reach: a
bastion. The central asks the bastion for **one stream to the server's sshd** and speaks SSH to the
server through it, so the server's own host key is still pinned and checked end to end, and the
bastion carries bytes it cannot read or change. It is a hop, not a trust.

```bash
noust node ssh web-5 --bastion hop@bastion.example.com:22 \
    --bastion-fingerprint SHA256:...                      # the bastion's host key, from its owner
noust node ssh web-5 --show                               # the line the bastion's operator adds
noust node ssh web-5 --timeout 30                         # wait longer for a slow link (1 to 300 seconds)
noust node ssh web-5 --no-bastion                         # direct again
noust node add web-5 --ssh noust-tunnel@10.0.0.5 --join-code - \
    --bastion hop@bastion.example.com --bastion-host-key 'ssh-ed25519 AAAA...'
```

In the console it is **Fleet > Servers > the server's menu > Connection options**
(`GET` and `PUT /api/nodes/{node}/ssh`).

- **The bastion's host key is given, never learnt.** A new bastion needs `--bastion-fingerprint`
  (`SHA256:...` as `ssh-keygen -lf` prints it, checked against what the bastion answers) or
  `--bastion-host-key`; with neither, nothing is changed. It is pinned in the central's own files, and a
  change closes the tunnel and says so, as for a server.
- **The change is tried before it is kept.** With the new options the central opens the tunnel and checks
  the server answers; when it does not, the old options are put back (`--no-test` keeps them without
  trying, for a server that is down now).
- **The central gets no shell on the bastion either.** `node ssh --show` prints an `authorized_keys`
  line for the account the central logs in as there, with `restrict` and a `permitopen` for this one
  server: one stream to it, no terminal, no command, no other destination. It uses the key the central
  already holds for that server.
- **The wait for the connection is the server's own** (10 seconds by default): a LAN answers in one, a
  satellite link or a slow VPN in thirty.

## Servers behind NAT or a CGNAT

A server in a house or on a carrier's CGNAT has no SSH port the central can reach. It reaches the
central instead: it keeps one SSH connection to it open (`ssh -R`) and the central talks to its
console through the loopback port that connection leaves, with the same fleet token, from
loopback, as it does through a tunnel it opens itself. **Nothing about the model changes**: the
central never gets a shell on the server, and the server gets none on the central.

What the central needs is an SSH server that the servers can reach: a central on a VPS, or a central
on the host of a NAS with its SSH port forwarded. A central in a container has no sshd of its own,
and `noust node link setup` says so. Prepare it once, as root, on the machine whose sshd the
servers will dial:

```bash
noust node link setup                  # the account noust-link and its restrictions in sshd
noust node link status                 # ready? which servers dial in?
```

`noust-link` is an account with no home, `nologin` as its shell and a password nothing matches,
restricted in sshd itself (a Noust `Match User noust-link` block, validated with `sshd -t` and
checked with `sshd -T`, and put back if sshd applies something else): it may open a remote
forward of one loopback port and nothing else. No command, no terminal, no local forward, no
Unix socket, no agent. Each server's key is further limited to the one port that is its own
(`permitlisten="127.0.0.1:24001"`), so one server cannot take another's.

Then, for each server:

```bash
noust node link invite web-5 --host nas.example.com        # on the central: the address web-5 dials
# on web-5, as root: the command it printed
noust fleet link --name nas --as web-5 --host nas.example.com --ssh-port 22 \
    --host-key 'ssh-ed25519 AAAA...' --remote-port 24001 --invite 3f9c...
noust node link add web-5 --link-code -                    # on the central: paste what web-5 printed
```

- The command carries **the central's SSH host key, which the server pins**: it never trusts
  whatever answers the first connection. Compare the fingerprint the invitation shows with
  `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the central.
- `noust fleet link` runs `noust-link-<central>.service`, restarted whenever the connection ends
  however long the central was out of reach. It issues the fleet token (`fleet-<central>`) and
  prints a **link code**, which holds that token and the public half of the key it dials in with,
  and which the central checks against the invitation: another server's, another central's or an
  expired (24 hours) code is refused. `--access read|deploy|admin` and `--allow-host-access` set the
  ceiling, as for any server.
- `noust node link add` installs the server's key, waits for the connection (30 seconds) and checks
  the token, and keeps nothing unless the server answers; if it does not connect, the key is taken
  out again and **the same code can be pasted again**. Needs two-factor sign-in, as every way of
  adding a server does, and asks a second person when your policy says so.
- **The central sends the token only to the link account's own listener.** The port is fixed, so a
  local account that took it while the link was down would otherwise receive the token. The central
  reads who owns the listener from the kernel and refuses a port held by anyone but `noust-link`
  or root: nothing is sent, and the message names the port.
- Ports 24000 to 24999 of the central's loopback are the links' (`--port` chooses another);
  `noust node link cancel NAME` gives back an invitation nobody answered. The console does it all:
  **Settings > Servers > Add a server**, then "It cannot be reached from outside".
- A server that dials in has no SSH key of the central's to rotate (`noust node rekey` says so).
  To take it out: `noust node remove web-5` on the central, which takes its key out and forgets its
  port, and `noust fleet unlink --name nas` on the server, which stops the service, removes its key
  and revokes its tokens.

## How far a central may go on a server

Each server sets a **ceiling** for every central it trusts, and enforces it itself where the
central's token is admitted, whatever the central claims for its operator:

| Ceiling | A central may |
|---------|---------------|
| `read` | read everything, change nothing |
| `deploy` | also start, stop, restart, update and roll back the applications already there, and take backups (not delete them or change where they go) |
| `admin` (default) | everything, except the server's own accounts, security settings and audit log, and what reaches the host (below) |

`host access` (off by default) additionally lets a central with `admin` reach the host itself:
change how the server is reached (SSH keys, sshd, the firewall, system accounts) and make
root-equivalent changes (raw systemd units, cron commands, backup hooks, raw site
configuration, an application's deploy hooks, moving an application to its own account), each of
which runs as root, or with an application's secrets, and so is host access by another name. A
compromised central is held to the ceiling of each server; the server's own accounts, tokens
and two-factor sign-in are never a central's to change.

When a server requires four-eyes approvals (`approval.enabled`, or the ENS profile), a
central's call that needs one is let through only with the central's word that it was approved
there: `X-Noust-Approval` (the central's request number) and `X-Noust-Approved-By` (who
approved it). The server cannot check the central's decision, only that one was taken: it
refuses the call when either header is missing or malformed, or when the approver named is the
operator who asked, and its audit log records both people and the central's token.

Sudo mode is confirmed on the central, not on the server. When a call needs it, the central asks
its own operator and vouches for the answer with `X-Noust-Elevated`; the server accepts that and
does not apply its own `auth.sudo.*` policy to the call, whatever profile the server runs
(`ens-medium` included). The central's policy is therefore the one in force for what its
operators do through it: a central that manages servers on the `ens-medium` profile has to run
the `ens-medium` profile itself (see [ENS.md](ENS.md), section 5.2).

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

## Seeing the whole fleet

The Fleet page and `noust fleet` ask every server at once, each with its own deadline, and show
what came back: a server that is down, too old or refuses the operator is one row with its
state and its own words (ssh's, or the server's API error), never an error for the whole page.
When a server cannot be reached, its last good answer is shown with its age.

```bash
noust fleet status                       # every server: reachability, version, apps, units, certificates
noust fleet apps --status failed         # every application of every server
noust fleet certs --expiring 21          # certificates, the soonest to expire first
noust fleet backups                      # each application's newest backup, the gaps first
noust fleet updates                      # Noust and system updates pending on each server
noust fleet activity --node vps1         # what happened lately
noust fleet logs error --since 1h         # search the logs of every node at once (docs/logs.md)
noust fleet metrics --metric memory --window 7d   # one metric of every node, side by side
```

In the console, the Fleet page has the same views as tabs (Summary, Servers, Applications,
Certificates, Backups, Updates, Activity), and each row links to the page on its server. On a
hub, the Fleet summary is the home page. The server selector in the top bar switches every other
page to one server, and each page shows that server's name above its title.

## Acting on several servers

```bash
noust node label vps1 env=prod region=eu     # labels live on the central; the server never sees them
noust node label vps1 region-                # remove one
noust fleet run certs_renew --label env=prod --plan
noust fleet run apps_update --label env=prod --serial 25% --max-failures 1 --canary vps1
noust fleet run os_updates --nodes vps1,vps2 --option scope=security
noust fleet run noust_update --label env=prod --option target_version=3.4.0 --plan
noust fleet jobs                             # the fleet jobs, and each server's outcome
noust fleet retry JOB_ID                     # again, on the servers that failed or were not reached
```

Actions: `certs_renew`, `backups_run`, `backups_verify`, `apps_update`, `apps_restart`,
`noust_update`, `index_refresh` and `os_updates`. Every run shows its plan first (which servers, in which
batches, what is skipped and why) and asks before it starts. A server that cannot run the action
(too old, above its ceiling, nothing to do) is skipped with the reason. `--serial` sets how many
servers run at a time, `--canary` runs one server alone first and stops everything if it fails,
and `--max-failures` stops the rest after that many failures. Each server runs the action
through its own API, as the same operator, with the same sudo mode and approvals as a call made
there by hand. The Fleet page starts the same jobs.

System updates never reboot a server; `noust_update` updates Noust with the server's own
installation method (the package manager or pip).

Updating Noust to a version of your choosing, across the fleet (`noust_update` with
`--option target_version=3.4.0`, or "Update to this version" in the console's dialog): the plan
says each server's step ("Noust 3.3.1 -> 3.4.0"). A server already on that version is skipped, one
whose package source does not offer it yet is skipped with the reason (**"on the way"** when it is
published and its index is behind, `index_behind`), and one that ends up short of it is reported as
failed with the version it is on. `index_refresh` ("Refresh package lists", also in a server's menu
on the Fleet page's Updates tab) reads each server's repositories again so a release published since
is seen; nothing is installed. Without a target, each server takes the newest its own source offers.

A system update that includes the `noust` package restarts that server's console in the middle.
The plan says so ("Also updates Noust"), Noust is installed last (on apt and dnf), and the
central waits for the console to come back and for the job to say how it ended, instead of
counting a failure and skipping the other servers. The job runs in its own systemd unit on the
server, and the console that starts again reads how the unit ended. A server older than 3.2
marks that job interrupted; the central then waits for the server's own record of the update.
In the console, the detail of each server in a fleet job shows the job's log on that server and
what it returned (see [console.md](console.md#fleet)).

## An application on several servers

An application can run on more than one server: the same repository, deployed to each, behind a load
balancer of yours. The central keeps **a placement** for it - the record "shop.example.com runs on
web-1, web-2 and every server labelled `role=web`" - and nothing more. It holds no copy of the
application: each server keeps its own releases, variables and database, and the central asks them.
It deploys nothing itself and routes no traffic.

```bash
noust fleet placement add shop.example.com --nodes web-1,web-2 --label role=web \
    --serial 1 --canary web-1                          # who runs it, and how an update goes out
noust fleet placement list
noust fleet placement status shop.example.com          # the commit each server serves, who differs
noust fleet placement rollout shop.example.com --plan  # one commit to all, planned first
noust fleet placement rollout shop.example.com --commit 3f9c2ab --serial 25% --max-failures 1
noust fleet placement copy shop.example.com --to web-3 # put it on a server that lacks it
noust fleet placement upstreams shop.example.com --format nginx --port 443
noust fleet placement remove shop.example.com          # forget the record; no server is touched
```

In the console it is **Fleet > On several servers** (`/api/fleet/placements`, see [api.md](api.md)).

- **Status compares, and says which differs.** Each server answers for itself: the commit it serves,
  when it was deployed, whether the application runs. The commit most servers serve is the reference;
  a server is `current`, `differs`, `missing` (it does not have the application), `unreachable` or
  `unknown`, each with a word and a shape, never only a colour. A server that cannot be asked shows its
  last answer with its age.
- **A rollout ends every server on the same commit.** It is the fleet's `apps_update` with the choices
  an application on several servers needs. The first server goes alone and builds the head of the
  branch (or the commit, tag or image tag you named); **the commit it ends on is fixed for the rest** and
  is kept with the job, so a push that lands halfway reaches none of them and a retry makes the same
  choice. `--serial`, `--canary` and `--max-failures` are the fleet's (above); the placement's own
  values are the default.
- **A rollout that stops puts the servers already moved back**, unless `--no-rollback`. Each server does
  it itself, by activating its previous release behind its own health gate, which is instant. A server
  whose deployment changed the schema of a database says so and is left where it is, because Noust puts
  code back, never a database; an application that deploys in place has no previous release and says
  that. Every server's outcome is in the job (`rolled_back`, or why it was not).
- **An image tag is the way to build once.** For an application that runs a registry image
  (`--type container`), `--image-tag 1.4.2` makes every server pull that tag instead of building the
  same source N times: build and push it from your CI, then roll it out. See
  [What the fleet is not](#what-the-fleet-is-not).
- **Copy puts an application on a server that lacks it**, from the export of one that has it (the
  servers' own `export` and `import`), and brings it to the commit the source serves. The document, with
  the secrets unless `--no-secrets`, goes from one server to the other through the central's memory and
  is written nowhere: not the job's result, its log or a server's record. `--new-domain` makes the copy
  under another name, to clone an application rather than spread it.
- **The load balancer is yours.** `upstreams` writes the servers for it - an nginx `upstream` block, a
  list of `address:port`, or JSON - marking the ones that serve right now. The addresses are the ones
  each server was added with (a server that dials in has none and is left out, saying so); `--port` is
  the application's port on them.
- **Every step is the server's own API call, as the same operator**, so each server's ceiling applies
  (a `read` server is skipped with the reason), sudo mode and approvals are asked as they are for a call
  made there by hand, and a server older than the action needs is skipped as it is for any bulk action.

## A fleet on different versions

A 3.1 central drives 3.0 servers, and a 3.0 central drives 3.1 servers. A 3.0 server shows as
`unsupported` in the fleet views it does not offer, bulk actions skip it with the reason, and its
ceiling shows as `unknown`. Upgrade the central first, then each server, then move each server to
the tunnel account. See [UPGRADING-3.1.md](UPGRADING-3.1.md#upgrading-a-fleet).

A 3.2 central drives 3.1 servers the same way: the pages that call routes a 3.1 server does not
have (a site's structure and diagram, the time zone list, the timeline, hooks) say that the
server runs an older Noust, and the rest works as before. See
[UPGRADING-3.2.md](UPGRADING-3.2.md#upgrading-a-fleet).

## What the fleet is not

These are decisions, not gaps waiting for a release. Each one follows from what the fleet is: a
central that reaches independent servers through tunnels it opens outward, drives them through their
own API and holds no shell and no copy of what they run.

- **It is not a cluster.** There is no Swarm, no Kubernetes and no scheduler that decides where a
  workload runs. Docker Swarm is on its way out upstream, and Kubernetes is a different product with
  a different operator; Noust will not manage either. Each application lives on the servers you chose,
  as systemd units behind nginx or as the Compose stack the server runs, and each server stays useful
  on its own if the central is gone. Spreading an application is [a placement](#an-application-on-several-servers):
  the same commit on each server, behind a balancer of yours.
- **How that differs from the others.** Komodo is a Compose-first tool with an agent on every server
  that the core reaches; Uncloud is a multi-machine Docker orchestrator with no control plane, a
  WireGuard mesh between the machines and its own ingress. Both treat the containers as the unit and
  need Docker on every machine. Noust's fleet is built around the server instead: an application is a
  native service or a Compose stack on a server that does not need Docker for the rest, the server runs
  no agent beyond Noust itself, the central never gets a shell, and each server sets a ceiling for it.
  A mesh between servers, service discovery across machines and moving a running workload by itself
  are not part of it; a private network between your servers is yours to provide (a bastion in front
  of a server is [supported](#a-server-behind-a-bastion)).
- **It does not build on a separate server.** Builds run on the server that serves the application, as an
  unprivileged account in a sandbox, so the build and the run see the same machine and nothing has to
  move between them. A dedicated build server with a registry between it and the rest is the shape
  other platforms need because their builds are images; for an application that already ships an
  image, **build it once and roll the tag out** (`noust fleet placement rollout --image-tag`): your CI
  builds and pushes, and every server pulls the same tag. Noust does not run the registry. The
  per-server side of building (how many builds at once, how many may wait, how long one may take) is
  each server's own: `deploy.max_concurrent_builds`, `deploy.queue_limit` and `deploy.max_minutes`
  (**Settings > General**, [CONFIG.md](CONFIG.md)).
- **It does not run your CI.** A runner executes code from a repository you may not control, with
  network and often the container socket, on a machine that also serves production. Noust keeps that
  out of the fleet: your CI runs where you trust it and calls Noust to deploy ([ci.md](ci.md)) with a
  token of scope `deploy`, which can update and roll back applications that exist and nothing else.

## Notifications

A server is declared down only after it has stayed silent for two minutes (two silences at least
twenty seconds apart), is asked less often the longer it stays down (down to once every six
minutes) and is announced once; it is announced again only after it answered. **Pause a server's monitoring** for maintenance
(`noust node pause web-5 --reason "kernel update"`, `noust node resume web-5`, or **Settings >
Servers > Pause monitoring**): a paused server is not asked, not counted as down and never
announced, and the fleet still shows what it last answered, with the reason beside it.

A central sends `node_unreachable` when a server stops answering, `node_recovered` when it
answers again, and `node_host_key_changed` when a server's SSH host key changed and the tunnel
was closed, through the central's own notification channels. `server.name` names each server in
every message.

## Rotating a server's key

```bash
noust node rekey vps1              # on the central: a new key pair, and the command for vps1
# on vps1, as root: the printed 'noust fleet authorize ...' (replaces the old key's line)
noust node rekey vps1 --join-code -   # on the central: paste the new code
```

The central switches to the new key and token only once `vps1` answers with them; otherwise
it keeps the old ones and the same code can be pasted again. Running `noust node rekey vps1`
again before finishing prints the same command.

## Moving a server to another central

A server is not owned by a central: it trusts one because its own operator ran `noust fleet authorize`
there, and it can trust two at once. Moving it is three steps in this order, and the third carries over
what the first central had recorded about it:

```bash
noust node export web-5 -o web-5.inventory.json          # on the old central
# on web-5, as root: noust fleet authorize --central-key '<the new central's key>' --name new-central
noust node add web-5 --ssh web5.example.com --join-code - --inventory web-5.inventory.json   # on the new one
noust node remove web-5                                   # on the old one, when the new one works
```

The inventory is the server's labels, the applications placed on it, the machine it is at a provider,
whether its monitoring is paused and, only with `--include-ssh`, how the old central reached it (its wait
and its bastion: a path that worked from one central may not exist from the other). **It holds no
secret**: not the server's fleet token, not the old central's key for it, not a cloud account's API
token, which never leave their central. A file is checked whole before anything is written, and what
cannot be applied (a cloud account the new central lacks, a label it would refuse) is reported with the
reason. For a server already added, `noust node inventory web-5 web-5.inventory.json` applies a file
later. In the console: the server's menu in **Fleet > Servers** (**Export its inventory**, **Apply an
inventory**).

## Moving a 3.0 server to the tunnel account

Upgrade Noust on the server to 3.1.1 first (3.1.0 does not know `--replace-root-key`), then:

```bash
noust node migrate-tunnel vps1              # on the central: the command for vps1
# on vps1, as root: the printed 'noust fleet authorize ... --replace-root-key ...'
noust node migrate-tunnel vps1 --join-code -   # on the central: paste the new code
```

On `vps1`, `authorize` creates `noust-tunnel`, installs the same key for it and takes that
key out of root's `authorized_keys`, then issues a new token (revoking the old one). The key is
matched by the key itself (`--replace-root-key`), not by the central's name in the line's
comment: a 3.0 line can name the central otherwise (`noust-central:arennalabs`), and 3.1.0,
which matched by name, left such lines in root's file. Every other line is kept, and a copy of
the file as it was is left next to it (`authorized_keys.noust-<UTC time>`, mode 0600).
`authorize` reports the file under "Old line removed from" (`moved_from` in `--json`) and the
copy under "Copy of it before" (`root_key_backup`).

The central switches to the new account once `vps1` answers as it, and puts back what it had if
it does not; the code can be pasted again. After that the server no longer needs
`PermitRootLogin` for the fleet.

Whatever its options, `authorize` then warns about every line still in root's
`authorized_keys` that carries Noust's tunnel options (`restrict,port-forwarding,permitopen=...`),
and so does the hardening check "A central's tunnel logs in as root" (`noust server security
checks`, counted by `noust health`). Each warning gives the exact command that removes that key
and nothing else:

```bash
sed -i '\#AAAAC3NzaC1lZDI1NTE5AAAA...#d' /root/.ssh/authorized_keys
```

A server migrated with 3.1.0 still has that line: remove it with the command the warning prints,
once `noust node list` on the central shows the server reached as `noust-tunnel`.

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

`noust central backup` writes everything a central needs to come back into one file, while it
runs: every file of its configuration and state directories, the databases as consistent
snapshots, and a SHA-256 manifest, encrypted and authenticated under a passphrase you type
(scrypt, then AES-256-CBC by openssl and HMAC-SHA256, the construction of sealed secrets). The
passphrase is stored nowhere.

```bash
docker exec -it noust noust central backup                       # to central/ under the backups directory
docker exec -it noust noust central backup --output /data/backups/central
docker exec -it noust noust central backup --verify FILE         # it opens, and every file matches
docker exec -it noust noust central backup --decrypt FILE --to /data/restore.tar.gz
```

Keep the file off the machine and the passphrase apart from it: without the passphrase nobody,
Noust included, can restore it. To restore, decrypt it, stop the central, and put the
archive's `noust-central/config/` and `noust-central/state/` back as the configuration and
state directories (`/data/config` and `/data/state` in the container, `/etc/noust` and
`/var/lib/noust` on a VPS); `MANIFEST.json` lists every file with its SHA-256. A sealed central's
secrets stay sealed inside the backup, so restoring them needs both passphrases.

What it holds, and what a copy of the whole volume holds too:

| Path | What |
|------|------|
| `/data/config/` | `config.yaml`, the console's signing key, token hash, sessions, two-factor state, audit log and its key, and its TLS pair (`panel-tls/`) |
| `/data/state/noust.db` | the store: servers, their pinned host keys, labels, accounts, fleet jobs, history |
| `/data/state/secrets/` | node keys and tokens (sealed or not) and the seal header `.seal` |
| `/data/backups/`, `/data/log/` | the central's own backups and logs |

A copy of the volume works too. Stop the container for a consistent copy (`docker stop noust`),
or copy it running and accept that the last minute may be missing. A sealed backup is only as
useful as the passphrase you still have: without it, restore gives you a central that cannot
reach any server.

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
`compose.yaml` (`ghcr.io/perkybeet/noust:3.1.0`) to update when you choose, or use `latest`.
Take a `noust central backup` first: a new version may migrate the store, and the copy it keeps
beside the store (`noust.db.v<old>-<time>.bak`) is the only way back to the older image. The
upgrade from 3.0 is described in [UPGRADING-3.1.md](UPGRADING-3.1.md).

## Troubleshooting

- **"The data directory /data is not writable by uid 10001"**: a bind-mounted folder belongs
  to someone else. `chown -R 10001:10001` it on the host.
- **The browser cannot reach the console**: check the NAS firewall allows 8443 from your LAN,
  and that your address is inside the allowed networks (`noust central status`). Docker on
  some systems presents clients with the bridge's address (`172.17.0.1`), which the defaults
  already allow.
- **"The secrets are sealed and locked"**: unlock them (above).
- **A server that dials in shows as not connected**: nothing listens on its link's port. On the
  server, `systemctl status noust-link-<central>` and `journalctl -u noust-link-<central>` say why:
  the central's address, a firewall in the way, or a host key that no longer matches the pinned one
  (the central's SSH host key changed: link it again). On the central, `noust node link status`
  says whether `noust-link` is still restricted in sshd.
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
