# An account on the GitHub container registry, for applications that run a private image.
resource "noust_registry_login" "ghcr" {
  name     = "ghcr"
  kind     = "ghcr"
  username = "deployer"
  password = var.ghcr_token
}

variable "ghcr_token" {
  type      = string
  sensitive = true
}
