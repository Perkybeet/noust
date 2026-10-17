# Defined once for a team, used by reference from an application's own variables:
# DATABASE_URL = "{{team.DATABASE_URL}}". A name that looks like a credential is masked in every read;
# `secret` says it outright.
resource "noust_team_variable" "registry_token" {
  team   = noust_team.acme.slug
  name   = "REGISTRY_TOKEN"
  value  = var.registry_token
  secret = true
}
