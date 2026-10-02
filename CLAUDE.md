# Noust - Context for AI Assistants

Noust (previously WASM, renamed in 3.0.0) is a Python 3.10+ CLI for deploying web apps on
Linux servers. Automates Nginx/Apache, SSL, systemd, databases and backups; builds every
deploy as a health-gated release with instant rollback; and serves an optional browser
console (React SPA) over a JSON API.

**Repository**: https://github.com/Perkybeet/noust | **License**: AGPL-3.0-or-later (from 2.1.0; releases up to 2.0.x were WASM-NCSAL 1.0)

---

## The four rules

These are not style preferences. Each one exists because its absence produced a specific
class of defect that shipped to users. Breaking one is how the project regresses.

### 1. Nothing runs a process except `CommandRunner`

`src/noust/core/runner.py` is the only module allowed to import `subprocess`. Everything
that calls nginx, systemctl, certbot, git, npm or a database client goes through it.

```python
result = self.runner.run(["systemctl", "restart", unit], timeout=30, check=True)
self.runner.stream(["npm", "install"], on_line=logger.substep, timeout=900)
self.runner.capture_to_file(["pg_dump", db], destination, compress=True)
```

Argv only, never a shell. Timeouts are mandatory. Secrets go through `env=` or `input=`,
never argv, because everything on a command line is visible in `ps` to every local user.

`tests/conftest.py` makes real process execution fail in every test, so code that bypasses
the runner cannot be tested and fails loudly instead. `tests/test_architecture.py` enforces
the import rule.

`--dry-run` is implemented here too, as `DryRunRunner`, and for file changes as
`DryRunFileSystem` in `core/fs.py`. It is not wired per command: that is what left the flag
honoured in three code paths and silently ignored in every destructive one.

### 2. `except Exception` is only allowed at an error boundary, and must log

There were 302 of them, 149 silent. That is the mechanism by which five calls to methods
that did not exist shipped for entire releases: every `AttributeError` became a cosmetic
warning. Catch the specific exception. If you genuinely need a broad catch, it belongs in
the CLI or API error boundary and it logs.

mypy is the guard for this class of bug and it blocks CI.

### 3. There is one implementation of each thing

The version lived in six hand-synchronised files, app-type detection had four
implementations with contradicting precedence, and the web API was a second implementation
of the whole product. When you find yourself writing something that already exists, use the
existing one or move it somewhere both callers can reach.

The web layer in particular is a **client** of the managers, never a parallel
implementation. An endpoint translates HTTP to a manager call and back.

### 4. Guards go at the chokepoint

A rule enforced in the caller is a rule with as many holes as there are callers. Unit
ownership is checked in `ServiceManager`, not in the endpoints that use it. Read-only SQL is
enforced by the database server, not by a keyword allowlist. Escaping is done by the
template engine, not by remembering.

---

## Architecture

```
src/noust/
  core/
    runner.py       the only place processes are executed
    fs.py           the seam every file change goes through (DryRunFileSystem under --dry-run)
    paths.py        every system path, unit name and marker, new and legacy, in one place
    migrate_from_wasm.py  moves a WASM 2.x server onto Noust's names; atomic, resumable, undone per step
    store.py        SQLite persistence (WAL) with versioned migrations
    config.py       layered config; secrets written 0600, redacted on the way out
    exceptions.py   NoustError hierarchy, used for real
    sealing.py      a central's secrets at rest: scrypt, then AES-256-CBC and HMAC-SHA256
                    through openssl, never a new dependency
  validators/       names, environments, sources, ports, domains
  managers/         adapters: web server, systemd, certs, backups, databases, source, cron
    diagnose.py     why an app is down: read-only probes, most likely cause first
    health.py       the server-wide report behind `noust health` and /api/system/health
    siteconf/       the one nginx/Apache parser: lossless tree, structured model, edit
                    operations, request routing (rule 3: nothing reads a site with a regex)
    stack_databases.py  a Compose stack's databases: detect, dump, restore
    app_identity.py the one answer to "which account does this app run as"
    site_topology.py, timeline.py   what a site reaches, live; one timeline over a stretch
  deployers/        strategies over a declarative pipeline (base.py), one per app type
    releases.py     ReleaseManager: the only code that knows releases/, current, shared/
    lifecycle.py    the one "update an app"; release activation; resource limits
    migrate.py      in-place to releases, explicit only, undone exactly on failure
    domains.py      the one "names an app answers on": store, site, certificate, DNS check
    compose_relay.py  zero-downtime for Compose: a twin of each web service while it is recreated
    compose_adopt.py  `noust app adopt`: register a stack that already runs, touching nothing
    helpers/        layout.py (which layout, where .env lives), health_gate.py, release_build.py,
                    project_file.py (noust.yaml), hooks.py (which hooks run), site.py (the one
                    writer and deleter of an app's site), compose_ports.py (the one port reader)
  fleet/            node enrollment: authorize.py installs the restricted key and prints the
                    join code; keys.py the per-node keypair; tunnels.py the SSH tunnels
                    (through CommandRunner); client.py a node's API over its own tunnel;
                    nodes.py the store-backed registry a central keeps of them
  central/          what a central is: role() (hub or server), setup.py (data directory, TLS,
                    the allow-list), unlock.py the socket a sealed central takes a passphrase on
  web/
    api/            thin layer over the managers; one error contract for every router;
                    node_proxy.py forwards every node call, event and WebSocket over its
                    tunnel (rule 3: a central re-implements nothing a node does)
    server.py       security middleware, CSP, serves the console
    events.py       the /events SSE stream the console listens to
    jobs.py         background jobs, persisted with their logs
    job_reconcile.py  a job in a transient systemd unit is followed again after a restart
    static/         the console's committed Vite build (generated from panel/, never edited)
  cli/              Click tree (app.py, commands/, including fleet.py, node.py, central.py);
                    handlers hold no business logic
panel/              Noust console source: React 19, TypeScript, Vite, TanStack Router/Query,
                    Base UI, Tailwind v4; src/api/schema.gen.ts is generated from openapi.json
  e2e/              Playwright + axe + CSP gate against the real backend
scripts/console_server.py   the real API on a seeded, sandboxed store (development and E2E)
tests/integration/run.py    real deploys in a systemd container (Docker); not part of pytest
```

### Adding a deployer

1. Create `deployers/mytype.py` implementing the deployer interface.
2. Set `APP_TYPE`, `DISPLAY_NAME`, `DETECTION_FILES`, `DEFAULT_PORT`.
3. Implement `detect()`, `get_install_command()`, `get_build_command()`, `get_start_command()`.
4. Register with `DeployerRegistry.register(MyTypeDeployer)` at the end of the file.
5. Add a `detect()` test with a fake file tree, including the ambiguous cases.

Build on `BaseDeployer` and the type gets releases, the health gate, domains, limits and deploy
hooks for free. `MonorepoDeployer` and `DockerComposeDeployer` implement `AppDeployer` directly,
which is why they deploy in place (`SUPPORTS_RELEASES` is read with `getattr`, defaulting to
false); they share the hooks, the site writer and the domain capability (`helpers/hooks.py`,
`helpers/site.py`) rather than re-implementing them.

---

## Deploy engine

An application is on one of two layouts, recorded in `apps.layout`:

- `inplace` (1.x): the service runs the tree every update rebuilds.
- `releases`: `releases/<id>/` per deploy, `current` pointing at the active one, `.env` and
  persistent paths in `shared/`, a git cache in `repo/`.

`BaseDeployer` works with three paths, and code must use the right one:

- `app_path`: the application directory the store records.
- `build_path`: where the code being deployed is built. The new release on releases,
  `app_path` in place. Everything that reads or builds the project uses it.
- `runtime_path`: what the unit and the web server are given. `app_path/current` on
  releases, `app_path` in place, so one unit and one site serve every release.

In place the three are the same directory, which is what keeps that layout exactly as it was.
Outside a deploy, ask `helpers/layout.py`: `env_file_for(app)` for the `.env`,
`code_path_for(app)` for the running code. Never join `app_path` with `".env"`.

Rules:

- **An in-place application is never converted implicitly.** `choose_layout()` keeps the
  layout an existing application has, a redeploy that asks for another is an error, and
  `ReleaseManager.activate()` refuses to replace a real `current` directory. Only
  `migrate.migrate()`, called by `noust app migrate` and `POST /api/apps/{d}/migrate`,
  moves an application onto releases.
- **Every activation passes the same `HealthGate`**: deploy, update, rollback, migration and
  limits applied with a restart. When it fails, what served before is put back (the previous
  release, the in-place tree, the old limits) and the error carries the probes and the
  journal verbatim.
- **`lifecycle.update_app` is the only update.** The CLI, the console's job and the webhook
  all call it.
- **Nothing is written through a symlink found in a release or in `shared/`.** A repository
  is untrusted input.
- **`app_path` is not always under the apps directory.** An adopted stack lives where it
  already ran (`/opt/proggest`). Never build `apps_directory / app_name`: use the store's
  `app_path` (`lifecycle`, `BackupManager` and deletion already do), and never remove a directory
  Noust did not create unless the operator names it (`--remove-adopted-directory`).

---

## Access, audit and builds (3.1)

- **Every API route declares a permission** in `web/permissions/routes_<area>.py`; one global
  dependency enforces it and publishes it as `x-noust-permission`, and a route without one is
  refused (a test fails first). Roles (`viewer`, `operator`, `admin`, `security`, `auditor`) are
  sets of permissions in `web/permissions/roles.py`; accounts live in `core/accounts/`. The
  master access token is break-glass, not the normal way in. Root-equivalent actions can require
  a second person (`core/accounts/approvals.py`, guard on the router).
- **One audit trail**: `noust.core.audit.record(event, actor=...)` with a closed catalog
  (`core/audit/catalog.py`; a test scans the code for undeclared events), an HMAC chain, and
  shipping to journald/syslog. CLI commands that change something are audited by `cli/app.py`.
- **Builds never run as root.** Install, build and hooks run through
  `CommandRunner(..., sandbox=SandboxSpec(...))` as `noust-build` in a transient systemd unit,
  failing closed when the sandbox cannot be proven; migrations run as the app's user. Existing
  apps switch per app after a test build (`noust app sandbox test|enable`).
- **Dry-run probes are declared by exact argv** (`core/runner.py` `READ_ONLY_PROBES` and each
  area's `probes.py`); a command is never read-only because one of its arguments looks harmless.
- **The ENS profile** (`security.profile: ens-medium`) lives in one module, `core/ens/profile.py`;
  every area reads its defaults from there.

## Compose, hooks and the web server (3.2)

Design: `docs/superpowers/specs/2026-10-02-noust-3.2-design.md`; the execution log is in its
plan. Operator documentation is `docs/compose.md`.

- **`noust.yaml` is untrusted input and the schema is closed.** `project_file.py` reads it
  (`hooks.pre_deploy`, `hooks.post_deploy`, `backup.databases`); an unknown key, `..`, a link or
  an absolute path outside a container is a `ValidationError` naming the field, never ignored.
  `hooks.resolve_hooks` is the one answer to "which hooks run": the operator's document (store
  `app_hooks`, root-equivalent to write) replaces the repository's whole, never merged. A hook is
  an argv (shlex, never a shell) through `CommandRunner`: in Compose a one-off container of the
  new image (`docker compose run --rm --no-deps`), elsewhere the release phase of the sandbox.
  `pre_deploy` failing aborts before any traffic moves; `post_deploy` failing is a deployment
  with `warnings` and the `deploy_hook_failed` notification. Prisma's automatic migration aborts
  on failure and does not run when hooks are declared.
- **Schema changes are a decision.** A hook marked `migrates` (read against the migration tools'
  "nothing applied" wording) or Prisma applying a migration sets `deployments.schema_changed`;
  every way back (`rollback`, `releases rollback`, `update --commit`, files-only restore, the
  API and the console) goes through `lifecycle.require_schema_change_confirmed` and refuses
  without `schema_changed_ok`. Noust puts code back, never a database. The automatic go-back
  after a failed gate still happens and says so first.
- **An operator's site is never rewritten or deleted** (`helpers/site.py`; the "Generated by
  Noust" marker decides). A domain becomes a site file in one place, `webserver.config_path`
  through `site_name_for`, because an adopted app's file may not be named after its domain
  (`proggest` for `proggest.es`).
- **The relay** (`compose_relay.py`) shares its parts with blue/green and is opt-in through the
  same `zero-downtime` command. nginx reaches each relayed service through
  `/etc/nginx/noust-upstreams/<app>/<service>.servers` (`core/paths.py`; upstream names keep the
  `wasm_bg_` prefix); an operator's site includes it itself and Noust prints the exact line. An
  interrupted update leaves a relay that the next update and `noust setup doctor` resolve.
- **Adopting never changes what runs.** `compose_adopt.py` reads the project from the
  containers' labels (`apps.compose_project`, passed as `-p` everywhere), proves the stack with
  `up --dry-run --no-build`, creates and enables the unit without starting it. No Compose
  update cleans a checkout: it is brought to the branch and what git does not track (the `.env`,
  bind-mounted data) stays.
- **A stack's databases are dumped before every Compose update**, in the pre-update backup, and a
  failed dump stops the update (`StackBackupError`): without it a migration cannot be undone.
  Passwords are read inside the container, never in argv. `backup_before_update` and
  `backup.databases: off` turn it off.
- **`siteconf` is the one nginx/Apache parser.** `render(parse(text)) == text` for any text the
  server accepts; edit operations change only the bytes of the element they name; `route` applies
  the server's own location algorithm. The API (`structure`, `config/edit`, `route`, `topology`)
  writes nothing: the visual editor produces text and saves it through the one `PUT .../config`
  (rule 3 and 4). A site's config test runs inside the live `nginx.conf` with the enabled sites.
- **One definition of a worker**: `compose_ports.is_headless_stack` (no TCP port published).
  Deploy, store, `diagnose`, the monitor and the summaries judge it by its containers.
- **Accounts and the sandbox.** `app_identity.service_account` is the only answer to "as whom";
  new apps get `noust-app-<name>`, existing ones only by `noust app identity migrate`. The
  sandbox is the default for apps from before 3.1 through `sandbox_trial.trial_before_update`: it
  never breaks an update that worked.
- **Monitor and jobs.** `app_unreachable`/`app_recovered` come from the monitor's `reachability`
  (three failures over a minute, the deploy's own `HealthCheck`); process samples (top 5 CPU and
  memory per minute, commands redacted) feed `GET /api/timeline`; a job records its transient
  unit (`jobs.unit`) so `job_reconcile` finishes it after a console restart.
- **Store v13** is `core/schema_v13.py`; every new column has its own validated setter and
  survives a redeploy's full-row write. The harness (`tests/integration/run.py`) now has Docker
  inside; `--scenario proggest` deploys a clean clone of Proggest, and nothing ever runs against
  the host's Docker or its `proggest*` containers.

## Fleet

A central is a Noust whose job is several other Noust servers (`central.role`: `hub` never
deploys anything itself, `server` also runs its own applications). It reaches each one over an
SSH tunnel it opens outward and drives it through its own API: rule 3 again, so a central
re-implements nothing a node does. `web/api/node_proxy.py` forwards every call, the event
stream and the log and job WebSockets over that tunnel; nothing runs twice.

- **The central never gets a shell.** `noust fleet authorize` runs on the node, as root, and
  installs the central's key for the unprivileged `noust-tunnel` account (no home, `nologin`,
  keys in a root-owned file), with a Noust `Match User` block in sshd
  (`AllowStreamLocalForwarding no`, `AllowTcpForwarding local`, `PermitOpen 127.0.0.1:<port>`,
  `PermitTTY no`, `ForceCommand /usr/bin/false`) and the same limits repeated as key options
  (`restrict,port-forwarding,permitopen=...,permitlisten="127.0.0.1:1",command="/usr/bin/false"`,
  built in `fleet/authorize.py`). Both layers matter: `permitlisten` narrows TCP `ssh -R` but not
  Unix-socket forwarding, which with a key in root's `authorized_keys` (3.0) could create a socket
  as root at any path. `--ssh-user root` exists only behind an explicit confirmation.
- **Each node sets a ceiling for its central.** `noust fleet authorize --access read|deploy|admin`
  (default `admin`) and `--allow-host-access` (SSH keys, sshd, firewall: off by default) are
  enforced by the node in the fleet admission (`fleet/policy.py` `permits()`), whatever the
  central claims; the central reads them from `GET /api/auth/fleet/self`.
- **Fleet tokens are accepted only from loopback.** They travel through the tunnel, never
  across the network, and only a request presenting one may carry `X-Noust-Actor`, so a node's
  audit log names the operator behind the central rather than just the central's name.
- **Elevation is the node's call, not the central's.** A node marks what needs sudo mode with
  the `x-noust-requires-elevation` OpenAPI extension; the central asks its own operator to
  confirm before forwarding such a call and vouches for it with `X-Noust-Elevated`, and the
  node refuses the call anyway if the central did not vouch - one source of truth, checked on
  both sides, so a compromised or outdated central cannot forward its way past a node's sudo
  mode.
- **A host-key change is never accepted silently.** Each node's key is pinned to the central's
  own `known_hosts` the first time it is added; a change closes the tunnel and says why instead
  of trusting whatever key answers next.

See `docs/CENTRAL.md` for running a central, sealing its secrets and adding a server.

---

## Conventions

- App name: the domain with dots as dashes (`shop.example.com` is `shop-example-com`). It
  names the directory `/var/www/apps/{app_name}/` and the unit `{app_name}.service`; units
  from before 0.14.1 keep a legacy `wasm-` prefix, and application units are never renamed.
  Noust's own units are `noust-web`, `noust-monitor`, `noust-previews`, and the cron and
  backup timers `noust-cron-*` and `noust-backup-*`; they carry the "Generated by Noust"
  marker, and "Generated by WASM" is still recognised.
- Paths: `/etc/noust`, `/var/lib/noust` (store `noust.db`), `/var/backups/noust`,
  `/var/log/noust`. Ask `core/paths.py`, never spell one: it resolves a WASM directory that
  has not moved yet. A 2.x server is moved by `core/migrate_from_wasm.py` (`noust
  migrate-from-wasm`, also run by the packages and on the first root command outside a
  systemd unit; `NOUST_NO_AUTO_MIGRATE=1` turns that off), which leaves the `wasm` names as
  symbolic links. The `wasm` command is an alias of `noust` for the whole 3.x series: never
  break it.
- Environment variables are `NOUST_*`, read through `paths.getenv`, which falls back to the
  `WASM_*` spelling.
- Some `wasm` names stay on purpose, because clients, repositories or data on disk already
  use them: the cookies `wasm_session`/`wasm_csrf`, the `X-WASM-CSRF` header, the WebSocket
  subprotocols `wasm.auth`/`wasm.token.`, the app-tree files `.wasm/`, `.wasm-runtime.json`,
  `.wasm-php.json`, `.wasm-php-tmp`, the backup payload dir `wasm-backup`, the export format
  `wasm-app`, the `wasm_ro_` database roles, the `wasm_bg_` upstream names, the git key
  `wasm.branch`, the `wasm-previous` image tag and the remote folder `wasm-backups`. Do not
  rename them within 3.x; accept both names where a new one was added (`noust.nginx.yaml`
  and `wasm.nginx.yaml`, `noust_tok_` and `wasm_tok_`, `noust_only` and `wasm_only`).
- Owner feedback is numbered: items 1-52 shipped in 3.1; 3.2 continues from 53 (53-64 are in the
  spec's section 8). Specs, plans and commit bodies cite them as "item N".
- Google-style docstrings on everything public, with Args/Returns/Raises.
- Type hints everywhere, modern syntax (`X | None`, `list[str]`).
- Actionable errors: `raise DeploymentError("what happened", details="how to fix it")`.
- Comments explain **why**, never what.
- No emojis in code, comments or commit messages. No AI assistant references in commits.
- Absolute paths in systemd units; `shutil.which()` or `/usr/bin/`, never nvm paths.
- Noust requires root. There is no `sudo` inside argv and no `run_command_sudo`.

---

## Commands

```bash
pip install -e ".[all,dev]"     # development install

pytest                          # tests
pytest --cov=noust              # with coverage
ruff check src/noust tests      # lint (blocking in CI)
ruff format src/noust tests     # format (blocking in CI)
mypy                            # types (blocking in CI)

python scripts/release.py --check         # version consistency
```

The console needs Node 22 (the major in `panel/.nvmrc`); CI runs every one of these:

```bash
cd panel && npm ci              # the lock file, exactly; never `npm install` in CI
npm run lint                    # eslint, zero warnings
npm run typecheck               # tsc, strict
npm test                        # vitest unit + component tests (axe included)
npm run check:api               # schema.gen.ts matches openapi.json (npm run gen:api to fix)
npm run build                   # writes src/noust/web/static: commit the result
npm run e2e                     # Playwright: every page, both themes, zero axe/CSP violations
npm run e2e:screens             # screenshots of every route, both themes + 390px mobile
```

To work on the console against the real API, run a seeded, sandboxed backend and the Vite
dev server, which proxies `/api`, `/events`, `/hooks` and `/ws` to it:

```bash
python scripts/console_server.py --port 8080    # prints {"url", "token", ...}; sign in with the token
cd panel && npm run dev                          # http://localhost:5173
```

`console_server.py` runs the real FastAPI app under uvicorn over a store seeded by
`tests/panel_factory.seed_console_state`, with every system path redirected into a temporary
directory and a fake runner answering systemctl, journalctl, nginx and certbot, so nothing on
the development machine is touched. `--totp` turns on the second sign-in factor; the E2E suite
starts one per Playwright worker.

---

## Releasing

The version has one source of truth: `[project].version` in `pyproject.toml`.

```bash
python scripts/release.py 1.0.0 -m "Summary of the change"
git commit -am "v1.0.0: Summary"
git tag -a v1.0.0 -m "Release v1.0.0"
git push && git push origin v1.0.0
```

`scripts/release.py` propagates to `setup.py`, `src/noust/__init__.py`, the RPM spec in
`rpm/`, the `.dsc` in `obs/` and both changelogs. CI runs `--check` and refuses to publish on a mismatch.
Do not edit those files by hand.

GitHub Actions publishes to PyPI and OBS on tag push, and the central's container image to
`ghcr.io/perkybeet/noust`.
**OBS builds**: https://build.opensuse.org/package/show/home:Perkybeet/noust (15-30 min)

Distribution names: `noust` on PyPI and OBS. The names WASM shipped under stay as
transitional packages through 3.x, each one only depending on `noust`: `wasm-cli` on PyPI,
`wasm` in the Debian build and `wasm-cli` in the RPM one (`noust` declares `Conflicts`, never
`Obsoletes`: see `docs/RENAME.md` for why). Do not drop them within 3.x. Their sources are in
`packaging/transitional/`; the container in `packaging/container/`.

---

## Packaging notes

The OBS tarball is produced with `git archive HEAD`, so **only committed files ship** and
the build environment has no network. That is why the console's Vite build is committed to
`src/noust/web/static/`: Node never runs during packaging, and the `web/static/**/*` glob in
`pyproject.toml` ships it in the wheel, sdist, deb and rpm. Frontend dependencies are
build-time only; nothing but the build ships, and it loads nothing from a CDN. The CI `panel`
job rebuilds and fails when the committed build differs from what `panel/` produces, so
after any change under `panel/src` run `npm run build` and commit `src/noust/web/static`.

When adding a dependency, declare it in all four places: `pyproject.toml`, `setup.py`,
`obs/debian.control` and the RPM spec in `rpm/`. `tests/test_architecture.py` fails if an import is
undeclared. Check the package exists on every target: `python3-inquirer` does not exist in
Debian or Ubuntu, which is why interactive mode never worked there.

| Import | Debian | RPM |
|--------|--------|-----|
| `click` | `python3-click` | `python3-click` |
| `jinja2` | `python3-jinja2` | `python3-jinja2` (openSUSE: `python3-Jinja2`) |
| `yaml` | `python3-yaml` | `python3-pyyaml` (openSUSE: `python3-PyYAML`) |
| `rich` | `python3-rich` | `python3-rich` |
| `questionary` | `python3-questionary` (Recommends: absent on Debian 12, Ubuntu 22.04) | `python3-questionary` |
| `fastapi` | `python3-fastapi` | `python3-fastapi` |
| `starlette` | `python3-starlette` | `python3-starlette` |
| `pydantic` | `python3-pydantic` | `python3-pydantic` |
| `uvicorn` | `python3-uvicorn` | `python3-uvicorn` |
| `psutil` | `python3-psutil` | `python3-psutil` |

Every model the API adds goes through `web/pydantic_compat.py`: Ubuntu 24.04 and Debian 12
ship pydantic 1.10, and a CI job pins it.

---

## The panel

**The design system is `docs/DESIGN.md`, and it is normative**: tokens, the seven page
templates (`components/page`), components, patterns and copy rules. A page is built from a
template and kit components; when something is missing the kit (and its gallery at `/__design`)
grows first. ESLint rules and a ratchet (`styles/design-rules.test.ts`, baseline that only goes
down) enforce it; read it before touching `panel/src`.

The Noust console is a React single-page application. Its source lives in `panel/`; its build
is committed to `src/noust/web/static/` and served by `server.py`: hashed chunks under
`/assets` (cached immutable for a year) and `index.html` (`no-store`) for every GET outside
`/api`, `/assets`, `/events`, `/ws`, `/hooks` and `/health`, where a miss answers JSON. The
backend renders no pages: the console is a client of the JSON API, the `/events` stream
(`machine`, `metrics`, `app`, `job`, `state`, `notice`) and the log and job WebSockets, like
any script. Destructive endpoints depend on `require_elevated` (sudo mode, `api/deps.py`);
the console answers the `403 elevation_required` with its "Confirm it's you" dialog and
retries once.
FastAPI's OpenAPI schema is exported to `panel/openapi.json` and compiled into
`panel/src/api/schema.gen.ts`, so the console cannot call an endpoint that does not exist.

The Content Security Policy is strict: `script-src 'self'; style-src 'self'`, no
`unsafe-inline` anywhere, and `require-trusted-types-for 'script'`. That rules out inline
`<script>`/`<style>`, style attributes in markup, `data:` fonts (hence `assetsInlineLimit: 0`
in `vite.config.ts`) and every string-to-DOM sink (`innerHTML`, `insertAdjacentHTML`, `eval`).
React sets styles through the CSSOM, which the policy allows, and Base UI runs with
`disableStyleElements`. Never add a library that injects `<style>` elements or writes HTML
strings (Radix, sonner and xterm all do, which is why they are not used). A policy is only
enforced in a browser, so the E2E suite collects every `securitypolicyviolation` and console
error and fails on any, and runs axe on every page in both themes (WCAG 2.2 AA, zero
violations).

Colour only ever encodes state (running green, in progress amber, failed red, stopped grey),
plus the violet accent for interactive elements; every state also has a shape and a text
label. Navigation, surfaces and text are achromatic, so anything coloured on screen is
telling the operator something. The design direction is D8 in
`docs/superpowers/specs/2026-09-25-wasm-v2-design.md`. UI copy is English and Spanish, sentence
case, and lives only in the typed catalogs of `panel/src/i18n` (rules and glossary in its
README): whole sentences with placeholders, never fragments.

A system error is never paraphrased. Show nginx's or systemd's own output verbatim in mono,
with the suggested fix above it.
