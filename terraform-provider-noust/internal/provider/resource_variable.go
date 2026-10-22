// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"
	"fmt"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

// A shared variable lives in one of four scopes (the server, a team, a project, an environment) that the
// API spells with four sets of routes and one object. The four resources below are one implementation
// told which scope it serves, so a rule about variables is written once.
var (
	_ resource.Resource                = (*variableResource)(nil)
	_ resource.ResourceWithConfigure   = (*variableResource)(nil)
	_ resource.ResourceWithImportState = (*variableResource)(nil)
)

// variableKind says how one of the four resources differs from the others.
type variableKind struct {
	// typeName is the resource's name after the provider's: team_variable.
	typeName string
	scope    noust.VariableScope
	// title is what the descriptions call the resource: "A team's shared variable".
	title string
	// owners describes the attributes that locate the owner, in the order of scope.Owner.
	owners []string
	// importForm is the shape of the import id.
	importForm string
	// needs is the permission sentence of the description.
	needs string
}

// NewTeamVariableResource is the noust_team_variable resource.
func NewTeamVariableResource() resource.Resource {
	return &variableResource{kind: variableKind{
		typeName: "team_variable", scope: noust.TeamVariables, title: "A team's shared variable",
		owners:     []string{"The slug of the team."},
		importForm: "team/NAME",
		needs:      "Needs the permission `apps.manage` and sudo mode to change or remove one.",
	}}
}

// NewProjectVariableResource is the noust_project_variable resource.
func NewProjectVariableResource() resource.Resource {
	return &variableResource{kind: variableKind{
		typeName: "project_variable", scope: noust.ProjectVariables, title: "A project's shared variable",
		owners:     []string{"The slug of the team.", "The slug of the project."},
		importForm: "team/project/NAME",
		needs:      "Needs the permission `apps.manage` and sudo mode to change or remove one.",
	}}
}

// NewEnvironmentVariableResource is the noust_environment_variable resource.
func NewEnvironmentVariableResource() resource.Resource {
	return &variableResource{kind: variableKind{
		typeName: "environment_variable", scope: noust.EnvironmentVariables, title: "An environment's shared variable",
		owners:     []string{"The slug of the team.", "The slug of the project.", "The slug of the environment."},
		importForm: "team/project/environment/NAME",
		needs:      "Needs the permission `apps.manage` and sudo mode to change or remove one.",
	}}
}

// NewServerVariableResource is the noust_server_variable resource.
func NewServerVariableResource() resource.Resource {
	return &variableResource{kind: variableKind{
		typeName: "server_variable", scope: noust.ServerVariables, title: "A server-wide shared variable",
		importForm: "NAME",
		needs:      "Needs the permission `settings.manage` and sudo mode to change or remove one.",
	}}
}

type variableResource struct {
	kind variableKind
	data *providerData
}

// variableValues is a variable's attributes, read from a plan, a state or built from an answer. The
// attributes that locate the owner differ by scope, so they are read by name and not through a struct.
type variableValues struct {
	owner    []string
	name     string
	value    types.String
	secret   types.Bool
	audience types.String
}

// attributeReader is what a plan, a state and a configuration have in common: reading one attribute.
type attributeReader interface {
	GetAttribute(ctx context.Context, p path.Path, target any) diag.Diagnostics
}

func (r *variableResource) read(ctx context.Context, from attributeReader, diags *diag.Diagnostics) variableValues {
	var v variableValues
	for _, part := range r.kind.scope.Owner {
		var value types.String
		diags.Append(from.GetAttribute(ctx, path.Root(part), &value)...)
		v.owner = append(v.owner, value.ValueString())
	}
	var name types.String
	diags.Append(from.GetAttribute(ctx, path.Root("name"), &name)...)
	v.name = name.ValueString()
	diags.Append(from.GetAttribute(ctx, path.Root("value"), &v.value)...)
	diags.Append(from.GetAttribute(ctx, path.Root("secret"), &v.secret)...)
	diags.Append(from.GetAttribute(ctx, path.Root("audience"), &v.audience)...)
	return v
}

// write records the values as the state, with the id made from the owner and the name.
func (r *variableResource) write(ctx context.Context, state *tfsdk.State, v variableValues, diags *diag.Diagnostics) {
	for i, part := range r.kind.scope.Owner {
		diags.Append(state.SetAttribute(ctx, path.Root(part), v.owner[i])...)
	}
	diags.Append(state.SetAttribute(ctx, path.Root("id"), strings.Join(append(append([]string{}, v.owner...), v.name), "/"))...)
	diags.Append(state.SetAttribute(ctx, path.Root("name"), v.name)...)
	diags.Append(state.SetAttribute(ctx, path.Root("value"), v.value)...)
	diags.Append(state.SetAttribute(ctx, path.Root("secret"), v.secret)...)
	diags.Append(state.SetAttribute(ctx, path.Root("audience"), v.audience)...)
}

// adopt takes what the server holds for the variable. A secret's value is masked in every answer, so the
// value the state already has stays: the server never says it, and a changed one is not seen. A variable
// that is not a secret is compared like any other attribute. After an import the state has no value yet,
// and the masked one is recorded, so the next plan sets the configured value.
func (v *variableValues) adopt(got *noust.Variable) {
	v.audience = types.StringValue(got.Audience)
	v.secret = types.BoolNull()
	if got.Secrecy.Marked {
		v.secret = types.BoolValue(got.Secrecy.Secret)
	}
	if !got.Secrecy.Secret || !known(v.value) {
		v.value = types.StringValue(got.Value)
	}
}

func (r *variableResource) request(v variableValues) noust.VariableRequest {
	request := noust.VariableRequest{Value: v.value.ValueString(), Audience: v.audience.ValueString()}
	if known(v.secret) {
		mark := v.secret.ValueBool()
		request.Secret = &mark
	}
	return request
}

func (r *variableResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_" + r.kind.typeName
}

func (r *variableResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	k := r.kind
	scope := k.scope.Name
	replaces := " Changing it replaces the variable."
	attributes := map[string]schema.Attribute{
		"id": schema.StringAttribute{
			Computed:            true,
			Description:         "The variable's place in the state, " + k.importForm + ".",
			MarkdownDescription: "The variable's place in the state: `" + k.importForm + "`.",
			PlanModifiers:       []planmodifier.String{keepString()},
		},
		"name": schema.StringAttribute{
			Required:            true,
			Description:         "The variable's name: a letter or underscore, then letters, digits and underscores." + replaces,
			MarkdownDescription: "The variable's name: a letter or underscore, then letters, digits and underscores. A reference spells it `{{" + scope + ".NAME}}`." + replaces,
			PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
		},
		"value": schema.StringAttribute{
			Required:  true,
			Sensitive: true,
			Description: "Its value; references to other scopes such as {{server.REGION}} are allowed. A secret's value is masked " +
				"in every read: the state keeps the one the configuration wrote, and a change made by hand to a secret is not seen.",
			MarkdownDescription: "Its value; references to other scopes such as `{{server.REGION}}` are allowed. A secret's value is " +
				"masked in every read of the API, so the state keeps the one the configuration wrote and a change made by hand " +
				"to a secret is not seen; a value that is not a secret is compared like any attribute.",
		},
		"secret": schema.BoolAttribute{
			Optional:            true,
			Description:         "True to mark the variable a secret (masked in every read and log), false to say it is not, unset to let the server decide from its name and value.",
			MarkdownDescription: "`true` to mark the variable a secret (masked in every read and log), `false` to say it is not, unset to let the server decide from its name and value (`PASSWORD`, `TOKEN` and the shapes of known credentials are secrets).",
		},
		"audience": schema.StringAttribute{
			Optional:            true,
			Computed:            true,
			Default:             stringdefault.StaticString("both"),
			Description:         "Which part of a deployment an application that uses it hands it to: both, build or runtime. Default both.",
			MarkdownDescription: "Which part of a deployment an application that uses it hands it to: `both`, `build` or `runtime`. Default `both`.",
			Validators:          []validator.String{stringvalidator.OneOf("both", "build", "runtime")},
		},
	}
	for i, part := range k.scope.Owner {
		attributes[part] = schema.StringAttribute{
			Required:            true,
			Description:         k.owners[i] + replaces,
			MarkdownDescription: k.owners[i] + replaces,
			PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
		}
	}
	resp.Schema = schema.Schema{
		Description: k.title + ", defined once and used by reference from an application's own variables.",
		MarkdownDescription: fmt.Sprintf(k.title+", defined once and used by reference from an application's own variables "+
			"(`DATABASE_URL={{%s.DATABASE_URL}}`). It is never injected by itself: an application gets only what it names. "+
			"Removing one is allowed while applications refer to it, and the provider warns with their names, because their "+
			"next deploy fails until the reference changes.\n\n%s", scope, k.needs),
		Attributes: attributes,
	}
}

func (r *variableResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

// set sends the variable and records what the server stored.
func (r *variableResource) set(ctx context.Context, v variableValues, state *tfsdk.State, diags *diag.Diagnostics, summary string) {
	got, err := r.data.client.SetVariable(ctx, r.kind.scope, v.owner, v.name, r.request(v))
	if err != nil {
		addError(diags, summary, err)
		return
	}
	v.adopt(got)
	r.write(ctx, state, v, diags)
}

func (r *variableResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	v := r.read(ctx, req.Plan, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	r.set(ctx, v, &resp.State, &resp.Diagnostics, "Cannot set the variable")
}

func (r *variableResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	v := r.read(ctx, req.Plan, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	r.set(ctx, v, &resp.State, &resp.Diagnostics, "Cannot change the variable")
}

func (r *variableResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	v := r.read(ctx, req.State, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	got, err := r.data.client.GetVariable(ctx, r.kind.scope, v.owner, v.name)
	if noust.IsNotFound(err) || (err == nil && got == nil) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the variables", err)
		return
	}
	v.adopt(got)
	r.write(ctx, &resp.State, v, &resp.Diagnostics)
}

func (r *variableResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	v := r.read(ctx, req.State, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	removed, err := r.data.client.DeleteVariable(ctx, r.kind.scope, v.owner, v.name)
	if noust.IsNotFound(err) {
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot remove the variable", err)
		return
	}
	if len(removed.Applications) > 0 {
		resp.Diagnostics.AddWarning("Applications still refer to the variable",
			fmt.Sprintf("%s was removed, and the variables of %s still refer to {{%s.%s}}: their next deploy fails until the reference is changed.",
				v.name, strings.Join(removed.Applications, ", "), r.kind.scope.Name, v.name))
	}
}

func (r *variableResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, ok := splitID(req.ID, len(r.kind.scope.Owner)+1)
	if !ok {
		importIDError(resp, req.ID, r.kind.importForm)
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	for i, part := range r.kind.scope.Owner {
		resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root(part), parts[i])...)
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("name"), parts[len(parts)-1])...)
}
