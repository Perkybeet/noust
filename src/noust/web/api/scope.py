# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Holding every request to the teams of whoever makes it.

:func:`enforce_scope` is installed on the API router beside ``require_auth``,
so no route can forget it. It works out the principal's
:class:`~noust.core.teams.scope.Scope`, makes it the ambient one (so
:func:`noust.core.teams.scoped.scoped_store` asks the store with it from
anywhere below), and, for a principal that does not see every team, holds the
route to what :mod:`noust.web.permissions.scopes` says it is about:

- a **resource** route is answered 404 when its resource is not visible - the
  same answer as for one that does not exist, never 403 - and 403 when the
  principal sees it but does not hold the route's permission in its team;
- an **instance** route is refused (403 ``instance_scope_required``);
- **open** routes are held to the permissions of the principal's instance role,
  as before teams, and **scoped** ones are left to their handler, which asks
  the scoped store;
- a call **through the central to a server assigned to the principal's team**
  (``/api/nodes/{node}/api/...``) is held twice: the server must be the team's
  (404 otherwise), and the call it forwards is judged by this same table applied
  to the server's own path. The server takes the central's token for an
  instance principal, so what a team member may not do on the central
  (anything about the machine) must be refused here, before it is forwarded.

A principal that sees every team passes straight through: nothing here changes
what the master token, a central, an ``admin`` or a server with one team does.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import Depends, HTTPException, Request

from noust.core.exceptions import ValidationError
from noust.core.teams import (
    Scope,
    ScopedStore,
    Target,
    TeamStore,
    bind_for_request,
    current_scope,
)
from noust.managers.database.registry import DatabaseRegistry
from noust.web.auth import ensure_permission, require_auth
from noust.web.permissions import PUBLIC
from noust.web.permissions.enforce import PermissionDenied, route_template
from noust.web.permissions.principal import scope_of
from noust.web.permissions.registry import (
    NODE_PROXY_TEMPLATE,
    match_path,
    permission_for_route,
    proxied_path,
)
from noust.web.permissions.scopes import (
    APP,
    BODY,
    DATABASE,
    DEPLOYMENT,
    DOMAIN,
    INSTANCE,
    JOB,
    NODE,
    OPEN,
    PATH,
    QUERY,
    RESOURCE,
    SCOPED,
    TEAM,
    Ref,
    Rule,
    rule_for,
)
from noust.web.permissions.team_roles import instance_permissions_of, permissions_at
from noust.web.permissions.websockets import node_stream_permission

logger = logging.getLogger(__name__)

#: What is said of a resource that is not there, or not there for the caller.
_NOT_FOUND: Mapping[str, str] = {
    APP: "Application not found: {value}",
    DOMAIN: "Not found: {value}",
    DATABASE: "Database not found: {value}",
    DEPLOYMENT: "Deployment not found: {value}",
    JOB: "Job not found: {value}",
    TEAM: "Team not found: {value}",
    NODE: "Node not found: {value}",
}


def not_found(kind: str, value: str) -> HTTPException:
    """
    The one answer for a resource a principal may not know exists.

    Args:
        kind: What was asked for (:data:`~noust.web.permissions.scopes.APP`...).
        value: What it was called.

    Returns:
        A 404, in the shape every other 404 of the API has.
    """
    return HTTPException(status_code=404, detail=_NOT_FOUND[kind].format(value=value))


async def _json_object(request: Request) -> dict[str, Any] | None:
    """
    Args:
        request: The request.

    Returns:
        Its JSON body when it is an object, otherwise None. The bytes are
        the ones the handler's own parser reads (Starlette keeps them on the
        request), so what is checked here is what the handler acts on.
    """
    try:
        raw = await request.body()
        value = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _values(ref: Ref, request: Request, body: dict[str, Any] | None) -> list[str]:
    """
    Read the parameters that name a resource.

    Args:
        ref: The reference.
        request: The request.
        body: Its JSON object body, when it has one.

    Returns:
        The values, in the order of ``ref.names``.

    Raises:
        ValidationError: When one is missing or is not a string, which the
            handler would refuse as well; refusing here keeps a body this
            module reads differently from the handler from being acted on.
    """
    values: list[str] = []
    for name in ref.names:
        if ref.where == PATH:
            raw: Any = request.path_params.get(name)
        elif ref.where == QUERY:
            raw = request.query_params.get(name)
        else:
            raw = body.get(name) if body is not None else None
        if not isinstance(raw, str) or not raw.strip():
            raise ValidationError(f"{name} is required", details=f"Give {name}.", field=name)
        values.append(raw.strip())
    return values


def _resolve(ref: Ref, values: list[str], scoped: ScopedStore) -> Target | None:
    """
    Find where a resource the principal sees lives.

    Args:
        ref: What kind of resource.
        values: Its name, from :func:`_values`.
        scoped: The scoped store of the principal.

    Returns:
        Its team, project and environment (0 where it has none), or None when
        the principal does not see the resource (or it does not exist: the two
        are one answer).
    """
    if ref.kind == APP:
        app = scoped.get_app(values[0].lower())
        return scoped.placement_of_project(app.project_id, app.environment_id) if app else None
    if ref.kind == DOMAIN:
        return scoped.placement_of_domain(values[0].lower())
    if ref.kind == DATABASE:
        engine, name = values
        # The route accepts an engine by any of its names (pg, mariadb...); the store has one.
        engine = DatabaseRegistry.canonical(engine) or engine.lower()
        record = scoped.get_database(name, engine)
        if record is None:
            return None
        return scoped.placement_of_project(record.project_id, record.environment_id)
    if ref.kind == DEPLOYMENT:
        deployment = scoped.get_deployment(int(values[0])) if values[0].isdigit() else None
        return scoped.placement_of_domain(deployment.domain) if deployment is not None else None
    if ref.kind == JOB:
        job = scoped.get_job(values[0])
        return scoped.placement_of_domain(job.domain) if job is not None else None
    if ref.kind == TEAM:
        from noust.core.teams import TeamStore

        team = TeamStore(scoped.store).get_team(values[0])
        if team is None or not scoped.scope.sees_team(team.id):
            return None
        return Target(team.id, 0, 0)
    if ref.kind == NODE:
        # A server nobody took is the instance's: a member is told it is not there.
        return scoped.placement_of_node(values[0])
    raise ValueError(f"Unknown resource kind {ref.kind!r}")  # a typo in the table


def _refuse_instance(permission: str) -> PermissionDenied:
    """
    Args:
        permission: What the route needs.

    Returns:
        The refusal of a server-wide route to a principal that sees one team.
    """
    return PermissionDenied(
        "instance_scope_required",
        permission,
        "This is about the whole server, and your account belongs to some of its teams only",
        "Ask an administrator of this server, or work inside your team's applications.",
    )


def require_instance_scope(permission: str) -> None:
    """
    Refuse, inside a handler, a part of a route that is about the whole server.

    For a route whose body can ask for something the server owns (a flag that
    installs a package as root, say) while the rest of it is a team's own: the
    route's rule cannot be "instance" for every call, so the handler says it for
    the one that is. Rule 4 still holds: it is the same refusal
    :func:`enforce_scope` gives an instance route, from the same scope.

    Args:
        permission: What the route needs, for the refusal's record.

    Raises:
        PermissionDenied: 403 ``instance_scope_required`` when the caller does
            not see every team.
    """
    if current_scope().restricted:
        raise _refuse_instance(permission)


async def enforce_scope(request: Request, session: dict[str, Any] = Depends(require_auth)) -> None:
    """
    Bind the principal's scope to the request and hold the route to it.

    Args:
        request: The request.
        session: The authenticated payload; empty for a public route.

    Raises:
        HTTPException: 404 for a resource the principal does not see.
        PermissionDenied: 403 for a permission the principal lacks in the
            resource's team, or for a server-wide route it may not call.
        ValidationError: 400 for a parameter that names a resource and is
            missing.
    """
    if not session:
        return
    scope = scope_of(session)
    bind_for_request(scope)
    request.state.scope = scope
    if scope.sees_all:
        return

    template = route_template(request)
    if template is None:
        return
    method = request.method.upper()
    permission = permission_for_route(method, template)
    if permission is None or permission == PUBLIC:
        return
    rule = rule_for(method, template)
    if rule is None:
        logger.error("No scope rule for %s %s; refusing it to a team member", method, template)
        raise PermissionDenied(
            "scope_undeclared",
            permission,
            f"{method} {template} declares no scope",
            "This is a defect in Noust: every route must be classified in "
            "noust.web.permissions.scopes. Report it; the route is refused until then.",
        )
    await _hold(request, session, scope, rule, permission)


async def _hold(
    request: Request, session: dict[str, Any], scope: Scope, rule: Rule, permission: str
) -> None:
    """
    Apply one rule to a principal that does not see every team.

    Args:
        request: The request.
        session: The authenticated payload.
        scope: The principal's scope.
        rule: The rule that governs the route.
        permission: What the route needs.
    """
    if rule.scoping == INSTANCE:
        raise _refuse_instance(permission)
    if rule.scoping in (OPEN, SCOPED):
        # Outside any team the principal holds what its instance role holds: a
        # permission only its team roles add is not valid on a route that is
        # about the server. A SCOPED route is held to the ceiling (the
        # handler narrows to the team it acts in).
        if rule.scoping == OPEN:
            ensure_permission(
                request,
                replace_permissions(session, instance_permissions_of(session)),
                permission,
            )
        return
    if rule.scoping != RESOURCE:
        raise ValueError(f"Unknown scoping {rule.scoping!r}")  # a typo in the table
    body = await _json_object(request) if any(ref.where == BODY for ref in rule.refs) else None
    scoped = ScopedStore(scope)
    for ref in rule.refs:
        values = _values(ref, request, body)
        placed = _resolve(ref, values, scoped)
        if placed is None:
            raise not_found(ref.kind, "/".join(values))
        ensure_permission_at(request, session, placed, permission)
        if ref.kind == NODE and route_template(request) == NODE_PROXY_TEMPLATE:
            inner = proxied_path(str(request.scope.get("path", "")))
            hold_node_call(request, session, placed, request.method.upper(), inner)


def hold_node_call(
    request: Request, session: dict[str, Any], placed: Target, method: str, inner: str | None
) -> None:
    """
    Hold a call forwarded to a server of the principal's team to what a team may do.

    The server answers to the central's token as it would to the instance, so
    the table that holds a team member on the central is applied here to the
    server's own path, with the resource being the team's whole server: a route
    that is about the machine is refused, one that is about the team's own
    resources needs the permission *in the server's team*, and a path this
    central does not know (a server newer than it) is refused, because nothing
    says what it reaches.

    Args:
        request: The request, for the audit record of a refusal.
        session: The authenticated payload.
        placed: The server's team.
        method: The HTTP method.
        inner: The server's own path, from ``/api`` on; None when it is not one.

    Raises:
        PermissionDenied: 403 ``instance_scope_required`` for a route that is the
            machine's, ``scope_undeclared`` for a path this central does not
            know, ``permission_denied`` for a permission the principal lacks in
            the server's team.
    """
    matched = match_path(method, inner) if inner else None
    if matched is None:
        raise node_path_unknown(method, inner)
    template, permission = matched
    rule = rule_for(method, template)
    if rule is None:
        raise node_path_unknown(method, inner)
    if rule.scoping == INSTANCE:
        raise _refuse_instance(permission)
    if rule.scoping == OPEN:
        # About nobody's resources: what the instance role holds, as on the central.
        ensure_permission(
            request,
            replace_permissions(session, instance_permissions_of(session)),
            permission,
        )
        return
    ensure_permission_at(request, session, placed, permission)


def node_path_unknown(method: str, inner: str | None) -> PermissionDenied:
    """
    Args:
        method: The HTTP method.
        inner: The server's own path.

    Returns:
        The refusal of a path this central cannot classify.
    """
    return PermissionDenied(
        "scope_undeclared",
        "unknown",
        f"{method} {inner or '?'} is not a path this central knows",
        "The server is newer than this central, or the path does not exist. A team "
        "uses only what the central can tell is its own; update the central, or an "
        "administrator of the server makes the call.",
    )


def stream_refusal(session: dict[str, Any], placed: Target, inner_stream: str) -> str | None:
    """
    Say why a team member may not open a server's stream through the central.

    The WebSocket twin of :func:`hold_node_call`: the handshake passes the
    middleware, not the router, so the handler asks the same question here.

    Args:
        session: The authenticated payload.
        placed: The server's team.
        inner_stream: The server's own stream, from ``/ws`` on.

    Returns:
        A sentence when it is refused, None when it may open.
    """
    needed = node_stream_permission(inner_stream)
    if needed is None:
        return "This central does not know that stream of the server"
    held = permissions_at(
        session, placed.team_id or None, placed.project_id or None, placed.environment_id or None
    )
    if needed not in held:
        return f"This needs the '{needed}' permission in the server's team"
    return None


def resolve_target_for(
    request: Request,
    session: dict[str, Any],
    *,
    permission: str,
    team: str | None = None,
    project: str | None = None,
    environment: str | None = None,
) -> Target:
    """
    Find where something new is created, and check the caller may create it there.

    The one place a creation names its project: an application, a site or a
    database says ``team``, ``project`` and ``environment`` (or leaves them out,
    and is given the caller's active team's defaults), and the caller must hold
    the creation's permission in that team, not merely somewhere.

    Args:
        request: The request, for the audit record of a refusal.
        session: The authenticated payload.
        permission: What creating it needs, such as ``apps.manage``.
        team: A team slug.
        project: A project slug.
        environment: An environment slug.

    Returns:
        The target: team, project and environment ids.

    Raises:
        TeamNotFoundError: 404 when the team, project or environment does not
            exist, or the team is not one the caller sees.
        TeamError: 400 when the request leaves out what cannot be guessed.
        PermissionDenied: 403 when the caller may not do it in that team.
    """
    target = TeamStore().resolve_target(
        scope_of(session), team=team, project=project, environment=environment
    )
    ensure_permission_at(request, session, target, permission)
    return target


def ensure_permission_at(
    request: Request, session: dict[str, Any], placed: Target, permission: str
) -> None:
    """
    Refuse a caller that does not hold a permission where a resource lives.

    Only an account has teams, projects and environments to hold it in; the
    master token, a central and a token nobody owns hold what they hold
    everywhere, which the route's own permission check has already settled.

    Args:
        request: The request, for the audit record of a refusal.
        session: The authenticated payload.
        placed: Where the resource lives; 0 for a project or an environment it
            does not have.
        permission: What is needed, such as ``apps.manage``.

    Raises:
        PermissionDenied: 403 when the caller lacks it there.
    """
    if "team_roles" not in session:
        return
    held = permissions_at(
        session, placed.team_id or None, placed.project_id or None, placed.environment_id or None
    )
    ensure_permission(request, replace_permissions(session, held), permission)


def ensure_permission_in_team(
    request: Request, session: dict[str, Any], team_id: int, permission: str
) -> None:
    """
    Refuse a caller that does not hold a permission in one team as a whole.

    Args:
        request: The request, for the audit record of a refusal.
        session: The authenticated payload.
        team_id: The team the permission is needed in.
        permission: What is needed, such as ``apps.manage``.

    Raises:
        PermissionDenied: 403 when the caller lacks it in that team.
    """
    ensure_permission_at(request, session, Target(team_id, 0, 0), permission)


def replace_permissions(session: dict[str, Any], permissions: frozenset[str]) -> dict[str, Any]:
    """
    Args:
        session: An authenticated payload.
        permissions: What it holds for the purpose of one check.

    Returns:
        A copy that holds exactly those, so the standard refusal (message,
        audit record) is the one every other check gives.
    """
    return {**session, "permissions": frozenset(permissions)}


__all__ = [
    "enforce_scope",
    "ensure_permission_at",
    "ensure_permission_in_team",
    "hold_node_call",
    "not_found",
    "replace_permissions",
    "require_instance_scope",
    "resolve_target_for",
    "stream_refusal",
]
