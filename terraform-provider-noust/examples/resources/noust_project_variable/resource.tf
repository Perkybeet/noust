# A project's variable, for the applications of every environment of the project.
resource "noust_project_variable" "region" {
  team     = noust_team.acme.slug
  project  = noust_project.web.slug
  name     = "REGION"
  value    = "eu-west-1"
  audience = "runtime"
}
