// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// APIError is the API's own refusal: every error of the API is {error, detail, hint, fields, output}, and
// nothing in it is paraphrased, because nginx's or systemd's words in Output are what an operator needs.
type APIError struct {
	// Operation is the provider's name for the call that was refused.
	Operation string
	// Status is the HTTP status.
	Status int
	// Code is the API's machine-readable error (elevation_required, permission_denied, not_found...).
	Code string
	// Detail says what is wrong.
	Detail string
	// Hint says what to do about it.
	Hint string
	// Fields names the request fields at fault, when the API says which.
	Fields map[string]any
	// Output is the verbatim output of the tool that failed (nginx, systemd, git...).
	Output string
	// RetryAfter is the number of seconds the server asked to wait, or zero.
	RetryAfter float64
}

// Error implements error.
func (e *APIError) Error() string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s: %d", e.Operation, e.Status)
	if e.Code != "" {
		fmt.Fprintf(&b, " %s", e.Code)
	}
	if e.Detail != "" {
		fmt.Fprintf(&b, ": %s", e.Detail)
	}
	return b.String()
}

// ElevationRequiredError is a call that asks for sudo mode from a token that cannot enter it.
type ElevationRequiredError struct{ *APIError }

// Unwrap lets errors.As find the underlying APIError.
func (e *ElevationRequiredError) Unwrap() error { return e.APIError }

// ApprovalRequiredError is a call the server turned into a request for a second person to approve (ENS
// four-eyes). The call did not run.
type ApprovalRequiredError struct {
	*APIError
	// ApprovalID names the request, as GET /api/approvals/{id} and X-Noust-Approval do.
	ApprovalID string
}

// Unwrap lets errors.As find the underlying APIError.
func (e *ApprovalRequiredError) Unwrap() error { return e.APIError }

// JobFailedError is a job that ended badly. Its Job carries the log and the error the API recorded (the
// health gate's probes and the journal, verbatim).
type JobFailedError struct {
	Job *Job
}

// Error implements error.
func (e *JobFailedError) Error() string {
	if e.Job == nil {
		return "the job failed"
	}
	message := e.Job.Error
	if message == "" {
		message = "the job ended " + e.Job.State
	}
	return fmt.Sprintf("job %s (%s) %s: %s", e.Job.ID, e.Job.Name, e.Job.State, message)
}

// AsAPIError returns the APIError inside err, whichever kind of refusal it is.
func AsAPIError(err error) (*APIError, bool) {
	var api *APIError
	if errors.As(err, &api) {
		return api, true
	}
	return nil, false
}

// IsNotFound reports whether err, or anything it wraps, is the API saying the object does not exist.
func IsNotFound(err error) bool {
	api, ok := AsAPIError(err)
	return ok && api.Status == 404
}

// errorBody is the API's error contract as it travels.
type errorBody struct {
	Error  json.RawMessage `json:"error"`
	Detail json.RawMessage `json:"detail"`
	Hint   *string         `json:"hint"`
	Fields map[string]any  `json:"fields"`
	Output *string         `json:"output"`
}

// parseAPIError decodes the API's error contract from a response body. A body that is not the contract
// (a proxy's page) becomes the detail as it came, shortened.
func parseAPIError(op Op, status int, body []byte, retryAfter float64) *APIError {
	e := &APIError{Operation: op.Name, Status: status, RetryAfter: retryAfter}
	var raw errorBody
	if err := json.Unmarshal(body, &raw); err != nil {
		text := strings.TrimSpace(string(body))
		if len(text) > 300 {
			text = text[:300] + "..."
		}
		e.Detail = text
		return e
	}
	// An error raised as an HTTP exception can carry the whole contract inside "detail".
	var nested errorBody
	if len(raw.Detail) > 0 && raw.Detail[0] == '{' && json.Unmarshal(raw.Detail, &nested) == nil && len(nested.Error) > 0 {
		raw = nested
	}
	_ = json.Unmarshal(raw.Error, &e.Code)
	if len(raw.Detail) > 0 {
		var text string
		if err := json.Unmarshal(raw.Detail, &text); err == nil {
			e.Detail = text
		} else {
			// FastAPI's own validation answer is a list; the API's wrapper normally turns it into
			// {fields}, but a body that still carries it is shown as it is.
			e.Detail = string(raw.Detail)
		}
	}
	if raw.Hint != nil {
		e.Hint = *raw.Hint
	}
	e.Fields = raw.Fields
	if raw.Output != nil {
		e.Output = *raw.Output
	}
	return e
}
