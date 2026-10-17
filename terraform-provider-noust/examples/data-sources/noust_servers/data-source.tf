data "noust_servers" "all" {}

output "unreachable" {
  value = [for server in data.noust_servers.all.servers : server.name if server.reachability != "reachable"]
}
