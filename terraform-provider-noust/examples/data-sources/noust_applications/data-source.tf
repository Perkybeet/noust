data "noust_applications" "staging" {
  team        = "acme"
  environment = "staging"
}

output "staging_domains" {
  value = [for app in data.noust_applications.staging.apps : app.domain]
}
