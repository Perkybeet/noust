data "noust_team" "default" {
  slug = "default"
}

resource "noust_project" "tools" {
  team = data.noust_team.default.slug
  name = "Tools"
}
