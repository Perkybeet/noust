resource "noust_project" "web" {
  team = noust_team.acme.slug
  name = "Web"
  slug = "web"
}
