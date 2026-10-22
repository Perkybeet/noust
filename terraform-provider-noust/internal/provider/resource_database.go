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
	_ resource.Resource                = (*databaseResource)(nil)
	_ resource.ResourceWithConfigure   = (*databaseResource)(nil)
	_ resource.ResourceWithImportState = (*databaseResource)(nil)
)

// NewDatabaseResource is the noust_database resource.
func NewDatabaseResource() resource.Resource { return &databaseResource{} }

type databaseResource struct{ data *providerData }

type databaseModel struct {
	ID                 types.String `tfsdk:"id"`
	Name               types.String `tfsdk:"name"`
	Engine             types.String `tfsdk:"engine"`
	Owner              types.String `tfsdk:"owner"`
	Encoding           types.String `tfsdk:"encoding"`
	App                types.String `tfsdk:"app"`
	Team               types.String `tfsdk:"team"`
	Project            types.String `tfsdk:"project"`
	Environment        types.String `tfsdk:"environment"`
	EngineVersion      types.String `tfsdk:"engine_version"`
	DumpBeforeDestroy  types.Bool   `tfsdk:"dump_before_destroy"`
	DisconnectOnDrop   types.Bool   `tfsdk:"disconnect_clients_on_destroy"`
	UnlinkAppsOnDrop   types.Bool   `tfsdk:"unlink_apps_on_destroy"`
	DeletionProtection types.Bool   `tfsdk:"deletion_protection"`
}

func (r *databaseResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_database"
}

func (r *databaseResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	keep := []planmodifier.String{keepString(), stringplanmodifier.RequiresReplace()}
	resp.Schema = schema.Schema{
		Description: "A database on one of the server's engines (PostgreSQL, MySQL/MariaDB, MongoDB...), owned by an account of its own.",
		MarkdownDescription: "A database on one of the server's running engines, created with an owner account of its " +
			"own. It belongs to the application, or the team, project and environment, you name.\n\n" +
			"**A database holds data.** Every attribute the server only takes when the database is made replaces it " +
			"when changed, which drops the data: put `lifecycle { prevent_destroy = true }` on production databases, " +
			"or set `deletion_protection`. Destroying a database dumps it first by default (`dump_before_destroy`), " +
			"and that dump is the server's last one.\n\n" +
			"Needs the permission `databases.write` to create and `databases.manage` with sudo mode to drop. " +
			"The connection string is not an attribute: an application created with `app` set receives it in its " +
			"environment, marked secret.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:            true,
				Description:         "The engine and the name, joined with a slash.",
				MarkdownDescription: "The engine and the name, joined with a slash: `engine/name`.",
				PlanModifiers:       []planmodifier.String{keepString()},
			},
			"name": schema.StringAttribute{
				Required:            true,
				Description:         "The database's name. Changing it replaces the database.",
				MarkdownDescription: "The database's name. Changing it replaces the database.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"engine": schema.StringAttribute{
				Required:            true,
				Description:         "The engine: postgresql, mysql, mongodb... as GET /api/databases/engines names it. Changing it replaces the database.",
				MarkdownDescription: "The engine: `postgresql`, `mysql`, `mongodb`... as `GET /api/databases/engines` names it. Changing it replaces the database.",
				PlanModifiers:       []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"owner": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The account that owns the database. Made by the server when omitted. Changing it replaces the database.",
				MarkdownDescription: "The account that owns the database. Made by the server when omitted. Changing it replaces the database.",
				PlanModifiers:       keep,
			},
			"encoding": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The character encoding. The engine's default when omitted. Changing it replaces the database.",
				MarkdownDescription: "The character encoding. The engine's default when omitted. Changing it replaces the database.",
				PlanModifiers:       keep,
			},
			"app": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The domain of the application the database belongs to. Left out, the database is not linked to one; one linked outside Terraform is kept. Changing it replaces the database.",
				MarkdownDescription: "The domain of the application the database belongs to. Left out, the database is not linked to one, and one linked outside Terraform is kept. Changing it replaces the database.",
				PlanModifiers:       keep,
			},
			"team": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The team that owns a database with no application. Changing it replaces the database.",
				MarkdownDescription: "The team that owns a database with no application. Changing it replaces the database.",
				PlanModifiers:       keep,
			},
			"project": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The project the database belongs to. Changing it replaces the database.",
				MarkdownDescription: "The project the database belongs to. Changing it replaces the database.",
				PlanModifiers:       keep,
			},
			"environment": schema.StringAttribute{
				Optional:            true,
				Computed:            true,
				Description:         "The environment the database belongs to. Changing it replaces the database.",
				MarkdownDescription: "The environment the database belongs to. Changing it replaces the database.",
				PlanModifiers:       keep,
			},
			"engine_version": schema.StringAttribute{
				Computed:            true,
				Description:         "The version of the engine the database runs on.",
				MarkdownDescription: "The version of the engine the database runs on.",
			},
			"dump_before_destroy": schema.BoolAttribute{
				Optional:            true,
				Description:         "Dump the database before dropping it, so the data can be restored. Default true.",
				MarkdownDescription: "Dump the database before dropping it, so the data can be restored. Default `true`.",
			},
			"disconnect_clients_on_destroy": schema.BoolAttribute{
				Optional:            true,
				Description:         "Disconnect open sessions before dropping the database. Default false.",
				MarkdownDescription: "Disconnect open sessions before dropping the database. Default `false`.",
			},
			"unlink_apps_on_destroy": schema.BoolAttribute{
				Optional:            true,
				Description:         "Remove the database's variables from the applications that use it when it is dropped. Default true when app is set, since they are the variables this database made for it, and false otherwise: the server refuses to drop a database an application uses.",
				MarkdownDescription: "Remove the database's variables from the applications that use it when it is dropped. Default `true` when `app` is set, since they are the variables this database made for it, and `false` otherwise: the server refuses to drop a database an application uses.",
			},
			"deletion_protection": schema.BoolAttribute{
				Optional:            true,
				Description:         "Refuse to destroy the database from Terraform until this is set to false and applied. Default false.",
				MarkdownDescription: "Refuse to destroy the database from Terraform until this is set to `false` and applied. Default `false`.",
			},
		},
	}
}

func (r *databaseResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.data = dataOf(req.ProviderData, &resp.Diagnostics)
}

// databaseInto copies what the API says about the database into the model.
func databaseInto(model *databaseModel, db *noust.Database) {
	model.ID = types.StringValue(db.Engine + "/" + db.Name)
	model.Name = types.StringValue(db.Name)
	model.Engine = types.StringValue(db.Engine)
	model.Owner = stringFromPtr(db.Owner)
	model.Encoding = stringFromPtr(db.Encoding)
	model.Team = stringFromPtr(db.Team)
	model.Project = stringFromPtr(db.Project)
	model.Environment = stringFromPtr(db.Environment)
	model.EngineVersion = stringFromPtr(db.EngineVersion)
	model.App = stringFromPtr(db.App)
}

func (r *databaseResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan databaseModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	db, err := r.data.client.CreateDatabase(ctx, noust.CreateDatabaseRequest{
		Name:        plan.Name.ValueString(),
		Engine:      plan.Engine.ValueString(),
		Owner:       stringOf(plan.Owner),
		Encoding:    stringOf(plan.Encoding),
		App:         stringOf(plan.App),
		Team:        stringOf(plan.Team),
		Project:     stringOf(plan.Project),
		Environment: stringOf(plan.Environment),
	})
	if err != nil {
		addError(&resp.Diagnostics, "Cannot create the database", err)
		return
	}
	databaseInto(&plan, db)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *databaseResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state databaseModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	db, err := r.data.client.GetDatabase(ctx, state.Engine.ValueString(), state.Name.ValueString())
	if noust.IsNotFound(err) || (err == nil && db.Missing) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the database", err)
		return
	}
	databaseInto(&state, db)
	resp.Diagnostics.Append(resp.State.Set(ctx, &state)...)
}

// Update changes only the options of destroying: everything the server holds replaces the database.
func (r *databaseResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan databaseModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	db, err := r.data.client.GetDatabase(ctx, plan.Engine.ValueString(), plan.Name.ValueString())
	if err != nil {
		addError(&resp.Diagnostics, "Cannot read the database back", err)
		return
	}
	databaseInto(&plan, db)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *databaseResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state databaseModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if boolOr(state.DeletionProtection, false) {
		resp.Diagnostics.AddError("The database is protected from deletion",
			"deletion_protection is true on noust_database."+state.Name.ValueString()+
				". Set it to false and apply that change first, then destroy.")
		return
	}
	accepted, err := r.data.client.DropDatabase(ctx, state.Engine.ValueString(), state.Name.ValueString(), noust.DropDatabaseOptions{
		KeepBackup: boolOr(state.DumpBeforeDestroy, true),
		Force:      boolOr(state.DisconnectOnDrop, false),
		// The variables a database made for its application go with it; a database somebody else linked
		// to an application is not Terraform's to unlink.
		Unlink: boolOr(state.UnlinkAppsOnDrop, stringOf(state.App) != ""),
	})
	if noust.IsNotFound(err) {
		return
	}
	if err != nil {
		addError(&resp.Diagnostics, "Cannot drop the database", err)
		return
	}
	if _, err := r.data.waitFor(ctx, accepted); err != nil {
		addError(&resp.Diagnostics, "The database was not dropped", err)
	}
}

func (r *databaseResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, ok := splitID(req.ID, 2)
	if !ok {
		importIDError(resp, req.ID, "engine/name")
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("engine"), parts[0])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("name"), parts[1])...)
}
