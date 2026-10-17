// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import "context"

// DeployKey is the key an application's repository is reached with. The private half never leaves the
// server; PublicKey is what to add at the code host.
type DeployKey struct {
	Name        string `json:"name"`
	Origin      string `json:"origin"`
	KeyType     string `json:"key_type"`
	Fingerprint string `json:"fingerprint"`
	PublicKey   string `json:"public_key"`
}

// AppDeployKey is the answer of the deploy-key routes of an application.
type AppDeployKey struct {
	Domain      string     `json:"domain"`
	Key         *DeployKey `json:"key"`
	Repository  *string    `json:"repository"`
	SourceIsSSH bool       `json:"source_is_ssh"`
	SSHURL      *string    `json:"ssh_url"`
	SharedWith  []string   `json:"shared_with"`
	Warnings    []string   `json:"warnings"`
}

// CreateDeployKey makes an ed25519 key for an application's repository (name names the new key), or
// assigns an existing one (existing names it).
func (c *Client) CreateDeployKey(ctx context.Context, app, name, existing string) (*AppDeployKey, error) {
	body := map[string]string{}
	if name != "" {
		body["name"] = name
	}
	if existing != "" {
		body["key"] = existing
	}
	var out AppDeployKey
	if err := c.Do(ctx, CreateDeployKey, Call{Args: []string{app}, Body: body, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// ImportDeployKey uses a private key the operator has as the application's deploy key.
func (c *Client) ImportDeployKey(ctx context.Context, app, name, privateKey string) (*AppDeployKey, error) {
	body := map[string]string{"private_key": privateKey}
	if name != "" {
		body["name"] = name
	}
	var out AppDeployKey
	if err := c.Do(ctx, ImportDeployKey, Call{Args: []string{app}, Body: body, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetDeployKey shows what an application's repository is reached with; Key is nil when it has no key of its own.
func (c *Client) GetDeployKey(ctx context.Context, app string) (*AppDeployKey, error) {
	var out AppDeployKey
	if err := c.Do(ctx, GetDeployKey, Call{Args: []string{app}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteDeployKey takes the repository off its deploy key; the key is deleted when nothing else uses it.
func (c *Client) DeleteDeployKey(ctx context.Context, app string) error {
	return c.Do(ctx, DeleteDeployKey, Call{Args: []string{app}})
}

// RegistryLogin is a registry account. Its password is never returned.
type RegistryLogin struct {
	Name       string   `json:"name"`
	Kind       string   `json:"kind"`
	Server     string   `json:"server"`
	Username   string   `json:"username"`
	Apps       []string `json:"apps"`
	LastStatus *string  `json:"last_status"`
	LastError  *string  `json:"last_error"`
}

// RegistryLoginRequest is the body of POST /api/registries.
type RegistryLoginRequest struct {
	Name     string `json:"name"`
	Kind     string `json:"kind"`
	Username string `json:"username"`
	Password string `json:"password"`
	Server   string `json:"server,omitempty"`
}

// RegistryLoginChange is the body of PATCH /api/registries/{name}; a nil field stays as it is.
type RegistryLoginChange struct {
	Kind     *string `json:"kind,omitempty"`
	Server   *string `json:"server,omitempty"`
	Username *string `json:"username,omitempty"`
	Password *string `json:"password,omitempty"`
}

// CreateRegistryLogin adds a registry account.
func (c *Client) CreateRegistryLogin(ctx context.Context, request RegistryLoginRequest) (*RegistryLogin, error) {
	var out RegistryLogin
	if err := c.Do(ctx, CreateRegistryLogin, Call{Body: request, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetRegistryLogin shows one account; the error satisfies IsNotFound when there is none.
func (c *Client) GetRegistryLogin(ctx context.Context, name string) (*RegistryLogin, error) {
	var out RegistryLogin
	if err := c.Do(ctx, GetRegistryLogin, Call{Args: []string{name}, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChangeRegistryLogin changes an account's kind, host, user or password.
func (c *Client) ChangeRegistryLogin(ctx context.Context, name string, change RegistryLoginChange) (*RegistryLogin, error) {
	var out RegistryLogin
	if err := c.Do(ctx, ChangeRegistryLogin, Call{Args: []string{name}, Body: change, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteRegistryLogin removes an account, its password and its assignments.
func (c *Client) DeleteRegistryLogin(ctx context.Context, name string) error {
	return c.Do(ctx, DeleteRegistryLogin, Call{Args: []string{name}})
}

// FleetServer is one server a central manages, or this server itself.
type FleetServer struct {
	Name         string            `json:"name"`
	Local        bool              `json:"local"`
	Reachability string            `json:"reachability"`
	Version      *string           `json:"version"`
	Labels       map[string]string `json:"labels"`
	Access       *string           `json:"access"`
}

// ListFleetServers returns every server the token's fleet view holds.
func (c *Client) ListFleetServers(ctx context.Context) ([]FleetServer, error) {
	var out struct {
		Items []FleetServer `json:"items"`
	}
	if err := c.Do(ctx, ListFleetServers, Call{Out: &out}); err != nil {
		return nil, err
	}
	return out.Items, nil
}
