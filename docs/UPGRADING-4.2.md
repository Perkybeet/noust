# Upgrading to 4.2

This guide is written against Noust 4.1.0. A server older than 4.1 goes through
[UPGRADING-4.1.md](UPGRADING-4.1.md) first; the packages do the steps in one upgrade. The whole list of
changes is in [CHANGELOG-4.2.md](CHANGELOG-4.2.md), and what a client of the HTTP API can notice is in
[API-CHANGELOG.md](API-CHANGELOG.md).

The short version: 4.2 puts the console in order and changes nothing that runs. No unit, site or application
is rewritten by the update, the command line keeps every command and the HTTP API only grows. The store moves
to schema 17 the first time Noust starts (a copy of the old one is kept beside it), a token acts in sudo mode
only if it was made to, and a database engine that still holds databases is not uninstalled. Run
`noust health` after updating.

## Action required

- **A token that deletes, restores or changes access needs to be made for it.** Until 4.2 a token issued for an
  account passed where the console asks "Confirm it's you" whenever the account could. Now only a token
  created with `--elevated` (`allow_elevated` in `POST /api/auth/tokens`) by someone who governs accounts
  (`accounts.manage`) does, and each use of it there is in the audit trail. A pipeline that deletes an
  application, restores a backup or changes who may do what answers `403 elevation_required` with its old token:
  issue it again with `noust token create NAME --owner ACCOUNT --elevated` and replace the secret.
- **Uninstalling a database engine is refused while it holds databases.** `noust db engine uninstall` (and
  `DELETE` of an engine) names each database, from the store and from the engine itself, and stops. Drop or move
  them first; a script that uninstalled an engine with databases in it now gets a `409` with their names.

## What you will notice

- **One frame on every screen.** The main bar has titled groups (Applications and data, Network, Observe,
  Protect, Infrastructure) and folds to its icons; every area has a column beside it with its sections, and an
  application, a database or a service opens inside that column. Network and Security are areas of their own.
  On a central the frame says whether one server or the whole fleet is on screen. On a phone the column is a
  selector at the top of the page and the bar sits at the foot.
- **Pages moved, and their old addresses lead to where they went.** A bookmark keeps working, and the command
  palette finds a page by the name it had:

  | 4.1 | 4.2 |
  |---|---|
  | An application's Settings, Source, Build, Builds, Container | Code and build (`/apps/<domain>/code`) |
  | An application's Deploys, Automation, Compose, Deploy on push, Hooks, Previews | Deploys and triggers (`/apps/<domain>/triggers`) |
  | An application's Resources, Processes, Mounts, Disk, Security | Resources and running (`/apps/<domain>/resources`) |
  | An application's Access, Protection | Access and protection (`/apps/<domain>/access`) |
  | An application's Export, Delete | sections of its column (`/apps/<domain>/export`, `/apps/<domain>/delete`) |
  | Domains > Published ports, Domains > Tunnels | Network (`/network`, `/network/tunnels`) |
  | Server > Network (firewall, listening ports, interfaces) | Network (`/network/firewall`, `/network/listening`, `/network/interfaces`) |
  | Server > Security | Security (`/security`), with Web firewall (WAF) and Access and sessions as pages of their own |
  | Server > System | Server > Updates (`/server/updates`) |
  | Databases > Servers | Databases > Engines (`/databases/engines`) |
  | A database's Users | its Access (`/databases/<engine>/<name>/access`) |
  | The map (`/map`) | Home; `GET /api/map` and `noust map` answer as before |
  | Settings > Controls | Settings > Compliance (`/settings/compliance`) |
  | Settings > Connections | Settings > Integrations (the same address) |

- **Home is panels of widgets.** Each person arranges, resizes and keeps their own, for one server or for the
  fleet, and the first visit opens on a panel that is already useful.
- **Background work stays in sight.** The task centre in the top bar follows every job and long operation of
  every server from any page; a task can be pinned to the foot of every page and has a page of its own, and
  Activity > Tasks keeps the history.
- **A hint to the terminal appears only where the console cannot do the thing**, and says why (it is only in the
  terminal, it runs on another server, or it is what a system printed). The commands that repeated a button are
  listed in [console.md](console.md), and the command line documents them all in [reference-cli.md](reference-cli.md).
- **A server that never asked for the ENS is not judged against it.** With no framework on, `noust ens check`
  exits 0 and says so, and Settings > Compliance shows nothing in red. A script that read a non-zero exit as
  "this server has findings" should run `noust compliance check --framework ens-media` instead.
- **Notifications can be paused until a moment.** Turning them off from the console or from Alerts asks until
  when: for an hour, until tomorrow at 09:00, or until turned on again.

## What changes on its own

- **At the first start, the store moves to schema 17.** It opens and creates two tables: each account's Home
  panels (`account_dashboards`) and the ledger of the software Noust installs (`installed_software`). The update
  refuses nothing for the lack of a self-backup, but it says so; take one first (`noust self-backup`).
- **The ledger starts empty.** Server > Software shows what is installed and its version from the first start,
  but what was installed before 4.2 is "not known to be Noust's": removing it from the console or with
  `noust server software remove` asks for `--confirm-unknown-origin`. What Noust installs from 4.2 on is recorded.
- **Nothing is rewritten.** Units, sites, applications, the fleet's settings and the store's existing rows stay as
  they were; the sign-in screen looks new and shows no picture until an operator chooses one.

## A fleet in the middle of an update

A 4.2 central drives servers still on 4.1, and a 4.1 server answers a 4.2 central. What a 4.1 server lacks it
does not offer: the fleet's task list, its software and its updates list mark it "needs 4.2" and go on with the
rest.

Update the servers first, a canary at a time (`noust fleet run noust_update --canary NAME`), then the central.
The central's own image: `docker compose pull && docker compose up -d`, after `noust central backup`.

## Rolling back

4.2 writes the store at schema 17, which 4.1 does not understand (it opens schema 16 and no later). The update
keeps a copy of the older store beside it, and `noust store compat --to 4.1.0` says which copy to put back and what
that loses: whatever changed in Noust since the copy was taken. [downgrading.md](downgrading.md) has the steps
and the table of which release opens which store. What 4.2 left that 4.1 ignores:

- **Home's panels**: the copy has none, and the console of 4.1 draws its fixed Home.
- **The ledger of installed software**: software installed while on 4.2 stays installed, and is no longer known to
  have been Noust's.
- **The sign-in picture**: it stays in `sign-in-image` under the state directory, and 4.1 shows its own screen.
- **Settings 4.1 ignores**: `notifications.muted_until` is read by nothing in 4.1, so a pause in progress ends
  with the downgrade. A token created with `--elevated` keeps working as any token of its account did in 4.1.
