// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import "context"

// VariableScope is one of the four places the API keeps shared variables: the whole server, a team, a
// project or an environment. The routes differ and the objects do not, so one client serves all of them
// and a scope only says which operations to call and what owns the variables.
type VariableScope struct {
	// Name is what a reference calls the scope: {{team.NAME}}.
	Name string
	// Owner names the arguments that locate the owner, outermost first: none for the server, the team's
	// slug for a team, and so on. Variables are addressed by these and then by their own name.
	Owner []string
	List  Op
	Set   Op
	Del   Op
}

// The scopes. Their operations are in AllOperations so that the contract tests hold each to the schema.
var (
	ServerVariables = VariableScope{Name: "server", List: ListServerVariables, Set: SetServerVariable, Del: DeleteServerVariable}
	TeamVariables   = VariableScope{Name: "team", Owner: []string{"team"}, List: ListTeamVariables, Set: SetTeamVariable, Del: DeleteTeamVariable}

	ProjectVariables = VariableScope{
		Name: "project", Owner: []string{"team", "project"},
		List: ListProjectVariables, Set: SetProjectVariable, Del: DeleteProjectVariable,
	}
	EnvironmentVariables = VariableScope{
		Name: "environment", Owner: []string{"team", "project", "environment"},
		List: ListEnvironmentVariables, Set: SetEnvironmentVariable, Del: DeleteEnvironmentVariable,
	}
)

// VariableScopes lists them all.
var VariableScopes = []VariableScope{ServerVariables, TeamVariables, ProjectVariables, EnvironmentVariables}

// Secrecy is why the API treats a variable as a secret, or does not.
type Secrecy struct {
	// Secret is whether the value is masked in every read.
	Secret bool   `json:"secret"`
	Reason string `json:"reason"`
	// Marked is true when the verdict is an operator's mark and not the classifier's guess from the
	// variable's name and value.
	Marked bool `json:"marked"`
}

// Variable is one shared variable as the API answers it. A secret's Value is masked in every read, so a
// caller that needs to compare it cannot.
type Variable struct {
	Name       string   `json:"name"`
	Value      string   `json:"value"`
	Secrecy    Secrecy  `json:"secrecy"`
	Audience   string   `json:"audience"`
	References []string `json:"references"`
	UpdatedAt  string   `json:"updated_at"`
	UpdatedBy  *string  `json:"updated_by"`
}

// VariableList is the variables of one scope.
type VariableList struct {
	Scope     string     `json:"scope"`
	Label     string     `json:"label"`
	Variables []Variable `json:"variables"`
	Unmasked  bool       `json:"unmasked"`
	Syntax    string     `json:"syntax"`
}

// VariableRequest is a variable to set. A nil Secret lets the server decide from the name and the value.
type VariableRequest struct {
	Value    string `json:"value"`
	Secret   *bool  `json:"secret"`
	Audience string `json:"audience,omitempty"`
}

// DeletedVariable is the answer to removing a variable: the applications whose variables still refer to it
// have a next deploy that fails until the reference changes.
type DeletedVariable struct {
	Name         string   `json:"name"`
	Applications []string `json:"applications"`
}

func (c *Client) variableList(ctx context.Context, scope VariableScope, owner []string) (*VariableList, error) {
	var out VariableList
	if err := c.Do(ctx, scope.List, Call{Args: owner, Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}

// find returns the variable called name, or nil.
func (l *VariableList) find(name string) *Variable {
	for i := range l.Variables {
		if l.Variables[i].Name == name {
			return &l.Variables[i]
		}
	}
	return nil
}

// withName is the arguments of a call about one variable: its owner's, then its own name.
func withName(owner []string, name string) []string {
	args := make([]string, 0, len(owner)+1)
	args = append(args, owner...)
	return append(args, name)
}

// GetVariable returns one shared variable, or nil when the scope has none by that name. The API lists a
// scope's variables and has no read of one, so the list is filtered here; the error satisfies IsNotFound
// when the owner (the team, project or environment) is unknown. A secret's value comes masked.
func (c *Client) GetVariable(ctx context.Context, scope VariableScope, owner []string, name string) (*Variable, error) {
	list, err := c.variableList(ctx, scope, owner)
	if err != nil {
		return nil, err
	}
	return list.find(name), nil
}

// SetVariable creates a shared variable or changes it, and returns it as the server stored it.
func (c *Client) SetVariable(ctx context.Context, scope VariableScope, owner []string, name string, request VariableRequest) (*Variable, error) {
	var out VariableList
	if err := c.Do(ctx, scope.Set, Call{Args: withName(owner, name), Body: request, Out: &out}); err != nil {
		return nil, err
	}
	stored := out.find(name)
	if stored == nil {
		return nil, &APIError{Operation: scope.Set.Name, Status: 200, Detail: "the server answered without the variable it just set"}
	}
	return stored, nil
}

// DeleteVariable removes a shared variable and reports the applications that still refer to it.
func (c *Client) DeleteVariable(ctx context.Context, scope VariableScope, owner []string, name string) (*DeletedVariable, error) {
	var out DeletedVariable
	if err := c.Do(ctx, scope.Del, Call{Args: withName(owner, name), Out: &out}); err != nil {
		return nil, err
	}
	return &out, nil
}
