// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-go/tftypes"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

// teamVariableState is the state of noust_team_variable.region in the team acme.
func teamVariableState(t *testing.T, r resource.Resource) tfsdk.State {
	t.Helper()
	var schemaResponse resource.SchemaResponse
	r.Schema(context.Background(), resource.SchemaRequest{}, &schemaResponse)
	kind := schemaResponse.Schema.Type().TerraformType(context.Background())
	text := func(s string) tftypes.Value { return tftypes.NewValue(tftypes.String, s) }
	return tfsdk.State{
		Schema: schemaResponse.Schema,
		Raw: tftypes.NewValue(kind, map[string]tftypes.Value{
			"id":       text("acme/REGION"),
			"team":     text("acme"),
			"name":     text("REGION"),
			"value":    text("eu-west"),
			"secret":   tftypes.NewValue(tftypes.Bool, nil),
			"audience": text("both"),
		}),
	}
}

func variableResourceAgainst(t *testing.T, handler http.HandlerFunc) resource.Resource {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set(noust.VersionHeader, noust.APIVersion)
		handler(w, r)
	}))
	t.Cleanup(server.Close)
	client, err := noust.New(noust.Config{URL: server.URL, Token: "tok_0123456789abcdef"})
	if err != nil {
		t.Fatal(err)
	}
	r := NewTeamVariableResource()
	r.(*variableResource).data = &providerData{client: client}
	return r
}

func TestRemovingAVariableApplicationsStillReferToIsDoneAndWarnsWithTheirNames(t *testing.T) {
	r := variableResourceAgainst(t, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"name":"REGION","applications":["shop.example.com","api.example.com"]}`))
	})
	var response resource.DeleteResponse

	r.Delete(context.Background(), resource.DeleteRequest{State: teamVariableState(t, r)}, &response)

	if response.Diagnostics.HasError() {
		t.Fatalf("a removal the server allowed is an error: %v", response.Diagnostics)
	}
	warnings := response.Diagnostics.Warnings()
	if len(warnings) != 1 {
		t.Fatalf("want one warning, got %v", response.Diagnostics)
	}
	for _, want := range []string{"shop.example.com, api.example.com", "{{team.REGION}}", "next deploy fails"} {
		if !strings.Contains(warnings[0].Detail(), want) {
			t.Errorf("the warning misses %q: %s", want, warnings[0].Detail())
		}
	}
}

func TestRemovingAVariableNobodyReferstoIsSilentAndOneAlreadyGoneIsNotAnError(t *testing.T) {
	quiet := variableResourceAgainst(t, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"name":"REGION","applications":[]}`))
	})
	var response resource.DeleteResponse
	quiet.Delete(context.Background(), resource.DeleteRequest{State: teamVariableState(t, quiet)}, &response)
	if len(response.Diagnostics) != 0 {
		t.Errorf("nothing to say: %v", response.Diagnostics)
	}

	gone := variableResourceAgainst(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(404)
		_, _ = w.Write([]byte(`{"error":"not_found","detail":"No variable REGION in acme"}`))
	})
	response = resource.DeleteResponse{}
	gone.Delete(context.Background(), resource.DeleteRequest{State: teamVariableState(t, gone)}, &response)
	if len(response.Diagnostics) != 0 {
		t.Errorf("a variable that is already gone is what destroy wants: %v", response.Diagnostics)
	}
}

func TestARefusedRemovalIsTheServersOwnError(t *testing.T) {
	r := variableResourceAgainst(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(403)
		_, _ = w.Write([]byte(`{"error":"elevation_required","detail":"Confirm it's you to continue"}`))
	})
	var response resource.DeleteResponse

	r.Delete(context.Background(), resource.DeleteRequest{State: teamVariableState(t, r)}, &response)

	errors := response.Diagnostics.Errors()
	if len(errors) != 1 || !strings.Contains(errors[0].Detail(), "allow_elevated") {
		t.Errorf("the refusal for sudo mode says how to get it: %v", response.Diagnostics)
	}
}

func TestAMaskedSecretKeepsTheValueTheStateHasAndAnImportedOneTakesTheMask(t *testing.T) {
	masked := &noust.Variable{Name: "DB_PASSWORD", Value: "********", Audience: "both", Secrecy: noust.Secrecy{Secret: true, Reason: "name"}}
	plain := &noust.Variable{Name: "REGION", Value: "eu-west", Audience: "runtime", Secrecy: noust.Secrecy{Reason: "plain"}}
	marked := &noust.Variable{Name: "NOTE", Value: "shown", Audience: "both", Secrecy: noust.Secrecy{Reason: "marked not secret", Marked: true}}

	configured := variableValues{value: types.StringValue("the-real-one")}
	configured.adopt(masked)
	if configured.value.ValueString() != "the-real-one" {
		t.Errorf("a secret's value is not in any answer, the state's stays: %q", configured.value.ValueString())
	}
	if !configured.secret.IsNull() {
		t.Error("the classifier's verdict is not an operator's mark")
	}

	imported := variableValues{}
	imported.adopt(masked)
	if imported.value.ValueString() != "********" {
		t.Errorf("an imported secret has only the mask to show: %q", imported.value.ValueString())
	}

	seen := variableValues{value: types.StringValue("old")}
	seen.adopt(plain)
	if seen.value.ValueString() != "eu-west" || seen.audience.ValueString() != "runtime" {
		t.Errorf("what is not a secret is read: %+v", seen)
	}

	decided := variableValues{value: types.StringValue("shown")}
	decided.adopt(marked)
	if decided.secret.IsNull() || decided.secret.ValueBool() {
		t.Errorf("an operator's mark is read back as it was written: %+v", decided.secret)
	}
}
