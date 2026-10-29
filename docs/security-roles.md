# Roles, permissions and the fleet

Part of [Security](security.md).

## Authorization

### Roles and permissions

Every API route declares the one permission it needs, in one map per area
(`noust.web.permissions.routes_<area>`); the authentication chokepoint enforces it on every
request, and the OpenAPI schema publishes it as `x-noust-permission`. A route missing from the
maps fails the test suite and is refused at runtime. Roles are sets of these permissions, stated
once in `noust.web.permissions.roles`:

| Permission | Governs | viewer | operator | admin | security | auditor |
|---|---|---|---|---|---|---|
| `apps.read`, `server.read`, `databases.read`, `backups.read`, `fleet.read`, `settings.read` | Seeing everything, secrets redacted | yes | yes | yes | yes | yes |
| `apps.operate` | Start, stop, restart, renew certificates, run cron jobs now | | yes | yes | | |
| `apps.deploy` | Update, roll back, activate a release, rebuild a deployment | | yes | yes | | |
| `backups.run` | Take and verify backups | | yes | yes | | |
| `apps.manage` | Create, configure and delete applications, sites, certificates, domains | | | yes | | |
| `secrets.reveal` | `.env` in clear, connection strings, exports with secrets, command lines of processes | | | yes | | |
| `root_equivalent` | Raw units, cron commands, backup hooks, raw site configuration, an application's deploy hooks, building as root, moving an application to its own account | | | yes | | |
| `server.manage` | Updates, reboots, storage, swap, time, host name | | | yes | | |
| `server.host_access` | SSH keys and sshd, the firewall, shutting down | | | yes | | |
| `databases.write`, `databases.manage` | Create, change and delete databases, users, rows | | | yes | | |
| `backups.manage` | Restore, delete, destinations, schedules | | | yes | | |
| `fleet.manage` | Add and remove servers, labels, bulk actions | | | yes | | |
| `settings.manage` | Noust's settings | | | yes | | |
| `security.manage` | The console's access settings, whole-configuration writes, accepted risks, unlocking a sealed central | | | | yes | |
| `accounts.read`, `accounts.manage` | The account list; accounts, roles, invitations | | | | both | read |
| `audit.read`, `audit.manage` | The whole audit log; its retention and destinations | | | | both | read |
| `compliance.read` | `noust ens check` and `report` | | | yes | yes | yes |

- **By default an `admin` holds every permission** above, including `security.manage`,
  `accounts.*` and `audit.*`: a person running their own servers needs one account, not three.
  The columns above are the matrix the ENS review decided and the one in force where the
  `roles_split` control is on (ENS, ISO/IEC 27001 and SOC 2 ask for it): there `security` and
  `auditor` govern and `admin` does not. The server decides what a role holds, by its own controls
  (`noust.web.permissions.roles.permissions_for_role`); a central forwards a role *name* and a node
  grants what its own profile gives it. Nobody changes or elevates their own account, whatever the
  profile: another administrator, or `root` at the terminal, does.
- **Separation of duties.** Where the `separation_of_duties` control is on
  (`security.separation_of_duties`, or a framework that asks for it), one person (`person_ref`)
  may not hold `admin` or `operator` together with `security`, nor `auditor` with any other role.
  `noust user exception add` records a documented exception, with a reason and an end date, for a
  small installation with one responsible person. Off by default.
- **Credentials that are not accounts.** The master token holds everything while no account
  exists, everything but `audit.manage` afterwards, and only account recovery where the
  `master_token` control is on (ENS MEDIA and others ask for it). A 3.0 token keeps its scope: `read` is a viewer, `deploy` a viewer that may also
  deploy, `admin` everything. An unknown role or scope is worth a viewer's permissions, never
  more.
- **Sudo mode and approvals come on top of the permission**, never instead of it.
- **Teams narrow what a role reaches, never widen the server.** The five roles are instance roles
  (4.0): `admin`, `security` and `auditor` see every team; a `viewer` or `operator` sees the teams
  it belongs to. See [Teams](#teams).
- **Roles are held where they are given** (4.0). A role can be given on an environment, a project,
  a team or the whole server, and holds only there; roles of your own can be defined from the
  permissions above, which never mix governing with running the server. Under the ENS profile an
  account has one role and none is given on the whole server (the `single_role_per_account`
  control, which a framework can ask for). All of it is checked in one place
  (`noust.web.permissions.access`), whoever asks: the console, the API or `noust access`. See
  [Roles where they are needed](teams.md#roles-where-they-are-needed).
- **Passwords can be turned off for single sign-on** (`auth.local_login.enabled`): only the accounts
  kept as the way back sign in with a password or a passkey, and the change is refused while it would
  leave nobody able to administer. See [Single sign-on](sso.md#turning-passwords-and-passkeys-off).
- **A token can be bound to a team, a project or an environment** (`noust token create --team ...`): it
  sees that and nothing else, even for an administrator's, and never reaches the machine.

### Teams

A team groups people and the projects they run; every application, site, database and backup
schedule belongs to one project of one team ([teams.md](teams.md)). The boundary is enforced where
the data is read, not where it is asked for:

- **The filter is in the data layer.** Routers read these resources through
  `noust.core.teams.ScopedStore`, which puts the principal's teams in the SQL; the store's own
  readers remain for what runs as the instance. `tests/test_scope_architecture.py` fails when a
  router reads one directly, when a route carries no scope guard and when a route no rule in
  `noust.web.permissions.scopes` names.
- **A resource outside the principal's teams reads as absent**: `404` with the same body as one that
  does not exist, for a read, a change and a delete, before sudo mode, approvals or the handler
  are reached. Never `403`, which would say it exists. Holding a permission in one team gives
  nothing in another: the permission is checked against the resource's own team.
- **A team role never reaches the machine.** `viewer`, `operator`, `admin` and `owner` of a team hold
  the permissions of the instance roles cut to applications, databases and backups; none holds
  `root_equivalent`, `server.*`, `settings.*`, `fleet.*`, `accounts.*`, `audit.*` or
  `security.manage`, and a route about the whole server is refused to a principal that does not see every
  team (`instance_scope_required`). A team admin who could write a raw nginx directive or a systemd
  unit would be root, and the filter would be decoration.
- **`security` and `auditor` never join a team**, so the separation of duties does not depend on a
  team's owner; an account that becomes one leaves its teams.
- **The master token, a central's fleet token and a token nobody owns act as the instance** and see
  every team; a token an account owns carries the teams of that account and never more than it.
  A node does not receive a team's scope from its central (the central reaches nodes as the
  instance): a principal that does not see every team is refused the node proxy.
- **What a team does not defend against.** Teams share a kernel, a web server, a database engine and
  a port range; a name taken by another team (a domain, a database) is a conflict whoever owns it,
  which tells the caller it is taken; a team's admin can create a site that proxies to any local
  port. Use a server per team where that matters.

Creating an application and inspecting a source are `apps.manage`: both fetch whatever source
the caller names and build or read it. Process listings (`GET /api/system/processes`,
`GET /api/monitor/processes`, monitor observations, the busiest processes in
`GET /api/timeline`) show command lines, which often carry passwords, only with `secrets.reveal`;
everyone else sees the process name. The timeline keeps each source's own permission: the journal
needs `secrets.reveal`, the audit trail `audit.read`, and a source the caller may not read is
named as withheld with the permission it needs.

**Noust's own units.** The console (`noust-web`), the monitor (`noust-monitor`) and the
`noust-cron-*` and `noust-backup-*` units behind cron jobs and backup schedules cannot be
started, stopped, enabled, disabled, rewritten, created or deleted through `/api/services`,
whatever the credential: an admin could otherwise stop the console it is talking to. The console
and the monitor are managed on the machine with `noust web ...` and `noust monitor ...` (and the
monitor from Server > System), and scheduled work through its own API. Reading the console's or
the monitor's journal, through `GET /api/services/{name}/logs` or `/ws/logs/{name}`, needs an
admin: the console logs the verbatim output of failed git, certbot and notification calls, and
the monitor what it saw of other processes. An application's journal needs `apps.read`.

**Local paths.** Creating an application from, or inspecting, a directory on the machine
rather than a repository URL reaches every file on it. It is accepted from the master token,
and from a console session in sudo mode; an API token is refused with `403` whatever it holds.
Under approvals it is also a four-eyes request. Scripts deploy from repositories.

**Stored sources.** A clone URL with a credential in it (`https://user:token@host/...`, or
`https://token@host/...`), stored by an older release, is shown with the credential replaced
by `***` in every application read; the stored value is left as it was, because updates clone
from it. It never reaches a command line: Noust takes the credential off the URL before git
sees it and hands it to git in the environment, as an `Authorization` header scoped to the
scheme, host and port it was stored for (`GIT_CONFIG_COUNT` with
`http.<scheme>://<host>/.extraHeader`), so a submodule or a redirect to another host never
receives it. The next forced update or cache sync rewrites the checkout's `origin` without
it, and every error, log line and piece of git output Noust relays has URL credentials
replaced by `***`. This needs git 2.31 or later, which every supported distribution ships.

### Acting as another person

Noust has no "sign in as" or "impersonate" button, and that is a decision, not a gap (checklist I-15).
Every action in the audit trail is attributed to one person who proved who they are with their own
second factor. A session in which an administrator acts as somebody else breaks that twice: the log
says that person did something they did not, and their second factor, which sudo mode and the
four-eyes approvals rely on, would be vouched for by someone else's. A product that offers it must also
promise that it never happens silently, and that is a promise about every code path that reads a
principal; Noust keeps its principals simple instead.

What a support question usually needs is one of these, and each leaves the trail honest:

* **What does she hold, and where?** `noust access list --user maria` (her bindings), `noust team
  members TEAM` and `noust user list`; in the console, Settings > Roles and
  Settings > Accounts. A resource of another team reads as absent (`404`) to her by design, and so
  does a project she holds no role in.
* **What does she see?** As root on the server, issue a read-only, short-lived token that acts for her
  and nothing more: `noust token create look-at-maria --scope read --owner maria --expires-hours 1`,
  optionally bound with `--team`, `--project` and `--env`. It holds at most what she holds, can never
  write or confirm sudo mode, shows up in `noust token list`, and every request it makes is attributed
  to `token:look-at-maria`, with the issue on the trail naming who issued it. `noust token revoke`
  ends it.
* **Reproduce it as her.** Make a second account with the same role and bindings and sign in as that.

An administrator who must act for a person who cannot (a leaver's last deploy) does it as themselves:
the audit trail then says who did it and why (`--reason`).

## The fleet

A central drives each server through that server's own API over an SSH tunnel it opens outward.
What limits it is enforced on the server, not trusted from the central:

- **The tunnel account.** From 3.1 the central's key is installed for `noust-tunnel`, an account
  with no home, no shell and no password, restricted by sshd itself to forwarding the console
  port (no terminal, no command, no agent, no remote forward, no Unix-socket forward). A server
  enrolled by 3.0 has the key in root's `authorized_keys`, where `permitlisten` does not stop a
  remote forward to a Unix socket created as root; `noust node migrate-tunnel` moves it. See
  [CENTRAL.md](CENTRAL.md).
- **Fleet tokens are accepted only from loopback**, the tunnel's end.
- **A ceiling per server**: `read`, `deploy` or `admin`, and `host access` (off by default),
  which a central needs to reach SSH, the firewall, system accounts or root-equivalent changes.
  The server grants the intersection of its ceiling and what the central says its operator may
  do (`X-Noust-Actor`, `X-Noust-Actor-Permissions`, from 4.1: roles are editable, so a name does
  not mean the same on two servers); a permission it does not know is left out, and what no
  central may have is never granted. From a central before 4.1 it reads the role's name
  (`X-Noust-Actor-Role`) in its own table; a role it does not know is a viewer. It is changed only
  on the server (`noust fleet access`); no API raises it.
- **Sudo mode and approvals are the server's call.** The central asks its own operator and
  vouches for it (`X-Noust-Elevated`, `X-Noust-Approval`, `X-Noust-Approved-By`); the server
  refuses the call if it did not, and refuses an approval whose approver is the requester.
- **A central never changes a server's own accounts, tokens or second factor.**
- **Host keys are pinned** from the join code; a change closes the tunnel and notifies.
