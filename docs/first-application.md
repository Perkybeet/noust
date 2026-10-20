# Your first application, database and service

This walks one server from "Noust is installed" to a running application with a certificate, a
database it uses, a backup, and a ready-made service from the catalog. Each step is shown in the
console and as the command that does the same. Allow about ten minutes; the part that takes time is
waiting for DNS.

The machine's part has been measured (`tests/integration/network_run.py --scenario first_deploy`): from
`apt install` on a bare Debian 13 server (no nginx, certbot, Node.js, git or Noust) through
`noust setup init` to a Node.js application answering over HTTPS took 52 seconds on the machine that
ran it (22 for the package, 19 for `setup init`, 10 for `noust create` with the certificate), with
Pebble, Let's Encrypt's test server, as the authority and the package read from a file. The figures and
what they leave out are in `tests/integration/results/first-deploy.json`; the budget is ten minutes.

## Before you start

- **Noust is installed and the server is ready.** `noust setup doctor` lists what a deployment
  needs and fixes nothing it is not told to; the console shows the same under **Settings > About >
  Installation check**. Every line should be green, or a warning you understand
  ([platforms.md](PLATFORMS.md) has the install).
- **A name points at the server.** Create an `A` record (and `AAAA` if the server has IPv6) for
  the domain you will use, `shop.example.com` below, at your DNS provider. Noust checks it before
  asking for a certificate and tells you if it does not resolve here; the certificate is the step
  that fails when it does not.
- **A repository to deploy.** Any of [these stacks](frameworks.md). If you only want to try Noust,
  a static page works: a repository with an `index.html` is deployed as a static site, and
  [`examples/`](../examples/README.md) has a minimal application for every type Noust deploys
  (`noust create -d hello.example.com -s examples/nodejs` from a clone of the repository).
- **Open the console.** It listens on `127.0.0.1:8080` of the server and nowhere else. From your
  computer: `ssh -N -L 8080:127.0.0.1:8080 root@server.example.com`, then
  <http://127.0.0.1:8080>, and sign in with the token `noust web enable` printed. A new server's
  Overview lists the first steps with what is done and what is not.

## 1. Deploy the application

In the console: **Applications > New application**. The wizard asks:

1. **Source**: the repository's URL (a private repository needs a deploy key or the GitHub App:
   `noust setup ssh --generate --show` prints a key to add). Noust reads the repository and shows
   what it found before anything is created: the type, the install, build and start commands, the
   port, and the variables in `.env.example` with the secrets flagged.
2. **Address**: the domain. The wizard checks its DNS.
3. **Configuration** and **Variables**: what Noust detected, editable. Secrets it can generate are
   generated.
4. **Deploy**: the build log streams until the application answers.

From the command line, the same:

```bash
noust create -d shop.example.com -s https://github.com/you/shop.git
```

What `noust create` did, in order: detected the type; fetched the code into a new release under
`/var/www/apps/shop-example-com/releases/`; installed and built it; wrote a systemd unit
(`shop-example-com.service`) and an nginx site pointing at `current`; asked Let's Encrypt for a
certificate; started the application; and **kept the release only because it answered** its health
check. Had it not, the previous release (here, none) would have kept serving and the error would
have carried the probes and the journal verbatim.

```bash
noust status shop.example.com           # how it is configured, whether it runs
noust logs shop.example.com --follow    # what it prints
```

If something is wrong, `noust diagnose shop.example.com` names the most likely cause first:
[troubleshooting.md](troubleshooting.md).

## 2. Change it, and go back

Push a commit, then:

```bash
noust update shop.example.com           # a new release behind the health check
noust releases rollback shop.example.com   # back to the previous release, in seconds
```

In the console, the application's page has **Update** and **Roll back**; the Deployments tab keeps
every build's log. To deploy on every push, the application's **Settings > Webhook** (**Deploy on
push**) shows the address and secret to give your Git host (`noust app webhook show
shop.example.com`), or connect the GitHub App for a GitHub repository. [releases.md](releases.md) has the whole model.

## 3. Add a database

An application's database is created with an account of its own, and its connection string is
written into the application's environment:

```bash
noust db install postgresql                        # once, if the engine is not on the server
noust db provision shop.example.com -e postgresql  # creates it, links it, restarts the app
```

(`noust create ... --database postgresql` does it at the first deploy, before the first build.)
In the console: **Databases**, where the new database has its own page with its tables, a SQL console
(reads are read-only because the database server enforces it), backups and users. `noust db tables
shop -e postgresql` and `noust db query shop "SELECT count(*) FROM orders" -e postgresql` do the same
from a terminal. Run migrations as a deploy hook, so they happen before the new version serves:
[frameworks.md](frameworks.md). [databases.md](databases.md) has the rest.

## 4. Back it up

```bash
noust backup create shop.example.com -m "First backup" --include-databases
noust backup schedule create shop.example.com --schedule daily --retention-count 7
```

A backup is one file under `/var/backups/noust/shop-example-com/` with its checksum, holding the
release, `.env` and the database dumps, so it restores on a server that knows nothing about this
one. That is still the same disk: add a place off the server (`noust backup destination add
offsite ...`, then `noust backup destination test offsite`) and the schedule copies each backup
there too. In the console: **Backups**. [disaster-recovery.md](disaster-recovery.md) is how to use
them when the server is gone.

## 5. Add a service from the catalog

Noust ships ready-made applications: password managers, wikis, analytics, monitoring, chat, file
sync and more, each pinned to a version, with generated secrets, a health check and next steps.

```bash
noust recipe list -q "password manager"           # search the catalog
noust recipe show vaultwarden                     # what it runs, what it opens, what it needs
noust recipe install vaultwarden -d vault.example.com
```

In the console: **Catalog**, a card per application; **Use** asks where it answers and the few
variables it needs, **Details** shows exactly what will run (the image, tag and digest). An installed
recipe is updated through the same update as any application (a copy first, the startup check, the
previous version back when it fails): `noust recipe update vault.example.com`.
[recipes.md](recipes.md) has the format, if you want to add one.

## 6. Make it safe to leave alone

The Overview's first steps also ask for these, and each is a minute:

- **Notifications**, so a failed deploy or a certificate about to expire reaches you, not a
  customer: **Settings > Notifications** (email, Telegram, Slack, Discord, or a webhook).
- **Two-step verification** on your account: **Settings > Security**. It is also what lets you
  publish the console on a name of your own (`noust console expose`).
- **A backup destination off the server**, from step 4.

From here: [the tour of every area](../README.md), the [console](console.md), [servers in a
fleet](CENTRAL.md), and [`noust --help`](reference-cli.md).
