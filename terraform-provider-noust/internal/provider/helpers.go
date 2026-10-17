// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/boolplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

// dataOf reads the provider data a resource or data source was configured with. It is nil before the
// provider is configured (during validation), which the callers treat as "nothing to do yet".
func dataOf(value any, diags *diag.Diagnostics) *providerData {
	if value == nil {
		return nil
	}
	data, ok := value.(*providerData)
	if !ok {
		diags.AddError("Unexpected provider data", fmt.Sprintf("Expected *providerData, got %T. This is a bug in the provider.", value))
		return nil
	}
	return data
}

// addError turns a failed call into a diagnostic that says what the server said, word for word, and
// what to do about the kinds of refusal a user can act on.
func addError(diags *diag.Diagnostics, summary string, err error) {
	diags.AddError(summary, describe(err))
}

func addWarning(diags *diag.Diagnostics, summary string, err error) {
	diags.AddWarning(summary, describe(err))
}

// describe writes an error the way an operator reads it: the API's detail and hint, then the output of the
// tool that failed, verbatim.
func describe(err error) string {
	var elevation *noust.ElevationRequiredError
	var approval *noust.ApprovalRequiredError
	var failed *noust.JobFailedError
	var api *noust.APIError
	var b strings.Builder
	switch {
	case errors.As(err, &elevation):
		b.WriteString("The server asks for sudo mode for this operation, and this token cannot enter it.\n\n")
		b.WriteString("Issue the token with allow_elevated (\"allow_elevated\": true in POST /api/auth/tokens, or --allow-elevated in `noust token create`), from an account that holds the permissions the configuration needs. ")
		b.WriteString("It is refused under the ENS profile, where a person confirms in the console instead.\n\n")
		writeAPIError(&b, elevation.APIError)
	case errors.As(err, &approval):
		fmt.Fprintf(&b, "This change needs a second person's approval, and the server has made it request %s. The call did not run.\n\n", approval.ApprovalID)
		b.WriteString("Once the request is approved, apply again with approval = \"" + approval.ApprovalID + "\" in the provider block (or NOUST_APPROVAL). An approval allows that one call, once, within the time the server gives.\n\n")
		writeAPIError(&b, approval.APIError)
	case errors.As(err, &failed):
		fmt.Fprintf(&b, "The server ran the job and it %s.\n\n", failed.Job.State)
		if failed.Job.Error != "" {
			b.WriteString(failed.Job.Error)
		} else {
			b.WriteString("It left no error. Its log:\n")
			for _, line := range failed.Job.Logs {
				fmt.Fprintf(&b, "  [%s] %s\n", line.Level, line.Message)
			}
		}
	case errors.As(err, &api):
		writeAPIError(&b, api)
		if api.Status == 403 && api.Code == "permission_denied" {
			if need := permissionOf(api.Operation); need != "" {
				fmt.Fprintf(&b, "\n\nThe operation %s needs the permission %s: add it to the token.", api.Operation, need)
			}
		}
	default:
		b.WriteString(err.Error())
	}
	return strings.TrimSpace(b.String())
}

func writeAPIError(b *strings.Builder, api *noust.APIError) {
	fmt.Fprintf(b, "%s (HTTP %d", api.Operation, api.Status)
	if api.Code != "" {
		fmt.Fprintf(b, ", %s", api.Code)
	}
	b.WriteString(")")
	if api.Detail != "" {
		fmt.Fprintf(b, ": %s", api.Detail)
	}
	if api.Hint != "" {
		fmt.Fprintf(b, "\n\n%s", api.Hint)
	}
	if len(api.Fields) > 0 {
		b.WriteString("\n\nFields:")
		for name, why := range api.Fields {
			fmt.Fprintf(b, "\n  %s: %v", name, why)
		}
	}
	if api.Output != "" {
		fmt.Fprintf(b, "\n\nOutput from the server:\n%s", api.Output)
	}
}

func permissionOf(operation string) string {
	for _, op := range noust.AllOperations {
		if op.Name == operation {
			return op.Permission
		}
	}
	return ""
}

// stringOf is the value of an optional string, empty when it is null or unknown.
func stringOf(value types.String) string {
	if value.IsNull() || value.IsUnknown() {
		return ""
	}
	return value.ValueString()
}

// known reports whether a value is set to something: neither null nor unknown.
func known(value interface {
	IsNull() bool
	IsUnknown() bool
}) bool {
	return !value.IsNull() && !value.IsUnknown()
}

func stringFromPtr(value *string) types.String {
	if value == nil {
		return types.StringNull()
	}
	return types.StringValue(*value)
}

func int64FromPtr(value *int) types.Int64 {
	if value == nil {
		return types.Int64Null()
	}
	return types.Int64Value(int64(*value))
}

func intPtrOf(value types.Int64) *int {
	if !known(value) {
		return nil
	}
	v := int(value.ValueInt64())
	return &v
}

func floatFromPtr(value *float64) types.Float64 {
	if value == nil {
		return types.Float64Null()
	}
	return types.Float64Value(*value)
}

func floatPtrOf(value types.Float64) *float64 {
	if !known(value) {
		return nil
	}
	v := value.ValueFloat64()
	return &v
}

func boolOr(value types.Bool, fallback bool) bool {
	if !known(value) {
		return fallback
	}
	return value.ValueBool()
}

// stringsOf converts a list of strings, empty when null or unknown.
func stringsOf(ctx context.Context, value types.List, diags *diag.Diagnostics) []string {
	if !known(value) {
		return nil
	}
	var out []string
	diags.Append(value.ElementsAs(ctx, &out, false)...)
	return out
}

// stringMapOf converts a map of strings, empty when null or unknown.
func stringMapOf(ctx context.Context, value types.Map, diags *diag.Diagnostics) map[string]string {
	if !known(value) {
		return nil
	}
	var out map[string]string
	diags.Append(value.ElementsAs(ctx, &out, false)...)
	return out
}

// createOnlyString is the plan modifier of a setting the API accepts when the object is made and has no
// call to change afterwards: changing it replaces the object. A value that was never known (an object
// that was imported, whose state cannot tell it) is adopted from the configuration, not replaced.
func createOnlyString() planmodifier.String {
	return stringplanmodifier.RequiresReplaceIf(
		func(_ context.Context, req planmodifier.StringRequest, resp *stringplanmodifier.RequiresReplaceIfFuncResponse) {
			resp.RequiresReplace = !req.StateValue.IsNull()
		},
		"Changing it replaces the object: the API only takes it when the object is made.",
		"Changing it replaces the object: the API only takes it when the object is made.",
	)
}

func createOnlyBool() planmodifier.Bool {
	return boolplanmodifier.RequiresReplaceIf(
		func(_ context.Context, req planmodifier.BoolRequest, resp *boolplanmodifier.RequiresReplaceIfFuncResponse) {
			resp.RequiresReplace = !req.StateValue.IsNull()
		},
		"Changing it replaces the object: the API only takes it when the object is made.",
		"Changing it replaces the object: the API only takes it when the object is made.",
	)
}

// splitID splits an import id of the form a/b/c into exactly n parts.
func splitID(id string, n int) ([]string, bool) {
	parts := strings.Split(id, "/")
	if len(parts) != n {
		return nil, false
	}
	for _, part := range parts {
		if part == "" {
			return nil, false
		}
	}
	return parts, true
}

func importIDError(resp *resource.ImportStateResponse, got, want string) {
	resp.Diagnostics.AddAttributeError(path.Root("id"), "Not an import id",
		fmt.Sprintf("Expected an id of the form %s, got %q.", want, got))
}

// keepString keeps the value a computed attribute had when the configuration does not set it, null
// included. The framework's UseStateForUnknown leaves a null state value unknown, which would put every
// attribute the server leaves empty (a limit, a branch) in every plan as "known after apply".
func keepString() planmodifier.String { return keepStringModifier{} }

type keepStringModifier struct{}

func (keepStringModifier) Description(context.Context) string {
	return "Keeps the value the object has when the configuration does not set it."
}

func (m keepStringModifier) MarkdownDescription(ctx context.Context) string {
	return m.Description(ctx)
}

func (keepStringModifier) PlanModifyString(_ context.Context, req planmodifier.StringRequest, resp *planmodifier.StringResponse) {
	if req.State.Raw.IsNull() || !req.PlanValue.IsUnknown() || !req.ConfigValue.IsNull() {
		return
	}
	resp.PlanValue = req.StateValue
}

// keepInt64 is keepString for numbers.
func keepInt64() planmodifier.Int64 { return keepInt64Modifier{} }

type keepInt64Modifier struct{}

func (keepInt64Modifier) Description(context.Context) string {
	return "Keeps the value the object has when the configuration does not set it."
}

func (m keepInt64Modifier) MarkdownDescription(ctx context.Context) string { return m.Description(ctx) }

func (keepInt64Modifier) PlanModifyInt64(_ context.Context, req planmodifier.Int64Request, resp *planmodifier.Int64Response) {
	if req.State.Raw.IsNull() || !req.PlanValue.IsUnknown() || !req.ConfigValue.IsNull() {
		return
	}
	resp.PlanValue = req.StateValue
}
