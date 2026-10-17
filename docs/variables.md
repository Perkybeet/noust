# Variables, references and secret managers

An application's variables are the ones in its own `.env`. From 3.4 a value can also **refer** to
something defined elsewhere, so a setting that is the same for ten applications is written once,
a secret that must not be kept in Noust stays in the manager that holds it, and the build and the
running application can be given different variables.

This page is for the operator. The routes are in [api.md](api.md), the teams, projects and
environments the scopes come from in [teams.md](teams.md).

## References

A value may hold any of these between double braces. The application keeps the text as it wrote it
and the `.env` holds what it resolved to, so `noust env show` and the console's Environment tab
show the reference, and a change to what it points at reaches the application at its next deploy.

| Reference | Is |
|---|---|
| `{{team.NAME}}` | A shared variable of the application's team |
| `{{project.NAME}}` | A shared variable of its project |
| `{{environment.NAME}}` | A shared variable of its environment (production, staging...) |
| `{{server.NAME}}` | A shared variable of this server |
| `{{app.URL}}`, `{{app.DOMAIN}}`, `{{app.BRANCH}}`, `{{app.COMMIT}}`, `{{app.RELEASE}}`, `{{app.PORT}}`, `{{app.HOST}}` | What Noust knows of this deployment |
| `{{secret:PROVIDER/PATH}}` | A value kept in a secret manager Noust was told about |

Anything else between double braces is the operator's own text and is left alone: a Handlebars or
Jinja template is a legitimate value. A backslash keeps a reference as text (`\{{project.X}}`).

A reference is resolved at every deploy, and again by `noust env resolve` (the console's
**Resolve references**) when a shared variable or a rotated secret should reach the `.env` now. The
running process keeps what it started with until it is restarted. A reference that names something
that does not exist, or that this application may not use, **stops the deploy before anything is
built**: nothing resolves to an empty string, because an application started with a value nobody
noticed is missing is worse than a deployment that does not start. The error names the reference
and says how to fix it.

```bash
noust env set shop.example.com 'API_URL={{project.API_URL}}' 'NEXTAUTH_URL={{app.URL}}'
noust env set shop.example.com 'STRIPE_KEY={{secret:vault/shop/stripe#key}}'
noust env resolve shop.example.com
```

## Shared variables

A shared variable belongs to a scope and is referred to from the applications in it:

| Scope | Defined by | Referred to as |
|---|---|---|
| Server | `settings.manage` | `{{server.NAME}}` from any application |
| Team | `apps.manage` in the team | `{{team.NAME}}` |
| Project | `apps.manage` in its team | `{{project.NAME}}` |
| Environment | `apps.manage` in its team | `{{environment.NAME}}` |

The same name in two scopes is two variables: nothing overrides anything, because a reference says
which one it means. A shared variable is **not** given to an application that does not refer to it.

```bash
noust variables set project API_URL https://api.example.com --team acme --project web
printf %s "$KEY" | noust variables set team STRIPE_PUBLIC --team acme --stdin --secret
noust variables list environment --team acme --project web --environment staging
noust variables used-by project API_URL --team acme --project web
```

A secret is hidden everywhere it is listed; reading it in clear needs an administrator in sudo
mode (`--unmask`, `?unmask=true`) and is recorded in the audit trail. Deleting a variable that an
application still refers to says which ones: their next deploy fails until the reference is changed.

In the console: a team's **Variables** tab, the **Project variables** and the environment's sections
of a project's page, and **Settings > Variables** for the server's.

## Bringing in a .env file, and seeing the stored one

`noust env set` takes `NAME=VALUE` words. A developer has a `.env`: comments, `export` in front
of a line, quotes, a value of several lines. `noust env sync` reads that file as the application's
own `.env` is read and **creates and updates what it holds, deleting nothing**:

```bash
noust env sync shop.example.com --file .env.production
printenv | grep ^APP_ | noust env sync shop.example.com --file -        # standard input
noust env sync shop.example.com --file .env --prune                    # also remove what the file lacks
noust --dry-run env sync shop.example.com --file .env                  # only say what would change
noust env sync shop.example.com --file .env --build-only                # the audience of what it sets
```

It says which names it created, updated and left as they were, and never a value. `PORT` and
`NODE_ENV` (the unit sets them) and the `NOUST_*` names are skipped with the reason, because a
`.env` from a laptop has a `PORT` and refusing the whole file for it would make the command
useless. A name set twice in the file counts once, the last, and is reported. The comment above a
variable becomes its comment. A running process keeps its values until it is restarted.

Over the API, `POST /api/apps/{domain}/env/sync` takes `{"content": "<the file's text>", "prune":
false, "audience": null, "dry_run": false}` (sudo mode, as a write is) and answers with the same
names. `GET /api/apps/{domain}/env/file` is the stored `.env` as text, for a viewer with a search
box and a download (`?download=true` names the file `<app>.env`): the secrets are masked as in
the table, `?unmask=true` needs an admin credential and sudo mode and is audited, and what only
the build is given is named in a comment at the end, without its value.

## Values of several lines, literals and comments

The `.env` is read by more than Noust: by systemd (`EnvironmentFile=`), by Docker Compose, by the
dotenv library of the application. These are the rules that make them all read the same file.

- **A value of several lines** (a private key, a certificate, a JSON document) is written between
  single quotes and read back up to the line that ends with the quote that opened it:

  ```
  # The signing key. Rotate yearly.
  TLS_KEY='-----BEGIN PRIVATE KEY-----
  MIIEvQIBADANBgkq...
  -----END PRIVATE KEY-----'
  ```

  systemd 255 reads that as one variable, byte for byte (single quotes may span lines and have no
  escapes); Compose and the Node and Python dotenv libraries read it the same. It may hold no
  single quote, which no quoting carries through all of them, and no control character but the line
  feed (a carriage return from a Windows file is refused: save it with Unix line endings). From a
  terminal, `noust env set shop.example.com --file TLS_KEY=key.pem` reads it from a file, taking off
  the one line feed an editor adds; in the console the value field takes line breaks and so does
  **Paste .env**. A value of lines is never put in a unit: only the `.env` takes it, and
  `noust create --env` still refuses one.
- **A value is literal.** A `$` (and a backslash) puts the value between single quotes, so Compose
  and a shell do not expand `pa$$word` or `${NAME}`. systemd never expanded them in this file.
  python-dotenv, though, expands `${NAME}` in a single-quoted value too: pass `interpolate=False`
  to it for a value that holds one. A value with both a `$` and a single quote is left bare.
- **A comment** is the run of `#` lines directly above a variable, with no blank line between. It is
  kept through every write of the file, by Noust or by hand, and goes with the variable when it is
  removed. `noust env comment shop.example.com QUEUE_URL "where the queue lives"` sets it (an empty
  text takes it away), `noust env set ... --comment` sets one while setting values, and the console
  shows it under the name and edits it in the variable's dialog; a `#` line above a pasted variable
  becomes its comment. At most 500 characters and 8 lines of plain text. A variable only the build
  is given has none: it is in no file. The comments of a hand-written file that are not directly
  above a variable are lost the first time Noust rewrites it, as they always were.
- **The file is sorted by name.** A write puts the variables in alphabetical order, each under its
  comment; the names are checked (letters, digits and underscores, not starting with a digit) before
  anything is saved.

## Who gets a variable

Every variable has an audience:

- `both` (the default): the install and the build see it, and so does the running application.
- `build`: only the install and the build. It is **never written to the application's `.env`**.
  For a token that fetches a private package, an analytics key baked into the bundle.
- `runtime`: only the running application. The build does not see it.

```bash
noust env set shop.example.com NPM_TOKEN=... --build-only
noust env audience shop.example.com SENTRY_DSN --runtime-only
```

In the console it is the **Given to** choice of a variable and of its row's menu; a change is
saved with the values.

Every type of application honours the audience, each the way its build works:

| Type | A variable only the build is given | A variable only the application is given |
|---|---|---|
| Node.js, Python, Next.js and the rest | In the environment of the install and the build; in no file. | Taken out of the install and the build. |
| Docker Compose | In the environment of `docker compose build`, so `${NPM_TOKEN}` in `build.args` and a Compose secret with `environment: NPM_TOKEN` read it; in no file, so no `env_file:` hands it to a container. | Set to empty for `docker compose build`, so `${NAME}` in `build.args` is empty (Compose would otherwise fill it from the `.env`). |
| Container, built from a Dockerfile or by Railpack | A BuildKit secret, `RUN --mount=type=secret,id=NPM_TOKEN`. It is **never a build argument**: an argument stays in the image's history for whoever pulls it, and Noust says so in the deploy log when the Dockerfile declares `ARG NPM_TOKEN`. | Not in the build: a container's build is given only what its settings and the build-only variables name. |
| Container, an image from a registry | **Refused**: nothing is built, so the variable would be given to nobody. Give it to the application instead. | The same as `both`. |
| Monorepo | In the environment of `pnpm install` and `pnpm build`; in no file, in the root `.env` or in a workspace's. | Taken out of the install and the build. |

The refusal is an error that names the variable and the way out, at the moment the variable is
saved, never a silent drop.

## What Noust gives every application

`NOUST_URL`, `NOUST_DOMAIN`, `NOUST_BRANCH`, `NOUST_COMMIT` and `NOUST_RELEASE` are given to the
running application, and going back to an earlier release gives it that release's commit. Where
they are written depends on what loads them:

| Type | Where | Rollback |
|---|---|---|
| Node.js, Python and the rest | A file beside each release (`.noust-env`) that the unit loads. | The unit loads the file of whatever `current` points at. |
| Docker Compose, container | Inside the stack's own `.env`, in lines headed `# Set by Noust on every deployment`, because that file is what reaches every container and every `docker compose` command (`${NOUST_URL}` in the compose file works, and so does `env_file: .env`). The rest of the file is never rewritten. | Going back to a release writes that release's commit and name; an update that does not pass the health gate puts the file back as it was. |
| Monorepo | A file in each workspace's directory (`.noust-env`) that its unit loads. A workspace answers on a name of its own, and is told that one. | The file belongs to the release. |

The names are Noust's: a variable of your own cannot use them, and `noust env show`, the export
and a clone leave the ones in a stack's `.env` out. The port is the unit's (`PORT`, and `HOST` for
the Node frameworks), and `{{app.PORT}}` and `{{app.HOST}}` name the same facts in a value.
`NOUST_RELEASE` is the release a stack's images are kept under; a stack that is not a checkout of
a repository has no commit and no release to name, so it is not given them.

A [preview](previews.md) is also told what it is: `NOUST_PREVIEW_OF` is the application it copies
and, when it is for a pull request, `NOUST_PR_NUMBER` is that pull request's number (a preview of
a branch or a tag has none). `{{app.PR_NUMBER}}` and `{{app.PREVIEW_OF}}` name them in a value, for
a database name or a banner that says which pull request this is.

The build gets them too, **except the commit and the release**: both change on every deploy, and a
build that depends on them could never be reused or compared with the last one. A build that needs
the commit asks for it by name: a variable whose value is `{{app.COMMIT}}` and whose audience is
`build`. A Dockerfile receives the ones it declares (`ARG NOUST_URL`) as build arguments. The names
are Noust's: a variable of your own cannot use them. The console lists them, read-only, under the
environment.

## Editing a monorepo's variables

A monorepo's units load a file in each workspace (`.env.production`), not the `.env` at the root
that `noust env` and the console edit. A change to the application's variables is carried to every
workspace's file, naming only what changed: a workspace keeps its own `PORT` and what its
`package.json` asked for. The same goes for a reference that resolves to a new value at the next
deploy. A running process keeps what it started with until it is restarted.

## Secret managers

A secret manager is where a value lives that Noust must not store. Noust keeps the **token** to read
it (sealed in the secret store, never in the database or `config.yaml`, never shown again) and the
reference; the value is fetched when the application is deployed.

| Kind | Address | Settings |
|---|---|---|
| `vault` | Yours, over https | `mount`, `kv_version`, `namespace` (all optional) |
| `doppler` | `https://api.doppler.com` | `project`, `config` |
| `infisical` | `https://app.infisical.com` or yours | `project_id`, `environment`; `secret_path`, `client_id` optional |

```bash
printf %s "$VAULT_TOKEN" | noust secrets add vault --kind vault --address https://vault.example.com:8200 --token-stdin
noust secrets test vault
noust env set shop.example.com 'DB_PASSWORD={{secret:vault/shop/db#password}}'
```

- A Vault path is `secret/path#field` (the field may be left out when the secret has one); a Doppler
  or Infisical path is the secret's name in the project and config set on the provider.
- A provider may be limited to one team (`--team`), so a team's application cannot name a manager
  that holds another team's secrets.
- The fetched value does end up in the application's environment file (`0600`, owned by the account
  it runs as), because systemd has to load it from somewhere. It is in no other place Noust keeps:
  not the store, the audit trail, an API response or a log; a deploy's log is scrubbed of it.
- Requests have a timeout, follow no redirect, read at most 1 MiB and never print the token.
- **Fail closed**: a manager that cannot be reached, answers an error or does not have the secret
  stops the deploy before anything is built or switched, with its own words. Removing a provider
  deletes its token at once: the applications that refer to it fail their next deploy until their
  references change.
- **At every deploy, for every type.** A Node.js application, a Docker Compose stack, a container
  and a monorepo resolve their references again before anything is built, so a rotated secret
  reaches the `.env` (and, for a monorepo, every workspace's file) with the next update. For a
  stack and a container it is the `.env` its containers load; recreating them (an update does) is
  what puts the value in the running process.

In the console: **Settings > Secret managers**, with a connection test.

## Applications in other places

- **Previews** are given only what their application's settings name, plus the set defined for
  previews alone ([previews.md](previews.md)).
- **A clone** keeps the references as written, so `{{app.URL}}` is the clone's own address
  ([teams.md](teams.md)).
- `noust app export` writes what the references resolved to, not the references: an import on another
  server does not need that server to have the scopes and providers they name.
