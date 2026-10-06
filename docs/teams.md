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
noust environment create Staging --team acme --project mobile-app
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
DELETE     /api/teams/{team}/projects/{project}
GET|POST   /api/teams/{team}/projects/{project}/environments
DELETE     /api/teams/{team}/projects/{project}/environments/{environment}
GET    /api/projects?team=       and   GET /api/environments?team=&project=     flat lists
POST   /api/apps/{domain}/move  {"team":..., "project":..., "environment":...}
POST   /api/auth/invitations    {"username":..., "role":..., "team": "acme", "team_role": "operator"}
```

`GET /api/apps`, `/api/sites`, `/api/databases/databases`, `/api/backup-schedules` and the lists of
history take `team`, `project` and `environment` filters where they list resources, and say where
each application lives (`team`, `project`, `environment`). `POST /api/apps`, `POST /api/sites` and
`POST /api/databases/databases` take the same three to say where the new resource goes; left out,
it goes to **the team you work in** (`PUT /api/teams/active`), your only team, or the default team
when you see them all. A member of several teams that has not chosen one is asked to.

Adding a person to a team needs an account that exists. A new person is invited by whoever manages
accounts (`POST /api/auth/invitations` with `team`), because creating a credential is the security
role's decision, not a team's.

## What a team member cannot reach

Anything that is about the whole server is closed to an account that does not see every team, and
answers `403` with `"error": "instance_scope_required"`: the server's own pages (`/api/server`,
`/api/system/processes`, `/api/services`, `/api/cron`, `/api/monitor`, `/api/metrics`, the
overview and timeline), backups and their destinations, certificates in bulk, the GitHub App, the
fleet and every node, an adopted Compose stack and an import of an exported application. These
are what 3.4 does not yet filter by team; the pages come to a team member as each is filtered.
Database engines, users and dumps are also the server's; the databases a team owns are its own.

## Audit

Every change is on the audit trail with whoever made it: `team.create`, `team.update`,
`team.delete`, `team.member.add`, `team.member.remove`, `project.create`, `project.delete`,
`environment.create`, `environment.delete` and `apps.move`.

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
