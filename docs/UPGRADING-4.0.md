# Upgrading to 4.0

This guide covers what to know before updating to 4.0: where to update from, what was removed, the
names the product kept from its first life that now carry Noust's, and the few things that change
on their own. A server is updated from 3.3.x with the usual package update; nothing is restarted by
it that was not restarted before, and everything that is outside Noust's control keeps working. This
page says what changes, what still works and what to do about each. The whole list of changes is in
[CHANGELOG-4.0.md](CHANGELOG-4.0.md).

The short version: sign in to the console once, run `noust health` to see which of your scripts,
repositories and sites still use an old name, run `noust setup rename` when you like, and look up
a screen you cannot find in [Where it is now](#where-it-is-now).

## Where to update from

- **From 3.3.x**, update with the usual package update (`apt`, `dnf`, `zypper` or `pip`). 4.0 opens the
  store, migrates it forward to schema 15 and keeps a copy of the older one beside it (see
  [Rolling back](#rolling-back)).
- **From WASM 2.x, update to 3.3.1 first.** 3.x moved a server onto Noust's names the first time root
  ran a command; 4.0 moves nothing. On a server whose configuration or store is still a real directory
  under the old names (`/etc/wasm`, `/var/lib/wasm`), 4.0 does not start: every command except help and
  version refuses and says how to go on, because a store created empty beside the real one would answer
  "No applications deployed" about a full server. The packages stop earlier: the Debian pre-install
  script and the RPM `%pre` refuse to unpack 4.0 over such a server and say to install 3.3.1 first. To
  go on, install 3.3.1, run any command as root so that it moves the data, check with `noust list`, and
  update to 4.0.
- **The links 3.x left behind** (`/etc/wasm`, `/var/lib/wasm`, `/var/backups/wasm`,
  `/etc/nginx/wasm-upstreams`, `~/.local/share/wasm` and `/usr/local/bin/wasm`) are retired by the
  package when you update, or by `noust setup retire-links` (`--dry-run` shows what it would do). A link
  goes only if it points exactly where 3.x pointed it, its target is there, and neither the
  configuration, an nginx site nor the store names it; a real file or directory is never touched, and a
  link that stays says why. A script of yours that uses one of those paths must change before the
  update, because nothing counts a script as a use.

## What was removed

| Removed | Use instead |
|---|---|
| The `wasm` command (its entry point, completions and manual page) | `noust`, with the same arguments |
| `noust migrate-from-wasm`, the notice about the rename and `NOUST_NO_AUTO_MIGRATE` | Nothing: 3.3.x does the move on the first command run as root |
| The `WASM_*` spelling of every `NOUST_*` variable (`WASM_APPS_DIR`, `WASM_WEB_STATE_DIR`...) | The `NOUST_*` variable |
| The directories and the store under the old names | The Noust ones, which 3.3.x already moved the data to |
| The transitional packages `wasm` (Debian) and `wasm-cli` (RPM and PyPI) | `noust`, the only package; remove the old one if it is still installed |
| `renamed_from_wasm` in the answer of `GET /api/auth/session` | Read it as absent: it meant `false` |
| The `linked_app` key of `noust db list --json`, which repeated `app` under the name 2.x used | `app`, with the same value |

## What you will notice

- **The console asks you to sign in once.** Its cookies are `noust_session` and `noust_csrf`, and
  the header it echoes is `X-Noust-CSRF`. The cookies of 3.x are never read; signing in and signing
  out expire them. Nothing else about a session changes.
- **The console is arranged differently.** The sidebar, the top bar, Settings and the application's
  page changed shape, and a few screens are somewhere else: [Where it is now](#where-it-is-now) lists
  each one. An old address redirects to the new one.
- **Units are renamed by the next update of each application.** An application deployed before
  0.14.1 runs as `wasm-<name>`. Its next update renames the unit as part of the restart it makes
  anyway, behind the same health check, and puts the old unit back if the release does not answer.
  Nothing stops an application only to rename it.
- **A database's read-only account is renamed by the next read of it** (`noust_ro_<database>`); the
  old one is dropped in the same step. A dump taken before the update restores, because a restore
  makes both names of the role.
- **Backups go to a new folder.** A destination that sent to the old default folder sends to
  `noust-backups` from the next backup. What is in the old folder stays listed, restorable and under
  its retention until it has expired. If the new folder cannot be created at the destination (a
  bucket a key may not create, a name another account owns) backups keep going to the old one and
  `noust backup destination test` says why. Naming a folder yourself ends all of this.
- **The files Noust keeps in an application's tree** (`.noust/`, `.noust-runtime.json`,
  `.noust-php.json`, `.noust-php-tmp`) are renamed by the next deployment of the application, with
  what is in them. Dependencies the old runtime stamp vouches for are not reinstalled.
- **The image a Compose update keeps to go back to** is tagged `noust_previous`; the next update of
  a stack retags what is under the old name.

## Where it is now

The console of 4.0 is arranged differently, and a few screens of 3.3 are somewhere else. Every old
address that changed redirects to the new one, keeping the filters it carried, and the command palette
(`Ctrl+K`) and the filter of Settings still find a screen by the name it had. Names are written as the
console draws them in English.

**Around the pages**

| In 3.3 | In 4.0 | The old address |
|---|---|---|
| The server selector in the top bar (one server, **All servers**, **This central**) | The scope selector at the head of the sidebar, and in the top bar on a narrow screen. It is not drawn on a lone server with nobody else added | Addresses of a server (`/n/<server>/...`) are unchanged |
| **Fleet**, an item of the sidebar of a central, with a strip of seven tabs: Summary, Servers, Applications, Certificates, Backups, Updates, Activity | **All servers**: the sidebar of that scope lists Overview, Servers, Applications, Certificates, Backups, Updates and Activity. Applications keeps a strip of two, with **On several servers** | `/fleet` and the pages under it are unchanged |
| **Domains and certificates** | **Domains** | `/domains`, unchanged |
| **Server > Logs** | **Logs**, with **Where from: System** | `/server/logs` redirects to `/logs?source=system`, keeping the service, level, range, words and stretch |
| The session menu: Theme, Keyboard shortcuts, Sign out | Also **Account** and **Sign-in security** | |
| **Emergency access**, on the sign-in page | **Access token** | `/login` is unchanged |

**Settings**

| In 3.3 | In 4.0 | The old address |
|---|---|---|
| **Settings > Security**: your password, authenticator app, passkeys and sessions, and the server's sign-in policy | **Account** (profile and password) and **Account > Sign-in security** (authenticator app, passkeys, sessions) in the avatar menu; the security profile and the sign-in policy are **Settings > Controls** | `/settings/security` redirects to `/account/security` |
| **Settings > Servers** | **Fleet > Servers**, the one list of servers (pause monitoring, create at a provider, **Add a server**) | `/settings/servers` redirects to `/fleet/servers`, keeping filters and the **Add a server** flow |
| **Settings > Notifications** | **Settings > Channels** | `/settings/notifications`, unchanged |
| **Settings > Compliance** | **Settings > ENS report**; the compliance frameworks and controls are in **Settings > Controls** | `/settings/compliance`, unchanged |
| **Settings > About** | **Settings > About Noust** | `/settings/about`, unchanged |
| Settings as one list (two blocks, the server's and the central's, on a central) | Six groups. This server: General, Variables, Secret managers, Console address, Integrations, About Noust. Monitoring: Channels, Alert rules, Probes and status page, Logs and metrics export. Developers: API tokens, API reference, Manifest, MCP. People and access: Accounts, Roles, Single sign-on, Approvals. Governance: Audit log, Controls, ENS report. Fleet: Servers, Cloud providers, Central | Every address of 3.3 is unchanged except the two above |

**An application**

| In 3.3 | In 4.0 | The old address |
|---|---|---|
| Settings, nine sections: General, Deploys, Deploy on push, Deploy hooks, Builds, Resources, Previews, Export, Delete | Six groups. Code: Source, Build, Container. Deploys: Releases and rollback, Automation, Compose. Triggers: Deploy on push, Deploy hooks, Previews. Runtime: Resources, Processes, Mounts, Disk, Service security. Access: Access, Protection. Export and delete. General stays at the top | **Builds** is **Build** (`/settings/builds` redirects to `/settings/build`); **Deploys** is **Releases and rollback** at the same address |
| Overview: **Live version**, **How it runs** and the dots of the last deploys | **Live now**, the last deployments, one health section, previews and where the application answers | |
| Deployments: a Releases section and a table of deployments | One list of deployments, filtered by search, state, branch and who started it, with the filters in the address | `/apps/<domain>/deployments`, unchanged |
| A rollback dialog | One rollback question, asked from the header (**Roll back**), a row, a deployment's page, Diagnose and the palette | |
| Environment: **Environment saved**, a step after saving | The bar saves only what changed: **Save and restart** or **Save only** | |

**The first screens**

| In 3.3 | In 4.0 |
|---|---|
| An empty Overview shows first steps | One card, **Deploy your first app**: **Try a demo**, **Use a repository**, popular recipes and **All recipes** |
| **Needs attention**, a card on the Overview | The same list, and the bell in the top bar (**Needs you** and **Recent**) |

## What still works, and until when

Noust accepts an old name on input and writes the new one. The list, with the reason for each and
when it stops, is `noust.core.legacy_names.REGISTRY`; the ones that matter to an operator:

| Old | New | Until |
|---|---|---|
| `X-WASM-CSRF` header | `X-Noust-CSRF` | 5.0 |
| WebSocket subprotocols `wasm.auth`, `wasm.token.<token>` | `noust.auth`, `noust.token.<token>` (answered in kind) | 5.0 |
| API tokens that start with the old prefix | `noust_tok_` for new ones | until each is revoked or expires |
| `wasm.nginx.yaml` in a repository | `noust.nginx.yaml` | 5.0 |
| `?wasm_only=` on `GET /api/services` | `?noust_only=` | 5.0 |
| backups and application exports of 3.x | read by one module, for as long as the file exists | never |

`noust health` names the API tokens that signed in with the old prefix lately, the repositories
that still have the old overrides file, and the sites that still name an old upstream, each with
what to change. A deployment that reads `wasm.nginx.yaml` prints the `git mv` that renames it.

## Sites you wrote that name an upstream of Noust's

Blue/green activation and a Compose relay give nginx an `upstream` block named `noust_bg_<app>`
(`wasm_bg_<app>` before 4.0). If a site **you** wrote says `proxy_pass http://wasm_bg_<app>`, it
keeps working: while any file you wrote names the old upstream, Noust defines it beside the new one
(a second block with the same servers, under a comment that says why) and never edits your site.
`noust setup doctor` prints the file, the line and the line to write. Change it whenever you like,
reload nginx, and run `noust setup rename`: the alias goes away once nothing names it. At no point
between your edit and that command is either name undefined.

## `noust setup rename`

Finishes the renaming without restarting anything, and is safe to repeat:

- the upstream names of blue/green and of Compose relays (as above);
- the files in each application's tree;
- the read-only accounts of every database engine that is running;
- the image tags of Compose stacks;
- the branch a detached checkout remembers;
- the first line ("Generated by ...") of sites and units written before 3.0, with a copy of each
  file under `/var/backups/noust/names-4.0`. That one also runs by itself, once, on the first
  privileged command after the update, because a file that no longer says Noust wrote it is a file
  Noust would stop managing without saying so.

`--dry-run` shows what it would do.

## What else changes on its own

None of these is a rename. Each is a behaviour that 4.0 starts with on a server that already runs.

- **Some security friction is a choice, not a default (item 83).** Until 3.3 a server on the standard
  profile still carried part of what the ENS profile asks for. In 4.0 each of those is a control with its
  own setting, off until you turn it on or enable a framework, and `noust compliance status` says what is on
  and what forces what ([COMPLIANCE.md](COMPLIANCE.md)). On a server that stays on the standard profile:
  the administrator holds every permission, including the security officer's (accounts, security
  settings, compliance) and the auditor's (the audit log), so one person can run a server with one
  account; a person may hold accounts of several roles; and a new account is no longer held at the
  second-factor enrolment on its first sign-in, except when a framework asks for a second factor or the
  console is published or listens beyond loopback (it can still set one up at **Account > Sign-in
  security**). A security officer's account keeps working as it was. A server that had chosen
  `security.profile: ens-medium` keeps all of it: the setting now means that ENS medium is on. To get the
  stricter behaviour back without a framework: `noust compliance set roles_split on`,
  `noust compliance set separation_of_duties on` and `noust compliance set second_factor on`.
- **An update is refused when the backup it needs did not happen.** An application with a database
  requires a backup before its update, and a backup that fails stops the update before anything is pulled
  or built. `noust app backup-required DOMAIN off` opts one application out.
- **Ports are checked in one place.** A Compose deploy or update asks the registry of ports before it
  builds anything, so a stack that publishes a port another application, a stream, a database or the
  console holds (8080 on a default install) is refused with the holder named, where Docker used to fail
  at the recreation.
- **The monitor restarts the web server.** When nginx or Apache falls and its configuration test passes,
  the monitor starts it again, within a limit an hour, and announces it; a server a person stopped is left
  alone. `monitor.restart_webserver: false` keeps the announcement and drops the restart
  ([MONITOR.md](MONITOR.md)).
- **Credentials are encrypted on the disk.** The secrets of notification channels in `config.yaml` and the
  credentials of backup destinations, with the key a destination's backups are encrypted with, are
  encrypted under a key in `/etc/noust/secrets.key`, by the console when it starts and the first time a
  backup uses a destination (`noust config protect` and `noust backup destination protect` do it at once).
  Without that key the sealed values cannot be read, and a server that lost it keeps the ciphertext as it
  is.
- **A Compose stack downloads its images before its unit starts**, so a large image no longer fails the
  first install or update at the two-minute wait of `systemctl start`.
- **Logs are masked.** The logs the console and the API show have private keys, tokens, `password=`
  assignments and credentials in URLs replaced by `***`.
- **API limits.** An IPv6 client is limited by its /64, a credential that holds sixteen event streams is
  refused a seventeenth, and a stream ends after twelve hours (the browser reconnects by itself).
- **Previews are never indexed.** A preview starts with the policy that asks search engines to stay away.
- **New things start with the new defaults; existing ones are left alone.** A new application on the
  releases layout updates without a cut after its first deployment, and its unit runs in systemd's sandbox
  from the start; an existing application keeps its way until you change it
  (`noust app zero-downtime`, `noust app hardening set`). A new backup destination is encrypted unless you
  say otherwise.

## A fleet in the middle of an update

A central of 4.0 reaches a node of 3.3 and the other way round: what a central sends a node (the
fleet token, `X-Noust-Actor`, the `Authorization` of a stream) never carried one of the old names.
The one thing that did is the export document a central carries from one server to another when it
copies an application: the central writes it in the spelling the destination's release reads, from
the version the node declares. Update nodes first, as before, a canary at a time.

## Rolling back

4.0 writes the store at schema 15, which 3.3.1 does not understand (it opens schema 13 and no later).
The upgrade keeps a copy of the older store beside it, and `noust store compat --to 3.3.1` says which
copy to put back and what that loses: whatever changed in Noust since the copy was taken. The sealed
credentials of notification channels and the switched-off domains are what 4.0 wrote that an earlier
release ignores. [downgrading.md](downgrading.md) has the steps, in order, and the table of which
release opens which store: schema 15 is the first that 4.0.0 wrote.
