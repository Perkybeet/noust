// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

var (
	_ resource.Resource                = (*teamResource)(nil)
	_ resource.ResourceWithConfigure   = (*teamResource)(nil)
	_ resource.ResourceWithImportState = (*teamResource)(nil)
)

// NewTeamResource is the noust_team resource.
func NewTeamResource() resource.Resource { return &teamResource{} }

type teamResource struct{ data *providerData }

type teamModel struct {
	ID   types.String `tfsdk:"id"`
	Slug types.String `tfsdk:"slug"`
	Name types.String `tfsdk:"name"`
}

func (r *teamResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_team"
}

func (r *teamResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A team: the unit that owns projects, environments and, through them, applications, databases and backup schedules.",
		MarkdownDescription: "A team: the unit that owns projects, environments and, through them, applications, " +
			"databases and backup schedules. Creating one also creates its `default` project with a `production` " +
			"environment. Deleting a team is refused by the server while it still holds anything.\n\n" +
			"Needs the permission `teams.manage` and sudo mode to delete.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The team's slug.",
				PlanModifiers:       []planmodifier.String{keepString()},
				MarkdownDescription: "The team's slug.",
			},
			"slug": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The team's slug, which commands and links name it by. Made from the name when omitted. Changing it replaces the team.",
				MarkdownDescription: "The team's slug, which commands and links name it by. Made from the name when omitted. Changing it replaces the team.",
				PlanModifiers: []planmodifier.String{
					keepString(),
					stringplanmodifier.RequiresReplace(),
				},
			},
			"name": schema.StringAttribute{
				Required:            true,
				Description:         "The team's display name.",
				MarkdownDescription: "The team's display name.",
			},
		},
	}
}

func (r *teamResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func (r *teamResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan teamModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	team, err := r.data.client.CreateTeam(ctx, plan.Name.ValueString(), stringOf(plan.Slug))
	if err != nil {
		addError(&resp.Diagnostics, "Cannot create the team", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, teamState(team.Slug, team.Name))...)
}

func teamState(slug, name string) *teamModel {
	return &teamModel{ID: types.StringValue(slug), Slug: types.StringValue(slug), Name: types.StringValue(name)}
}

func (r *teamResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state teamModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	team, err := r.data.client.GetTeam(ctx, state.Slug.ValueString())
	if noust.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the team", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, teamState(team.Slug, team.Name))...)
}

func (r *teamResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan teamModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	team, err := r.data.client.RenameTeam(ctx, plan.Slug.ValueString(), plan.Name.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot rename the team", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, teamState(team.Slug, team.Name))...)
}

func (r *teamResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state teamModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.data.client.DeleteTeam(ctx, state.Slug.ValueString()); err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot delete the team", err)
	}
}

func (r *teamResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("slug"), req.ID)...)
}
