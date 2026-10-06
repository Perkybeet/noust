# Single sign-on (OpenID Connect)

Noust 3.4 lets people sign in to the console with the identity provider the organisation already
has: Keycloak, Authentik, Okta, Auth0, Microsoft Entra, Google Workspace, or any server that speaks
**OpenID Connect** with the authorization code flow. The operator configures one or more providers;
the sign-in page then shows a button, "Sign in with *Acme SSO*", above the password form.

```
noust auth oidc set corp --issuer https://sso.acme.test/realms/acme --client-id noust \
    --prompt-secret --label "Acme SSO" --provisioning invite --allowed-domain acme.test
noust auth oidc test corp
```

**The provider is a first factor and nothing else.** Whoever it lets in still owes the account's
own second factor, an authenticator or a passkey, exactly as after a password. See
[The second factor](#the-second-factor).

This is the one implementation of the rules for both the console (`/api/auth/oidc`) and the
command line (`noust auth oidc`). SAML, SCIM and LDAP are not in 3.4; the design leaves room for
them ([What is not here](#what-is-not-here)).

## How a sign-in goes

1. The person presses the button. The console opens `GET /api/auth/oidc/start?provider=corp`.
   Noust remembers the sign-in (a random `state`, a `nonce`, a PKCE verifier, all single use, valid
   ten minutes), sets a cookie that ties the answer to this browser, and redirects to the
   provider's authorization endpoint.
2. The person signs in at the provider, with whatever the provider asks (its own password, its own
   MFA).
3. The provider redirects the browser to `GET /api/auth/oidc/callback`. Noust exchanges the code at
   the provider's token endpoint (the client secret in HTTP Basic, the PKCE verifier proving the
   code was asked for by this server), reads the provider's **signing keys**, verifies the **ID
   token** (signature, issuer, audience, expiry, nonce), and decides who the person is and whether
   they come in. The browser is sent to the sign-in page with a single-use ticket, or with one
   word saying why not.
4. The sign-in page collects the ticket (`POST /api/auth/oidc/complete`). An account with a second
   factor is answered the step it owes and finishes it as after a password; one without is signed in
   and may do nothing but enrol one.

Nothing in the address of steps 1 and 3 is a credential by itself: the ticket works only from the
browser that began (the cookie, `HttpOnly`, `SameSite=Lax`) and from the address that received the
answer; a replayed answer, an answer to another browser and an answer after ten minutes are
refused.

## Setting a provider up

1. **Register Noust at the provider** as an OpenID Connect client, authorization code flow. Give it
   the redirect URI `https://<the console's name>/api/auth/oidc/callback`: the console's public name
   (`web.public_url`, which `noust console expose` sets) followed by that path. `noust auth oidc
   show NAME` prints the exact address. Over an SSH tunnel it is
   `http://localhost:8080/api/auth/oidc/callback`, which providers accept for `localhost`.
2. **Tell Noust**:

   ```
   noust auth oidc set corp \
       --issuer https://sso.acme.test/realms/acme \
       --client-id noust --prompt-secret --label "Acme SSO"
   ```

   The secret is typed at a hidden prompt or piped with `--client-secret-stdin`, never an argument
   (it would be in `ps` and in the shell history). It is kept in `secrets/oidc/<name>` beside the
   store: mode 0600 in a 0700 directory, sealed with the rest on a sealed central, never printed,
   never in the database, never in `noust config show`. A provider with no secret is a *public
   client*: PKCE alone.
3. **Check it**: `noust auth oidc test corp` reads the discovery document and the key set,
   says which algorithms the provider signs with, whether the secret is stored, and which address to
   register. It signs nobody in.
4. **Decide who may come in** ([below](#who-comes-in)), and try it.

The **issuer** is written exactly as the provider's tokens write it (`iss`): some end in a slash
(Auth0), some do not (Keycloak). When it is wrong the test says what the provider answered.

Every setting can also be changed in the API (`PUT /api/auth/oidc/config/{provider}`, in sudo mode,
`accounts.manage`), which is what a console screen would use. Only the settings named change.

## Settings

| Setting (`noust auth oidc set`) | Default | What it does |
|---|---|---|
| `--issuer` | required | The provider's issuer URL. HTTPS, except a provider on this machine. |
| `--client-id` | required | The client Noust is registered as. |
| `--client-secret-stdin`, `--prompt-secret`, `--clear-secret` | none | The secret; see above. |
| `--label` | the name | What the button says after "Sign in with". 40 characters. |
| `--scopes` | `openid email profile` | `openid` is always asked. Add `groups` where the provider needs it. |
| `--enable`, `--disable` | enabled | A disabled provider has no button and its answers are refused. |
| `--require-email-verified`, `--allow-unverified-email` | required | Refuse a sign-in whose e-mail the provider does not say is verified. |
| `--assume-email-verified`, `--no-assume-email-verified` | no | Read an `email_verified` the provider never sends as true: for a directory the organisation controls (Microsoft Entra). A provider that says `false` is never assumed otherwise. |
| `--clock-skew` | 60 s | How far the provider's clock may differ, both ways. At most 300. |
| `--provisioning` | `off` | What a first sign-in may do: `off`, `invite` or `auto`. |
| `--default-role` | `viewer` | The role of an account that provisioning creates: `viewer`, `operator` or `admin`. |
| `--default-team`, `--default-team-role` | the default team | A team the created account joins instead, and its role there. |
| `--link-by-email`, `--no-link-by-email` | no | Link a first sign-in to the account of its verified e-mail. |
| `--allowed-domain` (repeat), `--clear-domains` | any | Only e-mail addresses of these domains. |
| `--require-amr` (repeat), `--clear-amr` | none | The token must say the provider used one of these methods (`mfa`, `hwk`...). Stricter, never looser. |
| `--groups-claim` | `groups` | Where the token lists groups; nest with `>` (`realm_access>roles`). |
| `--group-sync`, `--no-group-sync` | off | Let groups decide roles and team memberships. |
| `--map-role GROUP=ROLE`, `--map-team GROUP=TEAM:ROLE` (repeat), `--clear-mappings` | none | What each group means. |
| `--redirect-uri` | derived | The callback address, when it cannot be derived. |

## Who comes in

A person the provider vouches for is identified by the pair the provider guarantees, **issuer and
subject (`sub`)**, kept in `account_identities`. **Never by e-mail alone**: a provider can give an
address to somebody else, and a server that matches people to accounts by address alone lets whoever
holds the address take the account. After the first sign-in the link is the pair, and what the
e-mail says later does not matter.

For a person never seen before, in this order:

1. **The checks on the token.** The e-mail must be verified (or assumed so, see above), belong to an
   allowed domain when there are any, and the provider must have used one of the required methods
   when there are any. While **the server has no account at all, nobody signs in this way**: the
   first account is made by the operator (`noust user create NAME --role admin`), so a provider can
   never make the first administrator.
2. **`--link-by-email`**: the verified e-mail is compared with the *person reference* of accounts
   (`--person-ref` of `noust user create` and `invite`). Exactly one active account must match;
   two are never guessed between. The account must **already hold a second factor** (otherwise whoever
   controlled the address at the provider could enrol theirs and take it) and must not already be
   linked to somebody else at this provider. It is off by default: turn it on only for a provider
   that is authoritative for the addresses of the organisation.
3. **`--provisioning invite`**: the account an administrator invited for that e-mail
   (`noust user invite NAME --role operator --person-ref maria@acme.test`) is activated, and its
   invitation code stops working: one way in, not two.
4. **`--provisioning auto`**: an account is created, active, with no password, as `--default-role`
   (never `security` or `auditor`: a person gives those), in the default team or `--default-team`.
   Its name comes from `preferred_username`, or the e-mail before the `@`, and is made unique
   with a number. Under the ENS profile it is never `admin`. An address that already belongs to an
   account that is not linked is refused rather than answered with a second account.
5. **`--provisioning off`** (the default), or nothing above applied: refused. An administrator
   links the person by hand. The refusal is in the audit log with the subject:
   `noust auth oidc link corp maria --subject <sub>`.

An account that is disabled, locked or only invited is refused whichever way it is reached. Disabling
or unlinking someone in Noust ends their sign-ins through the provider at once; removing them at the
provider ends them at their next sign-in (see [Operating it](#operating-it)).

## Groups

With `--group-sync`, on **every** sign-in Noust reads the groups the token lists (`--groups-claim`)
and recomputes what they mean:

```
noust auth oidc set corp --group-sync \
    --map-role noust-ops=operator --map-role noust-admins=admin \
    --map-team acme-ops=acme:operator --map-team acme-owners=acme:owner
```

* **Role**: the first `--map-role` whose group the person is in decides, in the order written. A
  group can give `viewer`, `operator` or `admin`; never `security` or `auditor`, and under the ENS
  profile never `admin`. An account that is a security officer or an auditor is not touched.
* **Teams**: each `--map-team` the person is in adds a membership, the strongest role if several name
  one team. A team that does not exist is skipped and said.
* **Taking back**: leaving a group at the provider takes back what it gave at the next sign-in. Only
  what the mapping gave: a role an administrator set by hand since, and a membership somebody else
  added, are left alone (the identity remembers the role a mapping gave; memberships are marked
  `oidc:<provider>`).
* If separation of duties refuses the role the groups give, the sign-in is refused: it fails closed.

Providers that put too many groups in the token send a pointer instead (Microsoft Entra above 200); Noust reads
only what is in the token and the userinfo answer.

## The second factor

**An OpenID Connect sign-in counts as the first factor. Whatever the provider says about how it
authenticated the person (`amr`, `acr`) does not replace the account's own authenticator or
passkey.** That was decided, not left open, for four reasons:

* The claims are the provider's assertion, optional, and spelled differently by each provider.
  Noust's rules are about factors Noust verified itself.
* Every account must hold a second factor (`docs/security.md`), `noust console expose` refuses
  while any account that can sign in lacks one, and sudo mode confirms with the account's own
  authenticator or passkey. An SSO account that skipped enrolment would be unable to confirm
  anything destructive, and would block publishing the console.
* A provider that is compromised or misconfigured to claim MFA for everybody would otherwise
  disable the second factor of every account that signs in through it.
* The ENS profile asks for authentication controls the audited system enforces.

What follows:

* An account with a second factor is answered the step it owes after the provider (the same
  challenge a password opens, `POST /api/auth/login/second-factor` or a passkey).
* An account without one is signed in, and its session may do nothing but enrol one (`mfa_required`).
* `--require-amr mfa` is available for the opposite: refuse a sign-in the provider did not make with
  a method you name. It can only make the sign-in stricter.
* `noust user reset-mfa NAME` works for these accounts as for any: the next sign-in enrols again.
* **Sudo mode** confirms with the account's own second factor. Where the profile also asks for the
  password again (`auth.sudo.require_password`, always under ENS), an account that has no password
  (it came in through the provider) is told how to get one, `noust user invite NAME`, which lets its owner
  set a password and an authenticator: it keeps the password only for this.
* An account made by the provider has **no password** and cannot be signed in to with one. It is not an
  invitation waiting to be accepted: `noust user disable NAME` and `enable` bring it back active.

## ENS and the rest of the security model

* The ENS profile (`security.profile: ens-medium`) limits what a provider may hand out: never `admin`,
  by default role or by group. Idle and absolute session limits, the lockout of an address on every
  wrong guess at the answer, the audit trail and the notice apply to a sign-in through a provider
  exactly as to any other.
* A person is known to the audit log by their account name, with the provider in the detail.
* Locked-out addresses are refused the callback and the ticket collection by the same lockout as a
  password (`AUTH_PATHS`). What counts as a guess: an unknown, replayed or foreign answer, a token that
  does not verify. What does not: the provider being down, the person declining, the policy refusing
  somebody the provider vouched for.
* The incident lockdown (`noust incident freeze`) refuses the new sessions of accounts, whichever way
  they sign in.

## What Noust checks, and what it will not do

* **The ID token**: the signature, against the keys the provider published *at the address Noust
  discovered* (never a key or an address the token brings: `jwk`, `jku`, `x5u` are ignored), with
  `RS256`/`ES256` and their relatives (`RS384`, `RS512`, `PS256`, `PS384`, `PS512`, `ES384`, `ES512`);
  never `none`, never HMAC, never an algorithm the provider does not advertise. RSA keys under 2048 bits
  and points off their curve are refused. Then `iss` equal to the issuer, `aud` containing the client
  (and `azp` equal to it when there are several), `exp` (with the skew), `iat` and `nbf` in the right
  direction, the `nonce` of this sign-in, and a `sub`.
* **Key rotation**: the discovery document and the key set are cached for an hour. A token that names a
  key not in the cache refreshes it once, and never more than once a minute per provider, so a stream
  of forged tokens cannot make Noust fetch without end.
* **Where it talks**: HTTPS only (a provider on this machine may use HTTP), no credentials in the
  address, no link-local or unspecified address, no redirect followed, a timeout and a size limit
  on every answer. The discovery document can only name HTTPS endpoints.
* **The answer**: `state` and the browser cookie are random 256-bit values stored as digests; the
  sign-in is single use and ends after ten minutes; the ticket is single use, lives two minutes, and
  is tied to the cookie and to the address. `next` is reduced to a page of this console, so the
  sign-in cannot be used to bounce somebody to another site.
* **The userinfo endpoint** is asked only for what the token left out (the e-mail, the groups), and
  only for the same `sub`.
* **Dependencies**: none new. The signature check is `cryptography`, which passkeys already need
  (`python3-cryptography`).

## The audit trail

| Event | When |
|---|---|
| `auth.oidc.login` | A sign-in through a provider: success, or the refusal with its reason (`token_signature`, `token_audience`, `token_expired`, `no_account`, `email_unverified`, ...). |
| `auth.oidc.link`, `auth.oidc.unlink` | A person linked to or unlinked from an account, by hand or by the first sign-in. |
| `auth.oidc.provision` | An account created or activated by a first sign-in. |
| `auth.oidc.config` | A provider added, changed or removed, with the names of the settings (never the secret). |
| `user.role_change`, `team.member.add`, `team.member.remove` | What group sync changed, as `system:oidc:<provider>`. |

## Operating it

* **If the provider is down**, nobody can use the button; the password form and the master token still
  work. Keep at least one account with a password and its second factor (a break-glass administrator),
  and the access token (`noust web token`) for recovery.
* **Taking somebody out.** Removing them at the provider stops them at their next sign-in, but a session
  they already hold lasts until it expires (`auth.session.absolute_hours`, 12 by default, 8 under ENS).
  For now `noust user disable NAME` ends their sessions and tokens at once. Automatic de-provisioning
  (SCIM) is not in 3.4.
* **Rotating the client secret**: `noust auth oidc set corp --prompt-secret`. Changing the client id
  keeps the links. Changing the issuer does not: a link is keyed by the issuer exactly as the
  tokens write it, so links made under the old value no longer match (`noust auth oidc identities`
  lists them).
* **Removing a provider** removes its secret and its sign-ins in flight; accounts keep their links and
  their other ways in. One that has no password cannot sign in until it is given one
  (`noust user invite NAME`).
* **A link by mistake**: `noust auth oidc identities`, then `noust auth oidc unlink ID`.
* **A wrong configuration** locks nobody out: the password form, the passkeys and the access token
  are untouched, and `noust auth oidc remove NAME` (root) takes the provider away.

## Provider notes

Typical values; check the provider's own documentation.

| Provider | Issuer | Notes |
|---|---|---|
| Keycloak | `https://HOST/realms/REALM` | Client with *Client authentication* on and the standard flow. Groups: add a *Group Membership* mapper (claim `groups`) or read roles with `--groups-claim realm_access>roles`. |
| Authentik | `https://HOST/application/o/SLUG/` | Ends in a slash. Set the provider's signing key, or Authentik signs with HMAC, which Noust refuses. |
| Okta | `https://ORG.okta.com` (or the authorization server's) | Add the `groups` scope and a groups claim. |
| Auth0 | `https://TENANT.auth0.com/` | Ends in a slash. Groups need an Action adding a claim. |
| Microsoft Entra | `https://login.microsoftonline.com/TENANT/v2.0` | Does not send `email_verified`: use `--assume-email-verified`. `email` is an optional claim to add. |
| Google | `https://accounts.google.com` | Sends `email` and `email_verified`; restrict to a Workspace with `--allowed-domain`. No groups. |

## What is not here

* **SAML** (checklist I-20), **SCIM** (I-21) and **LDAP** (I-22) are not in 3.4. Identities are rows of
  `account_identities` with a `protocol` column, so a SAML person is one more row; provisioning and group
  sync already work from a list of claims, which is what a SAML assertion or a SCIM push would supply.
* **Turning the password form off** when single sign-in is on (I-23) is not in 3.4. The master token is
  the break-glass way in either way.
* **A console screen** to configure providers is not in 3.4; the API above is what it will call.
* **Multi-tenant providers** (several organisations behind one issuer, as Microsoft's `common` endpoint)
  are not supported: the issuer is one value, compared to `iss` exactly.
