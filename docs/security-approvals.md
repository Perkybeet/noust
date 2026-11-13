# Sudo mode and four-eyes approvals

Part of [Security](security.md).

## Sudo mode

Destructive and credential-changing actions ask a console session to prove it is still its
operator. The session already proved the account's password and second factor at sign-in, so
confirming asks for **one factor**: `POST /api/auth/elevate` with a current TOTP or backup code,
or a passkey (`/api/auth/passkeys/elevate`); for the master token its second factor, or the token
itself when it has none. An account with no second factor cannot enter sudo mode. The console
shows a "Confirm it's you" dialog and retries the action.

Sudo mode is a window that stays open while it is used, as GitHub's does. It opens for
`auth.sudo.idle_minutes` (15) and every destructive action performed in it pushes the end out by
that much again, but never past `auth.sudo.max_minutes` (120) from the moment it was confirmed,
nor past the session's own end. Only an action that needed sudo mode extends it: reading the
console, or leaving a tab polling, does not. The end is written at most once a minute. A session
that was confirmed before this behaviour existed ends at the deadline it was given.

`auth.sudo.require_password: true` asks for the account's password together with the code,
as 3.3 did; the passkey is still a confirmation on its own. A wrong code is counted by the
account's lockout and the address's, exactly as at sign-in, and a TOTP step is accepted once for
sudo mode. Each confirmation is audited as `auth.elevate`, with what it was made with (never the
code), and a refusal as `auth.elevate` `failure`.

Under the `ens-medium` profile the window is 10 minutes idle and 30 in all, the password is always
asked with the code, and an operator may tighten these values but not loosen them (see
[ENS.md](ENS.md)).

A central vouches for its operator's sudo mode to a node with `X-Noust-Elevated`, and reads the
window through the same `elevation_satisfied` the server uses for its own routes; a call the
node's schema marks as elevated passes through `ensure_elevated` on the central first, so working
on a node through a central keeps the central's window open. A bulk fleet job queued while
elevated keeps its elevated steps for 30 minutes whether or not the operator is still working.

Which routes need it is declared on each route and published in the OpenAPI schema as
`x-noust-requires-elevation`, so the console, a script and a central all read the same list. It
includes:

- deleting anything: an application, a database, a database user, a service, a site, a
  certificate, a backup, a backup schedule or destination, a cron job, a domain, an account;
- uninstalling a database engine; restoring an application or database backup; revoking a
  certificate;
- revealing a secret (an `.env` in clear, an application's database URL, a database password,
  a webhook secret, a destination's key) and writing an `.env`;
- moving an application to releases; changing its limits, health check, retention, branch,
  blue/green or previews;
- editing a unit or a site configuration by hand; creating a service; creating or rewriting a
  cron job or a backup schedule; building an application as root;
- editing, inserting or deleting a database row;
- writing Noust's configuration, whole or by section;
- server changes: updates, reboot and shutdown, storage clean-up, swap, time, host name, SSH,
  the firewall, fail2ban, accepted risks;
- managing accounts, invitations and separation-of-duties exceptions; issuing an API token;
  disabling 2FA; regenerating backup codes; deciding an approval;
- deploying or inspecting a local path (see [Local paths](security-roles.md#roles-and-permissions));
- `POST /api/jobs/delete`, which queues the same deletion `DELETE /api/apps/{domain}` does.

Sudo mode applies to sessions, whichever channel carries the session token: the cookie, or
`Authorization: Bearer` for a client that signed in with `bearer: true`. The master token and
API tokens issued by 3.0 are not asked to elevate. A token issued from 3.1 on is refused on these
routes unless it was issued with `allow_elevated`. The rule follows what the credential is,
never the header it arrived in.

## Four-eyes approvals

With `approval.enabled: true`, and always under the `ens-medium` profile, the changes that are
root on the host by another name need a second person: raw units, cron commands, backup hooks,
raw site configuration, deploy hooks, moving an application to its own account and building as
root (`root_equivalent`); SQL that writes, row edits
and `EXPLAIN ANALYZE` included; deploying from a directory on the server; adding and removing
fleet servers; opening a root shell on the server in the browser (see
[Terminal and one-off commands](terminal.md)); and role grants. The OpenAPI schema marks those
routes with `x-noust-requires-approval`.

A role grant is anything that leaves somebody able to do more than before, by whichever route:

- the roles of an account (`PATCH` with `role` or `roles`, and `PUT .../roles`): any change counts,
  a reorder or a role taken away included, because the role an account is known by is what an
  approval judges a decider by; a new account and a recovery invitation;
- a role on the server, a team, a project or an environment, through the access bindings or the
  team's own routes; adding an account to a team, or giving a member a higher role there;
- a permission added to a role, which every account that holds it holds from its next request: a
  role edited, put back to how Noust ships it with permissions it had taken out, or given what a
  newer release offers it;
- what a sign-in source (OpenID Connect, SAML, a directory or SCIM) hands out: its default role
  and team, what its groups mean, provisioning and linking by e-mail address; and adding one.

What waits for nobody, because it can only take power away: taking a binding or a team membership
from an account, a team member given a lower role, a role that ends up holding less than it did, a
sign-in source switched off or removed. Making a role, copying one or deleting one nobody holds
changes nothing until somebody is given it, which is where it is asked. The owner role cannot be
edited at all, and is handed on only by an owner, through every route.

- The first call does not run. It answers `202` with `approval_required` and becomes a request
  that snapshots the exact call (method, path, query and body, by their SHA-256), with a reason.
- By default only a `security` account decides. With `approval.approvers: [security, admin]`,
  an `admin` may approve infrastructure changes, never a role grant. Never the requester, never
  another account of the same person, never a token, the master token or a central. Root at the
  terminal decides too (`noust approval approve`), on record with its operating-system identity.
- A request waits `approval.request_hours` (24) for a decision. An approval lets its requester
  make that exact call once, within `approval.execute_minutes` (30), with
  `X-Noust-Approval: <id>`.
- Every step is an audit event naming both people.

## Tokens and the built-in assistant: the approval policy

The second person's rule is about what a call *is*. A token (CI, a script, a client of MCP) and the
built-in assistant are also callers that are not a person, so a server decides, for each of them, how
much it does without one. Who calls is read from the credential and never from what the client says of
itself: a **person** (a session, the master token, a central acting for its operator), a **token**, or
the **built-in assistant** (its own token, marked when it is issued). A person is held to the roles and
to the rule above and to nothing else.

For each permission that changes something (reading is not approved), in **production** and in **every
other environment** (the environment of the application the call names; what names none, or an
application the caller does not see, counts as production), the policy says one of three things:

| Value | What happens |
|---|---|
| `alone` | The call runs, as it always has. |
| `propose` | The call does not run. It becomes a request, with the same flow as above, that a person approves in the console; once approved, the same call, made again with `X-Noust-Approval`, runs once. |
| `never` | The call is refused with the sentence of the policy that forbids it and who changes it (`403 approval_never`). |

**What ships.** A token keeps doing what it did: `alone` everywhere its permissions let it, so a pipeline
that deployed yesterday deploys tomorrow. The assistant proposes everything that changes something, in
both environments. Nothing changes until somebody changes the table.

**What no table lifts** (the floor). The assistant never does what thirteen permissions let one do
(`secrets.reveal`, `root_equivalent`, `server.host_access`, `server.terminal`, `apps.terminal`,
`accounts.manage`, `accounts.read`, `audit.manage`, `audit.read`, `security.manage`, `settings.manage`,
`fleet.manage`, `teams.manage`): those rows are `never` and fixed (`403 approval_floor`). What asks for sudo
mode, or for a second person every time, is never the assistant's whatever its cells say: a person does
it from the console, in their own session. Where most of what a permission does asks for that (the *key*
rows: `apps.manage`, `server.manage`, `databases.manage`...), `alone` is not offered at all; a token keeps
doing what it does there until it is given a value that is offered, because a token issued to act in sudo
mode is a decision somebody already made.

**ENS.** The control `token_production_approval` (asked by ENS MEDIA and ALTA, `security.token_production_approval`
for anyone) makes `propose` the least a token's cell in production can be, and what the operator chose before
is kept and applies again if it goes away.

**Who decides what the policy held.** A person with an account who holds every permission the call needs,
in the application's team: another person of the team, and also the **owner of the token** (she is
supervising her own tool, which is not the second person the rule asks for). A call that the rule also holds
still asks for another person. A token, the master token and a central never decide. The request says who
called: the class, the token, the environment, the application and the client the token said it was.

**Who changes it.** `security.manage` and sudo mode (`PUT /api/config/approval-policy`; `noust approvals
policy set PERMISSION CALLER ENVIRONMENT VALUE` as root). Giving a token or the assistant more freedom than it
has waits for a second person where the server asks for one; taking it away does not. Before saving, the
console shows what it would change and to which credentials (`POST /api/config/approval-policy/preview`).

**The MCP.** A tool of MCP is a call of the API with the caller's token, so it comes through the same
place. A tool that changes something takes an `approval` argument: when a call waited for a person, the
answer says so ("Not done"), and once a person approved it the model calls the same tool again with the
request's number.

## What a token did, and leaving it as it was

Every token is a credential, and the audit trail already says what each did. `GET
/api/auth/tokens/{id}/activity` (and `noust token activity NAME`) reads it as **interventions**: what a
token did without a silence of more than thirty minutes between one thing and the next, each with its
timeline (the action, the application, how it ended, the deployment it started and the client it said it
was, labelled as what it said). The built-in assistant is **one credential for each person**, whatever
number of tokens it used (each conversation has one): `assistant:<person>`, whose interventions are its
conversations.

The owner of a token, whoever governs accounts and whoever reads the audit trail read it; a person of a
team sees only what names an application of their team.

"Leave it as it was" proposes, then does: for each application the intervention deployed, the way back
Noust already has, to the version before the first thing it deployed there, behind its health gate and
with its confirmation when a deployment changed the database's schema (Noust puts code back, never a
database). Where there is none it says why: somebody else deployed after it, it is already as it was, the
version before cannot be gone back to, the application is gone. What the token did that has no way back
here (a deleted application, a restored backup, a database, a variable, a service stopped) is listed with
its reason, never silently left out. Doing it is a job in sudo mode (`POST .../activity/{n}/undo`, `noust
token activity NAME N --apply`) that says what went back and what did not.
