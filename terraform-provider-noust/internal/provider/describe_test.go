// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"errors"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

func TestTheServersWordsAreShownAsTheyCame(t *testing.T) {
	err := &noust.APIError{
		Operation: "UpdateHealth",
		Status:    400,
		Code:      "deployment_error",
		Detail:    "The site was refused",
		Hint:      "Fix the server name",
		Fields:    map[string]any{"path": "must start with /"},
		Output:    "nginx: [emerg] unknown directive \"servr_name\" in /etc/nginx/sites-enabled/x:4",
	}

	text := describe(err)

	for _, want := range []string{
		"UpdateHealth (HTTP 400, deployment_error): The site was refused",
		"Fix the server name",
		"path: must start with /",
		"Output from the server:\nnginx: [emerg] unknown directive \"servr_name\" in /etc/nginx/sites-enabled/x:4",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in:\n%s", want, text)
		}
	}
}

func TestAMissingPermissionNamesItAndSudoModeSaysHowToGetIt(t *testing.T) {
	denied := describe(&noust.APIError{Operation: "CreateTeam", Status: 403, Code: "permission_denied", Detail: "This needs the 'teams.manage' permission"})
	if !strings.Contains(denied, "The operation CreateTeam needs the permission teams.manage: add it to the token.") {
		t.Errorf("%s", denied)
	}

	elevated := describe(&noust.ElevationRequiredError{APIError: &noust.APIError{Operation: "DeleteApp", Status: 403, Code: "elevation_required", Detail: "Confirm it's you to continue"}})
	for _, want := range []string{"sudo mode", "allow_elevated", "ENS profile", "DeleteApp (HTTP 403, elevation_required)"} {
		if !strings.Contains(elevated, want) {
			t.Errorf("missing %q in:\n%s", want, elevated)
		}
	}
}

func TestAnApprovalRequestIsReportedWithWhatToDoNext(t *testing.T) {
	text := describe(&noust.ApprovalRequiredError{
		APIError:   &noust.APIError{Operation: "CreateBackupSchedule", Status: 202, Code: "approval_required", Detail: "Request 17 is waiting"},
		ApprovalID: "17",
	})

	for _, want := range []string{"request 17", "The call did not run", `approval = "17"`, "NOUST_APPROVAL"} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in:\n%s", want, text)
		}
	}
}

func TestAFailedJobIsTheServersErrorAndNothingElse(t *testing.T) {
	text := describe(&noust.JobFailedError{Job: &noust.Job{
		ID: "ab12", Name: "Deploy shop", State: "failed",
		Error: "Release 20261017-1 did not pass its health check\n  Details: The application did not answer the health check.\n\nLast lines of the journal of shop:\nOct 17 04:43:15 acme shop[1]: Error: listen EADDRINUSE :::3000",
	}})

	if !strings.Contains(text, "did not pass its health check\n  Details: The application did not answer the health check.") ||
		!strings.Contains(text, "EADDRINUSE :::3000") {
		t.Errorf("the failure was changed:\n%s", text)
	}

	silent := describe(&noust.JobFailedError{Job: &noust.Job{ID: "cd34", State: "cancelled", Logs: []noust.JobLogLine{{Level: "error", Message: "stopped by an operator"}}}})
	if !strings.Contains(silent, "[error] stopped by an operator") {
		t.Errorf("a job with no error shows its log:\n%s", silent)
	}
}

func TestAnythingElseIsShownAsItIs(t *testing.T) {
	if got := describe(errors.New("dial tcp 127.0.0.1:1: connect: connection refused")); got != "dial tcp 127.0.0.1:1: connect: connection refused" {
		t.Errorf("%q", got)
	}
}

func TestEnvironmentDeltaSetsWhatIsNewAndUnsetsWhatLeft(t *testing.T) {
	set, unset := envDelta(
		map[string]string{"A": "1", "B": "2-new", "C": "3"},
		map[string]string{"B": "2", "C": "3", "D": "4", "E": "5"},
	)

	if len(set) != 2 || set["A"] != "1" || set["B"] != "2-new" {
		t.Errorf("set %v", set)
	}
	if strings.Join(unset, ",") != "D,E" {
		t.Errorf("unset %v", unset)
	}
}

func TestALimitOfZeroIsNoLimitAndTheServersNullReadsBackAsTheConfigurationWroteIt(t *testing.T) {
	if got := limitRequest(types.Int64Value(0)); got != nil {
		t.Errorf("0 was sent as %d", *got)
	}
	if got := limitRequest(types.Int64Value(256)); got == nil || *got != 256 {
		t.Errorf("256 was sent as %v", got)
	}
	if got := limitRequest(types.Int64Unknown()); got != nil {
		t.Error("an unknown limit was sent")
	}

	if got := limitFrom(nil, types.Int64Value(0)); got.ValueInt64() != 0 || got.IsNull() {
		t.Errorf("a 0 in the configuration became %v", got)
	}
	if got := limitFrom(nil, types.Int64Null()); !got.IsNull() {
		t.Errorf("a limit nobody set became %v", got)
	}
	n := 512
	if got := limitFrom(&n, types.Int64Null()); got.ValueInt64() != 512 {
		t.Errorf("a limit the server has became %v", got)
	}
}

func TestACalendarExpressionStaysWhatTheConfigurationWrote(t *testing.T) {
	custom := &noust.BackupSchedule{Schedule: "custom", OnCalendar: "*-*-* 03:30:00"}

	written := &backupScheduleModel{Schedule: types.StringValue("*-*-* 03:30:00"), OnCalendar: types.StringUnknown()}
	if got := scheduleName(custom, written); got != "*-*-* 03:30:00" {
		t.Errorf("just written: %q", got)
	}
	read := &backupScheduleModel{Schedule: types.StringValue("*-*-* 03:30:00"), OnCalendar: types.StringValue("*-*-* 03:30:00")}
	if got := scheduleName(custom, read); got != "*-*-* 03:30:00" {
		t.Errorf("unchanged: %q", got)
	}
	// The timer was changed on the server: what it runs on now is what the state must say, so the plan sees it.
	drifted := &backupScheduleModel{Schedule: types.StringValue("*-*-* 01:00:00"), OnCalendar: types.StringValue("*-*-* 01:00:00")}
	if got := scheduleName(custom, drifted); got != "*-*-* 03:30:00" {
		t.Errorf("changed outside Terraform: %q", got)
	}
	named := &noust.BackupSchedule{Schedule: "weekly", OnCalendar: "Mon *-*-* 02:00:00"}
	if got := scheduleName(named, read); got != "weekly" {
		t.Errorf("a named schedule: %q", got)
	}
}
