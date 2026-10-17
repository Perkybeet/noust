# An account that already exists, with the role it holds in the team. A team role raises what the account's own
# role allows inside the team and never reaches the server.
resource "noust_team_member" "alice" {
  team     = noust_team.acme.slug
  username = "alice"
  role     = "operator"
}
