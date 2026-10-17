# Terraform and OpenTofu

There is no Noust provider on the Terraform registry yet. This page says what exists, what a
provider is built from, and what is left for whoever writes the Go.

Noust already has a declarative way to hold a server in git: [a manifest](manifest.md), with `plan` and
`apply`, from a pipeline or the console. A provider is for teams whose infrastructure is already
Terraform or OpenTofu and who want a Noust application, a team or a role to sit in the same state and the
same `terraform plan` as the server it runs on.

## What is in the repository

* [`sdk/terraform/generator_config.yml`](../sdk/terraform/generator_config.yml): the map from the API to
  resources and data sources, in the format of HashiCorp's `terraform-plugin-codegen-openapi`. Feed it with
  the API's schema and the generator accepts it as it stands:

  ```bash
  go install github.com/hashicorp/terraform-plugin-codegen-openapi/cmd/tfplugingen-openapi@latest
  tfplugingen-openapi generate --config sdk/terraform/generator_config.yml \
      --output provider_code_spec.json panel/openapi.json
  ```

  The result is a provider code specification: the attributes of each resource, which are required, which
  are computed, which are optional. `tfplugingen-framework` turns it into the Go schema code of a
  provider built on the Plugin Framework.
* `tests/test_terraform_map.py` holds every operation the map names to `panel/openapi.json`: the path
  exists with that verb, a create takes a JSON body, a read answers an object, the list a provider filters
  has the field it filters by, a job-backed call answers `202`. An endpoint that is renamed or removed fails
  a test here, not a user's `terraform apply`.
* [`sdk/python`](../sdk/python): a typed client generated from the same schema ([api.md](api.md)), which
  is how the map is exercised and how a script that is not Terraform does the same things.

A provider is a Go program and the registry only publishes repositories named
`terraform-provider-<name>`, so the provider itself belongs in a repository of its own, generated from the
files above. It is not here because it cannot be built, tested or released from this repository's CI.

## The resources

The API has a read of one object for some things and only a list for others; the generator needs the first
to derive a schema, so the map has two parts.

**Generated** (`resources` and `data_sources` in the file): the generator derives their schema.

| Terraform | API | Notes |
|---|---|---|
| `noust_team` (resource and data source) | `/api/teams`, `/api/teams/{team}` | the slug is the identity |
| `noust_account` (resource and data source) | `/api/auth/accounts` | the password is write-only: it is in the plan, never in a read |
| `noust_role` (resource and data source) | `/api/access/roles` | a role of your own, from the permissions that exist |
| `noust_registry_login` | `/api/registries` | the password goes to the secret store, never back |
| `noust_application` (data source) | `GET /api/apps/{domain}` | what is running, to refer to its port, branch or state |

**Written by hand** (`x_noust.hand_written`): the provider reads the list and picks the object by the field
the map names, or waits for a job.

| Terraform | API | Notes |
|---|---|---|
| `noust_application` (resource) | `POST /api/apps`, `DELETE /api/apps/{domain}` | answers a job: see below |
| `noust_project`, `noust_environment` | `/api/teams/{team}/projects...` | identity `team/slug`, `team/project/slug` |
| `noust_team_member` | `/api/teams/{team}/members` | |
| `noust_access_binding` | `/api/access/bindings` | a role on a team, a project, an environment or the server |
| `noust_api_token` | `/api/auth/tokens` | the secret is in the answer to the create and in no read: kept in state, sensitive |
| `noust_backup_schedule` | `/api/backup-schedules` | |
| `noust_cron_job` | `/api/cron` | |
| `noust_team_variable`, `noust_server_variable` | `/api/teams/{team}/variables`, `/api/variables/server` | the value is sensitive |
| `noust_notification_channel` | `/api/notifications/channels` | only changed and removed: the channels exist; its settings are sensitive |
| `noust_secret_provider` | `/api/secret-providers` | the address and options; its token is sensitive and goes to the secret store |
| `noust_dns_provider` | `/api/dns/providers` | its credentials are sensitive |

## What a provider has to do that the schema cannot say

* **Wait for jobs.** A deployment, an update and a deletion answer `202` with a job. The resource exists
  when `GET /api/jobs/{id}` says `succeeded`; a `failed` job carries the health gate's probes and the journal
  verbatim, and they are the error the user reads, not a paraphrase. The Python client's `wait_for_job` is the
  reference.
* **Use a token that can do what the plan does.** Routes that are root-equivalent ask for sudo mode, and a
  token enters it only when it was made with `--allow-elevated`; a token bound to a project sees nothing else
  (`docs/teams.md`), which is the right scope for a state that manages one project. Under approvals a call
  answers a request to be approved by a second person: the provider reports it and stops, it does not retry.
  The token is read from `NOUST_TOKEN`, never from an argument.
* **Read before it writes.** A resource that was changed outside Terraform shows in `terraform plan`
  because the read is the API's own answer. An application's source, type and port cannot change once it is
  deployed, so a change to them replaces the resource; branch, limits, health check, domains, databases and
  variables are updated in place.
* **Never destroy by surprise.** Deleting an application is irreversible and the API asks for sudo mode;
  the provider should offer `prevent_destroy` guidance in its documentation and read the response's job.
* **Pin the API's version.** Every response carries `X-Noust-API-Version`; a provider built for version 1
  refuses a server that answers another, and a deprecated operation announces itself with `Deprecation`
  and `Sunset` headers ([api.md](api.md#versions-and-compatibility)).
* **Secrets stay marked.** Every field the map lists under `sensitive` is a sensitive attribute; the
  provider never logs a request body.

## Until there is a provider

The manifest does the same job from a pipeline, with a `plan` that fails a pull request when the repository
and the server have drifted:

```bash
noust --remote "$NOUST_URL" --token env:NOUST_TOKEN manifest plan noust-manifest.yaml --check
noust --remote "$NOUST_URL" --token env:NOUST_TOKEN manifest apply noust-manifest.yaml --yes
```

A Terraform configuration that needs a Noust object today can call the API through the generic REST
providers, or through `noust api METHOD PATH` in a `local-exec`; neither knows the schema.
