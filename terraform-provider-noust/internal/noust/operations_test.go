// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// openAPI finds the API's schema: NOUST_OPENAPI, or panel/openapi.json in the repository this directory
// lives in. A copy of the provider outside the repository (the registry's) skips these tests; the
// repository's CI always has the schema.
func openAPI(t *testing.T) map[string]any {
	t.Helper()
	path := os.Getenv("NOUST_OPENAPI")
	if path == "" {
		dir, err := os.Getwd()
		if err != nil {
			t.Fatal(err)
		}
		for ; dir != filepath.Dir(dir); dir = filepath.Dir(dir) {
			candidate := filepath.Join(dir, "panel", "openapi.json")
			if _, err := os.Stat(candidate); err == nil {
				path = candidate
				break
			}
		}
	}
	if path == "" {
		if os.Getenv("NOUST_REQUIRE_OPENAPI") != "" {
			t.Fatal("NOUST_REQUIRE_OPENAPI is set and panel/openapi.json was not found")
		}
		t.Skip("panel/openapi.json not found (set NOUST_OPENAPI): the schema is the repository's")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(raw, &document); err != nil {
		t.Fatal(err)
	}
	return document
}

func dig(value any, keys ...string) any {
	for _, key := range keys {
		object, ok := value.(map[string]any)
		if !ok {
			return nil
		}
		value = object[key]
	}
	return value
}

func operationOf(t *testing.T, document map[string]any, op Op) map[string]any {
	t.Helper()
	item, ok := dig(document, "paths", op.Path).(map[string]any)
	if !ok {
		t.Fatalf("%s: %s is not a route of the API", op.Name, op.Path)
	}
	operation, ok := item[strings.ToLower(op.Method)].(map[string]any)
	if !ok {
		t.Fatalf("%s: %s %s is not an operation of the API", op.Name, op.Method, op.Path)
	}
	return operation
}

func TestEveryOperationIsAnOperationOfTheAPI(t *testing.T) {
	document := openAPI(t)
	seen := map[string]bool{}
	for _, op := range AllOperations {
		key := op.Method + " " + op.Path
		if seen[key] {
			t.Errorf("%s is listed twice", key)
		}
		seen[key] = true
		operation := operationOf(t, document, op)

		permission, _ := operation["x-noust-permission"].(string)
		if permission != op.Permission {
			t.Errorf("%s: the API asks for the permission %q and the table says %q", op.Name, permission, op.Permission)
		}
		sudo, _ := operation["x-noust-requires-elevation"].(bool)
		if sudo != op.Sudo {
			t.Errorf("%s: the API's sudo mode is %v and the table says %v", op.Name, sudo, op.Sudo)
		}
		if deprecated, _ := operation["deprecated"].(bool); deprecated {
			t.Errorf("%s is deprecated in the API: move the provider off it", op.Name)
		}
		placeholders := strings.Count(op.Path, "{")
		parameters := 0
		if list, ok := operation["parameters"].([]any); ok {
			for _, entry := range list {
				if dig(entry, "in") == "path" {
					parameters++
				}
			}
		}
		if placeholders != parameters {
			t.Errorf("%s: the route has %d placeholders and the API declares %d path parameters", op.Name, placeholders, parameters)
		}
	}
}

// bodies pairs an operation with the Go type it sends and the Go type it decodes, so that a field the
// provider reads or writes that the API does not know fails here.
var bodies = []struct {
	op       Op
	request  any
	response any
}{
	{GetJob, nil, Job{}},
	{ListApps, nil, AppList{}},
	{GetApp, nil, App{}},
	{CreateApp, CreateAppRequest{}, JobAccepted{}},
	{DeleteApp, nil, JobAccepted{}},
	{GetAppEnv, nil, AppEnv{}},
	{MergeAppEnv, MergeEnvRequest{}, MergeEnvResult{}},
	{UpdateBranch, struct {
		Branch *string `json:"branch"`
	}{}, nil},
	{GetHealth, nil, Health{}},
	{UpdateHealth, Health{}, nil},
	{UpdateLimits, struct {
		Limits
		Restart bool `json:"restart"`
	}{}, nil},
	{MoveApp, struct {
		Team        string `json:"team"`
		Project     string `json:"project"`
		Environment string `json:"environment"`
	}{}, nil},
	{ListDomains, nil, AppDomains{}},
	{AddDomain, AddDomainRequest{}, DomainChange{}},
	{SetDomainPort, struct {
		Port *int `json:"port"`
	}{}, DomainChange{}},
	{DeleteDomain, nil, DomainChange{}},
	{CreateDatabase, CreateDatabaseRequest{}, Database{}},
	{GetDatabase, nil, Database{}},
	{DropDatabase, nil, JobAccepted{}},
	{ListBackupSchedules, nil, backupScheduleList{}},
	{CreateBackupSchedule, BackupScheduleRequest{}, scheduleAction{}},
	{UpdateBackupSchedule, BackupScheduleRequest{}, scheduleAction{}},
	{CreateTeam, nameRequest{}, Team{}},
	{GetTeam, nil, TeamDetail{}},
	{RenameTeam, renameRequest{}, Team{}},
	{AddTeamMember, addMemberRequest{}, Member{}},
	{SetTeamMemberRole, setRoleRequest{}, Member{}},
	{CreateProject, nameRequest{}, Project{}},
	{RenameProject, renameRequest{}, Project{}},
	{CreateEnvironment, nameRequest{}, Environment{}},
	{RenameEnvironment, renameRequest{}, Environment{}},
	{ListServerVariables, nil, VariableList{}},
	{SetServerVariable, VariableRequest{}, VariableList{}},
	{DeleteServerVariable, nil, DeletedVariable{}},
	{ListTeamVariables, nil, VariableList{}},
	{SetTeamVariable, VariableRequest{}, VariableList{}},
	{DeleteTeamVariable, nil, DeletedVariable{}},
	{ListProjectVariables, nil, VariableList{}},
	{SetProjectVariable, VariableRequest{}, VariableList{}},
	{DeleteProjectVariable, nil, DeletedVariable{}},
	{ListEnvironmentVariables, nil, VariableList{}},
	{SetEnvironmentVariable, VariableRequest{}, VariableList{}},
	{DeleteEnvironmentVariable, nil, DeletedVariable{}},
	{GetDeployKey, nil, AppDeployKey{}},
	{CreateRegistryLogin, RegistryLoginRequest{}, RegistryLogin{}},
	{GetRegistryLogin, nil, RegistryLogin{}},
	{ChangeRegistryLogin, RegistryLoginChange{}, RegistryLogin{}},
}

// jsonFields lists the JSON names a Go struct reads or writes, embedded structs included.
func jsonFields(t reflect.Type) []string {
	var names []string
	for i := 0; i < t.NumField(); i++ {
		field := t.Field(i)
		tag := field.Tag.Get("json")
		if field.Anonymous && tag == "" {
			names = append(names, jsonFields(field.Type)...)
			continue
		}
		name := strings.Split(tag, ",")[0]
		if name == "" || name == "-" {
			continue
		}
		names = append(names, name)
	}
	return names
}

// properties returns the property names of a schema, following $ref and every member of anyOf, oneOf
// and allOf.
func properties(document map[string]any, schema any) map[string]bool {
	names := map[string]bool{}
	var walk func(any, int)
	walk = func(node any, depth int) {
		object, ok := node.(map[string]any)
		if !ok || depth > 8 {
			return
		}
		if ref, ok := object["$ref"].(string); ok {
			walk(dig(document, "components", "schemas", strings.TrimPrefix(ref, "#/components/schemas/")), depth+1)
			return
		}
		if declared, ok := object["properties"].(map[string]any); ok {
			for key := range declared {
				names[key] = true
			}
		}
		for _, combine := range []string{"anyOf", "oneOf", "allOf"} {
			if members, ok := object[combine].([]any); ok {
				for _, member := range members {
					walk(member, depth+1)
				}
			}
		}
	}
	walk(schema, 0)
	return names
}

func TestTheGoTypesAreTheSchemasOfTheAPI(t *testing.T) {
	document := openAPI(t)
	for _, entry := range bodies {
		operation := operationOf(t, document, entry.op)
		if entry.request != nil {
			schema := dig(operation, "requestBody", "content", "application/json", "schema")
			allowed := properties(document, schema)
			for _, name := range jsonFields(reflect.TypeOf(entry.request)) {
				if !allowed[name] {
					t.Errorf("%s sends %q and the API's request has no such field", entry.op.Name, name)
				}
			}
		}
		if entry.response != nil {
			var schema any
			for _, code := range []string{"200", "201", "202"} {
				if found := dig(operation, "responses", code, "content", "application/json", "schema"); found != nil {
					schema = found
					break
				}
			}
			allowed := properties(document, schema)
			var missing []string
			for _, name := range jsonFields(reflect.TypeOf(entry.response)) {
				if !allowed[name] {
					missing = append(missing, name)
				}
			}
			sort.Strings(missing)
			if len(missing) > 0 {
				t.Errorf("%s reads %v and the API's answer has no such field", entry.op.Name, missing)
			}
		}
	}
}
