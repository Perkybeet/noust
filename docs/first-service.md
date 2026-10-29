# Add a service from the catalog

**Goal:** a ready-made application, such as a status page or a password manager, running on its
own domain, with its secrets generated and its backups scheduled.

**Time:** five minutes.

**You need:** [Noust installed](install.md) and a name pointing at the server
([Put it on your domain](first-domain.md) shows how). Recipes of the `compose` kind also need
Docker.

The example is Kelmoor's own: Uptime Kuma, to watch its clients' sites, on `status.kelmoor.dev`.

## How it works

The catalog holds more than a hundred applications as *recipes*: data that says where the
application comes from, at which version, what it needs, which variables to generate and how to
tell it is healthy. Installing a recipe is an ordinary deploy, so it gets releases, the health
check and the way back like any other application.

## Steps

1. **Find it.**

   ```bash
   noust recipe list -q monitoring
   ```

   In the console: **Catalog**, a card per application, with a search box.

2. **Read what it will run.**

   ```bash
   noust recipe show uptime-kuma
   ```

   It shows the source and the version it is pinned to, the port, what it keeps across releases,
   and the notes for after the install. In the console: **Details** on the card.

3. **Check that the server has what it needs.**

   ```bash
   noust recipe requirements uptime-kuma
   ```

   Each requirement is met or not, with the command that provides it. Uptime Kuma needs Node.js,
   which `noust setup init` installed.

4. **Install it.**

   ```bash
   noust recipe install uptime-kuma -d status.kelmoor.dev
   ```

   Noust deploys it like any application, gets its certificate and schedules its daily backups. A
   variable the recipe requires and cannot generate must be given with `--var NAME=VALUE`; the
   install stops before creating anything when one is missing. In the console: **Use** on the card.

5. **Finish what the recipe asks.** Uptime Kuma's note says it:

   ```text
   Open https://status.kelmoor.dev now to create the administrator account: until then, anyone who reaches it can.
   ```

## What you should see

`https://status.kelmoor.dev` opens on Uptime Kuma's own setup page. `noust status
status.kelmoor.dev` shows it as any other application, and the console lists it under **Catalog >
Installed** as well as under **Applications**.

## Keeping it up to date

```bash
noust recipe outdated                     # which installed recipes have a newer version
noust recipe update status.kelmoor.dev    # update one
```

An update is the update every application goes through: a backup first, the build, the health
check, and the previous version back if the new one does not answer.

## If it fails

- **A requirement is missing:** step 3 names it and the command that installs it. Or add
  `--install-requirements` to `noust recipe install`.
- **`compose` recipes and Docker:** install Docker first; `noust recipe requirements` says so.
- **The certificate was refused:** the application serves over HTTP until the name points here.
  [A certificate was not issued](troubleshooting-certificate.md).

[Recipes](recipes.md) covers the format, your own recipes and signed catalog packages.

## Next

[Make it safe to leave alone](first-safety.md).
