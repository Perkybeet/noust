// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework-validators/int64validator"
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
	_ resource.Resource                = (*domainResource)(nil)
	_ resource.ResourceWithConfigure   = (*domainResource)(nil)
	_ resource.ResourceWithImportState = (*domainResource)(nil)
)

// NewDomainResource is the noust_domain resource.
func NewDomainResource() resource.Resource { return &domainResource{} }

type domainResource struct{ data *providerData }

type domainModel struct {
	ID       types.String `tfsdk:"id"`
	App      types.String `tfsdk:"app"`
	Domain   types.String `tfsdk:"domain"`
	Kind     types.String `tfsdk:"kind"`
	Port     types.Int64  `tfsdk:"port"`
	CheckDNS types.Bool   `tfsdk:"check_dns"`
	Force    types.Bool   `tfsdk:"force"`
}

func (r *domainResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_domain"
}

func (r *domainResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A second name an application answers on: an alias that serves the application, or a redirect to its primary domain.",
		MarkdownDescription: "A name an application answers on besides its primary domain: an `alias` that serves the " +
			"application, or a `redirect` to it. The site is re-rendered and reloaded before the call returns, and " +
			"when the site serves TLS the certificate is extended to the new name as a job the provider waits for. " +
			"A certificate that cannot be issued (the name does not point at the server yet) is a warning, not an " +
			"error: the name is added, and the next apply after the DNS record exists does not need to change it.\n\n" +
			"The application's primary domain is the `domain` of the `noust_application`, not a resource of this kind.\n\n" +
			"Needs the permission `apps.manage`; removing a name needs sudo mode.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The application's domain and the name, joined with a slash.",
				MarkdownDescription: "The application's domain and the name, joined with a slash: `app/name`.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"app": schema.StringAttribute{
				Required:            true,
				Description:         "The primary domain of the application. Changing it replaces the name.",
				MarkdownDescription: "The primary domain of the application. Changing it replaces the name.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"domain": schema.StringAttribute{
				Required:            true,
				Description:         "The name to answer on. Changing it replaces the name.",
				MarkdownDescription: "The name to answer on. Changing it replaces the name.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"kind": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "alias (serve the application) or redirect (send visitors to the primary domain). Default alias. Changing it replaces the name.",
				MarkdownDescription: "`alias` (serve the application) or `redirect` (send visitors to the primary domain). Default `alias`. Changing it replaces the name.",
				Validators:          []validator.String{stringvalidator.OneOf("alias", "redirect")},
				PlanModifiers:       []planmodifier.String{keepString(), stringplanmodifier.RequiresReplace()},
			},
			"port": schema.Int64Attribute{
				Optional:            true,
				Description:         "The port of the application this name is proxied to, when it is not the application's own.",
				MarkdownDescription: "The port of the application this name is proxied to, when it is not the application's own. Updated in place.",
				Validators:          []validator.Int64{int64validator.Between(1, 65535)},
			},
			"check_dns": schema.BoolAttribute{
				Optional:            true,
				Description:         "Whether the server checks that the name points at it before adding it. Default true. Only used when the name is added.",
				MarkdownDescription: "Whether the server checks that the name points at it before adding it. Default `true`. Only used when the name is added.",
			},
			"force": schema.BoolAttribute{
				Optional:            true,
				Description:         "Add the name even though the server refuses it (it resolves elsewhere, or another site holds it). Default false. Only used when the name is added.",
				MarkdownDescription: "Add the name even though the server refuses it (it resolves elsewhere, or another site holds it). Default `false`. Only used when the name is added.",
			},
		},
	}
}

func (r *domainResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func (r *domainResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan domainModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	app, name := plan.App.ValueString(), plan.Domain.ValueString()
	change, err := r.data.client.AddDomain(ctx, app, noust.AddDomainRequest{
		Domain: name,
		Kind:   stringOf(plan.Kind),
		Port:   intPtrOf(plan.Port),
		DNS:    boolOr(plan.CheckDNS, true),
		Force:  boolOr(plan.Force, false),
	})
	if err != nil {
		addError(&resp.Diagnostics, "Cannot add the domain", err)
		return
	}
	plan.ID = types.StringValue(app + "/" + name)
	plan.Kind = types.StringValue(kindOf(change.Domains, name, plan.Kind))
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
	r.waitForCertificate(ctx, change, "The domain was added but its certificate was not issued", &resp.Diagnostics)
}

// waitForCertificate follows the job that extends the site's certificate to a change of its names. The
// change is made; only the certificate may be missing, and failing would taint a domain that is fine, so
// the server's own words go in a warning.
func (r *domainResource) waitForCertificate(ctx context.Context, change *noust.DomainChange, summary string, diags *diag.Diagnostics) {
	if change == nil || change.CertificateJobID == nil || *change.CertificateJobID == "" {
		return
	}
	if _, err := r.data.waitFor(ctx, &noust.JobAccepted{JobID: *change.CertificateJobID}); err != nil {
		addWarning(diags, summary, err)
	}
}

func kindOf(domains []noust.AppDomain, name string, fallback types.String) string {
	for _, entry := range domains {
		if entry.Domain == name {
			return entry.Kind
		}
	}
	return stringOf(fallback)
}

func (r *domainResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state domainModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	listed, err := r.data.client.ListDomains(ctx, state.App.ValueString())
	if noust.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the application's domains", err)
		return
	}
	for _, entry := range listed.Domains {
		if entry.Domain != state.Domain.ValueString() {
			continue
		}
		state.ID = types.StringValue(state.App.ValueString() + "/" + entry.Domain)
		state.Kind = types.StringValue(entry.Kind)
		state.Port = int64FromPtr(entry.Port)
		resp.Diagnostics.Append(resp.State.Set(ctx, &state)...)
		return
	}
	resp.State.RemoveResource(ctx)
}

func (r *domainResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan domainModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	// Only the port is changed in place; check_dns and force are options of adding the name.
	change, err := r.data.client.SetDomainPort(ctx, plan.App.ValueString(), plan.Domain.ValueString(), intPtrOf(plan.Port))
	if err != nil {
		addError(&resp.Diagnostics, "Cannot change the port of the domain", err)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
	r.waitForCertificate(ctx, change, "The port was changed but the certificate was not renewed", &resp.Diagnostics)
}

func (r *domainResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state domainModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	change, err := r.data.client.DeleteDomain(ctx, state.App.ValueString(), state.Domain.ValueString())
	if err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot remove the domain", err)
		return
	}
	r.waitForCertificate(ctx, change, "The domain was removed but the certificate was not renewed", &resp.Diagnostics)
}

func (r *domainResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, ok := splitID(req.ID, 2)
	if !ok {
		importIDError(resp, req.ID, "app/domain")
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("app"), parts[0])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("domain"), parts[1])...)
}
