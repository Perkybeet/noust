// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import "context"

// AppDomain is one name an application answers on.
type AppDomain struct {
	Domain    string `json:"domain"`
	Kind      string `json:"kind"`
	Port      *int   `json:"port"`
	Temporary bool   `json:"temporary"`
}

// AppDomains is the answer to GET /api/apps/{domain}/domains.
type AppDomains struct {
	App       string      `json:"app"`
	Canonical string      `json:"canonical"`
	Domains   []AppDomain `json:"domains"`
}

// DomainChange is the answer to adding a domain: the application's domains afterwards and the job that
// extends its certificate, when the site serves TLS.
type DomainChange struct {
	App              string      `json:"app"`
	Domains          []AppDomain `json:"domains"`
	TLS              bool        `json:"tls"`
	CertificateJobID *string     `json:"certificate_job_id"`
}

// AddDomainRequest is the body of POST /api/apps/{domain}/domains.
type AddDomainRequest struct {
	Domain string `json:"domain"`
	Kind   string `json:"kind,omitempty"`
	Port   *int   `json:"port,omitempty"`
	DNS    bool   `json:"dns"`
	Force  bool   `json:"force"`
}

// ListDomains returns the names an application answers on, the primary first.
func (c *Client) ListDomains(ctx context.Context, app string) (*AppDomains, error) {
	var out AppDomains
	if err := c.Do(ctx, ListDomains, Call{Args: []string{app}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// AddDomain makes an application answer on another name.
func (c *Client) AddDomain(ctx context.Context, app string, request AddDomainRequest) (*DomainChange, error) {
	var out DomainChange
	if err := c.Do(ctx, AddDomain, Call{Args: []string{app}, Body: request, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteDomain stops an application answering on a name. The certificate is left alone.
func (c *Client) DeleteDomain(ctx context.Context, app, name string) (*DomainChange, error) {
	var out DomainChange
	if err := c.Do(ctx, DeleteDomain, Call{Args: []string{app, name}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// SetDomainPort chooses which port of the application an alias is proxied to; nil is the application's own.
func (c *Client) SetDomainPort(ctx context.Context, app, name string, port *int) (*DomainChange, error) {
	var out DomainChange
	if err := c.Do(ctx, SetDomainPort, Call{Args: []string{app, name}, Body: map[string]*int{"port": port}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}
