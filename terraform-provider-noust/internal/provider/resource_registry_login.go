// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/diag"
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
	_ resource.Resource                = (*registryLoginResource)(nil)
	_ resource.ResourceWithConfigure   = (*registryLoginResource)(nil)
	_ resource.ResourceWithImportState = (*registryLoginResource)(nil)
)

// NewRegistryLoginResource is the noust_registry_login resource.
func NewRegistryLoginResource() resource.Resource { return &registryLoginResource{} }

type registryLoginResource struct{ data *providerData }

type registryLoginModel struct {
	ID       types.String `tfsdk:"id"`
	Name     types.String `tfsdk:"name"`
	Kind     types.String `tfsdk:"kind"`
	Server   types.String `tfsdk:"server"`
	Username types.String `tfsdk:"username"`
	Password types.String `tfsdk:"password"`
	Apps     types.List   `tfsdk:"apps"`
}

func (r *registryLoginResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_registry_login"
}

func (r *registryLoginResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "An account on a container registry that Noust pulls private images with.",
		MarkdownDescription: "An account on a container registry that Noust pulls private images with. The password goes " +
			"to the server's secret store and is not returned by any call, so Terraform keeps the one in your " +
			"configuration (marked sensitive) and sends it again when it changes.\n\n" +
			"Needs the permission `apps.manage` and sudo mode, because a credential travels in.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The account's name.",
				MarkdownDescription: "The account's name.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"name": schema.StringAttribute{
				Required:            true,
				Description:         "The account's name, which applications refer to it by. Changing it replaces the account.",
				MarkdownDescription: "The account's name, which applications refer to it by. Changing it replaces the account.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"kind": schema.StringAttribute{
				Required:            true,
				Description:         "The registry: dockerhub, ghcr, gitlab, ecr, acr, gar or custom.",
				MarkdownDescription: "The registry: `dockerhub`, `ghcr`, `gitlab`, `ecr`, `acr`, `gar` or `custom`.",
				Validators: []validator.String{
					stringvalidator.OneOf("dockerhub", "ghcr", "gitlab", "ecr", "acr", "gar", "custom"),
				},
			},
			"server": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The registry host. The kind's own when omitted; required for custom, ecr, acr and gar.",
				MarkdownDescription: "The registry host. The kind's own when omitted; required for `custom`, `ecr`, `acr` and `gar`.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"username": schema.StringAttribute{
				Required:            true,
				Description:         "The account. For ECR, the AWS access key id.",
				MarkdownDescription: "The account. For ECR, the AWS access key id.",
			},
			"password": schema.StringAttribute{
				Required:            true,
				Sensitive:           true,
				Description:         "The registry's password or token. For ECR, the AWS secret access key.",
				MarkdownDescription: "The registry's password or token. For ECR, the AWS secret access key. It is in the state, marked sensitive: keep the state private.",
			},
			"apps": schema.ListAttribute{
				Computed:            true,
				ElementType:         types.StringType,
				Description:         "The applications that use the account.",
				MarkdownDescription: "The applications that use the account.",
			},
		},
	}
}

func (r *registryLoginResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

// registryLoginFrom fills the model from the API's account, keeping the password the configuration holds:
// no call returns it.
func registryLoginFrom(ctx context.Context, login *noust.RegistryLogin, password types.String) (registryLoginModel, diag.Diagnostics) {
	apps := login.Apps
	if apps == nil {
		apps = []string{}
	}
	list, diags := types.ListValueFrom(ctx, types.StringType, apps)
	return registryLoginModel{
		ID:       types.StringValue(login.Name),
		Name:     types.StringValue(login.Name),
		Kind:     types.StringValue(login.Kind),
		Server:   types.StringValue(login.Server),
		Username: types.StringValue(login.Username),
		Password: password,
		Apps:     list,
	}, diags
}

func (r *registryLoginResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan registryLoginModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	login, err := r.data.client.CreateRegistryLogin(ctx, noust.RegistryLoginRequest{
		Name:     plan.Name.ValueString(),
		Kind:     plan.Kind.ValueString(),
		Username: plan.Username.ValueString(),
		Password: plan.Password.ValueString(),
		Server:   stringOf(plan.Server),
	})
	if err != nil {
		addError(&resp.Diagnostics, "Cannot add the registry account", err)
		return
	}
	model, diags := registryLoginFrom(ctx, login, plan.Password)
	resp.Diagnostics.Append(diags...)
	resp.Diagnostics.Append(resp.State.Set(ctx, &model)...)
}

func (r *registryLoginResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state registryLoginModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	login, err := r.data.client.GetRegistryLogin(ctx, state.Name.ValueString())
	if noust.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the registry account", err)
		return
	}
	model, diags := registryLoginFrom(ctx, login, state.Password)
	resp.Diagnostics.Append(diags...)
	resp.Diagnostics.Append(resp.State.Set(ctx, &model)...)
}

func (r *registryLoginResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan, state registryLoginModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	change := noust.RegistryLoginChange{}
	if plan.Kind.ValueString() != state.Kind.ValueString() {
		kind := plan.Kind.ValueString()
		change.Kind = &kind
	}
	if known(plan.Server) && plan.Server.ValueString() != state.Server.ValueString() {
		server := plan.Server.ValueString()
		change.Server = &server
	}
	if plan.Username.ValueString() != state.Username.ValueString() {
		username := plan.Username.ValueString()
		change.Username = &username
	}
	if plan.Password.ValueString() != state.Password.ValueString() {
		password := plan.Password.ValueString()
		change.Password = &password
	}
	login, err := r.data.client.ChangeRegistryLogin(ctx, plan.Name.ValueString(), change)
	if err != nil {
		addError(&resp.Diagnostics, "Cannot change the registry account", err)
		return
	}
	model, diags := registryLoginFrom(ctx, login, plan.Password)
	resp.Diagnostics.Append(diags...)
	resp.Diagnostics.Append(resp.State.Set(ctx, &model)...)
}

func (r *registryLoginResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state registryLoginModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.data.client.DeleteRegistryLogin(ctx, state.Name.ValueString()); err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot remove the registry account", err)
	}
}

func (r *registryLoginResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("name"), req.ID)...)
}
