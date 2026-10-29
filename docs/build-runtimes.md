# Runtimes and toolchains

Part of [Build, start and runtime](build.md).

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

An application deployed as `vite` before 4.0 stays `vite`: an update uses the type it was
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
4.0 it is built on the server, in the build sandbox, with the server's own toolchain, and the
program runs as the application's unit. A repository that commits a `Dockerfile` or a
`Containerfile` stays a container (the author said how it runs); `--type go`, `--type rust` or
`--type ruby` builds it natively anyway. **Rails 7.1 and later write a `Dockerfile` into every new
project**, so an application generated by `rails new` is deployed as a container unless
`--type ruby` (or `type: ruby` in `noust.yaml`) says otherwise.

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
  its own. The unit sets `PORT`, `HOST` and the address the framework reads
  (`SERVER_ADDRESS` for Spring Boot, `QUARKUS_HTTP_HOST`, `MICRONAUT_SERVER_HOST`), and the port
  reaches the framework from `PORT` on the command line (`--server.port=${PORT}` for Spring Boot,
  `-Dquarkus.http.port` and `-Dmicronaut.server.port` as JVM properties): the two instances of a
  zero-downtime application, each with its own `PORT`, would otherwise be told the same port.
  The build sandbox points Maven's and Gradle's `user.home` at the build cache
  (`MAVEN_OPTS` and `GRADLE_OPTS`), because the JVM ignores `$HOME` and `noust-build` has no home to
  keep `~/.m2` in; an application's own `MAVEN_OPTS` is added after that.
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
  `migrates: true`). The distributions ship an Elixir older than current Hex packages want (Ubuntu
  24.04 has Elixir 1.14 on Erlang/OTP 25, and recent `plug` and `cowlib` need 1.15 and OTP 26), and Mix
  reports that as an error from inside the dependency; when the output says a dependency requires a
  newer Elixir, the failure names it and the two versions and says to install one system-wide or to pin
  an older dependency.
- **Deno** runs `deno install` (with `--entrypoint` for the files the start task runs, so imports
  written in the program are fetched too) with the module cache in the release (`.deno`, through
  `DENO_DIR`), a `build` task when the configuration has one, and the unit runs `deno task start`, or
  the entry point with `--allow-net --allow-read --allow-env`; a program that needs more says so in a
  task of its own. A package published to JSR (a `name` and `exports` and no start task) is a library
  and is not deployed.

`runtime` and `publish_directory` are not keys of these types. A program of these types needs no
health route: any answer but a server error means it is up.

Each of them is built and run for real, on a clean server with systemd, by the integration harness
(`python tests/integration/toolchains_run.py`; the toolchains are installed in its image,
`tests/integration/Dockerfile.toolchains`, never on the host): a Maven project, a Spring Boot one
through the wrapper (also as a zero-downtime application), a Gradle Kotlin one with `installDist`, a
Ktor one with `buildFatJar`, an ASP.NET Core one, a Mix release (Plug, and Phoenix), a Deno task
server, a Go program with a module, a Rust one with a crate and a Rails 7.2 application with its
asset pipeline; each is created, answers through the web server, is updated to a second commit, and
goes back to the first with `noust releases rollback`. So are Nuxt, SvelteKit, Astro, Remix and
Angular (server-side rendered), each pinned to Node 22 by a `.nvmrc` on a server that has Node 18:
the unit runs the Node that Noust downloaded, and the scenario checks it says so. A scenario of its
own installs the runtimes: Node from nodejs.org (the checksum checked, the directory root's, nothing
set-uid in it, the build account able to run it and not to write to it), Python with `uv` (the error
without `uv` says how to get it), and PHP-FPM through `apt` on a server that had lost it.

## System packages

An application that resizes images wants `libvips-dev`, one that renders PDFs wants a font and a
converter. Noust builds on the host, so those are packages of the host, and an operator declares
them per application:

```bash
noust app packages set shop.example.com libvips-dev ffmpeg     # records the list and installs what is missing
noust app packages add shop.example.com fonts-noto-core
noust app packages show shop.example.com                       # what is declared, what the host has
noust app packages remove shop.example.com ffmpeg               # stops declaring it; it stays installed
```

`PUT /api/apps/{domain}/packages` records the list, `POST .../packages/install` queues the job that
installs it, and the **System packages** card of **Build** does both. The next update
installs whatever is missing before it installs dependencies or builds, so a rebuilt server brings
them back by itself; if they cannot be installed the update stops and what serves keeps serving.

- **Only the operator writes them.** Installing a package makes root run the package manager, so the
  list is stored apart from the build block, `noust.yaml` has no key for it (a `packages:` there is
  an unknown key), and writing it is `root.equivalent` with sudo mode, like the deploy hooks. A
  team's role never holds it.
- **The server's policy says which.** `security.app_packages` is a list of names and globs
  (`libvips*`, `fonts-*`); when it is not empty a package outside it is refused when declared and
  again when installed, whoever asks. Empty allows any package the distribution has.
- **A name is a name.** Letters, digits and `+._-`, starting with a letter or digit: never an option,
  a version pin, a path or a space, so what reaches the package manager as an argument is a package
  name. At most 50 per application.
- **Installed through the one backend** that knows each family (apt, dnf, zypper), as an argument
  vector with no shell, in the package manager's own non-interactive environment, **without what a
  package recommends** (a library is wanted, not the daemon it suggests). Only what the host lacks
  is installed, and nothing is ever removed.
- **Not for containers.** A Compose stack and a container application install what they need in
  their image; a preview uses its parent's list. A host whose packages are changed by transactions
  and a reboot, or with no apt, dnf or zypper, keeps the list and installs nothing: the update says
  to install them by hand.

Both changes are audited (`apps.packages`, `apps.packages_installed`), and the packages the
build links against are on the host the sandbox only reads.
