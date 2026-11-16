# Builds in the sandbox, and the account an application runs as

Part of [Releases](releases.md).

## Builds in the sandbox

Installing dependencies and building run code from the repository and from every dependency's
install scripts. From 3.1 that happens in a sandbox rather than as root:

```bash
noust app sandbox self-test                 # does the sandbox hold on this server?
noust app sandbox status shop.example.com   # sandbox, or root and why
noust app sandbox test shop.example.com     # build what the next update builds, in the sandbox; nothing is activated
noust app sandbox enable shop.example.com   # sandboxed from the next deploy on
noust app sandbox enable shop.example.com --network strict
noust app sandbox disable shop.example.com --reason "..."   # build as root: recorded, audited
```

- **Who builds in it**: applications and previews created from 3.1 on, and every application
  where it was enabled. An application deployed before 3.1 still building as root is tried in
  the sandbox by Noust itself, once, on its **next update** (3.2): after the source is in place
  and before the build, the update builds what it is about to build in a scratch copy, as
  `noust app sandbox test` does. If that passes, the sandbox is turned on and the update builds
  in it; if it fails, the update builds as root exactly as before, the failure is recorded and
  warned about (the application's page, `noust health`, `noust ens check`) and sent as a
  `deploy_failed` notification with the build's output, and Noust does not try again by itself:
  fix the build and run `noust app sandbox test`, then `enable` (`--force` skips the test).
  Nothing is tried, and nothing changes, for an application whose operator recorded a decision
  to build as root (`noust app sandbox disable --reason`), for a type with nothing to build
  (static) or that builds in Docker (Compose), or on a server where the sandbox does not hold
  (the self-test fails: containers, WSL without mount namespaces).
- **What the build sees**: its release, read-write; its own cache under
  `/var/cache/noust/build/<app>`; the application's `.env`, handed in by systemd, which
  `noust-build` itself cannot open; a clean environment; no `/root`, no `/etc/noust`, no store,
  no other application's files. Memory and CPU are limited.
- **Installs get devDependencies**, as a root build's did: a root install never read `.env`, so
  a `NODE_ENV=production` there (or npm's and yarn's own production settings) is removed from
  the install's environment, and `npm ci`, `pnpm install` and `yarn install` keep the
  devDependencies the build needs. The build itself gets the whole `.env`.
- **`--network strict`**: dependencies install with the network but without the application's
  variables, and the build runs with them but without a network. A build that fails for want of
  the network says so and how to allow it. A preview follows its application's profile and
  never gets production secrets.
- **`--pty`** runs the build on a terminal, for build scripts that reopen `/dev/stderr`.
- **In place and monorepo**: the build runs in the live tree as the service account, still with
  the rest of the machine hidden. `noust app migrate` is the way to full separation. Their
  `test` builds a copy of the tracked files as they are in that tree, uncommitted changes
  included, and lists those changes so they can be committed.
- **Docker Compose** builds run inside the Docker daemon. A new stack with `privileged: true` or
  the Docker socket mounted is refused unless `noust app sandbox compose-exception DOMAIN`
  records why it needs them.
- **Fail closed**: where the self-test fails (containers and WSL without mount namespaces), a
  build stops with the evidence and never falls back to root; `disable` with a reason is the
  explicit way to build that application as root.
- A build killed for its time or memory limit says which; cancelling a deploy stops its build
  unit.

## The account an application runs as

Until 3.1 every application ran as the one configured service account (`www-data`), so a
compromised application could read and write every other application's tree. From 3.2 an
application created by Noust runs as an account of its own, `noust-app-<name>` (a long name is
shortened and given a hash, to stay within 32 characters): a system account with its own group,
no home and no login shell, which owns the application's tree, its `.env` and its caches. The
unit says `User=` that account; a PHP application's FPM pool runs as it, one pool per account.
The web server still reads what it serves: a deployed tree is world-readable except its `.env`
files. Deleting the application with its files removes its account too.

```bash
noust app identity status shop.example.com    # the account it runs as, and whether it is its own
noust app identity migrate shop.example.com   # move an existing application onto its own account
```

- **Applications from before 3.2 keep the shared account** through every redeploy until you
  move them. `migrate` creates the account, hands it the files the shared account owned (tree,
  `.env`, build cache), rewrites the unit or the pool and restarts the application behind the
  health gate. If it does not answer, the owners of the files it changed, the unit or pool and
  the records are put back exactly and the application is restarted as it was. Over the API
  it is `POST /api/apps/{domain}/identity/migrate`, which is root-equivalent, needs sudo mode
  and is subject to four-eyes approval when approvals apply.
- **Where it writes is read first** (since 4.1). The health gate only proves that the application
  answers, and an application that writes outside its own folder into one the shared account owns
  (an upload directory the web server also serves) would pass it and fail at the first file it
  wrote, days later. So the absolute paths in its `.env` and in its unit (`Environment=`,
  `ReadWritePaths=`, `BindPaths=`, `WorkingDirectory=`, the arguments of `ExecStart=`) are read
  before anything changes, and a folder the shared account can write and the new account could
  not is refused by name, with who owns it and its mode. There are two ways forward: keep that
  data inside the application, in `shared/` (a persistent path), or let the new account in
  through a group that can write the folder, which is the group's job to say:
  `noust app identity migrate shop.example.com --shared-group uploads` (the body of the API call is
  `{"shared_group": "uploads"}`). The account joins the group with `usermod -aG` and leaves it
  again if the application does not answer. Permission bits are read, not ACLs.
- **All at once.** `noust app identity migrate --all` moves every application that can have an
  account of its own, one after the other, each behind its own health gate; one that fails is
  reported with its reason and the rest carry on. An application in zero-downtime mode starts its
  idle instance under the new account and switches to it, so nothing is cut. While any application
  still runs as the shared account, the server health card (`noust health`, the console's
  Overview) names **Application accounts** as a warning, with how many.
- **Not every application can have one.** A Docker Compose stack (its processes are the
  containers'), a monorepo (several units, which stay on the shared account for now) and a
  static site (nothing runs) keep the shared account; `status` says why. An application in
  zero-downtime mode must have it turned off to migrate. Creating and migrating need root.
