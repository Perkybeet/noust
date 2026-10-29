# The site answers 502, or does not answer

```bash
noust diagnose wrenfield.io
```

The verdict is `healthy`, `degraded` or `down`, and the line under it is the probable cause, in
Noust's words. Below it are the checks with their evidence: the journal lines, nginx's error log,
the probes. Find the cause in this page and apply the fix. In the console, the application's
**Diagnose** page shows the same.

A deploy that does not pass its health check never replaces what was serving. So a 502 right after
an update usually means the failure is older than the update, and going back may not fix it:
diagnose first.

## The application is not running

```text
No systemd unit found for wrenfield-io; it has never been started or its unit was removed.
```

Start it with `noust start wrenfield.io`. If its unit is gone, deploy it again with
`noust update wrenfield.io`, which writes the unit back.

## It ran out of memory

```text
Killed by the kernel for running out of memory (limit or machine).
```

Either its own memory limit or the whole machine ran out. `noust app limits wrenfield.io` shows its
limit; raise it with `noust app limits wrenfield.io --memory 1G --restart`, or give the machine room
with swap: `noust server swap create --size 2G`. On a small server, a build running next to the
application is the usual cause.

## It stops as soon as it starts

```text
The process exits with status 1 at start; see the last journal lines below.
```

The application starts, fails and systemd restarts it, again and again. The journal lines below the
verdict say why: a missing variable, a script that is not there (npm prints `Missing script:
"start"`), a port another program holds. Fix it and deploy again, or go back to the release that
worked: `noust releases rollback wrenfield.io`. `noust logs wrenfield.io -n 200` shows more of the
journal.

## systemd cannot start it

```text
The unit failed to start (Result=exit-code, exit status 203); see the last journal lines below.
```

systemd could not run the start command at all. Exit status 203 means the program does not exist
at that path. If you set the start command yourself, correct it:
`noust app build set wrenfield.io --start 'node server.js'` ([Build and start
commands](build.md)).

## It listens on another port

```text
The process is running but not listening on port 3000.
```

```text
Listening on 8080, Noust routes to 3000.
```

Noust gives the application its port in the `PORT` variable and sends nginx there. Make the
application listen on `PORT`, or deploy it again with the port it uses:
`noust create -d wrenfield.io -s https://github.com/kelmoor-labs/wrenfield-site.git --port 8080`.
Do not write `PORT` into its `.env`: the unit's own value wins, and the console and
`noust env configure` refuse it.

## nginx cannot reach it

```text
The app answers directly, but nginx returns 502 for wrenfield.io; nginx cannot reach the app.
```

The application is fine and nginx is not getting to it. Run `nginx -t`, and look at the site with
`noust site show wrenfield.io`. A site you edited by hand is an operator's site: Noust never
rewrites it, so check its `proxy_pass` yourself ([Operator sites](domains.md#operator-sites)).

## nginx points at the instance that does not serve

```text
nginx proxies wrenfield.io to 3001, but the blue instance listens on 3000.
```

The application runs as two instances, blue and green, and the site points at the idle one.
`noust update wrenfield.io` writes the site again and switches.

## The certificate expired

```text
The TLS certificate for wrenfield.io expired on 2026-10-12.
```

The site answers and browsers refuse it. See
[A certificate was not issued, or is about to expire](troubleshooting-certificate.md).

## The disk is full

```text
The disk holding the apps directory is 97% full.
```

Nothing can be written: not a log, a build or an upload. `noust server storage --analyze` shows
what takes the space. `noust server cleanup releases` prunes every application to its retention,
`noust server cleanup journal --size-mb 500` trims the journal, and `noust server cleanup
docker-build-cache` empties an old Docker build cache. Each takes `--plan` to say what it would
remove first.

## A worker of a Compose stack is down

```text
The worker's containers that are not running: ...; see their output below.
```

A stack that publishes no port is a worker, and is judged by its containers rather than by a port.
The message names each container that is down and why; their state and last output are under the
verdict. When none of them exists, it says so and gives the command that starts the stack.
[Docker Compose](compose.md) covers stacks.

## The verdict is healthy and the browser still fails

The problem is in front of the server: the name points elsewhere
([The name does not point at the server](troubleshooting-dns.md)), a firewall or a security group
closes ports 80 or 443, or a CDN in front of it fails.
