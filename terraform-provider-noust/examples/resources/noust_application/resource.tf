# A Node.js service deployed from git, behind the health gate, with its limits and variables.
resource "noust_application" "api" {
  domain   = "api.example.com"
  source   = "https://github.com/acme/api.git"
  branch   = "main"
  app_type = "nodejs"

  # The first deployment and every one after it must answer this before a release may serve.
  health_check = {
    path    = "/healthz"
    expect  = "200"
    timeout = 60
  }

  memory_max_mb     = 512
  cpu_quota_percent = 100

  # Only the names listed here are managed: a DATABASE_URL that Noust writes for a linked database stays.
  env_vars = {
    LOG_LEVEL = "info"
    API_KEY   = var.api_key
  }

  # Place it in a team's project and environment.
  team        = "acme"
  project     = "web"
  environment = "production"

  # A production application: Terraform refuses to destroy it until this is false.
  deletion_protection = true
}

variable "api_key" {
  type      = string
  sensitive = true
}
