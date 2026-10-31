# Upgrading to 4.1

This guide is written against Noust 4.0.0. A server older than 4.0 goes through
[UPGRADING-4.0.md](UPGRADING-4.0.md) first; the packages do the steps in one upgrade. The whole list of
changes is in [CHANGELOG-4.1.md](CHANGELOG-4.1.md), and what a client of the HTTP API can notice is in
[API-CHANGELOG.md](API-CHANGELOG.md).

The short version: 4.1 changes what the console shows and how a fleet is run, not what runs. No unit, site
or application is rewritten by the update, and everything that is outside Noust's control keeps working. The
store moves to schema 16 the first time Noust starts (a copy of the old one is kept beside it), a script that
passes `--json` to a command that asks must now pass `--yes` too, and the setting of the web server is
refused while the other server still has sites. Run `noust health` after updating.

## Action required

- **A script that passes `--json` to a command that asks a question needs `--yes`.** Until 4.1 `--json` was
  read as "nobody is here to ask", and about fifty commands went ahead on it: `noust compliance disable
  ens-media --json` turned the framework off on a central whose input was closed. A command that changes
  something and has a flag that spares its question (`--yes`, or `--force` where that is how the command spells
  it) is now refused under `--json` without that flag, before its body runs, with a usage error (exit status 2)
  that says which flag to add.
  - To look at what a command would do, run it with `--dry-run --json` on this machine, or read the plan with
    `noust manifest plan` (a server) or the command's own `--plan`. `noust compliance enable|disable|set` and
    `noust app migrate` print their plan under `--json` and change nothing until `--yes`.
  - To find your scripts' calls, search them for `--json` and run each once with `--dry-run`.
- **A script that sets `webserver` while the other web server still serves sites.** `noust config set
  webserver apache` (and `PUT /api/config/webserver`) sets the server new applications are put behind, and
  is refused (`webserver_in_use`, with how many applications and sites are behind each server) while any are
  behind the other one. Move them first: `noust webserver plan --to apache --all`, then `noust webserver
  migrate --to apache --all` ([network-webserver.md](network-webserver.md)).

## What you will notice

- **The console is new, and its pages are where the work is.** Twelve areas in a main bar (Home, Applications,
  Databases, Scheduled tasks, Domains, Logs, Alerts, Activity, Backups, Servers, Catalog and Settings), a
  second column inside an application, a server and Settings, breadcrumbs, and one bar instead of two if you
  prefer it (the bar says how). You sign in as before. Where the pages of 4.0 went is in
  [UPGRADING-4.0.md](UPGRADING-4.0.md#where-it-is-now) (the moves of 4.1 are its last rows), and the
  command palette finds a page by the name it had.
- **A fleet reads as one.** Applications, databases, sites, certificates, backups, scheduled tasks, logs,
  alerts and activity are one page each, with the server as a column and a filter. The five fleet routes that
  read the same things are deprecated with a Sunset of 2027-05-01: change a script from
  `GET /api/fleet/apps`, `/certificates`, `/backups`, `/activity` and `/logs` to the same name under
  `/api/views` before then. They answer as they did until that day.
- **Roles are one list.** Settings > Roles lists the five roles of 4.0, the owner and yours; an account holds
  one role or several, and the people table shows them all. What an account may do is the same as before the
  update: the first start works it out and changes nothing for anyone.
  - The oldest account that can sign in and already holds every permission is made the owner. A server where
    nobody does (the roles are kept apart, as ENS asks) is left without an owner; an owner, or root with `noust
    user roles`, makes one.
  - On a server where `roles_split` was in force the administrator keeps what it resulted in there, and the
    role that joins governing the server with running it is named in the ENS report. Split it with
    `noust access role set` or `noust user roles` when a framework asks.
  - A role you never edit receives the permissions a release adds; one you edited is offered them
    (`noust access role offers NAME`).
- **Approvals cover more.** With four-eyes approvals on, giving a team access, adding a team member or raising
  a member's role, widening a role you defined and changing what a sign-in source hands out wait for a second
  person, as role grants always did. A script that did these with a token will now get `202
  approval_required` and the request to decide ([security-approvals.md](security-approvals.md#four-eyes-approvals)).
  Only an owner gives or takes the owner role.
- **Services a timer starts are "scheduled".** Between two runs, a backup or a scheduled task is no longer
  counted among the stopped units, so a server that said "13 stopped" says "13 waiting for their timer".
- **Recipes that are root on the server ask first.** Portainer, Coder, Dozzle and OpenClaw can be installed with
  `noust recipe install --accept-root-equivalent` (sudo mode, `server.manage`). A server that names where code may
  come from (`security.allowed_sources`) allows the catalogue's recipes with the word `recipes` in the list
  (the console offers "Allow the catalog's recipes"; the refusal prints the command), and `local`, which some
  servers added to get them past the list, keeps working.
- **A server that has a central says when the central goes quiet.** If the central's monitor runs and has not
  called for `notifications.central_silent_minutes` (15; 0 turns it off), the server tells its operator once,
  through its own channels.

## What changes on its own

- **At the first start, the store moves to schema 16.** It opens, seeds the roles, gives each account its roles
  and creates the tables of the fleet's settings, the GitHub leases, the database servers' defaults and Noust
  DNS. The update refuses nothing for the lack of a self-backup, but it says so; take one first
  (`noust self-backup`).
- **Nothing is pushed to a server.** A central writes nothing until someone saves a setting of the fleet or
  presses Apply to the fleet, a server marked independent is never pushed anything, and a setting a server
  already has is not taken over unless you say so.
- **GitHub keeps deploying as it did.** A deploy key or a credential of a server's own is untouched; the central
  leases a server a token only when it has a GitHub App, the server has no way of its own to clone, and the
  server's ceiling is `deploy` or higher.
- **Applications keep the web server they were deployed behind**, and the setting of the web server is the one
  for new applications.
- **A token limit counts only if you set it.** `noust compliance set token_max_days 90` is recorded as your
  choice (`auth.tokens.enforce_max_days`) and kept when a framework is taken off; a server that never sets it
  behaves as before.

## A fleet in the middle of an update

A 4.1 central drives servers still on 4.0, and a 4.1 server answers a 4.0 central: what a server lacks it does
not offer (the fleet's settings, the leases of the GitHub App, `GET /api/roles/review`, database servers, the web
server move, Noust DNS), and the console says it runs an older Noust. The central sends a 4.1 server what the
person may do (`X-Noust-Actor-Permissions`) and a 4.0 server the name of the widest role of 4.0 they hold.

Update the servers first, a canary at a time (`noust fleet run noust_update --canary NAME`), then the central.
The central's own image: `docker compose pull && docker compose up -d`, after `noust central backup`.
Nothing in the fleet's settings, GitHub or DNS needs the whole fleet on 4.1: each server joins as it updates.

## Rolling back

4.1 writes the store at schema 16, which 4.0 does not understand (it opens schema 15 and no later). The update
keeps a copy of the older store beside it, and `noust store compat --to 4.0.0` says which copy to put back and what
that loses: whatever changed in Noust since the copy was taken. [downgrading.md](downgrading.md) has the steps
and the table of which release opens which store; schema 16 is the first that 4.1.0 wrote. What 4.1 left that 4.0
ignores:

- **Roles you edited, several roles of an account and the owner**: the copy holds the roles of 4.0, each account
  with one.
- **The fleet's settings, leases and deliveries**: the copy has none. A server that applied a setting keeps it
  as its own.
- **Applications that moved to the other web server.** The copy says nginx while the site is on Apache. Move
  them back before you go back: `noust webserver migrate --to nginx --all`.
- **Noust DNS is Knot, a service of the system.** Going back leaves it answering; 4.0 does not know it. Take it
  away first with `noust dns server remove` (`--delete-zones` when delegation has moved), or leave it to be
  managed by hand.
- **Settings 4.0 ignores**: `auth.tokens.enforce_max_days`, `notifications.central_silent_minutes` and
  `notifications.console_url` are read by nothing in 4.0, and the limit of tokens set by number alone does not
  count there.
