// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"context"
	"net/url"
	"sort"
	"strconv"
)

// App is an application as GET /api/apps/{domain} describes it.
type App struct {
	Name            string          `json:"name"`
	Domain          string          `json:"domain"`
	Status          string          `json:"status"`
	Active          bool            `json:"active"`
	Enabled         bool            `json:"enabled"`
	AppType         *string         `json:"app_type"`
	Branch          *string         `json:"branch"`
	Source          *string         `json:"source"`
	Port            *int            `json:"port"`
	Layout          string          `json:"layout"`
	Path            *string         `json:"path"`
	Unit            *string         `json:"unit"`
	RunAs           *string         `json:"run_as"`
	MemoryMaxMB     *int            `json:"memory_max_mb"`
	CPUQuotaPercent *int            `json:"cpu_quota_percent"`
	TasksMax        *int            `json:"tasks_max"`
	HealthPath      *string         `json:"health_path"`
	HealthExpect    *string         `json:"health_expect"`
	HealthTimeout   *int            `json:"health_timeout"`
	Team            *string         `json:"team"`
	Project         *string         `json:"project"`
	Environment     *string         `json:"environment"`
	Tags            []string        `json:"tags"`
	LastDeployment  *LastDeployment `json:"last_deployment"`
}

// LastDeployment is the newest row of an application's deployment history.
type LastDeployment struct {
	ID        int     `json:"id"`
	Status    string  `json:"status"`
	GitCommit *string `json:"git_commit"`
}

// AppList is the answer to GET /api/apps.
type AppList struct {
	Apps  []App `json:"apps"`
	Total int   `json:"total"`
}

// CreateAppRequest is the body of POST /api/apps. A field left at its zero value is left out, so the
// server's own default applies.
type CreateAppRequest struct {
	Domain          string            `json:"domain"`
	Source          string            `json:"source,omitempty"`
	Branch          string            `json:"branch,omitempty"`
	AppType         string            `json:"app_type,omitempty"`
	Port            *int              `json:"port,omitempty"`
	Layout          string            `json:"layout,omitempty"`
	Webserver       string            `json:"webserver,omitempty"`
	SSL             *bool             `json:"ssl,omitempty"`
	IncludeWWW      *bool             `json:"include_www,omitempty"`
	EnvVars         map[string]string `json:"env_vars,omitempty"`
	PersistentPaths []string          `json:"persistent_paths,omitempty"`
	HealthPath      string            `json:"health_path,omitempty"`
	HealthExpect    string            `json:"health_expect,omitempty"`
	HealthTimeout   *int              `json:"health_timeout,omitempty"`
	MemoryMaxMB     *int              `json:"memory_max_mb,omitempty"`
	CPUQuotaPercent *int              `json:"cpu_quota_percent,omitempty"`
	TasksMax        *int              `json:"tasks_max,omitempty"`
	Team            string            `json:"team,omitempty"`
	Project         string            `json:"project,omitempty"`
	Environment     string            `json:"environment,omitempty"`
}

// ListApps returns the applications the token sees, optionally narrowed to a team, project, environment or tag.
func (c *Client) ListApps(ctx context.Context, team, project, environment, tag string) ([]App, error) {
	query := url.Values{}
	for key, value := range map[string]string{"team": team, "project": project, "environment": environment, "tag": tag} {
		if value != "" {
			query.Set(key, value)
		}
	}
	var out AppList
	if err := c.Do(ctx, ListApps, Call{Query: query, Out: &out}); err != nil {
		return nil, err
	}
	sort.Slice(out.Apps, func(i, j int) bool { return out.Apps[i].Domain < out.Apps[j].Domain })
	return out.Apps, nil
}

// GetApp returns one application; the error satisfies IsNotFound when there is none.
func (c *Client) GetApp(ctx context.Context, domain string) (*App, error) {
	var app App
	if err := c.Do(ctx, GetApp, Call{Args: []string{domain}, Out: &app}); err != nil {
		return nil, err
	}
	return &app, nil
}

// CreateApp queues the deployment of a new application.
func (c *Client) CreateApp(ctx context.Context, request CreateAppRequest) (*JobAccepted, error) {
	var accepted JobAccepted
	if err := c.Do(ctx, CreateApp, Call{Body: request, Out: &accepted}); err != nil {
		return nil, err
	}
	return &accepted, nil
}

// DeleteAppOptions says what a deletion takes with the application.
type DeleteAppOptions struct {
	RemoveFiles bool
	RemoveSSL   bool
}

// DeleteApp queues the removal of an application.
func (c *Client) DeleteApp(ctx context.Context, domain string, options DeleteAppOptions) (*JobAccepted, error) {
	query := url.Values{}
	query.Set("remove_files", strconv.FormatBool(options.RemoveFiles))
	query.Set("remove_ssl", strconv.FormatBool(options.RemoveSSL))
	var accepted JobAccepted
	if err := c.Do(ctx, DeleteApp, Call{Args: []string{domain}, Query: query, Out: &accepted}); err != nil {
		return nil, err
	}
	return &accepted, nil
}

// AppEnv is an application's .env as the server reports it: values are redacted unless unmasked.
type AppEnv struct {
	Domain    string            `json:"domain"`
	Variables map[string]string `json:"variables"`
	Unmasked  bool              `json:"unmasked"`
}

// GetAppEnv reads the variables of an application. Their values are the server's redaction of them.
func (c *Client) GetAppEnv(ctx context.Context, domain string) (*AppEnv, error) {
	var env AppEnv
	if err := c.Do(ctx, GetAppEnv, Call{Args: []string{domain}, Out: &env}); err != nil {
		return nil, err
	}
	return &env, nil
}

// MergeEnvRequest is the body of PATCH /api/apps/{domain}/env.
type MergeEnvRequest struct {
	Set    map[string]string `json:"set,omitempty"`
	Unset  []string          `json:"unset,omitempty"`
	DryRun bool              `json:"dry_run"`
}

// MergeEnvResult names the variables the call changed (or would, on a dry run), never their values.
type MergeEnvResult struct {
	Domain          string   `json:"domain"`
	Changed         []string `json:"changed"`
	RestartRequired bool     `json:"restart_required"`
	DryRun          bool     `json:"dry_run"`
}

// MergeAppEnv sets and unsets some variables, keeping the others.
func (c *Client) MergeAppEnv(ctx context.Context, domain string, request MergeEnvRequest) (*MergeEnvResult, error) {
	var out MergeEnvResult
	if err := c.Do(ctx, MergeAppEnv, Call{Args: []string{domain}, Body: request, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// UpdateBranch pins the branch an application deploys from; an empty branch unpins it.
func (c *Client) UpdateBranch(ctx context.Context, domain, branch string) error {
	body := map[string]any{"branch": nil}
	if branch != "" {
		body["branch"] = branch
	}
	return c.Do(ctx, UpdateBranch, Call{Args: []string{domain}, Body: body})
}

// Health is an application's health check as GET /api/apps/{domain}/health describes it.
type Health struct {
	Kind        string  `json:"kind"`
	Path        *string `json:"path"`
	Expect      *string `json:"expect"`
	Timeout     *int    `json:"timeout"`
	Interval    *int    `json:"interval"`
	Retries     *int    `json:"retries"`
	StartPeriod *int    `json:"start_period"`
	Method      *string `json:"method"`
	Scheme      *string `json:"scheme"`
	Host        *string `json:"host"`
	Port        *int    `json:"port"`
}

// GetHealth reads the health check of an application.
func (c *Client) GetHealth(ctx context.Context, domain string) (*Health, error) {
	var out Health
	if err := c.Do(ctx, GetHealth, Call{Args: []string{domain}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// UpdateHealth replaces the health check of an application; a nil field is the server's default.
func (c *Client) UpdateHealth(ctx context.Context, domain string, health Health) error {
	if health.Kind == "" {
		health.Kind = "http"
	}
	return c.Do(ctx, UpdateHealth, Call{Args: []string{domain}, Body: health})
}

// Limits are the resource limits of an application's unit; nil is no limit.
type Limits struct {
	MemoryMaxMB     *int `json:"memory_max_mb"`
	CPUQuotaPercent *int `json:"cpu_quota_percent"`
	TasksMax        *int `json:"tasks_max"`
}

// UpdateLimits sets the limits of an application's unit, restarting it when asked.
func (c *Client) UpdateLimits(ctx context.Context, domain string, limits Limits, restart bool) error {
	body := struct {
		Limits
		Restart bool `json:"restart"`
	}{limits, restart}
	return c.Do(ctx, UpdateLimits, Call{Args: []string{domain}, Body: body})
}

// MoveApp moves an application, with its sites, databases and backup schedule, to another team, project
// or environment. An empty field stays as it is.
func (c *Client) MoveApp(ctx context.Context, domain, team, project, environment string) error {
	body := map[string]string{}
	for key, value := range map[string]string{"team": team, "project": project, "environment": environment} {
		if value != "" {
			body[key] = value
		}
	}
	return c.Do(ctx, MoveApp, Call{Args: []string{domain}, Body: body})
}

// RestartApp restarts an application.
func (c *Client) RestartApp(ctx context.Context, domain string) error {
	return c.Do(ctx, RestartApp, Call{Args: []string{domain}})
}
