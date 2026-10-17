// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"context"
	"net/url"
	"strconv"
)

// Database is a database as the databases area describes it.
type Database struct {
	Name          string   `json:"name"`
	Engine        string   `json:"engine"`
	Size          *string  `json:"size"`
	Tables        *int     `json:"tables"`
	Owner         *string  `json:"owner"`
	Encoding      *string  `json:"encoding"`
	Tracked       bool     `json:"tracked"`
	Missing       bool     `json:"missing"`
	App           *string  `json:"app"`
	Apps          []string `json:"apps"`
	Username      *string  `json:"username"`
	EngineVersion *string  `json:"engine_version"`
	Team          *string  `json:"team"`
	Project       *string  `json:"project"`
	Environment   *string  `json:"environment"`
}

// CreateDatabaseRequest is the body of POST /api/databases/databases.
type CreateDatabaseRequest struct {
	Name        string `json:"name"`
	Engine      string `json:"engine"`
	Owner       string `json:"owner,omitempty"`
	Encoding    string `json:"encoding,omitempty"`
	App         string `json:"app,omitempty"`
	Team        string `json:"team,omitempty"`
	Project     string `json:"project,omitempty"`
	Environment string `json:"environment,omitempty"`
}

// CreateDatabase creates a database on a running engine and records it in the store.
func (c *Client) CreateDatabase(ctx context.Context, request CreateDatabaseRequest) (*Database, error) {
	var out Database
	if err := c.Do(ctx, CreateDatabase, Call{Body: request, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetDatabase describes one database; the error satisfies IsNotFound when there is none.
func (c *Client) GetDatabase(ctx context.Context, engine, name string) (*Database, error) {
	var out Database
	if err := c.Do(ctx, GetDatabase, Call{Args: []string{engine, name}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// DropDatabaseOptions says how a database is dropped.
type DropDatabaseOptions struct {
	// KeepBackup dumps the database before dropping it.
	KeepBackup bool
	// Force disconnects open sessions first.
	Force bool
	// Unlink removes its variables from the applications using it.
	Unlink bool
}

// DropDatabase queues dropping a database.
func (c *Client) DropDatabase(ctx context.Context, engine, name string, options DropDatabaseOptions) (*JobAccepted, error) {
	query := url.Values{}
	query.Set("keep_backup", strconv.FormatBool(options.KeepBackup))
	query.Set("force", strconv.FormatBool(options.Force))
	query.Set("unlink", strconv.FormatBool(options.Unlink))
	var out JobAccepted
	if err := c.Do(ctx, DropDatabase, Call{Args: []string{engine, name}, Query: query, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}
