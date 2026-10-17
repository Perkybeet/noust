// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"fmt"
	"regexp"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/plancheck"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

func regexpFor(pattern string) *regexp.Regexp { return regexp.MustCompile(pattern) }

// The attributes the server cannot report back, which an import therefore leaves to the configuration.
var applicationImportIgnores = []string{
	"env_vars", "webserver", "ssl", "include_www", "persistent_paths", "restart_on_change",
	"remove_files_on_destroy", "remove_certificate_on_destroy", "deletion_protection",
}

func staticSite(domain, branch, greeting string) string {
	return providerBlock + fmt.Sprintf(`
resource "noust_application" "site" {
  domain   = %[1]q
  source   = %[2]q
  branch   = %[3]q
  app_type = "static"
  ssl      = true

  env_vars = {
    GREETING = %[4]q
  }

  remove_files_on_destroy = true
}

data "noust_application" "site" {
  domain = noust_application.site.domain
}
`, domain, codeHostSite, branch, greeting)
}

func TestAccApplication_staticSiteLifecycle(t *testing.T) {
	domain := domainFor("tfsite", suffix())
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				Config: staticSite(domain, "main", "hello"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_application.site", "id", domain),
					resource.TestCheckResourceAttr("noust_application.site", "app_type", "static"),
					resource.TestCheckResourceAttr("noust_application.site", "branch", "main"),
					resource.TestCheckResourceAttr("noust_application.site", "layout", "releases"),
					resource.TestCheckResourceAttr("noust_application.site", "team", "default"),
					resource.TestCheckResourceAttr("noust_application.site", "environment", "production"),
					resource.TestCheckResourceAttr("noust_application.site", "env_vars.GREETING", "hello"),
					// The data source sees what is deployed: the release the health gate let through.
					resource.TestCheckResourceAttr("data.noust_application.site", "last_deployment_status", "success"),
					resource.TestCheckResourceAttrSet("data.noust_application.site", "last_commit"),
				),
			},
			{
				// The branch and the variables are changed in place; nothing is deployed again.
				Config: staticSite(domain, "staging", "bonjour"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_application.site", plancheck.ResourceActionUpdate),
				}},
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_application.site", "branch", "staging"),
					resource.TestCheckResourceAttr("noust_application.site", "env_vars.GREETING", "bonjour"),
					resource.TestCheckResourceAttr("data.noust_application.site", "branch", "staging"),
				),
			},
			{
				// Nothing to do: the plan after an apply is empty, which the framework checks too.
				Config:   staticSite(domain, "staging", "bonjour"),
				PlanOnly: true,
			},
			{
				ResourceName:            "noust_application.site",
				ImportState:             true,
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: applicationImportIgnores,
			},
		},
	})
}

func nodeService(domain, source, extra string) string {
	return providerBlock + fmt.Sprintf(`
resource "noust_application" "api" {
  domain   = %[1]q
  source   = %[2]q
  branch   = "main"
  app_type = "nodejs"
  %[3]s
  remove_files_on_destroy = true
}
`, domain, source, extra)
}

func TestAccApplication_serviceWithLimitsHealthAndVariables(t *testing.T) {
	domain := domainFor("tfapi", suffix())
	first := `
  memory_max_mb = 256

  health_check = {
    path    = "/"
    expect  = "200-399"
    timeout = 20
  }

  env_vars = {
    API_KEY = "s3cr3t"
    MODE    = "blue"
  }
`
	second := `
  memory_max_mb     = 512
  cpu_quota_percent = 50

  health_check = {
    path     = "/"
    expect   = "200-399"
    timeout  = 20
    interval = 3
  }

  env_vars = {
    API_KEY = "s3cr3t"
    MODE    = "green"
    REGION  = "eu"
  }
`
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				Config: nodeService(domain, codeHostService, first),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_application.api", "app_type", "nodejs"),
					resource.TestCheckResourceAttrSet("noust_application.api", "port"),
					resource.TestCheckResourceAttrSet("noust_application.api", "unit"),
					resource.TestCheckResourceAttr("noust_application.api", "memory_max_mb", "256"),
					resource.TestCheckResourceAttr("noust_application.api", "health_check.timeout", "20"),
					// A limit left out is no limit, and the plan after the apply is empty.
					resource.TestCheckNoResourceAttr("noust_application.api", "cpu_quota_percent"),
					func(state *terraformState) error {
						// The server really has the variables, set before the first start.
						env, err := accClient(t).GetAppEnv(context.Background(), domain)
						if err != nil {
							return err
						}
						if env.Variables["MODE"] != "blue" || env.Variables["API_KEY"] == "s3cr3t" {
							return fmt.Errorf("variables on the server: %v (a secret must be redacted)", env.Variables)
						}
						return nil
					},
				),
			},
			{
				Config: nodeService(domain, codeHostService, second),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_application.api", plancheck.ResourceActionUpdate),
				}},
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_application.api", "memory_max_mb", "512"),
					resource.TestCheckResourceAttr("noust_application.api", "cpu_quota_percent", "50"),
					resource.TestCheckResourceAttr("noust_application.api", "health_check.interval", "3"),
					resource.TestCheckResourceAttr("noust_application.api", "env_vars.REGION", "eu"),
				),
			},
			{
				// A variable changed by hand is a difference, found without the server ever saying a value.
				PreConfig: func() {
					_, err := accClient(t).MergeAppEnv(context.Background(), domain, noust.MergeEnvRequest{Set: map[string]string{"MODE": "tampered"}})
					if err != nil {
						t.Fatal(err)
					}
				},
				Config:             nodeService(domain, codeHostService, second),
				PlanOnly:           true,
				ExpectNonEmptyPlan: true,
			},
			{
				Config: nodeService(domain, codeHostService, second),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_application.api", "env_vars.MODE", "green"),
					func(state *terraformState) error {
						dry, err := accClient(t).MergeAppEnv(context.Background(), domain, noust.MergeEnvRequest{Set: map[string]string{"MODE": "green"}, DryRun: true})
						if err != nil {
							return err
						}
						if len(dry.Changed) != 0 {
							return fmt.Errorf("the server still differs on %v", dry.Changed)
						}
						return nil
					},
				),
			},
			{
				// A variable removed from the configuration is removed from the server; the others stay.
				Config: nodeService(domain, codeHostService, `
  memory_max_mb     = 512
  cpu_quota_percent = 50
  health_check = {
    path     = "/"
    expect   = "200-399"
    timeout  = 20
    interval = 3
  }
  env_vars = {
    API_KEY = "s3cr3t"
  }
`),
				Check: func(state *terraformState) error {
					env, err := accClient(t).GetAppEnv(context.Background(), domain)
					if err != nil {
						return err
					}
					if _, present := env.Variables["MODE"]; present {
						return fmt.Errorf("MODE is still on the server: %v", env.Variables)
					}
					if _, present := env.Variables["API_KEY"]; !present {
						return fmt.Errorf("API_KEY was removed: %v", env.Variables)
					}
					return nil
				},
			},
			{
				ResourceName:            "noust_application.api",
				ImportState:             true,
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: applicationImportIgnores,
			},
		},
	})
}

func TestAccApplication_whatTheAPIOnlyTakesAtCreationReplacesIt(t *testing.T) {
	domain := domainFor("tfrep", suffix())
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				Config: nodeService(domain, "https://code.example.net/acme/first.git", ""),
				Check:  resource.TestCheckResourceAttr("noust_application.api", "source", "https://code.example.net/acme/first.git"),
			},
			{
				Config: nodeService(domain, "https://code.example.net/acme/second.git", ""),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_application.api", plancheck.ResourceActionDestroyBeforeCreate),
				}},
				Check: resource.TestCheckResourceAttr("noust_application.api", "source", "https://code.example.net/acme/second.git"),
			},
		},
	})
}

func TestAccApplication_replacingOneLeavesNothingInTheWayOfTheNewOne(t *testing.T) {
	domain := domainFor("tfkeep", suffix())
	config := func(source string) string {
		// remove_files_on_destroy is left at its default: the server refuses to deploy at a domain whose
		// directory is still there, and a replacement destroys the old application before it makes the new.
		return providerBlock + fmt.Sprintf(`
resource "noust_application" "api" {
  domain   = %q
  source   = %q
  branch   = "main"
  app_type = "nodejs"
}
`, domain, source)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{Config: config("https://code.example.net/acme/first.git")},
			{
				Config: config("https://code.example.net/acme/second.git"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_application.api", plancheck.ResourceActionDestroyBeforeCreate),
				}},
				Check: resource.TestCheckResourceAttr("noust_application.api", "source", "https://code.example.net/acme/second.git"),
			},
		},
	})
}

func TestAccApplication_aDeploymentTheGateRefusesIsTheServersOwnError(t *testing.T) {
	domain := domainFor("tfbad", suffix())
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				// The listener answers 200 to everything, so a gate that wants a 404 can never be satisfied.
				Config: nodeService(domain, codeHostService, `
  health_check = {
    path    = "/"
    expect  = "404"
    timeout = 5
    retries = 2
  }
`),
				ExpectError: regexpFor(`(?s)The application was not deployed.*did not pass its health check`),
			},
		},
	})
}

func TestAccApplication_deletionProtectionStopsADestroy(t *testing.T) {
	domain := domainFor("tfpro", suffix())
	config := func(protected bool) string {
		return providerBlock + fmt.Sprintf(`
resource "noust_application" "site" {
  domain                  = %q
  source                  = %q
  app_type                = "static"
  deletion_protection     = %t
  remove_files_on_destroy = true
}
`, domain, codeHostSite, protected)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{Config: config(true)},
			{Config: config(true), Destroy: true, ExpectError: regexpFor(`protected from deletion`)},
			{Config: config(false)},
		},
	})
}

func TestAccApplication_theAppDeletedByHandIsPlannedAgain(t *testing.T) {
	domain := domainFor("tfgone", suffix())
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{Config: staticSite(domain, "main", "hello")},
			{
				PreConfig: func() {
					client := accClient(t)
					accepted, err := client.DeleteApp(context.Background(), domain, noust.DeleteAppOptions{RemoveFiles: true})
					if err != nil {
						t.Fatal(err)
					}
					if _, err := client.WaitForJob(context.Background(), accepted, 0, 0); err != nil {
						t.Fatal(err)
					}
				},
				Config: staticSite(domain, "main", "hello"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_application.site", plancheck.ResourceActionCreate),
				}},
			},
		},
	})
}
