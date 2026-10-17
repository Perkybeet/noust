// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

// Package provider is the Noust Terraform and OpenTofu provider: resources and data sources that
// translate a configuration into calls of the Noust API (internal/noust) and the API's answers back into
// state. It implements nothing a Noust server does.
package provider

import (
	"context"
	"net/http"
	"os"
	"time"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-log/tflog"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

const (
	defaultJobTimeout   = 30 * time.Minute
	defaultPollInterval = 2 * time.Second
)

var _ provider.Provider = (*noustProvider)(nil)

// New returns the provider for the given version ("dev" in a local build, the tag in a release).
func New(version string) func() provider.Provider {
	return func() provider.Provider { return &noustProvider{version: version} }
}

type noustProvider struct {
	version string
}

// providerData is what the provider hands every resource and data source.
type providerData struct {
	client       *noust.Client
	jobTimeout   time.Duration
	pollInterval time.Duration
}

// waitFor follows a job the API queued until it ends.
func (d *providerData) waitFor(ctx context.Context, accepted *noust.JobAccepted) (*noust.Job, error) {
	return d.client.WaitForJob(ctx, accepted, d.pollInterval, d.jobTimeout)
}

type providerModel struct {
	URL             types.String `tfsdk:"url"`
	Token           types.String `tfsdk:"token"`
	CAFile          types.String `tfsdk:"ca_file"`
	JobTimeout      types.String `tfsdk:"job_timeout"`
	JobPollInterval types.String `tfsdk:"job_poll_interval"`
	Approval        types.String `tfsdk:"approval"`
}

func (p *noustProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "noust"
	resp.Version = p.version
}

func (p *noustProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Manage a Noust server (applications, domains, databases, backups, teams, deploy keys and registry accounts) from Terraform or OpenTofu, through the server's HTTP API.",
		MarkdownDescription: "Manage a [Noust](https://github.com/Perkybeet/noust) server " +
			"(applications, domains, databases, backups, teams, deploy keys and registry accounts) from Terraform " +
			"or OpenTofu, through the server's HTTP API.\n\n" +
			"The provider is a client of the API and implements nothing a server does: every change is the call " +
			"the console or `noust` would make, behind the same permissions, sudo mode, approvals and audit trail.",
		Attributes: map[string]schema.Attribute{
			"url": schema.StringAttribute{
				Optional:            true,
				Description:         "The console's address, such as https://noust.example.com. Read from NOUST_URL when omitted. Plain http is accepted only for localhost, 127.0.0.1 and ::1.",
				MarkdownDescription: "The console's address, such as `https://noust.example.com`. Read from `NOUST_URL` when omitted. Plain `http://` is accepted only for `localhost`, `127.0.0.1` and `::1`, because the token would cross the network in clear.",
			},
			"token": schema.StringAttribute{
				Optional:            true,
				Sensitive:           true,
				Description:         "An API token holding the permissions the configuration needs, issued with allow_elevated. Read from NOUST_TOKEN when omitted. Never the master token.",
				MarkdownDescription: "An API token holding the permissions the configuration needs, issued with `allow_elevated`. Read from `NOUST_TOKEN` when omitted. Never the master token: see the guide for the permissions each resource asks for.",
			},
			"ca_file": schema.StringAttribute{
				Optional:            true,
				Description:         "A PEM file with the certificate authority to trust instead of the system's, for a console on a private certificate. Read from NOUST_CA_FILE when omitted. There is no option to skip verification.",
				MarkdownDescription: "A PEM file with the certificate authority to trust instead of the system's, for a console on a private certificate. Read from `NOUST_CA_FILE` when omitted. There is no option to skip verification.",
			},
			"job_timeout": schema.StringAttribute{
				Optional:            true,
				Description:         "How long to wait for a job the server queued (a deployment, a removal), as a Go duration such as 30m. The job keeps running on the server when the wait ends. Default 30m.",
				MarkdownDescription: "How long to wait for a job the server queued (a deployment, a removal), as a Go duration such as `30m`. The job keeps running on the server when the wait ends. Default `30m`.",
			},
			"job_poll_interval": schema.StringAttribute{
				Optional:            true,
				Description:         "How often to ask the server about a job, as a Go duration. Default 2s.",
				MarkdownDescription: "How often to ask the server about a job, as a Go duration. Default `2s`.",
			},
			"approval": schema.StringAttribute{
				Optional:            true,
				Description:         "The id of an approved request (X-Noust-Approval), for a server that asks a second person to approve root-equivalent changes. An approval allows one call, once. Read from NOUST_APPROVAL when omitted.",
				MarkdownDescription: "The id of an approved request (`X-Noust-Approval`), for a server that asks a second person to approve root-equivalent changes. An approval allows one call, once. Read from `NOUST_APPROVAL` when omitted.",
			},
		},
	}
}

func (p *noustProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var config providerModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)
	if resp.Diagnostics.HasError() {
		return
	}
	// An unknown value (a token produced by another resource in the same plan) cannot be used to build a
	// client yet: say so rather than trying with nothing.
	for name, value := range map[string]types.String{"url": config.URL, "token": config.Token, "ca_file": config.CAFile} {
		if value.IsUnknown() {
			resp.Diagnostics.AddAttributeError(path.Root(name), "The provider "+name+" is not known yet",
				"The provider is configured before the resources it depends on exist, so its "+name+
					" must be a value known at plan time: a variable, an environment variable or a data source.")
		}
	}
	if resp.Diagnostics.HasError() {
		return
	}

	pick := func(value types.String, env string) string {
		if !value.IsNull() && value.ValueString() != "" {
			return value.ValueString()
		}
		return os.Getenv(env)
	}
	jobTimeout, ok := parseDuration(&resp.Diagnostics, "job_timeout", config.JobTimeout, defaultJobTimeout)
	pollInterval, ok2 := parseDuration(&resp.Diagnostics, "job_poll_interval", config.JobPollInterval, defaultPollInterval)
	if !ok || !ok2 {
		return
	}

	client, err := noust.New(noust.Config{
		URL:       pick(config.URL, "NOUST_URL"),
		Token:     pick(config.Token, "NOUST_TOKEN"),
		CAFile:    pick(config.CAFile, "NOUST_CA_FILE"),
		Approval:  pick(config.Approval, "NOUST_APPROVAL"),
		UserAgent: "terraform-provider-noust/" + p.version,
		OnDeprecated: func(op noust.Op, header http.Header) {
			tflog.Warn(ctx, "The Noust API announces that an operation is going away", map[string]any{
				"operation": op.Name, "sunset": header.Get("Sunset"), "link": header.Get("Link"),
			})
		},
	})
	if err != nil {
		resp.Diagnostics.AddError("The provider is not configured", err.Error())
		return
	}
	// Prove the address is a Noust server and the token a live credential now, so a typo is one clear error
	// at the start of a plan and not a failure on the first resource.
	if err := client.Verify(ctx); err != nil {
		addError(&resp.Diagnostics, "Cannot sign in to the Noust server", err)
		return
	}
	data := &providerData{client: client, jobTimeout: jobTimeout, pollInterval: pollInterval}
	resp.ResourceData = data
	resp.DataSourceData = data
}

func parseDuration(diags interface {
	AddAttributeError(path.Path, string, string)
}, name string, value types.String, fallback time.Duration) (time.Duration, bool) {
	if value.IsNull() || value.IsUnknown() || value.ValueString() == "" {
		return fallback, true
	}
	parsed, err := time.ParseDuration(value.ValueString())
	if err != nil || parsed <= 0 {
		diags.AddAttributeError(path.Root(name), "Not a duration", name+" must be a Go duration such as 30m or 90s, got \""+value.ValueString()+"\".")
		return fallback, false
	}
	return parsed, true
}

func (p *noustProvider) Resources(context.Context) []func() resource.Resource {
	return []func() resource.Resource{
		NewApplicationResource,
		NewDomainResource,
		NewDatabaseResource,
		NewBackupScheduleResource,
		NewTeamResource,
		NewTeamMemberResource,
		NewProjectResource,
		NewEnvironmentResource,
		NewDeployKeyResource,
		NewRegistryLoginResource,
	}
}

func (p *noustProvider) DataSources(context.Context) []func() datasource.DataSource {
	return []func() datasource.DataSource{
		NewApplicationDataSource,
		NewApplicationsDataSource,
		NewServersDataSource,
		NewTeamDataSource,
	}
}
