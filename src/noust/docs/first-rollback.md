# Ship a change, and roll it back

**Goal:** deploy a new version of your application, put the previous one back in seconds, and
have every push deploy by itself.

**Time:** five minutes.

**You need:** [a deployed application](first-application.md) and a commit pushed to the branch it
follows.

The examples use Kelmoor's application `wrenfield.io`, whose last commit is `a1c4f92`, "Rework the
homepage hero section".

## How it works

Each deploy builds a new release next to the one that serves. Noust starts it and asks it for an
answer, its health check. Only when it answers does traffic move to it; if it does not, the
release that was serving keeps serving and the deploy fails with the probes and the application's
log. The previous releases stay on disk, so going back rebuilds nothing.
[How Noust works](concepts.md#a-release) has the details.

## Steps

1. **Deploy the new commit.**

   ```bash
   noust update wrenfield.io
   ```

   Noust fetches the branch, builds the commit in a new release, and switches to it once it
   answers. In the console: the application's page, **Update**.

2. **See the releases.**

   ```bash
   noust releases list wrenfield.io
   ```

   The newest is first, and the one that serves has a `*`.

3. **Go back to the previous one.**

   ```bash
   noust releases rollback wrenfield.io
   ```

   Noust restarts the application on the release before the active one and keeps it only if it
   answers. It takes seconds: nothing is built. In the console: **Roll back**, on the application's
   page.

4. **Serve any other release on disk.** `noust releases rollback` takes a release's id, newer or
   older than the one that serves:

   ```bash
   noust releases rollback wrenfield.io 20261029-181502-a1c4f92
   ```

   When the fix is ready, push it and run `noust update wrenfield.io` again.

5. **Deploy on every push.** Your Git host has to reach the server, and the console listens only
   on `127.0.0.1`. Give webhooks a public name of their own, once per server: it forwards
   `/hooks/` to the console and answers nothing else. Point the name at the server first, as in
   [Put it on your domain](first-domain.md):

   ```bash
   noust web expose-hooks hooks.kelmoor.dev
   ```

   For a GitHub repository, then connect the GitHub App (**Settings > Integrations**). For any
   other Git host, create the application's webhook:

   ```bash
   noust app webhook rotate wrenfield.io
   ```

   It prints the secret, the payload URL, the events to enable and where to add them on the Git
   host. `noust app webhook show wrenfield.io` says later how far it is set up. In the console:
   the application's **Deploys and triggers** (Deploy on push). A push that arrives while a build runs waits
   its turn; several pushes become one update.

## What you should see

`noust releases list wrenfield.io` shows a table, newest first: each release's id (the time it was
built and its commit, `20261029-181502-a1c4f92`), the commit, its status (`active`, `superseded`,
`rolled_back`, `failed`, or `built` for one built and never served) and when it last served. The one
that serves is marked `*`.

`noust releases rollback wrenfield.io` ends with:

```text
Rolled back to release 20261022-093011-5e9d0b3 of wrenfield.io
The database was left as it is: going back puts code back, never data.
Release 20261029-181502-a1c4f92 stays on disk: noust releases rollback wrenfield.io 20261029-181502-a1c4f92
```

The application's **Deployments** tab in the console keeps every build's log, the failed ones
included.

## If it fails

- **The new release did not answer:** nothing changed for visitors. The error has the probes and
  the last lines of the application's log. Read them, fix the commit and deploy again.
  [A build or a deploy failed](troubleshooting-build.md).
- **The rollback is refused because a deploy changed the database schema:** a hook marked
  `migrates: true` ran since that release ([Give it a database](first-database.md)). The older code
  may not work with the newer schema, so Noust asks you to say it does:
  `noust releases rollback wrenfield.io --schema-changed-ok`. It still puts back only the code.
- **`noust releases list` says the application has no releases:** it is deployed in place (an
  older application, or one created with `--layout inplace`). `noust rollback wrenfield.io` restores its last backup instead, or move it to releases
  with `noust app migrate` ([Release, roll back and migrate](releases.md)).

## Next

[Add a service from the catalog](first-service.md).
