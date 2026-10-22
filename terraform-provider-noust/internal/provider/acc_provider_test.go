// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"fmt"
	"os"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

// providerWithToken configures the provider for the same server with another credential.
func providerWithToken(token string) string {
	return fmt.Sprintf(`provider "noust" {
  url   = %q
  token = %q
  job_poll_interval = "500ms"
}
`, os.Getenv("NOUST_URL"), token)
}

const oneTeam = `
resource "noust_team" "probe" {
  name = "Probe"
  slug = "probe-%s"
}
`

func TestAccProvider_aWrongTokenIsOneClearErrorAtTheStart(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		Steps: []resource.TestStep{{
			Config:      providerWithToken("noust_tok_not-a-token") + `data "noust_servers" "all" {}`,
			ExpectError: regexpFor(`(?s)Cannot sign in to the Noust server.*401`),
		}},
	})
}

func TestAccProvider_aTokenIsNeverSentInClearToAnotherHost(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		Steps: []resource.TestStep{{
			Config: `provider "noust" {
  url   = "http://noust.example.com"
  token = "anything"
}
data "noust_servers" "all" {}
`,
			ExpectError: regexpFor(`(?s)The provider is not configured.*cross the network in clear`),
		}},
	})
}

func TestAccProvider_somethingThatIsNotNoustIsRecognised(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		Steps: []resource.TestStep{{
			// The console serves its page on every other address; an API answer needs the version header.
			Config: fmt.Sprintf(`provider "noust" {
  url   = %q
  token = "anything"
}
data "noust_servers" "all" {}
`, "http://127.0.0.1:1"),
			ExpectError: regexpFor(`(?s)Cannot sign in to the Noust server.*cannot reach`),
		}},
	})
}

func TestAccPermissions_aTokenThatMayOnlyLookCannotChangeAndTheErrorNamesThePermission(t *testing.T) {
	id := suffix()
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheckProfiles(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		Steps: []resource.TestStep{
			{
				// It can plan: the data sources are reads.
				Config: providerWithToken(os.Getenv("NOUST_TOKEN_READ")) + `data "noust_team" "default" { slug = "default" }`,
				Check:  resource.TestCheckResourceAttr("data.noust_team.default", "slug", "default"),
			},
			{
				Config:      providerWithToken(os.Getenv("NOUST_TOKEN_READ")) + fmt.Sprintf(oneTeam, id),
				ExpectError: regexpFor(`(?s)Cannot create the team.*permission_denied.*needs\s+the\s+permission\s+teams\.manage`),
			},
		},
	})
}

func TestAccPermissions_aTokenWithoutSudoModeIsToldHowToGetIt(t *testing.T) {
	id := suffix()
	slug := "probe-" + id
	if os.Getenv("TF_ACC") == "" {
		t.Skip("Acceptance tests skipped unless env 'TF_ACC' set")
	}
	t.Cleanup(func() {
		// What the test could not remove with the credential it was given, the provider's token does.
		_ = accClient(t).DeleteTeam(context.Background(), slug)
	})
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheckProfiles(t) },
		ProtoV6ProviderFactories: protoV6Provider,
		CheckDestroy: destroyed(t, "noust_team", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
			_, err := client.GetTeam(ctx, rs.Primary.ID)
			return noust.IsNotFound(err), ignoreNotFound(err)
		}),
		Steps: []resource.TestStep{
			{
				// Making a team needs no sudo mode.
				Config: providerWithToken(os.Getenv("NOUST_TOKEN_NOSUDO")) + fmt.Sprintf(oneTeam, id),
				Check:  resource.TestCheckResourceAttr("noust_team.probe", "slug", slug),
			},
			{
				// Deleting one does.
				Config:      providerWithToken(os.Getenv("NOUST_TOKEN_NOSUDO")) + fmt.Sprintf(oneTeam, id),
				Destroy:     true,
				ExpectError: regexpFor(`(?s)Cannot delete the team.*sudo\s+mode.*allow_elevated`),
			},
			{
				// The provider's own token is issued with allow_elevated: the same destroy goes through.
				Config: providerWithToken(os.Getenv("NOUST_TOKEN")) + fmt.Sprintf(oneTeam, id),
			},
		},
	})
}
