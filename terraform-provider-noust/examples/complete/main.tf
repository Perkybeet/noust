# A team's shop on a Noust server, end to end: who owns it, what runs, on which names, with which database,
# where its backups go and how it pulls its images. `tests/integration/terraform_run.py --stage e2e`
# applies this very file to a real API with the real Terraform CLI.

terraform {
  required_providers {
    noust = {
      source = "perkybeet/noust"
    }
  }
}

variable "suffix" {
  type        = string
  default     = "demo"
  description = "Keeps the names of this example apart from another copy of it."
}

variable "domain" {
  type        = string
  default     = "shop.example.com"
  description = "The application's primary domain. It must point at the server before a certificate can be issued."
}

variable "repository" {
  type        = string
  default     = "https://github.com/acme/shop.git"
  description = "Where the application is deployed from."
}

variable "app_type" {
  type        = string
  default     = null
  description = "The type of application. Detected from the repository when null."
}

variable "greeting" {
  type        = string
  default     = "hello"
  description = "A variable of the application, to show a change in place."
}

# The address and the token come from NOUST_URL and NOUST_TOKEN: a token issued with allow_elevated and the
# permissions of `permissions.json`, never the master token.
provider "noust" {}

resource "noust_team" "shop" {
  name = "Shop ${var.suffix}"
  slug = "shop-${var.suffix}"
}

resource "noust_project" "web" {
  team = noust_team.shop.slug
  name = "Web"
  slug = "web"
}

resource "noust_environment" "staging" {
  team    = noust_team.shop.slug
  project = noust_project.web.slug
  name    = "Staging"
  slug    = "staging"
}

resource "noust_application" "shop" {
  domain      = var.domain
  source      = var.repository
  branch      = "main"
  app_type    = var.app_type
  team        = noust_team.shop.slug
  project     = noust_project.web.slug
  environment = noust_environment.staging.slug

  env_vars = {
    GREETING = var.greeting
  }

  # Destroying the application keeps its files on the server unless this says otherwise.
  remove_files_on_destroy = true
}

resource "noust_domain" "www" {
  app    = noust_application.shop.domain
  domain = "www.${var.domain}"
  kind   = "alias"
}

resource "noust_database" "shop" {
  name   = "shop_${replace(var.suffix, "-", "_")}"
  engine = "postgresql"
  app    = noust_application.shop.domain

  # A database holds data: the server dumps it before it is dropped, and Terraform can be told to refuse.
  deletion_protection = false
}

resource "noust_backup_schedule" "shop" {
  app             = noust_application.shop.domain
  schedule        = "daily"
  retention_count = 14
  retention_days  = 60
}

resource "noust_deploy_key" "shop" {
  app  = noust_application.shop.domain
  name = "shop-${var.suffix}"
}

resource "noust_registry_login" "ghcr" {
  name     = "ghcr-${var.suffix}"
  kind     = "ghcr"
  username = "deployer"
  password = "a-token-from-your-secret-store"
}

data "noust_servers" "all" {}

data "noust_application" "shop" {
  domain     = noust_application.shop.domain
  depends_on = [noust_application.shop]
}

output "public_key" {
  description = "Add this to the repository as a read-only deploy key."
  value       = noust_deploy_key.shop.public_key
}

output "status" {
  value = data.noust_application.shop.last_deployment_status
}

output "servers" {
  value = [for server in data.noust_servers.all.servers : "${server.name} ${server.reachability}"]
}
