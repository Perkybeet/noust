# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shared variables over HTTP: the server's, a team's, a project's, an environment's.

A client of :class:`~noust.core.variables.store.VariableStore`, like ``noust
variables``: what a name may be, how many a scope holds and what a value may
hold are the store's. A shared variable is used by reference
(``{{project.DATABASE_URL}}``) from an application's variables and never
injected by itself, so reading one is reading configuration, and a value that
looks like a secret is masked exactly as an application's own is. Showing it in
clear needs an administrator in sudo mode and is audited, as for an application.

The team, the project and the environment in the path are held to the
principal's teams before a handler runs (:mod:`noust.web.api.scope`); the
server's own variables belong to the whole server.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noust.core.audit import record as audit_record
from noust.core.teams import TeamStore, scoped_store
from noust.core.variables import expressions
from noust.core.variables.model import (
    BOTH,
    ENVIRONMENT,
    PROJECT,
    SERVER,
    TEAM,
    SharedVariable,
)
from noust.core.variables.store import VariableStore
from noust.web.api.apps import EnvSecrecyOut, _redact_env, _secrets_map
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, ensure_elevated, require_elevated
from noust.web.auth import actor_label, ensure_scope, scope_satisfies
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)
server_router = APIRouter(route_class=NoustErrorRoute)


class VariableOut(BaseModel):
    """
    One shared variable.

    Attributes:
        name: The variable's name.
        value: Its value, masked when it is a secret and clear was not asked for.
        secrecy: Why it is, or is not, treated as a secret.
        audience: ``both``, ``build`` or ``runtime``: which part of a deployment
            an application that uses it hands it to.
        references: The references the value itself holds, such as
            ``{{server.REGION}}``.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    value: str
    secrecy: EnvSecrecyOut
    audience: str = BOTH
    references: list[str] = Field(default_factory=list)
    updated_at: str = ""
    updated_by: str | None = None


class VariablesResponse(BaseModel):
    """
    The variables of one scope.

    Attributes:
        scope: ``server``, ``team``, ``project`` or ``environment``.
        label: Whose they are, such as ``acme/shop/staging``.
        variables: Them, by name.
        unmasked: Whether the values are in clear.
        syntax: How an application refers to one of them.
    """

    scope: str
    label: str
    variables: list[VariableOut]
    unmasked: bool = False
    syntax: str


class VariableRequest(BaseModel):
    """
    A variable to set.

    Attributes:
        value: Its value; ``{{...}}`` references to other scopes are allowed.
        secret: True or False to say what it is, null to let Noust decide from
            its name and value.
        audience: ``both`` (the default), ``build`` or ``runtime``.
    """

    value: str
    secret: bool | None = None
    audience: str = BOTH


class BulkVariablesRequest(BaseModel):
    """A set of variables to create or change; nothing else is touched."""

    variables: dict[str, VariableRequest] = Field(default_factory=dict)


class UsageResponse(BaseModel):
    """
    Who uses a variable.

    Attributes:
        name: The variable.
        applications: The domains of the applications whose variables refer to
            it, among those the principal sees.
    """

    name: str
    applications: list[str]


class DeletedResponse(BaseModel):
    """A variable removed."""

    name: str
    applications: list[str] = Field(
        default_factory=list,
        description="Applications whose variables still refer to it: their next deploy fails "
        "until the reference is changed",
    )


def _marks(variables: list[SharedVariable]) -> dict[str, bool]:
    """
    Args:
        variables: A scope's variables.

    Returns:
        The operator's explicit marks, which win over the classifier.
    """
    return {v.name: v.secret for v in variables if v.secret is not None}


def _response(
    scope: str,
    label: str,
    variables: list[SharedVariable],
    *,
    unmasked: bool,
    reveal_kind: bool,
) -> VariablesResponse:
    """
    Args:
        scope: The scope.
        label: Whose they are.
        variables: Its variables.
        unmasked: Show values in clear.
        reveal_kind: Whether a value-based verdict may name the vendor.

    Returns:
        The response; secrets masked unless asked for.
    """
    marks = _marks(variables)
    values = {v.name: v.value for v in variables}
    shown = values if unmasked else _redact_env(values, marks)
    secrecy = _secrets_map(values, marks, reveal_kind=reveal_kind)
    out = [
        VariableOut(
            name=v.name,
            value=shown[v.name],
            secrecy=secrecy[v.name],
            audience=v.audience,
            references=[ref.text for ref in expressions.find_references(v.value)]
            if not secrecy[v.name].secret or unmasked
            else [],
            updated_at=v.updated_at,
            updated_by=v.updated_by,
        )
        for v in variables
    ]
    return VariablesResponse(
        scope=scope,
        label=label,
        variables=out,
        unmasked=unmasked,
        syntax=f"{{{{{scope}.NAME}}}}",
    )


def _audit(session: dict[str, Any], event: str, label: str, names: list[str]) -> None:
    """
    Put a change on the audit trail. Never a value.

    Args:
        session: The authenticated payload.
        event: A catalog name.
        label: Whose variables.
        names: Which ones.
    """
    audit_record(event, actor=actor_of(session), target=label, details={"variables": sorted(names)})


def _reveal(request: Request, session: dict[str, Any], unmask: bool) -> tuple[bool, bool]:
    """
    Decide what a read may show.

    Args:
        request: The request.
        session: The authenticated payload.
        unmask: Whether clear values were asked for.

    Returns:
        Whether to show clear values, and whether a verdict may name a vendor.

    Raises:
        HTTPException: 403 when clear values need more than the caller has.
    """
    if unmask:
        ensure_scope(request, session, "admin")
        ensure_elevated(request, session)
    return unmask, scope_satisfies(str(session.get("scope") or "read"), "admin")


class _Target:
    """Where a request's variables live: a scope, its owner's id and its label."""

    def __init__(self, scope: str, owner: int, label: str) -> None:
        self.scope = scope
        self.owner = owner
        self.label = label


def _team_target(team: str) -> _Target:
    found = TeamStore().require_team(team)
    return _Target(TEAM, found.id, found.slug)


def _project_target(team: str, project: str) -> _Target:
    found = TeamStore().require_project(team, project)
    return _Target(PROJECT, found.id, f"{found.team}/{found.slug}")


def _environment_target(team: str, project: str, environment: str) -> _Target:
    found = TeamStore().require_environment(team, project, environment)
    return _Target(ENVIRONMENT, found.id, f"{team}/{project}/{found.slug}")


def _list(
    target: _Target, request: Request, session: dict[str, Any], unmask: bool
) -> VariablesResponse:
    clear, reveal_kind = _reveal(request, session, unmask)
    if clear:
        audit_record(
            "apps.env.reveal",
            actor=actor_of(session),
            target=target.label,
            details={"scope": target.scope},
        )
    return _response(
        target.scope,
        target.label,
        VariableStore().list(target.scope, target.owner),
        unmasked=clear,
        reveal_kind=reveal_kind,
    )


def _set(
    target: _Target, name: str, body: VariableRequest, session: dict[str, Any]
) -> VariablesResponse:
    store = VariableStore()
    store.set(
        target.scope,
        target.owner,
        name,
        body.value,
        secret=body.secret,
        audience=body.audience,
        actor=actor_label(session),
    )
    _audit(session, "variables.set", target.label, [name])
    return _response(
        target.scope,
        target.label,
        store.list(target.scope, target.owner),
        unmasked=False,
        reveal_kind=scope_satisfies(str(session.get("scope") or "read"), "admin"),
    )


def _bulk(
    target: _Target, body: BulkVariablesRequest, session: dict[str, Any]
) -> VariablesResponse:
    store = VariableStore()
    # Checked together first: one bad entry changes nothing.
    existing = {v.name: v for v in store.list(target.scope, target.owner)}
    merged: dict[str, dict[str, Any]] = {
        name: {"value": v.value, "secret": v.secret, "audience": v.audience}
        for name, v in existing.items()
    }
    for name, entry in body.variables.items():
        merged[name] = {"value": entry.value, "secret": entry.secret, "audience": entry.audience}
    store.replace(target.scope, target.owner, merged, actor=actor_label(session))
    _audit(session, "variables.set", target.label, list(body.variables))
    return _response(
        target.scope,
        target.label,
        store.list(target.scope, target.owner),
        unmasked=False,
        reveal_kind=scope_satisfies(str(session.get("scope") or "read"), "admin"),
    )


def _delete(target: _Target, name: str, session: dict[str, Any]) -> DeletedResponse:
    store = VariableStore()
    if not store.delete(target.scope, target.owner, name):
        raise HTTPException(status_code=404, detail=f"No variable {name} in {target.label}")
    _audit(session, "variables.delete", target.label, [name])
    return DeletedResponse(name=name, applications=_users(target, name))


def _users(target: _Target, name: str) -> list[str]:
    visible = {app.domain for app in scoped_store().list_apps()}
    users = VariableStore().referencing_apps(target.scope, target.owner, name)
    return [domain for domain in users if domain in visible]


# --- a team's ----------------------------------------------------------------


@router.get("/{team}/variables", response_model=VariablesResponse)
def list_team_variables(
    team: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    unmask: Annotated[bool, Query()] = False,
) -> VariablesResponse:
    """
    List a team's shared variables.

    Args:
        team: The team's slug.
        request: The request.
        session: The authenticated session.
        unmask: Show values in clear (an administrator in sudo mode; audited).

    Returns:
        The variables, secrets masked.
    """
    return _list(_team_target(team), request, session, unmask)


@router.put("/{team}/variables/{name}", response_model=VariablesResponse)
def set_team_variable(
    team: str,
    name: str,
    body: VariableRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> VariablesResponse:
    """
    Create or change one of a team's shared variables.

    Args:
        team: The team's slug.
        name: The variable's name.
        body: Its value, mark and audience.
        session: The authenticated, elevated session.

    Returns:
        The team's variables.
    """
    return _set(_team_target(team), name, body, session)


@router.post("/{team}/variables", response_model=VariablesResponse)
def set_team_variables(
    team: str,
    body: BulkVariablesRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> VariablesResponse:
    """
    Create or change several of a team's shared variables at once.

    Args:
        team: The team's slug.
        body: The variables.
        session: The authenticated, elevated session.

    Returns:
        The team's variables.
    """
    return _bulk(_team_target(team), body, session)


@router.delete("/{team}/variables/{name}", response_model=DeletedResponse)
def delete_team_variable(
    team: str, name: str, session: Annotated[dict, Depends(require_elevated)]
) -> DeletedResponse:
    """
    Remove one of a team's shared variables.

    Args:
        team: The team's slug.
        name: The variable's name.
        session: The authenticated, elevated session.

    Returns:
        What was removed, and the applications that still refer to it.
    """
    return _delete(_team_target(team), name, session)


@router.get("/{team}/variables/{name}/usage", response_model=UsageResponse)
def team_variable_usage(
    team: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> UsageResponse:
    """
    Name the applications whose variables refer to a team's variable.

    Args:
        team: The team's slug.
        name: The variable's name.
        session: The authenticated session.

    Returns:
        The applications the principal sees.
    """
    return UsageResponse(name=name, applications=_users(_team_target(team), name))


# --- a project's -------------------------------------------------------------


@router.get("/{team}/projects/{project}/variables", response_model=VariablesResponse)
def list_project_variables(
    team: str,
    project: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    unmask: Annotated[bool, Query()] = False,
) -> VariablesResponse:
    """
    List a project's shared variables.

    Args:
        team: The team's slug.
        project: The project's slug.
        request: The request.
        session: The authenticated session.
        unmask: Show values in clear (an administrator in sudo mode; audited).

    Returns:
        The variables, secrets masked.
    """
    return _list(_project_target(team, project), request, session, unmask)


@router.put("/{team}/projects/{project}/variables/{name}", response_model=VariablesResponse)
def set_project_variable(
    team: str,
    project: str,
    name: str,
    body: VariableRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> VariablesResponse:
    """
    Create or change one of a project's shared variables.

    Args:
        team: The team's slug.
        project: The project's slug.
        name: The variable's name.
        body: Its value, mark and audience.
        session: The authenticated, elevated session.

    Returns:
        The project's variables.
    """
    return _set(_project_target(team, project), name, body, session)


@router.post("/{team}/projects/{project}/variables", response_model=VariablesResponse)
def set_project_variables(
    team: str,
    project: str,
    body: BulkVariablesRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> VariablesResponse:
    """
    Create or change several of a project's shared variables at once.

    Args:
        team: The team's slug.
        project: The project's slug.
        body: The variables.
        session: The authenticated, elevated session.

    Returns:
        The project's variables.
    """
    return _bulk(_project_target(team, project), body, session)


@router.delete("/{team}/projects/{project}/variables/{name}", response_model=DeletedResponse)
def delete_project_variable(
    team: str, project: str, name: str, session: Annotated[dict, Depends(require_elevated)]
) -> DeletedResponse:
    """
    Remove one of a project's shared variables.

    Args:
        team: The team's slug.
        project: The project's slug.
        name: The variable's name.
        session: The authenticated, elevated session.

    Returns:
        What was removed, and the applications that still refer to it.
    """
    return _delete(_project_target(team, project), name, session)


@router.get("/{team}/projects/{project}/variables/{name}/usage", response_model=UsageResponse)
def project_variable_usage(
    team: str, project: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> UsageResponse:
    """
    Name the applications whose variables refer to a project's variable.

    Args:
        team: The team's slug.
        project: The project's slug.
        name: The variable's name.
        session: The authenticated session.

    Returns:
        The applications the principal sees.
    """
    return UsageResponse(name=name, applications=_users(_project_target(team, project), name))


# --- an environment's --------------------------------------------------------

_ENVIRONMENT_PATH = "/{team}/projects/{project}/environments/{environment}/variables"


@router.get(_ENVIRONMENT_PATH, response_model=VariablesResponse)
def list_environment_variables(
    team: str,
    project: str,
    environment: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    unmask: Annotated[bool, Query()] = False,
) -> VariablesResponse:
    """
    List an environment's shared variables: the set each environment gives its applications.

    Args:
        team: The team's slug.
        project: The project's slug.
        environment: The environment's slug.
        request: The request.
        session: The authenticated session.
        unmask: Show values in clear (an administrator in sudo mode; audited).

    Returns:
        The variables, secrets masked.
    """
    return _list(_environment_target(team, project, environment), request, session, unmask)


@router.put(_ENVIRONMENT_PATH + "/{name}", response_model=VariablesResponse)
def set_environment_variable(
    team: str,
    project: str,
    environment: str,
    name: str,
    body: VariableRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> VariablesResponse:
    """
    Create or change one of an environment's shared variables.

    Args:
        team: The team's slug.
        project: The project's slug.
        environment: The environment's slug.
        name: The variable's name.
        body: Its value, mark and audience.
        session: The authenticated, elevated session.

    Returns:
        The environment's variables.
    """
    return _set(_environment_target(team, project, environment), name, body, session)


@router.post(_ENVIRONMENT_PATH, response_model=VariablesResponse)
def set_environment_variables(
    team: str,
    project: str,
    environment: str,
    body: BulkVariablesRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> VariablesResponse:
    """
    Create or change several of an environment's shared variables at once.

    Args:
        team: The team's slug.
        project: The project's slug.
        environment: The environment's slug.
        body: The variables.
        session: The authenticated, elevated session.

    Returns:
        The environment's variables.
    """
    return _bulk(_environment_target(team, project, environment), body, session)


@router.delete(_ENVIRONMENT_PATH + "/{name}", response_model=DeletedResponse)
def delete_environment_variable(
    team: str,
    project: str,
    environment: str,
    name: str,
    session: Annotated[dict, Depends(require_elevated)],
) -> DeletedResponse:
    """
    Remove one of an environment's shared variables.

    Args:
        team: The team's slug.
        project: The project's slug.
        environment: The environment's slug.
        name: The variable's name.
        session: The authenticated, elevated session.

    Returns:
        What was removed, and the applications that still refer to it.
    """
    return _delete(_environment_target(team, project, environment), name, session)


@router.get(_ENVIRONMENT_PATH + "/{name}/usage", response_model=UsageResponse)
def environment_variable_usage(
    team: str,
    project: str,
    environment: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
) -> UsageResponse:
    """
    Name the applications whose variables refer to an environment's variable.

    Args:
        team: The team's slug.
        project: The project's slug.
        environment: The environment's slug.
        name: The variable's name.
        session: The authenticated session.

    Returns:
        The applications the principal sees.
    """
    return UsageResponse(
        name=name, applications=_users(_environment_target(team, project, environment), name)
    )


# --- the server's ------------------------------------------------------------

_SERVER = _Target(SERVER, 0, "server")


@server_router.get("/server", response_model=VariablesResponse)
def list_server_variables(
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    unmask: Annotated[bool, Query()] = False,
) -> VariablesResponse:
    """
    List the variables shared by the whole server.

    Args:
        request: The request.
        session: The authenticated session.
        unmask: Show values in clear (an administrator in sudo mode; audited).

    Returns:
        The variables, secrets masked.
    """
    return _list(_SERVER, request, session, unmask)


@server_router.put("/server/{name}", response_model=VariablesResponse)
def set_server_variable(
    name: str, body: VariableRequest, session: Annotated[dict, Depends(require_elevated)]
) -> VariablesResponse:
    """
    Create or change one of the server's shared variables.

    Args:
        name: The variable's name.
        body: Its value, mark and audience.
        session: The authenticated, elevated session.

    Returns:
        The server's variables.
    """
    return _set(_SERVER, name, body, session)


@server_router.post("/server", response_model=VariablesResponse)
def set_server_variables(
    body: BulkVariablesRequest, session: Annotated[dict, Depends(require_elevated)]
) -> VariablesResponse:
    """
    Create or change several of the server's shared variables at once.

    Args:
        body: The variables.
        session: The authenticated, elevated session.

    Returns:
        The server's variables.
    """
    return _bulk(_SERVER, body, session)


@server_router.delete("/server/{name}", response_model=DeletedResponse)
def delete_server_variable(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> DeletedResponse:
    """
    Remove one of the server's shared variables.

    Args:
        name: The variable's name.
        session: The authenticated, elevated session.

    Returns:
        What was removed, and the applications that still refer to it.
    """
    return _delete(_SERVER, name, session)


@server_router.get("/server/{name}/usage", response_model=UsageResponse)
def server_variable_usage(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> UsageResponse:
    """
    Name the applications whose variables refer to a server variable.

    Args:
        name: The variable's name.
        session: The authenticated session.

    Returns:
        The applications.
    """
    return UsageResponse(name=name, applications=_users(_SERVER, name))
