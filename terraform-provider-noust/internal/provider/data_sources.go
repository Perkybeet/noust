// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

var (
	_ datasource.DataSource              = (*applicationDataSource)(nil)
	_ datasource.DataSourceWithConfigure = (*applicationDataSource)(nil)
	_ datasource.DataSource              = (*applicationsDataSource)(nil)
	_ datasource.DataSourceWithConfigure = (*applicationsDataSource)(nil)
	_ datasource.DataSource              = (*serversDataSource)(nil)
	_ datasource.DataSourceWithConfigure = (*serversDataSource)(nil)
	_ datasource.DataSource              = (*teamDataSource)(nil)
	_ datasource.DataSourceWithConfigure = (*teamDataSource)(nil)
)

// NewApplicationDataSource is the noust_application data source.
func NewApplicationDataSource() datasource.DataSource { return &applicationDataSource{} }

// NewApplicationsDataSource is the noust_applications data source.
func NewApplicationsDataSource() datasource.DataSource { return &applicationsDataSource{} }

// NewServersDataSource is the noust_servers data source.
func NewServersDataSource() datasource.DataSource { return &serversDataSource{} }

// NewTeamDataSource is the noust_team data source.
func NewTeamDataSource() datasource.DataSource { return &teamDataSource{} }

// ---------------------------------------------------------------------------------------------------
// noust_application

type applicationDataSource struct{ data *providerData }

type applicationDataModel struct {
	ID                   types.String `tfsdk:"id"`
	Domain               types.String `tfsdk:"domain"`
	Status               types.String `tfsdk:"status"`
	Active               types.Bool   `tfsdk:"active"`
	Enabled              types.Bool   `tfsdk:"enabled"`
	AppType              types.String `tfsdk:"app_type"`
	Branch               types.String `tfsdk:"branch"`
	Source               types.String `tfsdk:"source"`
	Port                 types.Int64  `tfsdk:"port"`
	Layout               types.String `tfsdk:"layout"`
	Path                 types.String `tfsdk:"path"`
	Unit                 types.String `tfsdk:"unit"`
	RunAs                types.String `tfsdk:"run_as"`
	MemoryMaxMB          types.Int64  `tfsdk:"memory_max_mb"`
	CPUQuotaPercent      types.Int64  `tfsdk:"cpu_quota_percent"`
	TasksMax             types.Int64  `tfsdk:"tasks_max"`
	HealthPath           types.String `tfsdk:"health_path"`
	Team                 types.String `tfsdk:"team"`
	Project              types.String `tfsdk:"project"`
	Environment          types.String `tfsdk:"environment"`
	Tags                 types.List   `tfsdk:"tags"`
	LastDeploymentID     types.Int64  `tfsdk:"last_deployment_id"`
	LastDeploymentStatus types.String `tfsdk:"last_deployment_status"`
	LastCommit           types.String `tfsdk:"last_commit"`
}

func (d *applicationDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_application"
}

func readOnly(description string) schema.StringAttribute {
	return schema.StringAttribute{Computed: true, Description: description, MarkdownDescription: description}
}

func readOnlyInt(description string) schema.Int64Attribute {
	return schema.Int64Attribute{Computed: true, Description: description, MarkdownDescription: description}
}

func readOnlyBool(description string) schema.BoolAttribute {
	return schema.BoolAttribute{Computed: true, Description: description, MarkdownDescription: description}
}

func (d *applicationDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "An application that is already deployed: where it is, what it runs and in what state, to refer to its port, branch, team or status from other resources.",
		MarkdownDescription: "An application that is already deployed, however it got there: what it runs, where it belongs " +
			"and in what state, to refer to from other resources. Needs the permission `apps.read`.",
		Attributes: map[string]schema.Attribute{
			"id": readOnly("The application's domain."),
			"domain": schema.StringAttribute{
				Required:            true,
				Description:         "The application's primary domain.",
				MarkdownDescription: "The application's primary domain.",
			},
			"status":                 readOnly("The state of the application's unit: running, stopped, failed or static."),
			"active":                 readOnlyBool("Whether the unit is active."),
			"enabled":                readOnlyBool("Whether the unit starts at boot."),
			"app_type":               readOnly("The type of application."),
			"branch":                 readOnly("The branch it is pinned to, empty when any push deploys."),
			"source":                 readOnly("The address it is deployed from."),
			"port":                   readOnlyInt("The port it listens on."),
			"layout":                 readOnly("releases or inplace."),
			"path":                   readOnly("The application's directory on the server."),
			"unit":                   readOnly("The systemd unit that runs it."),
			"run_as":                 readOnly("The account it runs as."),
			"memory_max_mb":          readOnlyInt("MemoryMax in MB, empty when unlimited."),
			"cpu_quota_percent":      readOnlyInt("CPUQuota in percent of one CPU, empty when unlimited."),
			"tasks_max":              readOnlyInt("TasksMax, empty when unlimited."),
			"health_path":            readOnly("The path its health check probes, empty for the type's own."),
			"team":                   readOnly("The team it belongs to."),
			"project":                readOnly("The project it belongs to."),
			"environment":            readOnly("The environment it belongs to."),
			"last_deployment_id":     readOnlyInt("The newest deployment of its history."),
			"last_deployment_status": readOnly("The state of the newest deployment: success, failed, running..."),
			"last_commit":            readOnly("The git commit of the newest deployment."),
			"tags": schema.ListAttribute{
				Computed:            true,
				ElementType:         types.StringType,
				Description:         "Its tags.",
				MarkdownDescription: "Its tags.",
			},
		},
	}
}

func (d *applicationDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	d.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func (d *applicationDataSource) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	var config applicationDataModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)
	if resp.Diagnostics.HasError() {
		return
	}
	app, err := d.data.client.GetApp(ctx, config.Domain.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the application", err)
		return
	}
	tags := app.Tags
	if tags == nil {
		tags = []string{}
	}
	tagList, diags := types.ListValueFrom(ctx, types.StringType, tags)
	resp.Diagnostics.Append(diags...)
	model := applicationDataModel{
		ID:              types.StringValue(app.Domain),
		Domain:          types.StringValue(app.Domain),
		Status:          types.StringValue(app.Status),
		Active:          types.BoolValue(app.Active),
		Enabled:         types.BoolValue(app.Enabled),
		AppType:         stringFromPtr(app.AppType),
		Branch:          stringFromPtr(app.Branch),
		Source:          stringFromPtr(app.Source),
		Port:            int64FromPtr(app.Port),
		Layout:          types.StringValue(app.Layout),
		Path:            stringFromPtr(app.Path),
		Unit:            stringFromPtr(app.Unit),
		RunAs:           stringFromPtr(app.RunAs),
		MemoryMaxMB:     int64FromPtr(app.MemoryMaxMB),
		CPUQuotaPercent: int64FromPtr(app.CPUQuotaPercent),
		TasksMax:        int64FromPtr(app.TasksMax),
		HealthPath:      stringFromPtr(app.HealthPath),
		Team:            stringFromPtr(app.Team),
		Project:         stringFromPtr(app.Project),
		Environment:     stringFromPtr(app.Environment),
		Tags:            tagList,
	}
	if last := app.LastDeployment; last != nil {
		model.LastDeploymentID = types.Int64Value(int64(last.ID))
		model.LastDeploymentStatus = types.StringValue(last.Status)
		model.LastCommit = stringFromPtr(last.GitCommit)
	} else {
		model.LastDeploymentID = types.Int64Null()
		model.LastDeploymentStatus = types.StringNull()
		model.LastCommit = types.StringNull()
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &model)...)
}

// ---------------------------------------------------------------------------------------------------
// noust_applications

type applicationsDataSource struct{ data *providerData }

type applicationSummaryModel struct {
	Domain      types.String `tfsdk:"domain"`
	Status      types.String `tfsdk:"status"`
	AppType     types.String `tfsdk:"app_type"`
	Branch      types.String `tfsdk:"branch"`
	Port        types.Int64  `tfsdk:"port"`
	Team        types.String `tfsdk:"team"`
	Project     types.String `tfsdk:"project"`
	Environment types.String `tfsdk:"environment"`
}

type applicationsDataModel struct {
	ID          types.String              `tfsdk:"id"`
	Team        types.String              `tfsdk:"team"`
	Project     types.String              `tfsdk:"project"`
	Environment types.String              `tfsdk:"environment"`
	Tag         types.String              `tfsdk:"tag"`
	Apps        []applicationSummaryModel `tfsdk:"apps"`
}

func (d *applicationsDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_applications"
}

func filter(description string) schema.StringAttribute {
	return schema.StringAttribute{Optional: true, Description: description, MarkdownDescription: description}
}

func (d *applicationsDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "The applications the token sees, optionally of one team, project, environment or tag.",
		MarkdownDescription: "The applications the token sees, optionally of one team, project, environment or tag. A token " +
			"bound to a project sees that project's applications and nothing else. Needs the permission `apps.read`.",
		Attributes: map[string]schema.Attribute{
			"id":          readOnly("A constant, for Terraform."),
			"team":        filter("Only this team's applications."),
			"project":     filter("Only the projects with this slug."),
			"environment": filter("Only the environments with this slug."),
			"tag":         filter("Only the applications that carry this tag."),
			"apps": schema.ListNestedAttribute{
				Computed:            true,
				Description:         "The applications, ordered by domain.",
				MarkdownDescription: "The applications, ordered by domain.",
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"domain":      readOnly("The application's primary domain."),
						"status":      readOnly("The state of the application's unit."),
						"app_type":    readOnly("The type of application."),
						"branch":      readOnly("The branch it is pinned to."),
						"port":        readOnlyInt("The port it listens on."),
						"team":        readOnly("The team it belongs to."),
						"project":     readOnly("The project it belongs to."),
						"environment": readOnly("The environment it belongs to."),
					},
				},
			},
		},
	}
}

func (d *applicationsDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	d.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func (d *applicationsDataSource) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	var config applicationsDataModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)
	if resp.Diagnostics.HasError() {
		return
	}
	apps, err := d.data.client.ListApps(ctx, stringOf(config.Team), stringOf(config.Project), stringOf(config.Environment), stringOf(config.Tag))
	if err != nil {
		addError(&resp.Diagnostics, "Cannot list the applications", err)
		return
	}
	config.ID = types.StringValue("applications")
	config.Apps = make([]applicationSummaryModel, 0, len(apps))
	for _, app := range apps {
		config.Apps = append(config.Apps, applicationSummaryModel{
			Domain:      types.StringValue(app.Domain),
			Status:      types.StringValue(app.Status),
			AppType:     stringFromPtr(app.AppType),
			Branch:      stringFromPtr(app.Branch),
			Port:        int64FromPtr(app.Port),
			Team:        stringFromPtr(app.Team),
			Project:     stringFromPtr(app.Project),
			Environment: stringFromPtr(app.Environment),
		})
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &config)...)
}

// ---------------------------------------------------------------------------------------------------
// noust_servers

type serversDataSource struct{ data *providerData }

type serverModel struct {
	Name         types.String `tfsdk:"name"`
	Local        types.Bool   `tfsdk:"local"`
	Reachability types.String `tfsdk:"reachability"`
	Version      types.String `tfsdk:"version"`
	Labels       types.Map    `tfsdk:"labels"`
}

type serversDataModel struct {
	ID      types.String  `tfsdk:"id"`
	Servers []serverModel `tfsdk:"servers"`
}

func (d *serversDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_servers"
}

func (d *serversDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "The servers the Noust the provider talks to manages: itself, and the servers a central holds.",
		MarkdownDescription: "The servers the Noust the provider talks to manages: itself, and, on a central, the servers " +
			"enrolled in its fleet, with whether each answers and which version it runs. Needs the permission `fleet.read`.",
		Attributes: map[string]schema.Attribute{
			"id": readOnly("A constant, for Terraform."),
			"servers": schema.ListNestedAttribute{
				Computed:            true,
				Description:         "The servers, as the fleet view lists them.",
				MarkdownDescription: "The servers, as the fleet view lists them.",
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"name":         readOnly("The server's name in the fleet."),
						"local":        readOnlyBool("Whether it is the server the provider talks to."),
						"reachability": readOnly("reachable, unreachable or unknown."),
						"version":      readOnly("The Noust version it runs."),
						"labels": schema.MapAttribute{
							Computed:            true,
							ElementType:         types.StringType,
							Description:         "Its labels.",
							MarkdownDescription: "Its labels.",
						},
					},
				},
			},
		},
	}
}

func (d *serversDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	d.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func (d *serversDataSource) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	servers, err := d.data.client.ListFleetServers(ctx)
	if err != nil {
		addError(&resp.Diagnostics, "Cannot list the servers", err)
		return
	}
	model := serversDataModel{ID: types.StringValue("servers"), Servers: make([]serverModel, 0, len(servers))}
	for _, server := range servers {
		labels := server.Labels
		if labels == nil {
			labels = map[string]string{}
		}
		labelMap, diags := types.MapValueFrom(ctx, types.StringType, labels)
		resp.Diagnostics.Append(diags...)
		model.Servers = append(model.Servers, serverModel{
			Name:         types.StringValue(server.Name),
			Local:        types.BoolValue(server.Local),
			Reachability: types.StringValue(server.Reachability),
			Version:      stringFromPtr(server.Version),
			Labels:       labelMap,
		})
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &model)...)
}

// ---------------------------------------------------------------------------------------------------
// noust_team

type teamDataSource struct{ data *providerData }

type teamDataModel struct {
	ID       types.String `tfsdk:"id"`
	Slug     types.String `tfsdk:"slug"`
	Name     types.String `tfsdk:"name"`
	Members  types.Int64  `tfsdk:"members"`
	Projects types.Int64  `tfsdk:"projects"`
}

func (d *teamDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_team"
}

func (d *teamDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description:         "A team that already exists, to refer to from other resources.",
		MarkdownDescription: "A team that already exists, such as the `default` one every server has, to refer to from other resources. Needs the permission `teams.read`.",
		Attributes: map[string]schema.Attribute{
			"id": readOnly("The team's slug."),
			"slug": schema.StringAttribute{
				Required:            true,
				Description:         "The team's slug.",
				MarkdownDescription: "The team's slug.",
			},
			"name":     readOnly("The team's display name."),
			"members":  readOnlyInt("How many accounts are members of the team."),
			"projects": readOnlyInt("How many projects the team has."),
		},
	}
}

func (d *teamDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	d.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

func (d *teamDataSource) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	var config teamDataModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)
	if resp.Diagnostics.HasError() {
		return
	}
	team, err := d.data.client.GetTeam(ctx, config.Slug.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the team", err)
		return
	}
	config.ID = types.StringValue(team.Slug)
	config.Name = types.StringValue(team.Name)
	config.Members = types.Int64Value(int64(team.Members))
	config.Projects = types.Int64Value(int64(team.Projects))
	resp.Diagnostics.Append(resp.State.Set(ctx, &config)...)
}

var _ = noust.APIVersion
