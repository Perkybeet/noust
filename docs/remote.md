# Operating a server from elsewhere

The command line normally runs on the server it manages, as root. With a **context** it also runs from a
laptop or a pipeline: the same commands drive a server through its API, with a token, no SSH and no root
on the target. Nothing is re-implemented on the way: the command makes the request the console would
make, so the token's role, its team scope, sudo mode, approvals and the server's audit trail apply as
they do to anyone else. A command the server refuses prints the server's own words.

```
noust context add prod --url https://noust.acme.test --token - < token.txt   # keep where the token is, not the token
noust context use prod
noust list                                     # now runs on prod
noust update shop.acme.test                    # queues the update, follows it, exits 0 only if it went live
noust --local list                             # this machine's own, once
noust --remote https://noust.acme.test --token env:NOUST_TOKEN list      # one call, nothing saved
```

## Contexts

A context is a name, an address and **where to find a token**: a private file or a variable. It is
never the token itself.

| Command | What it does |
|---|---|
| `noust context add NAME --url URL --token -\|@FILE\|env:NAME` | Saves a context. `--token -` reads standard input into a private file (mode 0600) of its own; `--token-file FILE` and `--token-env VARIABLE` point at what already holds it; `--ca-file FILE` trusts a private authority; `--use` also makes it current |
| `noust context list` / `current` | The saved ones, and the one in use |
| `noust context use NAME` | The server commands run on from now on |
| `noust context verify [NAME]` | Calls the server and says who the token is there |
| `noust context remove NAME` | Forgets it, and the token file this tool kept for it |

Contexts live in `~/.config/noust/contexts.json` (`$XDG_CONFIG_HOME/noust` when set; mode 0600), and a token
read from standard input in a private `tokens/` folder beside it (0700). `--context NAME` (or `NOUST_CONTEXT`)
chooses one for a single command; `--remote URL` with `--token`, `--token-file` or `--token-env` does
without saving anything (`NOUST_REMOTE`, `NOUST_TOKEN` and `NOUST_TOKEN_FILE` are the same things for a
pipeline); `--local` runs on this machine whatever is in use.

## The token never goes in an argument

Everything on a command line is visible to every user of the machine in `ps`, and stays in the shell's
history. So `--token` takes **where the token is**, never the token:

* `--token -` reads standard input; `--token @FILE` reads a file; `--token env:NAME` reads a variable.
  A literal token is refused (`refused_locally`) with the way to do it.
* A token file must be private to its owner (mode 0600) or it is refused; the token is never printed,
  never written to the audit log of this machine and never part of an error message.
* The server's address must be HTTPS; plain `http://` is accepted only for this machine, such as through
  an SSH tunnel to `http://localhost:8080`. The certificate is verified; `--ca-file` adds an authority. A
  redirect is **not followed**: it would send the token somewhere nobody chose.

## What runs remotely

`list`, `status`, `update`, `rollback`, `logs`, `diagnose`, `releases`, `jobs`, `backup`, `env list`,
`team list`, `project list`, `environment list`, `health`, `whoami`, `api` and `manifest` have a form on
another server. A command with no remote form says so and offers `--local`.

* **`noust update`** queues the update and follows its log until the health gate decides. Exit 0 means
  the new release serves; a refused or failed update prints the probes and the journal as the server
  wrote them and exits 1. `--no-wait` only queues it.
* **A group**: `noust update --project web --env production`, `--team acme`, or several domains, queues
  every application in one call (`POST /api/jobs/update-many`); each goes through its own health gate,
  and a failure does not stop the others. `--serial` updates them one after another and stops at the
  first failure (`--keep-going` carries on).
* **`noust env list DOMAIN`** lists the names of an application's variables; their values are never
  printed, whatever the token may reveal. Variables are set with a manifest ([manifest.md](manifest.md)).
* **`noust api METHOD PATH`** calls any route and prints the JSON: `noust api POST /jobs/update -d
  '{"domain": "shop.acme.test"}'`. Settings > API reference lists them all.
* **`noust manifest plan|apply|export`** ([manifest.md](manifest.md)) writes a server down and makes it
  match.

## Exit codes

Pipelines depend on these:

| Code | Meaning |
|---|---|
| 0 | It worked: the update went live, the manifest is in place, the plan found nothing |
| 1 | It failed or was refused: the server's reason is printed. Also a job that failed, was cancelled, or was still running when `--timeout` ran out in `manifest apply` |
| 2 | A usage error, or `noust manifest plan --check` found something to apply |
| 4 | The job is still running on the server when the wait ran out (`update`, `rollback`, `backup`): the server keeps going; follow it with `noust jobs wait ID` |
| 130 | Interrupted |

`--json` prints the job or the answer as one JSON document for a program, and nothing else on standard
output.

## Safe by default

* A `429` is waited out (the `Retry-After` the server gave, at most 30 seconds) and tried again, because
  the server refused it before running it. A `5xx` is retried for reads only; a change that may have run
  is never repeated, because repeating it is not free.
* A job that is queued is a job on the server: closing the terminal does not stop it, and `noust jobs`
  shows it again.
* Nothing here widens a token: a token that cannot deploy cannot `update`, and one bound to a project
  sees that project only ([teams.md](teams.md#roles-where-they-are-needed)).
* Several servers: give each a context and choose with `--context`. A **central** drives its nodes
  itself (`docs/CENTRAL.md`); a context is for a machine that is not one.

## In CI

A pipeline needs three things: the address, a token with only what the step needs (made with
`noust token create ci-deploy --scope deploy --expires-hours 720 --owner NAME`, or for a project
`--team acme --project web`), and the exit code.

```yaml
- run: noust --remote "$NOUST_URL" --token env:NOUST_TOKEN update shop.acme.test
  env:
    NOUST_URL: https://noust.acme.test
    NOUST_TOKEN: ${{ secrets.NOUST_TOKEN }}
```
