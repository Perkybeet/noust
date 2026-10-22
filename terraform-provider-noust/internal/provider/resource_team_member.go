// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

var (
	_ resource.Resource                = (*teamMemberResource)(nil)
	_ resource.ResourceWithConfigure   = (*teamMemberResource)(nil)
	_ resource.ResourceWithImportState = (*teamMemberResource)(nil)
)

// NewTeamMemberResource is the noust_team_member resource.
func NewTeamMemberResource() resource.Resource { return &teamMemberResource{} }

type teamMemberResource struct{ data *providerData }

type teamMemberModel struct {
	ID       types.String `tfsdk:"id"`
	Team     types.String `tfsdk:"team"`
	Username types.String `tfsdk:"username"`
	Role     types.String `tfsdk:"role"`
}

func (r *teamMemberResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_team_member"
}

func (r *teamMemberResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "An account's membership of a team, with the role it holds there.",
		MarkdownDescription: "An account's membership of a team, with the role it holds there. The account must exist: a new " +
			"person is invited by a security officer, because making a credential is the security role's decision and " +
			"not a team's. The team role raises what the account's own role allows inside the team and never reaches the " +
			"server. The server refuses to demote or remove a team's last owner.\n\n" +
			"Needs the permission `teams.manage` and sudo mode.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The team's slug and the account's name, joined with a slash.",
				MarkdownDescription: "The team's slug and the account's name, joined with a slash: `team/username`.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"team": schema.StringAttribute{
				Required:            true,
				Description:         "The slug of the team. Changing it replaces the membership.",
				MarkdownDescription: "The slug of the team. Changing it replaces the membership.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"username": schema.StringAttribute{
				Required:            true,
				Description:         "The name of the account. Changing it replaces the membership.",
				MarkdownDescription: "The name of the account. Changing it replaces the membership.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"role": schema.StringAttribute{
				Required:            true,
				Description:         "The role in the team: viewer, operator, admin or owner. Changed in place.",
				MarkdownDescription: "The role in the team: `viewer`, `operator`, `admin` or `owner`. Changed in place.",
				Validators:          []validator.String{stringvalidator.OneOf("viewer", "operator", "admin", "owner")},
			},
		},
	}
}

func (r *teamMemberResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func teamMemberState(team string, member *noust.Member) *teamMemberModel {
	return &teamMemberModel{
		ID:       types.StringValue(team + "/" + member.Username),
		Team:     types.StringValue(team),
		Username: types.StringValue(member.Username),
		Role:     types.StringValue(member.Role),
	}
}

func (r *teamMemberResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan teamMemberModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	member, err := r.data.client.AddTeamMember(ctx, plan.Team.ValueString(), plan.Username.ValueString(), plan.Role.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot add the member to the team", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, teamMemberState(plan.Team.ValueString(), member))...)
}

func (r *teamMemberResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state teamMemberModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	member, err := r.data.client.GetTeamMember(ctx, state.Team.ValueString(), state.Username.ValueString())
	if noust.IsNotFound(err) || (err == nil && member == nil) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the team's members", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, teamMemberState(state.Team.ValueString(), member))...)
}

func (r *teamMemberResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan teamMemberModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	member, err := r.data.client.SetTeamMemberRole(ctx, plan.Team.ValueString(), plan.Username.ValueString(), plan.Role.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot change the member's role", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, teamMemberState(plan.Team.ValueString(), member))...)
}

func (r *teamMemberResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state teamMemberModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.data.client.RemoveTeamMember(ctx, state.Team.ValueString(), state.Username.ValueString()); err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot remove the member from the team", err)
	}
}

func (r *teamMemberResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, ok := splitID(req.ID, 2)
	if !ok {
		importIDError(resp, req.ID, "team/username")
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("team"), parts[0])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("username"), parts[1])...)
}
