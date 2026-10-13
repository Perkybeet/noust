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
- Shared variables by scope ([variables.md](variables.md)): `GET`, `POST` and per-name `PUT`, `DELETE` and
  `usage` under `/api/teams/{team}/variables`, `/api/teams/{team}/projects/{project}/variables`,
  `.../environments/{environment}/variables` and `/api/variables/server`. A variable has `value`,
  `secret` and `audience` (`both`, `build` or `runtime`); a value may hold `{{team.NAME}}`,
  `{{project.NAME}}`, `{{environment.NAME}}`, `{{server.NAME}}`, `{{app.URL}}` or
  `{{secret:PROVIDER/PATH}}`.
- Secret managers: `GET /api/secret-providers`, `GET /api/secret-providers/kinds`,
  `PUT` and `DELETE /api/secret-providers/{name}` and `POST .../{name}/test`. A token is written,
  never read.
- `GET /api/apps/{domain}/env` answers `audience`, `references` and `predefined` besides
  `variables`; `PUT` takes `audience`; `POST /api/apps/{domain}/env/resolve` resolves the
  references again without a deploy.
- Previews: `POST /api/apps/{domain}/previews` (a preview by hand from a `pull_request`, a
  `branch` or a `tag`), `GET .../previews/pull-requests`, `GET .../previews/{number}` and
  `.../logs`, `POST .../{number}/redeploy`, `/stop` and `/start`, and the variables defined for
  previews under `.../previews/variables`. The settings take and answer `inherit_env`, `allow_env`
  and `data_mode`; `GET .../previews` answers `has_database`, and a preview `kind`, `ref` and
  `databases`. A Docker Compose application has previews.
- Environments: `GET /api/apps/{domain}/promotion` and `POST .../promote` put a release on the same
  application in another environment of its project, without building it again;
  `POST /api/apps/{domain}/clone` makes another application with this one's settings.
- Tags on applications: `GET` and `PUT /api/apps/{domain}/tags`, `GET /api/tags`,
  `POST /api/tags/{tag}/deploy`, and a `tags` field and a `tag` filter on the applications list.

### Deprecated

Nothing.

### Removed

Nothing.

### Breaking changes

None. The baseline for version 1 is the API as 3.3.1 shipped it.

## 3.3.1 and earlier

The API had no version and no stated promise; `/api` was the only address. Version 1 starts from
what 3.3.1 served.
