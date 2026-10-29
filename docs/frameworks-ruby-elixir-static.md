# Ruby on Rails, Phoenix, and static site generators

Part of [Deploying by framework](frameworks.md).

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
