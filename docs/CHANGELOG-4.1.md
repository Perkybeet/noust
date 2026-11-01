# Noust 4.1 changelog

Changes since Noust 4.0.0. What to do before updating is in [UPGRADING-4.1.md](UPGRADING-4.1.md);
what a client of the HTTP API can notice is in [API-CHANGELOG.md](API-CHANGELOG.md).

4.1 makes the central the place a fleet is worked from, and gives the console a new frame, a new look
and one set of words. A fleet is read as one thing: an application, a database, a certificate or a log
line comes with the server it is on, as a column and a filter. The settings that belong to the fleet are
written once on the central and every server applies them. The GitHub App lives on the central and lends
each server what it needs to deploy. Roles are one list the operator edits. Beside that, 4.1 adds a name
server of your own (Noust DNS), a move of an application between nginx and Apache, database servers in any
version, a catalogue of 440 recipes that show the project's logo where it may be shown, and the
decision of every alert that code scanning had piled up. It is a minor version: the HTTP API only grows,
the command line keeps every command, and the store moves to schema 16.

## Before you upgrade

[UPGRADING-4.1.md](UPGRADING-4.1.md) has the order of an update and the way back; this is what to know
first. The two that can stop a script are marked.

- **The store moves to schema 16, and 4.0 cannot open it.** The upgrade keeps a copy of the store beside
  it. Going back to 4.0 means putting that copy back, which loses what changed since it was taken
  (`noust store compat --to 4.0.0` says which copy and what that loses; [downgrading.md](downgrading.md)
  has the steps).
- **Roles move to one list, and nobody gains or loses a permission by it.** On the first start the store
  seeds the five roles of 4.0 and the owner, turns the custom roles into rows of the same list and gives
  each account its roles. The oldest account that can sign in and already holds every permission becomes
  the owner; a server where nobody does (one that keeps the duties apart) is left without an owner rather
  than give anybody more than they had. `roles_split` no longer cuts permissions from the administrator
  behind the roles' back: a server where it was in force keeps its administrator as it resulted there, and
  the roles are judged as they are defined (item 96).
- **Two-person approvals cover more.** With approvals on, giving a team access, adding a team member or
  raising one's role, widening a role you defined, and changing what a sign-in source hands out now wait for
  a second person, as the other role grants always did. Only an owner gives or takes the owner role,
  through every route. Without approvals nothing changes, except the owner rule. See
  [Roles and approvals](#roles-and-approvals).
- **Action required if a script uses `--json`: a command that asks needs `--yes` beside it.** `--json` was
  read as "nobody is here to ask, so go ahead": `noust compliance disable ens-media --json` turned the
  framework off on a server with its input closed. A command that changes something and has a flag that
  spares its question is now refused under `--json` without that flag, before it does anything. To look at
  what a command would do, use `--dry-run --json` (on this machine) or `noust manifest plan` (on a server).
- **Action required if a script changes the web server with sites behind the other one.** `noust config
  set webserver apache` and `PUT /api/config/webserver` set the server *new* applications are put behind;
  they are refused (`webserver_in_use`) while applications or sites are still behind the other server,
  because every application keeps the one it was deployed behind. `noust webserver migrate` is how they
  move (item 105).
- **Five routes of the fleet are deprecated, with a Sunset of 2027-05-01.** `GET /api/fleet/apps`,
  `/certificates`, `/backups`, `/activity` and `/logs` keep working and answer `Deprecation`, `Sunset` and
  `Link` headers; each has a successor under `/api/views`. Nothing is removed in 4.1.
- **The console's pages moved, and the old addresses lead to where they went.** Alerts and probes are the
  Alerts area, a server's updates and storage are inside System and updates and Files, its firewall is in
  Network and ports, and the engines are Database servers. Bookmarks redirect, and the command palette
  finds a page by the name it had in 4.0.
- **A fleet in the middle of an update works.** A 4.1 central drives servers still on 4.0: a server lacks
  only what is new (the fleet's settings, the leases of the GitHub App, the review of roles, database
  servers, the web server move, Noust DNS), and the console says it needs updating. A 4.1 server under a
  4.0 central reads the role's name as before. Update the servers first, a canary at a time, as before.
- **Nothing is pushed on its own.** A central writes nothing to a server until someone saves a setting of
  the fleet or presses Apply to the fleet, and a server that is independent is never pushed anything.

## The new console

Items 86, 89, 91, 92, 93, 94, 97, 98, 99, 100, 106 and 107 are the console's. It was rebuilt in three pieces:
a frame, a kit in a new look, and the pages that stand on both. It keeps what the CSP, the contrast checks and
the weight budget require; the script every page loads is held to 398 KiB gzipped (4.0: 464 KiB).

### A frame you find your way in

- **Twelve areas in a main bar, the same with or without a fleet** (items 91, 107): Home; Applications,
  Databases and Scheduled tasks; Domains; Logs, Alerts and Activity; Backups; Servers and Catalog; and
  Settings under them. A lone server draws no server selector; with a fleet the selector in the top bar says
  which server a page is about, and on the lists the server is a column and a filter.
- **A second column where a thing has sections**: an application (use, configure, fix, danger), a server and
  Settings. Home has none. Alternatively one bar, with the same entries as tabs in the page. The choice is
  made in the bar itself, drawn as two small windows with a name each. Below 1280 px there is one bar; from
  1280 to 1439 px the main bar is folded to icons and opens over the page without pushing it; from 1440 px
  what you chose decides: the presentation, whether the main bar is folded, and the width of the page
  (comfortable or the whole width there is, item 89). It is kept in the browser, like the theme.
- **Breadcrumbs on every page of two levels or more** (item 100), written from the same register as the
  palette, so a page and its trail cannot disagree. A step also switches to its siblings, so you jump from
  one application to another without going up first.
- **No page names the server above its title** (item 92). The server's name is where it protects you: in the
  selector, in the machine strip, in the second column of an application and in every confirmation of a
  change to a machine.
- **One name for each thing** (item 107): application, deployment, version, backup, server, fleet, database
  server, scheduled task. A test holds the glossary, and the command palette finds a page by the name it had
  in 4.0 (Overview, Cron, Server, Recipes, Projects).

### A new look

- **The dry-stone identity** (items 86, 97): stone tokens in both themes; Atkinson Hyperlegible Next for the
  interface, Bricolage Grotesque for titles and JetBrains Mono for logs, commands and values that are code;
  three heights made of hard edges and one light from above (carved, laid, raised); one curve for each kind of
  movement; and a raised piece that slides to the tab, segment or item you chose. Colour still means state,
  and violet still means what you can press. Movement stops under reduced motion. What the design never draws
  is a list in [DESIGN.md](DESIGN.md), and a test fails on it.
- **The kit grew**: choice cards for options that have a description, a help popover that also works by touch
  and keyboard, logo tiles, a card tinted by its state, the fleet drawn as a course of slabs for each server
  (its applications, the ones with a problem first), and one restore dialog for whatever is backed up.
- **It is held by tests**: nothing is wider than a 390 px phone, nothing moves as a page loads (in Spanish
  too), every sentence the server says is in the console's language, and German, French, Brazilian
  Portuguese and Chinese have every text again.

### Pages that say less and show more

- **Home is one page for a server and for the fleet.** What needs attention is a card for each thing, with
  the one button that deals with it and the server it is on; an alert that fires on any server and a probe
  that is down count. The fleet is a course for each server, a server that does not answer says so at the
  head of its course, and an empty server gets its first steps (items 90, 107).
- **Applications are one list for the whole fleet**, with a server column and a strip to keep one server.
  The page of an application opens on what is live: its version with its commit and message, memory and CPU
  against their limits, the last deployments, certificate and uptime, where it answers and the end of its
  log. Every application shows the logo of its type.
- **One way to start an application** (item 99): a single box takes a repository link, a folder, an image or
  the name of what to install, and tells which it is. With a GitHub App connected its repositories are listed
  under the box; with nothing typed the popular recipes are. Compose, importing an export, adopting what
  already runs, GitLab, an archive, a pasted Dockerfile and a monorepo's workspaces are under "Other ways to
  start". A form whose button was drawn off without saying why now says what is missing.
- **A master switch is not one more row** (item 106): "Send notifications" governs the page, so it is in the
  page's header, and what it governs is drawn off while it is off.
- **Scheduled tasks are said in words** (item 107): every hour, day, week or month, or a calendar expression,
  with the next five runs shown on the server's clock before saving. A time limit is a checkbox that reveals
  the seconds, not a field whose emptiness means no limit.
- **Settings opens on a page of doors** in seven groups (people and access, notifications and channels,
  connections, security and frameworks, API, fleet, Noust), each with a line on what is inside and what is
  true now. A section is titled by its own name, and Settings finds a section by the name it had in 4.0.
- **Search covers the fleet**: with several servers the command palette also asks the central, and finds an
  application, a domain or a certificate on any of them, a server that is down included by its name.

## The fleet

### One view of everything (item 90)

- **Applications, databases, sites, certificates, backups, scheduled tasks, logs, alerts and activity each
  have one route under `/api/views`** that answers for the central and every server it reaches, the central
  first, with the server as a column and a filter rather than a place. A page of the console is one page
  whether it reads one server or all of them.
- **A server that cannot answer is a row, not an error.** One that is down is shown with its last answer
  and its age, one too old to have the route says so, and one that refuses you is counted once with the
  others that refused the same thing, with the permission that is missing and how to give it ("3 servers do
  not let you see their activity"). The JSON a server answered with is never shown to a person.
- **A search across the servers** (`GET /api/views/search`) finds a server, application, database, site,
  certificate or scheduled task by name, on whichever server it is.

### The fleet's settings, written once

- **Six kinds of setting are the fleet's**: notification channels, alert rules, backup destinations, image
  registry logins, shared variables and the address of the central's console. You save one on the central
  and every server applies it through its own typed route, so the server's validators judge it. The values of
  secrets go in and never come out (they show as `***` and are kept sealed on a sealed central).
  [central-operate.md](central-operate.md#settings-of-the-fleet) has the model.
- **Three levels for each server**: it follows the fleet; it has an exception (a value of its own set on the
  central, or one its operator changed on the server, which the central shows as differing from the fleet and
  can put back); or it is independent and is pushed nothing. A server that leaves keeps the last value it
  applied. Nothing that belongs to the machine (the web server, the firewall, SSH, ports, database engines,
  a server's accounts and audit) is ever pushed: the central shows a difference and the server decides.
- **Apply to the fleet** plans what would run, starts a fleet job and follows each server in its own state,
  with what failed in the server's words. A setting is also applied when it is saved and when a server
  answers after a silence.
- **Each change is signed by the person behind it.** A push with nobody in front of the console names the
  person and the change (`X-Noust-Actor: fleet-sync on behalf of <person> (change <id>)`), and a server takes
  that for sudo mode only for those six kinds and only under an `admin` ceiling. Taking over what a server
  already has, or undoing its operator's change, asks sudo mode.
- **A server before 4.1 is skipped as unsupported**, not failed, and shown as such.

### What a central lends

- **GitHub through the central** ([below](#sources-and-github)) and **Noust DNS on several servers from one
  action** ([below](#domains-dns-and-ionos)).
- **The central's permissions on a server are a page of that server**, shown as the server publishes them,
  with the command that sets another level on the server (`noust fleet access`): the ceiling is the server's,
  and nothing the central does can raise it.
- **The central sends a node what a person may do**, not the name of a role, because a role's name no longer
  means the same on two servers (item 96). The node crosses it with its ceiling.

## Roles and approvals

### One list of roles (item 96)

- **The five roles are rows of the list you edit.** Viewer, operator, admin, security and auditor come
  seeded and can be edited, duplicated and put back as Noust ships them (`noust access role duplicate`,
  `reset`), beside the roles you defined. The owner is the one protected role: it holds every permission,
  cannot be edited or deleted, and at least one account that can sign in must hold it.
- **A role you never touched follows Noust.** When a release adds a permission it receives it. A role you
  edited is not changed in silence: the console shows what a release offers it, and `noust access role
  offers NAME --accept|--dismiss` takes or leaves each permission.
- **An account holds several roles**, and what it may do is what they hold together (`noust user roles USER
  ROLE...`; `PUT /api/auth/accounts/{username}/roles`). The people table shows them all and "See what they
  can do" says which role gives each permission. The first account a server has is its owner, unless a
  framework keeps duties apart.
- **Frameworks judge the roles as they are defined.** ENS and the other frameworks that keep duties apart no
  longer trim the administrator behind the roles' back. The report names the role that joins governing the
  server with running it, `GET /api/roles/review` says what to split, and `single_role_per_account` allows
  several roles as long as together they do not join those duties.
- **Inviting a person asks for one address.** The account is named from it, and the name can be changed; the
  roles are cards with what each allows. The invitation is made whether or not mail can go, and the result
  says whether it did and why not.

### Approvals

- **Four more doors wait for a second person**, with the same rule and the same deciders as the others (a
  security officer, never an admin): a team's access (`POST /api/teams/{team}/access`), a member added to a
  team or given a higher role there, a permission added to a role (editing it, putting it back with what it
  had taken out, taking what a newer release offers), and what a sign-in source hands out (its default role
  and team, provisioning, linking by e-mail address, groups), always for one that does not exist yet.
  Removing a binding, a membership or a source, lowering a role and anything that changes nothing ask
  nobody.
- **Setting an account's roles waits too**, by `PATCH /api/auth/accounts/{username}` with `roles` and by
  `PUT .../roles`: any change counts, a reorder or a removal included, because the role an account is known
  by decides who it is to an approval.
- **Only an owner hands the owner role on**, through every route: setting roles, an access binding on the
  server, creating or inviting an account, and recovering an owner's account. Root at the terminal and the
  master token are still the way back for a server that lost its owners.
- **A request for an approval or a reason says which control asks and where it is turned off.** The owner of a
  server, holding every permission, read it as a problem with their permissions. It comes from the
  `approvals` and `change_reasons` controls, which apply to everybody; the console and the command line name
  the control and the command that changes it (`noust compliance set approvals off`).
- **A token limit you set counts without a framework** (`noust compliance set token_max_days 90`): the choice
  is recorded as `auth.tokens.enforce_max_days`, kept when a framework is taken off, and a number nobody
  chose still limits nothing.

## Recipes

- **The catalogue grows from 143 to 440 recipes** (item 98), many of them converted from Coolify's
  and Dokploy's templates by an importer that settles their files, mounts and credentials, and accepted only
  after the recipe started. Compose recipes are installed and checked in the harness one at a time and the
  result is kept: the catalogue says which recipes were started for real, with the version, the date and how
  long it took (`tested`; `noust recipe list --tested`). All 430 that can be installed were started; n8n is a
  Compose recipe with its PostgreSQL and a variant with workers, and Vault and Stalwart can now be installed.
- **A recipe that is not free software says so.** The 27 whose licence is source-available or open core carry
  the label and the licence's name on the tile, the recipe's page, the API (`free_software`) and the command line.
- **A recipe that is root on the server is offered, with a warning and a confirmation.** Portainer, Coder and
  Dozzle mount the Docker socket; OpenClaw runs commands for its user. Installing one needs
  `--accept-root-equivalent` (`accept_root_equivalent` in the API), sudo mode and the server's permission, and
  the tile says so before anyone opens the form.
- **What cannot be installed says why, in a sentence the console translates.** A recipe this release cannot
  install is listed after the ones that can, with a short label, the reason, what to do instead, the recipe
  that replaces it and, folded, what the check found as written. `unavailable_code` filters by the reason
  (licence, not for production, no image, not an application, host ports, archived, a platform's stack, no
  sign-in, root equivalent). BookStack, Portainer, Coder and OpenClaw, which 4.0 listed as unavailable, can be
  installed, and OpenBao joins.
- **Logos** (item 93): every recipe has a logo. Where the project's own logo may be shown it is the project's
  (the mark of the simple-icons collection, or a file from the project, sanitised and painted for each
  theme); where it may not, or there is no file that can ship, it is Noust's monogram. Each mark carries its
  licence and where it was taken from in `NOTICE` and the Debian copyright file, and a logo that is not safe
  never leaves the package.
- **Recipes are an origin of their own.** A server that names where code may come from
  (`security.allowed_sources`) and leaves recipes out refused every recipe, because Noust prepares the code
  in a folder it counted as a directory of the server. The word `recipes` allows them, `local` still covers
  them, `GET /api/recipes` says `recipes_allowed`, and the console offers "Allow the catalog's recipes".
- **n8n runs on Node.js 22**: the recipe pins it, since its `isolated-vm` does not build on the Node 20 a
  server may have.

## Databases and engines

- **One list of database servers** (items 79, 103). The engine installed on the system, the engines Noust
  runs in containers and the databases an application's stack runs are *database servers*, each with its
  version, where it runs, when its support ends and which databases it holds. One per engine is the
  **default** a new database goes to (`noust db servers`, `noust db servers default ENGINE@NAME`,
  `GET /api/databases/servers`). A database made with just the engine's name goes to the default; a version
  or a key (`mysql@legacy`) is always honoured over it.
- **Your databases and where they run are two things.** The page lists your databases (the server each runs
  on, who uses it, its size, whether it is backed up, one press to copy its connection) and, under them, the
  database servers as cards. A database the engine has and Noust does not manage shows **Add to Noust**, not
  a different kind of database.
- **Another version, beside the one installed.** "Another version" on a server opens the whole catalogue.
  MySQL 5.7 and 8.0, PostgreSQL 12 to 14, MariaDB 10.6 and MongoDB 6.0 run as instances pinned by digest once
  you accept that they have no security fixes (`--accept-unsupported`, `unsupported_ok` in the API); each
  says since when. They are never the default, and a prefix still means the supported version it meant.
- **An image of your own**, from your registry, is pinned by the digest read when the instance is created,
  or by the one you give.
- **Support in view.** Every version shows the date its project stops fixing it, and an upgrade opens the move
  to the recommended release.

## Domains, DNS and IONOS

- **IONOS is a DNS provider** (item 104): records and the certificate challenge, with an API key pasted whole as
  `prefix.secret`. The domain's DNS must be hosted by IONOS for the records to be seen.
- **Noust DNS: a name server of your own.** Noust installs Knot DNS on a primary and on secondaries in other
  networks; the zones stay in Noust's store and reach the secondaries through a catalog zone; DNSSEC is
  switched on for a zone and signed on the primary, so secondaries hold no key; it never answers recursion;
  and it is an option with its cost said first, never the default
  ([network-dns-server.md](network-dns-server.md)). The editor writes records from their fields, never from
  text, and a change Knot refuses leaves everything as it was. The delegation steps (glue, name servers at
  the registrar, DS) come in the order that works, with a check that says what is still missing.
- **From the command line, the API and the console**: `noust dns server` and `noust dns zone`,
  `/api/dns/noust/...`, and Domains > DNS. A primary and its secondaries are set up from the central in one
  action (`POST /api/fleet/dns/cluster`, a fleet job, primary first), and a server that did not allow host
  access is left out of the plan with the command that raises it.
- **Domains has its own sections**: names, sites, certificates, DNS, tunnels and published ports. "Add domain"
  asks which application, the name and where it points; the role, the port and the DNS record are under "More
  options".
- **A tunnel without cloudflared is a state of the page, not a form error.** `noust tunnel install-cloudflared`
  and the page's "Install cloudflared" add Cloudflare's own repository with its key pinned (the key must be
  exactly one public key with Cloudflare's fingerprint) and install it, in sudo mode.
- **Who holds each port** (`GET /api/server/ports`; Servers > Network and ports): every claim over TCP and
  UDP, in the product's words, and a search that answers "who uses port 5432".

## Changing the web server

- **An application moves between nginx and Apache with its site translated** (item 105). The site is read into
  what both servers share and written in the other's dialect; the target server tests every translated site
  inside its live configuration before anything is written; each site is then asked through the new server with
  the application's own health check, over HTTP and over TLS. A failure puts everything back in reverse order
  ([network-webserver.md](network-webserver.md)).
- **Nothing is hidden in the plan.** A line with no equivalent is named with its number, a protection that is
  lost (a rate limit, forward authentication, an address rule) has to be accepted, an operator's site is never
  rewritten, and what stops a site (Compose, zero-downtime, the console's own site) is said first.
- **Two kinds of move.** If the server you move to is not running, the move is a handover of every site at
  once, because moving some would leave the rest without a web server; if it already serves, a site moves on
  its own.
- **`noust webserver status|plan|migrate`**, `GET /api/webserver/status`, the plan and the move for one
  application or for every site, and Servers > Web server. The setting of new applications says it is that, and
  how many applications and sites are behind each server.

## Sources and GitHub

- **The GitHub App lives on the central** (item 90). A server with no App and no deploy key clones with an
  installation token the central leases it: read-only, narrowed to the repositories its applications follow,
  sealed on the server and renewed before it ends, so the App's key never leaves the central
  ([central-sources.md](central-sources.md)).
- **The central forwards the pushes.** GitHub's webhooks reach the central, which hands each event to the
  servers that follow that branch, tag or pull request. A server that was down gets it when it is back, for up
  to an hour; after that the delivery shows as expired and can be handed over again by hand.
- **The central writes the status of the commit and the comment of the preview**, with credentials a server
  never holds.
- **When the central goes quiet and a lease ends**, the error says exactly that, with the time, instead of
  "the repository is private". A deploy key or a credential of the server's own keeps working throughout.
- **The first application of a private repository** asks the central for access before it is created. The
  fleet's Connections page says how each server reaches its repositories, when that access ends and what
  keeps a server from having one, with "Renew now" and the pushes that did not arrive.
- **The webhook address is checked while you set the App up.** If GitHub cannot reach the central, the console
  says so with the two ways out (`noust web expose-hooks`, `web.hooks_url`).

## Notifications

- **A channel of the fleet goes only where its route can match.** One that names servers goes to those; one
  that names domains or tags goes to the servers where an application has one. A credential does not travel to
  a machine that could never use it, and the setting shows there as out of reach with the reason.
- **A server says when its central goes quiet.** The central's monitor says it keeps watch
  (`X-Noust-Heartbeat`), and a server that has not been called for `notifications.central_silent_minutes` (15
  by default; 0 turns it off) says so through its own channels, once, as `central_silent`. It goes on
  delivering its own notifications, and the central still says `node_unreachable` about the server.
- **One book of deliveries for the fleet** (`GET /api/views/deliveries`), newest first with each server. A
  server's links open its pages inside the central's console when you save the address of the console as a
  setting of the fleet (`notifications.console_url`).
- **The email account can be removed** like any other channel, and the question says that invitations and
  password-reset links use the same account.

## Start-up and structure

- **The command line starts in a fraction of the time** (item 88): `noust --version` goes from about 0.6 s to
  0.15 s and `noust --help` from about 1.6 s to 0.2 s. Command groups and packages import their modules on
  first use, and `--help` and Tab completion list the commands from a generated table of their summaries
  (`scripts/generate_command_summaries.py`). A test holds the number of modules imported at start to a budget.
- **Code scanning, decided** (item 87): the real findings are fixed in their cause and the rest decided; a job
  of the security workflow fails while one of high severity is open, and a CodeQL model pack teaches the
  scanner what the validators already guarantee ([code-scanning.md](code-scanning.md)).
- **The structure is held by tests** (item 88): the direction of the layers, no import cycles, the size of the
  largest modules to a ratchet that only goes down, and the names an installed server reads. The application
  directory, the deploy events, the rollback and the console's service left the modules they were in; the
  console's authentication is a package of fifteen modules instead of one of six thousand lines; each helper
  that was copied into dozens of modules is defined once.
- **The tests**: the Python suite runs in six slices on six runners (`pytest --shard K/N`), a test lets go of
  what it kept when it ends, every test is marked with its area (`pytest -m web`), what tests share lives in
  `tests/support`, and the console's tests wait for what they assert instead of for a time.
- **The documentation is in five tabs** (item 101): Start, Guides, Concepts, Reference and Releases, with
  Project beside them. Start takes someone who arrives with nothing from install to backups, troubleshooting is
  by symptom and quotes each message as the program prints it, and the longest guides are split into pages.
  `scripts/build_docs.py --emit` writes the whole documentation as one package with every link resolved, which
  the website draws, and the old addresses lead to the new pages.
- **Release**: the announcement waits until the files are signed, and the SBOM of the central's image is
  attested beside it instead of attached to the release.
- **Dependencies**: the workflows run on the current major version of each action, and the release keeps
  signing with cosign 2, whose separate signature and certificate the installer and the guides describe; the
  console moves to the current versions of its runtime and tooling, Base UI 1.9 among them. The console's
  accessibility checks run axe 4.14, which adds the Label in Name rule (WCAG 2.5.3).

## Fixes

### Found by running 4.0 on a fleet

- **`noust app identity migrate` reads where an application writes.** It only checked that the application
  answered. One that writes outside its tree into a folder the shared account owns would have passed and failed
  at its first upload, days later. It now reads the absolute paths of the `.env` and the unit first and refuses
  a folder only the shared account can write, naming it, who owns it and the ways forward: keep the data in
  `shared/`, or `--shared-group GROUP`.
- **`noust compliance disable ens-media --json` applied the change** with its input closed (see Before you
  upgrade).
- **The central's reachability probe saw nothing.** It asked as `noust (reachability probe)`, a label the
  client refuses (no spaces or brackets), so every round failed and no server was ever seen to answer or to be
  down by the probe; only a person looking at the fleet's page noticed. It asks as `noust-probe`.
- **`noust setup doctor` no longer calls a missing Node.js an error** on a server that runs nothing on it.
- **`noust health` no longer says a stack would not come back after a reboot** when its containers' restart
  policy says Docker starts it again; it names the containers whose policy would not.
- **Names an operator may choose are one rule** (`validators/object_names.py`). A probe's name refused an
  underscore its form said it took; probes, alert rules, channels, replicas and instances now ask one table,
  and the console checks a name before sending it.
- **`security.allowed_sources` could be broken by copying the command its own error suggested.** It stored
  `[github.com/acme` and `/path]` with the brackets and refused every deployment of the organisation's
  repositories. A list typed as text is read in one place, every entry is judged when the list is saved (a
  host, an organisation, a repository, or one of `local`, `push`, `upload`, `recipes`), and the suggested command
  is tested by running it.
- **A repository served over https on another port can be the source of an application.**
  `https://git.example.com:8443/team/app.git` (a GitHub Enterprise, a Gitea or a GitLab of your own) was
  refused, although ssh remotes with a port and the GitHub host setting both took one, so the source the
  GitHub integration writes for such a host could not be used. The port is read in one place and followed
  wherever an address is read: the credential a repository is cloned with is kept for its port and never sent
  to another, the webhook page is at the forge's own address, a push matches a repository on its host's port,
  and `security.allowed_sources` still names a host without one (a port is not compared). An address with a
  malformed host or port is refused, or matches nothing, where some readers raised.
- **Invitations**: the invitation is made even when mail cannot go, one address names the person and receives
  it, the error comes in the console's language, and the hint names Settings > Channels, the page's name since
  4.0.
- **The server's terminal says what keeps it off**, with the control and the exact command, and no longer
  blames an ENS profile that is not on.
- **Tunnels, About and the other pages that showed the server's English** inside a Spanish console now say the
  state in the console's language, with the output of a program kept verbatim.
- **Small ones**: `noust server security confirm` takes `-y`; a secret read from a pipe prints no warning that
  it may be echoed; the banner of an application that runs outside Noust no longer says what a restart would
  do.

### Services, deployments and alerts

- **A service its timer starts is not counted as stopped** between two runs: it is scheduled
  (`units.scheduled`, `state=scheduled`, `noust service list --state scheduled`).
- **A running deployment knows its commit** as soon as the source is fetched.
- **A person who acted through a central is named first**, in the console's language.
- **A firing alert, or a probe that is down, counts as needing attention** on its server.
- **A debug line no longer reaches a deployment's log.**

### Approvals (they existed in 4.0)

- **Doors a role grant went through without a second person.** With approvals on in 4.0, giving a team access,
  adding a member to a team or raising their role, widening a role you had defined and changing the roles a
  sign-in source hands out were done by one person. They follow the rule written above now, and a test fails
  on a route that gives someone more power and is outside the guard.

### Found by code scanning (item 87)

- **A name judged where its path is built.** An uploaded certificate's name or a lineage's name could point the
  database TLS plan, and the apply behind it, at any directory that held a `fullchain.pem` and a `privkey.pem`;
  the names of certificates, database instances and uploads are now judged by the function that builds the
  directory. The console's root files are served from a table of the files themselves.
- **A repository or group named `..` could move a request to another endpoint** of GitHub, GitLab, Gitea,
  Bitbucket or Coolify with the same credential. The names are judged once, with `fullmatch`, and every request's
  path is judged again where it is sent.
- **A restore drill is refused for a name the client did not give**, like every other route under
  `/api/apps/{domain}` (`.../shop.example.com:8080/restore-drills` queued a drill of `shop.example.com`).
- **A value in a log line cannot start another line or name the actor.** Every record reports its message on one
  line, and the audit bridge quotes every value.
- **Redacting a SQL statement takes time proportional to its length** (a statement of 32,000 tabs took four
  seconds, and the time grew with the square of the length).
- **One Jinja environment** renders every unit file, site and pool, instead of nine copies.
- **The probes of this machine never speak below TLS 1.2.** The health gate and the check of a site that moved
  between nginx and Apache ask through one client (`core/tls_policy.py`, beside the console's own floor); each had
  built its own, and a bare context leaves the version to the distribution's OpenSSL configuration.
- **Dependencies**: the Go provider's modules, the console's lock file, the Astro, Next.js, Nuxt and Remix examples
  and the central's image (system packages brought up to date, the example running as `nobody`) move past every
  known advisory. The central's image no longer carries pip: it vendors its own urllib3, msgpack and setuptools,
  which only a new pip updates, and a central runs from a read-only root file system and is updated by pulling the
  image.

### The console

- **Settings > Notifications fits a phone** and offers only the languages the server writes in.
- **Cancel on a new application no longer starts the read it stopped.**
- **The previews page decides whether a source is on GitHub by reading the start of the source**, not by finding
  `github.com/` anywhere in it.
- **The terminal takes the keyboard back when it is clicked.** The press on its screen gave the focus to the
  hidden text area, and the browser's own answer to a press on a canvas moved it to the page afterwards, so the
  terminal could not be typed in again with the mouse once the keyboard had gone elsewhere. The press is declined
  there; the text view, which needs it to select, keeps it.
- **Continue after a typed repository takes a press of any length.** Pressing it leaves the field, and 80 ms later
  the field's read drew a block above the button: the button moved some fifty pixels from under the finger, the
  release landed on something else and the press ended in no click (every hold from 90 to 300 ms, found in a
  browser). While a press on Continue is down, leaving the field reads nothing; the click is the ask. A read the
  field started when it was left another way is joined by a later press, and Continue is busy only for a read it
  asked for.
- **Typing an address in "What to deploy" no longer shows the catalog on the way** (`h`, `ht`, ... `https:/`): what
  can still become an address keeps the page as it was.
- **The stages of an update by push no longer run ahead of its log.** Its first line, "Building and activating a
  new release", read as the last phase it names and turned Start before anything was fetched.
- **A fleet job's title, and who started a job or a deployment, are said in the console's language and by
  name**: "Aplicar los ajustes de la flota en 2 servidores" in a Spanish console, "Marta Ruiz" and not "Console" or
  "Session marta", and "Noust itself" for what Noust does on its own (additive fields in the
  [API changelog](API-CHANGELOG.md)).
- **A server of the fleet is never drawn with none of its applications named.** Beyond two rows the well ones fill
  what is left of them by name, and one slab says how many more ("+2 running").
- **Spanish reads as it is said**: "Sitio estático", "Detección automática" and "Contenedor" for the types whose
  name is a description, "hace 1 día", "Desplegado" for a deployment that served, and a figure and its unit
  ("41 s") stay together instead of leaving a cell between them in the mono face.
- **An application's overview** gives its history of deployments the whole width, so a commit message is read and
  not cut, and the version that serves says when beside its number instead of after the message.
- **On a phone** the trail keeps the page and what it is under, whole; a card says its facts apart by room, with
  no mark that wrapped alone to a line; the primary action and the menu beside it wrap together.
- **A deployment that did not finish says so in the console's language.** "Release 20261031-2 did not pass its
  health check" reached the toast of the failed job and Home's list of what needs attention in English, in a
  Spanish console. The first line of the failure (the health check of a release, of an application or of a
  stack, a rollback that did not answer, an installation or a build that failed, ran out of memory or took too
  long) is now a sentence with a code, built where each is raised and read back from what a job and a
  deployment keep; what the gate, the journal and the tools printed after it is shown as it came (additive
  fields in the [API changelog](API-CHANGELOG.md)).
- **"Reconocer" is "Marcar como vista"**, and "Reconocida por" is "Vista por": an alert is taken, not recognised.
- **The step from the wizard to the deployment draws one picture, not three.** Pressing Deploy drew the next
  page's second column empty beside the wizard, then the page in its loading shapes, then the page. The frame now
  follows the page that is on screen, and the wizard reads what the page draws first (the application, its
  deployments, the deployment and its log, the page's code) before it leaves.
- **Choosing a result in the search no longer says "nothing matches" while it closes.** The search kept no
  commands once it was shut, and the dialog is on screen for the length of its exit.
