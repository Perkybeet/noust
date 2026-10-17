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

A Docker Compose application reads its `.env` itself, for the build and for the containers alike,
and Noust does not separate the two there: every variable is in its `.env`, whatever its audience.

## What Noust gives every application

`NOUST_URL`, `NOUST_DOMAIN`, `NOUST_BRANCH`, `NOUST_COMMIT` and `NOUST_RELEASE` are given to the
running application from a file beside each release (`.noust-env`), so going back to an earlier
release gives it that release's commit. The port is the unit's (`PORT`, and `HOST` for the Node
frameworks), and `{{app.PORT}}` and `{{app.HOST}}` name the same facts in a value.

A [preview](previews.md) is also told what it is: `NOUST_PREVIEW_OF` is the application it copies
and, when it is for a pull request, `NOUST_PR_NUMBER` is that pull request's number (a preview of
a branch or a tag has none). `{{app.PR_NUMBER}}` and `{{app.PREVIEW_OF}}` name them in a value, for
a database name or a banner that says which pull request this is.

The build gets them too, **except the commit and the release**: both change on every deploy, and a
build that depends on them could never be reused or compared with the last one. A build that needs
the commit asks for it by name: a variable whose value is `{{app.COMMIT}}` and whose audience is
`build`. The names are Noust's: a variable of your own cannot use them. The console lists them,
read-only, under the environment.

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

In the console: **Settings > Secret managers**, with a connection test.

## Applications in other places

- **Previews** are given only what their application's settings name, plus the set defined for
  previews alone ([previews.md](previews.md)).
- **A clone** keeps the references as written, so `{{app.URL}}` is the clone's own address
  ([teams.md](teams.md)).
- `noust app export` writes what the references resolved to, not the references: an import on another
  server does not need that server to have the scopes and providers they name.
