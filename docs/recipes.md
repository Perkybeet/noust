# Recipes

A recipe is a ready-made application: a file that says what to deploy, from where, with which
variables and database, how to tell it is healthy, and what the operator does next. The catalogue
ships with Noust (`noust recipe list`), and every recipe is plain data, validated when it loads,
never code.

This guide is for the operator who installs recipes and for the person who writes one. It covers:

- [Using the catalogue](#using-the-catalogue)
- [Updating what you installed](#updating-what-you-installed)
- [What a recipe needs from the server](#what-a-recipe-needs-from-the-server)
- [Backups from the first day](#backups-from-the-first-day)
- [Your own recipes](recipes-own.md#your-own-recipes)
- [Signed catalogue packages](recipes-own.md#signed-catalogue-packages)
- [A Compose document you paste](#a-compose-document-you-paste)
- [The format](recipes-format.md#the-format) (schema version 2)
- [Variables](recipes-format.md#variables)
- [Pinning policy](recipes-format.md#pinning-policy)
- [Updating the catalogue](recipes-own.md#updating-the-catalogue)
- [Logos](recipes-format.md#logos)
- [Names, marks, licences and logos of the projects](recipes-format.md#names-marks-licences-and-logos-of-the-projects)
- [Contributing a recipe](recipes-format.md#contributing-a-recipe)
- [What ships, and what does not](recipes-catalog.md#what-ships-and-what-does-not)

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
| `GET /api/recipes?q=&category=&tag=&kind=&available=&unavailable_code=&tested=` | The catalogue, with search and filters |
| `GET /api/recipes/categories` | The categories that hold a recipe, with how many |
| `GET /api/recipes/{name}` | One recipe in full: variables (never their values), images, ports it opens, health check, notes |
| `GET /api/recipes/{name}/logo?scheme=light\|dark` | Its logo, drawn for the theme the console shows: an SVG that needs nothing the console's CSP forbids, or a PNG where the project has no SVG |
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
`noust recipe update` as a job. See [console.md](console-applications.md#recipes).

### What a Docker recipe never does

Its Compose document is read through a closed schema, the way a repository's file would be, and a
recipe that needs any of these is not a recipe:

- an image without a tag, with a tag that moves (`latest`, `stable`, `edge`, `main`...), or
  without the `sha256` digest the registry served for it;
- `privileged`, the Docker socket (one recipe may say it is root on the server, below), the host's
  namespaces, devices, added capabilities, disabled
  confinement, `build:`, `env_file:`, `extends:`, `container_name:` and every other key outside the
  closed list (`composefile.py`, `_SERVICE_KEYS`);
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

## What a recipe needs from the server

A Coolify template copied to a server that lacks what it runs on fails in the middle of the
deployment. Noust asks first:

```bash
noust recipe requirements umami                    # what it needs, and whether this server has it
noust recipe install umami -d stats.example.com --install-requirements
```

`noust recipe requirements NAME` (and `GET /api/recipes/{name}/requirements`) lists the tools the
recipe's deployer runs, the language runtime it pins in `build.runtime` (Node, Python or PHP, asked of
the runtime manager) and the database engines it `requires`, each *met* or *missing*, with the command
that provides what is missing. It changes nothing and exits 1 when something is missing. An install
runs the same check before a database or a directory exists, and a server that cannot run the recipe
is refused with every missing piece named at once.

`--install-requirements` (`install_requirements` in the API, which needs the server's permission and
sudo mode, as installing a package as root does) installs what Noust knows how to install, a runtime
or a database engine, and still stops for what it does not: Docker, or an engine that is installed
but stopped. There is no Java deployer in Noust, so there is no Java runtime to check. The console
shows the same list on the recipe's page and in the new-application wizard before anything is made.

## Backups from the first day

An application installed from a recipe is scheduled the day it exists: every database Noust
provisioned for it is dumped daily (7 or 30 days kept) and each dump is test-restored, and its data is
archived daily, with the SQLite files inside snapshotted whole. They are the ordinary policies, kept
on this server until you pick a destination (`noust backup destination`). `--no-backups`
(`default_backups: false` in the API; a checkbox in the wizard) leaves them out. A policy that cannot
be made is a line in the install's notes, not a failed install.

An application with a database also *requires* the backup before an update: one that fails stops the
update before anything is pulled or built, because a migration that ran after a backup that did not
happen cannot be undone. The database dumps are in that backup, and on the release layout the dump is
taken before the release is built.

```bash
noust app backup-required shop.example.com            # show the setting
noust app backup-required shop.example.com off        # let an update go on without it
noust app backup-required shop.example.com default    # back to the rule: on with a database
```

Without a database a failed backup stays a warning, and the way back from a bad update is never held
to a backup of the bad state. `GET`/`PUT /api/apps/{domain}/backup-required` (sudo mode to change) and
the Deploys tab of an application's settings do the same.

## A Compose document you paste

The long tail of applications is a Compose file, not a recipe each. The new-application wizard takes
one as a source, and `noust compose check` reads it first, running nothing:

```bash
noust compose check docker-compose.yml        # or - for standard input; --json for the analysis
```

It lists the services, the images, the published ports and the variables the document reads (which
have a default, which need a value, the message of a `${NAME:?message}`), and what refuses it or is
only worth knowing. A document is refused what a repository's Compose file is already refused (a
privileged container, the Docker socket) and also a `build:` context or an `env_file:` it does not
have; it is warned about an image that moves, one pinned by tag only and a port on every address.
`POST /api/compose/analyze` is the same analysis; `POST /api/apps` takes `compose_document` for the
document itself, analyses it again (the preview is a convenience, the guard is the deployment's),
writes it beside the recipes' sources and deploys it with the Compose deployer behind nginx, with the
variables it reads given in `env_vars`. A refused document queues nothing and writes nothing.

A service from Coolify's catalogue is a Compose file too: `noust compose import FILE --domain D
--out DIR` converts it (the variables keep their names and get values, the routing becomes a port on
127.0.0.1, what only Coolify's proxy reads is dropped), as [coolify.md](coolify.md#a-service-template-from-coolifys-catalogue)
describes.
