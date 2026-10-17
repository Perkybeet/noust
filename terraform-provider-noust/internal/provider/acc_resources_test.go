// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/plancheck"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

// appFor is an application the other resources of a test hang from.
func appFor(domain string) string {
	return fmt.Sprintf(`
resource "noust_application" "api" {
  domain                  = %q
  source                  = %q
  branch                  = "main"
  app_type                = "nodejs"
  remove_files_on_destroy = true
}
`, domain, codeHostService)
}

func TestAccDomain(t *testing.T) {
	id := suffix()
	app := domainFor("tfdom", id)
	alias := domainFor("alias-tfdom", id)
	config := func(kind string, port string) string {
		return providerBlock + appFor(app) + fmt.Sprintf(`
resource "noust_domain" "alias" {
  app    = noust_application.api.domain
  domain = %q
  kind   = %q
  %s
}
`, alias, kind, port)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				Config: config("alias", ""),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_domain.alias", "id", app+"/"+alias),
					resource.TestCheckResourceAttr("noust_domain.alias", "kind", "alias"),
					func(state *terraform.State) error {
						listed, err := accClient(t).ListDomains(context.Background(), app)
						if err != nil {
							return err
						}
						for _, entry := range listed.Domains {
							if entry.Domain == alias {
								return nil
							}
						}
						return fmt.Errorf("the application does not answer on %s: %+v", alias, listed.Domains)
					},
				),
			},
			{
				// The port an alias is proxied to is changed in place.
				Config: config("alias", "port = 4399"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_domain.alias", plancheck.ResourceActionUpdate),
				}},
				Check: resource.TestCheckResourceAttr("noust_domain.alias", "port", "4399"),
			},
			{
				// A redirect is a different kind of name: it replaces the alias.
				Config: config("redirect", ""),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_domain.alias", plancheck.ResourceActionDestroyBeforeCreate),
				}},
				Check: resource.TestCheckResourceAttr("noust_domain.alias", "kind", "redirect"),
			},
			{ResourceName: "noust_domain.alias", ImportState: true, ImportStateVerify: true, ImportStateVerifyIgnore: []string{"check_dns", "force"}},
		},
	})
}

func TestAccDatabase(t *testing.T) {
	name := "tf_acc_" + suffix()
	config := func(extra string) string {
		return providerBlock + fmt.Sprintf(`
resource "noust_database" "main" {
  name   = %q
  engine = "postgresql"
  %s
  dump_before_destroy = false
}
`, name, extra)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy: destroyed(t, "noust_database", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
			db, err := client.GetDatabase(ctx, rs.Primary.Attributes["engine"], rs.Primary.Attributes["name"])
			if noust.IsNotFound(err) {
				return true, nil
			}
			return err == nil && db.Missing, err
		}),
		Steps: []resource.TestStep{
			{
				Config: config(""),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_database.main", "id", "postgresql/"+name),
					resource.TestCheckResourceAttrSet("noust_database.main", "owner"),
					resource.TestCheckResourceAttrSet("noust_database.main", "engine_version"),
				),
			},
			{Config: config(""), PlanOnly: true},
			{
				ResourceName:            "noust_database.main",
				ImportState:             true,
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: []string{"dump_before_destroy", "disconnect_clients_on_destroy", "unlink_apps_on_destroy", "deletion_protection"},
			},
			{
				// Data is not dropped by accident: protected, a destroy is refused.
				Config: config("deletion_protection = true"),
			},
			{Config: config("deletion_protection = true"), Destroy: true, ExpectError: regexpFor(`protected from deletion`)},
			{Config: config("deletion_protection = false")},
		},
	})
}

func TestAccDatabase_linkedToAnApplicationItIsDroppedBeforeIt(t *testing.T) {
	app := domainFor("tfdbl", suffix())
	name := "tf_link_" + suffix()
	config := providerBlock + appFor(app) + fmt.Sprintf(`
resource "noust_database" "main" {
  name                = %q
  engine              = "postgresql"
  app                 = noust_application.api.domain
  dump_before_destroy = false
}
`, name)
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy: resource.ComposeTestCheckFunc(
			appGone(t),
			destroyed(t, "noust_database", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
				db, err := client.GetDatabase(ctx, rs.Primary.Attributes["engine"], rs.Primary.Attributes["name"])
				if noust.IsNotFound(err) {
					return true, nil
				}
				return err == nil && db.Missing, err
			}),
		),
		Steps: []resource.TestStep{
			// Terraform destroys the database before the application it depends on, and the server refuses
			// to drop a database an application uses: the provider unlinks what it linked.
			{
				Config: config,
				Check:  resource.TestCheckResourceAttr("noust_database.main", "app", app),
			},
			{Config: config, PlanOnly: true},
		},
	})
}

func TestAccBackupSchedule(t *testing.T) {
	app := domainFor("tfbak", suffix())
	config := func(body string) string {
		return providerBlock + appFor(app) + fmt.Sprintf(`
resource "noust_backup_schedule" "daily" {
  app = noust_application.api.domain
  %s
}
`, body)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy: resource.ComposeTestCheckFunc(
			appGone(t),
			destroyed(t, "noust_backup_schedule", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
				schedule, err := client.GetBackupSchedule(ctx, rs.Primary.ID)
				return schedule == nil, err
			}),
		),
		Steps: []resource.TestStep{
			{
				Config: config(``),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "schedule", "daily"),
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "retention_count", "7"),
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "retention_days", "30"),
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "include_databases", "true"),
					resource.TestCheckResourceAttrSet("noust_backup_schedule.daily", "timer"),
				),
			},
			{
				Config: config(`
  schedule          = "weekly"
  retention_count   = 4
  retention_days    = 0
  retention_gb      = 2.5
  include_databases = false
`),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_backup_schedule.daily", plancheck.ResourceActionUpdate),
				}},
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "schedule", "weekly"),
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "retention_count", "4"),
					// 0 is "no age limit" and the server's null reads back as 0: no difference.
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "retention_days", "0"),
					resource.TestCheckResourceAttr("noust_backup_schedule.daily", "retention_gb", "2.5"),
				),
			},
			{
				Config: config(`
  schedule          = "*-*-* 03:30:00"
  retention_count   = 4
  retention_days    = 0
  retention_gb      = 2.5
  include_databases = false
`),
				Check: resource.TestCheckResourceAttr("noust_backup_schedule.daily", "on_calendar", "*-*-* 03:30:00"),
			},
			{ResourceName: "noust_backup_schedule.daily", ImportState: true, ImportStateVerify: true},
		},
	})
}

func TestAccDeployKey(t *testing.T) {
	app := domainFor("tfkey", suffix())
	config := providerBlock + appFor(app) + `
resource "noust_deploy_key" "repo" {
  app  = noust_application.api.domain
  name = "tf-key"
}
`
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				Config: config,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_deploy_key.repo", "id", app),
					resource.TestCheckResourceAttr("noust_deploy_key.repo", "name", "tf-key"),
					resource.TestCheckResourceAttr("noust_deploy_key.repo", "key_type", "ssh-ed25519"),
					resource.TestMatchResourceAttr("noust_deploy_key.repo", "public_key", regexpFor(`^ssh-ed25519 `)),
					resource.TestMatchResourceAttr("noust_deploy_key.repo", "fingerprint", regexpFor(`^SHA256:`)),
				),
			},
			{Config: config, PlanOnly: true},
			{ResourceName: "noust_deploy_key.repo", ImportState: true, ImportStateVerify: true, ImportStateVerifyIgnore: []string{"existing_key", "private_key"}},
		},
	})
}

func TestAccDataSources(t *testing.T) {
	domain := domainFor("tfdata", suffix())
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				Config: providerBlock + appFor(domain) + `
data "noust_servers" "all" {}

data "noust_team" "default" {
  slug = "default"
}

data "noust_applications" "production" {
  environment = "production"
  depends_on  = [noust_application.api]
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttrSet("data.noust_servers.all", "servers.0.name"),
					resource.TestCheckResourceAttr("data.noust_servers.all", "servers.0.local", "true"),
					resource.TestCheckResourceAttr("data.noust_team.default", "name", "Default"),
					func(state *terraform.State) error {
						found := false
						for key, value := range state.RootModule().Resources["data.noust_applications.production"].Primary.Attributes {
							if strings.HasSuffix(key, ".domain") && value == domain {
								found = true
							}
						}
						if !found {
							return fmt.Errorf("the production applications do not list %s", domain)
						}
						return nil
					},
				),
			},
		},
	})
}
