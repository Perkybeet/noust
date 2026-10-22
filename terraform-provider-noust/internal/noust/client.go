// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

// Package noust is the provider's client of the Noust HTTP API. It is the only code that speaks HTTP:
// the resources translate Terraform to a call here and back, and implement nothing a Noust server does.
package noust

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	// APIVersion is the version of the API this provider is written for. A server that answers another
	// is refused: the compatibility promise (docs/api.md) holds inside one version only.
	APIVersion = "1"
	// VersionHeader is the header every answer under /api carries.
	VersionHeader = "X-Noust-API-Version"
	// ApprovalHeader names the approved request a call is repeated with.
	ApprovalHeader = "X-Noust-Approval"

	defaultRequestTimeout = 60 * time.Second
	maxRetryWait          = 30 * time.Second
	maxReadAttempts       = 3
	// maxBusyAttempts bounds how long a change waits for another operation on the same application: the
	// waits are 1, 2, 4, 8 and then 10 seconds, about two minutes in all.
	maxBusyAttempts = 14
)

// Config is how a Client is made.
type Config struct {
	// URL is the console's address, such as https://noust.example.com.
	URL string
	// Token is an API token with the permissions the configuration needs. Never the master token.
	Token string
	// CAFile is a PEM bundle to trust instead of the system's, for a console on a private certificate.
	CAFile string
	// Approval is the id of an approved request the next call is made with. Optional.
	Approval string
	// UserAgent identifies the provider's version to the server's audit trail.
	UserAgent string
	// HTTPClient replaces the network, for tests.
	HTTPClient *http.Client
	// Sleep replaces time.Sleep between the repetitions of a read, for tests.
	Sleep func(time.Duration)
	// OnDeprecated is called when an answer announces its operation is going away (Deprecation and Sunset
	// headers), so the provider can say so in its log. Optional.
	OnDeprecated func(op Op, header http.Header)
}

// Client talks to one Noust server.
type Client struct {
	base      string
	token     string
	approval  string
	userAgent string
	http      *http.Client
	sleep     func(time.Duration)
	notice    func(op Op, header http.Header)
}

// loopback reports whether host names this machine, the only place a token may cross in clear.
func loopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// New makes a Client.
//
// The token is sent in an Authorization header and nowhere else, plain HTTP is refused for any host but
// loopback (the token would cross the network in clear), and a redirect is an error rather than a place to
// send the token.
func New(cfg Config) (*Client, error) {
	raw := strings.TrimSpace(cfg.URL)
	if raw == "" {
		return nil, errors.New("the Noust address is empty: set url in the provider block or NOUST_URL")
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Hostname() == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, fmt.Errorf("%q is not an http(s) address", raw)
	}
	if parsed.Scheme == "http" && !loopback(parsed.Hostname()) {
		return nil, fmt.Errorf("the token would cross the network in clear: use https:// for %s (plain http is only for localhost, 127.0.0.1 and ::1)", parsed.Hostname())
	}
	if strings.TrimSpace(cfg.Token) == "" {
		return nil, errors.New("the API token is empty: set token in the provider block or NOUST_TOKEN")
	}
	httpClient := cfg.HTTPClient
	if httpClient == nil {
		transport := http.DefaultTransport.(*http.Transport).Clone()
		if cfg.CAFile != "" {
			pem, readErr := os.ReadFile(cfg.CAFile)
			if readErr != nil {
				return nil, fmt.Errorf("cannot read ca_file: %w", readErr)
			}
			pool := x509.NewCertPool()
			if !pool.AppendCertsFromPEM(pem) {
				return nil, fmt.Errorf("ca_file %s holds no PEM certificate", cfg.CAFile)
			}
			transport.TLSClientConfig = &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
		}
		httpClient = &http.Client{
			Transport: transport,
			Timeout:   defaultRequestTimeout,
			// A redirect would carry the token to wherever it points.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		}
	}
	sleep := cfg.Sleep
	if sleep == nil {
		sleep = time.Sleep
	}
	agent := cfg.UserAgent
	if agent == "" {
		agent = "terraform-provider-noust"
	}
	return &Client{
		base:      parsed.Scheme + "://" + parsed.Host,
		token:     cfg.Token,
		approval:  cfg.Approval,
		userAgent: agent,
		http:      httpClient,
		sleep:     sleep,
		notice:    cfg.OnDeprecated,
	}, nil
}

// Call is one request of an operation.
type Call struct {
	// Args fill the route's {placeholders}, in order.
	Args []string
	// Query holds the query parameters.
	Query url.Values
	// Body is encoded as JSON when it is not nil.
	Body any
	// Out receives the decoded answer when it is not nil.
	Out any
}

// Do makes one request.
//
// A read is repeated after a 429 or a gateway error, waiting what the server asked; a change is never
// repeated after those, since it may have been made. The one refusal a change is repeated after is
// "app_busy": the server answers it before it does anything, because only one deploy, update or domain
// change runs on an application at a time, and Terraform applies the resources of one application in
// parallel. Anything but a 2xx is an *APIError, with the two refusals a caller can act on in their own
// type: *ElevationRequiredError and *ApprovalRequiredError.
func (c *Client) Do(ctx context.Context, op Op, call Call) error {
	path := "/api/v1" + strings.TrimPrefix(op.Expand(call.Args...), "/api")
	target := c.base + path
	if len(call.Query) > 0 {
		target += "?" + call.Query.Encode()
	}
	var payload []byte
	if call.Body != nil {
		encoded, err := json.Marshal(call.Body)
		if err != nil {
			return fmt.Errorf("%s: cannot encode the request: %w", op.Name, err)
		}
		payload = encoded
	}
	attempts := 1
	if op.Method == http.MethodGet {
		attempts = maxReadAttempts
	}
	busy := 0
	for attempt := 1; ; attempt++ {
		status, header, body, err := c.send(ctx, op, target, payload)
		if err != nil {
			return err
		}
		if attempt < attempts && retryable(status) {
			wait := retryDelay(header, attempt)
			c.sleep(wait)
			if ctx.Err() != nil {
				return ctx.Err()
			}
			continue
		}
		if status == http.StatusConflict && busy < maxBusyAttempts && parseAPIError(op, status, body, 0).Code == "app_busy" {
			busy++
			c.sleep(min(time.Duration(1<<min(busy-1, 4))*time.Second, 10*time.Second))
			if ctx.Err() != nil {
				return ctx.Err()
			}
			// Waiting for another operation does not use up the attempts a read has for gateway errors.
			attempt--
			continue
		}
		return c.finish(op, call, status, header, body)
	}
}

func retryable(status int) bool {
	return status == 429 || status == 502 || status == 503 || status == 504
}

func retryDelay(header http.Header, attempt int) time.Duration {
	if seconds, err := strconv.ParseFloat(header.Get("Retry-After"), 64); err == nil && seconds >= 0 {
		return min(time.Duration(seconds*float64(time.Second)), maxRetryWait)
	}
	return min(time.Duration(1<<attempt)*time.Second, maxRetryWait)
}

func (c *Client) send(ctx context.Context, op Op, target string, payload []byte) (int, http.Header, []byte, error) {
	var reader io.Reader
	if payload != nil {
		reader = bytes.NewReader(payload)
	}
	request, err := http.NewRequestWithContext(ctx, op.Method, target, reader)
	if err != nil {
		return 0, nil, nil, fmt.Errorf("%s: %w", op.Name, err)
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", "Bearer "+c.token)
	request.Header.Set("User-Agent", c.userAgent)
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if c.approval != "" {
		request.Header.Set(ApprovalHeader, c.approval)
	}
	response, err := c.http.Do(request)
	if err != nil {
		// The URL is shown without its query: the token is never in it, but a query can carry names.
		return 0, nil, nil, fmt.Errorf("%s: cannot reach %s: %w", op.Name, c.base, stripURL(err))
	}
	defer func() { _ = response.Body.Close() }()
	body, err := io.ReadAll(io.LimitReader(response.Body, 64<<20))
	if err != nil {
		return 0, nil, nil, fmt.Errorf("%s: reading the answer: %w", op.Name, err)
	}
	return response.StatusCode, response.Header, body, nil
}

// stripURL removes the address net/http puts in front of its errors, which this package already names.
func stripURL(err error) error {
	var urlErr *url.Error
	if errors.As(err, &urlErr) {
		return urlErr.Err
	}
	return err
}

func (c *Client) finish(op Op, call Call, status int, header http.Header, body []byte) error {
	if version := header.Get(VersionHeader); version != "" && version != APIVersion {
		return fmt.Errorf("%s: the server speaks API version %s and this provider is written for version %s: use a provider release that matches", op.Name, version, APIVersion)
	}
	if header.Get("Deprecation") != "" && c.notice != nil {
		c.notice(op, header)
	}
	if status >= 300 {
		retry, _ := strconv.ParseFloat(header.Get("Retry-After"), 64)
		if status >= 300 && status < 400 {
			return fmt.Errorf("%s: the server redirected to %q; the token is not sent to another address, so use the final address as url", op.Name, header.Get("Location"))
		}
		apiErr := parseAPIError(op, status, body, retry)
		if apiErr.Code == "elevation_required" {
			return &ElevationRequiredError{APIError: apiErr}
		}
		return apiErr
	}
	if status == http.StatusAccepted {
		if refusal := approvalRequest(op, status, header, body); refusal != nil {
			return refusal
		}
	}
	if call.Out == nil || len(bytes.TrimSpace(body)) == 0 {
		return nil
	}
	if err := json.Unmarshal(body, call.Out); err != nil {
		return fmt.Errorf("%s: the answer is not what the API documents (%v): is %s a Noust server?", op.Name, err, c.base)
	}
	return nil
}

// approvalRequest recognises the 202 a server answers instead of running a call that needs a second person.
func approvalRequest(op Op, status int, header http.Header, body []byte) error {
	var raw struct {
		Error  string `json:"error"`
		Fields struct {
			Approval string `json:"approval"`
		} `json:"fields"`
	}
	if json.Unmarshal(body, &raw) != nil || raw.Error != "approval_required" {
		return nil
	}
	apiErr := parseAPIError(op, status, body, 0)
	id := raw.Fields.Approval
	if id == "" {
		id = header.Get("X-Noust-Approval-Request")
	}
	return &ApprovalRequiredError{APIError: apiErr, ApprovalID: id}
}

// Verify proves the address is a Noust server and the token is a live credential, and returns the API
// version the server speaks.
func (c *Client) Verify(ctx context.Context) error {
	target := c.base + "/api/v1/auth/verify"
	status, header, body, err := c.send(ctx, VerifyToken, target, nil)
	if err != nil {
		return err
	}
	version := header.Get(VersionHeader)
	if version == "" && status < 300 {
		return fmt.Errorf("%s does not look like a Noust server: its answers carry no %s header", c.base, VersionHeader)
	}
	return c.finish(VerifyToken, Call{}, status, header, body)
}
