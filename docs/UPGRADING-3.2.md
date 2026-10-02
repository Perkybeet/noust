# Upgrading to 3.2

This guide is written against Noust 3.1.17. A server older than 3.1 goes through
[UPGRADING-3.1.md](UPGRADING-3.1.md) first (and a WASM 2.x server through
[UPGRADING-3.0.md](UPGRADING-3.0.md) before that); the packages do the steps in one upgrade, but
read those guides for what each one moves.

The short version: 3.2 deploys Docker Compose projects properly (deploy hooks, updates without a
cut, adopting a stack that already runs, a copy of its database before every update), shows the
web server as a structure and a diagram, and finishes two commitments of 3.1: the build sandbox
for every application and an account of its own for each. Your applications keep running through
the upgrade, and nothing about how an existing application is laid out, built or served is
converted behind your back. What 3.2 does change on its own is listed first, so nothing
surprises you.

One thing is one-way: the store moves to schema v13, and 3.1 does not understand it. The upgrade
keeps a copy of the store as it was, which is what [rolling back](#rolling-back) puts back.

## What changes on its own

When the new package is installed, and from the next update of each application:

- **The store is migrated to v13**, the first time Noust opens it (the package's own steps, the
  console's restart, or the next command). Before the first change, a copy is written beside it:
  `/var/lib/noust/noust.db.v12-<UTC time>-<pid>-<id>.bak`, mode `0600`, taken with SQLite's online
  backup so it includes what the write-ahead log still held. If the copy cannot be written (no
  space), nothing is migrated and the command says so.
- **The console restarts** onto the new version, if it runs as `noust-web.service`. Your access
  token, accounts and sessions keep working. A job that runs in its own systemd unit (an
  operating system update, Noust updating itself) is no longer marked interrupted when the
  console restarts: the console reads how the unit ended. A job that runs in the console itself
  still is, so wait until no job is running.
- **A failing Prisma migration now stops the update.** Until 3.1 an update of a Node or monorepo
  application that uses Prisma ran `prisma migrate deploy`, treated a failure as a warning and
  went on, so the new code went live against the schema it was not written for. From 3.2 the
  update stops before anything serves the new version, the application keeps running what it
  ran, and the error carries Prisma's own output. If one of your applications has been deploying
  past a failing migration, its next update fails until you fix the migration; that is the point.
- **A repository's `noust.yaml` (or `.noust.yaml`) is read at every deploy and update.** Nothing
  happens when there is none. If a repository already has a file of that name for some other
  purpose, 3.2 tries to read it as Noust's and refuses a file that is outside its schema,
  naming the field. Rename the other file, or fix this one. See
  [compose.md](compose.md#noustyaml).
- **The next update of a Docker Compose stack dumps its databases first**, and **stops if a dump
  fails**. See [Docker Compose projects](#6-docker-compose-projects) for what is dumped, how to see
  it before it matters, and how to turn it off.
- **The next update of an application that still builds as root tries the sandbox first**, once.
  See [The sandbox on the next update](#5-the-sandbox-on-the-next-update).
- **Applications created from 3.2 on run as their own system account**
  (`noust-app-<name>`), not as the shared service account. Existing ones keep the shared account
  until you move them. See [Accounts of their own](#7-accounts-of-their-own).
- **Going back past a deployment that changed the database schema asks first.** Only deployments
  that 3.2 recorded can say so (the column starts at false for everything older), so nothing
  asks about history that predates the upgrade. See [Rolling back past a
  migration](compose.md#rolling-back-past-a-migration).
- **Deleting an application whose directory is outside the apps directory keeps the directory.**
  Such a directory is one an adopted stack lives in, or one you placed there; "delete the files"
  no longer removes it unless you name it: `noust delete DOMAIN --remove-adopted-directory PATH`.
  Applications deployed by Noust under `/var/www/apps` behave as before.
- **Two notifications that did not exist are on by default.** `app_unreachable` and
  `app_recovered` tell you when an application whose unit is running stops answering its health
  check (three failed probes over at least a minute, once per outage) and when it answers again.
  `deploy_hook_failed` tells you when a deployment went live with a `post_deploy` hook that
  failed. Until now only `unit_failed` existed, which fires when systemd gives a unit up, so an
  application that was up for systemd and down for its users told nobody. If you have channels
  configured, expect messages from servers that had applications in that state all along. Turn
  them off with `noust config set notifications.events.app_unreachable false` (and the other
  two names).
- **The monitor records process samples.** Every minute, the five processes using most CPU and
  the five using most memory, in `/var/lib/noust/metrics.db`, for 26 hours: about 15,600 small
  rows. The command line of each is stored with its secrets redacted and is shown only to who
  may read command lines. History starts a minute after the monitor restarts. See
  [MONITOR.md](MONITOR.md#process-samples).
- **A site's configuration is tested inside the live one.** The console's "Test" and every write
  of a site copy `nginx.conf` (or `apache2.conf`) with the enabled sites listed and this one
  replaced by the candidate. A valid site that uses a zone, a `map` or a `log_format` declared in
  the main file now passes, and a site that declares an `upstream` another enabled site already
  declares is now refused, as the reload would have refused it.
- **Sites Noust writes from now on** follow the new rules for Compose stacks: with several web
  services and no `noust.nginx.yaml`, `/` goes to the service in front and no `/<service>` routes
  are invented, and a `rate_limit` declared for the whole server in `noust.nginx.yaml` is applied
  to the routes without their own (it used to declare a zone nothing used). An existing site
  changes only the next time Noust writes it: a first deploy, `noust domain add|remove` or turning
  zero-downtime on. An update of a Compose stack does not rewrite its site, and a site you wrote
  is never rewritten.
- **`POST /api/server/storage/analyze` needs `server.read`**, not `server.manage`: measuring
  what takes the space changes nothing, so a central with the `read` ceiling may ask for it.

### Stricter checks found by the 3.2 security review

- **Git in an application's tree runs as the tree's owner.** Noust no longer runs git as root
  with `safe.directory=*` inside a tree another account owns: reading runs as that owner, and
  root's own git there refuses configuration that would run a program. A checkout whose
  `.git/config` sets a `credential.helper` or a `filter.*` (Git LFS) is refused
  on update with the key named; remove it from that checkout's configuration (the remote's
  credentials belong in Noust's source credentials).
- **Testing or saving a site checks the files it reads.** `include`, `ssl_certificate*`,
  `ssl_trusted_certificate`, `*_log` and `auth_basic_user_file` paths must sit under the web
  server's configuration directory, `/etc/letsencrypt`, `/etc/ssl`, `/etc/pki`, `/var/log` or
  the application's directory; Lua, Perl and njs directives, `load_module` and piped logs are
  refused in a site. An operator site that uses another location still serves as before, but
  saving it from the console, or `noust site` edits, ask you to move the file first.
- **`noust.nginx.yaml` `custom_directives` is a closed list** of header, cache, compression and
  proxy-tuning directives; anything else belongs in your own site, which Noust keeps.
- **A Compose stack that gains `privileged` or the Docker socket** in an update, compared with
  the compose file that was serving, is refused until the exception is recorded
  (`noust app sandbox compose-exception DOMAIN --reason '...'`), as for a new stack.

## What does not change

- **Your applications**: their units, directories, sites, certificates, `.env` files and
  releases. Nothing an application runs is restarted by the upgrade.
- **Which hooks run.** None, until a repository's `noust.yaml` or an operator's document
  declares them. An application with no `noust.yaml` behaves as in 3.1 except for the three
  changes above that name it: Prisma, the database copy of a Compose stack, and the sandbox
  trial.
- **Zero-downtime for a Compose stack.** It is opt-in, with the same command as for blue/green.
- **Who builds as root on purpose.** `noust app sandbox disable DOMAIN --reason '...'` is
  respected: Noust never tries the sandbox for that application.
- **Which account an existing application runs as**, and the accounts, tokens, passkeys, roles
  and approvals of 3.1.
- **A fleet.** Servers enrolled by 3.1 keep working, with the access ceiling they set.
- **The names kept from WASM** (`wasm_session`, `X-WASM-CSRF`, `wasm_ro_` roles, `wasm_bg_`
  upstream names and the rest), and the `wasm` command.

## Upgrading a single server

### 1. Take stock

```bash
noust --version                           # 3.1.x
noust list                                # keep this output to compare afterwards
noust store path                          # where the store is
noust web status
noust health                              # read it now: see below what 3.2 adds to it
df -h "$(dirname "$(noust store path)")"  # room for a copy of the store
df -h /var/backups/noust                  # room for a copy of each Compose stack's databases
```

Wait until no job is running (the console's Activity page, or `GET /api/jobs?status=running`).
Look at which of your applications this guide is about:

```bash
noust list --json | python3 -c 'import json,sys; [print(a["domain"]) for a in json.load(sys.stdin)["items"] if a["type"] == "docker-compose"]'
noust app sandbox status shop.example.com     # per application: sandbox, or root and why
```

### 2. Back up

The upgrade keeps a copy of the store by itself, but keep your own of everything it touches:

```bash
install -d -m 700 /root/noust-3.1-backup
cp -a /etc/noust /root/noust-3.1-backup/etc-noust            # configuration, console state, tokens, audit log
cp -a /var/lib/noust /root/noust-3.1-backup/var-lib-noust    # the store, metrics, observations, secrets/
noust store export -o /root/noust-3.1-backup/store.json      # every record, readable
```

If `noust store path` printed a location outside `/var/lib/noust`, copy that directory too.
Stop the console first (`noust web stop`, or `systemctl stop noust-web`) for a copy that is
consistent to the second, or accept that the last seconds may be missing.

Keep a way to reinstall 3.1.17:

- **Debian and Ubuntu:** the `.deb` attached to the GitHub release
  `https://github.com/Perkybeet/noust/releases/tag/v3.1.17`.
- **Fedora and openSUSE:** OBS publishes only the latest build. Save the installed package before
  upgrading, while the repository still serves it: `dnf download noust`, or copy it from the
  zypper cache.
- **PyPI:** `pip install 'noust[all]==3.1.17'` stays available.

### 3. Upgrade the package

```bash
apt update && apt upgrade                    # Debian, Ubuntu
dnf upgrade --refresh                        # Fedora
zypper refresh && zypper update              # openSUSE
pip install -U 'noust[all]' wasm-cli         # PyPI (or 'noust[web]' without the monitor)
```

No dependency is new: the packages need nothing that 3.1 did not.

### 4. Check

```bash
noust --version                           # Noust 3.2.x
ls -l /var/lib/noust/noust.db.v12-*.bak   # the copy of the store as it was
noust list                                # the same applications, in the same states
noust web status
noust monitor status                      # recording the history, and now the process samples
noust health
noust setup doctor                        # also reports a Compose relay an interrupted update left
```

Sign in to the console with the same account. On a server with a Compose stack, open its
Settings: the hooks, the tags it follows, zero-downtime and the database copy are there.

### 5. The sandbox on the next update

3.1 made the build sandbox the default for new applications and left each existing one building
as root until you tested and enabled it. In 3.2, **the next update of an application that still
builds as root first builds that exact commit in the sandbox**, the same build `noust app sandbox
test` runs:

- **It passes:** the sandbox is turned on for the application and the update builds in it. From
  then on it builds in the sandbox, with `noust app sandbox status` saying so.
- **It fails:** the update builds as root, exactly as it did, so an update that worked never
  breaks because of the sandbox. Noust records why, warns on the application's page, in `noust
  health` and in `noust ens check`, sends a notification (under `deploy_failed`) with the build's
  output, and does not try again by itself.
- **Not tried:** an application with `noust app sandbox disable --reason`, a server where the
  sandbox does not hold (containers and WSL without mount namespaces: `noust app sandbox
  self-test`), a Docker Compose stack and a static site.

To find out before the update does, test each application now and decide:

```bash
noust app sandbox test shop.example.com      # builds the commit that is live; nothing is activated or restarted
noust app sandbox enable shop.example.com    # on, from the next deploy (asks for a passing test)
noust app sandbox disable shop.example.com --reason "needs the registry credentials in root's home"
```

The usual reasons a build fails there are in [UPGRADING-3.1.md](UPGRADING-3.1.md#7-builds-without-root):
it writes outside its own directory, reads a file of another application, or needs a private
registry's credentials from root's home.

### 6. Docker Compose projects

Everything below is opt-in except the database copy. For each stack:

**The database copy before an update.** The next `noust update` dumps every database the stack
runs into the pre-update backup (in `/var/backups/noust/<app>/`), before anything else, and stops
if a dump fails.

- Detected: services running the official `postgres`, `mysql`, `mariadb` or `mongo` image, with
  any tag. The user and database come from the service's environment as Compose resolves it
  (`docker compose config`).
- Not detected: `bitnami/postgresql`, `postgis/postgis` and the like. Declare them under
  `backup.databases` in `noust.yaml`, or the update copies nothing of them.
- To see what the update will do before it matters, take the same copy now: `noust backup create
  DOMAIN --include-databases` runs the same dumps through the same code and fails with the same
  error. Check it restores in a scratch place before you rely on it.
- If a stack's database cannot be dumped from its container (credentials in a secret the service
  does not expose, an image Noust does not know) the error carries the engine's output and the
  command that turns the copy off: `noust app backup-before-update DOMAIN off`. Turn it off only
  when the database is backed up another way, because without a copy there is no way back from a
  migration.
- Disk: each update's backup now carries a compressed dump of each database, and backups are
  rotated by `backup.max_per_app` (10 by default), so the space is up to that many dumps. See
  [compose.md](compose.md#stack-database-backups-and-restore).

**Move the project's own deploy steps into hooks.** If the project runs its migrations from its
container's entrypoint or from a script that wraps `docker compose up`, declare them where Noust
can order them, abort on failure and record them:

```yaml
# noust.yaml, at the root of the repository
hooks:
  pre_deploy:
    - run: ./node_modules/.bin/prisma migrate deploy
      service: backend
      migrates: true
```

A `pre_deploy` hook runs in a one-off container of the image the update just built, before any
container is recreated; one that fails stops the update with what served before still serving.
`migrates: true` is what makes going back past this deployment ask first. See
[compose.md](compose.md#noustyaml) and, to set hooks without touching the repository,
[compose.md](compose.md#operator-hooks).

**Turn on updates without a cut** when the stack qualifies:

```bash
noust app zero-downtime shop.example.com on     # checks its requirements and says what is missing
```

Each web service must publish a fixed host port, and the site must reach it through a servers
file of Noust's. The site Noust wrote is switched to it by the command. **A site you wrote
yourself must include the file**: the command refuses with the exact line, and has already
written the files so that the line loads. Add it inside the `upstream` block that proxies to the
service, reload nginx, and run the command again. See
[compose.md](compose.md#zero-downtime-updates-the-relay). Both versions of a service run side by
side for a few seconds during an update, which a queue consumer in the web process or a job
scheduled in-process may not tolerate.

**Adopt a stack that was never deployed by Noust:**

```bash
noust --dry-run app adopt shop.example.com --path /opt/shop       # what it would find; nothing changes
noust app adopt shop.example.com --path /opt/shop
```

The directory must be a git checkout with an `origin` (or pass `--source`), and `docker compose up
--dry-run` must say nothing would be recreated. Nothing is cloned, rebuilt or restarted. See
[compose.md](compose.md#adopting-a-running-stack).

**Workers.** A stack that publishes no port was never a web application. If an earlier version
registered it with a port and a site (WASM 1.x recorded the default port), `noust diagnose` and
`noust health` call it down or degraded. `noust app headless DOMAIN` clears the port and, when
Noust wrote its site and no alias uses it, offers to remove the site. It is never done for you.

**Deploy by tag**, if you want releases instead of a branch:
`noust app follow-tags DOMAIN 'v*'`, and point the forge's `release` or tag-push webhook at the
application. See [compose.md](compose.md#deploying-by-tag).

### 7. Accounts of their own

New applications run as `noust-app-<name>`. Move an existing one when you are ready:

```bash
noust app identity status shop.example.com    # the account it runs as, and whether it is its own
noust app identity migrate shop.example.com   # asks first; -y skips the question
```

`migrate` creates the account (no shell, no home), gives it the files the shared account owned
(the tree, the `.env`, the build cache), rewrites the unit or the PHP-FPM pool, and restarts the
application behind its health gate. If it does not answer, the file owners, the unit or the pool
and the records are put back exactly and it is restarted as before. Docker Compose stacks,
monorepos and static sites have no account to move.

### 8. Notifications

Check what will now reach your channels (see [What changes on its own](#what-changes-on-its-own)):

```bash
noust config get notifications.events.app_unreachable
noust config get notifications.events.app_recovered
noust config get notifications.events.deploy_hook_failed
noust notify test slack
```

### 9. Scripts and automation

- **A rollback past a schema change answers `409 schema_changed`**, with the deployments in
  `deployments`, from `POST /api/apps/{domain}/deployments/{id}/rollback`, `POST
  /api/apps/{domain}/releases/{id}/activate`, `POST /api/jobs/rollback` and a files-only
  `POST /api/backups/{id}/restore`. Send `schema_changed_ok: true` in the body to go ahead (a
  query parameter, `?schema_changed_ok=true`, on the release activation). On the command line it
  is `--schema-changed-ok`, on `noust rollback`, `noust releases rollback`, `noust update
  --commit` and `noust backup restore`. A script that rolls back and never met this error will
  meet it the first time a deployment of an application declared a migration.
- **Hooks are `root_equivalent` to write.** `PUT|DELETE /api/apps/{domain}/hooks` need sudo mode
  and, when approvals are on, a second person. A token must hold the permission.
- **`noust update --tag`, `noust create --follow-tags`, `noust app follow-tags`** are new, and
  so are the webhook's `release` and tag-push events for applications that follow tags.
- **New endpoints**, all documented in [api.md](api.md): hooks, adopt, headless, identity,
  backup-before-update, follow-tags, a site's structure, edit, route and topology, the time zone
  list and the timeline.
- **`GET /api/sites`** lists the web server's files too, each with `noust_managed`, `app` and
  `site_name`; a client that assumed every row was the store's should read the new field.
- **A deployment** reports `schema_changed`, `schema_changed_between`, `hooks` and `warnings`.
  One that went live with a failed `post_deploy` is a successful deployment with `warnings`.

## New settings

All optional; `noust config upgrade` adds those with defaults, and [CONFIG.md](CONFIG.md)
documents every one.

| Setting | Default | What it does |
|---|---|---|
| `notifications.events.app_unreachable` | `true` | Notify when a running application stops answering its health check |
| `notifications.events.app_recovered` | `true` | Notify when it answers again |
| `notifications.events.deploy_hook_failed` | `true` | Notify when a deployment went live with warnings: a `post_deploy` hook failed |

Everything else new is per application and stored, not configured: the operator's hooks, the
database copy, the tags it follows, its account and its zero-downtime relay.

## Upgrading a fleet

A 3.2 central drives 3.1 servers and a 3.1 central drives 3.2 servers: nothing stops working
halfway. Upgrade **the central first**, then the servers, as for
[a single server](#upgrading-a-single-server).

- A **3.2 central with 3.1 servers** reaches them as before through the proxy. The pages that
  call routes a 3.1 server does not have (a site's structure, edit and diagram, the time zone
  list, the timeline, hooks) say that the server runs an older Noust and leave the text editor
  and the plain forms where they were; the fleet views show what each server can answer.
- A **3.1 central with 3.2 servers** keeps working with what it has. It cannot show the new
  views of a 3.2 server, and has no way to send the confirmation of a rollback that passes a
  schema change (the server answers `409 schema_changed`): confirm that one from the server's own
  console or CLI.
- **An operating system update that includes Noust**, run from the central, says so in its plan
  ("Also updates Noust: the node's console will restart"), installs Noust last on apt and dnf, and
  waits for the node's console to come back and for the job to say how it ended. Before 3.2 the
  job was counted as failed, because the restart that the `noust` package causes cut it, and a
  failure threshold of zero skipped the remaining servers. A node that still runs 3.1 behaves
  as it did, and the central then waits for that node's own record of the update.
- A server's ceiling for its central (`noust fleet access`) is unchanged. Writing an
  application's hooks is `root_equivalent`, and a server grants that to a central only with the
  `admin` ceiling and host access on (`noust fleet access --host-access on`); the server refuses
  otherwise, whatever the central claims. Reading hooks and updating an application that has
  them need no more than before.

## What to check after upgrading

- `noust list` matches what you noted before; every application in the same state.
- `ls /var/lib/noust/noust.db.v12-*.bak` exists; keep it until you are sure you will not roll
  back.
- `noust health`: it now also reports Compose stacks registered with a port they do not publish,
  and applications whose sandbox trial failed. They describe the server as it already was.
- `noust monitor status` says the history, and the process samples, are being recorded.
- `noust audit status` and `noust audit verify` pass.
- Each Compose stack: its Settings page, `noust app backup-before-update DOMAIN` (the copy is
  on), and one update you watch.
- Each application that built as root: `noust app sandbox status DOMAIN`.
- The console: open a site of yours (Domains and certificates > Sites) and look at Structure and
  Diagram; open an application's Settings.
- On a central: `noust node list` and `noust fleet status`.
- Webhooks: `noust app webhook show DOMAIN` for each application that deploys on push.

## Rolling back

Going back to 3.1 means reinstalling 3.1 and putting back the store 3.1 understands. 3.1 refuses
to open a store written by a newer version, so it never touches the v13 store, but it must not be
started over it either: restore the copy first.

Before you start, finish what 3.2 is in the middle of:

```bash
noust app zero-downtime shop.example.com off     # for each Compose stack on the relay
noust server security pending                    # SSH or firewall changes waiting for confirmation
noust server updates history                     # no system update still running
```

Turning the relay off matters: it returns Noust's own site to proxying straight to the ports and
removes the servers files once nothing includes them. A site you wrote that includes a servers
file keeps including it, and 3.1 does not maintain those files; take the `include` out of your
site and put your `server 127.0.0.1:<port>;` line back.

1. **Stop the console and the monitor:**

   ```bash
   systemctl stop noust-web noust-monitor
   ```

2. **Reinstall 3.1.17:**

   ```bash
   apt install --allow-downgrades ./noust_3.1.17-*_all.deb         # Debian, Ubuntu
   dnf downgrade ./noust-3.1.17-*.noarch.rpm                       # Fedora, the package you saved
   zypper install --oldpackage ./noust-3.1.17-*.noarch.rpm         # openSUSE
   pip install 'noust[all]==3.1.17'                                # PyPI
   ```

3. **Put back the store and the console's state:**

   ```bash
   cd /var/lib/noust
   mv noust.db noust.db.3.2
   rm -f noust.db-wal noust.db-shm
   cp -a noust.db.v12-<time>-<pid>-<id>.bak noust.db
   mv /etc/noust /etc/noust.3.2
   cp -a /root/noust-3.1-backup/etc-noust /etc/noust
   ```

4. **Start the console and the monitor as before** (`noust web enable` with your options, or
   `systemctl start noust-web`; `noust monitor install` if you run it), and check with `noust
   --version`, `noust list` and `noust web status`.

What does not come back:

- **Everything recorded under 3.2**: the operator's hooks, deployments and jobs since the
  upgrade, the tags an application followed, the database-copy setting, and the sandbox state
  that a trial turned on. It stays in `/var/lib/noust/noust.db.3.2` and `/etc/noust.3.2`.
- **Adopted stacks** are not in the restored store: 3.1 does not know them. The stack keeps
  running as it did, and so does its site. The unit `<app>.service` that adopting created stays
  (it carries `-p`), enabled, and does nothing until started; remove it with `systemctl disable
  <app>` and delete the file in `/etc/systemd/system/` when nothing needs it.
- **Applications with an account of their own** (created under 3.2, or moved with `noust app
  identity migrate`) keep running as `noust-app-<name>`, because their units say so, while 3.1
  hands trees and `.env` files to the shared service account on its deploys. This case has not
  been exercised: before the next 3.1 deploy of such an application, set `User=` and `Group=` in
  its unit (or its PHP-FPM pool's `user` and `group`) back to `noust config get service_user`,
  `chown -R` its tree, `.env` and `/var/cache/noust/build/<app>` to the same account, and restart
  it. Remove the accounts with `userdel` when nothing uses them.
- **Applications the sandbox trial moved** go back to building as root, because the restored
  store says they did; their next update will try the sandbox again if 3.1 had not.
- **API tokens issued under 3.2** are not in the restored console state.
- **Changes 3.2 made to the system itself stay**: packages updated, files handed to an
  application's own account, a site that included a servers file, and what a restore of a stack's
  databases put back. They are ordinary system changes that 3.1 neither needs nor undoes.
- **The process samples** stay in `/var/lib/noust/metrics.db`, where 3.1 ignores them.
