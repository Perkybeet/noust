# Give it a database

**Goal:** a PostgreSQL database that belongs to your application, with its connection string in
the application's environment, and migrations that run before each new version serves.

**Time:** three minutes, plus the engine's install the first time.

**You need:** [a deployed application](first-application.md) that reads its connection string from
the `DATABASE_URL` variable, as most frameworks do.

The examples use Kelmoor's application `wrenfield.io`. MySQL, MariaDB, Redis, Valkey and MongoDB
work the same way: change `postgresql` for the engine's name.

## Steps

1. **See which engines the server runs.**

   ```bash
   noust db status
   ```

2. **Install PostgreSQL, once per server**, if it is not there.

   ```bash
   noust db install postgresql
   ```

   This installs the distribution's version. For another one, `noust db catalog` lists what can be
   installed, and `noust db install postgresql --version 17` installs it.

3. **Create the database and link it to the application.**

   ```bash
   noust db provision wrenfield.io -e postgresql
   ```

   Noust creates the database `wrenfield_io_db`, owned by an account of its own,
   `wrenfield_io_user`, with a random password. It writes the connection string into the
   application's environment as `DATABASE_URL` and restarts the application so it reads it.

   In the console: **Databases > New database**, choose the engine and, under **Used by**, the
   application, then **Create and link**.

4. **Run your migrations before each new version serves.** Add a `noust.yaml` at the root of the
   repository:

   ```yaml title="noust.yaml"
   hooks:
     pre_deploy:
       - run: ./node_modules/.bin/prisma migrate deploy
         migrates: true
   ```

   This one is for Prisma; use your framework's command (`php artisan migrate --force`,
   `python manage.py migrate`). On every deploy from now on, Noust runs it after the build and
   before any traffic moves to the new version. It is one program and its arguments, never a shell
   line. If it fails, the deploy stops and the version that was serving keeps serving.
   `migrates: true` tells Noust the hook changes the schema, so going back past this deploy later
   needs you to confirm it: Noust puts code back, never a database.

5. **Deploy, and look inside.**

   ```bash
   noust update wrenfield.io
   noust db tables wrenfield_io_db -e postgresql
   noust db query wrenfield_io_db "SELECT count(*) FROM orders" -e postgresql
   ```

   `noust db query` runs one statement as the database's read-only account unless you add
   `--write`, so a typo cannot change data. In the console, the database's page has the same
   tables, a SQL console, its backups and its users.

## What you should see

```bash
noust db links wrenfield.io        # the database, its engine and the variable it is in
noust env show wrenfield.io        # DATABASE_URL among the variables, its value hidden
```

The password never appears in a command line or a log. It is in the application's `.env`, mode
`0600` and owned by the application's own account.

To create the database together with the application, at its first deploy, add
`--database postgresql` to `noust create`. The database then exists before the first build.

## If it fails

- **The engine is not installed:** `noust db provision` stops and says so. Install it as in step 2,
  or add `--install` to the command.
- **A version the server does not run** (`--version 16` on a server with 17): the command is
  refused with the version that runs. Install the other one as an instance next to it:
  [Databases](databases.md) explains instances.
- **The application cannot connect:** check that it reads `DATABASE_URL` (some frameworks want
  separate variables: `noust db provision ... --extra-vars` also writes `DB_HOST`, `DB_PORT`,
  `DB_NAME` and the rest), and read its log with `noust logs wrenfield.io`.
- **The migration hook failed:** the deploy is recorded as failed with the hook's own output, and
  nothing changed for visitors. Fix the migration and deploy again.

## Next

[Back it up](first-backup.md).
