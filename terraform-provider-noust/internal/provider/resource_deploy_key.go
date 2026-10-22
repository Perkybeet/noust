// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework-validators/resourcevalidator"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

var (
	_ resource.Resource                     = (*deployKeyResource)(nil)
	_ resource.ResourceWithConfigure        = (*deployKeyResource)(nil)
	_ resource.ResourceWithImportState      = (*deployKeyResource)(nil)
	_ resource.ResourceWithConfigValidators = (*deployKeyResource)(nil)
)

// NewDeployKeyResource is the noust_deploy_key resource.
func NewDeployKeyResource() resource.Resource { return &deployKeyResource{} }

type deployKeyResource struct{ data *providerData }

type deployKeyModel struct {
	ID          types.String `tfsdk:"id"`
	App         types.String `tfsdk:"app"`
	Name        types.String `tfsdk:"name"`
	ExistingKey types.String `tfsdk:"existing_key"`
	PrivateKey  types.String `tfsdk:"private_key"`
	PublicKey   types.String `tfsdk:"public_key"`
	Fingerprint types.String `tfsdk:"fingerprint"`
	KeyType     types.String `tfsdk:"key_type"`
	Origin      types.String `tfsdk:"origin"`
}

func (r *deployKeyResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_deploy_key"
}

func (r *deployKeyResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{keepString(), stringplanmodifier.RequiresReplace()}
	resp.Schema = schema.Schema{
		Description: "The SSH key an application's git repository is reached with. Noust makes an ed25519 key and the public half is what to add at the code host; the private half never leaves the server.",
		MarkdownDescription: "The SSH key an application's git repository is reached with. By default Noust makes an " +
			"ed25519 key for the application, and `public_key` is what to add at the code host as a read-only deploy " +
			"key; the private half stays on the server and is never returned. Give `private_key` to use a key you " +
			"already have, or `existing_key` to assign one of the server's other keys.\n\n" +
			"The application must be deployed from an SSH git address. Needs the permission `apps.manage`; " +
			"importing a key and removing one need sudo mode. Removing it from Terraform takes the repository off " +
			"the key and deletes the key when nothing else uses it; the code host still trusts the public key " +
			"until you remove it there.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The application's domain.",
				MarkdownDescription: "The application's domain.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"app": schema.StringAttribute{
				Required:            true,
				Description:         "The domain of the application. Changing it replaces the key.",
				MarkdownDescription: "The domain of the application. Changing it replaces the key.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"name": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The name of the new key. Made by the server when omitted. Changing it replaces the key.",
				MarkdownDescription: "The name of the new key. Made by the server when omitted. Changing it replaces the key.",
				PlanModifiers:       replace,
			},
			"existing_key": schema.StringAttribute{
				Optional:            true,
				Description:         "The name of a key the server already has, to assign to the application instead of making one. Changing it replaces the assignment.",
				MarkdownDescription: "The name of a key the server already has (`GET /api/deploy-keys`), to assign to the application instead of making one. Changing it replaces the assignment.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"private_key": schema.StringAttribute{
				Optional:            true,
				Sensitive:           true,
				Description:         "The whole private key file of a key you already have, to import as the application's key. A key with a passphrase is refused. Changing it replaces the key.",
				MarkdownDescription: "The whole private key file of a key you already have, to import as the application's key. A key with a passphrase is refused. It is kept in the state, marked sensitive. Changing it replaces the key.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"public_key": schema.StringAttribute{
				Computed:            true,
				Description:         "The public half, to add at the code host.",
				MarkdownDescription: "The public half, to add at the code host.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"fingerprint": schema.StringAttribute{
				Computed:            true,
				Description:         "The key's fingerprint.",
				MarkdownDescription: "The key's fingerprint.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"key_type": schema.StringAttribute{
				Computed:            true,
				Description:         "The type of the key, such as ed25519.",
				MarkdownDescription: "The type of the key, such as `ed25519`.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"origin": schema.StringAttribute{
				Computed:            true,
				Description:         "How the server came by the key: generated or imported.",
				MarkdownDescription: "How the server came by the key: generated or imported.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
		},
	}
}

func (r *deployKeyResource) ConfigValidators(context.Context) []resource.ConfigValidator {
	return []resource.ConfigValidator{
		resourcevalidator.Conflicting(path.MatchRoot("existing_key"), path.MatchRoot("private_key")),
		resourcevalidator.Conflicting(path.MatchRoot("existing_key"), path.MatchRoot("name")),
	}
}

func (r *deployKeyResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

// deployKeyInto copies what the API says about the key into the model; what Terraform alone holds (the
// private key, the name of an assigned key) stays as it was.
func deployKeyInto(model *deployKeyModel, app string, answer *noust.AppDeployKey) bool {
	if answer.Key == nil {
		return false
	}
	model.ID = types.StringValue(app)
	model.App = types.StringValue(app)
	model.Name = types.StringValue(answer.Key.Name)
	model.PublicKey = types.StringValue(answer.Key.PublicKey)
	model.Fingerprint = types.StringValue(answer.Key.Fingerprint)
	model.KeyType = types.StringValue(answer.Key.KeyType)
	model.Origin = types.StringValue(answer.Key.Origin)
	return true
}

func (r *deployKeyResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan deployKeyModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	app := plan.App.ValueString()
	var (
		answer *noust.AppDeployKey
		err    error
	)
	if known(plan.PrivateKey) {
		answer, err = r.data.client.ImportDeployKey(ctx, app, stringOf(plan.Name), plan.PrivateKey.ValueString())
	} else {
		answer, err = r.data.client.CreateDeployKey(ctx, app, stringOf(plan.Name), stringOf(plan.ExistingKey))
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot give the application a deploy key", err)
		return
	}
	if !deployKeyInto(&plan, app, answer) {
		resp.Diagnostics.AddError("The server made no key", "The application has no deploy key after the call; check the server's answer with GET /api/apps/"+app+"/deploy-key.")
		return
	}
	for _, warning := range answer.Warnings {
		resp.Diagnostics.AddWarning("The server warns about the deploy key", warning)
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *deployKeyResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state deployKeyModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	app := state.App.ValueString()
	answer, err := r.data.client.GetDeployKey(ctx, app)
	if noust.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the deploy key", err)
		return
	}
	if !deployKeyInto(&state, app, answer) {
		resp.State.RemoveResource(ctx)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &state)...)
}

// Update has nothing to change on the server: every attribute that can differ replaces the key.
func (r *deployKeyResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan deployKeyModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *deployKeyResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state deployKeyModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.data.client.DeleteDeployKey(ctx, state.App.ValueString()); err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot take the application off its deploy key", err)
	}
}

func (r *deployKeyResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("app"), req.ID)...)
}
