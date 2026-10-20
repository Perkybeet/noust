# A server written down: the manifest

A manifest is a YAML file that says which projects and applications a server should hold, where
each one lives, its variables, its domains, its limits, its health check, its databases and its backup
schedule, the cron jobs it runs and, on a central, the servers of the fleet: their labels and team, where
an application is placed across them and which server an application or a cron job lives on. It lives
in git. **`plan`** says what applying it would change; **`apply`** makes the server
match it; **`export`** writes down a server that already runs. Applying is idempotent: apply it twice
and the second time finds nothing to do.

```
noust manifest validate noust-manifest.yaml                       # anywhere: no server needed
noust --context prod manifest plan noust-manifest.yaml            # what would change
noust --context prod manifest apply noust-manifest.yaml --yes     # make it so
noust --context prod manifest export > noust-manifest.yaml        # write a server down
```

It is a client of the server's API ([remote.md](remote.md)): every read and every change is a request
made as the token that applies the manifest, so the role, the team scope, sudo mode, approvals and the
audit trail are the server's, and a token that cannot deploy cannot apply a manifest that deploys.
Nothing is re-implemented here.

## The file

```yaml
version: 1                       # the only version this release reads
name: Acme production            # for people

projects:                        # projects and environments that should exist
  - team: acme
    project: web
    name: Web                    # when it has to be created
    environments: [staging, production]

applications:
  - domain: shop.example.com
    source: https://github.com/acme/shop
    branch: main
    team: acme
    project: web
    environment: production
    env:                         # merged into what the application has; others stay
      NODE_ENV: production
      DATABASE_PASSWORD: { from_env: SHOP_DB_PASSWORD }     # read where the manifest is applied from
      API_KEY: { from_file: /run/secrets/shop-api-key }
    domains: [www.shop.example.com]
    limits: { memory_max_mb: 1024, cpu_quota_percent: 200, tasks_max: 512 }
    health: { path: /healthz, expect: [200], timeout: 60 }
    databases:
      - engine: postgresql
    backups: { schedule: daily, retention_count: 7, retention_days: 30, include_databases: true }
```

```yaml
cron:                            # the jobs this server runs
  - name: nightly-report
    command: /usr/bin/python3 /opt/report.py --all
    schedule: daily              # hourly, daily, weekly, monthly or a systemd calendar
    user: www-data               # what the manifest leaves out stays as it is
    working_directory: /opt
    app: shop.example.com
    timeout: 600
    enabled: true
    # server: web-2              # run it on that server of the fleet instead

servers:                         # on a central: servers it already manages
  - name: web-2
    labels: { role: web, region: eu }   # merged into the labels it has
    team: acme                          # the team that has it (docs/teams.md)

placements:                      # where an application runs across the fleet
  - domain: shop.example.com
    servers: [web-1, web-2]
    serial: 1                    # a number of servers, or "25%"
    canary: web-1
    max_failures: 0
    rollback_on_failure: true

applications:
  - domain: api.example.com
    server: web-2                # lives on that server, reached through this central
    source: https://github.com/acme/api
```

Only `domain` is required of an application. `source` or `recipe` (one of them, to create it), `type`,
`branch`, `port`, `ssl`, `team`, `project` and `environment` say where it comes from and where it
lives. The schema is **closed**: a key it does not know is an error that names the path
(`applications[0].limits.memory_mb: unknown key`), never ignored, because a limit that was typed wrong
and silently dropped is a limit that was never set. A document over 1 MiB, more than 200 applications
or the same domain twice is refused.

## Cron and the fleet

**Cron jobs.** A job is created, or rewritten when its command, schedule, user, directory, application,
timeout or service differ from what the manifest says; what the manifest leaves out of a job it already
has stays as it is. A command is compared as the words it splits into (`a  b` is `a b`) and a schedule as
the calendar the server normalises it to (`daily` is `*-*-* 00:00:00`), so a plan converges. A plan names
what differs and **never prints a command**, which may hold a secret; `noust manifest export` writes them
as they are, and its header says to read them before committing. Writing a job is root-equivalent, so it
needs sudo mode; switching one on or off does not. Jobs the manifest does not name are listed as
`cron:NAME` and left alone.

**The fleet.** A central's manifest can also set what its servers carry and where applications run:
`servers` merges labels and assigns the team that has a server; `placements` says the servers an
application is named on, the selector that adds more, and how an update goes out. Registering a server
needs the join code its own `noust fleet authorize` printed, which does not belong in a document in git, so
a manifest only configures servers that are already managed (`noust node add`) and warns about one that is
not. On a server that is not a central, or with a token that may not see the fleet, these sections are a
warning and no change, so one manifest can be applied to either.

**An application on another server.** `server: web-2` makes the same calls the console would, one level
down: through the central, at `/api/nodes/web-2/api/...`, as the same token, with the node's own sudo mode
and permissions. Its project, team, variables, domains, databases and backups are planned and applied
there, a deployment is followed there, and the plan shows `web-2:api.example.com`. A server the central
does not manage is a warning, and what the manifest puts on it waits. `projects` are those of the server
the manifest is applied to.

## Plan

`plan` reads the server as the token, compares, and prints one line per change, in the order they would
run: the fleet's servers, projects, environments, applications, moving, branch, limits, health check,
domains, databases, variables, backups, cron jobs and placements. It changes nothing and never prints the
value of a variable or a cron command.

```
$ noust --context prod manifest plan noust-manifest.yaml
 Change     What                                                 Needs
 deploy     create shop.example.com from https://... (main)
 variables  set 3 variables (names only)                         sudo mode
 backups    daily, keep 7 for 30 days
Not in the manifest, and left as they are: blog.example.com
```

* **Differences that are not reconciled** (an application of another type than the manifest says, a
  source that changed) are warnings, not changes: a manifest does not rewrite what an application is.
* **Applications the manifest does not name are reported and left alone.** Applying never deletes
  anything: not an application, not a variable, not a domain.
* `unverified` marks a comparison the token could not make because reading the variables needs sudo
  mode; the plan then assumes they must be set.
* `--json` prints the plan for a program; **`--check` exits 2 when there is something to apply**
  (0 when there is not), for a pipeline that fails a pull request when the repository and the server
  have drifted.

## Apply

`apply` shows the plan, asks (`--yes` in a pipeline), and makes each change with the API call the
console would make. A change that fails **stops the run**: fix the cause and apply again, the plan then
holds only what is left. It waits for the deployments it queues and for their health gate
(`--no-wait` does not; `--timeout` bounds it).

* Variables are merged with `PATCH /api/apps/{domain}/env` (sudo mode): the names in the manifest are
  set, the others stay. Applications whose variables changed are listed; `--restart` restarts them so
  they use the new values.
* Exit code: **0 when everything is in place, 1 when a change failed** (the server's own words are
  printed). A deployment still running when the wait ran out counts as failed too, and says how to wait
  for it (`noust jobs wait JOB`) before applying again.
* `--json` prints the outcome of every change.

## Secrets

A manifest in git cannot hold a secret. A variable's value is a plain value, or **`{from_env: NAME}`**
(a variable of the machine that applies the manifest, such as a pipeline's secret) or
**`{from_file: PATH}`** (a file only the operator can read; a relative path is read from the manifest's
own folder). They are resolved **where the manifest is applied from**, never on the server:

* `plan` and `apply` read them on the machine you run the command on; the value goes to the server over
  TLS to set the variable and is not printed, logged or kept in the plan.
* The console's preview refuses them: a server does not read its own environment on behalf of a
  document it was sent.
* A variable that is missing where it is applied from is an error that names it, before anything is
  changed.

## From the console

Settings > Manifest takes a pasted manifest and shows what applying it would change on this server, in
order, with what needs sudo mode and what could not be checked. **Apply these changes** then queues the
apply as a job and follows it where you are: it waits for the deployments it queues, because what comes
after an application's creation (its domains, its databases, its backups) needs the application to exist.

* **It is you who applies.** The job calls this server's own API with the credentials you used to click:
  every change is a request to the route a person would have used, checked against your role, your team,
  sudo mode and the approvals, and audited as you (`manifest.apply` says who asked and how many changes,
  never a value). A change you may not make is refused where it happens and the run stops there, with
  the server's own words; the plan then holds only what is left.
* **It applies what you read.** The preview's digest travels with the apply: if the server or the
  manifest changed in between, nothing is applied and you are asked to preview again
  (`409 plan_changed`).
* **Sudo mode first**, because a manifest is several root-equivalent changes in one. A token applies
  from the console's API only when it is allowed to confirm sudo mode; from a pipeline, the command
  line (below) is the way and is a token's own.
* **Values are written in the manifest.** A variable read from the environment or from a file
  (`from_env`, `from_file`) is read where the manifest is applied from, and a server does not read its
  own for a document it was sent: a manifest that has them is refused here and applied from the machine
  that has them.
* One manifest is applied at a time (`409 apply_running`), and a job in the middle cannot be cancelled:
  every change before the failure stays, every change after it is left. Apply again.

The same thing over the API: `POST /api/manifest/plan` (`{"manifest": "<yaml or json>"}`, `apps.read`)
previews as the caller, and `POST /api/manifest/apply` (`{"manifest", "expect": "<digest>", "restart"}`,
`apps.manage` and sudo mode) answers `202` with the job to follow (`GET /api/jobs/{id}`), or `200` with
`queued: false` when the server already matches. The preview reads the server as the caller, so a team
sees only what it can see.

## In a pipeline

```yaml
# .github/workflows/deploy.yml
- run: noust --remote "$NOUST_URL" --token env:NOUST_TOKEN manifest plan noust-manifest.yaml --check
- run: noust --remote "$NOUST_URL" --token env:NOUST_TOKEN manifest apply noust-manifest.yaml --yes
```

The exit codes are the contract (see [remote.md](remote.md#exit-codes)): 0 done, 1 failed, 2 changes
waiting (`plan --check`).

## What it does not do

* It does not delete. Taking an application out of the manifest leaves it running; delete it on purpose
  (`noust delete`). The same goes for a cron job, a label and a placement.
* It does not register servers (their join code is a secret), create teams or run a node's own manifest.
* It does not manage accounts, roles or tokens, certificates' issuers, firewall rules or the server's own
  settings. Those have commands and routes of their own.
* It does not read a manifest from an application's repository. `noust.yaml` there is the project's
  (hooks, backups of its databases) and is untrusted input; a manifest is the operator's.
