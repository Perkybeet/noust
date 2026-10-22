# Deploying by framework

Noust deploys the stacks below natively: it detects them from the repository, installs and builds
them, runs them as a systemd unit (or serves their files) behind nginx or Apache with a
certificate, and keeps every deploy as a release behind a health gate. No Dockerfile and no
`Procfile` are needed. This page says, for each, what is detected, what Noust does, and the one or
two things the project may need to say itself.

Two things are the same for all of them:

```bash
noust create -d app.example.com -s https://github.com/you/app.git     # detect, build, run, serve, certificate
```

To see what Noust would do before it does it, the console's **New application** wizard inspects the
repository first (the type, the commands it will run, the port, the variables in `.env.example`
with the secrets flagged), and `noust --dry-run create ...` rehearses the command without changing
anything.

- When the repository has an `.env.example`, Noust generates the application's `.env` from it, with
  random values for the secrets. `--env KEY=value` and `--env-file` set variables of your own.
- When Noust does not detect what you mean, `--type` chooses it, and a `build:` block in
  `noust.yaml` (or `noust app build set`) replaces any of the install, build and start commands, the
  base directory, the directory a static build leaves and the runtime: [build.md](build.md).
- A command that must run on every deploy before the new version serves (a database migration)
  is a `pre_deploy` hook in `noust.yaml`: [compose.md](compose.md#noustyaml). Hooks run as the
  application's own account with its `.env`, and a failing `pre_deploy` stops the deploy with what
  served before still serving.
- Go, Rust, Ruby, Java and Kotlin, .NET, Elixir and Deno are built on the server with its own
  toolchain: [build.md](build.md#java-and-kotlin-net-elixir-and-deno) (Rails and Phoenix have their own
  sections below). A stack Noust has no native runtime for (Swift, Zig, Crystal...) is deployed from its
  `Dockerfile`, or with Railpack, as a container application: [containers.md](containers.md). Every stack on
  this page has a minimal example in [`examples/`](../examples/README.md) that deploys as it is.

## Node.js frameworks

| Framework | Detected by | What Noust runs |
|---|---|---|
| [Next.js](#nextjs) | `next.config.{js,mjs,ts}`, or `next` in `package.json` | install, `build`, then `start` (or the standalone server) |
| [Nuxt](#nuxt) | `nuxt` in `package.json`, or a `nuxt.config` | install, `build`, then Nitro's server (`.output/server/index.mjs`), or the generated files |
| [SvelteKit](#sveltekit) | `@sveltejs/kit` in `package.json` | install, `build`, then `node build` with `adapter-node`, or the files with `adapter-static` |
| [Astro](#astro) | `astro` in `package.json`, or an `astro.config` | install, `build`, then the files, or `dist/server/entry.mjs` with `@astrojs/node` |
| [Remix and React Router](#remix-and-react-router) | `@remix-run/*` or `@react-router/*` | install, `build`, then the `start` script or `remix-serve` (the client bundle in SPA mode) |
| [Angular](#angular) | `angular.json` with `@angular/core` | install, `build`, then the client bundle, or the SSR server |
| [Vite, Vue and React](#vite-vue-and-react) | `vite.config.*`, or `vite` in `package.json` | install, `build`, served as static files (`preview` when it uses SSR) |
| [Express, Fastify, Koa and NestJS](#express-fastify-koa-and-nestjs) | `package.json` with the framework, a `main` or a `start` script | install, `build` if present, then `start:prod`, `start:production` or `start` |

The package manager (npm, pnpm, Yarn, Bun) is detected from the lockfile; `--pm` forces one. The
Node version is whatever the project pins (`.nvmrc`, `.node-version`, `engines`, or `runtime:
node@22` in the `build:` block): [build.md](build.md#runtimes).

### Next.js

```bash
noust create -d shop.example.com -s https://github.com/you/shop.git
```

Noust runs `build` and then `start` on a port it chooses (`PORT` is in the unit). With
`output: "standalone"` in `next.config.js` it runs the standalone server, which is the smallest
thing to keep per release. Server-side secrets go in `.env` (generated from `.env.example`);
`NEXT_PUBLIC_*` variables are baked into the bundle at build time, so changing one needs an
update, not just a restart. A project that uses Prisma has its migration applied before the new
release serves, and a failed one aborts the update; declaring the migration as a hook takes it
over ([compose.md](compose.md#prisma)).

### Nuxt

```bash
noust create -d site.example.com -s https://github.com/you/site.git
```

Nuxt 3 and 4 build a Nitro server; Noust starts `node .output/server/index.mjs`. A project that
generates static files (`nuxt generate`, or `nitro.preset: 'static'`) is served as files and has no
process.

### SvelteKit

```bash
noust create -d app.example.com -s https://github.com/you/app.git
```

With `@sveltejs/adapter-node` Noust runs `node build` (with the forwarded-header variables a
SvelteKit server needs behind a proxy, without which a form submission is refused as cross-site);
with `adapter-static` it serves the output as files. `adapter-auto` and the platform adapters produce
nothing a server can run, and Noust refuses them with the way out: choose one of those two adapters
in `svelte.config.js`, or say what to run with `start:` in a `build:` block.

### Astro

Static output (the default) is served as files. With `@astrojs/node` in standalone mode Noust runs
`dist/server/entry.mjs`.

### Remix and React Router

Noust runs the project's `start` script (`remix-serve ./build/server/index.js`, or the React Router
equivalent). A project in SPA mode, whose build leaves only a client bundle, is served as files.

### Angular

A client-side Angular application is served from its build output
(`dist/<project>/browser`); one with server-side rendering (`@angular/ssr`) runs its server. Say
`publish_directory` in the `build:` block when the workspace has several projects.

### Vite, Vue and React

```bash
noust create -d spa.example.com -s https://github.com/you/spa.git
```

A Vite project (Vue, React, Svelte, Solid...) is built and its `dist` is served by the web server as
files, with `index.html` as the fallback so client-side routes work. If the project uses SSR, Noust
runs `preview` instead. A different output directory is `publish_directory:` in the `build:` block.
Variables prefixed `VITE_` are public: they are baked into the bundle at build time.

### Express, Fastify, Koa and NestJS

A Node server needs to listen on the port in the `PORT` environment variable (Noust sets it) and to
bind `127.0.0.1` or all interfaces; nginx is the public face. NestJS: its `build` produces `dist`, and
`start:prod` runs it. Put the database migration in a `pre_deploy` hook:

```yaml
# noust.yaml, at the root of the repository
hooks:
  pre_deploy:
    - run: npx prisma migrate deploy
      migrates: true
```

## PHP frameworks

PHP applications are detected by a `composer.json` with a front controller, or an `index.php` at the
root; Noust runs one PHP-FPM pool per application on its own socket, runs `composer install` when
there is a `composer.json`, finds `public/` (or the web root you give), and writes a fastcgi site
(nginx only). The PHP version is the one in `composer.json` (`require.php`) or `runtime: php@8.3`.

### Laravel

```bash
noust create -d shop.example.com -s https://github.com/you/shop.git --persist storage
```

- `--persist storage` keeps `storage/` (logs, uploads, the cache) across releases instead of losing
  it with each new release directory.
- `.env` is generated from `.env.example`, with random values for the secrets; read the
  application's **Environment** page after the first deploy and check that `APP_KEY` and the
  database settings are what the application needs.
- Migrations are a hook, run before the new release serves:

  ```yaml
  hooks:
    pre_deploy:
      - run: php artisan migrate --force
        migrates: true
  ```

  `migrates: true` makes Noust record that the deployment changed the schema, and ask before going
  back past it ([releases.md](releases.md)).
- A database for the app, with its connection string written into `.env`:
  `noust db provision shop.example.com -e mysql` (or `--database mysql` at `noust create`).

### Symfony

Same as Laravel: `public/` is the web root, `var/` is what to persist
(`--persist var`), and the migration is a hook (`php bin/console doctrine:migrations:migrate
--no-interaction`). Set `APP_ENV=prod` in the environment.

### WordPress and other PHP applications

WordPress, with its MariaDB, is in the catalog: `noust recipe install wordpress -d blog.example.com`
([recipes.md](recipes.md)). Any other PHP application with a front controller deploys like Laravel.

## Python frameworks

Python projects are detected by `requirements.txt`, `pyproject.toml`, `setup.py` or `Pipfile`.
Noust builds a virtual environment (Poetry or Pipenv when the project is locked with them) and runs
Gunicorn on `127.0.0.1`, with Uvicorn workers for FastAPI and Starlette (Hypercorn when the project
depends on it). Pin the interpreter with `runtime: python@3.12`.

### Django

```bash
noust create -d site.example.com -s https://github.com/you/site.git
```

The project's own `wsgi.py` or `asgi.py` is found; `collectstatic` runs when the settings define a
`STATIC_ROOT` (and Noust serves it); put the migration in a hook:

```yaml
hooks:
  pre_deploy:
    - run: python manage.py migrate --noinput
      migrates: true
```

Add the domain to `ALLOWED_HOSTS` (Noust does not guess it), and keep `SECRET_KEY` and the
database URL in `.env`.

### Flask and FastAPI

Noust's default start is `gunicorn app:app -b 127.0.0.1:PORT`, with Uvicorn workers for an ASGI
application (FastAPI, Starlette). If the application object is not `app` in `app.py`, say what to
run with `start:` in the `build:` block (`PORT` is in the environment):

```yaml
build:
  start: gunicorn -b 127.0.0.1:${PORT} -k uvicorn.workers.UvicornWorker app.main:app
```

## Ruby on Rails

```bash
noust create -d app.example.com -s https://github.com/you/app.git
```

A `Gemfile` next to a `config.ru` or a Rails `config/application.rb`, and no `Dockerfile` (a Dockerfile
is how a repository says it runs: it becomes a container application, and `--type ruby` builds it
natively anyway). The Ruby is the **server's own**, a distribution package: Noust does not install a
Ruby version for the application, and it says which package to install when the server has none
(`apt install ruby ruby-dev bundler build-essential`; the RPM names for Fedora and openSUSE). Bundler
reports a mismatch with the project's `.ruby-version`, and the deploy's log warns first.

- **Gems** are installed into the release, by the unprivileged build account in the sandbox:
  `vendor/bundle`, in deployment mode (`Gemfile.lock` is respected; without one the log says gems
  are being resolved on the server and asks you to commit it), without the `development` and `test`
  groups. A rollback is the release's own gems.
- **Assets** are precompiled as production (`rails assets:precompile`) when the application has
  `app/assets` or `app/javascript`; an API-only one has nothing to compile. The build runs with
  `RAILS_ENV=production` and a throwaway `SECRET_KEY_BASE`, whatever `.env` says.
- **It runs** under Puma when the bundle has it (`bundle exec puma -b tcp://127.0.0.1:PORT`), else
  `rackup`, behind the web server. `RAILS_LOG_TO_STDOUT` puts the application's log in the journal and
  `RAILS_SERVE_STATIC_FILES` lets Puma serve the compiled assets.
- **`.env`** gets `RAILS_ENV=production`, `RACK_ENV=production` and a generated `SECRET_KEY_BASE`, once;
  a value you set is never replaced.
- **Migrations are a decision.** Noust does not run `db:migrate` for you; when the application has
  migrations the log says so. Declare it as a hook, which runs before the new release serves and
  aborts the deploy when it fails:

  ```yaml
  # noust.yaml
  hooks:
    pre_deploy:
      - run: bundle exec rails db:migrate
        migrates: true
  ```

- **A database**: `noust db provision app.example.com -e postgresql` creates it and writes its
  connection string to `DATABASE_URL`, which Rails reads. Active Storage on the local disk needs
  `--persist storage`.
- **Background jobs** (Sidekiq, Solid Queue) are a process type: a `Procfile` with a `worker:` line runs
  beside the web process ([build.md](build.md#process-types)).
- A bare Rack application (a `config.ru` and no Rails) is the same type; `rackup` runs it on the port.

What Noust does not do for Rails: it installs no Ruby for the application.

## Phoenix and Elixir

```bash
noust create -d app.example.com -s https://github.com/you/app.git
```

A `mix.exs` with `mod: {...}`, a `releases:` block or Phoenix, and no `Dockerfile`. Noust builds a
**Mix release** as production and runs its script as the unit, so a rollback is a link to a release
that carries the Erlang runtime it was built with. The Elixir and Erlang are the server's own
(`apt install elixir erlang-dev build-essential`).

- **The build** is `MIX_ENV=prod` whatever `.env` says: Hex and Rebar are installed for the
  application (`mix local.hex --force`, in its build cache), `mix deps.get --only prod` is the install
  phase, then `mix compile`, Phoenix's `mix assets.deploy` when the project has the alias, and
  `mix release --overwrite`.
- **`.env`** gets `PHX_SERVER=true` (a release does not start the endpoint without it),
  `RELEASE_DISTRIBUTION=none` (a release started otherwise runs `epmd` and listens on a node name),
  `PHX_HOST` (the domain, which Phoenix checks WebSocket origins against) and a generated
  `SECRET_KEY_BASE`, once. Put `PORT` and `DATABASE_URL` where `config/runtime.exs` reads them: the
  unit sets `PORT`.
- **Migrations are a decision.** `mix phx.gen.release` writes a `Release.migrate` function; declare it
  as a hook:

  ```yaml
  # noust.yaml
  hooks:
    pre_deploy:
      - run: ./_build/prod/rel/my_app/bin/my_app eval MyApp.Release.migrate
        migrates: true
  ```

- **A database**: `noust db provision app.example.com -e postgresql`.
- **A server older than the dependencies.** The distributions ship an Elixir older than current Hex
  packages want (Ubuntu 24.04 has Elixir 1.14 on Erlang/OTP 25, and recent `plug` and `cowlib` need 1.15
  and OTP 26). Mix reports that from inside the dependency; Noust names the dependency and the two
  versions and says to install a newer Elixir system-wide or to pin an older dependency
  ([build.md](build.md#java-and-kotlin-net-elixir-and-deno)). This is not a corner case for Phoenix:
  what `mix phx.new` writes today (Phoenix 1.7.21) stops on Ubuntu 24.04 at `mix assets.setup`, with
  `phoenix_template` asking for Elixir 1.16; with Elixir 1.17 installed system-wide (the precompiled
  build for OTP 25 from `builds.hex.pm`, unpacked into `/usr/local` ahead of the distribution's) the
  same project deploys, serves, updates and goes back.

A plain Plug application without Phoenix is the same type. A cluster of nodes is not what this
sets up: a release runs alone, off the Erlang network.

## Jekyll and other static site generators

Jekyll is **not a type Noust detects**: a repository with a `Gemfile` and a `_config.yml` and no
`config.ru` has nothing to run, so the Ruby type declines it (it is not an application server), and
`index.html` is not at its root until it is built. There are three honest ways, in the order that
costs least:

1. **Build it where its tools are, in CI, and deploy the output.** A pipeline runs `bundle exec jekyll
   build` and deploys `_site` as a static site (`noust upload`, or a repository of the output). Nothing
   about Ruby is needed on the server.
2. **Build it on the server, as a static application with a `build:` block.** The server needs Ruby and
   Bundler (`apt install ruby ruby-dev bundler build-essential`), and gems must be installed where the
   unprivileged build account can write: commit a `.bundle/config` with `BUNDLE_PATH: "vendor/bundle"`
   (the `.gitignore` that `jekyll new` writes lists `.bundle/`: take that line out, or the file is not
   committed).

   ```yaml
   # noust.yaml
   build:
     install: bundle install
     build: bundle exec jekyll build
     publish_directory: _site
   ```

   ```bash
   noust create -d blog.example.com -s https://github.com/you/blog.git --type static
   ```

   `--type static` is needed because detection looks for an `index.html`. The site is served as files
   behind nginx or Apache, with a certificate, and every update is a release like any other.
3. **A container.** A `Dockerfile` with a build stage (Ruby, then `jekyll build`) that leaves the files
   in an image served by a web server runs as a container application ([containers.md](containers.md)).

Eleventy, Docusaurus, Hugo (its binary on the server) and every generator whose tools are on the server
follow the second way; the ones whose tools are Node's need only Node ([build.md](build.md)).

## Stacks without a native runtime

Go, Rust, Java and Kotlin, .NET and Deno are built on the server
([build.md](build.md#java-and-kotlin-net-elixir-and-deno)) unless the repository commits a
`Dockerfile`, like Ruby and Elixir above. The rest (Swift, Zig, Crystal...), and any of those you want
packaged, run as **container applications**: from your `Dockerfile`, or without one with Railpack.

```bash
noust create -d api.example.com -s https://github.com/you/api.git -t container
noust setup railpack                      # once, for repositories with no Dockerfile
noust create -d api.example.com -s https://github.com/you/api.git -t container --builder railpack
```

Each release is an image, so rollback is as quick as for any other application
([containers.md](containers.md)). The migration is a `pre_deploy` hook that runs in a one-off
container of the new image (`bin/rails db:migrate`, `mix ecto.migrate`).

A static site generator whose tools the server has (Eleventy and Docusaurus need only Node) is a
`static` application with `install`, `build` and `publish_directory` in its `build:` block; one whose
toolchain the server does not have is built where the toolchain is, as the Jekyll section above says.

## When it did not go right

Read the deployment's log in the console (**Deployments** of the application) or `noust logs
DOMAIN`. If the application deployed but does not answer, `noust diagnose DOMAIN` says why, most
likely cause first: [troubleshooting.md](troubleshooting.md).
