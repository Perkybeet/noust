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

### Deprecated

Nothing.

### Removed

Nothing.

### Breaking changes

None. The baseline for version 1 is the API as 3.3.1 shipped it.

## 3.3.1 and earlier

The API had no version and no stated promise; `/api` was the only address. Version 1 starts from
what 3.3.1 served.
