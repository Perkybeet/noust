# Teams, projects and environments

A server can be shared. Teams (3.4) are how: a **team** groups people, a **project** groups what they
run, an **environment** is a stage of a project (production, staging). Every application, site,
database and backup schedule belongs to one project and one environment, and a person sees the
teams they belong to and nothing of the others.

```
team  >  project  >  environment  >  applications, sites, databases, backup schedules
```

A server nobody organised in teams behaves as it always did: an upgrade creates the team `default`,
with the project `default` and the environment `production`, puts every existing resource in it, and
makes every account that can be a member one. Nothing changes until a second team exists.

**A team is a boundary of permissions and of organisation, not of execution.** The applications of two
teams still run on one kernel, under accounts of their own but on the same machine. A team that must
not share a host with another needs its own server (see [CENTRAL.md](CENTRAL.md)). A team's admin can
run its applications, create sites and databases for them and read their secrets; it cannot touch the
server, raw nginx or systemd configuration, cron commands or anything else that runs as root.

## Who sees what

| Principal | Sees |
|---|---|
| The master token, a central's fleet token, a token nobody owns | Every team |
| An `admin`, `security` or `auditor` account (instance roles) | Every team |
| A `viewer` or `operator` account | The teams it belongs to; every team there is when it belongs to all of them |
| An account that belongs to no team | Nothing |

What a principal does not see **reads as absent**: the same `404` as for something that does not
exist, never a `403` that would say it does. That holds for lists, for one resource, for a change and
for a delete, and for the live feeds (the event stream, journals and job logs).

`security` and `auditor` are **instance-only roles** and never join a team: an auditor that belonged
to one team would be an auditor of one team. The two roles that govern access and check it work
across all of them.

## Roles

The five roles of a server (`viewer`, `operator`, `admin`, `security`, `auditor`) are now
**instance roles**: what an account holds outside any team, and the floor of what it holds inside
one. A team adds four roles, mapped onto the same permission sets and cut to what a team can own:

| Team role | Holds in the team |
|---|---|
| `viewer` | Reads applications, databases, backups |
| `operator` | Also restarts, deploys, rolls back, takes backups |
| `admin` | Also creates, configures and deletes applications, sites, databases; reads secrets; manages backups |
| `owner` | Also governs the team: its members, projects and environments |

`GET /api/teams/roles` lists them. No team role holds `root_equivalent`, `server.*`, `settings.*`,
`fleet.*`, `accounts.*`, `audit.*` or `security.manage`. An account that is `viewer` on the server and
`admin` of `acme` can manage `acme`'s applications and only read the rest; one that is `operator`
on the server keeps that in every team it sees. The last owner of a team cannot leave it.

## Roles where they are needed

A membership gives a role in a whole team. Often that is too wide: the contractor who deploys the
staging environment of one project, the auditor-in-training who reads one project. A role can be
**given at a scope**: an environment, a project, a team or the whole server, and it holds only there.

```
noust access grant maria --role admin --team acme --project web
noust access grant ci-bot --role release-manager --team acme --project web --env staging
noust access list
noust access revoke 7
```

It is Settings > Roles and access in the console and `GET|POST /api/access/bindings` in the API
(`accounts.read` to look, `accounts.manage` to change, sudo mode, and a second person's approval
where approvals apply). What an account holds on a resource is the union of what it holds on the
environment, the project and the team that contain it, and on the server, with the floor of its own
role: the same person administers one project and only reads another.

**Roles of your own.** The built-in team roles cannot change. A custom role is a set of permissions
that exist (`noust access permissions` lists them, with what each means), defined once and given at any
scope that can carry it:

```
noust access role create release-manager --permission apps.read --permission apps.deploy \
    --label "Release manager"
```

* A scope narrower than the server holds only what a team can own: a role with `apps.*`, `databases.*`
  and `backups.*` can be given on an environment, a project or a team; one that also governs a team's
  members (`teams.manage`) only on a team or the server; anything that reaches the machine, only on the
  server. The console says where a role will be usable while it is defined.
* Where the separation of duties is asked for (ENS, ISO/IEC 27001 or `security.separation_of_duties`;
  see [COMPLIANCE.md](COMPLIANCE.md)), a role never mixes **governing** (`accounts.manage`,
  `security.manage`, `audit.manage`) with **running** the server: the rule that keeps one person from
  holding a `security` and an `admin`
  account keeps one role from being both. An account cannot be given a role that would make it do both
  at once, and an `auditor` holds nothing more.
* Where the `single_role_per_account` control is on (ENS), an account has one role: a role on the whole server is refused, and the
  same account is given the role on a team, a project or an environment instead.
* A role someone holds cannot be deleted: revoke it first. Redefining it re-checks its holders.
* Every change is an audit event (`access.role.create`, `access.role.update`, `access.role.delete`,
  `access.bind`, `access.unbind`).

**Roles given by a group.** An identity source can give a role at a scope to everyone in one of its
groups, so that "contractors on the web project" is a group at the provider and not a list kept here
([sso.md](sso.md#groups)): `noust auth oidc|saml|ldap|scim set NAME --group-sync --map-binding
release=release-manager@acme/web` (`GROUP=ROLE@TEAM[/PROJECT[/ENVIRONMENT]]`; built-in or custom roles,
never `security`, `auditor` or `owner`). The binding is recomputed at every sign-in (and as a SCIM
provider pushes the group), marked with its source (`oidc:corp`, `saml:entra`), and taken back when the
person leaves the group. A binding an administrator made by hand is never touched.

**A scope is a project, not an application.** To give a person one application only, put that
application in a project of its own (`noust app move`), which also gives it a place of its own in
lists and tokens. Roles on a single application are not offered: a second kind of boundary would be a
second place to forget.

**Tokens bound to a scope.** An API token can be bound to a team, a project or an environment when it
is made, so a client's token sees that and nothing else of the server:

```
noust token create client-ci --scope deploy --owner maria --team acme --project web
```

A bound token belongs to an account (the access token, which is nobody's, cannot issue one). Inside its
scope it holds at most what its owner holds there, cut to what a team can own: it never reaches the
machine, even for an administrator's. Outside, it holds a viewer's floor of what its owner holds, and the
resources of other teams and projects read as absent (`404`), like anything else a principal does not see.
The console's token dialog has the same choice (API tokens > Create token > What it can reach), and the
token list says where each one reaches.

## In the console

Everything below the API is a page of the console, in English and Spanish, and a viewer never
sees a button it would be refused.

- **The team switcher** is in the top bar, beside the server selector. It is drawn once there is
  something to switch (a second team, or a team that is not `default`): a server nobody organised
  looks exactly as it did. It says the team the console works in (or "All teams", which an
  administrator and anyone in several teams has), narrows the Applications, Databases and Sites
  lists to it, and is where a new application goes. An account's choice is remembered by the server
  (`PUT /api/teams/active`); the access token has no account, so its choice is kept in the browser.
- **Teams** (`/teams`) lists the teams with the operator's role in each and says plainly that a team
  is not a separate machine. A team's page has **Overview** (what it holds, its projects),
  **Members** (add an account, change a role in place, remove, leave, and invite a person through
  the accounts' invitation) and **Settings** (rename; delete, with the short name typed, refused with
  its reason while the team holds anything). Only an administrator creates a team.
- **Projects** (`/projects`) lists projects with their environments and what each holds. "New
  project" is two clicks (the button, then a name) and opens the project, with `production` ready.
  A project's page shows its environments as a segmented control, production first, and what lives
  in the one on screen grouped by kind. "New environment" is two clicks; renaming and deleting an
  environment or a project are one menu away, deleting guarded where the server would refuse (the
  last environment, anything that holds something, the default project).
- **The lists say where a row lives.** With more than one project or environment in view, the
  applications, databases and sites lists gain Project and Environment filters (in the URL: `?project=web&environment=staging`)
  and columns; with one of each they draw neither.
- **Moving an application** is "Move to another project..." in its row menu and "Move" beside
  "Where it lives" in its settings: pick the team, project and environment, and its sites,
  databases and backup schedule go with it, restarting nothing.
- **The new-application wizard** asks which project and environment only when there is more than one
  choice, starting from the team's default project and production, so the click budget of a public
  repository (New application, Inspect, Continue, Deploy) does not change. A project's page starts it
  on that project (`/apps/new?team=acme&project=web&environment=staging`).
- **Shared variables** are a **Variables** tab on a team's page, the **Project variables** and the
  environment's section on a project's page (they follow the environment on screen), and Settings >
  Variables for the server's: defined once, referred to from the applications with `{{team.NAME}}`,
  `{{project.NAME}}`, `{{environment.NAME}}` or `{{server.NAME}}` ([variables.md](variables.md)).
- **The command palette** finds teams and projects, and offers "New project" and "New team".

## From the command line

```
noust team create "Acme Corp" --slug acme       # a team, with project default and environment production
noust team list
noust team add-member acme maria --role owner    # an account that exists
noust user invite pablo --role viewer --team acme --team-role operator   # a person that does not
noust user create ana --role viewer --team acme --stdin                  # an account in the team only
noust team members acme
noust team remove-member acme pablo
noust team rename acme "Acme Inc"
noust team delete acme                           # only an empty one; the default team is kept

noust project create "Mobile app" --team acme    # also gets its production environment
noust project list --team acme
noust project rename mobile-app "Mobile" --team acme   # the display name; the short name stays
noust environment create Staging --team acme --project mobile-app
noust environment rename staging "Pre-production" --team acme --project mobile-app
noust environment list

noust create -d shop.acme.com -s ... --team acme --project mobile-app --environment staging
noust app move shop.acme.com --team acme --project web --env staging
noust list --team acme --project web             # also: noust db list, noust site list,
                                                 # noust backup schedule list
```

`noust env` is the variables of an application's `.env` (since 1.x), so the stages of a project are
`noust environment`.

An account made with `noust user create` or `noust user invite` and no `--team` joins the default
team, as every account was before teams. A person brought in for a team (`--team`) belongs to that
team only. A security officer or an auditor is refused a team with a message that says why.

Moving an application takes with it its sites, the databases linked to it, its backup schedule and
the previews of it, in one transaction. Nothing is restarted: where an application lives is a matter
of organisation, not of how it runs. What is left out of the command stays: `--env staging` moves
within the project, `--project mobile` goes to that project's production, `--team globex --project web`
to another team's.

### Several environments of one app

A project holds as many environments as its stages (production, staging, a client's pre-production),
and the same application is often in more than one. Three things are made for that:

- **Promote** puts the release that serves in one environment on the same app in another, **without
  building it again**: the release directory is copied to the target's releases, linked to the
  target's own `.env` and persistent paths, its `pre_deploy` hooks run, and it is activated behind
  the target's health gate. A release that does not answer is replaced by what served before. The
  target is an application of the same project, in another environment, built from the same
  repository and of the same type, on the release layout. Going back past a deployment of the target
  that changed its database's schema is refused unless confirmed (`--schema-changed-ok`).
- **Clone** makes another application with this one's settings: variables (references kept as
  written, so `{{app.URL}}` is the clone's own address), hooks, build block, tags, cron jobs, backup
  schedule and how its previews are made. It goes to the original's project and environment, or to
  the one named, and is deployed from the same source (or `--source`), built and checked like any
  other. Its data is not copied, and neither are the names the original also answers on. When the app
  has a database the clone **must** be told what to do with it: `empty` makes one of its own and gives
  it none of the original's connection settings, `share` uses the original's (and writes into its
  data). A cron job or a backup schedule belongs to the server, not to a team, so they are copied only
  when the person who clones sees the whole server.
- **Move** (above) changes where an application lives without changing how it runs.

```bash
noust app promotion shop-staging.acme.com                 # what serves, and where it can go
noust app promote shop-staging.acme.com --to shop.acme.com
noust app clone shop.acme.com shop-staging.acme.com --environment staging --database empty
```

In the console: **Promote to another environment** and **Clone application** are in an application's
menu in the list and in its header's more actions. To put a copy on another server, export the
application there (`noust app export --with-secrets`) and import it on the other.

### Tags on applications

An application carries up to 16 free **tags** (`web`, `client-acme`: 1 to 32 letters, digits, dots,
underscores or dashes). They group applications across projects, filter the lists, and let one call
update a whole group.

```bash
noust app tag add shop.acme.com web client-acme
noust list --tag client-acme
noust app tag deploy client-acme --force        # update every app that carries it, one after the other
curl -X POST -H "Authorization: Bearer $TOKEN" -d '{"force": false}' https://noust.example.com/api/tags/client-acme/deploy
```

Updating by tag is `Update` for each application (it pulls its source and builds a new release), so
an app with nothing new is skipped unless `--force`. The API call needs only `apps.deploy`, which is
what a pipeline's token holds, and answers what came of each application. Only the applications the
caller sees are reached. In the console: the list's **Tag** filter, **Update tagged apps** when it is
narrowed to one, and **App tags** in an application's General settings.

Databases and services carry tags too, with the same rules, so one label can mark the pieces of a
customer or of a stage:

```bash
noust db tag add shop --engine postgresql client-acme
noust db list --tag client-acme
noust service tag add worker client-acme       # the server's units: the instance's, never a team's
noust service list --tag client-acme
noust app tag restart client-acme              # restart every application and service that carries it
curl -X POST -H "Authorization: Bearer $TOKEN" -d '{"dry_run": true}' https://noust.example.com/api/tags/client-acme/restart
```

Restarting by tag reaches what the caller sees: a team's applications, never another team's, and the
server's units only for the instance. It restarts each one the way its own restart does, one that
fails does not stop the others, and `dry_run` (the global `--dry-run` on the command line) says what
would happen first. A database that carries the tag is listed and left: a database is restarted with
its engine, which every other database on it shares. The API call needs only `apps.operate`, the
permission a restart of a single application asks, so a pipeline's token can hold it. A database is
tagged with `databases.write` (a team's own databases, 404 for another team's); a service with
`server.manage`, because the tag chooses what a restart reaches.

## From the API

```
GET    /api/teams                                   the teams you see, with your role in each
POST   /api/teams                                   create one (an administrator)
GET    /api/teams/mine                              what you belong to, and the team you work in
PUT    /api/teams/active   {"team": "acme"}         choose the team you work in (null forgets it)
GET    /api/teams/roles                             what each team role holds
GET    /api/teams/{team}                            one team, with what it holds
PATCH  /api/teams/{team}   {"name": "..."}          rename it (an owner)
DELETE /api/teams/{team}                            delete an empty one
GET|POST   /api/teams/{team}/members                list; add an account (an owner)
PATCH|DELETE /api/teams/{team}/members/{username}   change its role; remove it
GET|POST   /api/teams/{team}/projects               list; create (an owner)
PATCH|DELETE /api/teams/{team}/projects/{project}   rename; delete an empty one
GET|POST   /api/teams/{team}/projects/{project}/environments
PATCH|DELETE /api/teams/{team}/projects/{project}/environments/{environment}
GET    /api/projects?team=       and   GET /api/environments?team=&project=     flat lists
POST   /api/apps/{domain}/move  {"team":..., "project":..., "environment":...}
POST   /api/auth/invitations    {"username":..., "role":..., "team": "acme", "team_role": "operator"}
```

`GET /api/apps`, `/api/sites`, `/api/databases/databases`, `/api/backup-schedules` and the lists of
history take `team`, `project` and `environment` filters where they list resources, and say where
each application, site and database lives (`team`, `project`, `environment`; a site file or a
database the store never recorded belongs to nobody and has none). `POST /api/apps`, `POST /api/sites` and
`POST /api/databases/databases` take the same three to say where the new resource goes; left out,
it goes to **the team you work in** (`PUT /api/teams/active`), your only team, or the default team
when you see them all. A member of several teams that has not chosen one is asked to.

Adding a person to a team needs an account that exists. A new person is invited by whoever manages
accounts (`POST /api/auth/invitations` with `team`), because creating a credential is the security
role's decision, not a team's.

## Notifications of a team

A team keeps its own notification channels (Slack, ntfy, Telegram, a webhook...) and they hear the
applications of that team only. See [notifications.md](notifications.md#a-teams-own-channels):
`noust notify channel set NAME --team acme ...` and `/api/teams/{team}/notifications/channels`.

## Servers of the fleet

A central's servers (see [CENTRAL.md](CENTRAL.md)) are the instance's until one is **assigned to a team**.
The team then sees the server in its server selector, reads its status and tests its connection, and
works on it through the central: its members open the server's console pages (applications, sites,
databases, backups, logs, jobs) as they open the central's own. Every other team reads the server as
absent (`404`, the body of one that is not registered), and so does any account that belongs to no team.

```
noust node assign web-2 --team acme       # the administrator of the central
noust node unassign web-2                 # back to the instance
noust node list                           # the Team column
PUT /api/nodes/web-2/team   {"team": "acme"}     # null gives it back (fleet.manage, sudo mode)
```

What a member does on a team's server is held twice, by the central, before the call is forwarded:

- **The permission is held in the server's team.** An account that administers `acme` and only reads
  `globex` deletes applications on the `acme` server and is refused on the `globex` one, even though
  the union of its roles would allow it.
- **Nothing about the machine.** The server takes the central's token for the instance, so the central
  applies to the server's own path the table that already holds a team member on the central: the
  server's services, cron jobs, processes, configuration, backups and the rest of what is "the server's"
  answer `403` with `instance_scope_required`. A path the central cannot classify (a server newer than
  the central) is refused with `scope_undeclared`. The server is also told the widest role the member
  holds in full *in that team* (a team administrator is an `operator` there, a team viewer a `viewer`),
  so it refuses what only its own administrators may do, creating or deleting applications included:
  an administrator of the central does that, or the server's own console.

How the central reaches a server (its key, SSH options, bastion, inventory, monitoring pause), adding and
removing servers, labels, placements across servers and the fleet's aggregate views stay the
administrator's. A team that is given a server cannot be deleted until the server is given back, and
a server removed from the central leaves its team with it.

## What a team member cannot reach

Anything that is about the whole server is closed to an account that does not see every team, and
answers `403` with `"error": "instance_scope_required"`: the server's own pages (`/api/server`,
`/api/system/processes`, `/api/services`, `/api/cron`, `/api/monitor`, `/api/metrics`, the
overview and timeline), backups and their destinations (database dumps, data volumes and Noust's
own backup, with the policies that schedule them: each writes a root timer and names a destination
the server owns), certificates in bulk, the GitHub App, the fleet's own views and the way the central reaches each server, an adopted
Compose stack, an import of an exported application and the install of a recipe (create the
application through `POST /api/apps` with a recipe instead). These are what 3.4 does not yet
filter by team; the pages come to a team member as each is filtered. Database engines (the
instances Noust runs in containers beside the host's included: making, upgrading and removing one),
users, imports and dumps are also the server's; the databases a team owns are its own, on whichever
engine, and a team member picks an instance for its application's database through the choices.

Three more are the server's though the route around them is a team's:

- **Opening a database to the network** (`POST /api/databases/public`, and its plan) opens a port,
  makes an account on the engine and edits the firewall, whoever's database it is. A team member
  sees which of its own databases are open, reads how to connect, reveals the account's password
  and closes one; opening is the administrator's.
- **Installing an engine** as part of creating a database (`install: true` on `POST
  /api/apps/{domain}/databases`) is a package install as root, like the engines' own install
  route, and is refused the same way. Which engines and versions are on offer
  (`GET /api/databases/provisioning/choices`) is a catalogue anyone may read.
- **The host keys Noust pinned** the first time it reached each code host
  (`/api/deploy-keys/hosts`) are the server's own trust in the network.

## What an application carries with it

An application's build block (`/api/apps/{domain}/build`), its container settings (`/container`),
the recipe it was installed from (`/api/recipes/installed/{domain}`), its deploy key and its git
credential are reached through the application. Another team's reads as absent (`404`, the body of
one that does not exist) for every method, before sudo mode or the handler, and the permission is
checked in the application's own team. `GET /api/recipes/installed` lists the installs of the
applications you see.

A table another area adds for something an application or a database owns has to say how the scope
reaches it: `tests/test_scope_architecture.py` fails until it does.

## Deploy keys and repositories

A deploy key and an application's https credential belong to a **repository**, not to a team:
git, a fetch in the cache and a webhook know the repository's address and nothing else. So two
applications built from the same URL use the same key whichever team they are in, and so does any
application that names a repository the server's own key (`noust setup ssh`) can read. That is a
consequence of teams sharing a host, as in the first section, and no filter changes it.

What a team member is shown of that is limited to its own applications:

- `GET /api/deploy-keys` lists the keys that reach a repository one of its applications is built
  from, and each one names only those repositories and those applications. A key that reaches
  another team's repositories alone does not exist for it.
- An application's `shared_with` names only the applications the member sees.
- Assigning an existing key (`POST /api/apps/{domain}/deploy-key` with `key`) needs a key the
  member was shown. Another team's key, named by guessing, is answered as a key that does not exist.
- Rotating a key (`POST` and `DELETE /api/apps/{domain}/deploy-key/rotate`) replaces it for every
  repository it reaches, so a member is refused when the key also reaches a repository none of its
  applications is built from; an administrator of the server rotates it. The member can take the
  repository off the shared key and generate one of its own.

## Audit

Every change is on the audit trail with whoever made it: `fleet.node.team`, `team.create`, `team.update`,
`team.delete`, `team.member.add`, `team.member.remove`, `project.create`, `project.delete`,
`project.update`, `environment.create`, `environment.update`, `environment.delete` and `apps.move`.

## For a developer

- `noust.core.teams` holds the model, `TeamStore` (the one reader and writer of the tables),
  `Scope` and `ScopedStore`. The store's own `get_app`, `list_sites` and the rest remain for what runs
  as the instance (jobs, the monitor, the CLI as root); **a router never calls them for these
  resources**: `tests/test_scope_architecture.py` fails when one does.
- `noust.web.permissions.scopes` says what every route is about: a resource (checked, 404 before the
  handler), a list or a creation (the handler asks the scoped store), an open route (nothing of any
  team's) or an instance route. A route no rule names fails the same test.
- The filter is in the SQL (`visible_clause`), not applied to what came back, so a list is never
  short of rows a hidden one displaced.
- A new resource gets its project from `noust.core.teams.ownership`: its application's, else the one the
  caller bound (`ownership_target`, which a job carries from the request), else the default.
