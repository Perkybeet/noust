# Terraform and OpenTofu provider for Noust

Manage a [Noust](https://github.com/Perkybeet/noust) server from Terraform or OpenTofu: its applications,
their domains, databases and backup schedules, the teams, projects and environments that own them, deploy
keys and registry accounts. The provider is a client of the server's HTTP API (`/api/v1`) and implements
nothing a server does, so every change is the call the console or `noust` would make, behind the same
permissions, sudo mode, approvals and audit trail.

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

Creating `noust_application` queues the deployment and waits for it: the resource exists when the release
has passed the server's health gate, and a deployment that fails is the error you read, with the gate's
probes and the journal word for word.

## What it manages

| Resource | What it is |
|---|---|
| `noust_application` | A deployed application: source, branch, type, health check, limits, variables, placement |
| `noust_domain` | A second name an application answers on (an alias, or a redirect to the primary domain) |
| `noust_database` | A database on one of the server's engines, with an owner of its own |
| `noust_backup_schedule` | An application's scheduled backups, retention and remote destinations |
| `noust_team`, `noust_project`, `noust_environment` | Who owns what |
| `noust_deploy_key` | The SSH key an application's repository is reached with |
| `noust_registry_login` | An account on a container registry |

Data sources: `noust_application`, `noust_applications`, `noust_servers` and `noust_team`.

The documentation of each is in [`docs/`](docs/) (generated from the schemas by `tfplugindocs`), the guide
to credentials, permissions and the semantics a provider has to get right is
[`docs/terraform.md`](../docs/terraform.md) of the Noust repository, and
[`examples/complete`](examples/complete/main.tf) is a team's shop, end to end, that CI applies with the real
Terraform CLI.

## Credentials

The provider reads `NOUST_URL` and `NOUST_TOKEN`. Use an API token made for it, narrowed to the
permissions of the resources you use and issued with **allow sudo mode** (console: Settings, API tokens);
[`permissions.json`](permissions.json) lists what each resource and data source needs. Never the master
token. Plain `http://` is accepted only for `localhost`, `127.0.0.1` and `::1`, and there is no option to
skip certificate verification (`ca_file` trusts a private authority instead).

## Developing

Nothing needs installing: Go runs in a `golang` container and Terraform in a `hashicorp/terraform` one.
From the Noust repository:

```bash
python tests/integration/terraform_run.py                       # build, vet, unit, acceptance, e2e and the Python SDK
python tests/integration/terraform_run.py --stage acceptance -k TestAccApplication
python tests/integration/terraform_run.py --native              # go, terraform and python on this machine (CI)
```

The acceptance tests (`TF_ACC=1`, [terraform-plugin-testing](https://github.com/hashicorp/terraform-plugin-testing))
run against the real API served by `scripts/console_server.py`, seeded and sandboxed, with tokens narrowed to
the permissions each profile names; they are given the master token never. The contract tests in
`internal/noust` hold the operations table and every request and answer type to `panel/openapi.json`, so a
route that is renamed, or that starts to need more, fails there and not in somebody's `terraform apply`.

Releasing to the Terraform Registry needs a public repository of its own and a signing key:
see [`docs/terraform-registry.md`](../docs/terraform-registry.md).

## License

AGPL-3.0-or-later, like Noust.
