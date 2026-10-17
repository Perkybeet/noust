// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import "sort"

// ResourceOperations is which operations of the API each resource and data source of the provider calls.
// It is how the documentation can say, for every resource, the permissions a token needs and whether it
// must be issued with allow_elevated, and how a test knows no operation in the table is dead weight.
var ResourceOperations = map[string][]Op{
	"provider":                   {VerifyToken},
	"noust_application":          {CreateApp, GetJob, GetApp, GetHealth, GetAppEnv, MergeAppEnv, UpdateBranch, UpdateHealth, UpdateLimits, MoveApp, RestartApp, DeleteApp},
	"noust_domain":               {ListDomains, AddDomain, SetDomainPort, DeleteDomain, GetJob},
	"noust_database":             {CreateDatabase, GetDatabase, DropDatabase, GetJob},
	"noust_backup_schedule":      {ListBackupSchedules, CreateBackupSchedule, UpdateBackupSchedule, DeleteBackupSchedule},
	"noust_team":                 {CreateTeam, GetTeam, RenameTeam, DeleteTeam},
	"noust_team_member":          {AddTeamMember, ListTeamMembers, SetTeamMemberRole, RemoveTeamMember},
	"noust_project":              {CreateProject, ListProjects, RenameProject, DeleteProject},
	"noust_environment":          {CreateEnvironment, ListEnvironments, RenameEnvironment, DeleteEnvironment},
	"noust_team_variable":        {ListTeamVariables, SetTeamVariable, DeleteTeamVariable},
	"noust_project_variable":     {ListProjectVariables, SetProjectVariable, DeleteProjectVariable},
	"noust_environment_variable": {ListEnvironmentVariables, SetEnvironmentVariable, DeleteEnvironmentVariable},
	"noust_server_variable":      {ListServerVariables, SetServerVariable, DeleteServerVariable},
	"noust_deploy_key":           {CreateDeployKey, ImportDeployKey, GetDeployKey, DeleteDeployKey},
	"noust_registry_login":       {CreateRegistryLogin, GetRegistryLogin, ChangeRegistryLogin, DeleteRegistryLogin},

	"data.noust_application":  {GetApp},
	"data.noust_applications": {ListApps},
	"data.noust_servers":      {ListFleetServers},
	"data.noust_team":         {GetTeam},
}

// Needs is what a token must hold to use one resource or data source: the permissions of every operation
// it calls (sorted), and whether any of them asks for sudo mode, which a token enters only when it was
// issued with allow_elevated.
type Needs struct {
	Permissions []string `json:"permissions"`
	Sudo        bool     `json:"sudo"`
}

// NeedsOf returns what the named resource or data source needs.
func NeedsOf(name string) Needs {
	seen := map[string]bool{}
	needs := Needs{Permissions: []string{}}
	for _, op := range ResourceOperations[name] {
		if !seen[op.Permission] {
			seen[op.Permission] = true
			needs.Permissions = append(needs.Permissions, op.Permission)
		}
		needs.Sudo = needs.Sudo || op.Sudo
	}
	sort.Strings(needs.Permissions)
	return needs
}

// PermissionReport is permissions.json: what a token needs for each resource, and for the whole provider.
type PermissionReport struct {
	// All is the union over every resource: the permissions of a token that can run any configuration.
	All       []string         `json:"all"`
	Resources map[string]Needs `json:"resources"`
}

// Report computes the permission report from the operations table.
func Report() PermissionReport {
	report := PermissionReport{Resources: map[string]Needs{}}
	union := map[string]bool{}
	for name := range ResourceOperations {
		needs := NeedsOf(name)
		report.Resources[name] = needs
		for _, permission := range needs.Permissions {
			union[permission] = true
		}
	}
	report.All = make([]string, 0, len(union))
	for permission := range union {
		report.All = append(report.All, permission)
	}
	sort.Strings(report.All)
	return report
}
