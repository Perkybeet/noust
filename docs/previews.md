# Previews

A preview is a short-lived copy of an application, at a name of its own, built from a pull request's
branch (or from a branch or a tag you name) and removed when the pull request closes or after a
while without a push. It is an ordinary application: it is created, updated and removed by the same
code every application goes through, with its own unit, site, certificate and, by default, its own
empty database.

A preview runs code nobody has reviewed. What it is given, and what it can reach, is therefore a
setting you choose, not something it inherits.

## Turning them on

```bash
noust preview enable shop.example.com --domain previews.example.com --max 3 --ttl 7d
```

- `*.previews.example.com` must be a DNS record that points at this server. A preview answers at
  `pr-<number>-<app>.previews.example.com`; one made by hand at `x-<n>-<app>.previews.example.com`.
- Pull request events must reach Noust: the deploy webhook with pull request events ticked, or this
  server's GitHub App on the repository.
- Only people trusted with the repository get a preview. A pull request from a fork never does
  (unless an operator makes it by hand and says so), on GitHub its author must be an owner, member or
  collaborator, and a bot's (Dependabot, Renovate) only with `--allow-bots`.
- A preview builds in the build sandbox, as the unprivileged `noust-build` account ([security.md](security.md)).

In the console: the application's **Settings > Previews**.

## What a preview is given

**Variables.** By default only the ones you name, then the set defined for previews alone. Nothing
else of the application's, production keys included, reaches code anyone who can open a pull request
wrote.

```bash
noust preview enable shop.example.com --env-mode allowlist --allow-env NEXT_PUBLIC_API_URL --allow-env SENTRY_DSN
noust preview vars set shop.example.com STRIPE_KEY sk_test_... --secret
noust preview vars set shop.example.com NEXTAUTH_URL '{{app.URL}}'
```

A variable defined for previews wins over the application's of the same name. A value made of
references ([variables.md](variables.md)) is copied as the references, so `{{app.URL}}` is the
preview's own address and not production's. `--env-mode all` is how previews were made before 3.4
(every variable but `--exclude-env`); the settings page says so for as long as it is in force.

**Data.** By default a preview gets an **empty database of its own** when the application has one,
made for it before its first build and dropped with it. It is given none of the application's
connection variables (`DATABASE_URL` and the `DB_*` family), so a pull request cannot write into
production's data. `--data shared` is the 2.2 way: the application's own databases, which a pull
request can then change. Copying the application's data into a preview is not offered: the schema a
branch migrates to is not the production one.

## Making one by hand

```bash
noust preview pulls shop.example.com                       # the open pull requests
noust preview create shop.example.com --pr 42
noust preview create shop.example.com --branch feature/login
noust preview create shop.example.com --tag v1.4.0
```

Reading the open pull requests, and building one by number, needs the GitHub App on the repository;
a branch or a tag needs nothing but the repository. A pull request from a fork is refused unless you
pass `--allow-fork`. In the console: **New preview** over the list.

## One preview at a time

A preview is addressed by its own domain, which `noust preview list` prints:

```bash
P=pr-42-shop-example-com.previews.example.com
noust preview list shop.example.com
noust preview logs $P            # the service's journal; --build for the last build's output
noust preview redeploy $P        # build it again from its branch as it is now
noust preview stop $P            # the service stops; files, certificate and database stay
noust preview start $P
noust preview remove $P
```

A stopped preview still counts toward the limit and still expires. Removing a preview takes down its
application, certificate and record, drops the database made for it and, for a Docker Compose
application, its volumes.

## Docker Compose applications

A Docker Compose application has previews too, as a stack of its own. The repository is checked out in
a directory of its own, and Compose derives the project from that directory, so the preview's
containers, default network and named volumes are apart from the original's. The compose file the
repository holds can still say things two copies of one stack cannot both have, so the preview runs
from a copy written beside it (`<name>.noust-preview.yml`) at every deploy and update, and the
original is never touched:

| In the repository's file | In the preview's copy |
|---|---|
| `name:` | Dropped: the directory names the project |
| `container_name:` | Dropped |
| Published ports | Only the web root service, on `127.0.0.1` and the port the preview was given; every other port is not published (the services still reach each other over the stack's network) |
| A host path mounted into a container (`/srv/shop/uploads:/data`) | Redirected under `.noust-preview-data/` in the preview's directory, so a pull request cannot write into production's files. A bind of an existing **file** (a key, a configuration) is read-only input and is left alone |
| `image:` beside `build:` | The image tag is dropped, so two builds never move one another's tag |
| `external: true` or a fixed `name:` on a volume or network | The project's own: an empty volume instead of production's |

A stack that uses the host's network (`network_mode: host`) cannot run beside its original, and one
that publishes no web port has nothing to open: both are refused with the reason. The host privilege
guard ([compose.md](compose.md)) runs on the repository's file, so a stack that asks for root is
refused for a preview unless an exception is recorded for it. The routes of a `noust.nginx.yaml`
name the original's ports and are not used: a preview proxies `/` to the web root. A preview
runs the stack's default profiles, and takes no backup of its databases before every push.

A monorepo has no previews yet: each of its workspaces takes a port, a unit and a site of its own,
which previews do not isolate.

## Cleaning up

A preview is removed when its pull request closes or merges, when its time-to-live runs out without a
push (`noust preview sweep` runs hourly from a timer), and when previews are turned off. A removal
that fails for one piece (a database that would not drop, a certificate) says so and removes the rest.
