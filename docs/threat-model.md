# Threat model: what Noust defends, and what it does not

Part of [Security](security.md).

## Threat model

**Trusted**

- Root on the machine, and anyone who can run `noust` as root. Noust does not defend the
  machine against its own administrator. The CLI is the emergency channel: it is not held to
  roles, and every command that changes something is audited with the operating-system identity
  that ran it.
- The console's master token: root-equivalent while no account exists, and emergency access
  afterwards.
- Accounts with the `admin` role, and tokens that hold `root_equivalent`: they can write units,
  cron commands and site configuration that run as root, and an application's deploy hooks, code
  that runs with its identity and its secrets on every deploy. [Four-eyes approvals](security-approvals.md#four-eyes-approvals)
  put a second person in front of exactly those changes.

**Defended against**

- **Anyone who can reach the console over the network** without a credential: the console
  listens on loopback by default, refuses cleartext beyond loopback, answers only the host names
  in `web.allowed_hosts` when set, and locks out repeated failures per address and per account.
- **Holders of narrow credentials**: a `viewer` account or a `read` token cannot read secrets or
  other processes' command lines, change anything or cancel jobs; an `operator` can restart,
  deploy and roll back what exists but cannot create an application or name a source for the
  machine to fetch and build; a `security` account governs access and the audit log and
  deploys nothing. Every route states its permission and one chokepoint enforces it.
- **One person acting alone** on root-equivalent changes, when approvals are on.
- **A hijacked browser tab or a malicious page**: CSRF tokens, `SameSite=Strict` cookies, a
  strict Content Security Policy with Trusted Types, and sudo mode before destructive actions.
- **Untrusted repository content**: a repository can contain symlinks, odd file names and
  values that end up in unit files and web server configuration.
- **Install and build scripts**: an application's dependencies install and build as the
  unprivileged `noust-build` account in a sandbox, unable to read Noust's configuration, the
  store, root's home or other applications' `.env` (see [Builds](#builds)). Applications
  deployed before 3.1 are tried in the sandbox on their next update, and covered when that
  passes.
- **Untrusted deploy hooks**: a repository's `noust.yaml` is read with a closed schema, and a hook
  is an argument list, never a shell, that runs as the application's account (or, while the
  application still builds as root, as Noust itself, as its migrations always did) and never
  with more privileges than the compose file already gives its service (see
  [Deploy hooks](#deploy-hooks)).
- **A compromised central**: each server enforces its own ceiling on what any central may do
  there, and a central never gets a shell (see [The fleet](security-roles.md#the-fleet)).
- **Unprivileged local users**: secrets never travel in a command line (which `ps` shows to
  every user), and every file holding one is `0600`.
- **Forged webhooks** and **server-side request forgery** through notification URLs.
- **Tampering with the audit log going unnoticed**: its lines are chained with an HMAC and can
  be shipped off the machine as they are written.

**Not defended against**

- **Teams from each other at run time.** A team is a boundary of permissions and organisation, not of
  execution (see [Teams](security-roles.md#teams)).
- **Applications from each other at run time, where they share an account.** From 3.2 an
  application Noust creates runs as its own system account, `noust-app-<name>`, with no home and
  no shell, owning its tree, `.env` and caches, so one compromised application cannot read
  another's files. An application from before 3.2 keeps the shared account (`service_user`,
  `www-data`) until `noust app identity migrate` moves it, and while it does, it can read the
  files, including the `.env`, of every other application on that account. A monorepo created
  from 4.0 runs all its workspaces' units as one account of its own, and an older one moves with
  the same command. A Docker Compose stack has no account (its processes are its containers'), so
  the compose check keeps it to its own files instead: a bind mount of Noust's configuration and
  secrets, of what contains them (`/`, `/etc`, `/var`) or of another application's tree is
  refused like `privileged` (see [Builds](#builds)). A static site runs nothing; its files are
  served read-only, and once no application shares the account that owns them, none can change
  them. Applications still share the kernel, the network and the machine: Noust is not a
  multi-tenant isolation boundary, and the build sandbox separates builds, not running
  applications.
- **A compromised application reaching the console on loopback.** It still needs a
  credential, but it is on the same host.
- **Anything with root**, including an application built as root because its sandbox was
  disabled (a recorded, per-application decision), a Docker Compose stack with an exception for
  privileged containers, and root rewriting the audit log with the local key; shipping the log
  off the machine is what proves its lines original.

## Processes

Everything Noust executes (nginx, systemctl, certbot, git, npm, database clients) goes through
one module, `CommandRunner`, and the test suite fails if any other module imports
`subprocess`.

- **Argv only, never a shell.** A string where an argument list is expected is refused, and so
  is a NUL byte. A repository or a form field cannot inject shell syntax.
- **Every call has a timeout.**
- **Secrets travel through the environment, standard input or a private defaults file**,
  never the command line. That includes every database password Noust hands to a client.
- **Running as another account** uses one prefix, `runuser -u <user> --`. There is no `sudo`
  in any command Noust runs.
- **Children get a clean environment** for builds and hooks: the runner no longer hands them
  everything its own process inherited.
- `--dry-run` is implemented in the same place: read-only probes still run, everything else is
  recorded and skipped. A command counts as a probe only when its exact argument shape is
  declared as one; anything else is assumed to change something. (3.0 counted a command as
  read-only when any of its arguments was the name of a read-only subcommand, so an argument
  such as `ufw allow 22 comment status` could have passed for a probe.) Files go through one
  seam that a rehearsal swaps out the same way,
  the console's own state included: a rehearsed `noust token create` or `noust sessions revoke`
  reads the real key and session database and writes neither, and on a fresh machine creates
  no `/etc/noust` at all.

Cron jobs follow the same rule: a job's command is split like a POSIX shell would split it
and run without a shell, so pipes, `&&` and globs are inert text. Write `/bin/sh -c "..."`
explicitly when a job needs a shell.

The unit of an application is sandboxed by systemd as well, without a container: a
read-only filesystem but for its own tree, no home directories, no devices, no capabilities
and a system-call filter. New applications get it by default; one that exists is moved with
`noust app hardening`. See [runtime.md](runtime.md#service-security). The files and the mounts
of an application are read and written as its own account, never as root, and a file of the
environment is never opened by either ([runtime.md](runtime.md#files)).

## Builds

Installing dependencies and building run code Noust did not write: the `postinstall` script of a
dependency of a dependency. Up to 3.0 it ran as root. From 3.1 it runs in a transient systemd
unit (`systemd-run --pipe --wait --collect`, through `CommandRunner` like everything else) as
`noust-build`, a system account with no home and no login shell, distinct from the account the
applications run as.

- **What it may write**: its release and its own caches, `/var/cache/noust/build/<app>`, per
  application so one build cannot poison another's. `ProtectSystem=strict`, `ProtectHome`,
  `PrivateTmp`, `NoNewPrivileges`; `/etc/noust`, the store and every other application's
  `.env` are inaccessible; memory and CPU are limited, and a build killed for time or memory
  says so.
- **What it gets**: a clean environment. The application's own `.env` reaches the build through
  `EnvironmentFile=`, which systemd reads as root; `noust-build` cannot open it. A preview's
  build gets no production secrets. With `--network strict`, dependencies install without the
  application's variables and the build runs without a network.
- **What stays outside**: fetching the source (it needs the deploy key and git credentials) runs
  as root and exports the release without `.git`. Migrations run as the application, as its
  unit would.
- **Fail closed.** Before the first sandboxed build, a canary run as `noust-build` must write its
  own directory and must fail to write outside it, into `/root`, or to read a file placed in
  `/etc/noust`. Containers and WSL without mount namespaces ignore `ProtectSystem` silently; the
  self-test is what notices, and the build stops rather than running as root.
- **Scope.** Applications and previews created from 3.1 on build in the sandbox. From 3.2,
  the next update of an application that still builds as root first builds that commit in the
  sandbox, once: when it passes the sandbox is turned on, and when it fails the update builds as
  root as before, flagged by `noust health` and `noust ens check` and by a notification, until
  `noust app sandbox test` passes and `noust app sandbox enable` is run. An in-place application
  builds in its live tree as the service account, still with the rest of the machine hidden.
  Building one application as root is a per-application decision recorded with a name and a
  reason (`noust app sandbox disable DOMAIN --reason ...`, permission `root_equivalent`).
- **Docker Compose** builds run in the Docker daemon, outside any sandbox Noust controls. A new
  stack is refused `privileged: true`, a mounted Docker socket and a bind mount of Noust's own
  state or of another application, all of which are root on the host or the secrets of
  everything on it, unless the application has a recorded exception. The mounts are judged after
  `..` is resolved (`/var/www/apps/shop/../other` and `../other` are the other application's
  directory), the long and the short syntax alike. A stack proven to run with one already is
  warned about and never stopped.

## Deploy hooks

A project can run commands around its own deploy (`hooks.pre_deploy` and `hooks.post_deploy`, in
a `noust.yaml` at the root of the repository, or set by the operator for one application). A hook
is code that runs on every deploy with the application's identity and secrets, so it is treated
as such:

- **A repository's `noust.yaml` is untrusted input.** The schema is closed (an unknown key is
  refused, not ignored, and names the field), a path that climbs with `..` is refused, an absolute
  working directory is accepted only inside a Compose service's container, a link instead of a
  file is refused unread, and the file is at most 64 KiB. It cannot choose a user, `privileged` or
  a mount for a hook.
- **A hook is an argument list.** `run` is split with `shlex` and run through `CommandRunner`:
  never a shell, always with a timeout (1 to 3600 seconds).
- **Where it runs bounds what it can do.** In a Docker Compose stack, in a one-off container of
  the image the deploy built, with the service's own environment, networks and volumes, and the
  privileges the compose file already gives that service (which the compose file check watches).
  In every other application, in the release phase of the build: as the application's account,
  with its `.env`, in a transient unit, never as `noust-build`. An application that still builds
  as root (an explicit decision, or one whose sandbox trial has not passed) runs its hooks as
  Noust itself, as root, exactly as its migrations always ran: a further reason to put every
  application in the sandbox.
- **The operator's hooks need `root_equivalent`**, sudo mode and, when approvals are on, a second
  person, and replace the repository's whole: two sources are never merged, so what runs is
  always one document someone wrote. Every change is audited (`apps.hooks`).
- **A hook's output is kept**, in the deployment's log and, the last 4000 characters of each, in
  its history, like any build output: a hook should not print a secret.

## The build block and the runtimes it pins

`build:` in a `noust.yaml` is the same kind of input as a hook, and is held to the same rules
(see [build.md](build.md)):

- **The schema is closed.** An unknown key, a path with `..`, an absolute path, `~` or a name a
  unit or a site could not carry is refused, naming the field; a link on the way to the base
  directory is never followed. A command is an argument list split with `shlex`, never a shell.
- **Where its commands run.** `install` and `build` run in the build sandbox as `noust-build`
  with the whole release writable and nothing else of the machine; `start` is the unit's
  `ExecStart=`, as the application's account. A runtime is run by absolute path, and the
  directory it is installed in is root's and cannot be written by a build.
- **The operator's block needs `root_equivalent`** and sudo mode, replaces the repository's
  whole, and every change is audited (`apps.build`).
- **A runtime is downloaded once, verified, and proved.** Node comes from nodejs.org as the
  official tarball, its SHA-256 compared with the release's published `SHASUMS256.txt` (fetched
  over HTTPS from the same origin; the release signature is not verified) before a member is
  unpacked, unpacked with the checks every archive gets (nothing outside the destination, no
  devices, no setuid), and run once to prove its version before it is put in place. Python comes
  from `uv`, PHP from the distribution's packages. Installing and removing runtimes is audited
  (`runtimes.install`, `runtimes.remove`).
- **A hint is not an order.** The version files of a repository (`.nvmrc`, `engines.node`,
  `.python-version`...) may make Noust install a runtime; a failed install never fails a
  deployment that the server's own runtime can build.

## Secrets via --stdin

The same rule applies one level up, to how an operator runs `noust` itself: a value typed after
`noust config set` is written to the shell's history file and, for as long as the process runs,
visible to every local user through `ps`. That is fine for `apps_directory`; it is not fine for
`monitor.smtp.password` or a webhook URL.

```bash
# Avoid - lands in shell history and in ps
noust config set monitor.smtp.password 'hunter2'

# Read from standard input instead - a single trailing newline is stripped
printf '%s' "$SMTP_PASSWORD" | noust config set monitor.smtp.password --stdin

# Or ask for it interactively, without echoing it back
noust config set monitor.smtp.password --prompt
```

`--stdin` and `--prompt` are mutually exclusive with giving `VALUE` on the command line, and
with each other; `noust config set KEY` with none of the three is a usage error. Typing a value
for a key `noust.core.config.redact_secrets` would redact (`password`, `token`, `key`,
`webhook`, `secret`, `credential`, `auth`, and their compounds) straight into `VALUE` on a real
terminal prints a warning suggesting `--stdin`, and is otherwise accepted: the warning is a
nudge, never a refusal, so a script that has already taken the value out of argv some other way
does not have to be rewritten.

## What the scrubbing does and does not do

Build output, job logs and failure messages are what the tools printed, and the tools run with the
application's environment: a script that echoes `$API_KEY`, a framework that prints its configuration
when it fails, a connection error that quotes the whole `DATABASE_URL`. That text is kept in the
deployment history and the job logs, readable with a far narrower credential than the `.env` it came
from. `noust.core.redact` replaces what it knows is secret with `***` wherever that text is stored or
shown: the values of the application's secret variables, the credentials Noust itself holds, the
password in a URL, and the command line of a process the monitor lists.

It works by value, not by shape, and that is its limit, stated so nobody relies on more:

- **A secret it was not told about is not recognised.** A token a build downloads and prints is
  invisible to it. Mark a variable secret (`noust env mark`), and keep secrets out of a hook's output.
- **A value shorter than six characters is left alone.** A secret variable set to `true` would otherwise
  blank out every ordinary word containing it. A value that short protects nothing worth that.
- **It covers what Noust persists or publishes**, not what a tool writes to a file inside the
  application's own tree.

[SECURITY-MODEL.md](SECURITY-MODEL.md) lists it among what Noust does not guarantee, with the tests
that hold it to what it does.

## Outbound requests

- **Notifications** (webhook, Slack, Discord, Telegram) refuse destinations in loopback,
  private (RFC 1918), carrier-grade NAT, link-local (which covers cloud metadata at
  `169.254.169.254`), the unspecified address (`0.0.0.0/8`, `::`) and their IPv6 equivalents.
  An IPv6 address that carries an IPv4 one - IPv4-mapped (`::ffff:0:0/96`), NAT64
  (`64:ff9b::/96`), 6to4 (`2002::/16`) - is judged by the IPv4 address inside it, so
  `::ffff:127.0.0.1` is loopback like `127.0.0.1`. Every redirect hop is checked again, so a
  public name that redirects or rebinds to a private address is still refused. Names you
  trust can be exempted with `notifications.allow_private_hosts`. Only `http` and `https` are
  accepted, and the "test" button never echoes the remote response back.
- **Backup destinations and source archives use the same guard** (`noust.core.outbound`, one
  implementation for every feature that sends a request to a host somebody typed). A backup
  destination's host (the `host` of SFTP and SMB, the `url` of WebDAV, the `endpoint` of S3) is
  judged when the destination is made, changed or tested: one that resolves to loopback, a private
  or carrier-grade-NAT network, link-local or the unspecified address is refused unless it is named
  in `security.allow_private_hosts` (`noust config set security.allow_private_hosts nas.lan --list`),
  so a NAS or a MinIO on your own network is a decision and not a default. Every later use, which
  is every scheduled backup, refuses only what can never be a destination (the cloud metadata
  service, `0.0.0.0/8`, `::`, link-local), so a destination made before 4.0 on your LAN keeps
  working until you test it. A source archive URL is judged the same way before the download and
  on every redirect hop. Names that do not resolve yet are accepted when configured and judged
  again when used. The guard compares the address a name resolves to when it asks; rclone and the
  download resolve again themselves, which is why the narrow look at every use exists and why this
  is a second line behind the permission to configure backups (`admin`), not a replacement for it.
- **The stored SMTP password stays with its server.** The console never receives it, so a
  write that does not name it keeps it: a blank password on `PUT /api/config/smtp`, `***`
  echoed back to `PUT /api/config`, a `PATCH /api/config` of another key. That holds only
  while the host, port, username and transport (`use_ssl`, `use_tls`) stay as they are;
  changing any of them without entering the password again is refused with `422` on
  `password`, so a credential that may write the settings cannot point the password at a
  server of its own and send itself a test email.
- **A Telegram bot token** must be `<digits>:<secret>` from its first character to its last,
  a trailing line break included; any other shape is refused on save (`422` on `bot_token`)
  and again before it becomes part of a Bot API URL.
- **Deploy webhooks** (`POST /hooks/deploy/{domain}`) verify `X-Hub-Signature-256` (GitHub),
  `X-Gitea-Signature` (Gitea) or `X-Hub-Signature: sha256=...` (Bitbucket Cloud) as HMAC-SHA256
  of the body, or `X-Gitlab-Token` (GitLab),
  all in constant time. An unknown application and one without a webhook secret look the
  same from outside. A bad signature is counted against that application, not the address:
  after 10 in 15 minutes its hook answers `429` (`"error": "locked_out"`, with `Retry-After`)
  for 15 minutes, while every other application's deliveries keep arriving. A forge sends
  every customer's deliveries from a few shared addresses, so counting against the address
  let anyone with an account on the same forge lock it out of the console. Deliveries larger
  than 5 MiB are refused with `413`. The per-application secret is stored in the Noust store
  (`0600`), because verifying an HMAC needs it.

What Noust itself sends off the machine: git fetches from your repositories, certbot's
requests to Let's Encrypt, notifications you configured, and, on every CLI run whose cached
answer is older than five minutes, an update check over HTTPS: the package repository this
server installs from (apt, dnf, zypper, or PyPI for pip) for the version it can install, and
`api.github.com` for the latest published release. When a release newer than the one running
fixes a vulnerability, the check says so apart from an ordinary update: a release whose GitHub
title ends in `(security release)` (written by the release workflow when a changelog bullet starts
with `SECURITY:`) is shown in the CLI banner, in `noust health`, in `GET /api/system/version` and in
Settings > About Noust, with the advisory its notes name. The list of recent releases is read only when a
newer release exists at all. `noust config set updates.check false` turns it off. A server can also
be told to install what the check finds by itself, inside a window, for the releases you name and never a
new major version (`noust self-update`, see [release-policy.md](release-policy.md#updating-by-itself)); it
is off by default and its runs are in the audit trail.

## Untrusted repositories

- Nothing is written through a symlink found inside a release or under `shared/`. A tracked
  link to `/etc` does not become a path Noust writes to as root.
- Inspecting a source reads its example environment files (`.env.example`, `.env.template`,
  `.env.sample`, at the root and under `apps/*`, `packages/*` and `services/*`) without
  following a symlink: each file is opened with `O_NOFOLLOW` and must be a regular file, and
  a linked workspace directory is not entered, so a link to `/etc/shadow` or another
  application's `.env` is not read. An inspection never returns the default of a variable
  whose name marks it as a secret, or whose default carries a password inside a URL.
- Persistent paths must be relative and may not contain `..`.
- Every value interpolated into a systemd unit (the start command, the working directory,
  environment variables) is validated and escaped: a newline cannot start a new directive.
- Release ids are validated before they reach the disk, and a commit id must be hexadecimal
  before it becomes part of a directory name.
- `ServiceManager` refuses to touch a unit that Noust does not own, whatever the caller.
- A repository's `noust.yaml` and `noust.nginx.yaml` are read with closed schemas: an unknown key
  is refused, and the values of a route (its path, sizes, timeouts, rates and redirect target) are
  checked to be exactly what their directive takes. A `static` route stays inside the application.
  `custom_directives` in `noust.nginx.yaml` is the exception: its lines go into the site's
  `server` block as written, behind only the web server's own test, so read it as you would read
  a site before deploying a repository you do not control.
- A web server site that someone else wrote is never rewritten or deleted by a deploy: Noust
  recognises its own sites by their marker, and leaves any other file alone.
- The servers files of a Compose relay (`/etc/nginx/noust-upstreams/<app>/<service>.servers`)
  hold only `server 127.0.0.1:<port>;` lines, are written atomically, never through a link, and
  names that become their path are validated as a single inert component.

## Files

| Path | Mode | Holds |
|---|---|---|
| `/etc/noust/` | `0700` | Configuration and the console's state |
| `/etc/noust/config.yaml` | `0600` | Configuration; the notification channels' secrets are encrypted in it. `noust config get` and the API print secrets as `***`; `noust config show` prints them in clear |
| `/etc/noust/secrets.key` | `0600` | The key of the encrypted secrets (the channels' in `config.yaml`, the backup destinations'). Keep it with the backup of `/etc/noust`: without it those secrets are entered again |
| `/etc/noust/web-secret` | `0600` | Signing key for sessions and token hashes |
| `/etc/noust/web-token` | `0600` | Hash of the master token |
| `/etc/noust/web-totp` | `0600` | TOTP secret and backup code hashes |
| `/etc/noust/web-sessions.db` | `0600` | Sessions, API token hashes, WebSocket tickets |
| `/etc/noust/web-audit.log`, and its closed files `web-audit.log.<UTC stamp>` | `0600` | Audit trail, chained |
| `/etc/noust/audit-key` | `0600` | HMAC key of the audit chain; never shipped |
| The store, under `/var/lib/noust/` (`noust store path` names the file) | `0600` | The store: applications, deployments, jobs, webhook secrets, accounts (TOTP secrets encrypted) |
| `totp.key` beside the store | `0600` | Key the accounts' TOTP secrets are encrypted under; never in the store itself |
| `secrets/backups/sidecar-mac-key` beside the store | `0600` | Key that signs every backup's metadata sidecar |
| `incidents/` beside the store | `0700`, files `0600` | Evidence packages of `noust incident freeze` |
| `incident-lockdown.json` beside the store | `0600` | Present while the console is locked down for an incident |
| `ens-reports/` beside the store | `0700`, files `0600` | Evidence bundles of `noust ens report` |
| `/var/lib/noust/job-logs/` | `0700`, files `0600` | Output of console jobs |
| `/var/lib/noust/deploy-logs/` | `0750`, files `0640` | Build logs, readable by an admin group |
| `/var/cache/noust/build/<app>/` | owned by `noust-build` | An application's build caches (npm, pip, ...), one directory per application |
| `/run/noust/sandbox/` | root | Scratch space of the build sandbox and its self-test |
| `.env`, `shared/.env` | `0600` | Application environment, owned by the application's account (the service account for one from before 3.2) |
| `/etc/nginx/noust-upstreams/<app>/` | root | The servers files a Compose relay switches while a service is recreated |
| `/etc/systemd/system/*.service` | `0644` | Units. See the note below. |

The store falls back to `~/.local/share/noust/` when `/var/lib/noust` is not writable; `noust
store path` prints where it is. The console state directory can be moved with
`NOUST_WEB_STATE_DIR`.

Environment variables given when an application is created (`noust create --env-file`, or
`env_vars` in `POST /api/apps`) are written into its `.env` file (`0600`, owned by the
application's account, `shared/.env` on the releases layout), and the unit loads it with
`EnvironmentFile=`. Only `PORT` and `NODE_ENV` - not secret, and Noust's to decide - stay
inline in the unit as `Environment=` lines. systemd lets `EnvironmentFile=` override
`Environment=`, so `noust env configure` and the console's Environment tab both refuse to set
either one there; change the port by redeploying (`noust create -d <domain> -s <source>
--port <port>`, or `POST /api/apps` with the new port) instead. A unit an earlier
version wrote, with every variable inline, keeps working as it is; the next `noust update` or
redeploy moves them into the `.env` file.

## The monitor

`noust monitor` reports what it sees and does nothing else: it never signals or kills a
process, never deletes or modifies a file other than its own unit, and never decides anything
from a process's command line. Each minute it keeps the busiest processes, with their command
lines redacted, for the timeline of a stretch of time; the redacted line is shown only to who may
read command lines, and is kept for 26 hours. See [MONITOR.md](MONITOR.md).
