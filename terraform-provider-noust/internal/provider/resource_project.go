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
	_ resource.Resource                = (*projectResource)(nil)
	_ resource.ResourceWithConfigure   = (*projectResource)(nil)
	_ resource.ResourceWithImportState = (*projectResource)(nil)
	_ resource.Resource                = (*environmentResource)(nil)
	_ resource.ResourceWithConfigure   = (*environmentResource)(nil)
	_ resource.ResourceWithImportState = (*environmentResource)(nil)
)

// NewProjectResource is the noust_project resource.
func NewProjectResource() resource.Resource { return &projectResource{} }

// NewEnvironmentResource is the noust_environment resource.
func NewEnvironmentResource() resource.Resource { return &environmentResource{} }

type projectResource struct{ data *providerData }

type projectModel struct {
	ID   types.String `tfsdk:"id"`
	Team types.String `tfsdk:"team"`
	Slug types.String `tfsdk:"slug"`
	Name types.String `tfsdk:"name"`
}

func (r *projectResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_project"
}

func (r *projectResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A project of a team: the group of applications, databases and backups an environment holds.",
		MarkdownDescription: "A project of a team. Creating one also creates its `production` environment. Deleting a " +
			"project is refused by the server while it still holds anything, and always for a team's `default` project.\n\n" +
			"Needs the permission `teams.manage` and sudo mode to delete.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The team's slug and the project's, joined with a slash.",
				MarkdownDescription: "The team's slug and the project's, joined with a slash: `team/project`.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"team": schema.StringAttribute{
				Required:            true,
				Description:         "The slug of the team the project belongs to. Changing it replaces the project.",
				MarkdownDescription: "The slug of the team the project belongs to. Changing it replaces the project.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"slug": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The project's slug. Made from the name when omitted. Changing it replaces the project.",
				MarkdownDescription: "The project's slug. Made from the name when omitted. Changing it replaces the project.",
				PlanModifiers:       []planmodifier.String{keepString(), stringplanmodifier.RequiresReplace()},
			},
			"name": schema.StringAttribute{
				Required:            true,
				Description:         "The project's display name.",
				MarkdownDescription: "The project's display name.",
			},
		},
	}
}

func (r *projectResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func projectState(team, slug, name string) *projectModel {
	return &projectModel{ID: types.StringValue(team + "/" + slug), Team: types.StringValue(team), Slug: types.StringValue(slug), Name: types.StringValue(name)}
}

func (r *projectResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan projectModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, err := r.data.client.CreateProject(ctx, plan.Team.ValueString(), plan.Name.ValueString(), stringOf(plan.Slug))
	if err != nil {
		addError(&resp.Diagnostics, "Cannot create the project", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, projectState(plan.Team.ValueString(), project.Slug, project.Name))...)
}

func (r *projectResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state projectModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, err := r.data.client.GetProject(ctx, state.Team.ValueString(), state.Slug.ValueString())
	if noust.IsNotFound(err) || (err == nil && project == nil) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the project", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, projectState(state.Team.ValueString(), project.Slug, project.Name))...)
}

func (r *projectResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan projectModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, err := r.data.client.RenameProject(ctx, plan.Team.ValueString(), plan.Slug.ValueString(), plan.Name.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot rename the project", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, projectState(plan.Team.ValueString(), project.Slug, project.Name))...)
}

func (r *projectResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state projectModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.data.client.DeleteProject(ctx, state.Team.ValueString(), state.Slug.ValueString()); err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot delete the project", err)
	}
}

func (r *projectResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, ok := splitID(req.ID, 2)
	if !ok {
		importIDError(resp, req.ID, "team/project")
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("team"), parts[0])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("slug"), parts[1])...)
}

type environmentResource struct{ data *providerData }

type environmentModel struct {
	ID      types.String `tfsdk:"id"`
	Team    types.String `tfsdk:"team"`
	Project types.String `tfsdk:"project"`
	Slug    types.String `tfsdk:"slug"`
	Name    types.String `tfsdk:"name"`
}

func (r *environmentResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_environment"
}

func (r *environmentResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "An environment of a project (staging, production): where an application runs and whose variables it reads.",
		MarkdownDescription: "An environment of a project, such as `staging`. Every project is made with a `production` " +
			"environment, so manage that one by importing it. Deleting an environment is refused by the server while " +
			"it still holds anything.\n\nNeeds the permission `teams.manage` and sudo mode to delete.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "team/project/environment, joined with slashes.",
				MarkdownDescription: "The team's, the project's and the environment's slugs, joined with slashes: `team/project/environment`.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"team": schema.StringAttribute{
				Required:            true,
				Description:         "The slug of the team. Changing it replaces the environment.",
				MarkdownDescription: "The slug of the team. Changing it replaces the environment.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"project": schema.StringAttribute{
				Required:            true,
				Description:         "The slug of the project. Changing it replaces the environment.",
				MarkdownDescription: "The slug of the project. Changing it replaces the environment.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"slug": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The environment's slug. Made from the name when omitted. Changing it replaces the environment.",
				MarkdownDescription: "The environment's slug. Made from the name when omitted. Changing it replaces the environment.",
				PlanModifiers:       []planmodifier.String{keepString(), stringplanmodifier.RequiresReplace()},
			},
			"name": schema.StringAttribute{
				Required:            true,
				Description:         "The environment's display name.",
				MarkdownDescription: "The environment's display name.",
			},
		},
	}
}

func (r *environmentResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func environmentState(team, project, slug, name string) *environmentModel {
	return &environmentModel{
		ID:      types.StringValue(team + "/" + project + "/" + slug),
		Team:    types.StringValue(team),
		Project: types.StringValue(project),
		Slug:    types.StringValue(slug),
		Name:    types.StringValue(name),
	}
}

func (r *environmentResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan environmentModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	created, err := r.data.client.CreateEnvironment(ctx, plan.Team.ValueString(), plan.Project.ValueString(), plan.Name.ValueString(), stringOf(plan.Slug))
	if err != nil {
		addError(&resp.Diagnostics, "Cannot create the environment", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, environmentState(plan.Team.ValueString(), plan.Project.ValueString(), created.Slug, created.Name))...)
}

func (r *environmentResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state environmentModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	found, err := r.data.client.GetEnvironment(ctx, state.Team.ValueString(), state.Project.ValueString(), state.Slug.ValueString())
	if noust.IsNotFound(err) || (err == nil && found == nil) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the environment", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, environmentState(state.Team.ValueString(), state.Project.ValueString(), found.Slug, found.Name))...)
}

func (r *environmentResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan environmentModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	renamed, err := r.data.client.RenameEnvironment(ctx, plan.Team.ValueString(), plan.Project.ValueString(), plan.Slug.ValueString(), plan.Name.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot rename the environment", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, environmentState(plan.Team.ValueString(), plan.Project.ValueString(), renamed.Slug, renamed.Name))...)
}

func (r *environmentResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state environmentModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.data.client.DeleteEnvironment(ctx, state.Team.ValueString(), state.Project.ValueString(), state.Slug.ValueString()); err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot delete the environment", err)
	}
}

func (r *environmentResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, ok := splitID(req.ID, 3)
	if !ok {
		importIDError(resp, req.ID, "team/project/environment")
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("team"), parts[0])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("project"), parts[1])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("slug"), parts[2])...)
}
