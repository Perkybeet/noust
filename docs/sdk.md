# A Python client of the API

`sdk/python` is a typed Python client of the HTTP API, generated from the same schema the console's
types come from (`panel/openapi.json`). One method per operation, grouped by area as the console groups
them, with the shape of every request and answer as a typed dictionary: an editor completes
`client.applications.list_apps()`, and mypy tells you that a field you read is not there.

It needs only the standard library and Python 3.11, and is not on PyPI yet: install it from the
repository (`pip install ./sdk/python`).

```python
import os
from noust_sdk import Client, ElevationRequired

client = Client("https://noust.example.com", token=os.environ["NOUST_TOKEN"])   # or Client.from_environment()

for app in client.applications.list_apps(team="acme")["apps"]:
    print(app["domain"], app["status"])

queued = client.applications.create_app({"domain": "shop.example.com", "source": "https://example.test/shop.git"})
job = client.wait_for_job(queued)            # returns when the health gate has decided; raises JobFailed otherwise
```

## What it does for you

* **Authentication.** The token is sent in an `Authorization` header, never in an address or a message, and
  comes from `NOUST_TOKEN` when you do not pass it. Plain `http://` is refused for any host but loopback, since
  the token would cross the network in clear, and a redirect is an error rather than a place to send it.
* **The error contract.** The API answers every refusal as `{error, detail, hint, fields, output}`; it becomes
  `APIError` with `status`, `code`, `detail`, `hint` and `output` (nginx's or systemd's own words, verbatim).
  A call that needs sudo mode raises `ElevationRequired`: a token enters sudo mode only when it was issued with
  `allow_elevated` ("Allow sudo mode" in the console, `"allow_elevated": true` in `POST /api/auth/tokens`).
  The rate limit raises `RateLimited` with `retry_after`.
* **Retries.** A read is repeated after a `429` or a gateway error, waiting what the server asked. A change is
  never repeated: it may have been made.
* **Jobs.** Deployments, updates, backups and deletions answer `202` with a job. `wait_for_job` follows it to
  its end and raises `JobFailed` with the job (its log and its error) when it did not succeed.
* **Documentation in the editor.** Each method's docstring has the verb and path, what the route does, the
  permission it needs and whether it asks for sudo mode or a second person's approval. `noust_sdk.OPERATIONS`
  lists every operation as data.

## How it stays true

`scripts/generate_sdk.py` writes `_models.py` and `_operations.py`; CI fails when they are out of date
(`--check`), and `tests/test_sdk_python.py` holds the client to the schema (every operation is a method with
its own verb and path), to the real application (a team member sees its team's applications, a viewer's
refusal arrives as an exception with the server's words) and to `mypy --strict`.

`tests/integration/sdk_live.py` is the other half: the same client over real HTTP, against a live API
(`scripts/console_server.py`, seeded and sandboxed, the server the [Terraform provider](terraform.md) is
proved with). With tokens narrowed to the permissions they need, it makes a team, deploys an application and
waits for the job, changes its variables and branch, adds a name, a backup schedule and a deploy key, makes
and drops a database, and sees the refusals a script meets arrive as the documented exceptions with the
server's words: a token that may only look (`APIError`, `permission_denied`, naming `teams.manage`), a token
that cannot enter sudo mode (`ElevationRequired`) and a deployment the health gate refuses (`JobFailed`).
`python tests/integration/terraform_run.py --stage sdk` runs it in containers; run by hand it starts a
server of its own. CI runs it.

After any change to the API:

```bash
python scripts/export_openapi.py      # panel/openapi.json
python scripts/generate_sdk.py        # sdk/python/noust_sdk/_models.py, _operations.py
```

## Other languages

The console's TypeScript types (`panel/src/api/schema.gen.ts`) are generated from the same schema with
`openapi-typescript`, and any generator that reads OpenAPI 3.1 can read `panel/openapi.json` (served to an
authenticated caller at `GET /api/openapi.json`). A Terraform and OpenTofu provider is derived from the schema
and a map kept in the repository: see [terraform.md](terraform.md).
