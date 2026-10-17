// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"
	"fmt"
	"sort"

	"github.com/hashicorp/terraform-plugin-framework-validators/int64validator"
	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/int64planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

var (
	_ resource.Resource                = (*applicationResource)(nil)
	_ resource.ResourceWithConfigure   = (*applicationResource)(nil)
	_ resource.ResourceWithImportState = (*applicationResource)(nil)
)

// NewApplicationResource is the noust_application resource.
func NewApplicationResource() resource.Resource { return &applicationResource{} }

type applicationResource struct{ data *providerData }

type healthCheckModel struct {
	Path        types.String `tfsdk:"path"`
	Expect      types.String `tfsdk:"expect"`
	Timeout     types.Int64  `tfsdk:"timeout"`
	Interval    types.Int64  `tfsdk:"interval"`
	Retries     types.Int64  `tfsdk:"retries"`
	StartPeriod types.Int64  `tfsdk:"start_period"`
}

type applicationModel struct {
	ID                         types.String      `tfsdk:"id"`
	Domain                     types.String      `tfsdk:"domain"`
	Source                     types.String      `tfsdk:"source"`
	Branch                     types.String      `tfsdk:"branch"`
	AppType                    types.String      `tfsdk:"app_type"`
	Port                       types.Int64       `tfsdk:"port"`
	Layout                     types.String      `tfsdk:"layout"`
	Webserver                  types.String      `tfsdk:"webserver"`
	SSL                        types.Bool        `tfsdk:"ssl"`
	IncludeWWW                 types.Bool        `tfsdk:"include_www"`
	PersistentPaths            types.List        `tfsdk:"persistent_paths"`
	EnvVars                    types.Map         `tfsdk:"env_vars"`
	HealthCheck                *healthCheckModel `tfsdk:"health_check"`
	MemoryMaxMB                types.Int64       `tfsdk:"memory_max_mb"`
	CPUQuotaPercent            types.Int64       `tfsdk:"cpu_quota_percent"`
	TasksMax                   types.Int64       `tfsdk:"tasks_max"`
	Team                       types.String      `tfsdk:"team"`
	Project                    types.String      `tfsdk:"project"`
	Environment                types.String      `tfsdk:"environment"`
	Unit                       types.String      `tfsdk:"unit"`
	RestartOnChange            types.Bool        `tfsdk:"restart_on_change"`
	RemoveFilesOnDestroy       types.Bool        `tfsdk:"remove_files_on_destroy"`
	RemoveCertificateOnDestroy types.Bool        `tfsdk:"remove_certificate_on_destroy"`
	DeletionProtection         types.Bool        `tfsdk:"deletion_protection"`
}

func (r *applicationResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_application"
}

func (r *applicationResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	stateOrReplace := []planmodifier.String{keepString(), stringplanmodifier.RequiresReplace()}
	stateOnly := []planmodifier.String{keepString()}
	limit := func(what, unit string, floor int64) schema.Int64Attribute {
		return schema.Int64Attribute{
			Optional:            true,
			Computed:            true,
			Description:         what + " for the application's unit, in " + unit + ". 0 removes the limit. Applied in place.",
			MarkdownDescription: what + " for the application's unit, in " + unit + ". `0` removes the limit. Applied in place.",
			Validators:          []validator.Int64{int64validator.Any(int64validator.OneOf(0), int64validator.AtLeast(floor))},
			PlanModifiers:       []planmodifier.Int64{keepInt64()},
		}
	}
	resp.Schema = schema.Schema{
		Description: "An application deployed by the server from a git repository: built as a release behind a health gate, served behind the web server on its domain, and run by systemd.",
		MarkdownDescription: "An application deployed by the server from a git repository. Creating it queues the deployment " +
			"and waits for the job: the resource exists when the release has passed the health gate, and a deployment " +
			"that fails is the error you read, with the gate's probes and the journal word for word.\n\n" +
			"What the API accepts only when the application is made (`source`, `app_type`, `port`, `layout`, " +
			"`webserver`, `ssl`, `include_www`, `persistent_paths`) replaces the application when it changes; " +
			"`branch`, `health_check`, the limits, `env_vars` and the team, project and environment are updated in " +
			"place. A replacement deletes the application and deploys it again, so protect a production " +
			"application with `deletion_protection` or `lifecycle { prevent_destroy = true }`.\n\n" +
			"Needs the permission `apps.manage` (and `apps.operate` for the restart after a change); deleting, and " +
			"every in-place change, need sudo mode, so the token must be issued with `allow_elevated`.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The application's domain.",
				MarkdownDescription: "The application's domain.",
				PlanModifiers:       stateOnly,
			},
			"domain": schema.StringAttribute{
				Required:            true,
				Description:         "The application's primary domain, which names it everywhere. Changing it replaces the application.",
				MarkdownDescription: "The application's primary domain, which names it everywhere. Changing it replaces the application.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"source": schema.StringAttribute{
				Required:            true,
				Description:         "The git address to deploy from. Changing it replaces the application.",
				MarkdownDescription: "The git address to deploy from. Changing it replaces the application.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"branch": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The branch to deploy and to pin: the webhook ignores pushes to any other. It must exist on the remote. Changed in place; removing it from the configuration does not unpin it.",
				MarkdownDescription: "The branch to deploy and to pin: the webhook ignores pushes to any other. It must exist on the remote. Changed in place; removing it from the configuration does not unpin it.",
				PlanModifiers:       stateOnly,
			},
			"app_type": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The type of application (nextjs, nodejs, python, static...), as GET /api/apps/types lists them. Detected from the repository when omitted. Changing it replaces the application.",
				MarkdownDescription: "The type of application (`nextjs`, `nodejs`, `python`, `static`...), as `GET /api/apps/types` lists them. Detected from the repository when omitted. Changing it replaces the application.",
				Validators:          []validator.String{stringvalidator.NoneOf("auto")},
				PlanModifiers:       stateOrReplace,
			},
			"port": schema.Int64Attribute{
				Optional:            true,
				Computed:            true,
				Description:         "The port the application listens on. The server picks a free one when omitted. Changing it replaces the application.",
				MarkdownDescription: "The port the application listens on. The server picks a free one when omitted. Changing it replaces the application.",
				Validators:          []validator.Int64{int64validator.Between(1, 65535)},
				PlanModifiers:       []planmodifier.Int64{keepInt64(), int64planmodifier.RequiresReplace()},
			},
			"layout": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "releases (every deploy is a release behind a health gate, with instant rollback) or inplace. The server's deploy.layout when omitted. Changing it replaces the application: an in-place application is never converted implicitly.",
				MarkdownDescription: "`releases` (every deploy is a release behind a health gate, with instant rollback) or `inplace`. The server's `deploy.layout` when omitted. Changing it replaces the application: an in-place application is never converted implicitly.",
				Validators:          []validator.String{stringvalidator.OneOf("releases", "inplace")},
				PlanModifiers:       stateOrReplace,
			},
			"webserver": schema.StringAttribute{
				Optional:            true,
				Description:         "nginx or apache. nginx when omitted. Only used when the application is made; changing it replaces the application.",
				MarkdownDescription: "`nginx` or `apache`. `nginx` when omitted. Only used when the application is made; changing it replaces the application.",
				Validators:          []validator.String{stringvalidator.OneOf("nginx", "apache")},
				PlanModifiers:       []planmodifier.String{createOnlyString()},
			},
			"ssl": schema.BoolAttribute{
				Optional:            true,
				Description:         "Obtain a certificate. true when omitted. Only used when the application is made; changing it replaces the application.",
				MarkdownDescription: "Obtain a certificate. `true` when omitted. Only used when the application is made; changing it replaces the application.",
				PlanModifiers:       []planmodifier.Bool{createOnlyBool()},
			},
			"include_www": schema.BoolAttribute{
				Optional:            true,
				Description:         "Also answer on www.<domain>, as a redirect to it. false when omitted. Only used when the application is made; use noust_domain afterwards.",
				MarkdownDescription: "Also answer on `www.<domain>`, as a redirect to it. `false` when omitted. Only used when the application is made; use `noust_domain` afterwards.",
				PlanModifiers:       []planmodifier.Bool{createOnlyBool()},
			},
			"persistent_paths": schema.ListAttribute{
				Optional:            true,
				ElementType:         types.StringType,
				Description:         "Releases layout only: paths, relative to the application, linked into shared/ and kept across every release (uploads, storage). Only used when the application is made.",
				MarkdownDescription: "Releases layout only: paths, relative to the application, linked into `shared/` and kept across every release (uploads, storage). Only used when the application is made.",
				PlanModifiers:       []planmodifier.List{listplanmodifier.RequiresReplaceIf(replaceUnlessImported, "Changing it replaces the application: the API only takes it when the application is made.", "Changing it replaces the application: the API only takes it when the application is made.")},
			},
			"env_vars": schema.MapAttribute{
				Optional:            true,
				Sensitive:           true,
				ElementType:         types.StringType,
				Description:         "Environment variables of the application. Only the names listed here are managed: variables the server sets itself (a linked database's DATABASE_URL) or an operator added are left alone. PORT and NODE_ENV are the unit's and are refused.",
				MarkdownDescription: "Environment variables of the application. Only the names listed here are managed: variables the server sets itself (a linked database's `DATABASE_URL`) or an operator added are left alone. `PORT` and `NODE_ENV` are the unit's and are refused.\n\nThe whole map is sensitive. Refreshing compares the values on the server without reading them (a dry run of the merge), so a variable changed by hand shows as a difference.",
			},
			"health_check": schema.SingleNestedAttribute{
				Optional:            true,
				Description:         "What the health gate asks of the application. The type's own check when omitted.",
				MarkdownDescription: "What the health gate asks of the application before a release may serve. The type's own check when omitted. A static site is checked by its files and refuses one.",
				Attributes: map[string]schema.Attribute{
					"path": schema.StringAttribute{
						Optional:            true,
						Description:         "Path to probe on 127.0.0.1, such as /healthz. / when omitted.",
						MarkdownDescription: "Path to probe on `127.0.0.1`, such as `/healthz`. `/` when omitted.",
					},
					"expect": schema.StringAttribute{
						Optional:            true,
						Description:         "Statuses that mean up, 100 to 599: 200-399, or 200,204. Any status below 500 when omitted.",
						MarkdownDescription: "Statuses that mean up, 100 to 599: `200-399`, or `200,204`. Any status below 500 when omitted.",
					},
					"timeout": schema.Int64Attribute{
						Optional:            true,
						Description:         "Seconds the gate waits, 5 to 600. 30 when omitted.",
						MarkdownDescription: "Seconds the gate waits, 5 to 600. `30` when omitted.",
						Validators:          []validator.Int64{int64validator.Between(5, 600)},
					},
					"interval": schema.Int64Attribute{
						Optional:            true,
						Description:         "Seconds between two probes, 1 to 60. 2 when omitted.",
						MarkdownDescription: "Seconds between two probes, 1 to 60. `2` when omitted.",
						Validators:          []validator.Int64{int64validator.Between(1, 60)},
					},
					"retries": schema.Int64Attribute{
						Optional:            true,
						Description:         "The most probes after the start period, 1 to 100. As many as fit when omitted.",
						MarkdownDescription: "The most probes after the start period, 1 to 100. As many as fit when omitted.",
						Validators:          []validator.Int64{int64validator.Between(1, 100)},
					},
					"start_period": schema.Int64Attribute{
						Optional:            true,
						Description:         "Seconds during which a failed probe does not count, 0 to 600. 0 when omitted.",
						MarkdownDescription: "Seconds during which a failed probe does not count, 0 to 600. `0` when omitted.",
						Validators:          []validator.Int64{int64validator.Between(0, 600)},
					},
				},
			},
			"memory_max_mb":     limit("MemoryMax", "MB", 64),
			"cpu_quota_percent": limit("CPUQuota", "percent of one CPU (200 is two CPUs)", 1),
			"tasks_max":         limit("TasksMax (processes and threads)", "tasks", 16),
			"team": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The team the application belongs to. The one the token works in, its only team or the default when omitted. Changing it moves the application, with its sites, databases and backup schedule.",
				MarkdownDescription: "The team the application belongs to. The one the token works in, its only team or the default when omitted. Changing it moves the application, with its sites, databases and backup schedule.",
				PlanModifiers:       stateOnly,
			},
			"project": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The project the application belongs to. The team's default when omitted. Changing it moves the application.",
				MarkdownDescription: "The project the application belongs to. The team's default when omitted. Changing it moves the application.",
				PlanModifiers:       stateOnly,
			},
			"environment": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The environment the application belongs to. production when omitted. Changing it moves the application.",
				MarkdownDescription: "The environment the application belongs to. `production` when omitted. Changing it moves the application.",
				PlanModifiers:       stateOnly,
			},
			"unit": schema.StringAttribute{
				Computed:            true,
				Description:         "The name of the systemd unit that runs the application.",
				MarkdownDescription: "The name of the systemd unit that runs the application.",
				PlanModifiers:       stateOnly,
			},
			"restart_on_change": schema.BoolAttribute{
				Optional:            true,
				Computed:            true,
				Default:             booldefault.StaticBool(true),
				Description:         "Restart the application after a change to env_vars or the limits, so the running process picks it up. Default true. The restart is not behind the health gate; a deployment is.",
				MarkdownDescription: "Restart the application after a change to `env_vars` or the limits, so the running process picks it up. Default `true`. The restart is not behind the health gate; a deployment is.",
			},
			"remove_files_on_destroy": schema.BoolAttribute{
				Optional:            true,
				Computed:            true,
				Default:             booldefault.StaticBool(true),
				Description:         "Also delete the application's directory (releases, shared files, uploads) when it is destroyed. Default true, because the server will not deploy at a domain whose directory is still there, which a replacement does. Set it to false to keep the files of an application you destroy for good; they must then be moved aside before that domain is deployed again.",
				MarkdownDescription: "Also delete the application's directory (releases, shared files, uploads) when it is destroyed. Default `true`, because the server will not deploy at a domain whose directory is still there, which a replacement does. Set it to `false` to keep the files of an application you destroy for good; they must then be moved aside before that domain is deployed again. Protect an application whose uploads matter with `deletion_protection`.",
			},
			"remove_certificate_on_destroy": schema.BoolAttribute{
				Optional:            true,
				Computed:            true,
				Default:             booldefault.StaticBool(false),
				Description:         "Also delete the application's certificate when it is destroyed. Default false.",
				MarkdownDescription: "Also delete the application's certificate when it is destroyed. Default `false`.",
			},
			"deletion_protection": schema.BoolAttribute{
				Optional:            true,
				Computed:            true,
				Default:             booldefault.StaticBool(false),
				Description:         "Refuse to destroy the application from Terraform until this is set to false and applied. Default false.",
				MarkdownDescription: "Refuse to destroy the application from Terraform until this is set to `false` and applied. Default `false`.",
			},
		},
	}
}

// replaceUnlessImported replaces only when the state holds a value to compare with: an imported application
// has none for what the API does not report, and adopts the configuration's.
func replaceUnlessImported(_ context.Context, req planmodifier.ListRequest, resp *listplanmodifier.RequiresReplaceIfFuncResponse) {
	resp.RequiresReplace = !req.StateValue.IsNull()
}

func (r *applicationResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

// limitFrom reads an API limit. The API's null is "no limit", which the schema writes as 0 when the
// configuration did.
func limitFrom(value *int, prior types.Int64) types.Int64 {
	if value != nil {
		return types.Int64Value(int64(*value))
	}
	if known(prior) && prior.ValueInt64() == 0 {
		return types.Int64Value(0)
	}
	return types.Int64Null()
}

// limitRequest is the API's value for a configured limit: nil (no limit) for 0 or nothing.
func limitRequest(value types.Int64) *int {
	if !known(value) || value.ValueInt64() == 0 {
		return nil
	}
	v := int(value.ValueInt64())
	return &v
}

// applicationInto copies what the API says about the application into the model. What the API cannot
// report (the create-time options, the variables' values) stays as the model had it.
func applicationInto(model *applicationModel, app *noust.App) {
	model.ID = types.StringValue(app.Domain)
	model.Domain = types.StringValue(app.Domain)
	model.Source = stringFromPtr(app.Source)
	model.Branch = stringFromPtr(app.Branch)
	model.AppType = stringFromPtr(app.AppType)
	model.Port = int64FromPtr(app.Port)
	model.Layout = types.StringValue(app.Layout)
	model.Team = stringFromPtr(app.Team)
	model.Project = stringFromPtr(app.Project)
	model.Environment = stringFromPtr(app.Environment)
	model.Unit = stringFromPtr(app.Unit)
	model.MemoryMaxMB = limitFrom(app.MemoryMaxMB, model.MemoryMaxMB)
	model.CPUQuotaPercent = limitFrom(app.CPUQuotaPercent, model.CPUQuotaPercent)
	model.TasksMax = limitFrom(app.TasksMax, model.TasksMax)
}

// healthFrom reads the server's health check as the model writes it: nil when nothing is customised.
func healthFrom(health *noust.Health) *healthCheckModel {
	if health.Path == nil && health.Expect == nil && health.Timeout == nil && health.Interval == nil &&
		health.Retries == nil && health.StartPeriod == nil {
		return nil
	}
	return &healthCheckModel{
		Path:        stringFromPtr(health.Path),
		Expect:      stringFromPtr(health.Expect),
		Timeout:     int64FromPtr(health.Timeout),
		Interval:    int64FromPtr(health.Interval),
		Retries:     int64FromPtr(health.Retries),
		StartPeriod: int64FromPtr(health.StartPeriod),
	}
}

func healthRequest(model *healthCheckModel) noust.Health {
	if model == nil {
		return noust.Health{Kind: "http"}
	}
	path, expect := stringOf(model.Path), stringOf(model.Expect)
	health := noust.Health{
		Kind:        "http",
		Timeout:     intPtrOf(model.Timeout),
		Interval:    intPtrOf(model.Interval),
		Retries:     intPtrOf(model.Retries),
		StartPeriod: intPtrOf(model.StartPeriod),
	}
	if path != "" {
		health.Path = &path
	}
	if expect != "" {
		health.Expect = &expect
	}
	return health
}

func (r *applicationResource) createRequest(ctx context.Context, plan *applicationModel, diags *diag.Diagnostics) noust.CreateAppRequest {
	request := noust.CreateAppRequest{
		Domain:          plan.Domain.ValueString(),
		Source:          plan.Source.ValueString(),
		Branch:          stringOf(plan.Branch),
		AppType:         stringOf(plan.AppType),
		Port:            intPtrOf(plan.Port),
		Layout:          stringOf(plan.Layout),
		Webserver:       stringOf(plan.Webserver),
		EnvVars:         stringMapOf(ctx, plan.EnvVars, diags),
		PersistentPaths: stringsOf(ctx, plan.PersistentPaths, diags),
		MemoryMaxMB:     limitRequest(plan.MemoryMaxMB),
		CPUQuotaPercent: limitRequest(plan.CPUQuotaPercent),
		TasksMax:        limitRequest(plan.TasksMax),
		Team:            stringOf(plan.Team),
		Project:         stringOf(plan.Project),
		Environment:     stringOf(plan.Environment),
	}
	if known(plan.SSL) {
		ssl := plan.SSL.ValueBool()
		request.SSL = &ssl
	}
	if known(plan.IncludeWWW) {
		www := plan.IncludeWWW.ValueBool()
		request.IncludeWWW = &www
	}
	if health := plan.HealthCheck; health != nil {
		// The first deployment is the one the gate protects, so what it asks goes in the request; the
		// rest of the check is set once the application exists.
		request.HealthPath = stringOf(health.Path)
		request.HealthExpect = stringOf(health.Expect)
		request.HealthTimeout = intPtrOf(health.Timeout)
	}
	return request
}

func (r *applicationResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan applicationModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	domain := plan.Domain.ValueString()
	accepted, err := r.data.client.CreateApp(ctx, r.createRequest(ctx, &plan, &resp.Diagnostics))
	if resp.Diagnostics.HasError() {
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot create the application", err)
		return
	}
	if _, err := r.data.waitFor(ctx, accepted); err != nil {
		addError(&resp.Diagnostics, "The application was not deployed", err)
		// A failed first deployment normally leaves nothing behind. When something is there, Terraform must
		// know about it (as a tainted resource) or the next apply would try to make it again and be refused.
		if app, getErr := r.data.client.GetApp(ctx, domain); getErr == nil {
			applicationInto(&plan, app)
			resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
		}
		return
	}
	app, err := r.data.client.GetApp(ctx, domain)
	if err != nil {
		addError(&resp.Diagnostics, "The application was deployed but cannot be read back", err)
		return
	}
	applicationInto(&plan, app)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	r.completeEnv(ctx, domain, &plan, &resp.Diagnostics)
	if health := plan.HealthCheck; health != nil && (known(health.Interval) || known(health.Retries) || known(health.StartPeriod)) {
		if err := r.data.client.UpdateHealth(ctx, domain, healthRequest(health)); err != nil {
			addError(&resp.Diagnostics, "The application was deployed but its health check could not be completed", err)
		}
	}
}

// completeEnv makes the server hold the variables the configuration lists. They travel in the request
// that deploys the application, but a type that runs nothing (a static site) keeps none of them, and the
// next refresh would find them missing and plan the same change again; a dry run says whether anything is
// missing, and only then are they set.
func (r *applicationResource) completeEnv(ctx context.Context, domain string, plan *applicationModel, diags *diag.Diagnostics) {
	wanted := stringMapOf(ctx, plan.EnvVars, diags)
	if len(wanted) == 0 || diags.HasError() {
		return
	}
	dry, err := r.data.client.MergeAppEnv(ctx, domain, noust.MergeEnvRequest{Set: wanted, DryRun: true})
	if err != nil {
		addError(diags, "The application was deployed but its variables could not be checked", err)
		return
	}
	if len(dry.Changed) == 0 {
		return
	}
	if _, err := r.data.client.MergeAppEnv(ctx, domain, noust.MergeEnvRequest{Set: wanted}); err != nil {
		addError(diags, "The application was deployed but its variables could not be set", err)
	}
}

func (r *applicationResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state applicationModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	domain := state.Domain.ValueString()
	app, err := r.data.client.GetApp(ctx, domain)
	if noust.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the application", err)
		return
	}
	applicationInto(&state, app)

	health, err := r.data.client.GetHealth(ctx, domain)
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the application's health check", err)
		return
	}
	state.HealthCheck = healthFrom(health)

	if known(state.EnvVars) && len(state.EnvVars.Elements()) > 0 {
		state.EnvVars = r.refreshEnv(ctx, domain, state.EnvVars, &resp.Diagnostics)
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &state)...)
}

// refreshEnv keeps of the variables Terraform manages those that are still on the server with the value it
// last wrote. The server never answers with the values of secrets, so it is asked the other way round: a dry
// run of the merge names the variables that would change, which are exactly those that were changed or removed
// outside Terraform, and they leave the state so the next plan sets them again.
func (r *applicationResource) refreshEnv(ctx context.Context, domain string, prior types.Map, diags *diag.Diagnostics) types.Map {
	managed := stringMapOf(ctx, prior, diags)
	if diags.HasError() {
		return prior
	}
	dry, err := r.data.client.MergeAppEnv(ctx, domain, noust.MergeEnvRequest{Set: managed, DryRun: true})
	if err != nil {
		api, isAPI := noust.AsAPIError(err)
		if !isAPI || (api.Status != 403) {
			addError(diags, "Cannot compare the application's variables", err)
			return prior
		}
		// The token may read but not write (a plan-only credential): the values cannot be compared, the
		// names still can.
		diags.AddWarning("The values of the application's variables were not compared",
			"The comparison needs the permission apps.manage and sudo mode; this token has less, so only the "+
				"presence of each variable was checked.\n\n"+describe(err))
		listed, listErr := r.data.client.GetAppEnv(ctx, domain)
		if listErr != nil {
			addError(diags, "Cannot read the application's variables", listErr)
			return prior
		}
		kept := map[string]string{}
		for name, value := range managed {
			if _, present := listed.Variables[name]; present {
				kept[name] = value
			}
		}
		return mapOf(ctx, kept, diags)
	}
	drifted := map[string]bool{}
	for _, name := range dry.Changed {
		drifted[name] = true
	}
	kept := map[string]string{}
	for name, value := range managed {
		if !drifted[name] {
			kept[name] = value
		}
	}
	return mapOf(ctx, kept, diags)
}

func mapOf(ctx context.Context, values map[string]string, diags *diag.Diagnostics) types.Map {
	out, d := types.MapValueFrom(ctx, types.StringType, values)
	diags.Append(d...)
	return out
}

func (r *applicationResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan, state applicationModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	client := r.data.client
	domain := plan.Domain.ValueString()
	restart := boolOr(plan.RestartOnChange, true)
	needsRestart := false

	// Each change is made and the state saved after it, so a failure halfway leaves Terraform knowing what
	// was done and the next plan finishing the rest.
	save := func() bool {
		resp.Diagnostics.Append(resp.State.Set(ctx, &state)...)
		return !resp.Diagnostics.HasError()
	}

	if changed(plan.Team, state.Team) || changed(plan.Project, state.Project) || changed(plan.Environment, state.Environment) {
		if err := client.MoveApp(ctx, domain, moveTo(plan.Team, state.Team), moveTo(plan.Project, state.Project), moveTo(plan.Environment, state.Environment)); err != nil {
			addError(&resp.Diagnostics, "Cannot move the application", err)
			return
		}
		state.Team, state.Project, state.Environment = plan.Team, plan.Project, plan.Environment
		if !save() {
			return
		}
	}

	if changed(plan.Branch, state.Branch) {
		if err := client.UpdateBranch(ctx, domain, plan.Branch.ValueString()); err != nil {
			addError(&resp.Diagnostics, "Cannot pin the branch", err)
			return
		}
		state.Branch = plan.Branch
		if !save() {
			return
		}
	}

	if !sameHealth(plan.HealthCheck, state.HealthCheck) {
		if err := client.UpdateHealth(ctx, domain, healthRequest(plan.HealthCheck)); err != nil {
			addError(&resp.Diagnostics, "Cannot change the health check", err)
			return
		}
		state.HealthCheck = plan.HealthCheck
		if !save() {
			return
		}
	}

	if differs(plan.MemoryMaxMB, state.MemoryMaxMB) || differs(plan.CPUQuotaPercent, state.CPUQuotaPercent) || differs(plan.TasksMax, state.TasksMax) {
		limits := noust.Limits{
			MemoryMaxMB:     limitRequest(plan.MemoryMaxMB),
			CPUQuotaPercent: limitRequest(plan.CPUQuotaPercent),
			TasksMax:        limitRequest(plan.TasksMax),
		}
		if err := client.UpdateLimits(ctx, domain, limits, restart); err != nil {
			addError(&resp.Diagnostics, "Cannot change the limits", err)
			return
		}
		state.MemoryMaxMB, state.CPUQuotaPercent, state.TasksMax = plan.MemoryMaxMB, plan.CPUQuotaPercent, plan.TasksMax
		if !save() {
			return
		}
	}

	planEnv := stringMapOf(ctx, plan.EnvVars, &resp.Diagnostics)
	stateEnv := stringMapOf(ctx, state.EnvVars, &resp.Diagnostics)
	set, unset := envDelta(planEnv, stateEnv)
	if len(set) > 0 || len(unset) > 0 {
		merged, err := client.MergeAppEnv(ctx, domain, noust.MergeEnvRequest{Set: set, Unset: unset})
		if err != nil {
			addError(&resp.Diagnostics, "Cannot change the variables", err)
			return
		}
		needsRestart = merged.RestartRequired
		state.EnvVars = plan.EnvVars
		if !save() {
			return
		}
	}
	// A static site has no unit: nothing runs, so there is nothing to restart.
	if needsRestart && restart && stringOf(state.Unit) != "" {
		if err := client.RestartApp(ctx, domain); err != nil {
			addError(&resp.Diagnostics, "The variables were changed but the application could not be restarted", err)
			return
		}
	}

	// What only the configuration holds is adopted as planned.
	state.RestartOnChange = plan.RestartOnChange
	state.RemoveFilesOnDestroy = plan.RemoveFilesOnDestroy
	state.RemoveCertificateOnDestroy = plan.RemoveCertificateOnDestroy
	state.DeletionProtection = plan.DeletionProtection
	state.Webserver, state.SSL, state.IncludeWWW, state.PersistentPaths = plan.Webserver, plan.SSL, plan.IncludeWWW, plan.PersistentPaths
	state.Source, state.AppType, state.Port, state.Layout = plan.Source, plan.AppType, plan.Port, plan.Layout
	state.Unit = plan.Unit
	state.EnvVars = plan.EnvVars
	state.HealthCheck = plan.HealthCheck
	app, err := client.GetApp(ctx, domain)
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the application back", err)
		return
	}
	applicationInto(&state, app)
	save()
}

// changed reports whether a computed-or-configured string was given a new value.
func changed(plan, state types.String) bool {
	return known(plan) && plan.ValueString() != state.ValueString()
}

func differs(plan, state types.Int64) bool {
	return known(plan) && (!known(state) || plan.ValueInt64() != state.ValueInt64())
}

// moveTo is the destination to send for one coordinate of a move: the new value, or nothing when it stays.
func moveTo(plan, state types.String) string {
	if changed(plan, state) {
		return plan.ValueString()
	}
	return ""
}

func sameHealth(a, b *healthCheckModel) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return a.Path.Equal(b.Path) && a.Expect.Equal(b.Expect) && a.Timeout.Equal(b.Timeout) &&
		a.Interval.Equal(b.Interval) && a.Retries.Equal(b.Retries) && a.StartPeriod.Equal(b.StartPeriod)
}

// envDelta names what a change of the variables sets (those whose value is new) and unsets (those that
// left the configuration).
func envDelta(plan, state map[string]string) (set map[string]string, unset []string) {
	set = map[string]string{}
	for name, value := range plan {
		if old, present := state[name]; !present || old != value {
			set[name] = value
		}
	}
	for name := range state {
		if _, kept := plan[name]; !kept {
			unset = append(unset, name)
		}
	}
	sort.Strings(unset)
	return set, unset
}

func (r *applicationResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state applicationModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	domain := state.Domain.ValueString()
	if boolOr(state.DeletionProtection, false) {
		resp.Diagnostics.AddError("The application is protected from deletion",
			fmt.Sprintf("deletion_protection is true on the application %s. Set it to false and apply that change first, then destroy.", domain))
		return
	}
	accepted, err := r.data.client.DeleteApp(ctx, domain, noust.DeleteAppOptions{
		RemoveFiles: boolOr(state.RemoveFilesOnDestroy, false),
		RemoveSSL:   boolOr(state.RemoveCertificateOnDestroy, false),
	})
	if noust.IsNotFound(err) {
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot delete the application", err)
		return
	}
	if _, err := r.data.waitFor(ctx, accepted); err != nil {
		addError(&resp.Diagnostics, "The application was not deleted", err)
	}
}

func (r *applicationResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("domain"), req.ID)...)
}
