# Upgrading to 3.1

This guide is written against Noust 3.0.0. A WASM 2.x server goes through
[UPGRADING-3.0.md](UPGRADING-3.0.md) first; the packages do both steps in one upgrade, but read
that guide for what the rename moves.

The short version: 3.1 adds accounts and roles, an audit trail with integrity, management of
the server itself, a fleet you can act on as a whole, builds that no longer run as root, and a
rebuilt console. Your applications keep running through the upgrade, your access token and API
tokens keep working, and nothing is converted behind your back: the new build sandbox, the new
tunnel account for fleet servers and your first account are each a step you take, in the order
below. What 3.1 does change on its own is listed first, so nothing surprises you.

One thing is one-way: the store moves to schema v12, and 3.0 does not understand it. The
upgrade keeps a copy of the store as it was, which is what [rolling back](#rolling-back) puts
back.

## What changes on its own

When the new package is installed, before you do anything:

- **The store is migrated to v12**, the first time Noust opens it (the package's own steps, the
  console's restart, or the next command). Before the first change, a copy is written beside it:
  `/var/lib/noust/noust.db.v11-<UTC time>.bak`, mode `0600`, taken with SQLite's online backup so
  it includes what the write-ahead log still held. If the copy cannot be written (no space),
  nothing is migrated and the command says so.
- **The configuration is cleaned.** The package runs `noust config upgrade` and then `noust
  config clean`, which removes the settings no version of Noust reads any more: `logging`,
  `nodejs`, `python`, `databases.default_encoding`, `databases.auto_start`,
  `databases.auto_enable`, `databases.backup_dir`, `monitor.use_ai`, `monitor.ai_interval` and
  `monitor.openai`. They are removed as text, so your comments and the rest of the file stay as
  they are, after a dated copy (`config.yaml.bak-<date>`, mode `0600`) is written beside it. The
  OpenAI API key an old file may hold is deleted from the file and is not kept in the copy.
  `noust db install` now always starts and enables the engine it installs, which is what
  `databases.auto_start` and `auto_enable` used to say. See [CONFIG.md](CONFIG.md).
- **The monitor is installed and enabled**, because the charts' history is now recorded by
  `noust-monitor` rather than by the console. It is left alone when it is already installed, and
  left off when you turned it off (see [The monitor](#3-the-monitor) before upgrading if you
  removed it under 3.0).
- **The console restarts** onto the new version, if it runs as `noust-web.service`. Your access
  token keeps working. Open sessions keep working, but sessions now end after **30 minutes
  without activity and 12 hours in total** (3.0: 12 and 24 hours); `auth.session.idle_minutes`
  and `auth.session.absolute_hours` change that.
- **The sign-in page** no longer shows the host name and version before you sign in. Set
  `auth.login_label` if you want it to say which server it is.
- **`web.allowed_hosts` is enforced.** It was read by nothing in 3.0. If your file sets it, the
  console now answers only those host names (plus loopback and the hosts of `web.public_url` and
  `web.hooks_url`), and any other host gets a 400. Check it before upgrading:
  `noust config get web.allowed_hosts`. Empty allows any host, as before.
- **A central's self-signed certificate** (the container, or `--self-signed`) is replaced once,
  on its first start under 3.1, by an ECDSA P-256 certificate with names. The browser asks about
  the new fingerprint once; `noust central status` shows it.
- **Sites written from now on** send `server_tokens off` (Apache: `ServerTokens Prod`,
  `ServerSignature Off`) and no longer send `X-XSS-Protection`. `Strict-Transport-Security` is
  sent only if you turn on `ssl.hsts` (off by default). Existing sites change the next time they
  are written.
- **Units written from now on** carry `MemoryAccounting=yes` and `CPUAccounting=yes`, so the
  application's metrics have something to read.

## What does not change

- **Your applications**: their units, directories, sites, certificates, `.env` files and
  releases. Nothing an application runs is restarted by the upgrade.
- **How existing applications build.** They keep building as root, as in 3.0, until you test and
  enable the sandbox for each one ([Builds without root](#7-builds-without-root)).
- **Tokens.** The master access token, and every API token with its scope. A token issued by 3.0
  keeps its 3.0 behaviour, including not being asked for sudo mode.
- **A fleet.** Servers enrolled by 3.0 keep working with the key in root's `authorized_keys`
  until you move them ([Upgrading a fleet](#upgrading-a-fleet)).
- **Who may do what, until you create an account.** On a server with no accounts, the access
  token is the whole console, exactly as in 3.0.
- **The names kept from WASM** (`wasm_session`, `X-WASM-CSRF`, `wasm_ro_` roles and the rest), and
  the `wasm` command.

## Upgrading a single server

### 1. Take stock

```bash
noust --version                           # 3.0.x
noust list                                # keep this output to compare afterwards
noust store path                          # where the store is
noust web status
noust monitor status                      # installed? running? you decide below whether it should be
noust config get web.allowed_hosts        # see "What changes on its own"
df -h "$(dirname "$(noust store path)")"  # room for a copy of the store
```

Wait until no job is running (the console's Activity page, or `GET /api/jobs?status=running`):
the console restarts during the upgrade and a job still running then is marked failed.

### 2. Back up

The upgrade keeps a copy of the store by itself, but keep your own of everything it touches:

```bash
install -d -m 700 /root/noust-3.0-backup
cp -a /etc/noust /root/noust-3.0-backup/etc-noust            # configuration, console state, tokens, audit log
cp -a /var/lib/noust /root/noust-3.0-backup/var-lib-noust    # the store, metrics, observations, secrets/
noust store export -o /root/noust-3.0-backup/store.json      # every record, readable
```

If `noust store path` printed a location outside `/var/lib/noust`, copy that directory too.
Stop the console first (`noust web stop`, or `systemctl stop noust-web`) for a copy that is
consistent to the second, or accept that the last seconds may be missing.

Keep a way to reinstall 3.0.0:

- **Debian and Ubuntu:** the `.deb` attached to the GitHub release
  `https://github.com/Perkybeet/noust/releases/tag/v3.0.0`.
- **Fedora and openSUSE:** OBS publishes only the latest build. Save the installed package
  before upgrading, while the repository still serves it: `dnf download noust`, or copy it
  from the zypper cache.
- **PyPI:** `pip install 'noust[all]==3.0.0'` stays available.

### 3. The monitor

The package installs and enables `noust-monitor` unless you turned it off. 3.1 remembers that
you did in `/var/lib/noust/monitor-declined`, which 3.0 never wrote. On the upgrade, the package
also treats the monitor as turned off when the journal still has lines from `noust-monitor` (or
`wasm-monitor`) or its observation database holds anything. If you removed the monitor under
3.0 and neither is left (a rotated journal), keep it off with:

```bash
touch /var/lib/noust/monitor-declined     # before upgrading; 'noust monitor enable' removes it
```

On Fedora and openSUSE the monitor needs `python3-psutil` (recommended by the package, not
required): `dnf install python3-psutil` or `zypper install python3-psutil`. Without it, the
monitor records nothing and `noust monitor status` says so. See [MONITOR.md](MONITOR.md).

### 4. Upgrade the package

```bash
apt update && apt upgrade                    # Debian, Ubuntu
dnf upgrade --refresh                        # Fedora
zypper refresh && zypper update              # openSUSE
pip install -U 'noust[all]' wasm-cli         # PyPI (or 'noust[web]' without the monitor)
```

#### Passkeys need `cryptography`

Passkeys verify signatures with the `cryptography` Python package. Everything else works
without it; the console only says passkeys are unavailable.

- **Debian and Ubuntu**: recommended by the package, so `apt` installs it by default
  (`python3-cryptography`).
- **Fedora and openSUSE**: suggested, not installed: `dnf install python3-cryptography`, or
  `zypper install python3-cryptography`.
- **PyPI**: included in `noust[web]` and `noust[all]`: `pip install -U 'noust[web]'`.
- **The container**: included.

Browsers offer passkeys only on a certificate they trust, so a console reached over an SSH tunnel
at `http://localhost` or with a self-signed certificate cannot use them; the console says why.

### 5. Check

```bash
noust --version                           # Noust 3.1.x
ls -l /var/lib/noust/noust.db.v11-*.bak   # the copy of the store as it was
noust list                                # the same applications, in the same states
noust web status
noust monitor status                      # running and recording the history, or off because you said so
noust config show                         # obsolete settings, if any, are listed apart
noust health                              # now also hardening checks and the build sandbox warnings
noust audit status                        # the audit trail works, and where it is shipped
noust app sandbox self-test               # whether the build sandbox holds on this server
```

Sign in to the console with the same access token. On a server with no accounts it opens
**Create the first account**.

### 6. Create the first account

Until an account exists, nothing about access has changed. Create one when you are ready; from
then on every action is taken by a named person, and the access token becomes emergency access.

In the console, sign in with the access token and follow **Create the first account**: an
administrator, then (recommended) a security officer for a different person. Or on the server:

```bash
noust user create alice --role admin --display-name "Alice" --person-ref alice@example.com
noust user create bob --role security --person-ref bob@example.com
noust user invite carol --role operator            # prints a one-use code; Carol sets her own password
noust user list
```

The password is asked at a hidden prompt (or read with `--stdin`). Each account enrols its
second factor, an authenticator app or a passkey, at its first sign-in, before it can do
anything else.

What happens when the first `admin` account exists:

- **API tokens issued before accounts** are adopted by that account and keep their scope. Fleet
  tokens are left alone: they belong to a central. `noust token list` shows each token's owner.
- **The access token becomes break-glass.** It still signs in, with its second factor, and every
  use is audited. It cannot manage the audit log. Under `security.profile: ens-medium` it can
  only recover access to accounts.
- **Roles apply.** `admin` does not manage accounts or read the whole audit log; `security` does.
  If one person must hold both, record why: `noust user exception add PERSON --reason '...'`.

Choose the roles from the table in [security.md](security.md#roles-and-permissions).

### 7. Builds without root

Applications created from 3.1 on, and every preview, install and build as the unprivileged
`noust-build` account in a sandbox. Existing applications keep building as root, with a warning
on their page, in `noust health` and in `noust ens check`, until you move each one:

```bash
noust app sandbox self-test                # once: does the sandbox hold on this server?
noust app sandbox status shop.example.com  # how it builds now
noust app sandbox test shop.example.com    # build what the next update builds, in the sandbox; nothing is activated
noust app sandbox enable shop.example.com  # from the next deploy on (asks for a passing test)
```

`test` copies what the next update would build into a scratch directory, installs and builds
it as `noust-build` exactly as an enabled sandbox would, and throws it away. On releases that is
the commit the application runs now; in place it is the files git tracks as they are in the
application's tree, uncommitted changes made on the server included (an in-place update builds
them), without `node_modules` or build output. `test` names those changes and says to commit
them: a fresh deploy of the repository would not have them. Nothing is
restarted and nothing enters the deployment history. When it fails, the error says what the
build tried to reach. The usual causes are a build that writes outside its own directory, reads
a file from another application, or needs a private registry's credentials from root's home. In
the console: application > Settings > Builds.

Options of `enable`: `--network strict` installs dependencies without the application's
variables and builds without a network; `--pty` runs the build on a terminal for build scripts
that reopen `/dev/stderr`.

**Where the sandbox cannot run.** Containers and WSL without mount namespaces fail the
self-test. There the first deploy of a new application stops with the evidence rather than
building as root. To build an application as root anyway, record the decision (it can be done
before its first deploy):

```bash
noust app sandbox disable shop.example.com --reason "WSL development machine, no mount namespaces"
```

New paths: `/var/cache/noust/build/<app>` (per-application build caches, owned by
`noust-build`) and `/run/noust/sandbox`. The `noust-build` system account is created on the first
sandboxed build.

**Docker Compose.** A new stack with `privileged: true` or the Docker socket mounted is refused.
A stack that already runs them is warned about and keeps deploying. If a new stack really needs
them: `noust app sandbox compose-exception DOMAIN`.

### 8. Branch pinning

3.1 records which branch an application deploys from when you choose one:

- `noust app branch DOMAIN` shows it; `noust app branch DOMAIN main` pins `main`;
  `noust app branch DOMAIN --unpin` removes the pin.
- A successful `noust update DOMAIN --branch X` pins `X`: the next plain update, and the
  webhook's, follow it.
- With a branch pinned, every update builds it and the deploy webhook ignores pushes to any
  other branch (they appear as ignored in `noust app webhook deliveries`).
- Without a pin, an update keeps following what it followed in 3.0: the branch the checkout is
  on for an in-place application, the recorded branch for one on releases.

Nothing is pinned by the upgrade. Check the applications that deploy on push:

```bash
noust app webhook show shop.example.com    # the branch that deploys, and a warning when none is pinned
noust app branch shop.example.com main
```

### 9. Scripts and automation

- **`--reason`.** Every command takes `--reason TEXT`, recorded in the audit log. It is optional
  unless you turn on the `ens-medium` profile, where commands that change something require it.
- **Tokens.** Existing tokens keep working. A token you issue from now on belongs to an account
  (`noust token create NAME --owner USERNAME`), holds at most that account's permissions, and
  cannot act where sudo mode is asked unless issued with `allow_elevated` (API only; refused under
  the ENS profile).
- **Sign-in over the API** takes `username`, `password` and `totp_code`, or `token` for the master
  token as before. See [api.md](api.md).
- **`403` errors name a permission** instead of a scope, and a call that needs a second
  person's approval answers `202` with `approval_required` when approvals are on.
- **Services moved** under the Server page in the console (`/services` redirects). The API paths
  are unchanged.
- **Database backups** that you schedule from 3.1 on are timers named
  `noust-backup-db-<engine>-<database>`.

## New settings

All optional; `noust config upgrade` adds those with defaults, and [CONFIG.md](CONFIG.md)
documents every one.

| Setting | Default | What it does |
|---|---|---|
| `server.name` | the short host name | Names this server in notifications and in the fleet |
| `security.profile` | `standard` | `ens-medium` caps sessions, lockout, passwords, token lifetimes, audit retention, approvals and backup encryption at Spain's ENS category MEDIUM, and makes `--reason` required |
| `auth.*` | 30 min idle, 12 h, 5 failures, 15 min, 12 characters | Sessions, account lockout, password length, token lifetimes, the notice, the sign-in label |
| `audit.*` | 365 days, journald `auto` | Retention, size, the host action ledger, journald, syslog receivers, the anti-flood rule |
| `retention.*` | jobs 90, deployments 365, sessions 30 days | How long Noust's other records are kept |
| `approval.*` | off | `approval.enabled`, `approval.approvers`, `approval.request_hours`, `approval.execute_minutes` |
| `metrics.retention_days` | `400` | Days the hourly tier of the charts is kept |
| `databases.safety_copies_kept` | `5` | Safety copies of a database kept before restores over it |
| `ssl.hsts` | `false` | Send `Strict-Transport-Security` from the sites Noust writes |
| `web.allowed_hosts` | empty | Now enforced: the host names the console answers to |
| `notifications.events.*` | see CONFIG.md | New events: restores, `backup_success`, reboots, approvals, fleet |

## Upgrading a fleet

A 3.1 central drives 3.0 servers and a 3.0 central drives 3.1 servers: nothing stops working
halfway. Upgrade **the central first**, then the servers, then move each server to the tunnel
account:

- A **3.1 central with 3.0 servers** reaches them as before through the proxy. The fleet views
  show each 3.0 server as `unsupported` for what it does not offer (its row says so, the rest of
  the page is unaffected), bulk actions skip it with the reason, and its access ceiling shows as
  `unknown`.
- A **3.0 central with 3.1 servers** keeps working too, with the scope its token has, but it
  cannot move a server to the tunnel account, show fleet views or run bulk actions, and a server
  that requires approvals refuses the calls that need one, because a 3.0 central cannot vouch
  for an approval.

### 1. The central

On a VPS, as [a single server](#upgrading-a-single-server), including creating its accounts.
Accounts matter most on the central: operators reach the servers through it, and each server
receives the operator's identity and role from it.

In a container:

```bash
docker exec noust noust store path       # note it; the copy of the store is written beside it
docker compose pull && docker compose up -d
docker logs noust                        # no new token; the new certificate's fingerprint
```

Pin the version in `compose.yaml` (`ghcr.io/perkybeet/noust:3.1.0`) to upgrade when you choose.
Take a copy of the volume first (see [CENTRAL.md](CENTRAL.md#backups)); from 3.1 on,
`noust central backup` makes an encrypted one while it runs.

Then check, from the central:

```bash
noust node list                          # every server, its version, and those still reached as root
noust fleet status
```

### 2. Each server

Upgrade each one as [a single server](#upgrading-a-single-server). A server's own accounts are
optional when it is only reached through the central: the central's accounts are the ones that
matter, and a central can never create or change a server's accounts, tokens or second factor.

Its ceiling stays `admin` with host access off, which is what 3.0 allowed except for host
access: from 3.1 on a central needs `host access` to change SSH, the firewall or system accounts,
or to make root-equivalent changes (raw units, cron commands, backup hooks, raw site
configuration). Set what you want on the server:

```bash
noust fleet access                          # the ceiling in force
noust fleet access --level deploy           # or read, or admin
noust fleet access --host-access on         # only if the central should reach the host itself
```

### 3. Move each server to the tunnel account

A server enrolled by 3.0 has the central's key in **root's** `authorized_keys`. That key can
create Unix sockets as root anywhere on the server (`permitlisten` does not limit them): see
[CENTRAL.md](CENTRAL.md). Move each one once it runs 3.1.1 or later (the central too):

```bash
noust node migrate-tunnel vps1              # on the central: prints the command for vps1
# on vps1, as root: run the printed 'noust fleet authorize ... --replace-root-key ...'
noust node migrate-tunnel vps1 --join-code -   # on the central: paste the code vps1 printed
```

On the server, `authorize` creates `noust-tunnel`, restricts it in sshd, installs the same key
for it, takes that key out of root's `authorized_keys` and issues a new fleet token. The key is
removed because `--replace-root-key` names it, whatever the central is called in the line's
comment; every other line stays, and a 0600 copy of the file as it was is kept next to it
(`authorized_keys.noust-<UTC time>`). Check that `authorize` printed root's file under "Old line
removed from". The central switches once the server answers as `noust-tunnel`; until then
nothing changes and the code can be pasted again. `noust node list` no longer flags it. The
server no longer needs `PermitRootLogin` for the fleet.

**Migrated with 3.1.0?** 3.1.0 matched root's line by the central's name, and a 3.0 line that
named the central otherwise stayed in root's file, still able to create Unix sockets as root.
On 3.1.1 `noust fleet authorize` warns about any such line whatever its flags, and so does the
hardening check "A central's tunnel logs in as root" (`noust server security checks`, counted by
`noust health`); each warning carries the exact command that removes that key, such as
`sed -i '\#AAAAC3NzaC1lZDI1NTE5AAAA...#d' /root/.ssh/authorized_keys`. Run it once the central
reaches the server as `noust-tunnel`.

If sshd refuses the account (usually an `AllowUsers` or `AllowGroups` without `noust-tunnel`),
`authorize` lists the reasons and changes nothing; see
[CENTRAL.md](CENTRAL.md#troubleshooting).

### 4. Labels and bulk actions

Group the servers with labels on the central, then act on them together:

```bash
noust node label vps1 env=prod
noust fleet run certs_renew --label env=prod --plan    # what it would do, and on which servers
noust fleet run apps_update --label env=prod --serial 1 --canary vps1
noust fleet jobs
```

## What to check after upgrading

- `noust list` matches what you noted before; every application in the same state.
- `ls /var/lib/noust/noust.db.v11-*.bak` exists; keep it until you are sure you will not roll back.
- `noust config show` lists no unexpected obsolete settings, and `config.yaml` still has your
  comments.
- `noust monitor status` says the history is being recorded, or the monitor is off because you
  said so.
- `noust audit status` and `noust audit verify` pass.
- `noust health`: read the new hardening findings and sandbox warnings. They describe the server
  as it already was; fix them or accept them (`noust server security accept CHECK --why '...'
  --days 90`) at your pace.
- The console: sign in, create the first account, enrol its second factor, and check that a
  deploy and a rollback of one application work.
- On a central: `noust node list` and `noust fleet status`; no server left reached as root.
- Webhooks: `noust app webhook show DOMAIN` for each application that deploys on push.

## Rolling back

Going back to 3.0 means reinstalling 3.0 and putting back the store 3.0 understands. 3.0 has
no check for a store newer than itself, so it must not be started on the v12 store: restore the
copy first.

Before you start, finish what 3.1 is in the middle of:

```bash
noust server security pending    # SSH or firewall changes waiting for confirmation: confirm or revert them
noust server updates history     # no system update still running
```

1. **Stop the console and the monitor:**

   ```bash
   systemctl stop noust-web noust-monitor
   ```

2. **Reinstall 3.0.0:**

   ```bash
   apt install --allow-downgrades ./noust_3.0.0-*_all.deb           # Debian, Ubuntu
   dnf downgrade ./noust-3.0.0-*.noarch.rpm                          # Fedora, the package you saved
   zypper install --oldpackage ./noust-3.0.0-*.noarch.rpm            # openSUSE
   pip install 'noust[all]==3.0.0'                                   # PyPI
   ```

3. **Put back the store and the console's state:**

   ```bash
   cd /var/lib/noust
   mv noust.db noust.db.3.1
   rm -f noust.db-wal noust.db-shm
   cp -a noust.db.v11-<time>.bak noust.db
   mv /etc/noust /etc/noust.3.1
   cp -a /root/noust-3.0-backup/etc-noust /etc/noust
   ```

   Restoring `/etc/noust` also puts back the configuration as it was before `config clean` and
   the audit log as 3.0 left it (3.1's lines stay in `/etc/noust.3.1/web-audit.log`).

4. **Disable what 3.0 does not know:**

   ```bash
   systemctl list-timers 'noust-backup-db-*'     # database backup policies: 3.0 has no 'noust db backup-run'
   systemctl disable --now noust-backup-db-<engine>-<database>.timer
   ```

5. **Start the console and the monitor as before** (`noust web enable` with your options, or
   `systemctl start noust-web`; `noust monitor install` if you run it), and check with
   `noust --version`, `noust list` and `noust web status`.

What does not come back:

- **Everything recorded under 3.1**: accounts, passkeys, approvals, database backup policies,
  accepted risks, fleet labels and jobs, deployments and jobs since the upgrade. It stays in
  `/var/lib/noust/noust.db.3.1` and `/etc/noust.3.1`.
- **API tokens issued under 3.1** are not in the restored console state.
- **A server moved to the tunnel account.** Its key is no longer in root's `authorized_keys`,
  so a central rolled back to 3.0 (whose restored store says root) cannot reach it. A server
  rolled back to 3.0 loses the fleet token `migrate-tunnel` issued, which lived in the console
  state you replaced. Either way, enroll the server again: `noust fleet authorize` on it, then
  `noust node remove` and `noust node add` on the central. The `noust-tunnel` account and its
  sshd block stay on the server; 3.0 neither uses nor removes them, and 3.0's
  `fleet deauthorize` does not look in that account's key file
  (`/etc/ssh/noust/noust-tunnel.keys`). Remove them by hand when nothing uses them.
- **Builds go back to root** for every application, sandboxed or not. `noust-build`,
  `/var/cache/noust/build` and `/run/noust/sandbox` stay unused; remove them with `userdel
  noust-build` and `rm -rf /var/cache/noust/build` if you want.
- **Changes 3.1 made to the system itself stay**: packages updated, SSH and firewall changes
  confirmed, swap, time zone and host name, database owners changed with `noust db fix-owner`,
  connection strings written into applications' `.env` files, and a central's new certificate.
  They are ordinary system changes that 3.0 neither needs nor undoes.
- **Charts recorded by the monitor** stay in `/var/lib/noust/metrics.db`, which 3.0's console
  keeps writing.
