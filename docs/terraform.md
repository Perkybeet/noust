# Terraform and OpenTofu

Noust has a provider for Terraform and OpenTofu: `terraform-provider-noust/` in this repository, written in
Go with the Terraform Plugin Framework. It manages a server's applications, their domains, databases and
backup schedules, the teams, projects and environments that own them, deploy keys and registry accounts, from
the same `terraform plan` and state as the infrastructure the server runs on.

It is a client of the [HTTP API](api.md) and implements nothing a server does. Every change is the call the
console or `noust` would make, behind the same permissions, [sudo mode](#a-token-that-can-do-what-the-plan-does),
approvals and audit trail, so the audit log of the server names the token Terraform used and nothing runs
twice or around a guard.

Noust also has a declarative way to hold a server in git without Terraform: [a manifest](manifest.md), with
`plan` and `apply`. The provider is for teams whose infrastructure is already Terraform or OpenTofu.

**Status.** The provider is built and tested against the real API (see [How it is proved](#how-it-is-proved)).
It is not on the Terraform Registry yet: that needs a public repository of its own and a signing key, which
are the owner's, and [terraform-registry.md](terraform-registry.md) is the guide and the script for it. Until
then it installs from source.

## Install

```bash
cd terraform-provider-noust
go build -o ~/go/bin/terraform-provider-noust
```

and tell Terraform (or OpenTofu) to use that directory for the provider, in `~/.terraformrc`:

```hcl
provider_installation {
  dev_overrides {
    "perkybeet/noust" = "/home/you/go/bin"
  }
  direct {}
}
```

Once it is in the registry, `source = "perkybeet/noust"` is all a configuration needs and `terraform init`
installs it. Nothing else changes.

```hcl
terraform {
  required_providers {
    noust = {
      source = "perkybeet/noust"
    }
  }
}

provider "noust" {} # NOUST_URL and NOUST_TOKEN

resource "noust_application" "api" {
  domain   = "api.example.com"
  source   = "https://github.com/acme/api.git"
  branch   = "main"
  app_type = "nodejs"

  env_vars = {
    LOG_LEVEL = "info"
  }
}
```

[`examples/complete`](../terraform-provider-noust/examples/complete/main.tf) is a team's shop from the team
down to the registry account that its private image is pulled with, and CI applies that file with the real
Terraform CLI to a real API.

## A token that can do what the plan does

The provider reads `NOUST_URL` and `NOUST_TOKEN`, never an argument. Use a token made for it, and not the
master token, which is break-glass and owns nothing.

* **Narrow it.** An API token issued by an account holds at most that account's role, and can be narrowed
  to a list of permissions. The table below is what each resource and data source needs, computed from the
  operations the provider calls; a token for one stack needs only the rows of the resources in it.
* **Let it enter sudo mode.** Deleting, and most in-place changes, are root-equivalent and ask for sudo mode,
  and a token enters it only when it was issued with `allow_elevated` ("Allow sudo mode" in the console's
  Settings, API tokens; `"allow_elevated": true` in `POST /api/auth/tokens`). Without it the provider stops
  at the first such call and says so, with the permission and the way to fix it. The ENS profile refuses
  `allow_elevated`: under it, what asks for sudo mode is done in the console by a person.
* **Bind it to what it manages.** A token bound to a project sees nothing else of the server
  ([teams.md](teams.md)), which is the right scope for a state that manages one project.
* **Approvals.** On a server that asks a second person to approve root-equivalent changes (a backup
  schedule, for one) the call answers a request and does not run. The provider reports the request's id and
  stops; once it is approved, apply again with `approval = "<id>"` (or `NOUST_APPROVAL`). An approval allows
  one call, once.

A token that may only look is a useful plan-only credential: refreshing reads, and the comparison of an
application's variables (below) is the one read that needs more, so with less the provider warns and checks
that each variable is there instead.

| Resource or data source | Permissions | Sudo mode |
|---|---|---|
| `noust_application` | `apps.manage`, `apps.operate`, `apps.read` | yes |
| `noust_domain` | `apps.manage`, `apps.read` | yes |
| `noust_database` | `apps.read`, `databases.manage`, `databases.read`, `databases.write` | yes |
| `noust_backup_schedule` | `backups.manage`, `backups.read`, `root_equivalent` | yes |
| `noust_team` | `teams.manage`, `teams.read` | yes |
| `noust_team_member` | `teams.manage`, `teams.read` | yes |
| `noust_project` | `teams.manage`, `teams.read` | yes |
| `noust_environment` | `teams.manage`, `teams.read` | yes |
| `noust_deploy_key` | `apps.manage`, `apps.read` | yes |
| `noust_registry_login` | `apps.manage`, `apps.read` | yes |
| `data.noust_application` | `apps.read` | no |
| `data.noust_applications` | `apps.read` | no |
| `data.noust_servers` | `fleet.read` | no |
| `data.noust_team` | `teams.read` | no |

(`permissions.json` in the provider's directory is the same table as data, and a test keeps both equal to the
operations the code calls.)

## The resources

| Resource | What it is | Import id |
|---|---|---|
| `noust_application` | A deployed application: source, branch, type, health check, limits, variables, placement | its domain |
| `noust_domain` | A second name an application answers on: an alias, or a redirect to the primary domain | `app/domain` |
| `noust_database` | A database on one of the server's engines, with an owner account of its own | `engine/name` |
| `noust_backup_schedule` | An application's scheduled backups, their retention and remote destinations | the application's domain |
| `noust_team`, `noust_project`, `noust_environment` | Who owns what | `team`, `team/project`, `team/project/environment` |
| `noust_team_member` | An existing account's role in a team | `team/username` |
| `noust_deploy_key` | The SSH key an application's repository is reached with | the application's domain |
| `noust_registry_login` | An account on a container registry | its name |

Data sources: `noust_application` (one deployed application, however it got there), `noust_applications`
(those of a team, project, environment or tag), `noust_servers` (the servers of the fleet) and `noust_team`.
The reference of every attribute is generated from the schemas into
[`terraform-provider-noust/docs`](../terraform-provider-noust/docs).

**What changes in place and what replaces.** The API takes some settings only when the object is made, and
Terraform is honest about it: changing those shows `must be replaced` in the plan. An application's `source`,
`app_type`, `port`, `layout`, `webserver`, `ssl`, `include_www` and `persistent_paths` replace it; its
`branch`, `health_check`, limits, `env_vars`, team, project and environment are updated in place (a move
carries its sites, databases and backup schedule). A database's attributes all replace it, which drops the
data. So protect what holds data, in the provider's own words (`deletion_protection = true`) or in
Terraform's (`lifecycle { prevent_destroy = true }`).

## What a provider has to do that the schema cannot say

* **Wait for jobs.** A deployment, a removal and a database drop answer `202` with a job. The resource
  exists when `GET /api/jobs/{id}` says `succeeded`; a `failed` job carries the health gate's probes and the
  journal verbatim, and they are the error you read, not a paraphrase (`job_timeout`, default 30 minutes, and
  `job_poll_interval` are in the provider block; the job keeps running on the server when the wait ends).
  A deployment that fails normally leaves nothing behind; if it left something, Terraform tracks it as
  tainted so the next apply does not try to make it again.
* **Say what the server said.** Every refusal is the API's `{error, detail, hint, fields, output}`, shown
  as it came, with nginx's or systemd's own output verbatim. A refusal for a missing permission names it; one
  for sudo mode says how to issue the token; one for a second person's approval gives the request.
* **Read before it writes.** A resource changed outside Terraform shows in `terraform plan`, because the read
  is the API's own answer. An application's variables are compared without reading them: the server answers a
  dry run of the merge with the names that would change (never their values), so a variable changed by hand
  leaves the state and the next plan sets it again. Only the names in `env_vars` are managed; what Noust
  writes itself (a linked database's `DATABASE_URL`) stays.
* **Never destroy by surprise.** `deletion_protection` refuses a destroy until it is lifted, on an
  application and on a database, and `lifecycle { prevent_destroy = true }` does the same from Terraform's
  side. A database is dumped before it is dropped unless `dump_before_destroy = false`. An application's
  certificate stays unless `remove_certificate_on_destroy`; its files are removed by default
  (`remove_files_on_destroy`), because the server will not deploy at a domain whose directory is still
  there and a replacement destroys the old application before it makes the new one: set it to `false` for
  an application you mean to keep the uploads of, and move the directory aside before deploying there again.
  The server refuses to delete a team, project or environment that still holds anything, and says what.
* **Wait its turn.** Only one deploy, update or domain change runs on an application at a time, and Terraform
  applies the resources of one application in parallel. The server refuses the second one at once, before it
  does anything (`app_busy`), and the provider tries again for about two minutes. It repeats no other change,
  since it may have been made.
* **Pin the API's version.** Every response carries `X-Noust-API-Version`; the provider speaks version 1 at
  `/api/v1` and refuses a server that answers another, or that is not a Noust at all. A deprecated operation
  announces itself with `Deprecation` and `Sunset` headers ([api.md](api.md#versions-and-compatibility)),
  which the provider logs.
* **Keep secrets marked and out of the way.** `env_vars`, a registry account's `password`, a deploy key's
  `private_key` and the token are sensitive attributes, and no request body is ever logged. The state still
  holds them, as it does for every provider: keep it in a backend that encrypts it. Where a secret only has
  to be sent, there is a way that keeps it out of the state altogether: a registry account's
  `password_wo`, a write-only attribute (Terraform and OpenTofu 1.11 or later) that never reaches the plan
  or the state, and `password_wo_version` to send a new one. A plain `http://` address
  is accepted only for `localhost`, `127.0.0.1` and `::1`, a redirect is an error and not a place to send the
  token, and there is no option to skip certificate verification (`ca_file` trusts a private authority).

## What the map holds that the provider does not do yet

[`sdk/terraform/generator_config.yml`](../sdk/terraform/generator_config.yml) is the map from the API to
resources, in the format of HashiCorp's `terraform-plugin-codegen-openapi`, and `x_noust.provided` says which
of its entries the provider offers. The rest is the plan, each as the API already has it: `noust_account`,
`noust_role`, `noust_access_binding` and `noust_api_token` (the secret is in the answer to
the create and in no read: kept in state, sensitive), `noust_cron_job`, `noust_team_variable` and
`noust_server_variable` (the value is sensitive), `noust_notification_channel` (only changed and removed: the
channels exist), `noust_secret_provider` and `noust_dns_provider` (their tokens and credentials are
sensitive). `tests/test_terraform_map.py` holds every operation the map names to `panel/openapi.json`, so the
map cannot drift from the API, and holds `provided` to what the provider's code calls.

Not done on purpose: a `noust_application` from a recipe, a Compose file or a container image, and
the application's processes, previews and zero-downtime settings. They are in the API and not yet in the
schema; the provider would grow to them one resource at a time, each with an acceptance test.

## How it is proved

`tests/integration/terraform_run.py` builds and tests the provider with nothing installed on the machine: Go
runs in a `golang` container and Terraform in a `hashicorp/terraform` one. Its stages:

* **unit**: gofmt, `go vet`, build, and the unit tests, among them the **contract tests** that hold the
  provider's operations table and every request and answer type to `panel/openapi.json` (the path exists
  with that verb, the permission and the sudo flag are the schema's, a field the provider sends or reads is a
  field of the API), and the client's own: the token only in a header, the error contract, retries, redirects,
  job waits, approvals and the version check.
* **acceptance**: every resource and data source against the live API of `scripts/console_server.py` (the
  real application on a seeded, sandboxed machine, with a code host and listeners so that an application
  really deploys), through `terraform-plugin-testing`: create, a plan that is empty afterwards, an update in
  place, a replacement, an import, a destroy checked against the server, drift made behind Terraform's back,
  a deployment the health gate refuses, deletion protection. The tokens are issued by an account
  that signed in, enrolled an authenticator and confirmed with a backup code, narrowed to the permissions of
  the table above, so the tests also prove what a token that may only look, and one that cannot enter sudo
  mode, are told.
* **e2e**: the provider binary under the real Terraform CLI applies `examples/complete`, plans again (empty),
  changes a variable in place, plans (empty), and destroys.
* **sdk**: the generated Python client ([sdk.md](sdk.md)) against the same live API.

```bash
python tests/integration/terraform_run.py                       # every stage, in Docker
python tests/integration/terraform_run.py --stage acceptance -k TestAccApplication
python tests/integration/terraform_run.py --native              # go, terraform and python on the PATH, as CI runs it
```

CI runs the same stages (`Terraform provider` in `.github/workflows/ci.yml`). The containers are named
`noust-test-...` and labelled `noust.test=1`, publish no port (the clients share the server container's
network), and only what is named exactly is ever removed.

## Without the provider

The manifest does the same job from a pipeline, with a `plan` that fails a pull request when the repository
and the server have drifted:

```bash
noust --remote "$NOUST_URL" --token env:NOUST_TOKEN manifest plan noust-manifest.yaml --check
noust --remote "$NOUST_URL" --token env:NOUST_TOKEN manifest apply noust-manifest.yaml --yes
```

A Terraform configuration can also call the API through the generic REST providers, or `noust api METHOD PATH`
in a `local-exec`; neither knows the schema.
