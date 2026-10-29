# Authentication

Part of [Security](security.md).

**Accounts.** People sign in with a username, a password and a second factor. Passwords are
hashed with scrypt from the standard library, with fixed, versioned parameters; the shortest
accepted is `auth.password.min_length` (12; 14 under the ENS profile). Every account enrols a
second factor, an authenticator app (TOTP) or a passkey, at its first sign-in and can do nothing
else until it has. Accounts are created with `noust user create` (password at a hidden prompt or
with `--stdin`, never in argv) or by a one-use invitation (`noust user invite`, valid 24 hours by
default; only its digest is stored), which is also how a person who lost their authenticator
gets back in. Disabling an account ends its sessions and tokens at once. Each account's TOTP
secret is encrypted at rest under a key kept beside the store, never in it.

**By mail.** When the server can send mail (`monitor.smtp.*` or `monitor.email_api.*`) and the console has a
public address (`web.public_url`), two things are mailed to an account's address (`noust user set-email`,
or the account's page): an **invitation** (`noust user invite NAME --email ADDRESS`, or the address field
of the invitation dialog) and a **link to choose a new password** ("Forgot your password?" on the sign-in
page). The link carries its code after the `#`, which a browser never sends to a server, so no access
log holds it. A reset link is good once and for 60 minutes, only its SHA-256 is stored, a new request
spends the one before it, an account is sent at most three an hour, and it is issued only to an active
account that has a password, an address and a second factor. Using it needs a code of that second factor
as well, so an inbox alone is not an account, and it changes the password and leaves the authenticator
as it was; it signs the account's other sessions out. The request is answered the same whether the
account exists, and the mail is sent after the answer. The address is never a way to sign in. Under the
ENS profile accounts are recovered by a security officer's invitation, and password reset by mail is off.

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
never adopted. Tokens start with `noust_tok_` (those issued before 3.0 start with the old product's
prefix and keep working; `noust health` names the ones still in use), are
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

- Cookie `noust_session`: `HttpOnly`, `SameSite=Strict`, `Secure` whenever the request arrived
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
session's CSRF token in the `X-Noust-CSRF` header; the console reads it from the `noust_csrf`
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
[Outbound requests](threat-model.md#outbound-requests)).
