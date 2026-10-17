# Noust makes the key; add the public half to the repository as a read-only deploy key.
resource "noust_deploy_key" "api" {
  app  = noust_application.api.domain
  name = "api"
}

output "api_deploy_key" {
  value = noust_deploy_key.api.public_key
}
