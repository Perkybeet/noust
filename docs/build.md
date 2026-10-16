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
- [Runtimes](#runtimes)
- [Node frameworks](#node-frameworks)
- [Python](#python)
- [Go, Rust and Ruby](#go-rust-and-ruby)
- [Java and Kotlin, .NET, Elixir and Deno](#java-and-kotlin-net-elixir-and-deno)
- [Process types](#process-types)
- [Build cache](#build-cache)
- [Updating, and what an update does to the unit](#updating-and-what-an-update-does-to-the-unit)
- [Commands and API](#commands-and-api)
- [Limits](#limits)
- [What changes when you upgrade](#what-changes-when-you-upgrade)

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
  (see [Runtimes](#runtimes)).

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
| `monorepo`, `docker-compose` | no: they are built by their own configuration | | | | | |

## Where it comes from, and who wins

There are two places, and the operator's wins **whole**, the way the operator's
[deploy hooks](compose.md#operator-hooks) win:

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

In the console, **Settings > Build commands** is this block: the six keys for what the type can
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
   of its workspace, `venv/bin`), then in the runtime the application is [pinned](#runtimes) to,
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

## Runtimes

A project can say which Node, Python or PHP it needs, and a server can host projects that need
different ones. Two ways, and the first wins:

- **An order**: `build.runtime`, or `noust app build set --runtime node@22`. The application
  runs on exactly that, installed by Noust if it is not yet. If it cannot be installed, the
  deployment fails before building anything.
- **A hint**: the version files the project already has. The server's own runtime is used when
  it satisfies the hint; a managed one is installed when it does not. A hint **never breaks a
  deployment that worked**: if the managed runtime cannot be installed (no network, no `uv`),
  the deployment goes on with the server's own and says so in its log.

| Language | Files read, in this order | Satisfied by |
|---|---|---|
| Node | `.nvmrc`, `.node-version`, `engines.node` of `package.json` | The server's `node --version` |
| Python | `.python-version`, `runtime.txt` | The server's `python3 --version` |
| PHP | `require.php` of `composer.json` | The newest installed PHP-FPM that satisfies it, the one already running the application first |

The project's own directory is read first, then the root of the repository (a monorepo keeps one
`.nvmrc` at the top). Ranges are the dialect of the file: npm's (`>=18 <21`, `^20.11`, `20.x`,
`18 || 20`), Composer's (`^8.1`, `>=8.1,<8.4`, `8.3.*`) and PEP 440's. `lts`, `lts/*`, `lts/iron`
and `node` are satisfied by any server. A file that cannot be understood is reported and skipped.

`node@22` is any installed 22.x, and the newest 22 when one has to be installed; to move an
application to a newer patch release, pin it (`node@22.12.0`). Where they are, and how they are
installed:

- **Node** is downloaded from nodejs.org as the official tarball, and its SHA-256 is checked
  against the `SHASUMS256.txt` of that release before one file is unpacked; it is unpacked with
  the same checks every archive of Noust gets (no links out, no devices, no setuid), run once to
  prove it is the version it says, and only then put in place with a rename.
  `node@22` takes the newest 22; a range takes the newest long-term release it allows.
  The checksum is fetched over HTTPS from the same origin: the release signature is not verified.
  Only glibc systems (not Alpine) and the architectures nodejs.org publishes.
- **Python** is installed with `uv python install` (uv must be on the server; without it the
  error says how to get it, or to install the system's `python3.x` instead).
- **PHP** is the distribution's package. A version that is not installed is installed with
  `apt` (`php8.3-fpm` and the extensions the generic PHP install adds) on Debian and Ubuntu; the
  versions their archives lack come from the Sury or Ondrej repositories, which Noust names but
  does not add. On Fedora, RHEL and openSUSE the error says how (Remi's modules, openSUSE's
  per-major packages).

Managed Node and Python live under `/opt/noust/runtimes/<language>/<version>` (Python's is the
`cpython-*` directory uv made inside it), outside the state directory, which the build sandbox
cannot open (`<data dir>/runtimes` for a central in a container). They are owned by root and
readable by everyone: a build can run them and cannot change them.

On a pinned application:

- Builds run with the runtime first on `PATH`, and its `node`, `npm` or `python3` by absolute
  path (the sandbox finds programs through its own fixed `PATH`).
- The unit sets `PATH=<runtime>/bin:<system path>` and its command uses the runtime's `npm`.
- Dependencies installed under another version are not reused: the stamps record the runtime's
  versions.
- A PHP application's pool is written in the FPM of its version; `start`, `stop`, `restart`,
  the health gate, limits and deletion all find the pool where it is. Changing the pin moves it.

```bash
noust runtime list                    # what is installed, what the server runs, who runs on what
noust runtime install node@22         # ahead of a deployment (a deployment installs it itself)
noust runtime install python@3.12
noust runtime install php@8.3
noust runtime remove node@22.11.0     # the full version; refused while an application runs on it
```

## Node frameworks

Five frameworks have a deployer of their own. Each is recognised from its files, tells a server
from static files by what its configuration says (read, never run), and sets what its server
needs: it listens on `127.0.0.1` where nginx proxies, and behind the proxy's forwarded headers.
They are tried before Next.js and Vite, because SvelteKit and Remix list `vite` too. The package
manager is the lockfile's (`pnpm-lock.yaml`, `bun.lockb` or `bun.lock`, `yarn.lock`,
`package-lock.json`), then `packageManager`.

| Type | Detected by | A server when | Static when | Start | Publishes |
|---|---|---|---|---|---|
| `nuxt` | `nuxt` (or `nuxt3`, `nuxt-edge`) or a `nuxt.config` | Nuxt 3 and 4: always, unless generated | `build` runs `nuxt generate`, or `nitro.preset: 'static'`; Nuxt 2 with `target: 'static'` | `node .output/server/index.mjs`; Nuxt 2: `<pm> run start` | `.output/public` (`dist` for Nuxt 2) |
| `sveltekit` | `@sveltejs/kit` | `adapter-node` | `adapter-static` | `node build` | `build`, or the adapter's `pages` |
| `astro` | `astro`, or an `astro.config` | `@astrojs/node` (standalone) | no adapter | `node ./dist/server/entry.mjs` | `dist`, or `outDir` |
| `remix` (also React Router v7) | `@remix-run/*`, `@react-router/*`, or a `react-router.config` | the project's `start` script, or `remix-serve` / `react-router-serve` over the build | SPA mode (`ssr: false`) | `<pm> run start` | `build/client` |
| `angular` | `angular.json` with `@angular/core` | `@angular/ssr`, or Universal, with a server build | otherwise | `node dist/<project>/server/server.mjs` (`main.js` for Universal) | `dist/<project>/browser` (`dist/<project>` for the older browser builder) |

What a server of these is given besides `PORT`: `HOST=127.0.0.1`; SvelteKit also
`PROTOCOL_HEADER=x-forwarded-proto`, `HOST_HEADER=x-forwarded-host` and
`PORT_HEADER=x-forwarded-port`, without which a form submission behind a proxy is refused as
cross-site.

A configuration that cannot be served is refused when the project is installed, with the way
out: SvelteKit with `adapter-auto` or a platform's adapter, Astro with the adapter of Vercel,
Netlify, Cloudflare or Deno or the Node adapter's `middleware` mode, a Remix project with
neither a `start` script nor `remix-serve`. If the project is built some other way, say what to
run (`start`) or where the files are (`publish_directory`): the block wins over what is refused.

An application deployed as `vite` before 3.4 stays `vite`: an update uses the type it was
created with.

## Python

- **Bound to the loopback address.** The default start is `gunicorn app:app -b 127.0.0.1:PORT`
  (nginx proxies to `127.0.0.1`), no longer `0.0.0.0`: bound to every interface, the port
  answered the network too wherever the firewall did not close it.
- **Workers**: `WEB_CONCURRENCY`, which Gunicorn reads when it is given no `-w`. The unit sets
  it to 4; put another value in the application's `.env` (its Environment page in the console, or `noust env configure`) and restart. With
  your own `start`, workers are an argument of it.
- **Servers**: Gunicorn (with Uvicorn workers for an ASGI application), or Hypercorn when the
  project depends on it and not on Gunicorn. Anything else is a `start` of your own, and then
  Noust does not install a server over the project's.
- **The version**: `.python-version`, `runtime.txt` or `build.runtime`
  ([Runtimes](#runtimes)); the virtual environment is created by that interpreter.
- **Installing**: `pip install -r requirements.txt`; a project with only a `pyproject.toml` (or
  a `setup.py`) is installed with `pip install .`; Poetry (`poetry install --without dev`) and
  Pipenv when their lockfile is there.
- **Django**: the project's own `wsgi.py` and `asgi.py` are found (never the virtual
  environment's), also when `manage.py` is in a subdirectory (`gunicorn --chdir backend ...`).
  `collectstatic` runs only when a settings module sets `STATIC_ROOT`: without it the command
  fails, and a project that serves static files another way has no use for it. **Migrations
  are not run for you**: a schema change is a decision, so declare it, and Noust records that
  the deployment changed the schema and asks before going back past it (a project with
  migrations and no hook is told so in the deployment's log):

  ```yaml
  hooks:
    pre_deploy:
      - run: ./venv/bin/python manage.py migrate --noinput
        migrates: true
  ```

## Go, Rust and Ruby

A repository with a `go.mod`, a `Cargo.toml` or a `Gemfile` used to be only ever a container. Since
3.4 it is built on the server, in the build sandbox, with the server's own toolchain, and the
program runs as the application's unit. A repository that commits a `Dockerfile` or a
`Containerfile` stays a container (the author said how it runs); `--type go`, `--type rust` or
`--type ruby` builds it natively anyway.

The toolchain is a distribution package, installed system-wide (the build account cannot use one
under `/root`). When it is missing, `noust inspect` and the deployment's pre-flight say what to
install on this server:

| Type | Install | Detected by |
|---|---|---|
| `go` | `apt install golang-go` | a `go.mod` and a `package main` (the root, or the one program under `cmd/`) |
| `rust` | `apt install cargo` | a `Cargo.toml` with a `src/main.rs`, a `src/bin/` or a `[[bin]]`, or a workspace |
| `ruby` | `apt install ruby ruby-dev bundler build-essential` | a `Gemfile` next to a `config.ru` or Rails's `config/application.rb` |

What each does:

- **Go** downloads the modules in the install phase (`go mod download`; nothing for a vendored
  module) and runs `go build -trimpath -buildvcs=false -o bin/<app> <package>`. A module with several
  programs says which with `build.build` and `build.start`. Go 1.21 and later fetches the toolchain a
  `go.mod` asks for; an older Go refuses a newer `go` line, saying so.
- **Rust** runs `cargo fetch` in the install phase and `cargo build --release --offline` for the
  program `cargo metadata` names (a workspace with several says which with `default-run` or the
  build block). The binary is copied into the release as `bin/<app>`.
- **Ruby** installs the bundle in the release (`vendor/bundle`, no development or test gems,
  `deployment` when there is a `Gemfile.lock`), precompiles the assets of a Rails application as
  production (`bundle exec rails assets:precompile`), and starts Puma on `127.0.0.1` and the
  application's port (`rackup` when the bundle has no Puma). Bundler is configured in the release's
  `.bundle/config`, so a hook and `noust app exec` find the gems without being told. `RAILS_ENV`
  and `RACK_ENV` are written to `.env` as `production` when it has no value for them, and a Rails
  application gets a generated `SECRET_KEY_BASE` the same way: never replaced once there.
  Migrations are a decision, as with Django: declare them as a hook.

  ```yaml
  hooks:
    pre_deploy:
      - run: [bundle, exec, rails, db:migrate]
        migrates: true
  ```

The unit sets `PORT` and, for Go and Rust, `HOST=127.0.0.1`; a program that binds every interface
regardless is the firewall's to close, as any application's is. `runtime` is not a key of these
types (the toolchain is the server's), and neither is `publish_directory`. A Go or Rust program
needs no health route: any answer but a server error means it is up.

## Java and Kotlin, .NET, Elixir and Deno

The same way, with the same decisions as Go, Rust and Ruby: built in the build sandbox with the
server's own toolchain, run as the application's unit, a `Dockerfile` keeps a repository a
container, `--type java` (or `dotnet`, `elixir`, `deno`) builds it natively anyway, and a
repository of the language that has nothing to run (a library) says so instead of "no deployer".

| Type | Install | Detected by |
|---|---|---|
| `java` | `apt install default-jdk-headless maven` (Gradle comes with the project's wrapper) | a `pom.xml` or `build.gradle[.kts]` that names a program: Spring Boot, Quarkus, Micronaut, Ktor, the `application` or a shadow plugin, or a `mainClass` (the root and the modules one level down are read) |
| `dotnet` | `apt install dotnet-sdk-8.0` (Debian needs Microsoft's feed) | a project that applies `Microsoft.NET.Sdk.Web` or references ASP.NET Core, at the root or up to three directories down (test projects are left out) |
| `elixir` | `apt install elixir erlang-dev build-essential` | a `mix.exs` with `mod: {...}`, a `releases:` block or Phoenix |
| `deno` | Deno 2 or later, system-wide (`npm install -g deno`) | a `deno.json[c]` with a `start` (or `serve`) task, or a `main.ts` / `server.ts` / `index.ts` |

What each does:

- **Java and Kotlin** build with the project's `mvnw` or `gradlew` when it has one (run as
  `sh <wrapper>`, so an archive that lost the executable bit still works), else the server's `mvn`
  or `gradle`. Maven runs `package -DskipTests` after `dependency:go-offline` in the install phase
  (for a project without modules); Gradle runs `bootJar` (Spring Boot), `buildFatJar` (Ktor),
  `installDist` (the `application` plugin) or `assemble`, always with `--no-daemon`. Tests are never
  run. The program is the jar that has a `Main-Class` in its manifest (not the one whose name looks
  right: the plain, sources and `.original` jars are skipped), copied to `dist/app.jar` so the unit's
  command does not change when the version in the file name does. Quarkus runs where it was built
  (`quarkus-app/quarkus-run.jar` needs its `lib/`), and `installDist` runs its start script. A
  multi-module build whose modules produce several programs says which with `build.start`. The JDK
  must be at least the Java the project asks for (`.java-version`, `.sdkmanrc`, the POM's
  `java.version` or `maven.compiler.release`, Gradle's compatibility); a Gradle toolchain provisions
  its own. The unit sets `PORT`, `HOST` and the port and address the framework reads
  (`SERVER_PORT` for Spring Boot, `QUARKUS_HTTP_PORT` for Quarkus, `MICRONAUT_SERVER_PORT`).
  Maven (multi-module) and Gradle download while they build, so the `--network strict` profile
  needs `build.install` to fetch first (`./gradlew dependencies`, `mvn dependency:go-offline`).
- **.NET** runs `dotnet restore` in the install phase (`--locked-mode` when `packages.lock.json` is
  committed) and `dotnet publish -c Release -o .publish --no-restore`, without the native host and
  without the compiler servers that would outlive the build's unit. The unit runs
  `dotnet .publish/<App>.dll` listening on `ASPNETCORE_URLS=http://127.0.0.1:<port>`, with
  `ASPNETCORE_CONTENTROOT` at the publish directory (that is where `wwwroot` and `appsettings.json`
  are). Several web projects need `build.build` to say which. The project's `TargetFramework` is
  checked against the SDK before anything is restored, and a published program whose ASP.NET Core
  runtime the server lacks is warned about (install it, or `DOTNET_ROLL_FORWARD=Major` in `.env`).
  Data Protection keys live in memory unless the application points them somewhere.
- **Elixir** builds a Mix release as production (`MIX_ENV=prod`, whatever `.env` says): Hex and Rebar
  are installed for the application, `mix deps.get --only prod` is the install, `mix compile`,
  Phoenix's `mix assets.deploy` (when the alias exists) and `mix release --overwrite` the build, and
  the unit runs `_build/prod/rel/<name>/bin/<name> start`. `.env` gets `PHX_SERVER=true`,
  `RELEASE_DISTRIBUTION=none` (a release started otherwise runs `epmd` and listens on a node name),
  and for Phoenix `PHX_HOST` (the domain) and a generated `SECRET_KEY_BASE`, once. Migrations are a
  decision: declare them as a hook (`run: [./_build/prod/rel/NAME/bin/NAME, eval, MyApp.Release.migrate]`,
  `migrates: true`).
- **Deno** runs `deno install` (with `--entrypoint` for the files the start task runs, so imports
  written in the program are fetched too) with the module cache in the release (`.deno`, through
  `DENO_DIR`), a `build` task when the configuration has one, and the unit runs `deno task start`, or
  the entry point with `--allow-net --allow-read --allow-env`; a program that needs more says so in a
  task of its own. A package published to JSR (a `name` and `exports` and no start task) is a library
  and is not deployed.

`runtime` and `publish_directory` are not keys of these types. A program of these types needs no
health route: any answer but a server error means it is up.

## Process types

A repository can say what it runs in one place, as Heroku's Procfile does, or under `processes:`
in `noust.yaml`:

```yaml
processes:
  web: bundle exec puma -b tcp://127.0.0.1:${PORT}
  release: bundle exec rails db:migrate
  worker: bundle exec sidekiq
```

```
web: ./bin/server --port $PORT
release: ./bin/migrate
worker: ./bin/jobs
```

- `web` is the command of the application's unit, when the build block gives no `start` (the
  operator's or the repository's, which win) and the type runs a process.
- `release` runs as the only `pre_deploy` hook when no hooks are declared (the operator's, or the
  repository's `hooks:`, win whole): the deployment stops before any traffic moves when it fails.
  It does not mark itself as migrating the schema; declare it under `hooks.pre_deploy` with
  `migrates: true` for that.
- Every other process type (`worker`, `clock`, ...) is a process of the application that runs beside
  the web one, from the same release, with the account, environment and sandbox of its unit
  ([runtime.md](runtime.md#processes)). The deployment records it with the repository as its source,
  writes its unit before the release is activated, and the health gate restarts it with the web
  process and judges them together: a worker that does not stay up puts the release back. Starting it
  is the repository's say; how many copies run is yours (`noust app process scale`), and a process
  you define with `noust app process set` under the same name always wins over the repository's.
  Dropping a type from the file stops it once the new release holds. Only applications on the
  releases layout run them: one deployed in place says so in the deployment's log (and in `noust app
  process-types show`) and `noust app migrate` moves it. A declared name a unit cannot have (a
  reserved one, an underscore, too long) is skipped with the reason, never a failed deployment.

A command is an argument vector, never a shell line. A Procfile line that needs a shell (`&&`, a
pipe, a redirection, a substitution) is refused with the way out (a script of the repository), and
leading `NAME=value` words run the program through `env`. A `processes:` block wins over a
`Procfile` whole.

**A Procfile is read by new applications only.** A repository that carried a Procfile before
Noust read one keeps starting the way it was deployed: an update never starts reading it, because
that would change a working application's start command and run a `release` command on a database
nothing had ever migrated that way. An application deployed from its Procfile remembers it, and a
`processes:` block in `noust.yaml` is always read. A Procfile that cannot be used (a line that needs a
shell, a link, too large) is a warning in the deployment's log and `noust app process-types show`, never
a failed deployment. `noust app process-types show DOMAIN` says which of the declared types the next
deployment uses and why one is not in effect.

## Build cache

The package managers' downloads live in `/var/cache/noust/build/<app>`, outside every release:
npm, pnpm, yarn, pip, uv and Composer's caches, Go's build and module caches, Cargo's registry and
compiled dependencies. A deploy fetches and compiles only what changed. `noust update --no-cache`
(and the console's "Rebuild without cache") empties it first, and `build: {cache: false}` does so on
every build. `noust app cache show|clear` reports and empties it without deploying.

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

## Limits

- **Blue/green** cannot serve a project from a subdirectory: both instances run from the root of
  their release. The deployment refuses it, naming the way out.
- **PHP** is served by nginx or by Apache (`mod_proxy_fcgi`, enabled with the site; a `.htaccess` is never read) from its web root (`webroot` in the PHP settings, which also serves a
  project in a subdirectory); `base_directory` is refused.
- **A pinned PHP** that is not installed is installed with apt only; on other distributions the
  error says how to install it by hand.
- **The resource monitor** samples the workers of the newest PHP-FPM only: an application pinned
  to an older PHP version has no worker samples until the monitor learns to look in each.
- **Rolling back** to a release keeps the unit of the latest deployment: going back past a
  change of `start`, `base_directory` or runtime does not put the old unit back. Deploy the
  older commit instead (`noust update --commit`): it is built again with the block that commit carries (or the operator's) and the unit follows.
- **Cron jobs** of an application run in the root of its release, not in its base directory.
- A tree that does not exist yet (before the fetch, under `--dry-run`) has no build block: a
  rehearsal shows the type's commands.

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
