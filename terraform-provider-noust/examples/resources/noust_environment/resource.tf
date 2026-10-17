# Every project is made with a production environment; add the others.
resource "noust_environment" "staging" {
  team    = noust_team.acme.slug
  project = noust_project.web.slug
  name    = "Staging"
  slug    = "staging"
}
