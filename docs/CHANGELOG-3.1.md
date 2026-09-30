# Noust 3.1 changelog

Changes since Noust 3.0.0. Upgrade notes are in [UPGRADING-3.1.md](UPGRADING-3.1.md): read
them before upgrading a server, and in particular before upgrading a fleet.

3.1 does not break what an operator already uses. API tokens keep working, a server enrolled in
a fleet by 3.0 keeps working until you move it to the new tunnel account, and an existing
application keeps building the way it did until you switch it to the build sandbox. What changes
without asking is listed under [What changes on its own](UPGRADING-3.1.md#what-changes-on-its-own).

## Accounts, roles and passkeys

Up to 3.0 the console had one credential, the master access token, and every session signed in
with it was root. 3.1 has named accounts, each with one role.

- **Five roles.** `viewer` sees everything with secrets redacted and changes nothing.
  `operator` also restarts, deploys, rolls back, renews certificates and runs backups. `admin`
  also creates, configures and deletes, reads secrets and makes root-equivalent changes; it
  does not manage accounts, security settings or the audit log. `security` manages accounts,
  roles, security settings and the audit log and deploys nothing. `auditor` reads everything,
  the account list and the whole audit log included, and changes nothing.
- **Separation of duties.** One person (`--person-ref`, an e-mail) may not hold `admin` or
  `operator` together with `security`, nor `auditor` with anything else. A small installation
  with a single responsible person records a documented exception with a reason and an end
  date: `noust user exception add`.
- **Permissions at the chokepoint.** Every API route declares the permission it needs
  (`apps.read`, `apps.deploy`, `root_equivalent`, `server.host_access`, `audit.read`...), the
  authentication layer enforces it on every request, and the OpenAPI schema publishes it as
  `x-noust-permission`. A route without one fails the test suite and is refused at runtime.
  Sudo mode still applies on top of the permission.
- **The first account.** On a server with no accounts, Noust behaves exactly as 3.0 did. Create
  the first `admin` with `noust user create NAME --role admin` or from the console, which opens
  a "Create the first account" page when you sign in with the access token. It also offers a
  `security` account, recommended for a different person.
- **The master token becomes emergency access.** Once an account exists, the access token
  still signs in (break-glass), asks for its second factor, and every use of it is audited.
  Under the `ens-medium` profile it can only recover access to accounts.
- **API tokens belong to an account.** A token holds at most its owner's permissions, can be
  narrowed further and limited to network ranges (`allowed_cidrs`), and a token issued from 3.1
  on cannot act where sudo mode is asked unless it was issued with `allow_elevated` (never under
  the `ens-medium` profile, where tokens also expire within 90 days). Tokens issued by 3.0 keep
  their scope and their 3.0 behaviour, and are adopted by the first `admin` account.
  `noust token create NAME --owner USERNAME`.
- **Sessions** end after 30 minutes without activity and 12 hours in total (`auth.session.*`;
  3.0 allowed 12 and 24 hours). After signing in, the console shows the last sign-in and the
  failures since. Five failed sign-ins lock the account for 15 minutes, on top of the lockout
  per address. The sign-in page no longer shows the host name, the version or whether a second
  factor is on before you authenticate; `auth.login_label` sets what it shows instead.
- **A notice of rights and obligations** (`auth.notice.text`) can be shown after sign-in and its
  acceptance is recorded.
- **Invitations.** `noust user invite NAME --role ROLE` prints a one-use code valid for 24 hours;
  the person sets their own password and second factor. An invitation for an existing account
  is a recovery.
- **Every account enrols a second factor** (an authenticator app or a passkey) before it can do
  anything else.
- **Passkeys.** WebAuthn, implemented in Noust (ES256 and RS256, and EdDSA when the installed
  `cryptography` supports it), with user verification required and several passkeys per
  account. A passkey signs in on its own and confirms sudo mode. The master token can have
  passkeys too; once it has one, the token alone no longer signs in. The first passkey comes
  with backup codes. Browsers only offer passkeys on a certificate they trust, and the console
  says so when it is not. Passkeys need the `cryptography` Python package (see
  [UPGRADING-3.1.md](UPGRADING-3.1.md#passkeys-need-cryptography)).
- **CLI:** `noust user list|create|invite|set-role|disable|enable|unlock|reset-mfa|remove|exception`,
  `noust passkey list|remove|reset`. Passwords are read at a hidden prompt or with `--stdin`,
  never from argv.

## Audit and ENS

- **One audit log, a closed catalogue.** Every event comes from one catalogue (`noust audit
  events` lists it) with the actor, their role, where it came from, the target, the outcome and
  a correlation id. Sensitive reads are recorded too: revealing an `.env`, exporting with
  secrets, reading the audit log.
- **Integrity.** Each line is chained with HMAC-SHA256 under a key of its own, with periodic
  checkpoints. `noust audit verify` reports the first broken link. Root on the machine holds
  the key and could rewrite a consistent chain, which is why the log is also shipped off it.
- **Shipping.** To journald wherever it exists, to syslog receivers in RFC 5424 over a Unix
  socket, UDP, TCP or TLS (with an optional client certificate), with an on-disk queue and
  retries, and to standard output in a central's container (`audit.*`).
- **Retention.** 365 days by default (`audit.retention_days`, at least 90), and nothing is
  deleted before every destination received it. A burst of identical events is collapsed. A
  failing audit trail shows in the console and in `noust health`. Jobs, deployment records and
  ended sessions have their own retention (`retention.*`).
- **The CLI is audited.** Every command that changes something records the operating-system
  identity (`SUDO_USER`, `loginuid`), its sanitised arguments and its outcome. `--reason` says
  why (a ticket, a change reference); the `ens-medium` profile requires it.
- **The host action ledger.** Every process Noust runs and every file it writes that changes
  the system is recorded and linked to the event that caused it (`audit.host_activity`).
- **Four-eyes approvals.** With `approval.enabled` (or under the `ens-medium` profile),
  root-equivalent changes (raw units, cron commands, backup hooks, raw site configuration),
  SQL that writes, deploying from a directory on the server, adding and removing servers, and
  role grants become requests another person approves: `security` by default, an `admin` other
  than the requester for infrastructure if `approval.approvers` allows it. The approved call
  runs once, and both people are on record. `noust approval list|show|approve|reject`, and
  Settings > Approvals.
- **The `ens-medium` profile** (`security.profile`) caps sessions, lockout, password length,
  token lifetimes, audit retention, approvals, `--reason` and backup encryption at the values
  of Spain's Esquema Nacional de Seguridad, category MEDIUM.
- **Evidence.** `noust ens check` compares the server with the profile, measure by measure
  (exit 0, 1 or 2 for a monitoring system). `noust ens report` writes the evidence bundle for
  an auditor, hashed into the audit log. `noust ens access-review` and `noust audit review`
  record the periodic reviews; `noust ens inventory` keeps each application's owner,
  criticality and classification. The console has them under Settings > Compliance.
- **Incidents.** `noust incident freeze` takes an owner-only evidence package with a SHA-256
  manifest and locks the console to the master token until `noust incident unfreeze`.
- **Backups** carry a MAC in their metadata, a scheduled backup is deep-verified at least every
  `backup.verify_days`, and under the profile nothing goes to an unencrypted destination.
- **TOTP secrets are encrypted at rest**, under a key kept beside the store and never in it.
- **Allowed code origins** per server (`security.allowed_sources`), `reason` on deploys, and
  previews built without production secrets.
- [ENS.md](ENS.md) has the compliance matrix, the hardening guide and what stays the
  organisation's.

## The server

The Server page and `noust server` manage the machine itself, not only what Noust deployed on
it. Every change runs as a job with its output verbatim, needs sudo mode and is audited.

- **Updates.** Pending updates with the security ones first; apply all or only security in their
  own transient systemd unit, so a console restart during the upgrade does not cut it; refresh
  the package metadata on demand; "a reboot is due" with the reason, and the services left
  running on replaced libraries, which can be restarted; the distribution's own automatic
  updates (unattended-upgrades, dnf-automatic; transactional openSUSE shows its state only).
  A full upgrade lists what it would remove before it removes anything. Nothing reboots the
  server because an update finished. `noust server updates list|refresh|apply|auto|history|restart-services|repair`.
- **Reboot and shutdown**, now or scheduled, after checks of what they would interrupt, with an
  event when the server is back: `noust server reboot`, `noust server shutdown`.
- **Security.** Hardening checks, each with its reason, the system's own words and either a safe
  automatic fix or the exact steps; "accept the risk" with a reason and an end date, shown as
  accepted and never as passed. The same checks feed `noust health` and `noust ens check`.
  `noust server security checks|fix|accept|unaccept|risks`.
- **SSH.** sshd's effective configuration (`sshd -T -C`), the administrators' keys with their
  last use, and fixes applied safely: Noust's own drop-in `00-noust.conf`, proof that another
  way in exists, `sshd -t`, a reload, and an automatic revert after 120 seconds unless you
  confirm from a new login. `noust server security ssh status|keys|add-key|remove-key|plan|harden`,
  `noust server security confirm|revert|pending`.
- **Firewall.** ufw or firewalld compared with the ports that really answer, a warning when
  Docker publishes ports past the firewall, and a guard that never closes SSH or a public
  console. Changes revert unless confirmed. `noust server security firewall status|allow|deny|delete|enable|disable`.
- **fail2ban**: status, bans, unbanning, and installing it with a jail that never bans the SSH
  sessions open now (EPEL on RHEL rebuilds only with `--epel`).
- **Storage.** The real filesystems without bind mounts, what takes their space, a closed list
  of clean-ups (journal, package cache, old releases, Docker build cache and dangling images;
  never Docker volumes) and swap. `noust server storage|cleanup|swap`.
- **System.** Time zone and synchronisation, host name, the operating system and its end of
  life (the table ships with Noust; no network), processes by unit, the journal of any unit.
  `noust server status|time|hostname|processes|logs`.
- **Database ports open beyond the machine** are reported here and on the Databases page.
- **Services** moved under Server; `/services` redirects to it.

## The fleet

- **Fleet views.** One aggregator behind `/api/fleet/*`, `noust fleet status` and the console's
  Fleet page asks every server in parallel, each with its own deadline, and answers with what
  it has: a server that is down, too old or refuses the operator is one row with its state and
  its own words, never an error for the whole page. A cache serves the last good answer, with
  its age, when a server cannot be reached. Views: summary, servers, applications,
  certificates, backups, updates and activity. `noust fleet apps|certs|backups|updates|activity`.
- **Bulk actions.** `noust fleet run ACTION` (and the Fleet page) renews certificates, backs up,
  verifies backups, updates or restarts applications, updates Noust or the operating system on
  several servers at once: chosen by name or by label (`noust node label vps1 env=prod`), shown
  as a plan first, in series or in batches (`--serial`), with a canary, a failure threshold and
  `noust fleet retry` for the servers that failed. Servers that cannot run the action are
  skipped with the reason. `noust fleet jobs` shows each server's outcome.
- **The tunnel account.** `noust fleet authorize` now installs the central's key for
  `noust-tunnel`, an account with no home, no shell and no password, restricted by sshd itself
  to forwarding the console port (see the security fix below). The server no longer needs
  `PermitRootLogin` for the fleet. `--ssh-user root` still works with `--i-understand`.
  `noust node migrate-tunnel` moves a server enrolled by 3.0.
- **A ceiling per server.** Each server decides the most any central may do there, and enforces
  it itself: `read`, `deploy` or `admin` (the default, as in 3.0), and `host access` (off by
  default), which a central with `admin` also needs to change SSH, the firewall, system accounts
  or to make root-equivalent changes. `noust fleet authorize --access ... --allow-host-access`,
  `noust fleet access`. The server publishes it and the central greys out what it would refuse.
- **Roles across the fleet.** The central forwards the operator's identity and role
  (`X-Noust-Actor`, `X-Noust-Actor-Role`); the server grants the intersection of its ceiling and
  what its own table gives that role. A central's approvals are forwarded as its word
  (`X-Noust-Approval`, `X-Noust-Approved-By`) and a server that requires approvals refuses a
  call without them.
- **Settings by scope.** General, Notifications, Integrations and About follow the selected
  server; Servers, Accounts, Security, API tokens, Approvals, Audit log and Compliance belong to
  the central and say so. A central still cannot change a server's own accounts, tokens or
  second factor.
- **Knowing where you are.** The console shows three contexts: one server, all servers (the
  Fleet pages) and this central. It remembers the last server, and every page shows the
  server's name above its title when there is a fleet.
- **`authorize` is gentler.** It adopts a console you started in the background (moving it to
  the service on the same port, and putting it back if the service fails), shows its progress,
  keeps your access token, and refuses to run on the central that issued the key.
- **Key rotation**: `noust node rekey`. **Pinned SSH algorithms**: ed25519 host keys,
  `sntrup761x25519-sha512@openssh.com` or `curve25519-sha256`, ChaCha20-Poly1305 or AES-GCM.
- **Fleet notifications**: a server unreachable, recovered, or its host key changed.
- **`noust central backup`** writes the central's configuration, store, secrets and keys into one
  file encrypted under a passphrase you type; `--verify` and `--decrypt` check and open it.
- **The central's self-signed certificate** is ECDSA P-256 with names (host name, addresses,
  `NOUST_TLS_NAMES`), replaced once on the first start of 3.1. TLS 1.2 or later with AEAD
  suites only.
- The "Add a server" dialog: its buttons work at every step, it checks that what you paste is a
  join code (`noust-join:v1:`), and it says plainly that step 1 runs on the other server.

## Builds without root

Until 3.0, installing dependencies and building ran as root: any `postinstall` script of any
dependency could read `/root/.ssh`, the console's token, the store and every other
application's `.env`.

- **The sandbox.** Install, build and a deployer's hooks run in a transient systemd unit as the
  `noust-build` account, with `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
  `NoNewPrivileges`, the configuration, the store and other applications' `.env` files out of
  reach, a clean environment and memory and CPU limits. It may write its release and its own
  cache in `/var/cache/noust/build/<app>`, and nothing else. Fetching the source stays root and
  outside the sandbox; migrations run with the application's own identity, as its unit would.
- **Fail closed.** A self-test proves the sandbox holds on this server before the first
  sandboxed build (`noust app sandbox self-test`). Where it does not (containers and WSL without
  mount namespaces), the build stops with the evidence; it never falls back to root.
- **Who builds in it.** Applications and previews created from 3.1 on. Existing applications
  keep building as before, with a warning on their page, in `noust health` and in `noust ens
  check`, until you try the sandbox with a build of the current commit (`noust app sandbox
  test`) and turn it on (`noust app sandbox enable`). It becomes the default for every
  application in 3.2.
- **Building as root** is an explicit decision per application, recorded with your name and a
  reason: `noust app sandbox disable DOMAIN --reason '...'`.
- **A strict network profile** (`--network strict`): dependencies install with the network and
  without the application's variables, and the build runs without a network.
- **Previews** build without production secrets.
- **Docker Compose**: a new stack with `privileged` containers or the Docker socket mounted is
  refused unless the application has an exception (`noust app sandbox compose-exception`);
  existing stacks are warned.
- A build that times out or runs out of memory says so, and cancelling a build stops its unit.
- The console: application > Settings > Builds.

## Deploys

- **Branch pinning.** `noust app branch DOMAIN BRANCH` pins the branch an application deploys
  from; `--unpin` removes it. A successful `noust update --branch X` pins X. With a branch
  pinned, every update builds it and the webhook ignores pushes to any other branch. Without a
  pin, an in-place application follows the branch its checkout is on, as before, and an
  application on releases follows its recorded branch; a one-off `update --branch` in 3.0 could
  leave the repository cache on another branch, and the next plain update or webhook switched
  production to it.
- **The deploy webhook as a guided setup** in the console (application > Settings > Deploy on
  push): the public URL, the secret, the values to enter at the forge, the branch that deploys
  (and the warning when none is pinned) and the last deliveries, ignored ones included.
  `noust app webhook show|rotate|disable|deliveries`.
- **Instant rollback**, the new name of "releases" in the console, explained by what it gives
  you before what it changes on disk.
- App units carry `MemoryAccounting=` and `CPUAccounting=`, so per-application metrics have
  something to read.

## Databases

- **One model.** The CLI and the API go through the same service and the store is the record,
  which fixes backups made from one front end that the other could not see. `noust db adopt`
  tracks databases created outside Noust; `noust db forget` drops the record of one that is gone.
- **Per application.** `noust db provision DOMAIN -e ENGINE` creates a database and an account
  and writes the connection string into the application's environment; `noust db link`,
  `unlink` and `links` do the rest. Rotating a password (`noust db user-password`) updates the
  applications that use it behind the health gate. The application page has a Database tab.
- **A page per database**: overview, data, query, backups, users, connect and metrics.
- **Browse data** read-only (`noust db tables|describe|rows`), with the read-only mode enforced
  by the database server; Redis and Valkey keys (`noust db keys|key`). Editing one row, found by
  its primary key, needs sudo mode (`noust db row insert|update|delete`).
- **The SQL console**: history, saved statements, export, `EXPLAIN` and a time limit
  (`noust db history|saved|export|explain`).
- **Backups** as jobs, never a synchronous request: policies with a schedule, retention,
  verification and remote destinations (`noust db backup-schedule set`, timers
  `noust-backup-db-<engine>-<database>`), PostgreSQL's custom format for new dumps, and
  restores that never lose data: `--as-new` restores into a new database, and restoring over
  one takes a safety copy first and puts it back if the restore fails
  (`databases.safety_copies_kept`).
- **Users** with profiles (`owner`, `read_write`, `read_only`).
- **Metrics**: size, largest tables, connections, and slow queries where the engine exposes them
  (`noust db metrics`, `noust db slow-queries`).
- **Connect** from your computer through an SSH tunnel (`noust db connect-info`); database ports
  open beyond the machine are reported (`noust db exposure`).
- **Engine fixes.** Connection URLs encode passwords; a PostgreSQL 15+ database gets its
  application as owner (`noust db fix-owner` changes an existing one, after showing the plan);
  Redis gets `requirepass`; new MongoDB installations have authentication on, and existing
  ones are warned that their users protect nothing. `noust db install` always starts and
  enables the engine. Engine versions are the distribution's, with their end of life shown.

## Metrics and the Overview

- **History recorded by the monitor.** The collector moved from the console to `noust-monitor`,
  so the charts' history exists whether or not a console runs. Tiers of 5 s, 1 minute, 10
  minutes and 1 hour, each with the mean and the maximum; the hourly tier is kept 400 days
  (`metrics.retention_days`). The package installs and enables the monitor unless you turned it
  off; see [MONITOR.md](MONITOR.md).
- **Per-application metrics for every kind of unit**: legacy `wasm-*` units, blue/green
  instances, monorepos, Docker Compose containers, PHP-FPM pools, and requests and errors per
  minute for static sites from the web server's access log. When there is nothing to show, the
  chart says why and how to fix it.
- **Charts** keep the window you asked for with gaps as gaps, show the value beside the cursor
  with a crosshair shared across the grid, work from the keyboard, and open larger with a range
  selector and drag-to-zoom. `GET /api/metrics/query` answers several series in one request.
- **The Overview** answers "how is this server": six key figures (applications running and
  failed, deploys today, certificates due, backups in the last 24 hours, disk, pending system
  updates), what needs attention first, recent activity as a timeline, charts and quick
  actions. On a central, the fleet summary. `GET /api/overview`.

## Notifications

- **Rewritten per channel**: Telegram HTML with a link button, Slack Block Kit with a coloured
  bar, Discord embeds, multipart e-mail with an HTML table and the Noust mark, and the generic
  webhook as additive JSON (`version: 1`) with an optional HMAC signature
  (`notifications.channels.webhook.secret`, `X-Noust-Signature`). Text glyphs, no emoji. Every
  message names its server (`server.name`).
- **Reliable limits**: a message cut to a channel's limit never loses its link.
- **New and corrected events**: `restore_success`, `restore_failed`, an expired certificate,
  recoveries, `backup_success` (off by default), `server_rebooted`, `server_back`,
  `approval_requested`, `approval_decided`, and on a central `node_unreachable`,
  `node_recovered` and `node_host_key_changed`. Deduplication rules are tested.

## The console and its design system

- **A design system**, written down in [DESIGN.md](DESIGN.md) and enforced by tests: colour only
  for state (green running, amber in progress, red failed, grey stopped) plus violet for what
  you can act on and a separate palette for chart series; one type scale with named roles;
  seven page templates (list, detail with tabs, settings, dashboard, wizard, file editor,
  access); one component for each pattern. A gallery in the development build shows every
  piece in both themes.
- **Every page rebuilt on it.** The Overview; Applications with rows that become cards on a
  phone; an application's settings as a page per subsection (General, Deploys, Deploy on push,
  Builds, Resources, Previews, Export, Delete); Backups with tabs for backups, schedules and
  destinations; the Server page with seven tabs; Databases; the Fleet with its tabs; Settings
  with a side navigation and a save bar.
- **Plain words.** About sixty texts rewritten in English and Spanish so that a term of Noust's
  (release, in place, drain, health gate) comes with what it does for you.
- **Deleting an application** no longer ticks "delete files" and "delete certificate" for you.
- The content column no longer shifts when a tab changes (`scrollbar-gutter: stable`), tested
  end to end.
- Accounts, roles, invitations, passkeys, approvals, the audit log and compliance under
  Settings; a first-run page to create the first accounts.

## Fixes and security fixes

### Security fixes

- **A central could create Unix sockets as root on a 3.0 server.** 3.0 installed the central's
  key in root's `authorized_keys` with `restrict,port-forwarding,permitopen=...,permitlisten=...`,
  and documented that it could only forward the console port. `permitlisten` limits TCP
  listeners only: the same key could ask for a remote forward to a path
  (`ssh -R /etc/nologin:...`), and sshd created that Unix socket as root, anywhere, including a
  path that stops every other account from logging in. 3.1 enrolls servers as `noust-tunnel`
  with `AllowStreamLocalForwarding no` enforced by sshd, and `noust node migrate-tunnel` moves a
  3.0 server. Until you move it, a server enrolled by 3.0 remains exposed to whoever holds that
  central's key. `noust node list` flags the ones still reached as root.
- **`--dry-run` could run a command that changes the system.** 3.0 judged a command read-only
  when any of its arguments was a read-only subcommand's name, not only the subcommand itself.
  Adding firewall probes on that rule would have let `ufw allow 22 comment status` run for real
  under `--dry-run`. 3.1 runs a command under a rehearsal only when its exact argument shape is
  declared as a probe; anything else is assumed to change something.
- **Build scripts ran as root** with every secret on the machine in reach (see Builds without
  root).
- **The runner passed its whole environment to child processes**; builds and hooks now get a
  clean one.
- **`web.allowed_hosts` was read by nothing.** It is now enforced on every request: a request for
  a host not listed answers 400 (empty still allows any host).
- **The `noust.audit` logger had no handler**, so SQL run from the console was not recorded
  where the documentation said. It is now an audit event.
- **Restoring a database with `--drop`** no longer runs without a safety copy.
- nginx and Apache sites send `server_tokens off` (or the Apache equivalent) and no longer send
  `X-XSS-Protection`; `Strict-Transport-Security` is available with `ssl.hsts` (off by default).

### Fixes

- A failed test of a backup destination answers 4xx with the reason instead of 500.
- A malformed release id answers 404 or 422 instead of 500.
- `/ws/logs` resolves every kind of application unit and reports journalctl's own error.
- `noust config clean` removes settings no version reads any more (`logging`, `nodejs`,
  `python`, `databases.default_encoding`, `databases.auto_start`, `databases.auto_enable`,
  `databases.backup_dir`, `monitor.use_ai`, `monitor.ai_interval`, `monitor.openai`), keeps
  comments, writes a dated `0600` copy first, and deletes the old OpenAI API key instead of
  keeping it in the copy. `noust config show` lists obsolete settings apart. The package upgrade
  runs it. Every setting is documented in [CONFIG.md](CONFIG.md).
- `server.name` names the server in notifications and in the fleet.
- The monitor remembers that you turned it off (`/var/lib/noust/monitor-declined`).

## Packaging

- Passkeys use `cryptography`: included in `noust[web]` and `noust[all]`, recommended by the
  Debian package, suggested by the RPM one (`python3-cryptography`). No other runtime
  dependency is new.
- The container image is unchanged in how it runs; its Python environment includes
  `cryptography`.
- The package upgrade runs `noust config upgrade`, `noust config clean` and `noust monitor
  autoenable`, and restarts a running console.
- **The store moves to schema v12, one way.** A copy of the store as it was is written beside it
  first (`noust.db.v11-<time>.bak`); 3.1 refuses to open a store written by a newer version, and
  going back to 3.0 means restoring that copy. See
  [UPGRADING-3.1.md](UPGRADING-3.1.md#rolling-back).
- New paths: `/var/cache/noust/build/<app>` (build caches, owned by `noust-build`) and
  `/run/noust/sandbox`; the `noust-build` system account is created on the first sandboxed
  build.
- Every release publishes a software bill of materials, and CI runs `pip-audit` and `npm audit`.
