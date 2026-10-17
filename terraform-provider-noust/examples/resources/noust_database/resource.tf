# A PostgreSQL database for the application. Its connection string reaches the application's environment,
# marked secret, and is not an attribute here.
resource "noust_database" "api" {
  name   = "api_production"
  engine = "postgresql"
  app    = noust_application.api.domain

  # Data is not dropped by accident.
  deletion_protection = true
}
