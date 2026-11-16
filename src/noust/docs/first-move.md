# Move an application from Coolify or Dokploy

**Goal:** one application that runs on Coolify or Dokploy today, running on a Noust server with its
variables and its data, and its domain moved over without a gap.

**Time:** fifteen minutes for one application, plus the time to copy its data.

**You need:** a new server with [Noust installed](install.md), a read-only API token of the old
platform, and SSH access to the old server to copy the data. Coolify and Dokploy keep running
until the very last step.

Kelmoor moves Brumaria's shop, a Next.js application from `github.com/kelmoor-labs/brumaria-shop`
with a PostgreSQL database, from its Coolify at `https://coolify.kelmoor.dev` to `fra-1`, at
`203.0.113.10`.

## How it works

Coolify and Dokploy keep an application's settings in their own database, not in the repository.
`noust import` reads them through the platform's API with a token you give it, proposes what Noust
would deploy (the type, the commands, the port, the health check, the variables, the databases
and the domains) and warns about anything that has no equivalent. It writes nothing to the old
platform, and keeps the token nowhere.

## Steps

1. **Make a read-only token on the old platform, and save it in a file only you can read.**
   In Coolify, under **Keys & Tokens > API tokens**, give it the `read` and `read:sensitive`
   permissions, so the variables come too. In Dokploy, under **Settings > API Keys**. On the new
   server:

   ```bash
   umask 077
   cat > /root/coolify.token     # paste the token, press Enter, then Ctrl+D
   ```

2. **List the applications.**

   ```bash tab="Coolify"
   noust import --from coolify --url https://coolify.kelmoor.dev --token-file /root/coolify.token
   ```

   ```bash tab="Dokploy"
   noust import --from dokploy --url https://dokploy.kelmoor.dev --token-file /root/dokploy.key
   ```

3. **Read the one you are moving.** Add `--app` with its name:

   ```bash
   noust import --from coolify --url https://coolify.kelmoor.dev --token-file /root/coolify.token \
     --app brumaria-shop
   ```

   Read the proposal, and the warnings under it. Each warning says what to do instead. The
   proposal also prints the commands that copy the data out of the old server.

4. **Put the secrets in a file.** A variable whose value looks like a credential, such as a
   `DATABASE_URL` with a password, is not carried over: it points at the old platform's database.
   Write the ones the application needs into `/root/shop.env`, one `NAME=value` per line, leaving
   out `DATABASE_URL` (step 6 writes it).

5. **Deploy it.**

   ```bash
   noust import --from coolify --url https://coolify.kelmoor.dev --token-file /root/coolify.token \
     --app brumaria-shop --deploy shop.brumaria.es --env-file /root/shop.env
   ```

   This is an ordinary deploy, behind the health check. The name still points at the old server,
   so the certificate cannot be issued yet: the deploy says so, and the application serves over
   HTTP until step 9.

6. **Give it a database, and load the data.** An import names the databases but does not create
   them.

   ```bash
   noust db provision shop.brumaria.es -e postgresql
   ```

   On the old server, run the `docker exec ... pg_dump` command the proposal printed, copy the dump
   to the new server (`scp`), and load it:

   ```bash
   noust db import shop_brumaria_es_db /root/brumaria-shop.dump -e postgresql
   ```

   Uploaded files and volumes come the same way: the proposal printed the `tar` command that copies
   each one out, and the one that puts it in the application's `shared/` folder here.

7. **Test it before moving the name.** From any machine, ask the new server directly:

   ```bash
   curl --resolve shop.brumaria.es:80:203.0.113.10 http://shop.brumaria.es/
   noust diagnose shop.brumaria.es
   ```

8. **Move the name.** At the DNS provider, point `shop.brumaria.es` at `203.0.113.10`, and check
   it with `noust dns check shop.brumaria.es`.

9. **Get the certificate,** now that the name points here:

   ```bash
   noust cert create -d shop.brumaria.es
   ```

10. **Turn the old one off, and keep its backups** until Noust has made a few of its own:

    ```bash
    noust backup schedule create shop.brumaria.es --schedule daily
    ```

    After a few days, check them with `noust backup verify` and a `noust backup drill`.

## What you should see

The proposal in step 3 lists the type, the build and start commands, the port, the health check,
the variables (secret values hidden), the databases and the domains, then the warnings. After
step 9, `https://shop.brumaria.es` answers from the new server, and `noust status shop.brumaria.es`
shows it running with its certificate.

## If it fails

- **The import says the application runs an image, a Dockerfile or a Compose file:** an import
  builds only what Noust builds from a repository. The message gives the `noust create` command
  that deploys it instead.
- **`The platform names no repository for it`:** give the repository with `--source`.
- **The token is refused:** check its permissions. Coolify needs `read:sensitive` for the
  variables.
- **The application cannot reach its database:** step 6 wrote a new `DATABASE_URL`; check that the
  file of step 4 did not carry an old one.

To take over the applications of a server Coolify runs, in place and without recreating a
container, see [Taking over a server Coolify runs](coolify.md#taking-over-a-server-coolify-runs).
The full maps of concepts and screens are in [From Coolify](coolify.md) and
[From Dokploy](dokploy.md).

## Next

[Next steps](next-steps.md).
