# A variable of the whole server, for every application on it: {{server.REGION}}.
resource "noust_server_variable" "region" {
  name  = "REGION"
  value = "eu-west-1"
}
