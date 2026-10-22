// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestEveryOperationIsUsedByAResourceAndEveryResourceOperationIsInTheTable(t *testing.T) {
	listed := map[string]bool{}
	for _, op := range AllOperations {
		listed[op.Name] = true
	}
	used := map[string]bool{}
	for resource, ops := range ResourceOperations {
		for _, op := range ops {
			used[op.Name] = true
			if !listed[op.Name] {
				t.Errorf("%s calls %s, which is not in AllOperations", resource, op.Name)
			}
		}
	}
	for _, op := range AllOperations {
		if !used[op.Name] {
			t.Errorf("%s is in the table and no resource calls it: remove it", op.Name)
		}
	}
}

func TestSudoIsReportedForTheResourcesThatAskForIt(t *testing.T) {
	if !NeedsOf("noust_application").Sudo || !NeedsOf("noust_registry_login").Sudo {
		t.Error("an application and a registry account ask for sudo mode")
	}
	if NeedsOf("data.noust_application").Sudo || NeedsOf("data.noust_servers").Sudo {
		t.Error("a data source never asks for sudo mode")
	}
}

// TestPermissionsJSONIsCurrent keeps permissions.json, which the live tests mint their tokens from and the
// documentation quotes, equal to what the operations table says. NOUST_UPDATE_PERMISSIONS=1 rewrites it.
func TestPermissionsJSONIsCurrent(t *testing.T) {
	path := filepath.Join("..", "..", "permissions.json")
	want, err := json.MarshalIndent(Report(), "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	want = append(want, '\n')
	if os.Getenv("NOUST_UPDATE_PERMISSIONS") != "" {
		if err := os.WriteFile(path, want, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%v: run the tests with NOUST_UPDATE_PERMISSIONS=1 to write it", err)
	}
	if string(got) != string(want) {
		t.Errorf("permissions.json is out of date: run the tests with NOUST_UPDATE_PERMISSIONS=1")
	}
}
