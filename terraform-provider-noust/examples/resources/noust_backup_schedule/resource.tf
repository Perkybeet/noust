# Back the application up every night at half past three, keep the last 14 and nothing older than two
# months, and copy each one to a destination created in the console.
resource "noust_backup_schedule" "api" {
  app             = noust_application.api.domain
  schedule        = "*-*-* 03:30:00"
  retention_count = 14
  retention_days  = 60

  destinations = [
    {
      name           = "offsite-sftp"
      retention_days = 90
    },
  ]
}
