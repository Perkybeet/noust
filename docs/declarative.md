# Declare an application

One file says how an application is run: its build, its hooks, its health check and limits, how
its repository is fetched, the non-secret variables, the extra names it answers on and its
scheduled tasks. It can be written by hand, or written from an application that is running, and
applied to a server. It is the keys `noust.yaml` already has, plus the ones that are the
server's to set.

- [Write one from an application](#write-one-from-an-application)
- [Apply one](#apply-one)
- [The keys](#the-keys)
- [What a repository can and cannot say](#what-a-repository-can-and-cannot-say)
- [The API](#the-api)

## Write one from an application

```bash
noust app export shop.example.com --as-yaml -o noust.yaml
```

Only what differs from the defaults is written, so a plain application gives `{}`. The header
says how to apply it. Secret variables (a name or a value that looks like a secret) are never
written: their names are listed in a comment, and `--with-secrets` is refused with `--as-yaml`.
The `build` and `hooks` blocks are the operator's own (the ones set with `noust app build set` and
`noust app hooks set`), because those are what an application has beyond its repository.

## Apply one

```bash
noust app apply shop.example.com noust.yaml --check     # what would change
noust app apply shop.example.com noust.yaml             # asks before anything that runs commands
noust app apply shop.example.com noust.yaml --yes       # a script: no question
noust app apply shop.example.com noust.yaml --prune     # also remove names and tasks it does not list
```

- Only the **sections the file has** are touched, and only the parts of them that differ. A key
  left out is left alone, not reset.
- **Variables are set, never removed**, and a secret in the file is refused naming it: set it on
  the server with `noust env configure`. Changing a variable does not restart the application.
- **Domains and tasks are added, not removed**, unless `--prune`, which removes the aliases,
  redirects and tasks of the application that the file does not list. A certificate is never
  revoked. A cron job with the name of one that is not this application's is refused.
- **What runs commands is listed and confirmed first**: the build block, hooks, tasks and Compose
  arguments run as the application or on the machine. Without a terminal `--yes` is required.
- A part that is refused (a domain another application has, a limit the unit cannot run under)
  is reported and the rest are applied.
- The port, and the layout, type and source, are chosen when an application is created: a file
  that names another port gets a note, never a change.

The console does both from an application's Export settings: **Download the declaration** writes
the file, and **Apply a declaration** checks a pasted one first (showing what it would change and
flagging what runs commands) before it applies it.

### Reviewing a set of changes

The dialog is where a set of changes is read before it is made. **Start from what runs now** fills it
with the declaration the server would export, so a change is an edit of a line rather than a file
written from memory. Once a draft is checked, the plan says what each change does and, under it, every
block of the file that differs from what runs is shown **line by line** (a line removed and a line
added are marked with a sign and a word as well as a ground), each with **Put the block back**: that
block returns to what runs now, the others stay as drafted, and the check is made again, so the plan
and the agreement to run commands are always those of the text that is left. A block the draft does
not have is not a change, and a block that differs only in comments or blank lines is not one either.

What the set covers is said in the dialog and is the table above: the health check, limits, release
retention, source options, Compose settings, the build block, deploy hooks, the variables it names,
aliases and redirects, and scheduled tasks. Each part is applied **on its own and in that order**, not
as one transaction: a limit is applied through the health check and is put back if the application
does not answer, a variable is written now and reaches the application at its next restart, a build
block or hooks run at the next deploy, and a part that cannot be applied leaves the ones before it in
place and is named in the result. There is no single step that undoes the whole set; a site's
configuration, a database or anything outside this list is changed on its own page.

## The keys

```yaml
app:
  health: {path: /up, expect: 200-399, timeout: 30}     # what every activation asks of the app
  limits: {memory_max_mb: 512, cpu_quota_percent: 100, tasks_max: 512}
  keep_releases: 5                                     # releases layout only
  port: 3000                                           # checked, never changed
source:                                                # docs/sources.md
  submodules: true
  lfs: auto                                            # auto | on | off
  shallow: true
  watch_paths: ["apps/web/**", "!**/*.md"]
  skip_markers: true
compose:                                               # Docker Compose stacks, docs/compose.md
  file: deploy/compose.yml
  profiles: [web]
build:                                                 # docs/build.md
  install: pnpm install --frozen-lockfile
  build: pnpm build
  start: node dist/server.js
hooks:                                                 # docs/compose.md
  pre_deploy:
    - run: pnpm migrate
      migrates: true
env:                                                   # non-secret only
  LOG_LEVEL: info
domains:
  aliases: [www.example.com]
  redirects: [old.example.com]
tasks:
  - {name: nightly, schedule: daily, command: ./bin/cleanup}
  - {name: weekly, schedule: "Mon *-*-* 03:00:00", command: ./bin/report, user: www-data, enabled: false}
```

The schema is closed: an unknown key, in the file or in a block, is refused naming the field
(`tasks[0].colour`), never ignored. A command is an argument vector, never a shell line. `backup`
and `processes` are accepted (they are the repository's own keys, read from the code) and apply
leaves them alone.

## What a repository can and cannot say

The same file can be the repository's `noust.yaml`. A deployment reads `build`, `hooks`, `backup` and
`processes` from it, as it always did (the operator's own block of each wins whole). It accepts
the other blocks, so one file can serve both, and **applies none of them**: a repository that
could set its own domains, limits, variables or cron jobs would be setting the server's. Only
someone who runs `noust app apply`, or calls the API with sudo mode and the root-equivalent
permission, does.

Applying a repository's `noust.yaml` makes its `build` and `hooks` the operator's, which then win over
whatever the repository says later. Apply the file once, from the server, when that is what you
want.

## The API

| | |
|---|---|
| `GET /api/apps/{domain}/declaration` | The file for an application, as `{domain, declaration}`. Needs the permission that reveals secrets and admin scope, like the export. |
| `POST /api/apps/{domain}/apply` | `{declaration, check, confirm, prune}`: the plan, and the steps when it was applied. Root-equivalent, needs sudo mode. A plan that runs commands is refused without `confirm: true`. |

Every apply is audited as `apps.apply`, naming the sections and what was and was not applied; each
part is also audited by the manager that owns it (`apps.build`, `apps.source_options`, `apps.compose`...).
