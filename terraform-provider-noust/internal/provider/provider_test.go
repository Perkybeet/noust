// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider_test

import (
	"context"
	"sort"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"

	"github.com/Perkybeet/terraform-provider-noust/internal/provider"
)

// protoV6Provider is the provider as Terraform reaches it.
var protoV6Provider = map[string]func() (tfprotov6.ProviderServer, error){
	"noust": providerserver.NewProtocol6WithError(provider.New("test")()),
}

func TestEverySchemaIsValidAndTheProviderOffersWhatTheDocsPromise(t *testing.T) {
	server, err := protoV6Provider["noust"]()
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.GetProviderSchema(context.Background(), &tfprotov6.GetProviderSchemaRequest{})
	if err != nil {
		t.Fatal(err)
	}
	for _, diagnostic := range response.Diagnostics {
		t.Errorf("%s: %s", diagnostic.Summary, diagnostic.Detail)
	}

	var resources, dataSources []string
	for name := range response.ResourceSchemas {
		resources = append(resources, name)
	}
	for name := range response.DataSourceSchemas {
		dataSources = append(dataSources, name)
	}
	sort.Strings(resources)
	sort.Strings(dataSources)
	wantResources := []string{
		"noust_application", "noust_backup_schedule", "noust_database", "noust_deploy_key", "noust_domain",
		"noust_environment", "noust_project", "noust_registry_login", "noust_team", "noust_team_member",
	}
	wantDataSources := []string{"noust_application", "noust_applications", "noust_servers", "noust_team"}
	if strings.Join(resources, ",") != strings.Join(wantResources, ",") {
		t.Errorf("resources %v, want %v", resources, wantResources)
	}
	if strings.Join(dataSources, ",") != strings.Join(wantDataSources, ",") {
		t.Errorf("data sources %v, want %v", dataSources, wantDataSources)
	}
}

func TestSecretsAreMarkedSensitive(t *testing.T) {
	server, _ := protoV6Provider["noust"]()
	response, err := server.GetProviderSchema(context.Background(), &tfprotov6.GetProviderSchemaRequest{})
	if err != nil {
		t.Fatal(err)
	}
	sensitive := map[string][]string{
		"noust_application":    {"env_vars"},
		"noust_registry_login": {"password"},
		"noust_deploy_key":     {"private_key"},
	}
	for resource, names := range sensitive {
		attributes := map[string]bool{}
		for _, attribute := range response.ResourceSchemas[resource].Block.Attributes {
			attributes[attribute.Name] = attribute.Sensitive
		}
		for _, name := range names {
			if !attributes[name] {
				t.Errorf("%s.%s is not sensitive", resource, name)
			}
		}
	}
	for _, attribute := range response.Provider.Block.Attributes {
		if attribute.Name == "token" && !attribute.Sensitive {
			t.Error("the provider's token is not sensitive")
		}
	}
}
