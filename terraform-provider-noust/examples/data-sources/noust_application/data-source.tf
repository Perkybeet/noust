# An application someone else deployed: refer to its port or its state without managing it.
data "noust_application" "legacy" {
  domain = "legacy.example.com"
}

output "legacy_port" {
  value = data.noust_application.legacy.port
}
