# The recipe format

Part of [Recipes](recipes.md).

## The format

`src/noust/recipes/<name>.yaml`. The file name is the recipe's name, and `schema: 2` selects this
format. A file with no `schema` is the 2.3 format (one native recipe, `database.engine`, Jinja
`env`) and still loads. Anything unknown is an error naming the key.

```yaml
schema: 2
name: vaultwarden
kind: compose                     # native | compose
title: Vaultwarden
slogan: A lightweight Bitwarden-compatible password manager server.     # one line, 100 characters
description: One or two sentences, 400 characters.
category: security                # a closed list, below
tags: [passwords, bitwarden]      # up to 8, lower case, [a-z0-9-]
logo: simple-icons:vaultwarden    # the collection's mark, or logo.svg: recipes/assets/<name>/logo.svg
version: "1.37.4"                 # the upstream version this recipe pins; a quoted string
revision: 1                       # 1 by default; raised when only the recipe changed
updated: 2026-10-06               # when the recipe last changed
homepage: https://github.com/dani-garcia/vaultwarden
docs: https://github.com/dani-garcia/vaultwarden/wiki
repository: https://github.com/dani-garcia/vaultwarden
license: AGPL-3.0-only            # an SPDX expression
min_noust: "4.0.0"                # optional: the oldest Noust that can install it (enforced)
architectures: [amd64, arm64, arm]   # compose: required; every image is published for these (enforced)
needs: [Docker Engine with the Compose plugin]   # prose, shown to the operator
port: 8000                        # the host port nginx proxies to, near which a free one is chosen
requires: []                      # native only, below
variables: {}                     # below
health: {path: /alive, expect: "200", timeout: 60}   # what the health gate asks; timeout optional (5-600)
changelog:                        # up to 5 entries: what each version changed
  - {version: "1.37.4", summary: One sentence.}
notes:                            # what to tell the operator after the deploy (Jinja)
  - "Open {{ url }} and create your account now."
```

**Categories**: `ai`, `analytics`, `automation`, `backend`, `business`, `cms`, `communication`,
`data`, `development`, `files`, `media`, `monitoring`, `productivity`, `search`, `security`,
`smart-home`, `utilities`, `other`. The list is closed on purpose: a free-text category drifts
into several spellings of the same word ("Networking" and "networking"), and a filter by category
then misses half of them.

`min_noust` and `architectures` are applied, not only shown: an install or an update on a Noust
older than `min_noust`, or on a server whose architecture is not in `architectures`
(`amd64`, `arm64`, `arm`, `386`, `riscv64`, as Docker spells them), is refused with the reason
before anything is provisioned. Coolify emits `minversion` and `amd_only` and nothing reads them.
A native recipe may leave `architectures` out, meaning any; a Compose recipe may not, and
`scripts/recipe_pins.py check` reads from each image's index, by the pinned digest, what it is
published for and fails when the recipe says anything else.

**Notes** may use `{{ url }}`, `{{ domain }}`, `{{ https }}`, `{{ app_name }}`, `{{ app_path }}`,
`{{ port }}` and `{{ vars.NAME }}`, the value of a variable that is not a secret. A secret is
never in a note: it would end up in the job's log.

A recipe that cannot be deployed with this release is listed, not hidden:

```yaml
available: false
unavailable_code: licence          # why, as a code: licence, not_for_production, no_image,
                                   # not_an_application, host_ports, archived, platform_stack,
                                   # root_equivalent or no_sign_in
replaced_by: openbao               # optional: the recipe that stands in for it
unavailable_reason: The evidence: the file or page read, the day, and what it said.
```

It carries only the catalogue fields above. The code is what a screen translates and filters by: the
API answers it as a sentence of the server's words (`unavailable`, with its `code` and `params`, and
`unavailable_text` and `unavailable_hint` in English), `GET /api/recipes?available=false` lists what
is left out and `?unavailable_code=licence` narrows it, and `noust recipe list --unavailable
[--reason CODE]` does the same. `unavailable_reason` stays the evidence, to be folded under the
sentence. Installing a listed recipe is refused with that sentence. Vault, Excalidraw, PocketBase and
others are listed this way (see the end of [the catalog](recipes-catalog.md)).

### `kind: compose`

```yaml
digest_verified: true             # every image carries a digest read from its registry (default)
public_ports:                     # what the stack opens to the network, on purpose
  - {port: 22000, protocol: tcp, purpose: Syncthing's sync protocol.}
files: {noust.nginx.yaml: noust.nginx.yaml}      # recipes/assets/<name>/..., copied beside the document
compose: |
  services:
    app:
      image: ghcr.io/o/app:1.2.3@sha256:...
      restart: unless-stopped                    # required: the unit only runs `up -d` once
      environment:
        DOMAIN: "${DOMAIN}"
      volumes:
        - app-data:/data
      ports:
        - "127.0.0.1:${PORT}:8080"               # exactly one service publishes ${PORT}
  volumes:
    app-data:
```

`PORT` is the one variable Noust always provides, and no recipe may declare it: it is the port the
site proxies to, written to the stack's `.env` where editing it is refused. Every other `${NAME}`
the document uses is a variable the recipe declares, or carries its own `${NAME:-default}` or
`${NAME:?message}`; a declared variable the document never uses is an error.

`requires` is for native recipes. A Compose recipe cannot reach the server's databases (its
containers cannot see `127.0.0.1` of the host): its database is a service of its stack. It may ask
for `s3`.

A stack with a database follows the same few rules in every recipe, and
`tests/recipes/test_catalog_wave2.py` holds them:

- The database is the official `postgres`, `mysql`, `mariadb` or `mongo` image, with the account and
  the database named in its environment (`POSTGRES_USER` and `POSTGRES_DB`, `MYSQL_USER` and
  `MYSQL_DATABASE`, `MARIADB_*`), so that Noust dumps it before every update with the engine's own
  client. An image Noust does not recognise (PostgreSQL with pgvector or VectorChord) is declared
  by a `noust.yaml` the recipe ships (`files: {noust.yaml: noust.yaml}`), whose
  `backup.databases` names the service, the engine, the database and the user.
- Its data is in a named volume mounted where the engine keeps it, and what uses it starts when
  it answers: `depends_on` with `condition: service_healthy` and a `healthcheck` on the database.
- Nothing but the web port, and the ports `public_ports` declares, is published: a database, a
  cache or a worker is reachable only from inside the stack.
- No credential is written in the document: the database password and every key are generated
  variables (`generate: password32`, `hex64`, `base64_32`...), the same value in the application
  and in the database through one variable.
- Settings an operator may add later and that are not worth a variable (an SMTP server) are
  passed through: `NAME:` with no value in `environment` takes the value from the application's
  environment when the operator sets it, and sets nothing when not.

Any recipe, native or Compose, may ship configuration files in `files`, as `path: asset` (copied
as it is) or `path: {asset: ..., render: true, mode: "0640"}` (`mode` is `"0600"`, `"0640"` or `"0644"`). A rendered file has its `${NAME}`
references filled with the application's variables, secrets included, when it is written, so a
configuration can carry the domain and a generated key; every name it uses must be one the recipe
declares, which is checked when the recipe loads and not when someone installs it. A file is
written 0600 (0640 for a native recipe, whose account reads its group's files) unless the recipe
asks for 0640 or 0644. For a native recipe the files are seeded once into the application and
linked into every release like a persistent path, and an edit the operator makes is never
overwritten; nothing is written through a link.

A recipe may ship a `noust.nginx.yaml` (upload size and read timeout: `max_body_size`,
`routes: [{path: /, read_timeout: 600}]`), which the Compose deployer builds the site from.
Applications that take uploads or answer slowly need it: nginx's default is 1 MB and 60 seconds.

#### Several public addresses

Some applications are two programs that must answer on two names: Typebot's builder and the viewer
visitors open, Logto's sign-in service and its admin console. The recipe's `port` and its
`${PORT}` are the first, the application's own address. Each of the others is an **entrypoint**:

```yaml
variables:
  VIEWER_DOMAIN: {required: true, description: The viewer's address, a second name that points here.}
  VIEWER_PORT: {default: "3470", description: The viewer's port on 127.0.0.1.}
entrypoints:
  viewer:
    title: Viewer
    description: Where visitors open and answer the chatbots you publish.
    domain_variable: VIEWER_DOMAIN     # a required variable: the operator gives the name
    port_variable: VIEWER_PORT         # a loopback port the document publishes
compose: |
  services:
    viewer:
      ports:
        - "127.0.0.1:${VIEWER_PORT}:3000"
```

The address is a required variable on purpose: the console's wizard, `--var` and the API ask for it
like any other input, with no new field to learn. Before anything exists the install checks that it
is a domain, differs from the application's own and from the other entrypoints', and belongs to no
other application; it chooses a free port from the variable's default up (an operator's value
is kept) and writes it to the stack's `.env`. Once the application is deployed, each name is added
to it as an alias proxied to that port, through the one `noust domain add --port` implementation,
which orders the certificate that covers every name. The name has to point at the server first;
when it cannot be added (another site answers on it, the web server refuses it) the install is
still a deployment, and a note says why with the command that repeats it. A variable that carries
the address to the application (`NEXT_PUBLIC_VIEWER_URL`) is composed with
`value: "${SCHEME}://${VIEWER_DOMAIN}"` and a `SCHEME: {from: scheme}` variable. A name of a
Compose stack may go to a port the stack publishes and to no other. At most four entrypoints.

An update that brings an entrypoint the installed version lacked asks for its address like an
install does (nothing is touched until it is given), writes the address and the port to the
`.env`, and adds the name once the update has passed its health gate; one the application already
answers on is left as it is.

#### A recipe that is root on the server

Some applications are root on the host by design: Portainer and Coder manage the containers of the
server through the Docker daemon's socket, so whoever signs in can start a container with any part
of the server mounted, Noust's applications and their data included. A recipe that is one says so,
and is offered with a warning and a confirmation instead of being left out:

```yaml
root_equivalent: docker_socket     # the only kind: the stack mounts /var/run/docker.sock
```

It allows exactly one thing the closed schema refuses, the mount `/var/run/docker.sock:/var/run/docker.sock`
(`:ro` or not) and nothing else of the host: `privileged`, another path of the daemon and every other
refusal stand. The catalogue carries the warning in the server's coded words (`root_equivalent`,
`root_equivalent_warning`, `root_equivalent_text` and `root_equivalent_hint` of `GET /api/recipes`),
so a screen can say it in its own language before anyone presses install.

Installing one is refused until somebody confirms it, in the one place every way in meets
(`plan_recipe`): `noust recipe install portainer -d tools.example.com --accept-root-equivalent` on the
command line, `accept_root_equivalent: true` in `POST /api/recipes/{name}/install` and in
`POST /api/apps`. Through the API the confirmation also needs the server's permission
(`server:manage`) and sudo mode, as a package installed as root does. The confirmation records the
stack's exception for the compose guard in the name of whoever gave it, with the recipe in the
reason, exactly as `noust app sandbox compose-exception` does (and audited the same way), so the
deployment that follows is not refused and the exception is listed where every other one is. Without
the record a stack of another application that mounts the socket is still refused.

### `kind: native`

```yaml
requires: [postgres]              # postgres | mysql | mariadb (one of them), redis | valkey | mongodb, s3
app_type: nodejs                  # a registered deployer, never docker-compose or monorepo
source:                           # exactly one of:
  git: https://github.com/o/r.git #   a repository and a fixed ref (a tag, never a branch)
  ref: v1.2.3
  # archive: https://h/app.tar.gz #   an archive, with sha256 or checksum_url
  # sha256: ...
  # template: {package.json: package.json}   files copied from assets/<name>/
layout: releases                  # or inplace
build:                            # the same block, with the same names, as noust.yaml's build:
  base_directory: .
  install: npm ci
  build: npm run build
  start: node dist/server.js
  publish_directory: dist
  runtime: node@22
persistent_paths: [data]
php: {webroot: ".", deny: [wp-config.php], max_upload: 64m, shared_from_release: [wp-content]}
```

`requires` is provisioned through the existing database managers: a SQL database and its
account by the same function the 2.3 recipes used, Redis, Valkey and MongoDB by the database
service, which records them so the application's backups and its Database tab know them. `s3`
is provisioned by nobody, because Noust manages no object store: it is an endpoint the operator
names, and its variables (`from: s3.endpoint`, `s3.bucket`, `s3.region`, `s3.access_key`,
`s3.secret_key`) are required inputs.

**A choice of database engine.** MySQL and MariaDB speak one protocol, so a recipe that runs on either
says so and the operator chooses at install:

```yaml
requires: [mysql]
database_choices: [mysql, mariadb]   # the recipe's own first; at least two; mysql and mariadb only
```

```bash
noust recipe install wordpress -d blog.example.com --database mariadb
```

The install then runs on the chosen engine: the recipe's requirement, and the variables that read the
database (`from: mysql.url` becomes `from: mariadb.url`), name it, so the check that the server has the
engine, the provisioning and the notes all work from it. An engine the recipe does not offer, and a
recipe that offers no choice, are refused before anything is created. The API takes `database` in
`POST /api/recipes/{name}/install` (and `recipe_database` in `POST /api/apps`); the recipe's summary lists
`database_choices`; the console shows the choice on the recipe's review step. WordPress offers it. A
Compose recipe keeps its database in the stack, so it has no choice to offer.

`build` commands are split with `shlex` and run as an argv, never through a shell: a pipe, `&&` or
a redirect is an error. Every key is optional; what is left out is what the application's type does.
The block is carried in the plan (`RecipePlan.build`, and `build_block` in the deployer's
configuration) for the deployer that runs it.

## Variables

A variable is an environment variable of the installed application: the `.env` of a native one, the
stack's `.env` for a Compose one. Each says where its value comes from, with exactly one of:

| Key | Meaning |
|---|---|
| `generate: user` | 16 lower-case characters, starting with a letter |
| `generate: password32` / `password64` | 32 or 64 letters and digits |
| `generate: hex32` / `hex64` | 32 or 64 hexadecimal characters |
| `generate: base64_32` / `base64_64` | the base64 of 32 or 64 random bytes |
| `generate: jwt`, `signed_with: SECRET`, `role: anon` | an HS256 token with a `role`, signed with the variable `SECRET` (the pair Supabase-style stacks need) |
| `from: url` / `fqdn` / `scheme` / `https` / `port` / `app_name` / `app_path` | what the deployment gives: `https://domain`, `domain`, `https`, `true`, ... |
| `from: postgres.url` and `.host .port .name .user .password` | what a requirement provisioned (`postgres`, `mysql`, `mariadb`, `mongodb`; `redis` and `valkey` have `url host port password`) |
| `value: "${A}/path"` | composed from other variables |
| `default: "x"` | what the operator is asked for; `""` is a value |
| `required: true` | what the operator must give: install stops without it |

`url` and `fqdn` may name `service:` and `port:`; this release routes one web endpoint per
application, and they must name it.

References use the syntax Compose and the shell share: `${NAME}`, `${NAME:-default}`,
`${NAME:?message}`, and `$$` for a dollar sign. A bare `$NAME` is refused: whether it is a variable
is exactly the doubt the syntax removes.

Where the value comes from, in order: what the operator gave (`--var`, `variables` in the API),
what the application already has, then the recipe. So a generated secret is generated once and
persisted across redeploys and updates, and an edit made after install (the console's Environment
tab) survives them.

A variable is a secret when it is generated as a password, hex, base64 or jwt, when it comes from
`.password`, `.url` of a provisioned service, `s3.access_key` or `s3.secret_key`, when it is
composed from a secret, or when it says `secret: true`. Secrets are marked as such on the
application, so they are masked wherever its variables are shown, and the recipe API never returns
a value.

## Pinning policy

A recipe competes on trust, not on count. Coolify's catalogue has `latest` or no tag in 54% of its
templates and no digest anywhere; here:

1. **No floating tag, ever.** A tag names a release. If upstream publishes none (Excalidraw
   publishes only `latest` and per-build `sha-` tags), the recipe is listed as unavailable with
   that reason rather than shipped on a floating tag.
2. **Tag and digest.** `image: repo:1.2.3@sha256:...`: the tag is what a human reads, the digest is
   what Docker pulls. The digest is the one the registry serves for that tag (for a multi-
   architecture image, the index's), read with `scripts/recipe_pins.py resolve`, never typed from
   memory. An image mirrored on ghcr.io and Docker Hub usually has the same digest, though not
   always (Jellyfin's differ): the recipe pins the registry it names. Prefer ghcr.io when the
   project publishes there, which is not subject to Docker Hub's limit on anonymous pulls. The
   tool asks any registry where its anonymous tokens come from (Docker Hub, ghcr.io, quay.io,
   codeberg.org, docker.litellm.ai...), so a project that publishes on its own forge is pinned
   the same way. A database image is pinned like the application's: PostgreSQL 17 and Valkey 9
   by their exact tags, MySQL 8.4 and MariaDB 11.8 where the application needs them.
3. **`digest_verified: false`** is the honest form of a pin that could not be read: tag only, and
   `noust recipe show` warns. A test lists every recipe that uses it; the list only shrinks.
4. **Native sources**: a git tag (never a branch), or an archive with its `sha256` (or the
   published checksum URL, for a project that publishes one per release), or a template whose
   files name a pinned version.
5. **Package data.** The document, the files and the logo ship with Noust. Nothing in a recipe is
   fetched at install except what the pin names.

## Logos

A logo is an SVG under `recipes/assets/<name>/`, named by `logo:`. The console's Content Security
Policy forbids inline styles, scripts and every fetch from another origin, and a logo is served
from the same origin as an image, so the file is held to a closed list: only `svg`, `g`, `rect`,
`circle`, `ellipse`, `line`, `polyline`, `polygon`, `path`, `text` and `title`; no `style`
attribute or element, no `class`, no event handler, no `href`, no `url(...)`, no `data:`, no
`foreignObject`, no doctype or entity, a `viewBox`, at most 8 KiB. It is never fetched from
anywhere.

`logo:` is one of two things:

- `simple-icons:<slug>`, the mark the [simple-icons](https://github.com/simple-icons/simple-icons)
  collection holds for the project (CC0-1.0, files of lower-case letters and digits such as
  `nextdotjs`). The file is `recipes/assets/_simple-icons/<slug>.svg`, copied unchanged from the
  collection and shared by every recipe that names the slug; its `NOTICE` says which release of the
  collection and what the licence covers.
- `logo.svg`, a file of the recipe: the monogram drawn for Noust (two letters on a dark tile, under
  Noust's licence), for a project the collection has no mark for or whose guidelines forbid a third
  party's use of its logo.

The catalogue says which (`logo_source`, `logo_icon` and `logo_license` of `GET /api/recipes`), so
that a screen can credit a mark as it draws it. `scripts/recipe_logos.py report` lists who has a mark
and who has the monogram, `apply` writes the marks into the recipes and `check` fails on a slug the
collection does not have or a file that is not the collection's. A project's logo is its trademark,
and the collection's licence covers its files and not the brand: the policy for a project's mark, and
for everything else a recipe says about someone else's software, is in the next section.

## Names, marks, licences and logos of the projects

A recipe is Noust's description of how to run someone else's software, so what it may call that
software, show of it and ship of it is written down. This is what Noust does. It is the project's
practice, so that a rights holder, an operator and a contributor can all
read it, and not legal advice.

**Names.** A recipe names a project by its own name, to say what it deploys ("Gitea", "Rocket.Chat",
"Plausible Analytics Community Edition"). The name is never Noust's own: not in a domain, a command
or a package. Where the recipe is packaging the project did not make, the description says so
("not affiliated with or endorsed by"), because the guidelines projects publish (Plausible's, at
plausible.io/trademark, read on 2026-10-06) allow truthfully referring to a product and indicating
compatibility, and forbid suggesting endorsement and using their marks for a hosted, resold or
modified offer. A guideline that matters is named in the recipe's header comment with the day it
was read.

**Logos.** A recipe's logo is the project's mark from the simple-icons collection (CC0-1.0; its
`NOTICE`, beside the files, records the release and says the licence covers the files and not the
brand), or the monogram drawn for Noust: two letters on a dark tile, under Noust's licence. A mark
from anywhere else is added only when its licence or a written permission allows redistributing it,
with a `LOGO-NOTICE` file beside it that names the project, the source URL, the licence or
permission and the date. A project whose guidelines do not allow a third party's use of its logo
(Plausible's, `plausible.io/trademark`, read 2026-10-06) never gets its mark here, whatever the
collection holds: it is given the monogram. `tests/recipes/test_catalog_policy.py` fails on a
logo that is none of these, and on a mark in the package that no recipe names.

**Licences.** `license` is the SPDX expression of the project's code (`LicenseRef-...` where SPDX
has no identifier). Noust redistributes none of that code: a recipe is configuration and a pointer
to the image the project publishes, pinned by its digest. Three cases:

- An open source licence: the recipe is installable.
- A source-available licence that lets an organisation run the software for itself (n8n's and
  NocoDB's Sustainable Use License, Directus's Monospace Sustainable Core License, Open WebUI's
  branding clause, PLANKA's Community License, Tandoor's AGPL with the Commons Clause, Outline's
  Business Source License, Typebot's Functional Source License and Bugsink's PolyForm Shield
  License), or an open core whose
  repository holds some files under a proprietary licence (Stirling PDF, AFFiNE, Chatwoot,
  Infisical): the recipe is installable, its description says "source-available" or "open core",
  and a note names the restriction that matters (not offering it to others as a service, no
  competing use, keeping a name and logo).
- A licence that forbids what an operator of a server does (personal use only, no access for
  others), a community edition its own project declares not for production, or terms that cannot
  be told from the file: the recipe is listed as unavailable with the file or page named and the
  day it was read (Joplin Server's licence, Cal.diy's README, Vault's Business Source License).

A file copied from a project's repository (Plausible's ClickHouse settings) is shipped unchanged
with that repository's licence text in a `NOTICE` file in the recipe's assets.

**Withdrawal.** A recipe becomes an unavailable listing, with the reason and where it was read,
when the project asks for it, when its licence changes to one of the third kind, when its
repository is archived (MinIO) or it stops publishing images that can be pinned, or when it needs
what a recipe may not have (the Docker socket, the host's network). Nothing is deleted without a
reason a person can read.

## Which recipes have been started for real

A recipe that loads has been read by a closed schema and a lint; whether it starts is only known
when it runs. `tests/integration/recipes_run.py` starts the catalogue's Compose recipes one at a
time inside the harness container (its own Docker, never the machine's), with
`scripts/recipe_smoke.py`: the variables resolved as a deployment resolves them, the stack up as a
throwaway project, the health gate asked exactly as the gate asks it, the same path asked as nginx
asks it, every container inspected for a restart loop, an out-of-memory kill and a failed exit. It
writes what happened to `tests/integration/results/recipes.json` (recipe, version, result, seconds,
date, and for a failure the problem and the end of the log) before the next recipe starts, and
removes the images the recipe pulled by name. A recipe counts as tested when its latest result is
`pass`; a failure is fixed or the recipe is listed with the reason. The harness reads Docker Hub's
public images through a pull-through mirror (`mirror.gcr.io`, the same digests) because Hub counts
anonymous pulls per address.

Which recipes the catalogue carries as tested is `recipes/tested.json`, written from that file by
`scripts/recipe_tested.py` (a test fails while the two disagree): `GET /api/recipes` answers `tested`
with the version that was started, the day, the seconds it took and whether that is the version the
recipe pins now, and takes `?tested=true|false`; `noust recipe list --tested|--untested` does the same.

```bash
tests/integration/recipes_run.py run --next 10 --budget 35   # the next ten, none started after 35 minutes
tests/integration/recipes_run.py run NAME...                 # these, now
tests/integration/recipes_run.py status                      # the totals and every recipe that did not pass
tests/integration/recipes_run.py accept                      # drafts that passed, into the catalogue
```

It is the machine's one Docker place: it runs through `noust-docker`, one batch at a time, and a
batch gives the place back after its budget so that the other harnesses get their turn. Look at the
free space of the drive Docker lives on before a batch; the runner stops under 30 GB.

## Contributing a recipe

[Contributing a recipe](contributing-recipes.md) is the walk through: asking for one, writing it,
testing it on your own Docker and sending it. What follows is the rule it is judged by.

The rule of eligibility: the upstream project has at least 1,000 stars on GitHub (or on the forge
that hosts it: Codeberg for Forgejo), has made a release in the last year and is not archived, and
its licence allows running it for others (the licence goes in `license`; a source-available one is
said so in the description). A project that publishes a build per commit and no release (SearXNG)
is pinned to the newest dated build by tag and digest, and its recipe says so.

A recipe is accepted when:

1. **It loads.** `pytest tests/recipes` loads every recipe through the closed schema and the
   catalogue's lint (pinned images, loopback ports, logo, notes that render).
2. **Its pins were read.** `scripts/recipe_pins.py check NAME` passes, and the recipe's comments
   cite where its configuration comes from (the project's own compose file, image configuration,
   documentation page), not memory.
3. **It starts closed.** No default credential, no first-visitor-wins page without a note saying so
   at the top of `notes`, no open API without saying so in the description.
4. **Its data is declared.** Every volume is named and the notes say where the data is and that it
   needs a copy before an update.
5. **Its health check is real.** `health.path` is an endpoint of the application that answers 200
   without a login; `timeout` is raised for what is slow to start. The gate's request is the
   plainest there is: `http://127.0.0.1:<port><path>`, with `Host: 127.0.0.1:<port>`, no
   `X-Forwarded-*` header, and a redirect taken as the answer when the recipe states what it expects.
   An application that checks the host (Django's `ALLOWED_HOSTS`, Nextcloud's trusted domains,
   Baserow's public URL) must accept `127.0.0.1`, and one that redirects plain HTTP to its https
   address (Ghost) needs `expect: 200-399`.
   `scripts/recipe_smoke.py NAME` is how to know: it starts the stack on your Docker, makes
   exactly that request, makes it again the way nginx does, looks for a restart loop and an
   out-of-memory kill, and removes the project and its volumes.
6. **It does not need what a recipe may not have** (above). If it needs it, it is not a recipe; list
   it as unavailable with the reason.

Open the request in the repository's issues with the project, why it is wanted and the page with
its container instructions, and the change as a pull request with the recipe, its assets and its
test results.
