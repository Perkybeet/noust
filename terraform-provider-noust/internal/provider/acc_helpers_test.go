// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/hashicorp/terraform-plugin-testing/helper/acctest"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

// The acceptance tests (TF_ACC=1) run against a real Noust API: tests/integration/terraform_run.py starts
// scripts/console_server.py, issues the tokens below with the permissions each profile names, and runs
// `go test`. They are never given the master token.
//
//	NOUST_URL             the server
//	NOUST_TOKEN           the "provider" profile: every permission the provider asks for, allow_elevated
//	NOUST_TOKEN_NOSUDO    the same permissions without allow_elevated
//	NOUST_TOKEN_READ      may look and nothing else
const (
	codeHostSite    = "https://code.example.net/acme/site.git"
	codeHostService = "https://code.example.net/acme/service.git"
)

func testAccPreCheck(t *testing.T) {
	t.Helper()
	for _, name := range []string{"NOUST_URL", "NOUST_TOKEN"} {
		if os.Getenv(name) == "" {
			t.Fatalf("%s must be set for the acceptance tests: see tests/integration/terraform_run.py", name)
		}
	}
}

func testAccPreCheckProfiles(t *testing.T) {
	t.Helper()
	testAccPreCheck(t)
	for _, name := range []string{"NOUST_TOKEN_NOSUDO", "NOUST_TOKEN_READ"} {
		if os.Getenv(name) == "" {
			t.Fatalf("%s must be set for the permission tests", name)
		}
	}
}

// accClient is a client of the same server, with the provider's own token, for what a test checks or
// changes behind Terraform's back.
func accClient(t *testing.T) *noust.Client {
	t.Helper()
	client, err := noust.New(noust.Config{URL: os.Getenv("NOUST_URL"), Token: os.Getenv("NOUST_TOKEN"), UserAgent: "terraform-provider-noust/acceptance"})
	if err != nil {
		t.Fatal(err)
	}
	return client
}

// suffix makes a name that is not another test's, nor the previous run's.
func suffix() string {
	return strings.ToLower(acctest.RandStringFromCharSet(6, acctest.CharSetAlpha))
}

func domainFor(prefix, id string) string { return fmt.Sprintf("%s-%s.example.net", prefix, id) }

// terraformState is what a check function of the testing framework receives.
type terraformState = terraform.State

const providerBlock = `provider "noust" {
  job_poll_interval = "500ms"
}
`

// destroyed checks that the object a resource stood for is gone from the server, asking the server.
func destroyed(t *testing.T, kind string, gone func(ctx context.Context, client *noust.Client, state *terraform.ResourceState) (bool, error)) resource.TestCheckFunc {
	return func(state *terraform.State) error {
		client := accClient(t)
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		for _, rs := range state.RootModule().Resources {
			if rs.Type != kind {
				continue
			}
			ok, err := gone(ctx, client, rs)
			if err != nil {
				return fmt.Errorf("asking whether %s %s is gone: %w", kind, rs.Primary.ID, err)
			}
			if !ok {
				return fmt.Errorf("%s %s is still on the server", kind, rs.Primary.ID)
			}
		}
		return nil
	}
}

func appGone(t *testing.T) resource.TestCheckFunc {
	return destroyed(t, "noust_application", func(ctx context.Context, client *noust.Client, rs *terraform.ResourceState) (bool, error) {
		_, err := client.GetApp(ctx, rs.Primary.ID)
		return noust.IsNotFound(err), ignoreNotFound(err)
	})
}

func ignoreNotFound(err error) error {
	if noust.IsNotFound(err) {
		return nil
	}
	return err
}
