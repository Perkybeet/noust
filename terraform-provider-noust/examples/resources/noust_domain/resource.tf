# www answers on the application too, and an old name sends visitors to it.
resource "noust_domain" "www" {
  app    = noust_application.api.domain
  domain = "www.api.example.com"
}

resource "noust_domain" "legacy" {
  app    = noust_application.api.domain
  domain = "api-old.example.com"
  kind   = "redirect"
}
