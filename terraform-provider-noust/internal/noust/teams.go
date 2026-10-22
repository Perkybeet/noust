// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import "context"

// Team is a team as creating and renaming it answer.
type Team struct {
	ID        int     `json:"id"`
	Slug      string  `json:"slug"`
	Name      string  `json:"name"`
	CreatedBy *string `json:"created_by"`
}

// TeamDetail is a team with what it holds, as GET /api/teams/{team} describes it.
type TeamDetail struct {
	Team
	Members   int            `json:"members"`
	Projects  int            `json:"projects"`
	Resources map[string]int `json:"resources"`
}

// Project is a project of a team.
type Project struct {
	ID           int      `json:"id"`
	Team         string   `json:"team"`
	Slug         string   `json:"slug"`
	Name         string   `json:"name"`
	Environments []string `json:"environments"`
}

// Environment is an environment of a project.
type Environment struct {
	ID      int    `json:"id"`
	Team    string `json:"team"`
	Project string `json:"project"`
	Slug    string `json:"slug"`
	Name    string `json:"name"`
}

type nameRequest struct {
	Name string `json:"name"`
	Slug string `json:"slug,omitempty"`
}

type renameRequest struct {
	Name string `json:"name"`
}

// CreateTeam creates a team, with a default project and its production environment.
func (c *Client) CreateTeam(ctx context.Context, name, slug string) (*Team, error) {
	var out Team
	if err := c.Do(ctx, CreateTeam, Call{Body: nameRequest{name, slug}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetTeam reads a team; the error satisfies IsNotFound when there is none.
func (c *Client) GetTeam(ctx context.Context, slug string) (*TeamDetail, error) {
	var out TeamDetail
	if err := c.Do(ctx, GetTeam, Call{Args: []string{slug}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// RenameTeam changes a team's display name. Its slug stays.
func (c *Client) RenameTeam(ctx context.Context, slug, name string) (*Team, error) {
	var out Team
	if err := c.Do(ctx, RenameTeam, Call{Args: []string{slug}, Body: renameRequest{name}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteTeam deletes a team that holds nothing.
func (c *Client) DeleteTeam(ctx context.Context, slug string) error {
	return c.Do(ctx, DeleteTeam, Call{Args: []string{slug}})
}

// CreateProject creates a project in a team, with its production environment.
func (c *Client) CreateProject(ctx context.Context, team, name, slug string) (*Project, error) {
	var out Project
	if err := c.Do(ctx, CreateProject, Call{Args: []string{team}, Body: nameRequest{name, slug}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetProject returns a project of a team, or nil when the team has none by that slug. The API lists a
// team's projects and has no read of one, so the list is filtered here; the error satisfies IsNotFound
// when the team itself is unknown.
func (c *Client) GetProject(ctx context.Context, team, slug string) (*Project, error) {
	var out struct {
		Projects []Project `json:"projects"`
	}
	if err := c.Do(ctx, ListProjects, Call{Args: []string{team}, Out: &out}); err != nil {
		return nil, err
	}
	for i := range out.Projects {
		if out.Projects[i].Slug == slug {
			return &out.Projects[i], nil
		}
	}
	return nil, nil
}

// RenameProject changes a project's display name. Its slug stays.
func (c *Client) RenameProject(ctx context.Context, team, slug, name string) (*Project, error) {
	var out Project
	if err := c.Do(ctx, RenameProject, Call{Args: []string{team, slug}, Body: renameRequest{name}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteProject deletes a project that holds nothing.
func (c *Client) DeleteProject(ctx context.Context, team, slug string) error {
	return c.Do(ctx, DeleteProject, Call{Args: []string{team, slug}})
}

// CreateEnvironment creates an environment in a project.
func (c *Client) CreateEnvironment(ctx context.Context, team, project, name, slug string) (*Environment, error) {
	var out Environment
	if err := c.Do(ctx, CreateEnvironment, Call{Args: []string{team, project}, Body: nameRequest{name, slug}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetEnvironment returns an environment of a project, or nil when the project has none by that slug.
func (c *Client) GetEnvironment(ctx context.Context, team, project, slug string) (*Environment, error) {
	var out struct {
		Environments []Environment `json:"environments"`
	}
	if err := c.Do(ctx, ListEnvironments, Call{Args: []string{team, project}, Out: &out}); err != nil {
		return nil, err
	}
	for i := range out.Environments {
		if out.Environments[i].Slug == slug {
			return &out.Environments[i], nil
		}
	}
	return nil, nil
}

// RenameEnvironment changes an environment's display name. Its slug stays.
func (c *Client) RenameEnvironment(ctx context.Context, team, project, slug, name string) (*Environment, error) {
	var out Environment
	if err := c.Do(ctx, RenameEnvironment, Call{Args: []string{team, project, slug}, Body: renameRequest{name}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteEnvironment deletes an environment that holds nothing.
func (c *Client) DeleteEnvironment(ctx context.Context, team, project, slug string) error {
	return c.Do(ctx, DeleteEnvironment, Call{Args: []string{team, project, slug}})
}

// Member is an account's membership of a team.
type Member struct {
	Username     string `json:"username"`
	Role         string `json:"role"`
	InstanceRole string `json:"instance_role"`
}

type addMemberRequest struct {
	Username string `json:"username"`
	Role     string `json:"role"`
}

type setRoleRequest struct {
	Role string `json:"role"`
}

// AddTeamMember adds an existing account to a team with a role, or changes its role there.
func (c *Client) AddTeamMember(ctx context.Context, team, username, role string) (*Member, error) {
	var out Member
	if err := c.Do(ctx, AddTeamMember, Call{Args: []string{team}, Body: addMemberRequest{username, role}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetTeamMember returns an account's membership of a team, or nil when it is not a member. The API lists a
// team's members and has no read of one, so the list is filtered here; the error satisfies IsNotFound when
// the team itself is unknown.
func (c *Client) GetTeamMember(ctx context.Context, team, username string) (*Member, error) {
	var out struct {
		Members []Member `json:"members"`
	}
	if err := c.Do(ctx, ListTeamMembers, Call{Args: []string{team}, Out: &out}); err != nil {
		return nil, err
	}
	for i := range out.Members {
		if out.Members[i].Username == username {
			return &out.Members[i], nil
		}
	}
	return nil, nil
}

// SetTeamMemberRole changes a member's role in a team.
func (c *Client) SetTeamMemberRole(ctx context.Context, team, username, role string) (*Member, error) {
	var out Member
	if err := c.Do(ctx, SetTeamMemberRole, Call{Args: []string{team, username}, Body: setRoleRequest{role}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// RemoveTeamMember takes an account out of a team. The server refuses to remove the team's last owner.
func (c *Client) RemoveTeamMember(ctx context.Context, team, username string) error {
	return c.Do(ctx, RemoveTeamMember, Call{Args: []string{team, username}})
}
