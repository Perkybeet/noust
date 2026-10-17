# Security

Noust runs as root and deploys code from repositories onto the machine it manages. This page
states what it defends against, what it does not, and every control involved, so an operator
can decide how to expose it. [SECURITY-MODEL.md](SECURITY-MODEL.md) is its companion: each guarantee
with the failure it answers, the tests that fail if it stops holding, the numbers generated from
the code, and how to verify a release.

## Threat model

**Trusted**

- Root on the machine, and anyone who can run `noust` as root. Noust does not defend the
  machine against its own administrator. The CLI is the emergency channel: it is not held to
  roles, and every command that changes something is audited with the operating-system identity
  that ran it.
- The console's master token: root-equivalent while no account exists, and emergency access
  afterwards.
- Accounts with the `admin` role, and tokens that hold `root_equivalent`: they can write units,
  cron commands and site configuration that run as root, and an application's deploy hooks, code
  that runs with its identity and its secrets on every deploy. Four-eyes approvals (below) put a
  second person in front of exactly those changes.

**Defended against**

- **Anyone who can reach the console over the network** without a credential: the console
  listens on loopback by default, refuses cleartext beyond loopback, answers only the host names
  in `web.allowed_hosts` when set, and locks out repeated failures per address and per account.
- **Holders of narrow credentials**: a `viewer` account or a `read` token cannot read secrets or
  other processes' command lines, change anything or cancel jobs; an `operator` can restart,
  deploy and roll back what exists but cannot create an application or name a source for the
  machine to fetch and build; a `security` account governs access and the audit log and
  deploys nothing. Every route states its permission and one chokepoint enforces it.
- **One person acting alone** on root-equivalent changes, when approvals are on.
- **A hijacked browser tab or a malicious page**: CSRF tokens, `SameSite=Strict` cookies, a
  strict Content Security Policy with Trusted Types, and sudo mode before destructive actions.
- **Untrusted repository content**: a repository can contain symlinks, odd file names and
  values that end up in unit files and web server configuration.
- **Install and build scripts**: an application's dependencies install and build as the
  unprivileged `noust-build` account in a sandbox, unable to read Noust's configuration, the
  store, root's home or other applications' `.env` (see [Builds](#builds)). Applications
  deployed before 3.1 are tried in the sandbox on their next update, and covered when that
  passes.
- **Untrusted deploy hooks**: a repository's `noust.yaml` is read with a closed schema, and a hook
  is an argument list, never a shell, that runs as the application's account (or, while the
  application still builds as root, as Noust itself, as its migrations always did) and never
  with more privileges than the compose file already gives its service (see
  [Deploy hooks](#deploy-hooks)).
- **A compromised central**: each server enforces its own ceiling on what any central may do
  there, and a central never gets a shell (see [The fleet](#the-fleet)).
- **Unprivileged local users**: secrets never travel in a command line (which `ps` shows to
  every user), and every file holding one is `0600`.
- **Forged webhooks** and **server-side request forgery** through notification URLs.
- **Tampering with the audit log going unnoticed**: its lines are chained with an HMAC and can
  be shipped off the machine as they are written.

**Not defended against**

- **Teams from each other at run time.** A team is a boundary of permissions and organisation, not of
  execution (see [Teams](#teams)).
- **Applications from each other at run time, where they share an account.** From 3.2 an
  application Noust creates runs as its own system account, `noust-app-<name>`, with no home and
  no shell, owning its tree, `.env` and caches, so one compromised application cannot read
  another's files. An application from before 3.2 keeps the shared account (`service_user`,
  `www-data`) until `noust app identity migrate` moves it, and while it does, it can read the
  files, including the `.env`, of every other application on that account. A monorepo created
  from 3.4 runs all its workspaces' units as one account of its own, and an older one moves with
  the same command. A Docker Compose stack has no account (its processes are its containers'), so
  the compose check keeps it to its own files instead: a bind mount of Noust's configuration and
  secrets, of what contains them (`/`, `/etc`, `/var`) or of another application's tree is
  refused like `privileged` (see [Builds](#builds)). A static site runs nothing; its files are
  served read-only, and once no application shares the account that owns them, none can change
  them. Applications still share the kernel, the network and the machine: Noust is not a
  multi-tenant isolation boundary, and the build sandbox separates builds, not running
  applications.
- **A compromised application reaching the console on loopback.** It still needs a
  credential, but it is on the same host.
- **Anything with root**, including an application built as root because its sandbox was
  disabled (a recorded, per-application decision), a Docker Compose stack with an exception for
  privileged containers, and root rewriting the audit log with the local key; shipping the log
  off the machine is what proves its lines original.

## Processes

Everything Noust executes (nginx, systemctl, certbot, git, npm, database clients) goes through
one module, `CommandRunner`, and the test suite fails if any other module imports
`subprocess`.

- **Argv only, never a shell.** A string where an argument list is expected is refused, and so
  is a NUL byte. A repository or a form field cannot inject shell syntax.
- **Every call has a timeout.**
- **Secrets travel through the environment, standard input or a private defaults file**,
  never the command line. That includes every database password Noust hands to a client.
- **Running as another account** uses one prefix, `runuser -u <user> --`. There is no `sudo`
  in any command Noust runs.
- **Children get a clean environment** for builds and hooks: the runner no longer hands them
  everything its own process inherited.
- `--dry-run` is implemented in the same place: read-only probes still run, everything else is
  recorded and skipped. A command counts as a probe only when its exact argument shape is
  declared as one; anything else is assumed to change something. (3.0 counted a command as
  read-only when any of its arguments was the name of a read-only subcommand, so an argument
  such as `ufw allow 22 comment status` could have passed for a probe.) Files go through one
  seam that a rehearsal swaps out the same way,
  the console's own state included: a rehearsed `noust token create` or `noust sessions revoke`
  reads the real key and session database and writes neither, and on a fresh machine creates
  no `/etc/noust` at all.

Cron jobs follow the same rule: a job's command is split like a POSIX shell would split it
and run without a shell, so pipes, `&&` and globs are inert text. Write `/bin/sh -c "..."`
explicitly when a job needs a shell.

The unit of an application is sandboxed by systemd as well, without a container: a
read-only filesystem but for its own tree, no home directories, no devices, no capabilities
and a system-call filter. New applications get it by default; one that exists is moved with
`noust app hardening`. See [runtime.md](runtime.md#unit-security). The files and the mounts
of an application are read and written as its own account, never as root, and a file of the
environment is never opened by either ([runtime.md](runtime.md#files)).

## Builds

Installing dependencies and building run code Noust did not write: the `postinstall` script of a
dependency of a dependency. Up to 3.0 it ran as root. From 3.1 it runs in a transient systemd
unit (`systemd-run --pipe --wait --collect`, through `CommandRunner` like everything else) as
`noust-build`, a system account with no home and no login shell, distinct from the account the
applications run as.

- **What it may write**: its release and its own caches, `/var/cache/noust/build/<app>`, per
  application so one build cannot poison another's. `ProtectSystem=strict`, `ProtectHome`,
  `PrivateTmp`, `NoNewPrivileges`; `/etc/noust`, the store and every other application's
  `.env` are inaccessible; memory and CPU are limited, and a build killed for time or memory
  says so.
- **What it gets**: a clean environment. The application's own `.env` reaches the build through
  `EnvironmentFile=`, which systemd reads as root; `noust-build` cannot open it. A preview's
  build gets no production secrets. With `--network strict`, dependencies install without the
  application's variables and the build runs without a network.
- **What stays outside**: fetching the source (it needs the deploy key and git credentials) runs
  as root and exports the release without `.git`. Migrations run as the application, as its
  unit would.
- **Fail closed.** Before the first sandboxed build, a canary run as `noust-build` must write its
  own directory and must fail to write outside it, into `/root`, or to read a file placed in
  `/etc/noust`. Containers and WSL without mount namespaces ignore `ProtectSystem` silently; the
  self-test is what notices, and the build stops rather than running as root.
- **Scope.** Applications and previews created from 3.1 on build in the sandbox. From 3.2,
  the next update of an application that still builds as root first builds that commit in the
  sandbox, once: when it passes the sandbox is turned on, and when it fails the update builds as
  root as before, flagged by `noust health` and `noust ens check` and by a notification, until
  `noust app sandbox test` passes and `noust app sandbox enable` is run. An in-place application
  builds in its live tree as the service account, still with the rest of the machine hidden.
  Building one application as root is a per-application decision recorded with a name and a
  reason (`noust app sandbox disable DOMAIN --reason ...`, permission `root_equivalent`).
- **Docker Compose** builds run in the Docker daemon, outside any sandbox Noust controls. A new
  stack is refused `privileged: true`, a mounted Docker socket and a bind mount of Noust's own
  state or of another application, all of which are root on the host or the secrets of
  everything on it, unless the application has a recorded exception. The mounts are judged after
  `..` is resolved (`/var/www/apps/shop/../other` and `../other` are the other application's
  directory), the long and the short syntax alike. A stack proven to run with one already is
  warned about and never stopped.

## Deploy hooks

A project can run commands around its own deploy (`hooks.pre_deploy` and `hooks.post_deploy`, in
a `noust.yaml` at the root of the repository, or set by the operator for one application). A hook
is code that runs on every deploy with the application's identity and secrets, so it is treated
as such:

- **A repository's `noust.yaml` is untrusted input.** The schema is closed (an unknown key is
  refused, not ignored, and names the field), a path that climbs with `..` is refused, an absolute
  working directory is accepted only inside a Compose service's container, a link instead of a
  file is refused unread, and the file is at most 64 KiB. It cannot choose a user, `privileged` or
  a mount for a hook.
- **A hook is an argument list.** `run` is split with `shlex` and run through `CommandRunner`:
  never a shell, always with a timeout (1 to 3600 seconds).
- **Where it runs bounds what it can do.** In a Docker Compose stack, in a one-off container of
  the image the deploy built, with the service's own environment, networks and volumes, and the
  privileges the compose file already gives that service (which the compose file check watches).
  In every other application, in the release phase of the build: as the application's account,
  with its `.env`, in a transient unit, never as `noust-build`. An application that still builds
  as root (an explicit decision, or one whose sandbox trial has not passed) runs its hooks as
  Noust itself, as root, exactly as its migrations always ran: a further reason to put every
  application in the sandbox.
- **The operator's hooks need `root_equivalent`**, sudo mode and, when approvals are on, a second
  person, and replace the repository's whole: two sources are never merged, so what runs is
  always one document someone wrote. Every change is audited (`apps.hooks`).
- **A hook's output is kept**, in the deployment's log and, the last 4000 characters of each, in
  its history, like any build output: a hook should not print a secret.

## The build block and the runtimes it pins

`build:` in a `noust.yaml` is the same kind of input as a hook, and is held to the same rules
(see [build.md](build.md)):

- **The schema is closed.** An unknown key, a path with `..`, an absolute path, `~` or a name a
  unit or a site could not carry is refused, naming the field; a link on the way to the base
  directory is never followed. A command is an argument list split with `shlex`, never a shell.
- **Where its commands run.** `install` and `build` run in the build sandbox as `noust-build`
  with the whole release writable and nothing else of the machine; `start` is the unit's
  `ExecStart=`, as the application's account. A runtime is run by absolute path, and the
  directory it is installed in is root's and cannot be written by a build.
- **The operator's block needs `root_equivalent`** and sudo mode, replaces the repository's
  whole, and every change is audited (`apps.build`).
- **A runtime is downloaded once, verified, and proved.** Node comes from nodejs.org as the
  official tarball, its SHA-256 compared with the release's published `SHASUMS256.txt` (fetched
  over HTTPS from the same origin; the release signature is not verified) before a member is
  unpacked, unpacked with the checks every archive gets (nothing outside the destination, no
  devices, no setuid), and run once to prove its version before it is put in place. Python comes
  from `uv`, PHP from the distribution's packages. Installing and removing runtimes is audited
  (`runtimes.install`, `runtimes.remove`).
- **A hint is not an order.** The version files of a repository (`.nvmrc`, `engines.node`,
  `.python-version`...) may make Noust install a runtime; a failed install never fails a
  deployment that the server's own runtime can build.

## Secrets via --stdin

The same rule applies one level up, to how an operator runs `noust` itself: a value typed after
`noust config set` is written to the shell's history file and, for as long as the process runs,
visible to every local user through `ps`. That is fine for `apps_directory`; it is not fine for
`monitor.smtp.password` or a webhook URL.

```bash
# Avoid - lands in shell history and in ps
noust config set monitor.smtp.password 'hunter2'

# Read from standard input instead - a single trailing newline is stripped
printf '%s' "$SMTP_PASSWORD" | noust config set monitor.smtp.password --stdin

# Or ask for it interactively, without echoing it back
noust config set monitor.smtp.password --prompt
```

`--stdin` and `--prompt` are mutually exclusive with giving `VALUE` on the command line, and
with each other; `noust config set KEY` with none of the three is a usage error. Typing a value
for a key `noust.core.config.redact_secrets` would redact (`password`, `token`, `key`,
`webhook`, `secret`, `credential`, `auth`, and their compounds) straight into `VALUE` on a real
terminal prints a warning suggesting `--stdin`, and is otherwise accepted: the warning is a
nudge, never a refusal, so a script that has already taken the value out of argv some other way
does not have to be rewritten.

## What the scrubbing does and does not do

Build output, job logs and failure messages are what the tools printed, and the tools run with the
application's environment: a script that echoes `$API_KEY`, a framework that prints its configuration
when it fails, a connection error that quotes the whole `DATABASE_URL`. That text is kept in the
deployment history and the job logs, readable with a far narrower credential than the `.env` it came
from. `noust.core.redact` replaces what it knows is secret with `***` wherever that text is stored or
shown: the values of the application's secret variables, the credentials Noust itself holds, the
password in a URL, and the command line of a process the monitor lists.

It works by value, not by shape, and that is its limit, stated so nobody relies on more:

- **A secret it was not told about is not recognised.** A token a build downloads and prints is
  invisible to it. Mark a variable secret (`noust env mark`), and keep secrets out of a hook's output.
- **A value shorter than six characters is left alone.** A secret variable set to `true` would otherwise
  blank out every ordinary word containing it. A value that short protects nothing worth that.
- **It covers what Noust persists or publishes**, not what a tool writes to a file inside the
  application's own tree.

[SECURITY-MODEL.md](SECURITY-MODEL.md) lists it among what Noust does not guarantee, with the tests
that hold it to what it does.

## Exposing the console

The console acts as root, so how it is reached matters more than anything else on this page.

| Setup | Command |
|---|---|
| Loopback only (default). Reach it through an SSH tunnel. | `noust web start -d` |
| TLS served by Noust | `noust web start -d --host 0.0.0.0 --tls-cert fullchain.pem --tls-key privkey.pem` |
| TLS with a self-signed certificate | `noust web start -d --host 0.0.0.0 --self-signed` |
| Behind a TLS-terminating reverse proxy | `noust web start -d --trusted-proxy 127.0.0.1` |

- **Beyond loopback, TLS is mandatory.** Binding to any other address without a certificate
  and key (or `--self-signed`) is refused, both when the options are parsed and again where
  the socket is bound. `--insecure-http` overrides this, and says what it does: the access
  token and the session cookie then cross the network in clear.
- `--self-signed` mints a certificate under `/etc/noust/panel-tls/` and reuses it while it is
  valid.
- `--allow-ip ADDR/CIDR` (repeatable) answers only those clients; everyone else gets `403`
  before authentication. It restricts who connects; it encrypts nothing, and does not lift the
  TLS rule.
- `--trusted-proxy ADDR/CIDR` is the only way `X-Forwarded-For`, `X-Real-IP` and
  `X-Forwarded-Proto` are believed, and only from that peer. By default Noust trusts nobody's
  forwarding headers. Declaring the proxy is what makes the session cookie `Secure` and the
  client address in the audit log the real one.
- Requests are rate limited. A request without a valid credential (the sign-in, the forge
  webhooks, anything anonymous) is counted per client address, 120 a minute by default
  (`web.rate_limit_requests`). A request with a valid credential is counted per credential
  instead (one sign-in, one API token, the master token), 1200 a minute by default
  (`web.rate_limit_authenticated_requests`): over an SSH tunnel every request comes from
  `127.0.0.1`, and a per-address budget made every tab and script share one. A wrong
  credential buys nothing: it is counted as anonymous, and guessing is stopped by the
  lockout below, which is separate. An IPv6 client is counted by its /64, not by its
  address: a customer is given 2^64 of them, and counting each one gave every request a fresh
  budget (and a fresh lockout, and a way to fill the limiter's table with entries that pushed
  everyone else's out). The audit trail and the session's binding keep the exact address.
  The limiter is not a way to test a credential. It only looks at one on `/api`, `/events`
  and `/ws`, where an endpoint checks it too; `/health` and the forge webhooks, which never
  check one, are always counted by address, whatever they carry. A wrong credential it does
  look at counts towards the lockout exactly once, like one an endpoint refuses, even on a
  route that never checks it (a public endpoint, a `404`). An address that is locked out,
  or already over the anonymous budget, is counted by address without its credential being
  checked at all, so a flood that is refused anyway costs no database query per request.
  The console's shell (`index.html` on any console
  address) and its hashed build assets spend no budget, so reloading the page cannot lock
  the operator out. A refusal is `429` with `"error": "rate_limited"` and a `Retry-After`
  of the seconds until the budget frees; every counted response carries
  `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` (the UNIX time at which the
  budget is whole again) for the budget it was counted in. Both
  budgets share `web.rate_limit_window` (60 seconds) and `web.rate_limit_enabled`.
- Request bodies are capped before authentication and before routing: 1 MiB for everything
  but the forge webhooks, which get 5 MiB. A larger body is refused with `413` and
  `"error": "payload_too_large"`, from its `Content-Length` or, for a chunked body, as it is
  read. The API's largest legitimate bodies (a unit file, a site configuration, an `.env`)
  are a few kilobytes.
- `web.allowed_hosts` (names, or `*.example.com`) makes the console answer only those `Host`
  headers, plus loopback and the hosts of `web.public_url` and `web.hooks_url`; any other host
  gets `400`. Empty answers any host.

## Authentication

**Accounts.** People sign in with a username, a password and a second factor. Passwords are
hashed with scrypt from the standard library, with fixed, versioned parameters; the shortest
accepted is `auth.password.min_length` (12; 14 under the ENS profile). Every account enrols a
second factor, an authenticator app (TOTP) or a passkey, at its first sign-in and can do nothing
else until it has. Accounts are created with `noust user create` (password at a hidden prompt or
with `--stdin`, never in argv) or by a one-use invitation (`noust user invite`, valid 24 hours by
default; only its digest is stored), which is also how a person who lost their authenticator
gets back in. Disabling an account ends its sessions and tokens at once. Each account's TOTP
secret is encrypted at rest under a key kept beside the store, never in it.

**Passkeys.** WebAuthn with attestation `none`, ES256 and RS256 (and EdDSA when the installed
`cryptography` supports it), resident keys and user verification required, several per account,
a random user handle per server. A passkey signs in on its own and confirms sudo mode. The first
one comes with backup codes. `noust passkey reset USERNAME` is the root-only way back when every
passkey is lost. Browsers offer passkeys only on a certificate they trust.

**Single sign-on.** A provider that speaks OpenID Connect (authorization code with PKCE, `state` and
`nonce`, ID tokens verified against the provider's published keys) can be one more way to sign in
(`noust auth oidc`, [sso.md](sso.md)). It is a first factor like a password and never replaces the
account's own second factor: what the provider says about its MFA is not trusted to skip ours. A
person is identified by the provider's issuer and subject, never by e-mail alone; an account that has
no second factor is never linked by e-mail; the first account of a server is never made by a
provider; the ENS profile never lets a provider hand out `admin`.

**Two steps.** The console signs a person in with their username (or the email of the person,
when it names exactly one account that may sign in) and password first. When the account has a
second factor, that answer opens the second step, a single-use challenge bound to the account and
the address that lasts five minutes: the code or the account's passkey finishes it, and the
password is not sent again. An account without a second factor is let in on its password and may
do nothing but set one up. A wrong code counts toward the account's lockout like a wrong password.

**Lockout.** Five consecutive failed sign-ins (wrong passwords or codes) lock the account for 15
minutes (`auth.lockout.*`), in addition to the lockout per address described below. The sign-in
page does not show the host name, the version or whether a second factor is on before
authentication, and a failure of the first step says the same thing whatever was wrong: an
unknown name, a wrong password and an email on two accounts read alike.

**The master token.** Every `noust web start` issues a new access token and prints it once,
in the same banner whether it runs in the foreground or, with `-d`, in the background. Only
a hash is stored, salted with the console's signing key, in `/etc/noust/web-token` (`0600`);
a token cannot be shown again, only replaced with `noust web token --new`.

Under systemd it is different. `noust web enable` issues the token: it writes the new hash
before it restarts `noust-web.service`, so the token it replaces stops working at once, and
prints the new one when the service is serving. The service itself issues none and prints
none (its output is the journal); it serves whatever hash is on disk. So the same token
stays valid across every restart of the unit - a crash, a reboot, `systemctl restart
noust-web`, a package upgrade - until you rotate it with `noust web token --new` (or run
`noust web enable` again, which also issues a new one).

A running console reads the token hash from disk on every request, so rotating it takes
effect immediately - no restart needed. To retire a token that may have leaked, just run
`noust web token --new`; the token in force stops working at once, in every console already
running: in the background, or as `noust-web.service`.

`--regenerate` goes further: it rotates the signing key too, which immediately signs out
every session, and, because API tokens and TOTP backup codes are salted with that same key,
also invalidates every one of those - issue new ones afterwards with `noust token create` and
`noust 2fa backup-codes`.

While no account exists, the master token is the whole console, as in 3.0. Once one does, it
is **emergency access** (break-glass): it still signs in, with its own second factor or passkey
when it has one (a master token with a passkey no longer signs in alone), every use is audited,
and it cannot manage the audit trail it leaves. Under `security.profile: ens-medium` it can only
recover access to accounts. `noust incident freeze` leaves it as the only way into the console.

**API tokens.** `noust token create NAME --scope read|deploy|admin [--expires-hours N]
[--owner USERNAME]`, or the console's Settings > API tokens, where an account issues its own.
A token acts for its owner and holds at most the owner's permissions, whatever its scope; it
can be narrowed to a list of permissions and to networks (`allowed_cidrs`), and it follows its
owner: a changed role applies at its next request, a disabled owner stops it. A token issued
from 3.1 on is not accepted where sudo mode is asked unless it was issued with
`allow_elevated` (refused under the ENS profile, where tokens also expire within
`auth.tokens.max_days`, 90). Tokens issued before accounts existed keep their 3.0 scope and
behaviour and are adopted by the first `admin` account; fleet tokens belong to a central and are
never adopted. Tokens start with `noust_tok_` (`wasm_tok_` before 3.0; those keep working), are
shown once, and only their salted hash is kept. `noust token list` shows every token ever
issued, live and revoked, without the token itself, and where each is accepted from;
`noust token revoke ID` takes effect on the next request.

*Where a token works from* is chosen at issue (`noust token create --allow-ip ADDR/CIDR`, repeatable)
and can be changed on a live token without issuing another and breaking what uses it:
`noust token set ID --allow-ip ADDR/CIDR` replaces the list and `--any-ip` lifts it, in the console's
Settings > API tokens too (`PATCH /api/auth/tokens/{id}`). Taking addresses away is immediate;
adding some or lifting the limit asks for sudo mode, because it widens what a stolen token can do.
A person changes their own tokens and nobody else's, and each change is on the audit trail
(`auth.token.update`) with the networks before and after.

*Turning the API off for tokens.* A server whose scripts do not use the API can set
`web.api_enabled: false`: every API token then gets `403 api_disabled`, while the console (which
signs in with a session) carries on and a central's fleet token keeps working, because that is how a
central reaches its node. It is read on every request, so it applies without a restart; a value that
cannot be read leaves the API on rather than lock scripts out over a typo.

**Sessions.** Signing in creates a server-side session in `/etc/noust/web-sessions.db`.

- Cookie `wasm_session`: `HttpOnly`, `SameSite=Strict`, `Secure` whenever the request arrived
  over TLS (directly or through a declared proxy).
- 30 minutes without activity end a session (`auth.session.idle_minutes`; 15 under the ENS
  profile), and no session lasts more than 12 hours from sign-in
  (`auth.session.absolute_hours`; 8 under the profile), however active. Activity renews it,
  rotating its id. A rotated id keeps working for 30 seconds so requests in flight do
  not fail, and each of those requests is handed the same successor: a session renews into
  one new session, never several. Signing out or revoking a session also ends the id it was
  renewed from.
- A session is bound to the client address it was issued to.
- `noust sessions list`, `noust sessions revoke PREFIX`, `noust sessions revoke-others`, and the
  console's Settings manage them.

**CSRF.** Every `POST`, `PUT`, `PATCH` and `DELETE` made with a session must carry the
session's CSRF token in the `X-WASM-CSRF` header; the console reads it from the `wasm_csrf`
cookie. That holds whichever channel carries the session: a client that signed in with
`bearer: true` and presents the session token as `Authorization: Bearer` sends the CSRF token
it received in the same answer. The master token and API tokens are not sessions and have no
CSRF token.

**Two-factor authentication.** TOTP (RFC 6238: SHA-1, 6 digits, 30 seconds, one step of
clock drift either way). An account enrols it at its first sign-in (or a passkey instead);
`noust user reset-mfa USERNAME` removes it so the account enrols a new one. The master token's
own second factor is enrolled with `noust 2fa enroll` and `noust 2fa confirm CODE` or from the
console. Confirming prints eight backup codes once; only their salted hashes are kept.
When enabled, sign-in requires a code or an unused backup code. A code is accepted once per
purpose - signing in, sudo mode, turning 2FA off: the time step it matched is remembered, and
that code, or an older one still inside the drift window, is refused for the same purpose
afterwards. Enrolling and confirming from the console need sudo mode, confirmed with the
master token, since 2FA is not on yet.

**Lockout.** Five failed credentials from one address lock it out for 15 minutes. Every
channel counts against the same budget: sign-in, sudo-mode elevation, disabling 2FA, bearer
tokens, session cookies, a credential presented to `GET /api/auth/session`, and WebSocket
handshakes. A session token this console signed but that has since expired or been revoked is
refused without being counted: it cannot be a guess, and counting it would lock out an
operator whose browser merely outlived its session. A locked-out address is refused at
`/api/auth/login`, `/api/auth/elevate` and `/api/auth/2fa/disable`, on any request carrying a
bearer token or a session cookie, and on WebSocket handshakes. The lockout is the attacker's
address; an operator connecting from elsewhere is not affected. Sign-in from an address many
people share is the exception: every operator reaching the console over `ssh -L` arrives from
`127.0.0.1`, and a trusted proxy that passes no usable forwarding header resolves to its own
address, so there sign-in failures are counted per address and name (and the master token under
a name of its own) rather than locking everyone on the tunnel out at once; the per-account lockout
still counts every one of them. Webhook signatures are counted per application instead (see
[Outbound requests](#outbound-requests)).

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
  (3.4): `admin`, `security` and `auditor` see every team; a `viewer` or `operator` sees the teams
  it belongs to. See [Teams](#teams).
- **Roles are held where they are given** (3.4). A role can be given on an environment, a project,
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
  members TEAM` and `noust user list`; in the console, Settings > Roles and access and
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
- deploying or inspecting a local path (see [Local paths](#roles-and-permissions));
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
[Terminal and one-off commands](terminal.md)); and role grants (a role change, a new account, a
recovery invitation). The OpenAPI schema marks those routes with `x-noust-requires-approval`.

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
  The server grants the intersection of its ceiling and what its own table gives the role the
  central names for its operator (`X-Noust-Actor`, `X-Noust-Actor-Role`); a role it does not know
  is a viewer. It is changed only on the server (`noust fleet access`); no API raises it.
- **Sudo mode and approvals are the server's call.** The central asks its own operator and
  vouches for it (`X-Noust-Elevated`, `X-Noust-Approval`, `X-Noust-Approved-By`); the server
  refuses the call if it did not, and refuses an approval whose approver is the requester.
- **A central never changes a server's own accounts, tokens or second factor.**
- **Host keys are pinned** from the join code; a change closes the tunnel and notifies.

## Streams

The event stream (`GET /events`) and the WebSockets (`/ws/...`) are authenticated once, at
the handshake, and then stay open for hours. So:

- **The credential is checked again** every 30 seconds on a WebSocket and every 25 on
  `/events`. Revoking an API token, rotating the master token (`noust web token --new` or
  `--regenerate`), signing out, revoking a session or letting it expire ends every stream it
  opened: a WebSocket closes with `4401`, the event stream ends. A session renewal does not;
  it is the same sign-in under a new id.
- **One credential holds at most 8 WebSockets at once.** Every log socket is a
  `journalctl -f` running as root; the ninth handshake is refused with `4429` until one
  closes. The budget is per credential, so a script at its limit does not stop the console.
- **A WebSocket lasts at most 12 hours.** It then closes with `4408`, and the client
  reconnects, authenticating again.
- **One credential holds at most 16 event streams** (`GET /events`, and a central's relay of a
  node's, `/api/nodes/{node}/events`), counted where every request passes and given back however
  the stream ends; the seventeenth is refused with `429` (`"error": "rate_limited"`). Each stream is
  a subscription and a periodic render of the machine, so without a cap one low-privilege token could
  open streams until the console had nothing left for anyone else. A stream ends after 12 hours as
  well, and the browser's `EventSource` reconnects by itself.
- **`/ws/logs/{name}` streams only a unit Noust manages**, decided by the same ownership rule
  every other service operation goes through. A unit Noust did not create, `ssh` for example,
  is refused.

## The browser

The console is a static build served by Noust itself, loads nothing from any other origin,
and runs under a Content Security Policy with no `unsafe-inline` for scripts or styles and Trusted
Types, which forbid string-to-DOM sinks. The end-to-end suite fails on any CSP violation, and
every response carries the same headers whatever it answers: a page, the API, an asset, a 404, and
a refusal made before routing (a host or an address that is not allowed, a rate limit, a body too
large). `Strict-Transport-Security` is added when the request came over TLS (directly, or through a
declared proxy); everything is `Cache-Control: no-store` except the content-hashed files under
`/assets/`, which are immutable for a year. The exact values of every header are printed in
[SECURITY-MODEL.md](SECURITY-MODEL.md#the-browser), generated from the code that sends them and
checked against it by a test.

## Databases

The console's SQL runner is read-only unless told otherwise, and that is enforced by the
database server, not by inspecting the statement:

- **PostgreSQL**: the statement runs in a `READ ONLY` transaction in a session that signs in
  over `127.0.0.1`, with a password, as a dedicated role `wasm_ro_<database>`: not a
  superuser, `default_transaction_read_only` on, granted only `CONNECT`, `USAGE` and `SELECT`.
  Because the login itself is the limit, `SET ROLE`, `RESET ROLE`, `set_config('role', ...)`
  and `SET SESSION AUTHORIZATION` have nothing to return to, and the server refuses
  superuser-only functions such as `pg_read_file` and `COPY ... TO PROGRAM`. The password is
  random, set as a SCRAM verifier, and kept in a 0600 root-owned file under
  `/var/lib/noust/secrets/postgresql/`. `pg_hba.conf` must allow
  `host <database> wasm_ro_<database> 127.0.0.1/32 scram-sha-256` (the Debian and Ubuntu
  default `host all all 127.0.0.1/32` line does); if it does not, read mode fails with that
  line in the error and never falls back to the superuser.
- **MySQL and MariaDB**: the statement runs in a `READ ONLY` transaction as a dedicated
  account with `SELECT` on that one database and nothing else (no `FILE`, so no
  `LOAD_FILE()` or `INTO OUTFILE`). Its password is rotated on every call and never stored.
- **MongoDB and Redis** have no read-only mode; every statement is a write.

A write needs `databases.write` and sudo mode (and, under the ENS profile, `root_equivalent`:
it runs as the engine's superuser); `EXPLAIN ANALYZE` executes its statement and counts as a
write. With approvals on, a write is a four-eyes request. The server enforces a statement
timeout and bounds the output. Only one statement is accepted per request in read mode. Every
statement is an audit event: a read by its digest, a write with its text, secrets scrubbed.
(Up to 3.0 the text went to a `wasm.audit` logger that had no handler, so it was recorded
nowhere.)

The data browser (tables, rows, Redis keys) reads as the same read-only account. Editing a row
changes exactly one row, found by its whole primary key, needs `databases.write` and sudo mode,
and is a four-eyes request when approvals are on. Restoring over an existing database takes a
safety copy first and puts it back if the restore fails; `--as-new` restores beside it.

The statement reaches the client as data, never as a script: psql gets it as a `-c` string
(a leading backslash is refused) and mysql reads it in `--binary-mode` with its client
commands (`system`, `\!`, `source`, `tee`, `pager`) refused, so no shell escape is reachable
from the console.

Restoring a dump runs its SQL with the engine's administrative account, so a restore trusts
the dump's SQL; it needs sudo mode and is audited. The client never reads the dump as a
script: a PostgreSQL plain dump with psql meta-commands (`\!`, `\o`, `\set`, `\connect`)
outside its COPY data is refused before anything is dropped, pg_dump's own
`\restrict`/`\unrestrict` excepted; MySQL and MariaDB read the dump on stdin in
`--binary-mode`, where `system`, `\!` and `source` are off.

## Outbound requests

- **Notifications** (webhook, Slack, Discord, Telegram) refuse destinations in loopback,
  private (RFC 1918), carrier-grade NAT, link-local (which covers cloud metadata at
  `169.254.169.254`), the unspecified address (`0.0.0.0/8`, `::`) and their IPv6 equivalents.
  An IPv6 address that carries an IPv4 one - IPv4-mapped (`::ffff:0:0/96`), NAT64
  (`64:ff9b::/96`), 6to4 (`2002::/16`) - is judged by the IPv4 address inside it, so
  `::ffff:127.0.0.1` is loopback like `127.0.0.1`. Every redirect hop is checked again, so a
  public name that redirects or rebinds to a private address is still refused. Names you
  trust can be exempted with `notifications.allow_private_hosts`. Only `http` and `https` are
  accepted, and the "test" button never echoes the remote response back.
- **Backup destinations and source archives use the same guard** (`noust.core.outbound`, one
  implementation for every feature that sends a request to a host somebody typed). A backup
  destination's host (the `host` of SFTP and SMB, the `url` of WebDAV, the `endpoint` of S3) is
  judged when the destination is made, changed or tested: one that resolves to loopback, a private
  or carrier-grade-NAT network, link-local or the unspecified address is refused unless it is named
  in `security.allow_private_hosts` (`noust config set security.allow_private_hosts nas.lan --list`),
  so a NAS or a MinIO on your own network is a decision and not a default. Every later use, which
  is every scheduled backup, refuses only what can never be a destination (the cloud metadata
  service, `0.0.0.0/8`, `::`, link-local), so a destination made before 3.4 on your LAN keeps
  working until you test it. A source archive URL is judged the same way before the download and
  on every redirect hop. Names that do not resolve yet are accepted when configured and judged
  again when used. The guard compares the address a name resolves to when it asks; rclone and the
  download resolve again themselves, which is why the narrow look at every use exists and why this
  is a second line behind the permission to configure backups (`admin`), not a replacement for it.
- **The stored SMTP password stays with its server.** The console never receives it, so a
  write that does not name it keeps it: a blank password on `PUT /api/config/smtp`, `***`
  echoed back to `PUT /api/config`, a `PATCH /api/config` of another key. That holds only
  while the host, port, username and transport (`use_ssl`, `use_tls`) stay as they are;
  changing any of them without entering the password again is refused with `422` on
  `password`, so a credential that may write the settings cannot point the password at a
  server of its own and send itself a test email.
- **A Telegram bot token** must be `<digits>:<secret>` from its first character to its last,
  a trailing line break included; any other shape is refused on save (`422` on `bot_token`)
  and again before it becomes part of a Bot API URL.
- **Deploy webhooks** (`POST /hooks/deploy/{domain}`) verify `X-Hub-Signature-256` (GitHub),
  `X-Gitea-Signature` (Gitea) or `X-Hub-Signature: sha256=...` (Bitbucket Cloud) as HMAC-SHA256
  of the body, or `X-Gitlab-Token` (GitLab),
  all in constant time. An unknown application and one without a webhook secret look the
  same from outside. A bad signature is counted against that application, not the address:
  after 10 in 15 minutes its hook answers `429` (`"error": "locked_out"`, with `Retry-After`)
  for 15 minutes, while every other application's deliveries keep arriving. A forge sends
  every customer's deliveries from a few shared addresses, so counting against the address
  let anyone with an account on the same forge lock it out of the console. Deliveries larger
  than 5 MiB are refused with `413`. The per-application secret is stored in the Noust store
  (`0600`), because verifying an HMAC needs it.

What Noust itself sends off the machine: git fetches from your repositories, certbot's
requests to Let's Encrypt, notifications you configured, and, on every CLI run whose cached
answer is older than five minutes, an update check over HTTPS: the package repository this
server installs from (apt, dnf, zypper, or PyPI for pip) for the version it can install, and
`api.github.com` for the latest published release. When a release newer than the one running
fixes a vulnerability, the check says so apart from an ordinary update: a release whose GitHub
title ends in `(security release)` (written by the release workflow when a changelog bullet starts
with `SECURITY:`) is shown in the CLI banner, in `noust health`, in `GET /api/system/version` and in
Settings > About, with the advisory its notes name. The list of recent releases is read only when a
newer release exists at all. `noust config set updates.check false` turns it off. A server can also
be told to install what the check finds by itself, inside a window, for the releases you name and never a
new major version (`noust self-update`, see [release-policy.md](release-policy.md#updating-by-itself)); it
is off by default and its runs are in the audit trail.

## Untrusted repositories

- Nothing is written through a symlink found inside a release or under `shared/`. A tracked
  link to `/etc` does not become a path Noust writes to as root.
- Inspecting a source reads its example environment files (`.env.example`, `.env.template`,
  `.env.sample`, at the root and under `apps/*`, `packages/*` and `services/*`) without
  following a symlink: each file is opened with `O_NOFOLLOW` and must be a regular file, and
  a linked workspace directory is not entered, so a link to `/etc/shadow` or another
  application's `.env` is not read. An inspection never returns the default of a variable
  whose name marks it as a secret, or whose default carries a password inside a URL.
- Persistent paths must be relative and may not contain `..`.
- Every value interpolated into a systemd unit (the start command, the working directory,
  environment variables) is validated and escaped: a newline cannot start a new directive.
- Release ids are validated before they reach the disk, and a commit id must be hexadecimal
  before it becomes part of a directory name.
- `ServiceManager` refuses to touch a unit that Noust does not own, whatever the caller.
- A repository's `noust.yaml` and `noust.nginx.yaml` are read with closed schemas: an unknown key
  is refused, and the values of a route (its path, sizes, timeouts, rates and redirect target) are
  checked to be exactly what their directive takes. A `static` route stays inside the application.
  `custom_directives` in `noust.nginx.yaml` is the exception: its lines go into the site's
  `server` block as written, behind only the web server's own test, so read it as you would read
  a site before deploying a repository you do not control.
- A web server site that someone else wrote is never rewritten or deleted by a deploy: Noust
  recognises its own sites by their marker, and leaves any other file alone.
- The servers files of a Compose relay (`/etc/nginx/noust-upstreams/<app>/<service>.servers`)
  hold only `server 127.0.0.1:<port>;` lines, are written atomically, never through a link, and
  names that become their path are validated as a single inert component.

## Files

| Path | Mode | Holds |
|---|---|---|
| `/etc/noust/` | `0700` | Configuration and the console's state |
| `/etc/noust/config.yaml` | `0600` | Configuration; the notification channels' secrets are encrypted in it. `noust config get` and the API print secrets as `***`; `noust config show` prints them in clear |
| `/etc/noust/secrets.key` | `0600` | The key of the encrypted secrets (the channels' in `config.yaml`, the backup destinations'). Keep it with the backup of `/etc/noust`: without it those secrets are entered again |
| `/etc/noust/web-secret` | `0600` | Signing key for sessions and token hashes |
| `/etc/noust/web-token` | `0600` | Hash of the master token |
| `/etc/noust/web-totp` | `0600` | TOTP secret and backup code hashes |
| `/etc/noust/web-sessions.db` | `0600` | Sessions, API token hashes, WebSocket tickets |
| `/etc/noust/web-audit.log`, and its closed files `web-audit.log.<UTC stamp>` | `0600` | Audit trail, chained |
| `/etc/noust/audit-key` | `0600` | HMAC key of the audit chain; never shipped |
| The store, under `/var/lib/noust/` (`noust store path` names the file) | `0600` | The store: applications, deployments, jobs, webhook secrets, accounts (TOTP secrets encrypted) |
| `totp.key` beside the store | `0600` | Key the accounts' TOTP secrets are encrypted under; never in the store itself |
| `secrets/backups/sidecar-mac-key` beside the store | `0600` | Key that signs every backup's metadata sidecar |
| `incidents/` beside the store | `0700`, files `0600` | Evidence packages of `noust incident freeze` |
| `incident-lockdown.json` beside the store | `0600` | Present while the console is locked down for an incident |
| `ens-reports/` beside the store | `0700`, files `0600` | Evidence bundles of `noust ens report` |
| `/var/lib/noust/job-logs/` | `0700`, files `0600` | Output of console jobs |
| `/var/lib/noust/deploy-logs/` | `0750`, files `0640` | Build logs, readable by an admin group |
| `/var/cache/noust/build/<app>/` | owned by `noust-build` | An application's build caches (npm, pip, ...), one directory per application |
| `/run/noust/sandbox/` | root | Scratch space of the build sandbox and its self-test |
| `.env`, `shared/.env` | `0600` | Application environment, owned by the application's account (the service account for one from before 3.2) |
| `/etc/nginx/noust-upstreams/<app>/` | root | The servers files a Compose relay switches while a service is recreated |
| `/etc/systemd/system/*.service` | `0644` | Units. See the note below. |

The store falls back to `~/.local/share/noust/` when `/var/lib/noust` is not writable; `noust
store path` prints where it is. The console state directory can be moved with
`NOUST_WEB_STATE_DIR` (`WASM_WEB_STATE_DIR` is still read when it is not set).

Environment variables given when an application is created (`noust create --env-file`, or
`env_vars` in `POST /api/apps`) are written into its `.env` file (`0600`, owned by the
application's account, `shared/.env` on the releases layout), and the unit loads it with
`EnvironmentFile=`. Only `PORT` and `NODE_ENV` - not secret, and Noust's to decide - stay
inline in the unit as `Environment=` lines. systemd lets `EnvironmentFile=` override
`Environment=`, so `noust env configure` and the console's Environment tab both refuse to set
either one there; change the port by redeploying (`noust create -d <domain> -s <source>
--port <port>`, or `POST /api/apps` with the new port) instead. A unit an earlier
version wrote, with every variable inline, keeps working as it is; the next `noust update` or
redeploy moves them into the `.env` file.

## Audit log

Every event goes through one function and one closed catalogue (`noust audit events` lists it),
into `/etc/noust/web-audit.log`: one JSON line per event with its time, a sequence number and
id, the event and its category and severity, the actor (kind, name, role, and how it came:
session, token, fleet, CLI with the operating-system user), the client address, the target,
the outcome, details and a correlation id shared by everything one request, command or job did.
A 3.0 line is still a valid line, and the 3.0 fields are still there. Credentials are never
written to it.

Recorded:

- every change made through the API and every command that changes something, with its
  `--reason` when given (required under the ENS profile);
- sign-ins, sign-outs, failures, lockouts, refusals for a permission, CSRF or sudo mode;
- accounts, roles, invitations, tokens, sessions, second factors and passkeys;
- sensitive reads: revealing an `.env` or a secret, exporting with secrets, reading the audit
  log itself;
- approvals, from request to use; accepted risks; the sandbox turned off for an application;
- with `audit.host_activity` (`mutations` by default), every process Noust runs and every file
  it writes that changes the system, linked to the event that caused it; read-only probes are
  not recorded.

**Integrity.** Each line carries the MAC of the previous one (HMAC-SHA256 under the chain's own
key), and a signed checkpoint is written every `audit.checkpoint_minutes`. `noust audit verify`
reports the first broken link. Root holds the key and could rewrite a consistent chain, so a
pass proves consistency, not originality: compare the head with a copy shipped off the machine.

**Shipping.** To journald wherever its socket exists, to RFC 5424 syslog receivers
(`audit.syslog`: Unix socket, UDP, TCP with octet counting, or TLS with an optional client
certificate and a pinned key), and to standard output in a central's container. Each destination
has an on-disk queue and is retried; one that falls more than `audit.sink_lag_minutes` behind is
reported. `noust audit status` says whether the trail works and where it goes; a failing trail
shows in the console and in `noust health`.

**When the trail cannot be written** (the disk is full, the directory went read-only, the file
cannot be opened) an action is never lost for it by default: `audit.on_failure: warn` makes the
change and is loud, since the failure is kept as state, shown in the console and in `noust
health`, and followed by an `audit.recovered` event when a write works again. Set
`audit.on_failure: refuse` and, under `security.profile: ens-medium` always, no change goes
unrecorded instead: while the trail cannot take an event, every change through the API (and its
webhooks, `POST /hooks/...`) answers `503 audit_unavailable` with the reason and the way out, and
every command that changes something exits 1 before doing anything. Reading, signing in and out,
`noust audit`, `health`, `ens`, `config`, `setup`, `incident` and `web`, which are how the trail
is put right, stay open, and so do the units and timers (a scheduled backup that did not run is
worse than one whose line was not written; their failure keeps shouting). The server looks at
the log's directory *before* the change (it exists, can be written to, has room) so the first
change after the disk filled is the first one refused, not the last one made blind; a look is
never a write. A refusal is itself an `audit.refused` event, and the write that records it
working is also what shows the trail has recovered.

**Retention and flooding.** The file is closed every day and when it reaches
`audit.rotate_mb`. Closed files are deleted once older than `audit.retention_days` (365, at
least 90) **and** received by every destination, and the deletion is itself an event that
anchors the chain's new start; `audit.max_total_mb` caps the disk it takes. A burst of identical
events is collapsed into one with a count. Jobs, deployment records and ended sessions have their
own retention (`retention.*`).

Read it from Settings > Audit log, `noust audit list|show|export`, or `GET /api/audit` (all need
`audit.read`: a `security` or `auditor` account). `noust audit review` records a periodic review.

## Backups

A backup holds the application's `.env` and its database dumps, so it is a secret wherever it
goes (ENS mp.si.2, mp.info.6):

- **Integrity.** Each backup's metadata sidecar (`<id>.json`) carries an HMAC-SHA256 under a key
  kept in the secret store, not beside the backups. The MAC covers the archive's SHA-256, so an
  archive and a sidecar replaced together on the backup storage or on a remote are caught by
  `noust backup verify`, which also says when a backup predates the MAC or was signed by
  another server.
- **Encryption on the way out.** Under `security.profile: ens-medium`, or with
  `backup.encryption: required`, no backup or database dump is uploaded to a destination that is
  not encrypted: the refusal comes before rclone runs, at the one place every upload passes.
  Encrypt a destination with `noust backup destination update <name> --encrypt` and keep its key
  (`show-key`) apart from the server. `noust backup destination add` encrypts every remote
  destination unless you pass `--no-encrypt` (a `local` one, a disk this server mounts, is not),
  and `noust health` names the remote destinations made before that, or without it, that still
  send backups in clear.
- **Proven restorable.** A scheduled backup deep-verifies what it took (the archive unpacked with
  the extractor a restore uses) whenever the last good verification is older than
  `backup.verify_days` (7; the ENS profile never allows longer), and sends nothing that fails.
  Every verification, from any caller, is a `backups.verify` audit event.
- **The central itself.** `noust central backup` writes the configuration, the store (a
  consistent SQLite snapshot taken while it runs), the secrets and the keys into one file,
  encrypted and authenticated under a passphrase that is typed and never stored (scrypt,
  AES-256-CBC by openssl, HMAC-SHA256, the construction of sealed secrets). `--verify` proves a
  copy against its manifest; `--decrypt` gives the archive back for a restore.

## Secrets at rest

Noust keeps a secret in a file only root can read, beside the data it belongs to. Two kinds are also
**encrypted** under a key in a file of its own (`/etc/noust/secrets.key`, 0600): the credentials of
the backup destinations and the secrets of the notification channels in `config.yaml` (webhook URLs,
bot tokens, the SMTP password, a mail provider's key, a webhook's headers). That protects against a
copy: a `config.yaml` pasted in a ticket or kept in a backup that left the key out discloses nothing.
It is not a protection against root on the same machine, who reads the key as well, and Noust does not
say it is. The rest of the guarantee is about the copies too:

- **Where they live.** Credentials Noust holds for itself (a registry's password, a backup
  destination's key, a GitHub App's private key, a node's token) are one 0600 file each under
  `secrets/` beside the store, and the database holds only their names. `/etc/noust/config.yaml`
  holds the few that are configuration: the notification channels' secrets encrypted as said, and
  the database administrators' passwords as text, 0600 as well. `noust config protect` encrypts
  what a file written before 4.0 still holds as text (the console does it when it starts), and
  losing `secrets.key` makes those channels unreadable until their secrets are entered again.
  A central can seal the secret store under a passphrase (`docs/CENTRAL.md`).
- **They do not travel.** No read of the API returns one (the configuration, the variables and the
  exports show `***`; what reveals a secret on purpose needs a name or a parameter, a permission
  and sudo mode, and leaves an audit event); `noust app export` leaves them out unless
  `--with-secrets`; `noust store export` masks the secret variables of applications and services
  and the credentials of source addresses unless `--with-secrets` (audited as
  `store.export.secrets`); the evidence package of an incident carries a copy of the store with the
  columns that hold a secret value emptied, and the copy is rewritten so the old values are not left
  in its free pages.
- **It is tested, and keeps being.** `tests/test_secrets_at_rest.py` plants a secret in every place
  Noust keeps one, reads every route of the API that needs no name, and fails if any answer holds one,
  so a route added later is covered when it is added. The list of columns that hold a secret value
  is `noust.core.store_secrets`; a test reads the real schema and fails on a column that reads as a
  secret (or holds a value under another name) that nobody has ruled on, so a table another area adds
  cannot put a credential into a snapshot unnoticed.

What this does not do: a backup of the application (`.env`, database dumps) is a secret by design and
is protected by being encrypted on the way out (below); `noust config show` and
`noust store export --with-secrets` print secrets in clear to the root who asks.

## Incidents

`noust incident freeze --reason "<incident reference>"` takes an owner-only evidence package
(the audit log and its verification, journal excerpts, the configuration without secrets, a store
snapshot with the secret values taken out of it (`store/scrubbed.json` says how many rows), running
units, listening sockets, the console's sessions and tokens without hashes),
writes a `MANIFEST.sha256` of it (`sha256sum -c` checks it) and records that manifest's hash in
the audit log, which is shipped off the machine. It then locks the console down: every new
session of an account is refused where sessions are made, and only the master token - the
break-glass way in - still signs in. `--revoke-sessions` also ends the sessions open now;
`noust incident unfreeze --reason ...` lifts the lockdown.

## Compliance (ENS)

`security.profile: ens-medium` fixes the values Spain's Esquema Nacional de Seguridad, category
MEDIUM, expects (sessions, lockout, passwords, token lifetimes, audit retention, approvals,
`--reason`, backup encryption, the console's certificate), stated once in
`noust.core.ens.profile` and read by every area. `noust ens check` compares the server with it
(exit 0, 1 or 2), `noust ens report` writes the evidence bundle, and the console shows both under
Settings > Security > Compliance. Code can be restricted to named origins with
`security.allowed_sources`. The full mapping to the RD 311/2022 measures, and what stays the
organisation's, is in [ENS.md](ENS.md).

## The monitor

`noust monitor` reports what it sees and does nothing else: it never signals or kills a
process, never deletes or modifies a file other than its own unit, and never decides anything
from a process's command line. Each minute it keeps the busiest processes, with their command
lines redacted, for the timeline of a stretch of time; the redacted line is shown only to who may
read command lines, and is kept for 26 hours. See [MONITOR.md](MONITOR.md).

## Reporting a vulnerability

Please report security issues privately, through a GitHub private advisory or by email to
yago.lopez.adeje@gmail.com, rather than in a public issue. The process, the timeline and what
is in scope are in [SECURITY.md](../SECURITY.md).
