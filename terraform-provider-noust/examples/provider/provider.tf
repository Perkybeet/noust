terraform {
  required_providers {
    noust = {
      source  = "perkybeet/noust"
      version = "~> 0.1"
    }
  }
}

# The address and the token come from NOUST_URL and NOUST_TOKEN, so neither is in the configuration. Use an
# API token made for Terraform (Settings > API tokens in the console, with "Allow sudo mode"), narrowed to
# the permissions of the resources you use: the guide lists them for each one. Never the master token.
provider "noust" {}

# Or, for a server whose certificate comes from a private authority:
#
# provider "noust" {
#   url         = "https://noust.internal.example.com"
#   ca_file     = "/etc/ssl/internal-ca.pem"
#   job_timeout = "45m"
# }
