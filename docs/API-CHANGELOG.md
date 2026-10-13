# API changelog

What changed in the HTTP API, release by release, for people who write against it. The product's
own changes are in the `CHANGELOG-*.md` files; this one only says what a client can notice. The
promise it is held to, and how an operation is deprecated and retired, are in
[api.md](api.md#versions-and-compatibility).

Each release has up to four lists:

- **Added**: new operations, parameters, fields and response codes. Always compatible.
- **Deprecated**: an operation announced as going away, with its `Sunset` date and what replaces
  it. It keeps working until then.
- **Removed**: an operation retired after its `Sunset`.
- **Breaking changes**: anything a client written against the previous baseline could notice
  that is not a removal after notice (a security fix, an operation that never worked). Each
  entry says what, why and what a client does now. A change here also changes
  `panel/openapi.baseline.json` in the same commit (`python scripts/api_compat.py --update
  --accept-breaking`).

## 3.4.0

API version 1.

### Added

- Every route is also served at `/api/v1/...`. It is the same route as `/api/...` (same
  permission, same audit entry), not a copy; `/api/...` keeps working.
- `X-Noust-API-Version: 1` on every response under `/api`.
- `GET /api/openapi.json`: `info.version` is the API's version (`1`; it was the product's
  version, and `unversioned` in the checked-in file), `info.x-noust-version` the product's, and
  `info.x-noust-api-prefix` the versioned address.
- Deprecated operations announce themselves with `Deprecation`, `Sunset` and `Link` headers and
  `deprecated: true` plus `x-noust-deprecation` in the schema. None is deprecated yet.
- `/api/databases/instances`: the engines Noust runs itself, in containers, beside the host's
  (another version of an engine, or one the host does not provide): list, `catalog`, `plan`, make
  (a job, sudo mode), describe, `start` (a job), `stop`, `upgrade` (a job, sudo mode),
  `remove-plan` and remove (`confirm`, a job, sudo mode). An instance is addressed everywhere an
  `{engine}` is taken by its key (`postgresql@pg16`).
- `GET /api/databases/engines`: `managed`, `pinned_version` and `memory_limit_mb` on an engine that
  is an instance Noust runs.
- `GET /api/databases/provisioning/choices`: each flavour lists its `instances` and the
  `instance_versions` one could be made in; a version an instance runs has `state` `instance` and
  the `instance` that runs it. `GET .../provisioning/plan` takes `instance`, and answers with it.
- `POST /api/apps/{domain}/databases` and `POST /api/apps` (`database`): `instance`, the name of
  an instance to make the database on. A `version` the host does not run is answered by the
  instance that runs it, when exactly one does.
- Single sign-on through an OpenID Connect provider ([sso.md](sso.md)): `GET /api/auth/oidc/providers`,
  `GET /api/auth/oidc/start`, `GET /api/auth/oidc/callback` and `POST /api/auth/oidc/complete` (public,
  counted by the sign-in lockout), and the security officer's `/api/auth/oidc/config`,
  `/api/auth/oidc/config/{provider}` (`PUT`, `DELETE`), `/api/auth/oidc/config/{provider}/test` and
  `/api/auth/oidc/identities`. `POST /api/auth/login/second-factor` also finishes the step a provider's
  answer opens, and `auth_method` of a session may now be `oidc`.
- A site's policy ([network.md](network.md)): `GET`, `PUT` and `DELETE /api/apps/{domain}/policy`,
  `PUT` and `DELETE /api/apps/{domain}/policy/users/{username}`, and the server's
  `GET /api/policy/cloudflare-ranges` with `POST .../refresh`. A list that would refuse the caller's
  own address is a `400` on `allow_ips` until `accept_lockout` is sent.
- Domains: `port` on `GET /api/apps/{domain}/domains` and on its entries, `canonical` on the list and on
  every change, `force`, `port` and `dns` in the body of `POST /api/apps/{domain}/domains`, `dns` and
  `canonical` on every change, `PUT /api/apps/{domain}/domains/canonical` and
  `PUT /api/apps/{domain}/domains/{name}/port`. A wildcard (`*.example.com`) is a valid alias. Removing a
  name queues the certificate to be ordered again without it (`certificate_job_id`). A name another site
  already answers on is a `409` with `error: domain_overlap`. `verdict`, `cdn` and `resolver` on
  the DNS check (`here`, `cdn`, `elsewhere`, `none`, `unknown`).
- DNS: `/api/dns/providers` (list with the kinds, add and `PATCH`, `DELETE`, `.../verify`),
  `/api/dns/records`, `POST /api/dns/sweep` and `GET`, `POST /api/apps/{domain}/dns`. A record somebody
  else made at a name is a `409` with `error: record_conflict` and `hostname`, `record_id`, `current` and
  `wanted`; the same request with `replace_record` and `replace_current` replaces exactly that record.
- Certificates: `GET`, `PUT /api/certs/acme` and `POST /api/certs/acme/register` (a job),
  `GET`, `POST /api/certs/uploaded` and `DELETE /api/certs/uploaded/{name}`,
  `GET`, `PUT /api/apps/{domain}/certificate`. `source` and `challenge` on the entries of
  `GET /api/certs`, and `auto_renew` is false for an upload.
- `GET`, `POST /api/streams`, `POST /api/streams/plan` and `DELETE /api/streams/{name}`: the TCP and UDP
  ports nginx publishes. `409` when nginx cannot (`error: prerequisiteerror`, with the fix in `hint`).
- `GET`, `POST /api/tunnels`, `DELETE /api/tunnels/{name}`, `POST /api/tunnels/{name}/routes` and
  `DELETE /api/tunnels/{name}/routes/{hostname}`: Cloudflare Tunnels. A conflicting DNS record is an
  outcome of the route (`dns.outcome: conflict`), not an error.

### Deprecated

Nothing.

### Removed

Nothing.

### Breaking changes

None. The baseline for version 1 is the API as 3.3.1 shipped it.

## 3.3.1 and earlier

The API had no version and no stated promise; `/api` was the only address. Version 1 starts from
what 3.3.1 served.
