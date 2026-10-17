// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"context"
	"encoding/json"
)

// Optional is a request field that is either left out (the server's default applies) or sent, possibly as
// null (the API reads null as "no limit" in a retention field). The zero value is "left out"; use
// with `json:",omitzero"`.
type Optional[T any] struct {
	set   bool
	value *T
}

// Some is a field sent with a value.
func Some[T any](value T) Optional[T] { return Optional[T]{set: true, value: &value} }

// Null is a field sent as null.
func Null[T any]() Optional[T] { return Optional[T]{set: true} }

// IsZero reports whether the field is left out; encoding/json's omitzero reads it.
func (o Optional[T]) IsZero() bool { return !o.set }

// MarshalJSON writes the value, or null.
func (o Optional[T]) MarshalJSON() ([]byte, error) {
	if o.value == nil {
		return []byte("null"), nil
	}
	return json.Marshal(*o.value)
}

// ScheduleDestination is a remote destination a schedule pushes each backup to, with the retention it
// keeps there.
type ScheduleDestination struct {
	Name           string   `json:"name"`
	RetentionCount *int     `json:"retention_count"`
	RetentionDays  *int     `json:"retention_days"`
	RetentionGB    *float64 `json:"retention_gb"`
}

// BackupSchedule is an application's scheduled backup.
type BackupSchedule struct {
	Domain           string                `json:"domain"`
	AppName          string                `json:"app_name"`
	Timer            string                `json:"timer"`
	Schedule         string                `json:"schedule"`
	OnCalendar       string                `json:"on_calendar"`
	RetentionCount   *int                  `json:"retention_count"`
	RetentionDays    *int                  `json:"retention_days"`
	RetentionGB      *float64              `json:"retention_gb"`
	IncludeDatabases bool                  `json:"include_databases"`
	Destinations     []ScheduleDestination `json:"destinations"`
}

// BackupScheduleRequest is the body of POST /api/backup-schedules and PUT /api/backup-schedules/{domain}.
// A retention field left out takes the API's default (7 backups, 30 days); one sent as null is "no limit
// of its own", which is a different thing, hence Optional.
type BackupScheduleRequest struct {
	Domain           string                `json:"domain"`
	Schedule         string                `json:"schedule"`
	RetentionCount   Optional[int]         `json:"retention_count,omitzero"`
	RetentionDays    Optional[int]         `json:"retention_days,omitzero"`
	RetentionGB      Optional[float64]     `json:"retention_gb,omitzero"`
	IncludeDatabases bool                  `json:"include_databases"`
	Destinations     []ScheduleDestination `json:"destinations"`
}

type backupScheduleList struct {
	Schedules []BackupSchedule `json:"schedules"`
}

type scheduleAction struct {
	Success  bool            `json:"success"`
	Message  string          `json:"message"`
	Schedule *BackupSchedule `json:"schedule"`
}

// GetBackupSchedule returns the schedule of an application, or nil when it has none. The API lists
// schedules and has no read of one, so the list is filtered here.
func (c *Client) GetBackupSchedule(ctx context.Context, domain string) (*BackupSchedule, error) {
	var out backupScheduleList
	if err := c.Do(ctx, ListBackupSchedules, Call{Out: &out}); err != nil {
		return nil, err
	}
	for i := range out.Schedules {
		if out.Schedules[i].Domain == domain {
			return &out.Schedules[i], nil
		}
	}
	return nil, nil
}

// CreateBackupSchedule schedules automatic backups of an application.
func (c *Client) CreateBackupSchedule(ctx context.Context, request BackupScheduleRequest) (*BackupSchedule, error) {
	return c.writeSchedule(ctx, CreateBackupSchedule, nil, request)
}

// UpdateBackupSchedule changes the schedule of an application.
func (c *Client) UpdateBackupSchedule(ctx context.Context, request BackupScheduleRequest) (*BackupSchedule, error) {
	return c.writeSchedule(ctx, UpdateBackupSchedule, []string{request.Domain}, request)
}

func (c *Client) writeSchedule(ctx context.Context, op Op, args []string, request BackupScheduleRequest) (*BackupSchedule, error) {
	if request.Destinations == nil {
		request.Destinations = []ScheduleDestination{}
	}
	var out scheduleAction
	if err := c.Do(ctx, op, Call{Args: args, Body: request, Out: &out}); err != nil {
		return nil, err
	}
	return out.Schedule, nil
}

// DeleteBackupSchedule removes the schedule of an application.
func (c *Client) DeleteBackupSchedule(ctx context.Context, domain string) error {
	return c.Do(ctx, DeleteBackupSchedule, Call{Args: []string{domain}})
}
