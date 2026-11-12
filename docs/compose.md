# Docker Compose

How Noust deploys, updates and looks after a Docker Compose stack, and the 3.2 features that
a stack gets first: deploy hooks and migrations, going back past a migration, updates without a
cut, adopting a stack that already runs, copies of its databases, deploying by tag and workers
that publish no port. Hooks, the rollback check, the site you wrote yourself and deploying by
tag also apply to other application types where this guide says so.

The rest of this guide is on pages of its own:

- [noust.yaml, hooks and migrations in a stack](compose-hooks.md)
- [Zero-downtime updates (the relay)](compose-relay.md)
- [Adopting a running stack](compose-adopt.md)
- [Stack database backups and restore](compose-backups.md)

## What Noust does with a Compose stack

A stack is deployed in place: the directory holds the checkout, the `.env`, and whatever the
containers write next to the code. It runs under one systemd unit, `<app>.service`, which runs
`docker compose up -d` and `down`, and an update rebuilds the images, recreates the
containers, judges the result with the same health gate as any other application and puts back
the images that served when it does not pass. That part is unchanged since 2.1 and is
described in [releases.md](releases-compose.md#docker-compose-and-monorepo-applications). A stack that
publishes no port is a worker: no site, no certificate, judged by its containers.

What 3.2 adds, each described below:

- [`noust.yaml`](compose-hooks.md#noustyaml): a file in the repository that hangs commands on a deployment
  (`pre_deploy`, `post_deploy`) and declares the stack's databases. [Operator
  hooks](compose-hooks.md#operator-hooks) replace it from the server.
- [A migration that fails stops the update](compose-hooks.md#noustyaml), and a deployment records whether it
  changed the database schema, so [going back past it asks first](compose-hooks.md#rolling-back-past-a-migration).
- [Updates without a cut](compose-relay.md#zero-downtime-updates-the-relay): each web service is recreated
  while a twin of it serves.
- [A site you wrote yourself](#your-own-site) is never rewritten or deleted.
- [Adopting a stack that already runs](compose-adopt.md#adopting-a-running-stack), without touching it.
- [A copy of the stack's databases before every update](compose-backups.md#stack-database-backups-and-restore),
  and a restore that puts them back.
- [Deploying a tag or a release](#deploying-by-tag) instead of a branch.
- [Workers without a web port](#workers-without-a-web-port) are judged by their containers.
- [Which service gets `/`](#which-service-gets-the-root-route), and more
  [options for `noust.nginx.yaml`](#routing-with-noustnginxyaml).
- [Smaller fixes](#other-things-that-changed-for-compose): ports, volume names, placeholders.

Nothing changes for an existing stack until its operator asks, except that an update now copies
the stack's databases first (and stops if it cannot) and the fixes under [Other things that
changed](#other-things-that-changed-for-compose).

## File, profiles and extra arguments

A stack is created with a compose file and profiles (`noust create --compose-file
--compose-profile`) and keeps them in its unit. They, and a few arguments of `docker compose build`
and `up`, can be changed afterwards:

```bash
noust app compose show shop.example.com
noust app compose set shop.example.com --file deploy/compose.prod.yml --profile web --profile jobs
noust app compose set shop.example.com --build-args '--pull --build-arg VERSION=1.2' --up-args '--wait'
noust app compose set shop.example.com --service web --service worker   # build and start only these
noust app compose reset shop.example.com
```

The same is `GET|PUT|DELETE /api/apps/{domain}/compose` (changing them needs sudo mode). A new
file must be in the checkout (it is refused otherwise, with nothing changed) and, with new
profiles, is written to the unit too, so a start at boot brings up the same stack. The arguments
are a **closed list**, never a shell line: for `build`, `--pull`, `--no-cache`, `--quiet`,
`--with-dependencies`, `--build-arg KEY=VALUE`, `--progress auto|plain|tty|quiet`; for `up`,
`--wait`, `--wait-timeout N`, `--timeout N`, `--force-recreate`, `--no-deps`,
`--renew-anon-volumes`, `--always-recreate-deps`, `--scale SERVICE=N` (at most 50) and `--pull
always|missing|never`. `--build` and `--no-build` are not on it: Noust decides when to build.
An update used to ignore the profiles in the unit, so a service under a profile was left out of
every rebuild; it reads them now.

## Editing the document of a stack deployed from one

A stack created from a document (pasted into the console's **Docker Compose** form, converted from a
Coolify template, or given as `compose_document` to `POST /api/apps`) has no repository: the
document is its source, kept beside the store and fetched again by every update. It can be read and
replaced without deleting the stack:

```bash
noust app compose document shop.example.com                    # print it
noust app compose replace shop.example.com --from new.yml --check   # judge it, write nothing
noust app compose replace shop.example.com --from new.yml           # keep it
noust app compose replace shop.example.com --from new.yml --apply   # keep it and update now
cat new.yml | noust app compose replace shop.example.com --from -
```

- **The guard is the creation's.** The same analysis that refuses a pasted document refuses a
  replacement: root on the host (`privileged`, the Docker socket), a `build:` or an `env_file:` that
  points at files a document does not have. A variable the document **requires**
  (`${NAME:?message}`) that the stack has no value for is refused too, naming it and the
  `noust env set` that gives it. Nothing is written until it all passes, and a document identical to
  the one the stack has changes nothing.
- **Writing rebuilds nothing.** The stack runs the new document at its next update
  (`noust update`), and until then its pending changes list **Compose document** as needing a
  build, without showing the text. With `--apply` the update runs now behind the health gate, and
  when the new document does not come up the previous one is put back where the next update reads
  it and the stack runs it again, the same way a failed recipe update is undone.
- **Reading is a reveal.** A document can hold a value typed into it, so reading it asks for the
  permission that reveals secrets and is audited (`apps.compose.document`); saving needs sudo
  mode, because the document decides what runs as root's containers.
- **Not every stack has one.** A stack from a repository keeps its compose file in git (edit it
  there and update, or choose another file of the checkout with `noust app compose set --file`); an
  adopted stack runs the file it already had; a stack installed from a recipe is the recipe's,
  updated with `noust recipe update`. Each of them answers with that instead of an error, and
  `GET /api/apps/{domain}/compose/document` says `editable: false` and why.

Over the API: `GET /api/apps/{domain}/compose/document` reads it, `POST .../compose/document/check`
judges a candidate (the services, the variables, what refuses it and which required variables the
stack lacks) and `PUT .../compose/document` keeps it; the update that runs it is
`POST /api/apps/{domain}/update`.

## Variables the compose file asks for

A compose file interpolates `${NAME}` from the project's `.env` (and from the server's
environment). `${NAME:-default}` has a default of its own, `${NAME:?message}` refuses to run
without a value, and a plain `${NAME}` that nothing sets becomes an empty string with a warning
that scrolls past in a build log. Noust reads the file (the document, so a commented `# ${OLD}` is
not a variable) and says what it asks for and which of it the stack has:

```bash
noust app compose variables shop.example.com
noust env set shop.example.com DB_PASSWORD=...     # how a value is given
```

| State | Meaning |
|---|---|
| `set` | The `.env` (or the server's environment) gives it a value. |
| `default` | Nothing gives it a value and the file has a default. |
| `unset` | Nothing gives it a value and the file has no default: Compose will use an empty string. A warning in the deploy log. |
| `missing` | The file **requires** it (`${NAME:?message}`) and nothing gives it a value. |

- **A deploy or an update refuses first.** With a `missing` variable the stack is not built and
  nothing that serves is touched; the error names each variable with the file's own message and
  the `noust env set` that gives it a value. A first deploy reads the `.env.example` first, so a
  secret it lists is generated before the check. A preview is not judged: its variables come from
  its parent's.
- **`${NAME:?}` and `${NAME:-x}` read an empty value as unset; `${NAME?}` and `${NAME-x}` do
  not**, as Compose does.
- **Values are never shown**, only whether there is one. The `.env` read is the one beside the
  compose file (the project directory) over the one at the root of the application.
- The same list is `GET /api/apps/{domain}/compose/variables`, and in the console it is under the
  table of **Environment**, where **Add** starts the add dialog from the variable's name. Giving a
  value is the environment's own `PUT /api/apps/{domain}/env`.
- The `SERVICE_*` variables Coolify generates are not a Compose feature; Noust's recipes declare
  their generated variables themselves ([recipes.md](recipes.md)).

## The Docker networks of a stack

Compose gives each stack a network of its own, so by default two stacks cannot reach each other.
That quietly stops being true when a service is attached to a network another stack uses: an
external proxy network, Docker's default bridge, the host's own network. Anything on a shared
network reaches everything else on it, and nothing on the stack's page said so.

```bash
noust app networks shop.example.com            # the networks, who is on them, what is worth knowing
noust app networks shop.example.com --json
```

It lists every network the stack's containers are on and every network its Compose project made,
with the driver, the address ranges and each container on it (the stack's own, and the others
marked). It says in one line whether the stack is isolated, meaning every network is its own and
nothing else is on it, and for each network what Docker reported that is worth a second look: it is
shared and with whom, it is Docker's default bridge or the host's network, another stack made it, a
person made it, or it is internal and cannot reach the outside.

It is **read only on purpose**. Creating, scanning and adopting the host's Docker networks is the
host's Docker administration, not what Noust is for, and one flat network that everything and the
proxy join is the shape this exists to point out, not to copy. Noust gives a stack what Compose
gives it and shows the result as Docker says it. Fixing a shared network is editing the stack's own
compose file: give it a network of its own, or `internal: true` for one that needs no way out.

API: `GET /api/apps/{domain}/networks` (`apps.read`; an application of another team is a 404, and
one that does not run in Docker answers that it has no network). Console: **Application, Settings, Compose,
Docker networks**, for a Compose stack. The listing is Docker's, read when the page is opened.

## Which service gets the root route

With several services that publish a port and no `noust.nginx.yaml`, `/` goes to the **service
in front**: the web service that depends on another web service and that no other web service
depends on (in Shop, the Next.js frontend that depends on the NestJS backend). When there is
none, to the first web service in the compose file. A port given at deploy time (`--port`) wins.

Noust no longer invents `/<service>` routes for the others: a frontend routed at `/frontend`
does not work with Next.js, and a backend left at `/` was the wrong service. The other web
services are listed in a warning with how to route them, with a `noust.nginx.yaml` (see below).

Every reader of a stack's ports is one: the short form with and without an address
(`3000:3000`, `127.0.0.1:3000:3000`, `[::1]:3000:3000`), ranges (`3000-3002:3000-3002`),
protocols (`53:53/udp`, which is not a web port), the long form with `target`, `published` and
`host_ip`, and values Compose already resolved. Before, three readers each had their own, and
the one choosing the site's port failed on `127.0.0.1:3000:3000` and fell back to 3000.

The file is read the way Docker Compose reads it, not as YAML 1.1 does: `- 2222:22` is the string it
is, where PyYAML takes it for a number in base 60 (133342) that no port could be, and `restart: no`
is `no`, not `false`. The Compose documentation tells people to quote ports whose container side is
under 60 for this reason; a document that does not is still read right (`noust.core.compose_yaml`).
A port that reads a variable (`127.0.0.1:${PORT:-80}:80`) is read at its default, and a stack that
is deployed gets `PORT=<the port its site proxies to>` in its `.env` when it publishes on `${PORT}`.

## Routing with noust.nginx.yaml

A `noust.nginx.yaml` (or the file name of before 4.0, which still works until 5.0 and which a deployment asks you to rename) at the root of the
repository gives the site routes of its own, and tunes the plain proxy site. Docker Compose
stacks and the single-process application types read it; monorepos do not. The schema is
closed and every value is checked to be exactly what its directive takes: a key nothing reads, a
bad value or a `static` path that leaves the application is reported with its field, and a file
with a problem is **not used at all** (the site proxies `/` to the root service and the log
says why), never half applied.

| Top-level key | Meaning |
|---|---|
| `routes` | The list of routes, below. |
| `max_body_size` | `client_max_body_size` for the whole server: a number with an optional `k`, `m` or `g` (`20m`). |
| `rate_limit` | A rate (`100r/s`, `10r/m`) applied to every route that proxies and has none of its own. In 3.1 it declared a zone nobody used. |
| `security_headers` | Headers added to the defaults (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`). |
| `custom_directives` | Lines added to the server block. |

A route is **one of** `port`, `static` or `return`:

| Route key | Meaning |
|---|---|
| `path` | The location path, starting with `/` (default `/`). Paths are unique. |
| `port` | Proxy to `127.0.0.1:<port>`, 1 to 65535 (a route without `port`, `static` or `return` means 3000). |
| `static` | Serve files from this directory of the application, relative to its root, no `..`: an `alias`, with no process involved. |
| `cache` | `expires` for a `static` route: `1y`, `30d`, `12h`, `max`, `off`. |
| `return` | A redirect: `{code: 301, to: /new}`; the code is 301, 302, 303, 307 or 308. |
| `name` | Name of the upstream, derived from the path when left out. |
| `websocket` | `true` adds the upgrade headers. |
| `rate_limit`, `rate_limit_burst` | This route's rate (`10r/s`) and burst (default 5). |
| `max_body_size` | `client_max_body_size` for this route only. |
| `buffering` | `false` turns `proxy_buffering` off (server-sent events, streams). |
| `timeout`, `read_timeout`, `send_timeout` | Seconds, 1 to 86400. `timeout` defaults to 60 and is the default of the other two. |
| `buffer_size`, `strip_prefix` | `proxy_buffer_size`; `strip_prefix: true` removes the route's path before proxying. |

A file where no route names a port (routes that only serve files, redirect, or carry options for
`/`) tunes the plain proxy site instead of replacing it. For example, a stack like Shop:

```yaml
max_body_size: 25m
rate_limit: 50r/s
routes:
  - path: /api/
    port: 3000
    max_body_size: 100m
    read_timeout: 300
  - path: /socket.io/
    port: 3000
    websocket: true
    buffering: false
  - path: /assets/
    static: public/assets
    cache: 1y
  - path: /portal/
    return: {code: 302, to: /portal/dashboard}
  - path: /
    port: 3001
```

## Your own site

An application's site is a file Noust writes, carrying the line "Generated by Noust". A site
without that line was written by someone else, and since 3.2 no deployer rewrites it or deletes
it, whatever the type: deploy, update, certificates, a failed deploy's undo and deleting the
application all leave it alone. 3.1 respected it for single-process applications only; a Compose
stack's was replaced on every deploy, and a certificate step deleted it.

- What Noust would need it to contain is checked, and reported as warnings with their fix:
  in the deployment's log, a `server_name` that lacks a name recorded for the application
  ("Its server_name does not include ..."), and, for [the
  relay](compose-relay.md#zero-downtime-updates-the-relay), a site that includes no servers file of Noust's
  (the update then recreates the containers with a cut and says how to see what is missing).
  `noust app zero-downtime DOMAIN` prints the exact line a site needs.
- **`noust delete`** removes everything else and says what it kept: "has a nginx site
  configuration Noust did not write; it is left in place, and its certificate with it", and, for
  a stack that was relayed, that the servers files stay while the site includes them. Remove them
  by hand when nothing should answer on the name: `noust site delete DOMAIN`.
- **A site whose file is not named after the domain** (`shop` for `shop.example.com`) is found by
  the names it serves when the application is [adopted](compose-adopt.md#adopting-a-running-stack), and its name
  is recorded (`site_name`). Every command that turns a domain into a file asks that one place,
  so `noust site`, the certificates and the console all find it.
- **Aliases and certificates.** Docker Compose and monorepo applications now accept
  `noust domain add`, as every other type does. For a site you wrote, Noust records the name
  and warns that its `server_name` does not list it: add it yourself.

See [domains.md](domains.md#operator-sites) for domains and aliases.

## Workers without a web port

A stack whose services publish no TCP port on the host is a worker (it fetches, processes and
sends, and nobody connects to it). There is one definition of that for the whole product: no
service publishes a TCP port. A port published at a random host port, or in a form that cannot
be read here (`${PORT}:3000`), still counts as published, so a stack is never wrongly called a
worker and loses its site.

A worker is judged by its containers, not by a port nothing listens on: it is well when every
container runs, or ran and exited with 0 (a migration, a seed), none keeps restarting, none is
dead, none exited with an error and none reports itself `unhealthy`. `noust list`, `noust
health`, `noust diagnose`, the console and the monitor all use that. `noust diagnose` shows the
state of its containers and the last lines of `docker compose logs`, and the monitor sends
`app_unreachable` and `app_recovered` for it when a container exits with an error or restarts
in a loop (see [MONITOR.md](MONITOR.md#what-it-watches)). A deployment of one skips the site and
the certificate.

**A stack registered with a port by 1.x.** 1.x gave every stack the default port 3000 and
wrote it a site, so a healthy worker reads as "nothing answers on its port", and its domain
answers `502`. `noust health`, `noust diagnose` and the application's page point at the fix:

```bash
noust app headless worker.example.com                  # clears the port; asks about the site
noust app headless worker.example.com --remove-site    # and removes the site Noust wrote for it
noust app headless worker.example.com --keep-site
```

Nothing is restarted. The site is removed only when you say so, Noust wrote it and the
application answers on no other name; a certificate is left as it is. It is never done on its
own. `GET /api/apps/{domain}/headless` (`apps.read`) says whether the stack is a worker with a
port recorded and whether its site can go; `POST` (`apps.manage`, sudo) does it, with
`{"remove_site": true}` to remove the site.

**A worker that starts publishing a port.** A stack adopted or deployed while it published nothing
has no port recorded. Publish one in its compose file and update it: the update records the port the
file names, so the health gate probes it and `noust list` and the console judge the stack as the web
it has become. The update does not write a site for it: an adopted stack's site is yours.

## Scheduled tasks of a stack

A cron job associated with a stack can run **in one of its services** instead of on the server:

```bash
noust cron create nightly-report "php artisan reports:send" --schedule daily \
  --app shop.example.com --service app --timeout 600
```

The command runs by `docker compose exec -T` in the container of that service, as an argument
vector like any other task: nothing is put behind `sh -c`, so a container without a shell runs a
task as well as one with. The program is the container's, not the server's, so it is not looked up
on the server. `--timeout SECONDS` (1 to 86400) is the longest the command may run; past it the
command is stopped and the run counts as failed, which the job's history shows. Over the API,
`POST /api/cron` takes `timeout` and `service`, and the console's job dialog has both fields
(the service only for a job of an application).

When the client is stopped, Docker leaves the process it started inside the container running:
the timeout ends the run Noust waits for, not necessarily the process in the container, so give a
task that can hang its own deadline as well.

## Deploying by tag

An application follows a branch, as always, or the tags that match a pattern. Following tags
fits a project that deploys on a release and is what replaces a hand-written CI gate.

```bash
noust create -d shop.example.com -s git@github.com:you/shop.git --follow-tags 'v*'
noust app follow-tags shop.example.com 'v*'    # an application already deployed
noust app follow-tags shop.example.com         # what it follows
noust app follow-tags shop.example.com --off   # the branch again
noust update shop.example.com --tag v1.4.0     # one tag, now
```

- **The pattern** is a glob over tag names (`v*`, `release-*`), case-sensitive like git's.
  Only a git source can follow tags, and not together with a pinned branch (`noust app branch
  DOMAIN --unpin` first). Setting it rebuilds nothing: the next release, or `noust update
  DOMAIN`, which deploys the newest matching tag. `noust create --follow-tags` asks the remote
  first and fails at once, before anything is built, when no tag matches.
- **Tags are ordered as versions,** never as text: `v1.10.0` is newer than `v1.9.0`, and a
  release is newer than its own `-rc.1`. A tag with no version number (`latest`, `nightly`) has
  no place in the order and is not deployed.
- **Never backwards.** A tag that is not newer than what is deployed is refused:
  `noust update --tag v1.2.0` after v1.3.0 says so and points to `noust rollback`, and a webhook
  for it is ignored and recorded.
- **The webhook** of an application that follows tags deploys what the forge announces: a
  **published release** (GitHub or Gitea `release` events with the action `published` or
  `released`, not a draft and not a pre-release) and a **tag push** (GitHub, Gitea, GitLab and Bitbucket Cloud).
  Each queues an update to exactly that tag, resolved in the repository. A push to a branch
  deploys nothing. Enable the `release` event at the forge besides the push. Ignored deliveries
  appear in `noust app webhook deliveries DOMAIN` and in the console with the reason, as
  `ignored_tag` (a draft, a pre-release, a tag the pattern does not match, not a version, already
  deployed, older than the deployed one, or already being deployed) and `ignored_branch`.

| Endpoint | Method, permission |
|---|---|
| `/api/apps/{domain}/follow-tags` | `PATCH` `apps.manage`, sudo: `{"pattern": "v*"}`, or `{"pattern": null}` to follow the branch. `GET /api/apps/{domain}` shows `follow_tags`. |

In the console: the application's Code and build shows the tags it deploys and **Follow
tags...** changes them.

## A stack running outside its unit

When someone runs `docker compose up -d` by hand while the stack's unit is stopped, the site
serves but Noust does not supervise it, and nothing done to the unit reaches it. Whether a reboot
brings it back is the containers' own `restart` policy: Docker starts `always` and
`unless-stopped` containers again when it starts, and not those with `no` or `on-failure`, so
Noust reads the policy (one `docker inspect`) and names the containers a reboot would not bring
back, or says that Docker restarts them, instead of assuming. Noust shows that as its own state,
**Running outside Noust**
(`running_unmanaged` in the API), never as stopped: `noust list`, `noust health`, the console,
the overview and the fleet summary all say it, and the monitor announces it once (a warning
under the "Service down, and back" switch), not again until it has stopped running outside.

Only stacks whose unit exists and is stopped are looked at, all of them with one `docker ps` (and
one `docker inspect` of what runs); a unit that failed stays failed.

Hand it back to its unit:

```bash
noust app reclaim converter.example.org
```

or "Hand it back to Noust" on the application's page (`POST /api/apps/{domain}/reclaim`, sudo
mode). The unit is enabled and started. Starting it runs `docker compose up -d`, which leaves
containers that already run as the compose file says exactly as they are; that is proven first
with the same `docker compose up --dry-run` adoption uses, and a start that would recreate
something is refused with Compose's output until you accept it (`--accept-recreate`,
`accept_recreate` in the API).

## Other things that changed for Compose

- **`noust create --force` and a directory that holds data.** A Compose stack keeps its data
  beside its code, so with the type named (`-t docker-compose`) the deployer brings a git
  checkout that is already there to the branch in place (fetch and `reset --hard`, never
  `git clean`) and keeps what git does not track, the `.env` and bind-mounted data, and says so.
  A directory with files that is not a git checkout is refused, with how to move it aside. The
  automatic type detection (the default) fetches before the Compose deployer is chosen, so name
  the type when the directory holds data. No Compose update, and nothing in an adopted stack,
  ever runs a clean checkout.
- **`.env.example` placeholders in Spanish** are never written verbatim. A variable whose example
  value starts with `genera-`, `generar-`, `tu-`, `pon-` (each also with `_`) or contains `cambiar`
  is a template, like `your-secret-key-here` always was: for a secret it is replaced by a generated
  value, in hexadecimal with the length the marker or the name says when it says one
  (`genera-clave-hex-64-caracteres` becomes 64 hex characters), and for anything else it is left
  out and named in one warning with how to give it (`--env-file`, or `noust env configure`).
  `genera-clave-hex-64-caracteres` used to end up as the key itself and keep a backend from
  starting.
- **Volume names** resolve to what Docker really created (`<project>_<key>`, or the `name:` the
  volume gives itself), so a volume backup no longer mounts an empty new one.
- **Project names.** An adopted stack passes its project with `-p` everywhere, including the unit.
- **The build sandbox and the compose file check** are unchanged: Compose builds run inside the
  Docker daemon, a new stack with `privileged` containers or the Docker socket mounted is
  refused unless the application has an exception, and an existing one is warned. See
  [releases.md](builds-and-accounts.md#builds-in-the-sandbox).
- **The check judges what Compose resolves the file to**, not only the `services` the file spells
  out: a service brought in by an `include` or an `extends`, a `jobs` entry, a service under any
  profile, the file of a `secrets` or `configs` entry, the volumes (`volumes_from: container:x`) or
  the namespace (`pid: container:x`) of a container that is not part of the stack. An `include` or
  an `extends` whose file is outside the application (a link inside it that leads out counts) is
  refused before Compose reads it, and a file that includes or extends and that Compose cannot
  resolve is refused rather than guessed. Noust asks Compose with
  `docker compose --profile "*" config --format json`, which changes nothing.
- **Deleting an adopted application keeps its directory** (see [above](compose-adopt.md#deleting-an-adopted-stack)).
- **Ignored webhook deliveries** carry the `ignored_tag` reason for applications that follow
  tags.
- **Images of the releases are kept**, so going back to one is instant instead of a rebuild
  (`noust releases list|rollback|keep` work on a stack): see
  [releases.md](releases-compose.md#kept-images-and-instant-rollback).

See also: [releases.md](releases.md) for releases, the health gate and rolling back,
[domains.md](domains.md) for domains and certificates, [console.md](console.md) for the console,
[api.md](api.md) for every endpoint and [security.md](security.md) for roles and sudo mode.
