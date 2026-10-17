# An environment's variable: staging and production can hold the same name with different values.
resource "noust_environment_variable" "api_url" {
  team        = noust_team.acme.slug
  project     = noust_project.web.slug
  environment = noust_environment.staging.slug
  name        = "API_URL"
  value       = "https://api.staging.example.com"
}
