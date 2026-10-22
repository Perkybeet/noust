// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"context"
	"fmt"
	"time"
)

// Job is a unit of work the server queued (a deployment, a removal, a certificate). The state is one of
// queued, running, succeeded, failed or cancelled.
type Job struct {
	ID          string         `json:"id"`
	Type        string         `json:"type"`
	Name        string         `json:"name"`
	Description string         `json:"description"`
	State       string         `json:"state"`
	Error       string         `json:"error"`
	Result      map[string]any `json:"result"`
	Logs        []JobLogLine   `json:"logs"`
}

// JobLogLine is one line of a job's log.
type JobLogLine struct {
	Level   string `json:"level"`
	Message string `json:"message"`
}

// JobAccepted is the answer to a call that queued a job (HTTP 202).
type JobAccepted struct {
	JobID   string `json:"job_id"`
	Status  string `json:"status"`
	Message string `json:"message"`
}

// Finished reports whether the job has ended, well or badly.
func (j *Job) Finished() bool {
	switch j.State {
	case "succeeded", "failed", "cancelled":
		return true
	}
	return false
}

// JobTimeoutError is a wait that ran out. The job keeps running on the server.
type JobTimeoutError struct {
	Job     *Job
	Timeout time.Duration
}

// Error implements error.
func (e *JobTimeoutError) Error() string {
	return fmt.Sprintf("job %s (%s) was still %s after %s; it keeps running on the server: follow it with GET /api/jobs/%s, or raise job_timeout in the provider block", e.Job.ID, e.Job.Name, e.Job.State, e.Timeout, e.Job.ID)
}

// WaitForJob follows a job until it ends. A job that failed or was cancelled is a *JobFailedError carrying
// the job, so the error the user reads is the server's (the health gate's probes and the journal), word
// for word.
func (c *Client) WaitForJob(ctx context.Context, accepted *JobAccepted, interval, timeout time.Duration) (*Job, error) {
	if accepted == nil || accepted.JobID == "" {
		return nil, fmt.Errorf("the server queued no job: nothing to wait for")
	}
	if interval <= 0 {
		interval = 2 * time.Second
	}
	deadline := time.Now().Add(timeout)
	for {
		var job Job
		if err := c.Do(ctx, GetJob, Call{Args: []string{accepted.JobID}, Out: &job}); err != nil {
			return nil, err
		}
		if job.Finished() {
			if job.State != "succeeded" {
				return &job, &JobFailedError{Job: &job}
			}
			return &job, nil
		}
		if timeout > 0 && time.Now().After(deadline) {
			return &job, &JobTimeoutError{Job: &job, Timeout: timeout}
		}
		select {
		case <-ctx.Done():
			return &job, ctx.Err()
		case <-time.After(interval):
		}
	}
}
