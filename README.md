> **WASM is now Noust.** From 3.0.0 the product is called Noust. What changed: the name, the
> command (`noust`), the configuration and data paths (`/etc/noust`, `/var/lib/noust`,
> `/var/backups/noust`) and Noust's own systemd units (`noust-web`, `noust-monitor`,
> `noust-cron-*`, `noust-backup-*`, `noust-previews`). What did not: your applications, their
> units, directories, domains and certificates, the console, the API and your tokens. The
> package upgrade (or, with pip, the first `noust` command run as root) moves everything to
> the new names and leaves symbolic links at the old paths, and `wasm` keeps working as an
> alias for the whole 3.x series.
> Read [docs/UPGRADING-3.0.md](docs/UPGRADING-3.0.md) before upgrading a 2.x server.

<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Perkybeet/noust/main/docs/brand/noust-wordmark-dark.svg">
    <img src="https://raw.githubusercontent.com/Perkybeet/noust/main/panel/src/assets/brand/noust-wordmark.svg" alt="Noust" width="360">
  </picture>
</h1>

<p align="center">
  <a href="https://build.opensuse.org/package/show/home:Perkybeet/noust">
    <img src="https://build.opensuse.org/projects/home:Perkybeet/packages/noust/badge.svg?type=default" alt="OBS Build Status">
  </a>
  <a href="https://pypi.org/project/noust/">
    <img src="https://img.shields.io/pypi/v/noust?color=blue&logo=pypi&logoColor=white" alt="PyPI Version">
  </a>
  <a href="https://pypi.org/project/noust/">
    <img src="https://img.shields.io/pypi/pyversions/noust?logo=python&logoColor=white" alt="Python Version">
  </a>
  <a href="https://github.com/Perkybeet/noust/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/Perkybeet/noust?color=blue" alt="License">
  </a>
  <a href="https://github.com/Perkybeet/noust/stargazers">
    <img src="https://img.shields.io/github/stars/Perkybeet/noust?style=social" alt="GitHub Stars">
  </a>
</p>

**Your server, Vercel-grade. No Docker required.**

In Orkney and Shetland, a *noust* is the hollow on the shore where a boat is drawn up and
sheltered between voyages. Your server is the noust, and your applications are the boats.

Noust deploys web applications onto a Linux server you own and keeps them running. Point it
at a repository and a domain: it builds the application, runs it as a systemd unit behind
nginx or Apache, obtains its certificate, and from then on every deploy is a new release
that only goes live if it answers, and can be undone in seconds. The same engine is driven
from the CLI, a browser console and a JSON API.

![The Noust console](https://raw.githubusercontent.com/Perkybeet/noust/main/docs/assets/console/overview.png)

---

## What Noust is, and is not

**It is**

- **Your server.** A VPS or a bare-metal machine running Ubuntu, Debian, Fedora or openSUSE.
- **systemd-native.** Every application is a unit you can inspect with `systemctl` and
  `journalctl`; every site is a file in `/etc/nginx` or `/etc/apache2`. Nothing sits between
  you and your processes, and there is no daemon with a privileged socket.
- **A distribution package.** Installed with `apt`, `dnf` or `zypper`, or from PyPI.
- **Atomic deploys with instant rollback.** Each deploy builds in its own directory, is
  activated behind a health check, rolls back by itself when it does not answer, and any
  release still on disk can be reactivated in seconds. Blue/green activation, opt-in per
  application, keeps the old version serving until the new one answers.
- **Per-application resource limits** with cgroups: memory, CPU and tasks.
- **A console and an API** over exactly what the CLI does, with scoped tokens, two-factor
  authentication, sudo mode and an audit log.

**It is not**

- **A cluster or an orchestrator.** Each server runs its own applications and manages
  itself. 3.0 brings a central that manages several Noust servers from one console (see
  [The fleet](#the-fleet)); it does not schedule work across them.
- **A container platform.** Applications run as ordinary processes. Docker is only needed if
  you deploy a Docker Compose project, which Noust then runs as a unit.
- **An isolation boundary between applications.** They run as the same service account by
  default. Deploy only code you trust, as you would on any server you administer.
- **Zero-downtime by default.** Activating a release restarts the unit, unless blue/green is
  turned on for the application. There is no web terminal.

---

## Install

Noust needs root: it writes to `/etc`, `/var` and systemd. Run the commands below as root, or
prefix them with `sudo`. The packages come from the `home:Perkybeet` repository on the
openSUSE Build Service.

### Ubuntu and Debian (recommended)

```bash
# Add GPG key
curl -fsSL https://download.opensuse.org/repositories/home:/Perkybeet/xUbuntu_26.04/Release.key | \
  gpg --dearmor | sudo tee /usr/share/keyrings/noust.gpg > /dev/null

# Add repository
echo 'deb [signed-by=/usr/share/keyrings/noust.gpg] https://download.opensuse.org/repositories/home:/Perkybeet/xUbuntu_26.04/ /' | \
  sudo tee /etc/apt/sources.list.d/noust.list

# Install
sudo apt update
sudo apt install noust
```

Replace `xUbuntu_26.04` with your release: `xUbuntu_24.04`, `xUbuntu_22.04`, `Debian_12` or
`Debian_13`. A server that already has the WASM repository configured needs no new
repository: it is the same one.

**Supported versions:**
- Ubuntu 26.04 LTS (Resolute Raccoon, Python 3.14)
- Ubuntu 24.04 LTS (Noble Numbat)
- Ubuntu 22.04 LTS (Jammy Jellyfish)
- Debian 12 (Bookworm) and 13 (Trixie)

### Fedora

```bash
sudo dnf config-manager --add-repo \
  https://download.opensuse.org/repositories/home:/Perkybeet/Fedora_42/home:Perkybeet.repo
sudo dnf install noust
```

### openSUSE

```bash
# Tumbleweed
sudo zypper ar -f \
  https://download.opensuse.org/repositories/home:/Perkybeet/openSUSE_Tumbleweed/ \
  home_Perkybeet
sudo zypper install noust

# Leap 15.6
sudo zypper ar -f \
  https://download.opensuse.org/repositories/home:/Perkybeet/openSUSE_Leap_15.6/ \
  home_Perkybeet
sudo zypper install noust
```

### PyPI

```bash
pip install noust            # the CLI
pip install 'noust[web]'     # with the console and the API
pip install 'noust[all]'     # with the console, the API and the monitor
```

The packages WASM was published as, `wasm` (Debian, Ubuntu) and `wasm-cli` (Fedora,
openSUSE, PyPI), are transitional in 3.x: upgrading one installs `noust`. See
[docs/RENAME.md](docs/RENAME.md).

### A container, for a central

The image `ghcr.io/perkybeet/noust` runs a central: the console and the connections to your
servers, and nothing else. It deploys no applications. See [The fleet](#the-fleet).

```bash
docker run -d --name noust --restart unless-stopped \
  -p 8443:8443 -v noust-data:/data \
  --read-only --tmpfs /tmp:size=16m --cap-drop ALL \
  --security-opt no-new-privileges:true \
  ghcr.io/perkybeet/noust:latest
docker logs noust    # the console's sign-in token, once, and the certificate's fingerprint
```

It serves the console over TLS on `8443` (a self-signed certificate until you mount your own),
answers only the loopback and private address ranges by default, and keeps everything under
`/data`. [`packaging/container/compose.yaml`](packaging/container/compose.yaml) is a ready
example, including for a NAS running UGOS Pro. See [docs/CENTRAL.md](docs/CENTRAL.md).

### From source

```bash
git clone https://github.com/Perkybeet/noust.git
cd noust
pip install -e ".[all]"
```

The console's Python packages (FastAPI, Uvicorn, psutil) are recommended by the Debian
package and suggested by the RPM one. If they are missing, `noust web install` installs them.

---

## First deploy

```bash
noust setup init                                                   # web server, certbot, git, Node.js, directories
noust create -d shop.example.com -s https://github.com/you/shop.git   # detect, build, run, serve, certificate
noust status shop.example.com                                      # how it is configured, whether it runs
noust update shop.example.com                                      # after a push: a new release behind the health check
noust releases rollback shop.example.com                           # back to the previous release, in seconds
```

`noust create` detects the application type, installs and builds it in a new release under
`/var/www/apps/shop-example-com/`, writes a systemd unit and an nginx site pointing at
`current`, obtains a Let's Encrypt certificate, and keeps the release only if the
application answers. When the repository has an `.env.example`, a `.env` is generated from
it, with random values for secrets. Useful options: `--type`, `--port`, `--branch`, `--www`,
`--webserver apache`, `--no-ssl`, `--persist storage`, `--env-file` (its variables are
written into the application's `.env`, `0600` and loaded by the unit with
`EnvironmentFile=`; only `PORT` and `NODE_ENV` stay inline in the unit, which local users can
read). To change the port later, redeploy with `--port` rather than editing the `.env` - the
unit's own `PORT` would otherwise win over one written there. For a private repository,
create a deploy key with `noust setup ssh --generate --show` and use the SSH URL, or connect
the GitHub App.

Run `noust` with a command and `--help` for its options, or `noust -i` for an interactive
menu. `--dry-run` before any command rehearses it without changing anything, and `--json`
gives machine-readable output where supported.

---

## The console

```bash
noust web enable          # a systemd service on 127.0.0.1:8080; prints an access token
```

The console listens on loopback unless you give it TLS, so opening the server's address in a
browser will not reach it. Reach it through an SSH tunnel from your own machine; the banner
prints the exact line:

```bash
ssh -L 8080:127.0.0.1:8080 root@server.example.com    # then open http://localhost:8080
```

`noust web enable` keeps it running: it writes `noust-web.service`, which starts at boot and
restarts on failure, and `noust web disable` removes it. To try it first, `noust web start`
runs it in the foreground until Ctrl+C, and `noust web start -d` in the background until
`noust web stop` or the next reboot. All three take the same options and print the access
token the same way.

To expose it, serve TLS (`--host 0.0.0.0 --tls-cert ... --tls-key ...`, or `--self-signed`),
or put it behind a reverse proxy that terminates TLS and declare it with `--trusted-proxy`.
Binding beyond loopback without TLS is refused unless you pass `--insecure-http`. A running
console reads the token from disk on every request, so `noust web token --new` retires the
old one at once, with no restart needed.

Sign in with the access token, plus a code when two-factor authentication is on
(`noust 2fa enroll`). Destructive actions ask you to confirm it is you (sudo mode) and stay
confirmed for 10 minutes.

It covers everything the CLI does: applications with their deployments, releases, live logs,
metrics, environment, domains, diagnosis and settings; databases with a read-only SQL runner;
backups, schedules and remote destinations; certificates and sites; services; cron; an
activity timeline; the machine; and settings, notifications, integrations and API tokens.
The new-application wizard deploys from a repository, a recipe or an exported application.
It speaks English and Spanish, following the browser, switched in Settings > General; what
nginx, systemd or certbot print is shown verbatim, as they wrote it. Keyboard: `Ctrl K` for
the command palette, `g a` for applications, `/` to search, `?` for every shortcut. Light,
dark and system themes. Built to WCAG 2.2 AA and tested with axe on every page.

![An application's deployments](https://raw.githubusercontent.com/Perkybeet/noust/main/docs/assets/console/app-deployments.png)

See [docs/console.md](docs/console.md).

---

## Releases and instant rollback

```
/var/www/apps/shop-example-com/
  releases/20260925-143012-a1b2c3d/    one build per deploy
  releases/20260924-101500-9f8e7d6/
  current -> releases/20260925-143012-a1b2c3d
  shared/.env                           outside every release
  shared/storage/                       persistent paths, linked into each release
  repo/                                 git cache
```

- **Isolated builds.** A deploy fetches and builds in a new directory; the running
  application sees nothing until activation. Dependencies are copied from the active release
  instead of reinstalled when the lockfiles have not changed.
- **Health-gated activation.** `current` is swapped atomically and the unit restarted; the
  release stays only if the application answers its health check (by default, any status
  below 500 on its port) within about 30 seconds. The path, accepted statuses and timeout
  are set per application with `noust app health`.
- **Automatic rollback.** If it does not answer, the previous release is put back and the
  deploy fails with the probe results and the unit's journal, verbatim.
- **Instant rollback.** `noust releases rollback DOMAIN [RELEASE]`, the console, or
  `POST /api/apps/{domain}/releases/{id}/activate`: re-point, restart, same health gate.
  `noust update DOMAIN --commit SHA` rebuilds an exact commit.
- **Retention.** The newest five releases, plus the active one, are kept by default;
  `noust releases keep DOMAIN N` changes it.

Applications deployed in place (by WASM 1.x, or with `--layout inplace`) stay in place until
you run `noust app migrate DOMAIN` (rehearse it first with `noust --dry-run app migrate
DOMAIN`): the live tree becomes the first release, the `.env` and what the application wrote
for itself move to `shared/`, and if it does not come up everything is put back. Monorepo and
Docker Compose projects keep deploying in place. See [docs/releases.md](docs/releases.md).

### Blue/green

```bash
noust app zero-downtime shop.example.com on
```

Opt-in per application. The application runs as two instances of one systemd template unit,
each on its own port and release, behind an nginx upstream. An activation starts the idle
instance on the new release, passes the health gate on its port, moves the upstream with
`nginx -t` and a reload, and stops the old instance after a drain (10 seconds by default). A
failed gate stops the new instance; the old one never stopped. Only for applications on
releases, with a process, behind nginx; the application must tolerate two copies running
for a few seconds.

---

## Pull request previews

```bash
noust preview enable shop.example.com --domain previews.example.com
noust preview list shop.example.com
```

Every pull request gets `pr-<n>-<app>.previews.example.com`, deployed on releases with 256 MB
and half a CPU, rebuilt on each push, and removed when the request closes or after seven
days without a push, up to a quota per application. Events come from the application's own
webhook (GitHub, GitLab, Gitea) or from the GitHub App. Pull requests from forks are refused,
and on GitHub so are those of authors who are not the repository's owners, members or
collaborators. A preview gets the application's environment variables, production secrets
included, except those listed with `--exclude-env`; its build runs as root like every deploy.

---

## GitHub

A GitHub App per server, created from the console (Settings > Integrations) with GitHub's
manifest flow: it lives in your account, its private key stays on the server (`0600`), and
you choose the repositories when you install it. Private repositories clone with one-hour
installation tokens passed through git's environment only, never in a URL, `.git/config`, a
command line or a log; `github:owner/repo` works as a source everywhere. Pushes and pull
requests arrive at `/hooks/github`, and each deployment shows on GitHub as in progress,
success or failure. `noust web expose-hooks DOMAIN` publishes only `/hooks/` of the console
on a domain of its own, so GitHub can reach a console that listens on loopback.
`noust github status` shows what is connected.

---

## Recipes

```bash
noust recipe list
noust recipe show wordpress                         # what it needs, what it creates
noust create --recipe wordpress -d blog.example.com
```

WordPress (PHP-FPM and MariaDB), Uptime Kuma, Umami (PostgreSQL) and n8n, each with its
source pinned or checked against a published checksum, generated secrets, its database,
persistent paths, a health check and the next steps. Also "From a recipe" in the console's
new-application wizard. Noust installs nothing on its own: `noust recipe show` lists the
packages a recipe needs.

---

## Moving applications

```bash
noust app export shop.example.com -o shop.json        # secrets only with --with-secrets
noust app import shop.json --domain shop.example.org
noust import --from vercel ./shop                     # also: railway, render, heroku
```

An export is everything that defines an application, as versioned JSON; an import deploys it
through the normal path and applies the rest (domains, health check, retention, secret marks,
cron jobs, backup schedule, previews, blue/green), listing what it could not. `noust import
--from` reads another platform's configuration in a repository and proposes Noust's, with a
warning for each thing that has no equivalent.

---

## Domains, aliases and redirects

```bash
noust domain add shop.example.com shop.example.org                    # alias: serves the app too
noust domain add shop.example.com old-shop.example.com --kind redirect # 301 to the primary
noust domain list shop.example.com
noust domain remove shop.example.com shop.example.org
```

Every name is rendered into the site, tested by the web server before it is reloaded, and,
when the application serves TLS, added to its certificate. `noust create --www` records
`www.<domain>` as a redirect. The console checks where each name resolves before you add it.
See [docs/domains.md](docs/domains.md).

---

## Resource limits

```bash
noust app limits shop.example.com --memory 512M --cpu 50% --tasks 256 --restart
noust app limits shop.example.com --memory none         # remove one limit
noust app limits shop.example.com                       # show them
```

The limits become `MemoryMax=`, `CPUQuota=` and `TasksMax=` in the unit (200% is two CPUs).
With `--restart`, the application must pass the health gate under its new limits or the old
ones are put back. Docker Compose projects set their limits in the compose file.

---

## Diagnose

```bash
noust diagnose shop.example.com
```

Explains why an application is down. It checks the unit's state and exit status, whether the
recorded port is listening (and which port the process listens on instead), an HTTP probe
straight to the application and one through nginx, the last journal lines, nginx's error log
for the domain, the certificate, the last deployment, OOM kills in the last week and disk
space, and puts the most likely cause first: "Listening on 3001, Noust routes to 3000",
"Killed by the kernel for running out of memory". Every probe only reads. `--json` for
scripts; the exit code is 1 when the application is down.

`noust health` checks the whole server: free disk, the web server, every application,
certificates close to expiry and memory pressure.

![Diagnose](https://raw.githubusercontent.com/Perkybeet/noust/main/docs/assets/console/app-diagnose.png)

---

## Backups

```bash
noust backup create shop.example.com -m "Before the migration" --include-databases
noust backup list shop.example.com
noust backup verify BACKUP_ID
noust backup restore BACKUP_ID
noust backup schedule create shop.example.com --schedule daily --retention-count 7
noust rollback shop.example.com            # restore the latest backup, after a safety backup
```

A backup is one `.tar.gz` under `/var/backups/noust/<app>/`, mode `0600`, with a SHA-256
checksum: the application (for one on releases, the active release and `shared/`), its `.env`
unless `--no-env`, and on request its database dumps (`--include-databases`) and Docker
volumes, so it restores on a server that knows nothing about this one. Schedules are systemd
timers named `noust-backup-<app>`, and their retention applies only to the backups they
made: manual and safety backups are never removed by a schedule.

**Off the server.** Remote destinations go through rclone: SFTP, SMB, WebDAV, S3 and
compatibles, B2, Google Drive, OneDrive, Dropbox, pCloud and mounted paths, with optional
encryption per destination.

```bash
noust backup destination add offsite ...        # --help lists the kinds and their options
noust backup destination test offsite
noust backup push BACKUP_ID offsite
noust backup remote-list offsite
noust backup restore --from offsite BACKUP_ID
```

Each scheduled backup is copied to the schedule's destinations, verified and pruned per
destination; each server keeps its own folder, so servers sharing a bucket never remove each
other's backups. Keep the key an encrypted destination shows: it is the only way to read its
backups on a replacement server.

---

## Databases

```bash
noust db install postgresql                 # also: mysql (MariaDB), redis, mongodb
noust db create shop --engine postgresql
noust db user-create shop --engine postgresql --database shop
noust db connection-string shop shop --engine postgresql
noust db query shop "SELECT count(*) FROM orders" --engine postgresql
noust db backup shop --engine postgresql
```

`noust db query` is read-only unless `--write`, and the database server enforces it: a
read-only transaction under a dedicated role or account with `SELECT` and nothing else, so
functions like `pg_read_file` or `LOAD_FILE()` are out of reach. MongoDB and Redis have no
read-only mode. Passwords never appear in a command line.

---

## Cron

```bash
noust cron create nightly-report "/usr/bin/node scripts/report.js" --schedule daily \
  --app shop.example.com --working-directory /var/www/apps/shop-example-com/current
noust cron list
noust cron run nightly-report
noust cron runs nightly-report
```

Jobs are systemd timers (`noust-cron-<name>`). A schedule is `hourly`, `daily`, `weekly`,
`monthly` or any `OnCalendar=` expression. The command runs without a shell: write
`/bin/sh -c "..."` when you need pipes or `&&`. `--app` associates the job with an
application and makes its directory the default working directory; for an application on
releases, the code is under `current/`, so name that directory. Runs and their output are
read back from the journal.

---

## Notifications and webhooks

Notifications go to a webhook, Slack, Discord, Telegram or email on `deploy_started` (off by
default), `deploy_success`, `deploy_failed`, `deploy_rolled_back`, `backup_failed`,
`cert_expiring`, `unit_failed` and `disk_threshold`, from every deployment: the CLI's, the
console's, webhooks' and previews'. A failure carries the health gate's evidence. Configure
them in the console (Settings > Notifications) or with
`noust config set notifications.<key> <value>`, and test a channel with
`noust notify test slack`. They are written in English or Spanish
(`notifications.language`). Private and loopback destinations are refused unless listed in
`notifications.allow_private_hosts`.

Deploy on push: in the console (application > Settings > Webhook) or with
`POST /api/apps/{domain}/webhook-secret`, create a secret, and point a GitHub, Gitea or
GitLab webhook at `https://<console>/hooks/deploy/<domain>`, which the forge must be able to
reach. Signatures are verified; only pushes to the followed branch update the application.

---

## API

```bash
noust token create ci --scope deploy
curl -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps
curl -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/openapi.json
```

A JSON API under `/api`, with scoped tokens: `read` (everything visible, secrets redacted),
`deploy` (also create applications, update, roll back) and `admin`. Long operations are jobs
you follow over REST, Server-Sent Events (`/events`) or WebSockets. Every error has the same
shape: `{error, detail, hint, fields, output}`, where `output` is the system tool's own
output. The contract is served as OpenAPI at `/api/openapi.json`. See
[docs/api.md](docs/api.md).

---

## The fleet

3.0 brings the fleet: one Noust, the central, shows and drives several Noust servers from a
single console and CLI. The central can be one more VPS, or a container on a machine at home
such as a NAS, reaching each server over an SSH tunnel it opens outward, so no port has to be
opened anywhere. A central that only manages the fleet (`central.role = hub`) deploys nothing
itself.

```bash
noust node key vps1                                                    # on the central
noust fleet authorize --central-key 'ssh-ed25519 AAAA...' --name nas   # on vps1, as root
noust node add vps1 --ssh root@vps1.example.com --join-code -          # on the central
noust fleet status                                                     # every node it manages
```

Enrollment is inverted: the central never logs in to a server with your credentials. You
authorize it **on the server**, where you are already root: `noust fleet authorize` installs
the central's key restricted to forwarding that server's console port only - it opens no shell
and runs nothing else - and prints a join code, which you paste into the central
(`noust node add`) to finish. The central pins the server's SSH host key from that code; a
change closes the tunnel rather than being accepted.

The central's calls to a node carry a `fleet` token accepted only from the tunnel's loopback
end, and `X-Noust-Actor`, so the node's audit log names the operator behind the central rather
than the central itself. Anything a node marks as needing confirmation asks for it on the
central, the same sudo mode as any other destructive action, and the node refuses the call
anyway if the central did not vouch for it.

The console gets a server selector (every page also exists at `/n/<server>/...`, so a link
survives a refresh), a Fleet page, and Settings > Servers to add, test and remove one. A
central with its secrets sealed shows a lock screen until you unlock it, before it opens a
single tunnel.

![Fleet](https://raw.githubusercontent.com/Perkybeet/noust/main/docs/assets/console/fleet.png)

See [docs/CENTRAL.md](docs/CENTRAL.md) for running a central on a NAS or a VPS, sealing its
secrets, backups and troubleshooting.

---

## Security model

- **Root, on purpose.** Noust administers the machine, so it runs as root and anyone holding
  its master token or an admin token is root-equivalent. Treat them that way.
- **Processes are started with an argument list, never a shell**, always with a timeout, and
  secrets travel through the environment or standard input, never the command line. Only one
  module may start a process, and the test suite enforces it.
- **The console listens on loopback**, and refuses to serve beyond it without TLS.
- **Strict Content Security Policy** with Trusted Types; no inline scripts or styles; nothing
  loaded from another origin.
- **Sessions** are server-side, `HttpOnly`, `SameSite=Strict`, with CSRF tokens and two-factor
  authentication. Five failed credentials lock an address out for 15 minutes, across sign-in,
  tokens, WebSockets and webhook signatures.
- **Sudo mode**: deleting, restoring, revealing secrets, editing units and sites, writing SQL
  or configuration, and issuing tokens need a confirmation from the last 10 minutes.
- **Audit log**: every state-changing request, sign-in and credential change is appended to
  `/etc/noust/web-audit.log`.
- **Secrets at rest** are `0600`: configuration, `.env` files, the store, the credentials
  Noust keeps for itself, backups.

See [docs/security.md](docs/security.md), which also says how to report a vulnerability.

---

## Command reference

| Command | Does |
|---|---|
| **Applications** | |
| `noust create` | Deploy an application and put it online (also `deploy`, `new`; `--recipe`) |
| `noust list` | List deployed applications (`--json`) |
| `noust status` | Show how an application is configured and whether it runs (`--json`) |
| `noust start`, `stop`, `restart` | Control an application |
| `noust update` | Pull, rebuild and redeploy an application |
| `noust delete` | Delete an application and everything deployed with it |
| `noust logs` | Show or follow an application's log (`-f`, `--json`) |
| `noust env` | Show, configure, mark or export an application's environment |
| `noust releases` | List releases, set their retention and roll back to one instantly |
| `noust app` | Migrate to releases; limits, health check, blue/green; export and import |
| `noust domain` | Add, list and remove aliases and redirects |
| `noust preview` | Pull request previews |
| `noust recipe` | List and show the ready-made applications |
| `noust import` | Read another platform's configuration and propose Noust's |
| `noust diagnose` | Explain why an application is down |
| `noust rollback` | Restore an application's latest backup |
| **Web server and certificates** | |
| `noust site` | Create, enable, disable, show and delete nginx or Apache sites |
| `noust cert` | Obtain, list, inspect, renew, revoke and delete certificates |
| **Services and schedules** | |
| `noust service` | Create and control the systemd services Noust owns |
| `noust cron` | Run commands on a schedule, as systemd timers |
| **Data** | |
| `noust backup` | Create, verify, restore, schedule and send backups off the server |
| `noust db` | Install engines; manage databases, users, backups and queries |
| **The machine** | |
| `noust setup` | Prepare the server (`init`), check it (`doctor`), SSH keys, completions |
| `noust health` | Check the server and report what needs attention |
| `noust monitor` | Watch processes, resources, units and certificates, and report |
| `noust config` | Read and set Noust's configuration |
| `noust store` | Inspect, export and maintain Noust's database |
| `noust migrate-from-wasm` | Move a server WASM ran onto Noust's names (`--dry-run` shows the plan) |
| **Fleet** | |
| `noust fleet authorize` | Run on a server: let a central manage it, and print the join code |
| `noust fleet deauthorize` | Run on a server: stop trusting a central, revoke its key and tokens |
| `noust fleet status` | Run on a central: every node it manages, reachability, version, apps |
| `noust node` | Run on a central: register, list, show, test and remove nodes |
| `noust central` | Run and look after a central: serve it (`run`), seal, unseal, unlock, status |
| **Console and access** | |
| `noust web` | Start, stop and inspect the console, or run it as a service; issue its access token |
| `noust github` | The GitHub App: status, installations, repositories |
| `noust token` | Create, list and revoke scoped API tokens |
| `noust sessions` | List and revoke console sessions |
| `noust 2fa` | Enrol, confirm, disable or recover two-factor authentication |
| `noust notify` | Send a test notification through a channel |

Global options go before the command: `-v` (verbose), `--dry-run`, `--json`, `--no-color`,
`-i` (interactive menu), `--changelog`, `-V` (version). Tab completion: `noust setup
completions`. `wasm` runs the same program throughout 3.x.

---

## Supported application types

| Type | Detected by | Built and run with | Releases |
|---|---|---|---|
| `nextjs` | `next.config.{js,mjs,ts}`, or `next` in `package.json` | Install, `build`, then `start` or the standalone server | Yes |
| `vite` | `vite.config.{js,ts,mjs}`, or `vite` in `package.json` | Install, `build`, served as static files; `preview` when it uses SSR | Yes |
| `nodejs` | `package.json` with Express, Fastify or Koa, a `main` or a `start` script | Install, `build` if present, then `start:prod`, `start:production` or `start` | Yes |
| `python` | `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` | A virtual environment (Poetry or Pipenv when locked); Gunicorn, with Uvicorn workers for FastAPI and Starlette; Django's `collectstatic` | Yes |
| `php-fpm` | `composer.json` with a front controller, or a root `index.php` | A PHP-FPM pool per application on its own socket, `composer` when there is a `composer.json`, a fastcgi site (nginx only); `public/` detected | Yes |
| `static` | `index.html`, and no project manifest | Served by the web server from `public`, `dist`, `build`, `www`, `html` or the root | Yes |
| `monorepo` | `turbo.json`, pnpm workspaces and at least two apps under `apps/` | pnpm; one unit and one subdomain per workspace | In place |
| `docker-compose` | A compose file (`docker-compose.prod.yml` first) | `docker compose` v2 under a systemd unit, nginx in front of published ports | In place |

Detection tries the most specific type first: monorepo, Docker Compose, Next.js, PHP, Vite,
Python, Node.js, static. When nothing matches, Noust falls back to Node.js and says so; pass
`--type` to choose. Node package managers (npm, pnpm, Yarn, Bun) are detected from the
lockfile; `--pm npm|pnpm|bun` forces one.

---

## Configuration

`/etc/noust/config.yaml`, mode `0600`. Read and change it with the CLI rather than by hand:

```bash
noust config show                              # everything in effect, secrets in clear
noust config get deploy.layout                 # one key; secrets print as ***
noust config set ssl.email ops@example.com
noust config upgrade                           # add the options a newer Noust expects
```

Common keys: `apps_directory` (`/var/www/apps`), `webserver` (`nginx`), `service_user`
(`www-data`), `ssl.email`, `deploy.layout` (`releases` for new applications, or `inplace`),
`backup.directory` (`/var/backups/noust`), `notifications.*`, `monitor.*`. `NOUST_APPS_DIR`,
`NOUST_WEBSERVER`, `NOUST_SERVICE_USER` and `NOUST_SSL_EMAIL` override the matching keys (the
`WASM_` spellings are still read). The reference configuration is
`/usr/share/noust/config.example.yaml`.

## Files

```
/var/www/apps/<app>/          applications (see Releases above)
/etc/noust/config.yaml        configuration, 0600 in a 0700 directory
/etc/noust/web-*              console state: signing key, token hash, sessions, 2FA, audit log
/var/lib/noust/noust.db       the store: applications, deployments, jobs, releases, domains
/var/lib/noust/               also build logs, and secrets/ for the credentials Noust keeps
/var/backups/noust/           backup archives
/var/log/noust/               Noust's own log files
/etc/systemd/system/          units: <app>.service, noust-web, noust-monitor, noust-cron-*,
                              noust-backup-*, noust-previews
```

On a server upgraded from WASM, `/etc/wasm`, `/var/lib/wasm`, `/var/backups/wasm` and
`/etc/nginx/wasm-upstreams` are symbolic links to their new names. When `/var/lib/noust` is
not writable the store lives in `~/.local/share/noust/`; `noust store path` prints where it
is.

## Requirements

- **Operating system**: Ubuntu 22.04+, Debian 12+, Fedora 40+, openSUSE Leap 15.6+
- **Python**: 3.10 to 3.14
- **Privileges**: root
- **Installed by `noust setup init` when missing**: nginx or Apache, certbot, git, Node.js
- **Per application type**: `python3-venv` for Python, PHP-FPM and its extensions for PHP,
  Docker with the Compose plugin for Compose projects, the engine for databases
  (`noust db install`), rclone for remote backup destinations

---

## Documentation

- [docs/CHANGELOG-3.0.md](docs/CHANGELOG-3.0.md): what changed in 3.0
- [docs/UPGRADING-3.0.md](docs/UPGRADING-3.0.md): upgrading a WASM 2.x server to Noust 3.0
- [docs/CENTRAL.md](docs/CENTRAL.md): running a central, adding a server, sealing its secrets
- [docs/console.md](docs/console.md): the console, page by page
- [docs/releases.md](docs/releases.md): the release layout, health gate, rollback, migration
- [docs/domains.md](docs/domains.md): aliases, redirects, certificates, DNS checks
- [docs/api.md](docs/api.md): authentication, errors, events, WebSockets, endpoints
- [docs/security.md](docs/security.md): threat model, controls, reporting vulnerabilities
- [docs/MONITOR.md](docs/MONITOR.md): the resource monitor
- [docs/UPGRADING-2.0.md](docs/UPGRADING-2.0.md): upgrading WASM from 1.6, and between 2.x
  releases
- Earlier releases, as WASM: [2.3](docs/CHANGELOG-2.3.md), [2.2](docs/CHANGELOG-2.2.md),
  [2.1](docs/CHANGELOG-2.1.md), [2.0](docs/CHANGELOG-2.0.md)
- `man noust`, and `noust <command> --help`

---

## Development

```bash
git clone https://github.com/Perkybeet/noust.git
cd noust
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"

pytest                          # tests
ruff check src/noust tests      # lint
ruff format src/noust tests     # format
mypy                            # types
```

The console's source is in `panel/` (React, TypeScript, Vite; Node 22). Its build is
committed to `src/noust/web/static/`, so packaging never runs Node. See
[CLAUDE.md](CLAUDE.md) for the project's rules and the console workflow.

---

## License

From 2.1.0, Noust (then WASM) is free software under the **GNU Affero General Public License,
version 3 or later** ([LICENSE](LICENSE)). You may use it for anything, commercially
included, study it, change it and share it. If you change Noust and let others use your
changed version over a network (a hosted service, say), you must offer them its source under
the same licence.

Releases up to 2.0.x were published as WASM under the WASM Non-Commercial Source-Available
License 1.0, and stay under it.

Copyright (c) 2024-2026 Yago López Prado. For a licence on other terms (for example to
embed Noust in a closed product), write to yago.lopez.adeje@gmail.com.

## Acknowledgments

- [Certbot](https://certbot.eff.org/) - SSL certificate automation
- The open-source community

---

## Support

- **Issues**: [GitHub Issues](https://github.com/Perkybeet/noust/issues)
- **Email**: yago.lopez.adeje@gmail.com

---

<p align="center">
  Developed by <a href="https://bitbeet.dev">Bitbeet</a>
</p>
