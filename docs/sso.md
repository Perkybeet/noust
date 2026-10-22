# Single sign-on, directories and provisioning

Noust 4.0 lets people sign in to the console with the identity provider the organisation already
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
command line (`noust auth oidc`). Three more ways to the same admission follow the OpenID Connect
chapters: [SAML 2.0](#saml-20), [a directory](#directories-ldap-active-directory) (LDAP and Active
Directory) and [provisioning](#provisioning-scim) (SCIM). Which to use:

| If the organisation has | Use | People sign in with |
|---|---|---|
| An OpenID Connect provider (Keycloak, Okta, Entra, Google) | `noust auth oidc` | A button; the provider's own page |
| A provider that only speaks SAML (ADFS, Shibboleth, an Entra enterprise app) | `noust auth saml` | A button; the provider's own page |
| An LDAP or Active Directory server and no provider in front of it | `noust auth ldap` | The sign-in page's name and password |
| Any provider, and a need to retire access when someone leaves | `noust auth scim`, beside one of the above | (it signs nobody in) |

All of them are one admission (`noust.core.accounts.federation`): who comes in, what a new account
starts as, what groups mean, and **the account's own second factor, which none of them replaces**. A
name belongs to one source, whatever its protocol.

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

Every setting can also be changed in the console (Settings > Single sign-on) and in the API
(`PUT /api/auth/oidc/config/{provider}`, in sudo mode, `accounts.manage`), which is what that screen
calls. Only the settings named change.

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

* **Roles at a scope**: `--map-binding GROUP=ROLE@TEAM[/PROJECT[/ENVIRONMENT]]` gives a role held at
  a project or an environment, built-in or custom (`release-manager@acme/web`), as an access binding
  marked with its source (`oidc:corp`, `saml:entra`). It is never `security`, `auditor` or `owner`, and it is taken back when the
  group is left, like the rest.
* **The same options on every source**: `noust auth oidc|saml|ldap|scim set` take `--group-sync`,
  `--map-role`, `--map-team`, `--map-binding` and `--clear-mappings`, and the console has one box for
  them (`GROUP=ROLE`, `GROUP=TEAM:ROLE`, `GROUP=ROLE@TEAM/PROJECT`, one per line; everything before the
  last `=` is the group, so a directory's group name can hold commas and equals signs).

Providers that put too many groups in the token send a pointer instead (Microsoft Entra above 200); Noust reads
only what is in the token and the userinfo answer.

## GitHub

GitHub is OAuth 2.0 without OpenID Connect: no ID token, no discovery document, no signing keys. Noust
signs people in with it all the same, as a provider of `--kind github`, and everything after that is the
admission every other source goes through (provisioning, linking by a verified address, groups, the
second factor).

```bash
# 1. At GitHub: Settings > Developer settings > OAuth Apps > New. Its Authorization callback URL is the
#    one 'noust auth oidc show github' prints (https://your-console/api/auth/oidc/callback).
# 2. Here: the client id, and the client secret at a hidden prompt.
noust auth oidc set github --kind github --client-id Iv1.abc123 --prompt-secret --label GitHub
noust auth oidc test github
noust auth oidc link github maria 583231       # her numeric GitHub id, not her login
noust auth oidc set github --link-by-email     # or: link a first sign-in by a verified address
```

* **Endpoints are fixed**: github.com, or the GitHub Enterprise Server named with `--issuer
  https://ghe.example.com` (HTTPS). The API is `https://api.github.com`, or `/api/v3` of the server.
* **The person is `(issuer, numeric id)`.** A login can be renamed and then taken by somebody else, so it is
  only ever a hint for the name of a new account. The id never changes.
* **Only an address GitHub says is verified is believed, and only the primary one.** The address on a
  profile is whatever its owner typed and is ignored. A person with no verified primary address is refused
  while `--require-email-verified` is on (the default).
* **The access token lives for one sign-in.** It is bought with the code (PKCE and the client secret),
  used to read the profile, the addresses and, if asked, the organisations, and dropped: it is never
  stored, logged or put in a ticket. The scopes are `read:user user:email`, and `read:org` when groups
  are mapped; Noust never asks for one that writes.
* **Groups** (`--group-sync`) are the organisations a person belongs to (`acme`) and their teams
  (`acme/web`): `--map-role acme/web=operator`. A private organisation shows up only when the OAuth App
  has been approved for it by the organisation.
* `allow_signup=false` is sent: signing in here never makes anyone a GitHub account.
* Providing a way in is not the same as letting everybody in. Provisioning is `off` until an
  administrator changes it: with `auto`, restrict who can come with `--allowed-domain` or, better, by
  mapping an organisation to the role.

## SAML 2.0

For the provider that only speaks SAML: Microsoft Entra enterprise applications, Okta, Keycloak,
ADFS, Shibboleth, Google Workspace's SAML apps. It is the same admission as OpenID Connect (who comes
in, groups, the second factor); only the protocol that proves the person differs.

```
noust auth saml set entra --metadata-file entra-federation-metadata.xml --label "Entra ID" \
    --provisioning invite --allowed-domain acme.test
noust auth saml metadata entra --origin https://noust.acme.test > noust-service-provider.xml
noust auth saml test entra --origin https://noust.acme.test
```

1. **Register Noust at the provider** as a service provider. `noust auth saml metadata NAME` prints
   the document the provider can import, and the console serves it, without a credential, at
   `https://<the console's name>/api/auth/saml/metadata/NAME`. It says the **entity id** (that same
   address, unless `--sp-entity-id` sets another), the **response URL** the signed answer is posted to
   (`https://<the console's name>/api/auth/saml/acs`, HTTP-POST binding) and that assertions must be
   signed.
2. **Tell Noust who the provider is**: its metadata (`--metadata-file FILE`, or `-` for standard
   input), or `--entity-id`, `--sso-url` and one `--certificate-file` per signing certificate. The
   certificates are the **only** keys a signature is checked with; a key the message carries is never
   used. During a rollover give the old and the new together; giving the list again replaces it.
3. **Say how people are found**: by default a person is their `NameID`, which must be a persistent
   identifier (a transient one, new at every sign-in, is refused: it would make a new person each time).
   An e-mail address or a username as the `NameID` is accepted but is only as stable as the provider
   keeps it; an attribute that never changes (Entra's `objectidentifier`, Okta's `user.id`) is better.
   `--subject-attribute` names another attribute; `--email-attribute`, `--name-attribute`,
   `--username-attribute` and `--groups-attribute` say where the rest is. A `NameID` that declares itself
   an e-mail address is read as one; a persistent one never is.
4. **Check it**: `noust auth saml test NAME` reads the certificates (and says how long they are good
   for), the sign-in address and, with `--origin` (or `web.public_url`), what Noust tells the provider.

**A sign-in** goes like the OpenID Connect one: `GET /api/auth/saml/start?provider=NAME` remembers the
request (its ID, valid ten minutes, single use) and redirects with an `AuthnRequest` over the
HTTP-Redirect binding; the provider posts its signed response to `POST /api/auth/saml/acs`; Noust
verifies it, decides who the person is, and redirects to the sign-in page with the same single-use
ticket, collected at `POST /api/auth/oidc/complete`. The provider is a first factor; the account's own
second factor follows ([below](#the-second-factor)).

**What Noust checks.** The XML is read under a strict profile (`noust.core.accounts.saml_xml`):

* No DTD, entity, comment or processing instruction anywhere: a signed document never needs them and
  they are how two parsers read one message two ways.
* The signature is an enveloped one on the assertion, or on the response that holds it, made with RSA or
  ECDSA and SHA-256 or stronger (SHA-1 and RSA keys under 2048 bits are refused), over **exclusive
  canonicalisation** without comments. It has **one reference, to the element it is in by its own `ID`**,
  and every `ID` in the message is unique: that is what defeats signature wrapping. What is read is what
  was verified, never a copy elsewhere in the document; a response with a second assertion, or one moved
  elsewhere, is refused.
* The issuer is the provider's entity id; the audience is this server's entity id; the destination and
  the recipient of the bearer confirmation are this server's response URL; the response answers **the
  request this sign-in made** (`InResponseTo`); the time conditions hold, with `--clock-skew` of
  tolerance (60 s, 300 at most); a condition Noust does not know is a refusal; the assertion's own `ID`
  is kept, as a digest, until it could no longer be valid, so it cannot be presented twice.
* The identifier is `NameID` or `--subject-attribute`; the e-mail, the name and the groups are
  attributes. A certificate's expiry is reported by the test but is not checked at sign-in: the trust
  is in the pinned key, as providers often keep signing with a certificate past its date. A required authentication context class can be named with `--require-context` (stricter,
  never looser).

**What it will not do**, said plainly:

* **A sign-in the provider starts** (an unsolicited response, the "IdP-initiated" flow) is refused: it
  has no request to answer, which is what makes a replayed response detectable at all. Start the
  sign-in from the console's button; where a provider only offers its own portal tile, point the tile
  at `https://<the console>/api/auth/saml/start?provider=NAME`.
* **Encrypted assertions** are refused. The connection is TLS already; a decryption key is one more
  secret to keep. Turn encryption off at the provider and keep the signature.
* The `AuthnRequest` is **not signed**. Providers that insist on a signed request cannot be used.
* **Single logout** is not implemented: ending a session at the provider does not end the console's.
  Disabling the account (`noust user disable`, or SCIM) does.
* The metadata is read, not verified: if it carries a signature it is ignored, and what it says is
  shown for you to check against what the provider's administrator tells you.
* **It needs HTTPS.** The answer is a form the provider's page posts to the console, a cross-site POST,
  and a browser only sends the binding cookie with it when the cookie is `SameSite=None; Secure`. Over
  plain HTTP no SAML sign-in can finish, and `noust auth saml test` says so.
* The code is Noust's own (about a thousand lines over the standard library and `cryptography`, which
  passkeys and OpenID Connect already use, imported only when SAML is). No SAML library was added as a
  dependency. It has tests for the attacks above (`tests/test_saml.py`, `tests/test_saml_xml.py`) but no
  third-party audit: try a new provider with an account you can afford to lose before turning anything
  else off.

## Directories (LDAP, Active Directory)

For the organisation that keeps its people in a directory and wants one password for everything. The
sign-in page asks which directory to sign in against and takes the **name and password the person has
there**.

```
noust auth ldap set ad --host ldap.acme.test --user-base ou=people,dc=acme,dc=test \
    --bind-dn cn=noust,ou=system,dc=acme,dc=test --prompt-password \
    --provisioning invite --label "Acme directory"
noust auth ldap test ad
```

* **The directory proves the password.** Noust finds the person with a search you write as a template
  (`--user-filter`, default `(&(objectClass=person)(uid={username}))`), then **binds as the entry it
  found**; it never compares, stores or sends an empty password (an empty password with a name is an
  anonymous bind, which most directories accept). What was typed goes into the search as one escaped
  value (RFC 4515): it can never add a condition. A name that matches two entries refuses the sign-in.
* **Who a person is** is the directory's stable identifier (`--subject-attribute`: `entryUUID` for
  OpenLDAP, 389 and FreeIPA, `objectGUID` for Active Directory, which is kept as hex), never their name
  or their e-mail. An entry without one is refused. For Active Directory: `--user-filter
  '(&(objectClass=user)(sAMAccountName={username}))' --subject-attribute objectGUID --username-attribute
  sAMAccountName`.
* **The service account** (`--bind-dn`) searches. Its password is typed at a hidden prompt or piped
  with `--password-stdin`, never an argument, and is kept in `secrets/ldap/<name>` (0600), never in the
  database or `noust config show`. Without `--bind-dn` the search is anonymous.
* **The connection is TLS**: `--tls ldaps` (port 636, the default) or `--tls starttls` (389), with the
  certificate and host name verified (`--ca-file` for a private authority). Plain text is possible only
  to this machine. The host passes the same outbound guard as every address somebody typed: a directory
  on a private network is allowed by naming it in `security.allow_private_hosts`. Every message is
  bounded and the whole exchange has a deadline (`--timeout`).
* **Groups** come from `--groups-attribute` (`memberOf`), or a search (`--group-base`,
  `--group-filter` with `{dn}` and `{username}`), and go through the [mapping](#groups) like the other
  sources'.
* **A wrong name and a wrong password are one answer**, counted against the address and the name like a
  wrong password; a directory that cannot be reached is a `503` and is not counted; what was typed never
  reaches the audit trail.
* The client is a few hundred lines of BER over the standard library, not a dependency: bind, search,
  StartTLS and unbind. **Not there**: SASL, referrals, paged results and the nested-group matching rules
  of Active Directory (list the groups a person is in on the entry, or search them).

## Provisioning (SCIM)

Retiring a person at the identity provider has to retire their access, and waiting for their next
sign-in is too late: a session or an API token they hold keeps working. **SCIM 2.0** (RFC 7643 and 7644)
is how the provider says so as it happens. Noust is the SCIM server; the provider calls it.

```
noust auth scim set entra --label "Entra ID" --default-role viewer --allowed-domain acme.test \
    --group-sync --map-role noust-ops=operator
```

The first run prints the **endpoint** (`https://<the console>/api/scim/v2`) and a **bearer token**,
once. Give both to the provider (Entra: *Provisioning > Tenant URL* and *Secret token*; Okta: the SCIM
2.0 connector's base URL and *HTTP Header* authentication). Only a SHA-256 of the token is kept, 256
random bits needing no slow hash; `--rotate-token` issues another and the old one stops at once.

What a provider can and cannot do:

* **It creates the accounts of people it tells Noust about.** They are `invited` and have no credential:
  the person's first sign-in at an identity provider (OpenID Connect, SAML or a directory), whose
  verified e-mail is the account's, activates it. SCIM keeps the roster; single sign-on signs people in;
  the e-mail ties them. `--link-by-email` attaches the person to an account that already has that
  e-mail, as a first sign-in would, with the same conditions. A person sent without an e-mail is
  created as well (a minimal `POST` is valid SCIM) and nobody can sign in as them: the provider may
  send the e-mail later, once, with the checks creating the person would have had, or an
  administrator links them by hand. A source limited to some domains refuses a person with none.
* **It switches an account off** when the person is deactivated or deleted at the provider, which ends
  their sessions and API tokens at once, and **back on only if it was the provider that switched it
  off**: an administrator's own decision is never overridden.
* **Its groups**, pushed as they change, go through the same mapping every source has: roles, team
  memberships and roles at a project or environment. Leaving a group takes effect at once.
* **It never** reaches an account it did not provision, changes an account's e-mail (separation of
  duties is judged by it), deletes an account (the audit trail keeps naming somebody), or gives
  `security`, `auditor` or a team `owner`.
* Supported: `/Users` and `/Groups` with `GET` (the filter every provider uses, `userName eq "..."`,
  `externalId eq` or `id eq`, and `displayName eq` for groups; paging), `POST`, `PUT`, `PATCH` (the forms
  Entra and Okta send) and `DELETE`; `POST .../.search` (the query in a body, also over users and groups
  together at `/.search`); `attributes` and `excludedAttributes` on every answer; and
  `/ServiceProviderConfig`, `/ResourceTypes` and `/Schemas` (list responses, and one by its id). Any other
  filter is a `400 invalidFilter` rather than ignored, and a path or a method Noust does not have is
  answered in the protocol's own error body.
* **Kept and not kept.** What Noust models is the user name, the external id, the display name (the
  `name.formatted`), the first e-mail, `active` and the groups. Phone numbers, addresses, title and the
  other core attributes a provider sends are accepted and not kept: Noust has no use for them, and a
  provider that sends them must not be refused. They are not read back.

`noust auth scim show` lists the sources; `noust auth scim users NAME` lists who a source provisioned. A
wrong token is a `401` counted against the address like a wrong password; a right one is never counted,
however many calls a synchronisation makes and however many of them are answered 404.

## Checked against real software

The unit tests drive each protocol with providers written for them. `tests/integration/identity.py`
(not part of pytest: it drives Docker and the network) drives the same code with programs written by
other people, in containers named `noust-test-*` that it removes:

| Software | What is held to it |
|---|---|
| Keycloak (SAML) | Noust configured from Keycloak's own metadata and Keycloak registered from Noust's; sign-in with the assertion signed, the response signed and both; the same person is the same account; a replayed response, a response changed after signing and an encrypted assertion are refused; a group gives the role the mapping says. |
| Keycloak (OpenID Connect) | The discovery document and keys, a real ID token with PKCE, groups giving a role, a replayed callback refused. |
| OpenLDAP | Plain text on this machine, StartTLS and LDAPS verified against a private authority (and refused against another), a service account that may only read, group search, `entryUUID` as who someone is, a name written to widen the search, an empty password; and the same through the console's own route. |
| `scim2-tester` (a SCIM 2.0 conformance client) | Discovery, create, read, replace, patch, delete, filters and projection for users and groups. What it reports that Noust does by design (attributes accepted and not kept, an e-mail that is not changed once set) is listed in the harness with its reason; anything else fails the run. |

Running a real client found what the unit tests had not: a provider's own calls were counted against
the sign-in lockout, so its first synchronisation locked its address out; the discovery documents were
bare arrays and not list responses; `attributes`, `excludedAttributes` and `.search` were missing; and
a user sent with only a name was refused.

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
| `auth.saml.login`, `auth.saml.link`, `auth.saml.provision`, `auth.saml.config` | The same four for a SAML provider; a refused response names its reason (`saml_invalid` with what was wrong, `replay`, `binding`, `unknown_state`, `idp_status`). |
| `auth.ldap.login`, `auth.ldap.link`, `auth.ldap.provision`, `auth.ldap.config` | The same four for a directory; what was typed is never in them. |
| `auth.scim.provision`, `auth.scim.link`, `auth.scim.deprovision`, `auth.scim.update`, `auth.scim.denied`, `auth.scim.config` | A provider's calls: a person provisioned, attached to an account, switched off; a change or a request that was not applied; a call refused for its token; a source configured or its token issued. |
| `user.role_change`, `team.member.add`, `team.member.remove` | What group sync changed, as `system:oidc:<provider>`. |

## Turning passwords and passkeys off

Once everybody comes in through the provider, a second front door with passwords of its own is only
a thing to defend. Turn it off:

```
noust auth local-login exempt add ana      # keep ana's password and second factor as the way back
noust auth local-login off
noust auth local-login status              # who keeps a password, and who could still administer
```

The same is Settings > Single sign-on > Passwords and passkeys, and `PUT /api/auth/oidc/local-login`
(`{"enabled": false, "exempt": ["ana"]}`, sudo mode, `accounts.manage`). It is the setting
`auth.local_login.enabled`, with the accounts that keep theirs in `auth.local_login.exempt` (ten at most:
a short list is the point).

* A password or a passkey no longer signs an account in, **except the accounts in the exempt list**, the
  break-glass administrators. The refusal is decided as soon as the password is right, so a wrong
  password and a refused one cannot be told apart by an attacker.
* The provider's sign-in, **the master token** and the command line as root work exactly as before.
  Under the ENS profile the master token only recovers accounts, so the rule below is stricter there.
* **It refuses to leave nobody able to administer.** Turning it off is refused while no administrator
  could still sign in: one who keeps a password, or one linked to an enabled provider. The same rule is
  checked where the last way in would be taken away: removing or disabling a provider, unlinking a
  person, or shortening the exempt list while passwords are off is refused when it would leave nobody.
* The sign-in page then offers the providers first and keeps the account form behind "Sign in with a
  password or a passkey", with the Access token button where it was.
* Sessions that are open stay open; nothing is signed out by the switch.
* It is an audit event, `auth.local_login.change`, and a refusal names who would have been left.

## Operating it

* **If the provider is down**, nobody can use the button; the password form and the master token still
  work. Keep at least one account with a password and its second factor (a break-glass administrator,
  `noust auth local-login exempt add NAME` once passwords are off), and the access token
  (`noust web token`) for recovery.
* **Taking somebody out.** Removing them at the provider stops them at their next sign-in, but a session
  they already hold lasts until it expires (`auth.session.absolute_hours`, 12 by default, 8 under ENS).
  `noust user disable NAME` ends their sessions and tokens at once, and so does a
  [SCIM](#provisioning-scim) source when the provider deactivates the person.
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

For SAML, what matters at the provider is the same everywhere: the service provider entity id and
response URL from `noust auth saml metadata`, **assertions signed and not encrypted**, a persistent
`NameID` (or an attribute that never changes) and the attributes `email`, `displayName` and `groups`
(or the names you set with `--email-attribute`, `--name-attribute` and `--groups-attribute`).
Microsoft Entra names its claims with long URIs; copy them as they appear in *Attributes & Claims*.
Keycloak: client protocol *saml*, *Sign assertions* on, *Encrypt assertions* off. ADFS: a relying party
trust whose *Signature* is the assertion's and with no encryption certificate.

| Provider | Issuer | Notes |
|---|---|---|
| Keycloak | `https://HOST/realms/REALM` | Client with *Client authentication* on and the standard flow. Groups: add a *Group Membership* mapper (claim `groups`) or read roles with `--groups-claim realm_access>roles`. |
| Authentik | `https://HOST/application/o/SLUG/` | Ends in a slash. Set the provider's signing key, or Authentik signs with HMAC, which Noust refuses. |
| Okta | `https://ORG.okta.com` (or the authorization server's) | Add the `groups` scope and a groups claim. |
| Auth0 | `https://TENANT.auth0.com/` | Ends in a slash. Groups need an Action adding a claim. |
| Microsoft Entra | `https://login.microsoftonline.com/TENANT/v2.0` | Does not send `email_verified`: use `--assume-email-verified`. `email` is an optional claim to add. |
| Google | `https://accounts.google.com` | Sends `email` and `email_verified`; restrict to a Workspace with `--allowed-domain`. No groups. |
| GitHub | `--kind github` (no issuer) | An OAuth App; see [GitHub](#github). A GitHub Enterprise Server: `--issuer https://HOST`. |

## What is not here

* **SAML**: provider-initiated sign-in, encrypted assertions, signed requests and single logout (see
  [SAML 2.0](#saml-20)).
* **LDAP**: SASL, referrals, paged results and nested group matching rules (see
  [Directories](#directories-ldap-active-directory)).
* **SCIM**: bulk operations, sorting, `ETag`s and password changes; it never deletes an account.
* **Multi-tenant providers** (several organisations behind one issuer, as Microsoft's `common` endpoint)
  are not supported: the issuer is one value, compared to `iss` exactly.
