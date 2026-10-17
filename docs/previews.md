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
- Only people trusted with the repository get a preview. A pull request from a fork gets none by
  default (unless an operator makes it by hand and says so, or asks for [approvals](#forks-and-people-you-do-not-know)),
  on GitHub its author must be an owner, member or collaborator, and a bot's (Dependabot, Renovate)
  only with `--allow-bots`.
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
connection variables (`DATABASE_URL` and the `DB_*` family), so a pull production's data. `--data shared` is the 2.2 way: the application's own databases, which a pull
request can then change.

`--data clone` gives each preview a database of its own that **starts as a copy of the
application's**. The copy is made before the preview's first build, so the branch's migrations (its
`pre_deploy` hooks) run over real data, which is the point; it is a dump of the application's
database loaded through the same import every upload goes through, and the dump is deleted as soon as
it is loaded. A preview's code then reads a copy of production's data, personal data included, so
turn it on for an application whose pull requests you trust (or that holds nothing personal); the
settings page says so. A copy is made once, when the preview is made: a later push does not copy
again. It needs a database Noust has recorded for the application.

## What a preview is called

By default `pr-<number>-<app>.previews.example.com` (`x-<n>-...` for one made by hand). An application
can choose another name with a template, using the placeholders Coolify has:

```bash
noust preview enable shop.example.com --domain-template '{{pr_id}}.{{domain}}'
noust preview enable shop.example.com --domain-template 'pr-{{pr_id}}-{{random}}.{{base}}'
```

| Placeholder | Is |
|---|---|
| `{{pr_id}}` | The pull request's number (`x1`, `x2`... for a preview made by hand) |
| `{{random}}` | Eight letters and digits, made when the preview is made |
| `{{domain}}` | The application's domain (`shop.example.com`) |
| `{{name}}` | The application's name, its domain with dashes (`shop-example-com`) |
| `{{base}}` | The base domain previews answer under |

A template must hold `{{pr_id}}` or `{{random}}` (two previews must never share a name) and give a
domain whose name, with dots as dashes, is at most 64 characters. `{{pr_id}}.{{domain}}` needs a
wildcard record under the application's own domain. A template applies to the previews made from
then on: one that exists keeps its name. An empty template (`--domain-template ''`) goes back to the
default.

A preview tells search engines to stay out (`X-Robots-Tag: noindex, nofollow`), so a throwaway copy
never becomes a duplicate of the site that outlives it. `--index` turns that off for an application.

A preview is told which pull request it is for: `NOUST_PR_NUMBER` (none for a preview of a branch or
a tag) and `NOUST_PREVIEW_OF` (the application it copies), also `{{app.PR_NUMBER}}` and
`{{app.PREVIEW_OF}}` in a value ([variables.md](variables.md)).

## Forks and people you do not know

By default a pull request from a fork, or on GitHub from someone who is not an owner, member or
collaborator, gets no preview: its code would run on your server. With approvals the preview waits
instead:

```bash
noust preview enable shop.example.com --forks approve
noust preview list shop.example.com          # status "awaiting"
noust preview approve pr-9-shop-example-com.previews.example.com
noust preview reject pr-9-shop-example-com.previews.example.com
```

A pull request that waits is recorded with its commit, the pull request is told that an operator has
to approve it, and **nothing of it is built or run**. Approving builds that commit; a push of
another commit waits again, because the code is new, while the commit that was approved arriving
again (an edit of the title, a redelivered event) is built. A pull request that waits counts toward
`--max` and expires like any preview. A fork's commit lives only in the fork, so it is built by its
id over the application's own branch (code that already runs in production). Bots are not approved
commit by commit: `--allow-bots` is their switch. In the console, the preview's row has **Approve**
and **Reject**; approving asks for sudo mode, as making a preview by hand does.

## Hooks of a preview's life

`noust.yaml` can name two more moments beside `pre_deploy` and `post_deploy`:

```yaml
hooks:
  preview_post_create:        # once the preview's first deployment serves: seed it
    - run: ./scripts/seed-demo-data.sh
      timeout: 300
  preview_pre_destroy:        # before it is removed: clean up what it made elsewhere
    - run: ./scripts/delete-demo-tenant.sh
```

They run in the preview, as its own account, in its sandbox and with its environment (a Compose
stack's, in a container of the service they name with `service:`), exactly as `noust app exec` runs
a command, and every run is on record. They stop at the first hook that fails, and a failure is a
warning in the job's log: a preview that is seeded badly is still a preview, and one whose clean-up
fails is still removed. They have no `workdir` (commit a script that changes directory) and no
`migrates`. The preview's own hooks are the ones its code declares; the operator's document for the
preview application, when there is one, replaces them whole, as for any application.

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

## Monorepos

A monorepo deployed as one application per workspace ([workspaces.md](workspaces.md)) has previews
for each application. Two things make the pull request, not the repository, the unit:

- **Only the pull requests that change an application's directory build its preview.** The
  application's watch paths (`noust app source show`) are weighed against the files of the whole
  pull request (read from GitHub through the App), so a change to `apps/api` builds the API's preview
  and not the web application's, and a change to a shared package builds both if both watch it.
  Where the files cannot be read (a code host other than GitHub, no App on the repository) the pull
  request is built, as it always was.
- **A preview can point at its sibling's preview of the same pull request.** A value
  `{{peer:api.example.com/URL}}` is `https://api.example.com` in production and, in the preview of
  pull request 7, the API's preview of pull request 7 when the API has previews on the same
  repository (`{{peer:api.example.com/DOMAIN}}` is the bare domain). If the API has no such preview
  the value is production's address. A name that depends on `{{random}}` cannot be known before the
  API's preview exists, and is an error that says so.

The old `monorepo` type, which runs one unit and one site per workspace in place, has no previews:
each of its workspaces takes a port, a unit and a site of its own, which previews do not isolate.
Deploy its workspaces as applications instead (`noust workspace create`).

## Cleaning up

A preview is removed when its pull request closes or merges, when its time-to-live runs out without a
push (`noust preview sweep` runs hourly from a timer), and when previews are turned off. A removal
that fails for one piece (a database that would not drop, a certificate) says so and removes the rest.
