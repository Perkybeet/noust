// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/plancheck"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

func registryConfig(name, username, password string) string {
	return providerBlock + fmt.Sprintf(`
resource "noust_registry_login" "ghcr" {
  name     = %[1]q
  kind     = "ghcr"
  username = %[2]q
  password = %[3]q
}
`, name, username, password)
}

func TestAccRegistryLogin(t *testing.T) {
	name := "ghcr-" + suffix()
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy: destroyed(t, "noust_registry_login", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
			_, err := client.GetRegistryLogin(ctx, rs.Primary.ID)
			return noust.IsNotFound(err), ignoreNotFound(err)
		}),
		Steps: []resource.TestStep{
			{
				Config: registryConfig(name, "deployer", "first-secret"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_registry_login.ghcr", "id", name),
					resource.TestCheckResourceAttr("noust_registry_login.ghcr", "server", "ghcr.io"),
					resource.TestCheckResourceAttr("noust_registry_login.ghcr", "username", "deployer"),
					// The server never returns it; the state holds the configuration's.
					resource.TestCheckResourceAttr("noust_registry_login.ghcr", "password", "first-secret"),
				),
			},
			{
				// A new password and user are an update in place, not a new account.
				Config: registryConfig(name, "deployer-2", "second-secret"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_registry_login.ghcr", plancheck.ResourceActionUpdate),
				}},
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_registry_login.ghcr", "username", "deployer-2"),
					resource.TestCheckResourceAttr("noust_registry_login.ghcr", "password", "second-secret"),
				),
			},
			{
				ResourceName:            "noust_registry_login.ghcr",
				ImportState:             true,
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: []string{"password"},
			},
			{
				Config:   registryConfig(name, "deployer-2", "second-secret"),
				PlanOnly: true,
			},
		},
	})
}
