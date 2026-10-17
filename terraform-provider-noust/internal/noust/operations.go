// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"net/url"
	"strings"
)

// Op is one operation of the Noust API as the provider uses it. The table below is the only place the
// provider spells a route: operations_test.go holds every entry to panel/openapi.json (the path exists
// with that verb, the permission and the sudo-mode flag are the schema's), so a route that is renamed or
// made to need more fails a test here and not somebody's `terraform apply`.
type Op struct {
	// Name is how errors and logs call the operation.
	Name string
	// Method is the HTTP verb.
	Method string
	// Path is the canonical /api/... route, with {placeholders} filled in by Expand.
	Path string
	// Permission is the permission the API's x-noust-permission names.
	Permission string
	// Sudo is true when the API asks for sudo mode (x-noust-requires-elevation): a token enters it only
	// when it was issued with allow_elevated.
	Sudo bool
}

// Expand returns the route with its placeholders replaced, in order, by the escaped arguments.
func (o Op) Expand(args ...string) string {
	out := o.Path
	for _, arg := range args {
		start := strings.IndexByte(out, '{')
		end := strings.IndexByte(out, '}')
		if start < 0 || end < start {
			break
		}
		out = out[:start] + url.PathEscape(arg) + out[end+1:]
	}
	return out
}

// The operations of the API the provider calls, by area.
var (
	VerifyToken = Op{"VerifyToken", "GET", "/api/auth/verify", "self", false}
	GetJob      = Op{"GetJob", "GET", "/api/jobs/{job_id}", "apps.read", false}

	ListApps     = Op{"ListApps", "GET", "/api/apps", "apps.read", false}
	GetApp       = Op{"GetApp", "GET", "/api/apps/{domain}", "apps.read", false}
	CreateApp    = Op{"CreateApp", "POST", "/api/apps", "apps.manage", false}
	DeleteApp    = Op{"DeleteApp", "DELETE", "/api/apps/{domain}", "apps.manage", true}
	GetAppEnv    = Op{"GetAppEnv", "GET", "/api/apps/{domain}/env", "apps.read", false}
	MergeAppEnv  = Op{"MergeAppEnv", "PATCH", "/api/apps/{domain}/env", "apps.manage", true}
	UpdateBranch = Op{"UpdateBranch", "PATCH", "/api/apps/{domain}/branch", "apps.manage", true}
	GetHealth    = Op{"GetHealth", "GET", "/api/apps/{domain}/health", "apps.read", false}
	UpdateHealth = Op{"UpdateHealth", "PATCH", "/api/apps/{domain}/health", "apps.manage", true}
	UpdateLimits = Op{"UpdateLimits", "PATCH", "/api/apps/{domain}/limits", "apps.manage", true}
	MoveApp      = Op{"MoveApp", "POST", "/api/apps/{domain}/move", "apps.manage", true}
	RestartApp   = Op{"RestartApp", "POST", "/api/apps/{domain}/restart", "apps.operate", false}

	ListDomains   = Op{"ListDomains", "GET", "/api/apps/{domain}/domains", "apps.read", false}
	AddDomain     = Op{"AddDomain", "POST", "/api/apps/{domain}/domains", "apps.manage", false}
	DeleteDomain  = Op{"DeleteDomain", "DELETE", "/api/apps/{domain}/domains/{name}", "apps.manage", true}
	SetDomainPort = Op{"SetDomainPort", "PUT", "/api/apps/{domain}/domains/{name}/port", "apps.manage", false}

	CreateDatabase = Op{"CreateDatabase", "POST", "/api/databases/databases", "databases.write", false}
	GetDatabase    = Op{"GetDatabase", "GET", "/api/databases/databases/{engine}/{name}", "databases.read", false}
	DropDatabase   = Op{"DropDatabase", "DELETE", "/api/databases/databases/{engine}/{name}", "databases.manage", true}

	ListBackupSchedules  = Op{"ListBackupSchedules", "GET", "/api/backup-schedules", "backups.read", false}
	CreateBackupSchedule = Op{"CreateBackupSchedule", "POST", "/api/backup-schedules", "root_equivalent", true}
	UpdateBackupSchedule = Op{"UpdateBackupSchedule", "PUT", "/api/backup-schedules/{domain}", "root_equivalent", true}
	DeleteBackupSchedule = Op{"DeleteBackupSchedule", "DELETE", "/api/backup-schedules/{domain}", "backups.manage", true}

	CreateTeam        = Op{"CreateTeam", "POST", "/api/teams", "teams.manage", false}
	GetTeam           = Op{"GetTeam", "GET", "/api/teams/{team}", "teams.read", false}
	RenameTeam        = Op{"RenameTeam", "PATCH", "/api/teams/{team}", "teams.manage", false}
	DeleteTeam        = Op{"DeleteTeam", "DELETE", "/api/teams/{team}", "teams.manage", true}
	AddTeamMember     = Op{"AddTeamMember", "POST", "/api/teams/{team}/members", "teams.manage", true}
	ListTeamMembers   = Op{"ListTeamMembers", "GET", "/api/teams/{team}/members", "teams.read", false}
	SetTeamMemberRole = Op{"SetTeamMemberRole", "PATCH", "/api/teams/{team}/members/{username}", "teams.manage", true}
	RemoveTeamMember  = Op{"RemoveTeamMember", "DELETE", "/api/teams/{team}/members/{username}", "teams.manage", true}
	CreateProject     = Op{"CreateProject", "POST", "/api/teams/{team}/projects", "teams.manage", false}
	ListProjects      = Op{"ListProjects", "GET", "/api/teams/{team}/projects", "teams.read", false}
	RenameProject     = Op{"RenameProject", "PATCH", "/api/teams/{team}/projects/{project}", "teams.manage", false}
	DeleteProject     = Op{"DeleteProject", "DELETE", "/api/teams/{team}/projects/{project}", "teams.manage", true}
	CreateEnvironment = Op{"CreateEnvironment", "POST", "/api/teams/{team}/projects/{project}/environments", "teams.manage", false}
	ListEnvironments  = Op{"ListEnvironments", "GET", "/api/teams/{team}/projects/{project}/environments", "teams.read", false}
	RenameEnvironment = Op{"RenameEnvironment", "PATCH", "/api/teams/{team}/projects/{project}/environments/{environment}", "teams.manage", false}
	DeleteEnvironment = Op{"DeleteEnvironment", "DELETE", "/api/teams/{team}/projects/{project}/environments/{environment}", "teams.manage", true}

	CreateDeployKey = Op{"CreateDeployKey", "POST", "/api/apps/{domain}/deploy-key", "apps.manage", false}
	GetDeployKey    = Op{"GetDeployKey", "GET", "/api/apps/{domain}/deploy-key", "apps.read", false}
	ImportDeployKey = Op{"ImportDeployKey", "POST", "/api/apps/{domain}/deploy-key/import", "apps.manage", true}
	DeleteDeployKey = Op{"DeleteDeployKey", "DELETE", "/api/apps/{domain}/deploy-key", "apps.manage", true}

	CreateRegistryLogin = Op{"CreateRegistryLogin", "POST", "/api/registries", "apps.manage", true}
	GetRegistryLogin    = Op{"GetRegistryLogin", "GET", "/api/registries/{name}", "apps.read", false}
	ChangeRegistryLogin = Op{"ChangeRegistryLogin", "PATCH", "/api/registries/{name}", "apps.manage", true}
	DeleteRegistryLogin = Op{"DeleteRegistryLogin", "DELETE", "/api/registries/{name}", "apps.manage", true}

	ListServerVariables  = Op{"ListServerVariables", "GET", "/api/variables/server", "settings.read", false}
	SetServerVariable    = Op{"SetServerVariable", "PUT", "/api/variables/server/{name}", "settings.manage", true}
	DeleteServerVariable = Op{"DeleteServerVariable", "DELETE", "/api/variables/server/{name}", "settings.manage", true}

	ListTeamVariables  = Op{"ListTeamVariables", "GET", "/api/teams/{team}/variables", "apps.read", false}
	SetTeamVariable    = Op{"SetTeamVariable", "PUT", "/api/teams/{team}/variables/{name}", "apps.manage", true}
	DeleteTeamVariable = Op{"DeleteTeamVariable", "DELETE", "/api/teams/{team}/variables/{name}", "apps.manage", true}

	ListProjectVariables  = Op{"ListProjectVariables", "GET", "/api/teams/{team}/projects/{project}/variables", "apps.read", false}
	SetProjectVariable    = Op{"SetProjectVariable", "PUT", "/api/teams/{team}/projects/{project}/variables/{name}", "apps.manage", true}
	DeleteProjectVariable = Op{"DeleteProjectVariable", "DELETE", "/api/teams/{team}/projects/{project}/variables/{name}", "apps.manage", true}

	ListEnvironmentVariables  = Op{"ListEnvironmentVariables", "GET", "/api/teams/{team}/projects/{project}/environments/{environment}/variables", "apps.read", false}
	SetEnvironmentVariable    = Op{"SetEnvironmentVariable", "PUT", "/api/teams/{team}/projects/{project}/environments/{environment}/variables/{name}", "apps.manage", true}
	DeleteEnvironmentVariable = Op{"DeleteEnvironmentVariable", "DELETE", "/api/teams/{team}/projects/{project}/environments/{environment}/variables/{name}", "apps.manage", true}

	ListFleetServers = Op{"ListFleetServers", "GET", "/api/fleet/servers", "fleet.read", false}
)

// AllOperations lists every operation above, for the tests that hold the table to the API's schema and
// for the documentation of the permissions a token needs.
var AllOperations = []Op{
	VerifyToken, GetJob,
	ListApps, GetApp, CreateApp, DeleteApp, GetAppEnv, MergeAppEnv, UpdateBranch, GetHealth, UpdateHealth,
	UpdateLimits, MoveApp, RestartApp,
	ListDomains, AddDomain, DeleteDomain, SetDomainPort,
	CreateDatabase, GetDatabase, DropDatabase,
	ListBackupSchedules, CreateBackupSchedule, UpdateBackupSchedule, DeleteBackupSchedule,
	CreateTeam, GetTeam, RenameTeam, DeleteTeam, AddTeamMember, ListTeamMembers, SetTeamMemberRole,
	RemoveTeamMember, CreateProject, ListProjects, RenameProject,
	DeleteProject, CreateEnvironment, ListEnvironments, RenameEnvironment, DeleteEnvironment,
	CreateDeployKey, GetDeployKey, ImportDeployKey, DeleteDeployKey,
	CreateRegistryLogin, GetRegistryLogin, ChangeRegistryLogin, DeleteRegistryLogin,
	ListServerVariables, SetServerVariable, DeleteServerVariable,
	ListTeamVariables, SetTeamVariable, DeleteTeamVariable,
	ListProjectVariables, SetProjectVariable, DeleteProjectVariable,
	ListEnvironmentVariables, SetEnvironmentVariable, DeleteEnvironmentVariable,
	ListFleetServers,
}
