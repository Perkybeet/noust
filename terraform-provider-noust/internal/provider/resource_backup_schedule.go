// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework-validators/float64validator"
	"github.com/hashicorp/terraform-plugin-framework-validators/int64validator"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/int64default"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/Perkybeet/terraform-provider-noust/internal/noust"
)

var (
	_ resource.Resource                = (*backupScheduleResource)(nil)
	_ resource.ResourceWithConfigure   = (*backupScheduleResource)(nil)
	_ resource.ResourceWithImportState = (*backupScheduleResource)(nil)
)

// NewBackupScheduleResource is the noust_backup_schedule resource.
func NewBackupScheduleResource() resource.Resource { return &backupScheduleResource{} }

type backupScheduleResource struct{ data *providerData }

type destinationModel struct {
	Name           types.String  `tfsdk:"name"`
	RetentionCount types.Int64   `tfsdk:"retention_count"`
	RetentionDays  types.Int64   `tfsdk:"retention_days"`
	RetentionGB    types.Float64 `tfsdk:"retention_gb"`
}

type backupScheduleModel struct {
	ID               types.String       `tfsdk:"id"`
	App              types.String       `tfsdk:"app"`
	Schedule         types.String       `tfsdk:"schedule"`
	RetentionCount   types.Int64        `tfsdk:"retention_count"`
	RetentionDays    types.Int64        `tfsdk:"retention_days"`
	RetentionGB      types.Float64      `tfsdk:"retention_gb"`
	IncludeDatabases types.Bool         `tfsdk:"include_databases"`
	Destinations     []destinationModel `tfsdk:"destinations"`
	Timer            types.String       `tfsdk:"timer"`
	OnCalendar       types.String       `tfsdk:"on_calendar"`
}

func (r *backupScheduleResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_backup_schedule"
}

func (r *backupScheduleResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "The scheduled backups of an application, run by a systemd timer on the server, with their retention and the remote destinations each backup is copied to.",
		MarkdownDescription: "The scheduled backups of an application, run by a systemd timer on the server, with their " +
			"retention and the remote destinations each backup is copied to. An application has at most one schedule.\n\n" +
			"A schedule is a root timer and its retention decides which backups are thrown away, so creating or " +
			"changing one needs the permission `root_equivalent` and sudo mode (and a second person's approval on a " +
			"server that asks for it); removing one needs `backups.manage` and sudo mode. Removing it stops future " +
			"backups; the backups already made stay.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The application's domain.",
				MarkdownDescription: "The application's domain.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"app": schema.StringAttribute{
				Required:            true,
				Description:         "The domain of the application to back up. Changing it replaces the schedule.",
				MarkdownDescription: "The domain of the application to back up. Changing it replaces the schedule.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"schedule": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "hourly, daily, weekly, monthly or a systemd OnCalendar expression such as *-*-* 03:30:00. Default daily.",
				MarkdownDescription: "`hourly`, `daily`, `weekly`, `monthly` or a systemd `OnCalendar` expression such as `*-*-* 03:30:00`. Default `daily`.",
				Default:             stringdefault.StaticString("daily"),
			},
			"retention_count": schema.Int64Attribute{
				Optional:            true,
				Computed:            true,
				Description:         "How many of the backups this schedule made to keep. Default 7. 0 leaves the server's backup.max_per_app in charge.",
				MarkdownDescription: "How many of the backups this schedule made to keep. Default `7`. `0` leaves the server's `backup.max_per_app` in charge, over every backup of the application.",
				Validators:          []validator.Int64{int64validator.Between(0, 365)},
				Default:             int64default.StaticInt64(7),
			},
			"retention_days": schema.Int64Attribute{
				Optional:            true,
				Computed:            true,
				Description:         "The age in days after which a backup this schedule made is thrown away. Default 30. 0 is no age limit.",
				MarkdownDescription: "The age in days after which a backup this schedule made is thrown away. Default `30`. `0` is no age limit.",
				Validators:          []validator.Int64{int64validator.Between(0, 3650)},
				Default:             int64default.StaticInt64(30),
			},
			"retention_gb": schema.Float64Attribute{
				Optional:            true,
				Description:         "The most the backups this schedule made may weigh together, in gigabytes. The newest always stays. No limit when omitted.",
				MarkdownDescription: "The most the backups this schedule made may weigh together, in gigabytes. The newest always stays. No limit when omitted.",
				Validators:          []validator.Float64{float64validator.AtLeast(0.001)},
			},
			"include_databases": schema.BoolAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "Dump the application's databases as well. Default true.",
				MarkdownDescription: "Dump the application's databases as well. Default `true`.",
				Default:             booldefault.StaticBool(true),
			},
			"destinations": schema.ListNestedAttribute{
				Optional:            true,
				Description:         "Remote destinations (created under /api/backup-destinations) each backup is copied to, with the retention kept there.",
				MarkdownDescription: "Remote destinations (created in the console's backup destinations) each backup is copied to, with the retention kept there.",
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"name": schema.StringAttribute{
							Required:            true,
							Description:         "The destination's name.",
							MarkdownDescription: "The destination's name.",
						},
						"retention_count": schema.Int64Attribute{
							Optional:            true,
							Description:         "How many backups to keep there.",
							MarkdownDescription: "How many backups to keep there.",
							Validators:          []validator.Int64{int64validator.Between(1, 365)},
						},
						"retention_days": schema.Int64Attribute{
							Optional:            true,
							Description:         "How many days to keep a backup there.",
							MarkdownDescription: "How many days to keep a backup there.",
							Validators:          []validator.Int64{int64validator.Between(1, 3650)},
						},
						"retention_gb": schema.Float64Attribute{
							Optional:            true,
							Description:         "The most the backups of this application may weigh there, in gigabytes.",
							MarkdownDescription: "The most the backups of this application may weigh there, in gigabytes.",
							Validators:          []validator.Float64{float64validator.AtLeast(0.001)},
						},
					},
				},
			},
			"timer": schema.StringAttribute{
				Computed:            true,
				Description:         "The name of the systemd timer.",
				MarkdownDescription: "The name of the systemd timer.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"on_calendar": schema.StringAttribute{
				Computed:            true,
				Description:         "The calendar expression the timer runs on.",
				MarkdownDescription: "The calendar expression the timer runs on.",
			},
		},
	}
}

func (r *backupScheduleResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

// retentionRequest turns a retention setting into a request field: left out when it is not set (the
// server's default), null for 0 (no limit of its own), the number otherwise.
func retentionRequest(value types.Int64) noust.Optional[int] {
	if !known(value) {
		return noust.Optional[int]{}
	}
	if value.ValueInt64() == 0 {
		return noust.Null[int]()
	}
	return noust.Some(int(value.ValueInt64()))
}

func scheduleRequest(plan *backupScheduleModel) noust.BackupScheduleRequest {
	request := noust.BackupScheduleRequest{
		Domain:           plan.App.ValueString(),
		Schedule:         stringOf(plan.Schedule),
		RetentionCount:   retentionRequest(plan.RetentionCount),
		RetentionDays:    retentionRequest(plan.RetentionDays),
		IncludeDatabases: boolOr(plan.IncludeDatabases, true),
	}
	if request.Schedule == "" {
		request.Schedule = "daily"
	}
	if gb := floatPtrOf(plan.RetentionGB); gb != nil {
		request.RetentionGB = noust.Some(*gb)
	}
	for _, destination := range plan.Destinations {
		request.Destinations = append(request.Destinations, noust.ScheduleDestination{
			Name:           destination.Name.ValueString(),
			RetentionCount: intPtrOf(destination.RetentionCount),
			RetentionDays:  intPtrOf(destination.RetentionDays),
			RetentionGB:    floatPtrOf(destination.RetentionGB),
		})
	}
	return request
}

// scheduleInto copies what the API says about the schedule into the model.
func scheduleInto(model *backupScheduleModel, schedule *noust.BackupSchedule) {
	model.ID = types.StringValue(schedule.Domain)
	model.App = types.StringValue(schedule.Domain)
	model.Schedule = types.StringValue(scheduleName(schedule, model))
	model.Timer = types.StringValue(schedule.Timer)
	model.OnCalendar = types.StringValue(schedule.OnCalendar)
	model.IncludeDatabases = types.BoolValue(schedule.IncludeDatabases)
	model.RetentionCount = zeroIfNil(schedule.RetentionCount)
	model.RetentionDays = zeroIfNil(schedule.RetentionDays)
	model.RetentionGB = floatFromPtr(schedule.RetentionGB)
	if len(schedule.Destinations) == 0 {
		// An empty list and an omitted one are the same schedule; keep what the configuration wrote.
		if len(model.Destinations) > 0 {
			model.Destinations = nil
		}
		return
	}
	model.Destinations = make([]destinationModel, 0, len(schedule.Destinations))
	for _, destination := range schedule.Destinations {
		model.Destinations = append(model.Destinations, destinationModel{
			Name:           types.StringValue(destination.Name),
			RetentionCount: int64FromPtr(destination.RetentionCount),
			RetentionDays:  int64FromPtr(destination.RetentionDays),
			RetentionGB:    floatFromPtr(destination.RetentionGB),
		})
	}
}

// scheduleName is the schedule as the configuration writes it. The API names a calendar expression
// "custom" and keeps the expression in on_calendar, so an expression stays what the configuration wrote
// for as long as the timer still runs on it, and becomes what the timer runs on when it was changed.
func scheduleName(schedule *noust.BackupSchedule, model *backupScheduleModel) string {
	if schedule.Schedule != "custom" {
		return schedule.Schedule
	}
	if known(model.Schedule) && known(model.OnCalendar) && model.OnCalendar.ValueString() == schedule.OnCalendar {
		return model.Schedule.ValueString()
	}
	if known(model.Schedule) && !known(model.OnCalendar) && model.Schedule.ValueString() != "" {
		// Just written: the plan holds the expression and the timer has not been read before.
		return model.Schedule.ValueString()
	}
	return schedule.OnCalendar
}

// zeroIfNil reads the API's null retention as 0, which the schema documents as "no limit of its own".
func zeroIfNil(value *int) types.Int64 {
	if value == nil {
		return types.Int64Value(0)
	}
	return types.Int64Value(int64(*value))
}

func (r *backupScheduleResource) write(ctx context.Context, plan *backupScheduleModel, update bool) (*noust.BackupSchedule, error) {
	request := scheduleRequest(plan)
	if update {
		return r.data.client.UpdateBackupSchedule(ctx, request)
	}
	return r.data.client.CreateBackupSchedule(ctx, request)
}

func (r *backupScheduleResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan backupScheduleModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	created, err := r.write(ctx, &plan, false)
	if err != nil {
		addError(&resp.Diagnostics, "Cannot schedule the backups", err)
		return
	}
	if created == nil {
		// The server created it but did not echo it: read it back.
		created, err = r.data.client.GetBackupSchedule(ctx, plan.App.ValueString())
		if err != nil || created == nil {
			resp.Diagnostics.AddError("The schedule is not there after it was created", "Check GET /api/backup-schedules.")
			return
		}
	}
	scheduleInto(&plan, created)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *backupScheduleResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state backupScheduleModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	schedule, err := r.data.client.GetBackupSchedule(ctx, state.App.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the backup schedule", err)
		return
	}
	if schedule == nil {
		resp.State.RemoveResource(ctx)
		return
	}
	scheduleInto(&state, schedule)
	resp.Diagnostics.Append(resp.State.Set(ctx, &state)...)
}

func (r *backupScheduleResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan backupScheduleModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	updated, err := r.write(ctx, &plan, true)
	if err != nil {
		addError(&resp.Diagnostics, "Cannot change the backup schedule", err)
		return
	}
	if updated == nil {
		updated, err = r.data.client.GetBackupSchedule(ctx, plan.App.ValueString())
		if err != nil || updated == nil {
			resp.Diagnostics.AddError("The schedule is not there after it was changed", "Check GET /api/backup-schedules.")
			return
		}
	}
	scheduleInto(&plan, updated)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *backupScheduleResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state backupScheduleModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.data.client.DeleteBackupSchedule(ctx, state.App.ValueString()); err != nil && !noust.IsNotFound(err) {
		addError(&resp.Diagnostics, "Cannot remove the backup schedule", err)
	}
}

func (r *backupScheduleResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("app"), req.ID)...)
}
