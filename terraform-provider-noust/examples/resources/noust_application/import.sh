# An application already deployed, by its primary domain. Add its configuration, then plan: what the
# server cannot report (the variables' values, ssl, include_www) is adopted from the configuration.
terraform import noust_application.api api.example.com
