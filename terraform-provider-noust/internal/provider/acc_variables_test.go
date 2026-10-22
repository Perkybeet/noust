// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/plancheck"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

// variableGone is the destroy check of a shared variable: the server no longer lists it.
func variableGone(t *testing.T, kind string, scope noust.VariableScope) resource.TestCheckFunc {
	return destroyed(t, kind, func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
		var owner []string
		for _, part := range scope.Owner {
			owner = append(owner, rs.Primary.Attributes[part])
		}
		found, err := client.GetVariable(ctx, scope, owner, rs.Primary.Attributes["name"])
		// A team that is gone took its variables with it.
		return found == nil || noust.IsNotFound(err), ignoreNotFound(err)
	})
}

func allVariablesGone(t *testing.T) resource.TestCheckFunc {
	return resource.ComposeTestCheckFunc(
		variableGone(t, "noust_team_variable", noust.TeamVariables),
		variableGone(t, "noust_project_variable", noust.ProjectVariables),
		variableGone(t, "noust_environment_variable", noust.EnvironmentVariables),
		variableGone(t, "noust_server_variable", noust.ServerVariables),
	)
}

func variablesConfig(id, serverName, region, audience string) string {
	return providerBlock + fmt.Sprintf(`
resource "noust_team" "acme" {
  name = "Acme"
  slug = "vars-%[1]s"
}

resource "noust_project" "web" {
  team = noust_team.acme.slug
  name = "Web"
  slug = "web"
}

resource "noust_environment" "staging" {
  team    = noust_team.acme.slug
  project = noust_project.web.slug
  name    = "Staging"
  slug    = "staging"
}

resource "noust_team_variable" "region" {
  team     = noust_team.acme.slug
  name     = "REGION"
  value    = %[3]q
  audience = %[4]q
}

resource "noust_project_variable" "region" {
  team     = noust_team.acme.slug
  project  = noust_project.web.slug
  name     = "REGION"
  value    = %[3]q
  audience = %[4]q
}

resource "noust_environment_variable" "region" {
  team        = noust_team.acme.slug
  project     = noust_project.web.slug
  environment = noust_environment.staging.slug
  name        = "REGION"
  value       = %[3]q
  audience    = %[4]q
}

resource "noust_server_variable" "region" {
  name     = %[2]q
  value    = %[3]q
  audience = %[4]q
}
`, id, serverName, region, audience)
}

func TestAccSharedVariables(t *testing.T) {
	id := suffix()
	team := "vars-" + id
	server := "TF_ACC_" + strings.ToUpper(id)
	drifted := func() {
		_, err := accClient(t).SetVariable(context.Background(), noust.TeamVariables, []string{team}, "REGION", noust.VariableRequest{Value: "us-east-1"})
		if err != nil {
			t.Fatal(err)
		}
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             allVariablesGone(t),
		Steps: []resource.TestStep{
			{
				Config: variablesConfig(id, server, "eu-west-1", "both"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_team_variable.region", "id", team+"/REGION"),
					resource.TestCheckResourceAttr("noust_project_variable.region", "id", team+"/web/REGION"),
					resource.TestCheckResourceAttr("noust_environment_variable.region", "id", team+"/web/staging/REGION"),
					resource.TestCheckResourceAttr("noust_server_variable.region", "id", server),
					resource.TestCheckResourceAttr("noust_team_variable.region", "value", "eu-west-1"),
					resource.TestCheckResourceAttr("noust_team_variable.region", "audience", "both"),
					// Nothing marked it, so the attribute stays what the configuration said: nothing.
					resource.TestCheckNoResourceAttr("noust_team_variable.region", "secret"),
				),
			},
			{
				// The value and the audience change in place; the names are the identity.
				Config: variablesConfig(id, server, "eu-central-1", "runtime"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_team_variable.region", plancheck.ResourceActionUpdate),
					plancheck.ExpectResourceAction("noust_project_variable.region", plancheck.ResourceActionUpdate),
					plancheck.ExpectResourceAction("noust_environment_variable.region", plancheck.ResourceActionUpdate),
					plancheck.ExpectResourceAction("noust_server_variable.region", plancheck.ResourceActionUpdate),
				}},
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("noust_team_variable.region", "value", "eu-central-1"),
					resource.TestCheckResourceAttr("noust_server_variable.region", "audience", "runtime"),
				),
			},
			{ResourceName: "noust_team_variable.region", ImportState: true, ImportStateVerify: true},
			{ResourceName: "noust_project_variable.region", ImportState: true, ImportStateVerify: true},
			{ResourceName: "noust_environment_variable.region", ImportState: true, ImportStateVerify: true},
			{ResourceName: "noust_server_variable.region", ImportState: true, ImportStateVerify: true},
			{
				// Changed behind Terraform's back, a value that is not a secret shows in the next plan.
				PreConfig:          drifted,
				Config:             variablesConfig(id, server, "eu-central-1", "runtime"),
				PlanOnly:           true,
				ExpectNonEmptyPlan: true,
			},
			{
				Config: variablesConfig(id, server, "eu-central-1", "runtime"),
				Check:  resource.TestCheckResourceAttr("noust_team_variable.region", "value", "eu-central-1"),
			},
			{
				// Removed behind Terraform's back, it is planned again.
				PreConfig: func() {
					if _, err := accClient(t).DeleteVariable(context.Background(), noust.ServerVariables, nil, server); err != nil {
						t.Fatal(err)
					}
				},
				Config:             variablesConfig(id, server, "eu-central-1", "runtime"),
				PlanOnly:           true,
				ExpectNonEmptyPlan: true,
			},
			{
				Config: variablesConfig(id, server, "eu-central-1", "runtime"),
				Check:  resource.TestCheckResourceAttr("noust_server_variable.region", "value", "eu-central-1"),
			},
		},
	})
}

func secretsConfig(id, password, note string, noteMark string) string {
	return providerBlock + fmt.Sprintf(`
resource "noust_team" "vault" {
  name = "Vault"
  slug = "vault-%[1]s"
}

# Secret by its name: the classifier of the server decides.
resource "noust_team_variable" "db_password" {
  team  = noust_team.vault.slug
  name  = "DB_PASSWORD"
  value = %[2]q
}

# Marked a secret although nothing about it says so.
resource "noust_team_variable" "note" {
  team   = noust_team.vault.slug
  name   = "RELEASE_NOTE"
  value  = %[3]q
  %[4]s
}

# Marked not a secret although its name says so: the server shows it, so a change is seen.
resource "noust_team_variable" "public_token" {
  team   = noust_team.vault.slug
  name   = "PUBLIC_TOKEN"
  value  = "pk_visible"
  secret = false
}
`, id, password, note, noteMark)
}

func TestAccSharedVariableSecretsStayMaskedAndTheMarkIsTheOperators(t *testing.T) {
	id := suffix()
	team := "vault-" + id
	changeBehindTheBack := func(name, value string) func() {
		return func() {
			if _, err := accClient(t).SetVariable(context.Background(), noust.TeamVariables, []string{team}, name, noust.VariableRequest{Value: value}); err != nil {
				t.Fatal(err)
			}
		}
	}
	verifyPassword := func(state *terraform.State) error {
		got, err := accClient(t).GetVariable(context.Background(), noust.TeamVariables, []string{team}, "DB_PASSWORD")
		if err != nil || got == nil {
			return fmt.Errorf("the password is not on the server: %v", err)
		}
		if !got.Secrecy.Secret || got.Value == "s3cret-one" {
			return fmt.Errorf("the server shows the password: %+v", got)
		}
		return nil
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             variableGone(t, "noust_team_variable", noust.TeamVariables),
		Steps: []resource.TestStep{
			{
				Config: secretsConfig(id, "s3cret-one", "first", "secret = true"),
				Check: resource.ComposeAggregateTestCheckFunc(
					verifyPassword,
					// The state keeps what the configuration wrote, since the server never says a secret.
					resource.TestCheckResourceAttr("noust_team_variable.db_password", "value", "s3cret-one"),
					resource.TestCheckNoResourceAttr("noust_team_variable.db_password", "secret"),
					resource.TestCheckResourceAttr("noust_team_variable.note", "secret", "true"),
					resource.TestCheckResourceAttr("noust_team_variable.public_token", "secret", "false"),
				),
			},
			{
				// A secret is changed in place like any other value.
				Config: secretsConfig(id, "s3cret-two", "first", "secret = true"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_team_variable.db_password", plancheck.ResourceActionUpdate),
					plancheck.ExpectResourceAction("noust_team_variable.note", plancheck.ResourceActionNoop),
				}},
				Check: resource.TestCheckResourceAttr("noust_team_variable.db_password", "value", "s3cret-two"),
			},
			{
				// Without the mark the note is an ordinary value again, and the server shows it.
				Config: secretsConfig(id, "s3cret-two", "first", ""),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_team_variable.note", plancheck.ResourceActionUpdate),
				}},
				Check: resource.TestCheckNoResourceAttr("noust_team_variable.note", "secret"),
			},
			{
				// A change made by hand to a secret cannot be seen: the API masks it in every read. This is
				// the limit the documentation states, and the plan stays empty.
				PreConfig: changeBehindTheBack("DB_PASSWORD", "changed-by-hand"),
				Config:    secretsConfig(id, "s3cret-two", "first", ""),
				PlanOnly:  true,
			},
			{
				// What the server shows is compared: the same change to a variable marked not secret is.
				PreConfig:          changeBehindTheBack("PUBLIC_TOKEN", "changed-by-hand"),
				Config:             secretsConfig(id, "s3cret-two", "first", ""),
				PlanOnly:           true,
				ExpectNonEmptyPlan: true,
			},
			{
				Config: secretsConfig(id, "s3cret-two", "first", ""),
				Check:  resource.TestCheckResourceAttr("noust_team_variable.public_token", "value", "pk_visible"),
			},
			{
				// A secret cannot be imported with its value; the configuration sets it on the next apply.
				ResourceName:            "noust_team_variable.db_password",
				ImportState:             true,
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: []string{"value"},
			},
			{ResourceName: "noust_team_variable.public_token", ImportState: true, ImportStateVerify: true},
		},
	})
}

func TestAccSharedVariableRefusesWhatTheServerRefusesAndSaysItWordForWord(t *testing.T) {
	id := suffix()
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		Steps: []resource.TestStep{
			{
				Config: providerBlock + fmt.Sprintf(`resource "noust_server_variable" "bad" {
  name  = "not a name-%s"
  value = "x"
}
`, id),
				ExpectError: regexpFor(`(?s)Cannot\s+set\s+the\s+variable.*Invalid\s+environment\s+variable\s+name`),
			},
			{
				Config: providerBlock + `resource "noust_team_variable" "orphan" {
  team  = "no-such-team"
  name  = "X"
  value = "x"
}
`,
				ExpectError: regexpFor(`(?s)Cannot set the variable.*404`),
			},
			{
				Config: providerBlock + `resource "noust_server_variable" "bad" {
  name     = "X"
  value    = "x"
  audience = "sometimes"
}
`,
				ExpectError: regexpFor(`(?s)audience.*one\s+of`),
			},
		},
	})
}

func TestAccSharedVariableNeedsSudoModeAndSaysHowToGetIt(t *testing.T) {
	id := suffix()
	name := "TF_ACC_NOSUDO_" + strings.ToUpper(id)
	if os.Getenv("TF_ACC") == "" {
		t.Skip("Acceptance tests skipped unless env 'TF_ACC' set")
	}
	t.Cleanup(func() {
		_, _ = accClient(t).DeleteVariable(context.Background(), noust.ServerVariables, nil, name)
	})
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheckProfiles(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		Steps: []resource.TestStep{{
			Config: providerWithToken(os.Getenv("NOUST_TOKEN_NOSUDO")) + fmt.Sprintf(`resource "noust_server_variable" "x" {
  name  = %q
  value = "x"
}
`, name),
			ExpectError: regexpFor(`(?s)Cannot\s+set\s+the\s+variable.*sudo\s+mode.*allow_elevated`),
		}},
	})
}

func referencingApplication(id, domain, level, suffix string) string {
	return providerBlock + fmt.Sprintf(`
resource "noust_team" "shop" {
  name = "Shop"
  slug = "refs-%[1]s"
}

resource "noust_project" "web" {
  team = noust_team.shop.slug
  name = "Web"
  slug = "web"
}

resource "noust_project_variable" "log_level" {
  team    = noust_team.shop.slug
  project = noust_project.web.slug
  name    = "LOG_LEVEL"
  value   = %[3]q
}

# The application names the variable through the resource, so Terraform removes the application first.
resource "noust_application" "site" {
  domain   = %[2]q
  source   = %[5]q
  branch   = "main"
  app_type = "static"
  team     = noust_team.shop.slug
  project  = noust_project.web.slug

  env_vars = {
    LOG_LEVEL = "{{project.${noust_project_variable.log_level.name}}}%[4]s"
  }

  remove_files_on_destroy = true
}
`, id, domain, level, suffix, codeHostSite)
}

// An application's variable made of a reference is resolved by the server and stored as the text it was
// written from. The plan must compare the text, and not the value it resolved to, or it would show a
// difference after every apply.
func TestAccApplicationVariablesMadeOfReferencesPlanEmptyAndFollowTheSharedVariable(t *testing.T) {
	id := suffix()
	domain := domainFor("tfrefs", id)
	resolved := func(want string) resource.TestCheckFunc {
		return func(*terraform.State) error {
			env, err := accClient(t).GetAppEnv(context.Background(), domain)
			if err != nil {
				return err
			}
			if env.Variables["LOG_LEVEL"] != want {
				return fmt.Errorf("the server resolved LOG_LEVEL to %q, want %q", env.Variables["LOG_LEVEL"], want)
			}
			return nil
		}
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy:             appGone(t),
		Steps: []resource.TestStep{
			{
				Config: referencingApplication(id, domain, "info", ""),
				Check:  resolved("info"),
			},
			{
				// The shared variable changes; the application's configuration does not, so nothing is planned
				// for it: the next deploy resolves the reference again.
				Config: referencingApplication(id, domain, "debug", ""),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_project_variable.log_level", plancheck.ResourceActionUpdate),
					plancheck.ExpectResourceAction("noust_application.site", plancheck.ResourceActionNoop),
				}},
			},
			{
				// A different expression is a change of the application, made in place.
				Config: referencingApplication(id, domain, "debug", "-verbose"),
				ConfigPlanChecks: resource.ConfigPlanChecks{PreApply: []plancheck.PlanCheck{
					plancheck.ExpectResourceAction("noust_application.site", plancheck.ResourceActionUpdate),
				}},
				Check: resolved("debug-verbose"),
			},
			{
				Config:   referencingApplication(id, domain, "debug", "-verbose"),
				PlanOnly: true,
			},
		},
	})
}
