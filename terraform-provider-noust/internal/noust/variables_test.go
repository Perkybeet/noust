// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"testing"
)

const variableList = `{"scope":"team","label":"acme","syntax":"{{team.NAME}}","unmasked":false,"variables":[
 {"name":"REGION","value":"eu-west","audience":"both","references":[],"secrecy":{"secret":false,"reason":"plain","marked":false}},
 {"name":"STRIPE_KEY","value":"********","audience":"runtime","references":[],"secrecy":{"secret":true,"reason":"name","marked":false}}]}`

func TestEachScopeAddressesItsVariablesByTheirOwnersThenTheirName(t *testing.T) {
	for _, tc := range []struct {
		scope VariableScope
		owner []string
		list  string
		one   string
	}{
		{ServerVariables, nil, "/api/v1/variables/server", "/api/v1/variables/server/REGION"},
		{TeamVariables, []string{"acme"}, "/api/v1/teams/acme/variables", "/api/v1/teams/acme/variables/REGION"},
		{ProjectVariables, []string{"acme", "web"}, "/api/v1/teams/acme/projects/web/variables", "/api/v1/teams/acme/projects/web/variables/REGION"},
		{
			EnvironmentVariables, []string{"acme", "web", "staging"},
			"/api/v1/teams/acme/projects/web/environments/staging/variables",
			"/api/v1/teams/acme/projects/web/environments/staging/variables/REGION",
		},
	} {
		t.Run(tc.scope.Name, func(t *testing.T) {
			var paths []string
			client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
				paths = append(paths, r.Method+" "+r.URL.Path)
				if r.Method == http.MethodDelete {
					_, _ = w.Write([]byte(`{"name":"REGION","applications":[]}`))
					return
				}
				_, _ = w.Write([]byte(variableList))
			})
			ctx := context.Background()

			if _, err := client.GetVariable(ctx, tc.scope, tc.owner, "REGION"); err != nil {
				t.Fatal(err)
			}
			if _, err := client.SetVariable(ctx, tc.scope, tc.owner, "REGION", VariableRequest{Value: "eu-west"}); err != nil {
				t.Fatal(err)
			}
			if _, err := client.DeleteVariable(ctx, tc.scope, tc.owner, "REGION"); err != nil {
				t.Fatal(err)
			}

			want := []string{"GET " + tc.list, "PUT " + tc.one, "DELETE " + tc.one}
			for i := range want {
				if paths[i] != want[i] {
					t.Errorf("call %d was %s, want %s", i, paths[i], want[i])
				}
			}
			if len(tc.owner) != len(tc.scope.Owner) {
				t.Errorf("%s names %v as its owner's parts and the test passes %v", tc.scope.Name, tc.scope.Owner, tc.owner)
			}
		})
	}
}

func TestAVariableTheScopeDoesNotHoldIsNilAndAnUnknownOwnerIsNotFound(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/teams/ghost/variables" {
			w.WriteHeader(404)
			_, _ = w.Write([]byte(`{"error":"not_found","detail":"No team ghost"}`))
			return
		}
		_, _ = w.Write([]byte(variableList))
	})
	ctx := context.Background()

	absent, err := client.GetVariable(ctx, TeamVariables, []string{"acme"}, "MISSING")
	if err != nil || absent != nil {
		t.Errorf("an absent variable is nil without an error, got %+v, %v", absent, err)
	}
	found, err := client.GetVariable(ctx, TeamVariables, []string{"acme"}, "STRIPE_KEY")
	if err != nil || found == nil || !found.Secrecy.Secret || found.Audience != "runtime" {
		t.Errorf("a secret is read with its verdict and audience: %+v, %v", found, err)
	}
	_, err = client.GetVariable(ctx, TeamVariables, []string{"ghost"}, "REGION")
	if !IsNotFound(err) {
		t.Errorf("an unknown team is not found, got %v", err)
	}
}

func TestTheMarkIsSentAsTheOperatorWroteItAndNullLeavesTheDecisionToTheServer(t *testing.T) {
	yes, no := true, false
	for name, tc := range map[string]struct {
		mark *bool
		want string
	}{"unset": {nil, `null`}, "secret": {&yes, `true`}, "not secret": {&no, `false`}} {
		t.Run(name, func(t *testing.T) {
			var sent map[string]json.RawMessage
			client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
				raw, _ := io.ReadAll(r.Body)
				if err := json.Unmarshal(raw, &sent); err != nil {
					t.Error(err)
				}
				_, _ = w.Write([]byte(variableList))
			})

			_, err := client.SetVariable(context.Background(), TeamVariables, []string{"acme"}, "REGION", VariableRequest{Value: "eu-west", Secret: tc.mark, Audience: "build"})

			if err != nil {
				t.Fatal(err)
			}
			if string(sent["secret"]) != tc.want || string(sent["audience"]) != `"build"` || string(sent["value"]) != `"eu-west"` {
				t.Errorf("sent %s", sent)
			}
		})
	}
}

func TestAnAnswerWithoutTheVariableJustSetIsAnErrorAndNotASilentSuccess(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(variableList))
	})

	_, err := client.SetVariable(context.Background(), TeamVariables, []string{"acme"}, "OTHER", VariableRequest{Value: "x"})

	if err == nil {
		t.Error("the server did not answer the variable that was set")
	}
}

func TestRemovingAVariableReportsWhoStillRefersToIt(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"name":"REGION","applications":["shop.example.com","api.example.com"]}`))
	})

	out, err := client.DeleteVariable(context.Background(), TeamVariables, []string{"acme"}, "REGION")

	if err != nil {
		t.Fatal(err)
	}
	if len(out.Applications) != 2 || out.Applications[0] != "shop.example.com" {
		t.Errorf("applications %v", out.Applications)
	}
}
