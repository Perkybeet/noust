# Recipes

A recipe is a ready-made application: a file that says what to deploy, from where, with which
variables and database, how to tell it is healthy, and what the operator does next. The catalogue
ships with Noust (`noust recipe list`), and every recipe is plain data, validated when it loads,
never code.

This page is for the operator who installs recipes and for the person who writes one. It covers:

- [Using the catalogue](#using-the-catalogue)
- [Updating what you installed](#updating-what-you-installed)
- [The format](#the-format) (schema version 2)
- [Variables](#variables)
- [Pinning policy](#pinning-policy)
- [Updating the catalogue](#updating-the-catalogue)
- [Logos](#logos)
- [Contributing a recipe](#contributing-a-recipe)
- [What ships, and what does not](#what-ships-and-what-does-not)

## Using the catalogue

```bash
noust recipe list                              # the whole catalogue, deployable recipes first
noust recipe list -q "password manager"        # words in the name, title, slogan, description, tags
noust recipe list --category media --kind compose
noust recipe show vaultwarden                  # what it runs, what it opens, what to give it
noust recipe install vaultwarden -d vault.example.com
noust recipe install freshrss -d rss.example.com --var ADMIN_EMAIL=me@example.com
```

`noust recipe install` is `noust create --recipe`: the same deployment, the same health gate, the
same job in the console. A variable the recipe requires (`required: true`, or `${VAR:?message}` in
its document) stops the install before anything exists: no database, no directory, no generated
secret. The error names every missing variable at once.

The same catalogue is the API's:

| Route | What it does |
|---|---|
| `GET /api/recipes?q=&category=&tag=&kind=` | The catalogue, with search and filters |
| `GET /api/recipes/categories` | The categories that hold a recipe, with how many |
| `GET /api/recipes/{name}` | One recipe in full: variables (never their values), images, ports it opens, health check, notes |
| `GET /api/recipes/{name}/logo` | Its logo, an SVG that needs nothing the console's CSP forbids |
| `POST /api/recipes/{name}/install` | Queue an install: `{domain, variables, port, ssl, ...}` |
| `GET /api/recipes/installed` | What was installed from a recipe, at which version, and whether it is behind |
| `POST /api/recipes/installed/{domain}/update` | Queue the update to the catalogue's version |

Recipes written by Noust for Docker are marked **runs in Docker** (`kind: compose`): a Compose
stack of pinned images behind nginx, deployed by the Compose deployer. A native recipe is a
pinned source (a git tag, or an archive and its checksum) built by one of Noust's deployers and run
by a systemd unit.

### In the console

The console's **Catalog** page (`/catalog`, `g` `c`) is this catalogue: a card per recipe with its
logo, slogan, category, tags, version and the date it changed, search and the category, tag and kind
filters (the API's own, kept in the address), and paging at 20, 50, 100 or all. **Details** opens
a recipe's page; **Use** opens the new-application wizard on that recipe, with the variables it
cannot run without in front. **Installed** is `noust recipe outdated` with an **Update** that queues
`noust recipe update` as a job. See [console.md](console.md#catalog).

### What a Docker recipe never does

Its Compose document is read through a closed schema, the way a repository's file would be, and a
recipe that needs any of these is not a recipe:

- an image without a tag, with a tag that moves (`latest`, `stable`, `edge`, `main`...), or
  without the `sha256` digest the registry served for it;
- `privileged`, the Docker socket, the host's namespaces, devices, added capabilities, disabled
  confinement, `build:`, `env_file:`, `extends:`;
- a published port other than `127.0.0.1:${PORT}:<port>` (nginx is the only way in), a loopback
  port the operator picks through a variable, and the ports its `public_ports` declares and the
  console shows before installing (Syncthing's sync port is one);
- a host path written in the document. A volume is a Docker volume, a file the recipe ships, or
  `${VARIABLE}:/path` where the variable defaults to a directory inside the application
  (`./media`): the operator may point it elsewhere, the recipe may not.

## Updating what you installed

Coolify copies a template when a service is created and never looks at it again. Noust remembers
which recipe and which version each application came from, and compares it with the catalogue.

```bash
noust recipe outdated                 # what the catalogue has a newer version of, and what changed
noust recipe outdated --all           # the ones that are current too
noust recipe update vault.example.com
```

`noust recipe update` is not a second way to update. It renders the new version's source and hands
it to `noust update`'s one implementation (`lifecycle.update_app`): the pre-update backup (the
databases of a Compose stack included, and the update stops if that copy fails), the build, the
health gate and the instant return to what served. What it adds:

- **Variables.** The ones the application has are never regenerated and never reset: an edit you
  made stays. The ones the new version declares and the application lacks are generated (or, when
  `required`, asked for with `--var` before anything is touched).
- **Services.** A version that needs a service the installed one did not use (it takes a variable
  from `postgres.url` the application never had) is refused before anything is touched: services
  are provisioned when a recipe is installed.
- **Digests.** The Compose deployer goes back to a previous image by its name, and an image pinned
  by digest has no name to go back to. When a Docker recipe's new version fails, the files the
  stack ran with are written back, in the application and in the recipe's own source directory;
  and when the containers had been recreated, the unit is restarted from them behind the same
  gate, and the error says whether the previous version answers again. An update refused before
  the deployer (another operation running, a database copy that failed) touches nothing of the
  application.

A recipe has a `version` (the application's) and a `revision` (the recipe's own, for a fix to the
recipe with the same application version). The catalogue is ahead when the version is newer, or
the version is the same and the revision is higher. The version is compared by its numbers
(`1.10.0` is newer than `1.9.9`, `1.2.0` is newer than `1.2.0-rc1`); a version that is not numbers
(a commit) counts as newer when it differs.

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
logo: logo.svg                    # recipes/assets/<name>/logo.svg
version: "1.37.4"                 # the upstream version this recipe pins; a quoted string
revision: 1                       # 1 by default; raised when only the recipe changed
updated: 2026-10-06               # when the recipe last changed
homepage: https://github.com/dani-garcia/vaultwarden
docs: https://github.com/dani-garcia/vaultwarden/wiki
repository: https://github.com/dani-garcia/vaultwarden
license: AGPL-3.0-only            # an SPDX expression
min_noust: "3.4.0"                # optional: the oldest Noust that can install it (enforced)
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
`smart-home`, `utilities`, `other`. The list is closed on purpose: Coolify's free text ended up
with 38 spellings, "Networking" and "networking" among them.

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
unavailable_reason: Why, in a sentence that says what it waits for.
```

It carries only the catalogue fields above, and `noust recipe show` and the console print the
reason. Ghost, Plausible, PocketBase, Excalidraw and Vault are listed this way.

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

A recipe may ship a `noust.nginx.yaml` (upload size and read timeout: `max_body_size`,
`routes: [{path: /, read_timeout: 600}]`), which the Compose deployer builds the site from.
Applications that take uploads or answer slowly need it: nginx's default is 1 MB and 60 seconds.

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
   project publishes there, which is not subject to Docker Hub's limit on anonymous pulls.
3. **`digest_verified: false`** is the honest form of a pin that could not be read: tag only, and
   `noust recipe show` warns. A test lists every recipe that uses it; the list only shrinks.
4. **Native sources**: a git tag (never a branch), or an archive with its `sha256` (or the
   published checksum URL, for a project that publishes one per release), or a template whose
   files name a pinned version.
5. **Package data.** The document, the files and the logo ship with Noust. Nothing in a recipe is
   fetched at install except what the pin names.

## Updating the catalogue

When upstream releases a version:

```bash
# 1. the new tag's digest, read from the registry
python scripts/recipe_pins.py resolve ghcr.io/dani-garcia/vaultwarden:1.38.0

# 2. in the recipe: the image reference, `version`, `updated`, and a `changelog` entry;
#    keep `revision` at 1 for a new version, raise it for a fix to the recipe itself
# 3. check every pin of the shipped recipes against where it is published
python scripts/recipe_pins.py check --evidence /tmp/pins
```

`check` resolves each image's tag in its registry and compares the digest, reads the
architectures it is published for, tests each archive's SHA-256 (or the checksum the project
publishes beside it), and looks for each git tag, failing on any difference; `--evidence` writes what was read,
per recipe, as JSON. It makes only anonymous, read-only requests.

Then read the release notes for what the new version asks of an update (a database migration, a new
variable, a new port) and put it in the recipe: a new variable is declared, and `changelog` says
what changed. The tests load every recipe, so a typo cannot ship.

## Logos

A logo is an SVG under `recipes/assets/<name>/`, named by `logo:`. The console's Content Security
Policy forbids inline styles, scripts and every fetch from another origin, and a logo is served
from the same origin as an image, so the file is held to a closed list: only `svg`, `g`, `rect`,
`circle`, `ellipse`, `line`, `polyline`, `polygon`, `path`, `text` and `title`; no `style`
attribute or element, no `class`, no event handler, no `href`, no `url(...)`, no `data:`, no
`foreignObject`, no doctype or entity, a `viewBox`, at most 8 KiB. It is never fetched from
anywhere.

The logos this release ships are plain monograms drawn for Noust (two letters on a dark tile), not
the projects' own marks: a project's logo is its trademark, and shipping it needs a licence that
allows redistribution and a record of it. A contributor may add a project's real logo when its
licence allows it, with the licence and the source in a `LOGO-NOTICE` file beside it; otherwise
the monogram stands. Coolify ships 349 third-party SVGs with no attribution; this is the
opposite.

## Contributing a recipe

The rule of eligibility: the upstream project has at least 1,000 stars on GitHub, has made a
release in the last year and is not archived, and its licence allows running it for others
(the licence goes in `license`; a source-available one is said so in the description).

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
   without a login; `timeout` is raised for what is slow to start.
6. **It does not need what a recipe may not have** (above). If it needs it, it is not a recipe; list
   it as unavailable with the reason.

Open the request in the repository's issues with the project, why it is wanted and the page with
its container instructions, and the change as a pull request with the recipe, its assets and its
test results.

## What ships, and what does not

The 3.4 catalogue has 20 recipes you can install and 5 that are listed with the reason.

| Recipe | Version | Kind | Runs |
|---|---|---|---|
| WordPress | 7.1.2 | native | PHP-FPM, MySQL/MariaDB |
| Uptime Kuma | 1.23.17 | native | Node.js |
| Umami | 2.20.2 | native | Node.js, PostgreSQL |
| n8n | 1.123.82 | native | Node.js |
| Vaultwarden | 1.37.4 | compose | Docker |
| Memos | 0.31.0 | compose | Docker |
| Syncthing | 2.1.6 | compose | Docker; opens 22000 tcp and udp |
| Stirling PDF | 3.1.0 | compose | Docker |
| Grafana | 13.2.3 | compose | Docker |
| Meilisearch | 1.54.3 | compose | Docker |
| code-server | 4.140.0 | compose | Docker |
| Jellyfin | 12.2 | compose | Docker |
| Mailpit | 1.31.4 | compose | Docker; SMTP on 127.0.0.1 |
| Open WebUI | 0.11.4 | compose | Docker |
| Ollama | 0.35.1 | compose | Docker; no authentication of its own |
| ntfy | 2.28.0 | compose | Docker |
| Navidrome | 0.64.2 | compose | Docker |
| linkding | 1.47.0 | compose | Docker |
| FreshRSS | 1.30.1 | compose | Docker |
| Homepage | 2.4.0 | compose | Docker |

Listed, not deployable: Ghost (needs a MySQL engine distinct from MariaDB), Plausible (needs
ClickHouse), PocketBase (a binary with no image, and no deployer in this release that runs one),
Excalidraw (no release tag to pin) and Vault (a server that starts sealed, and a licence to review).

The four migrated recipes keep the versions they were tested at; newer majors exist (Uptime Kuma 2,
Umami 3, n8n 2) and become a recipe revision of their own once they have been deployed here.
