// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"fmt"
	"os"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/plancheck"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

func teamsConfig(id, teamName, projectName, environmentName string) string {
	return providerBlock + fmt.Sprintf(`
resource "noust_team" "acme" {
  name = %[2]q
  slug = "acme-%[1]s"
}

resource "noust_project" "web" {
  team = noust_team.acme.slug
  name = %[3]q
  slug = "web"
}

resource "noust_environment" "staging" {
  team    = noust_team.acme.slug
  project = noust_project.web.slug
  name    = %[4]q
  slug    = "staging"
}

data "noust_team" "acme" {
  slug       = noust_team.acme.slug
  depends_on = [noust_project.web, noust_environment.staging]
}
`, id, teamName, projectName, environmentName)
}

func TestAccTeamProjectEnvironment(t *testing.T) {
	id := suffix()
	slug := "acme-" + id
	check := func(t *testing.T) resource.TestCheckFunc {
		return resource.ComposeTestCheckFunc(
			destroyed(t, "noust_team", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
				_, err := client.GetTeam(ctx, rs.Primary.ID)
				return noust.IsNotFound(err), ignoreNotFound(err)
			}),
			destroyed(t, "noust_project", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
				_, err := client.GetProject(ctx, rs.Primary.Attributes["team"], rs.Primary.Attributes["slug"])
				return noust.IsNotFound(err), ignoreNotFound(err)
			}),
		)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             check(t),
		Steps: []resource.TestStep{
			{
				Config: teamsConfig(id, "Acme", "Web", "Staging"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_team.acme", "id", slug),
					resource.TestCheckResourceAttr("noust_team.acme", "name", "Acme"),
					resource.TestCheckResourceAttr("noust_project.web", "id", slug+"/web"),
					resource.TestCheckResourceAttr("noust_environment.staging", "id", slug+"/web/staging"),
					resource.TestCheckResourceAttr("data.noust_team.acme", "name", "Acme"),
					// A team is made with its default project: the one just made is its second.
					resource.TestCheckResourceAttr("data.noust_team.acme", "projects", "2"),
				),
			},
			{
				// A rename is an update in place; the slugs are the identity.
				Config: teamsConfig(id, "Acme Corp", "Web shop", "Pre-production"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_team.acme", plancheck.ResourceActionUpdate),
					plancheck.ExpectResourceAction("noust_project.web", plancheck.ResourceActionUpdate),
					plancheck.ExpectResourceAction("noust_environment.staging", plancheck.ResourceActionUpdate),
				}},
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_team.acme", "name", "Acme Corp"),
					resource.TestCheckResourceAttr("noust_project.web", "name", "Web shop"),
					resource.TestCheckResourceAttr("noust_environment.staging", "name", "Pre-production"),
				),
			},
			{ResourceName: "noust_team.acme", ImportState: true, ImportStateVerify: true},
			{ResourceName: "noust_project.web", ImportState: true, ImportStateVerify: true},
			{ResourceName: "noust_environment.staging", ImportState: true, ImportStateVerify: true},
			{
				// Renamed behind Terraform's back: the next plan says so.
				PreConfig: func() {
					if _, err := accClient(t).RenameTeam(context.Background(), slug, "Renamed by hand"); err != nil {
						t.Fatal(err)
					}
				},
				Config:             teamsConfig(id, "Acme Corp", "Web shop", "Pre-production"),
				PlanOnly:           true,
				ExpectNonEmptyPlan: true,
			},
			{
				Config: teamsConfig(id, "Acme Corp", "Web shop", "Pre-production"),
				Check:  resource.TestCheckResourceAttr("noust_team.acme", "name", "Acme Corp"),
			},
		},
	})
}

func TestAccTeamRefusesToBeDeletedWhileItHoldsSomething(t *testing.T) {
	id := suffix()
	slug := "held-" + id
	database := "held_" + id
	config := providerBlock + fmt.Sprintf(`resource "noust_team" "held" {
  name = "Held"
  slug = %q
}
`, slug)
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		Steps: []resource.TestStep{
			{Config: config},
			{
				// A database of the team, made outside Terraform: the server will not delete a team that
				// holds anything, and says what it holds.
				PreConfig: func() {
					_, err := accClient(t).CreateDatabase(context.Background(), noust.CreateDatabaseRequest{Name: database, Engine: "postgresql", Team: slug})
					if err != nil {
						t.Fatal(err)
					}
				},
				Config:      config,
				Destroy:     true,
				ExpectError: regexpFor(`(?s)Cannot delete the team.*409`),
			},
			{
				// Emptied, the framework's own destroy at the end of the test succeeds.
				PreConfig: func() {
					client := accClient(t)
					accepted, err := client.DropDatabase(context.Background(), "postgresql", database, noust.DropDatabaseOptions{})
					if err != nil {
						t.Fatal(err)
					}
					if _, err := client.WaitForJob(context.Background(), accepted, 0, 0); err != nil {
						t.Fatal(err)
					}
				},
				Config: config,
			},
		},
	})
}

func TestAccTeamMember(t *testing.T) {
	id := suffix()
	slug := "crew-" + id
	// The account the tokens belong to exists on the server, and a team can have it as a member.
	account := os.Getenv("NOUST_ACCOUNT")
	config := func(role string) string {
		return providerBlock + fmt.Sprintf(`
resource "noust_team" "crew" {
  name = "Crew"
  slug = %q
}

resource "noust_team_member" "member" {
  team     = noust_team.crew.slug
  username = %q
  role     = %q
}
`, slug, account, role)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t); requireAccount(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy: destroyed(t, "noust_team_member", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
			member, err := client.GetTeamMember(ctx, rs.Primary.Attributes["team"], rs.Primary.Attributes["username"])
			return member == nil, ignoreNotFound(err)
		}),
		Steps: []resource.TestStep{
			{
				Config: config("viewer"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_team_member.member", "id", slug+"/"+account),
					resource.TestCheckResourceAttr("noust_team_member.member", "role", "viewer"),
				),
			},
			{
				// A role is changed in place.
				Config: config("operator"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_team_member.member", plancheck.ResourceActionUpdate),
				}},
				Check: resource.TestCheckResourceAttr("noust_team_member.member", "role", "operator"),
			},
			{ResourceName: "noust_team_member.member", ImportState: true, ImportStateVerify: true},
			{
				// Changed by hand, it is a difference.
				PreConfig: func() {
					if _, err := accClient(t).SetTeamMemberRole(context.Background(), slug, account, "admin"); err != nil {
						t.Fatal(err)
					}
				},
				Config:             config("operator"),
				PlanOnly:           true,
				ExpectNonEmptyPlan: true,
			},
		},
	})
}
