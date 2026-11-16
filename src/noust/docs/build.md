# Build, start and runtime

How Noust installs, builds and starts an application when the commands of its type are not
what the project needs: your own commands, a project in a subdirectory of the repository, the
directory a static build leaves, and the Node, Python or PHP version it runs on. And the Node
frameworks and Python servers Noust now knows by name.

Every type has commands of its own (`npm ci`, `npm run build`, `gunicorn app:app`), and that is
what makes a redeploy and a rollback behave like the first deploy. Nothing here changes an
application that says nothing: the commands of its type apply exactly as before. What you say
replaces what the type would have done, step by step, and what you leave out keeps the type's.

- [The build block](#the-build-block)
- [Where it comes from](#where-it-comes-from-and-who-wins)
- [Base directory](#base-directory)
- [Your own commands](#your-own-commands)
- [Static output](#static-output)
- [Runtimes](build-runtimes.md#runtimes)
- [Node frameworks](build-runtimes.md#node-frameworks)
- [Python](build-runtimes.md#python)
- [Go, Rust and Ruby](build-runtimes.md#go-rust-and-ruby)
- [Java and Kotlin, .NET, Elixir and Deno](build-runtimes.md#java-and-kotlin-net-elixir-and-deno)
- [Process types](build-processes.md#process-types)
- [Build cache](#build-cache)
- [How long a build may take](#how-long-a-build-may-take)
- [System packages](build-runtimes.md#system-packages)
- [Updating, and what an update does to the unit](#updating-and-what-an-update-does-to-the-unit)
- [Commands and API](#commands-and-api)
- [Limits](build-processes.md#limits)
- [What changes when you upgrade](#what-changes-when-you-upgrade)

The rest of this guide is on pages of its own:

- [Runtimes and toolchains](build-runtimes.md)
- [Process types and limits](build-processes.md)

## The build block

A `noust.yaml` (or `.noust.yaml`) at the root of the repository may carry a `build:` block. Every
key is optional:

```yaml
build:
  base_directory: apps/web              # the project lives in this subdirectory
  install: pnpm install --frozen-lockfile
  build: pnpm --filter web build
  start: node dist/server.js --port ${PORT}
  publish_directory: dist               # static output the web server serves (no start)
  runtime: node@22                      # node@22, python@3.12, php@8.3
  timeout: 3600                         # seconds each of install and build may take
```

The file is untrusted input, like the rest of the repository, and its schema is closed:

- An unknown key is refused, never ignored: a misspelt `strat` would otherwise be a start
  command that silently never applies. Every refusal names the field (`build.base_directory`).
- A path is relative to the repository. `..`, an absolute path, `~`, a backslash and names with
  spaces or quotes are refused, and a link anywhere on the way to the base directory is refused
  when the code is fetched: nothing is built, run or served through a link a repository committed.
- A command is an argument vector: Noust splits it the way a shell would split it
  (`make 'all targets'` is two words) and **never gives it to a shell**. `&&`, `|`, `>` and `$(...)`
  are ordinary arguments. For a pipe or a redirection, commit a script and run it
  (`build: ./scripts/build.sh`). A list of arguments works too:
  `start: [gunicorn, -b, "127.0.0.1:${PORT}", "app:app"]`.
- `start` and `publish_directory` are exclusive: an application is files the web server serves
  or a process it proxies to.
- `runtime` must be `node@`, `python@` or `php@` followed by a major (`22`), a minor (`22.11`), a
  full version (`22.11.0`), `lts` or `latest`; a range is for a repository's own version files
  (see [Runtimes](build-runtimes.md#runtimes)).

A type only accepts the keys it can honour. `publish_directory` on a Node server, `start` on a
static site and `base_directory` on a PHP application are refused, with what to do instead.

| Type | `base_directory` | `install` | `build` | `start` | `publish_directory` | `runtime` |
|---|---|---|---|---|---|---|
| `nodejs`, `nextjs` | yes | yes | yes | yes | no | node |
| `nuxt`, `sveltekit`, `astro`, `remix`, `angular` | yes | yes | yes | yes | yes (when static) | node |
| `vite` | yes | yes | yes | yes (SSR) | yes | node |
| `static` | yes | yes | yes | no | yes | node, python |
| `python` | yes | yes | yes | yes | no | python |
| `php-fpm` | no (use `webroot`) | yes | yes | no | no | php |
| `go`, `rust`, `ruby`, `java`, `dotnet`, `elixir`, `deno` | yes | yes | yes | yes | no | none: the server's toolchain |
| `monorepo` | no: it is built by its workspaces' own tools | | | | | |
| `docker-compose`, `container` | no: the image is built by the stack's or the Dockerfile's own configuration. Only `timeout` and `cache` apply ([how long a build may take](#how-long-a-build-may-take)) | | | | | |

## Where it comes from, and who wins

There are two places, and the operator's wins **whole**, the way the operator's
[deploy hooks](compose-hooks.md#operator-hooks) win:

1. The operator's block, stored on the server for one application (below). When it exists, the
   repository's `build:` is not read at all: nothing is merged, so what only the repository said
   (a base directory, say) no longer applies.
2. The repository's `noust.yaml`.
3. Neither: the commands of the application's type.

```bash
noust app build show shop.example.com            # which block applies, and where it comes from
noust app build set shop.example.com --start 'node dist/server.js' --runtime node@22
noust app build set shop.example.com --file build.yaml    # the whole block from a file ('-': stdin)
noust app build unset shop.example.com runtime            # remove one key
noust app build unset shop.example.com                    # remove the block: the repository's applies again
```

`set` with flags adds to what the operator already set; `--file` replaces it. The same is
`GET`, `PUT` and `DELETE /api/apps/{domain}/build` (the body names the keys; `PUT` needs sudo
mode and the `root_equivalent` permission, because these commands run in the build sandbox and,
for `start`, as the application with its secrets). Every change is audited as `apps.build`.

Before the first deploy there is no application to hold a block, so a new application is given
its own at creation, with `--build-file`: a YAML file holding `build:`. It replaces the
repository's, decides the type detection too (`--type auto` looks in its base directory), and is
stored as the operator's block for every later update. That is how several applications come
from one repository:

```bash
noust create -d web.example.com -s https://github.com/you/shop.git --build-file web.yaml
noust create -d api.example.com -s https://github.com/you/shop.git --build-file api.yaml
```

```yaml
# web.yaml                         # api.yaml
build:                             build:
  base_directory: apps/web           base_directory: apps/api
  build: pnpm --filter web build     start: ./bin/api --port ${PORT}
```

`--build-file` is refused for a monorepo or Docker Compose project, and with `--recipe`.

In the console, the application's **Code and build** page holds this block: the six keys for what the type can
honour, where the commands come from (this application's own settings, the repository's
`noust.yaml` or the type), the rule that the operator's block wins whole, and what the service was
last written from. Saving asks for sudo mode. Runtimes cannot be listed from the console (no
endpoint says what is installed): it suggests the common ones and names `noust runtime list`.

## Base directory

For a monorepo (or any repository whose project is not at its root), `base_directory` names the
subdirectory the project lives in. Noust then:

- **Detects the type** there (`--type auto` looks at `apps/web`, not at the workspace root).
- **Builds there.** Install and build run with `apps/web` as their working directory, in the
  sandbox, with the **whole release** writable (a workspace installs at its root). The release
  is handed to the service account whole.
- **Runs there.** The unit's `WorkingDirectory` and the web server's root are
  `current/apps/web`; `current` still points at the release, so an activation is the same swap.
- **Finds the package manager above it.** pnpm, Yarn, Bun and npm lockfiles live at the root of
  a workspace, not in the project: the nearest lockfile from the project up to the root of the
  repository decides, then the `packageManager` field of `package.json` (Corepack's), then npm.
- **Reuses dependencies** from the same subdirectory of the active release when its lockfile is
  unchanged. A workspace whose lockfile is above the project installs every time: slower, never
  wrong.

`noust.yaml`, hooks and `noust app build` always refer to the repository's root; a hook's
`workdir`, `.env.example` and `noust.nginx.yaml` are read from the base directory.

## Your own commands

`install` replaces the type's install, `build` its build and `start` its start. Where each runs:

| Command | Runs | As | With |
|---|---|---|---|
| `install` | in the project, at deploy and every update | `noust-build`, in the build sandbox | the install network and the application's variables as the sandbox allows |
| `build` | in the project, after install | `noust-build`, in the build sandbox | the project's own tools on `PATH` (`node_modules/.bin`, `venv/bin`) |
| `start` | the unit's `ExecStart=` | the application's account | the unit's environment, then its `.env` |

A failing `install` is **not retried** as something else: what the type does when `pnpm install
--frozen-lockfile` fails (it retries without the flag) is for the type's own command only.

`start` is the text of an `ExecStart=`, so systemd expands `${VARIABLE}` in it, from the unit's
environment: `${PORT}` is the port Noust proxies to (for blue/green, each instance's own),
`${WEB_CONCURRENCY}` the workers of a Python application. A literal `%` stays a literal `%`
(Noust doubles it), and a literal `$` is written `$$`. systemd needs an absolute program, so the
first word is made one:

1. An absolute path stays as it is.
2. A path with a slash (`./bin/server`, `dist/server`) is inside the project, as it is seen
   through `current`.
3. A bare name is looked for in the project's own tools (`node_modules/.bin` of the project and
   of its workspace, `venv/bin`), then in the runtime the application is [pinned](build-runtimes.md#runtimes) to,
   then in the system's directories. Never nvm's: a service cannot read a home directory.

A script of `venv/bin` is started through the environment's own interpreter
(`current/venv/bin/python current/venv/bin/gunicorn ...`), because its first line names the
release it was installed in, which pruning eventually deletes.

Examples:

```yaml
build:                                  # a Go service built by make, with a binary in the repository
  build: make release
  start: ./bin/server --listen 127.0.0.1:${PORT}

build:                                  # Bun
  install: bun install --frozen-lockfile
  build: bun run build
  start: bun run start

build:                                  # Python with the server of your choice
  start: uvicorn app.main:app --host 127.0.0.1 --port ${PORT} --workers 2
```

A toolchain Noust does not pin (Go, Ruby, Rust, Bun) is used as the server has it: it has to be
installed, and the command found in the system's directories or named by its path.

## Static output

`publish_directory` names where a static build leaves the files the web server serves, relative
to the project. It makes any build a static site: a `static` application with `install` and
`build` is built first (Eleventy, Hugo, Docusaurus: whatever the server has the tools for) and
served from that directory; a Vite or framework application serves it instead of running a server.

```yaml
build:
  install: npm ci
  build: npm run build
  publish_directory: _site
```

The directory must exist after the build and must not be a link. A Node framework's
deployment fails right after the build naming where to set it; for a Vite or static site the
health gate refuses a release that has nothing to serve, and the previous one stays. It is
served through `current` like any release, so activating a release needs no reload.

## Build cache

The package managers' downloads live in `/var/cache/noust/build/<app>`, outside every release:
npm, pnpm, yarn, pip, uv and Composer's caches, Go's build and module caches, Cargo's registry and
compiled dependencies. A deploy fetches and compiles only what changed. `noust update --no-cache`
(and the console's "Rebuild without cache") empties it first, and `build: {cache: false}` does so on
every build. `noust app cache show|clear` reports and empties it without deploying.

## How long a build may take

The install of the dependencies and the build each get a time limit: 1800 and 2700 seconds
unless something says otherwise. `build: {timeout: 3600}` (or `noust app build set DOMAIN
--timeout 3600`, or the console's build commands) gives that application 3600 seconds for each of the
two; `noust config set deploy.install_timeout N` and `deploy.build_timeout N` change the server's
default for every application that does not say (60 to 86400, read again every few seconds). Every
type that Noust builds takes `timeout`, including PHP and static sites.

**Images the Docker daemon builds take it too.** A Docker Compose stack and a container application
(a Dockerfile or Railpack) are not built by the build block, but the block may carry the two keys
that apply to an image: `timeout` (how long `docker compose build` or `docker build` may take, and
how long bringing the containers up may wait on one; the server's `deploy.build_timeout`, 2700
seconds, when the application does not say) and `cache: false` (the same as `noust update
--no-cache`, on every build). `noust app build set stack.example.com --timeout 7200`, or `build:
{timeout: 7200}` in the repository's `noust.yaml`. Any other key is refused with the reason, not
ignored. A monorepo is built by its own tools and takes no build block.

A step that is stopped says whether it was the time (`did not finish within 2700s`) or the memory
(`killed due to insufficient memory`), and for the time it names the two commands that give it
more. More time does not help a build that is stuck rather than slow (a private registry that does
not answer): read the output above the message.

## Updating, and what an update does to the unit

An update does not write the unit again, so a `start` command, a base directory or a runtime
that changed (in the repository, or by `noust app build set`) would never have reached the
process. Noust remembers what each unit was written from, and when an update would write
something different it edits the unit's `ExecStart`, `WorkingDirectory` and `PATH` and nothing
else of it (a unit you edited by hand keeps its edits), before the release is activated.

- When the release does not pass its health gate, the previous release **and the previous unit**
  are put back and restarted, and the deployment fails with the probes and the journal.
- Removing the block puts the type's own command back, in the same way.
- An application the build block does not touch (no `start`, no `base_directory`, no pinned
  runtime) never has its unit rewritten by an update.

`noust app build show` prints what the unit was last written from (`Unit starts`).

## Commands and API

| | |
|---|---|
| `noust app build show DOMAIN` | The block the next deployment uses, where it comes from, what the unit runs (`--json`) |
| `noust app build set DOMAIN` | `--base-directory`, `--install`, `--build`, `--start`, `--publish-directory`, `--runtime`, or `--file` |
| `noust app build unset DOMAIN [KEY]...` | Remove keys of the operator's block, or all of it |
| `noust runtime list`, `install`, `remove` | The runtimes |
| `GET`, `PUT`, `DELETE /api/apps/{domain}/build` | The same, over the API |
| `noust app process-types show DOMAIN` | The process types declared, and which a deployment uses (`GET /api/apps/{domain}/process-types`) |

## What changes when you upgrade

Nothing for an application whose repository has no `build:` block, no version file Noust
reads and no pinned runtime, with these exceptions, each a fix:

- A **new** Python application binds `127.0.0.1` and takes its workers from
  `WEB_CONCURRENCY`; an existing one keeps its unit until an update rewrites it for another reason.
- Django's `collectstatic` no longer runs for a project without a `STATIC_ROOT`, where it failed.
- Poetry installs with `--without dev`, the form Poetry 2 accepts (`--no-dev` was removed).
- A Django project's WSGI module is found from the project, not from the virtual environment.
- Next.js, Vite and Node projects are unaffected by the new frameworks unless they depend on
  Nuxt, SvelteKit, Astro, Remix, React Router or have an `angular.json`: those were refused
  (Nuxt, Angular) or deployed as Vite (SvelteKit, Astro, Remix) before.
- An application whose repository has a `.nvmrc`, `.node-version`, `engines.node`,
  `.python-version`, `runtime.txt` or a `require.php` that the server's runtime does not satisfy
  gets a managed runtime at its next update, and its unit is rewritten to use it.
