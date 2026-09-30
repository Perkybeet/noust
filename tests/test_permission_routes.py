"""
Every route declares its permission, and the declaration is what is enforced.

A route nobody classified is a route anybody signed in can call: that is how a
single ``admin`` scope ended up guarding everything in 3.0. So the maps in
``noust.web.permissions`` are held to the application's real route table
(nothing missing, nothing stale), the schema publishes them, and a principal
is refused every route whose permission its role does not hold - asked over
HTTP, through the dependency the API router installs, on every route.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import ROLES, AuthPolicy, passwords
from noust.core.store import NoustStore
from noust.web.api.openapi import api_routes
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig, require_auth
from noust.web.permissions import ALL_PERMISSIONS, PUBLIC, Permission
from noust.web.permissions.enforce import PERMISSION_EXTENSION, SECURITY_CONFIG_SECTIONS
from noust.web.permissions.registry import match_path, permission_for_route, route_map
from noust.web.permissions.roles import (
    ADMIN,
    AUDITOR,
    OPERATOR,
    ROLE_PERMISSIONS,
    SECURITY,
    VIEWER,
    permissions_for_role,
)
from noust.web.server import create_app, get_token_manager

PASSWORD = "correct horse battery staple"

#: Routes that answer without a credential, on purpose, and nothing else.
EXPECTED_PUBLIC = {
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/invitations/open"),
    ("POST", "/api/auth/invitations/accept"),
    ("POST", "/api/auth/passkeys/login/options"),
    ("POST", "/api/auth/passkeys/login"),
    ("POST", "/hooks/deploy/{domain}"),
    ("POST", "/hooks/github"),
    ("GET", "/health"),
    ("GET", "/{path}"),
}


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def app(sandbox: Path) -> Any:
    """The whole console, as it is served."""
    return create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
        )
    )


def served_routes(app: Any) -> set[tuple[str, str]]:
    """
    Every ``(METHOD, template)`` the application serves, HEAD left to GET.

    Args:
        app: The application.

    Returns:
        The routes.
    """
    served = set()
    for route in api_routes(app.routes):
        for method in route.methods or ():
            if method == "HEAD" and "GET" in route.methods:
                continue
            served.add((method, str(route.path_format)))
    return served


def _dependency_calls(dependant: Any) -> Iterator[Any]:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


class TestTheMapsAreComplete:
    def test_every_route_declares_a_permission(self, app: Any) -> None:
        missing = sorted(
            f"{method} {path}"
            for method, path in served_routes(app)
            if permission_for_route(method, path) is None
        )
        assert not missing, (
            "These routes declare no permission. Add each to the routes_<area>.py of "
            "noust.web.permissions it belongs to:\n" + "\n".join(missing)
        )

    def test_no_map_names_a_route_that_does_not_exist(self, app: Any) -> None:
        served = served_routes(app)
        stale = sorted(
            f"{method} {path}"
            for method, path in route_map()
            if (method, path) not in served and not (method == "HEAD" and ("GET", path) in served)
        )
        assert not stale, "Mapped but not served:\n" + "\n".join(stale)

    def test_only_the_known_routes_are_public(self) -> None:
        public = {key for key, permission in route_map().items() if permission == PUBLIC}
        assert public == EXPECTED_PUBLIC

    def test_every_other_route_authenticates(self, app: Any) -> None:
        unguarded = sorted(
            f"{sorted(route.methods)} {route.path_format}"
            for route in api_routes(app.routes)
            if any(
                permission_for_route(method, str(route.path_format)) != PUBLIC
                for method in route.methods or ()
            )
            and not any(call is require_auth for call in _dependency_calls(route.dependant))
        )
        assert not unguarded, unguarded

    def test_the_schema_publishes_every_permission(self, app: Any) -> None:
        schema = app.openapi()
        missing = [
            f"{method.upper()} {path}"
            for path, operations in schema["paths"].items()
            for method, operation in operations.items()
            if isinstance(operation, dict) and PERMISSION_EXTENSION not in operation
        ]
        assert not missing, missing
        restart = schema["paths"]["/api/apps/{domain}/restart"]["post"]
        assert restart[PERMISSION_EXTENSION] == Permission.APPS_OPERATE


class TestTheRoles:
    def test_the_roles_are_the_accounts_roles(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(ROLES)

    def test_every_permission_is_held_by_someone(self) -> None:
        held = set().union(*ROLE_PERMISSIONS.values())
        assert held == set(ALL_PERMISSIONS)

    def test_every_role_sees_and_manages_itself(self) -> None:
        for permissions in ROLE_PERMISSIONS.values():
            assert VIEWER <= permissions

    def test_operators_restart_renew_and_deploy_but_do_not_change(self) -> None:
        assert {Permission.APPS_OPERATE, Permission.APPS_DEPLOY} <= OPERATOR
        assert Permission.APPS_MANAGE not in OPERATOR
        assert permission_for_route("POST", "/api/certs/{domain}/renew") in OPERATOR
        assert permission_for_route("POST", "/api/services/{name}/restart") in OPERATOR

    def test_admin_does_not_govern_security_accounts_or_the_audit(self) -> None:
        assert (
            not {
                Permission.SECURITY_MANAGE,
                Permission.ACCOUNTS_MANAGE,
                Permission.ACCOUNTS_READ,
                Permission.AUDIT_READ,
                Permission.AUDIT_MANAGE,
            }
            & ADMIN
        )

    def test_security_changes_nothing_but_security(self) -> None:
        changes_infrastructure = {
            Permission.APPS_OPERATE,
            Permission.APPS_DEPLOY,
            Permission.APPS_MANAGE,
            Permission.ROOT_EQUIVALENT,
            Permission.SECRETS_REVEAL,
            Permission.SERVER_MANAGE,
            Permission.DATABASES_WRITE,
            Permission.DATABASES_MANAGE,
            Permission.BACKUPS_MANAGE,
            Permission.FLEET_MANAGE,
            Permission.SETTINGS_MANAGE,
        }
        assert not changes_infrastructure & SECURITY

    def test_the_auditor_only_reads(self) -> None:
        writes = {p for p in AUDITOR if not p.endswith(".read") and p != Permission.SELF}
        assert not writes

    def test_an_unknown_role_only_reads(self) -> None:
        assert permissions_for_role("superuser") == VIEWER


class TestRefinements:
    @pytest.mark.parametrize("section", sorted(SECURITY_CONFIG_SECTIONS))
    def test_patching_a_security_section_needs_security(
        self, sandbox: Path, app: Any, section: str
    ) -> None:
        client, csrf = signed_in(app, "admin")

        response = client.patch(
            "/api/config",
            json={"path": f"{section}.anything", "value": "x"},
            headers={CSRF_HEADER_NAME: csrf},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "permission_denied"
        assert "security.manage" in response.json()["detail"]

    def test_a_proxied_path_needs_what_it_needs_on_the_node(self) -> None:
        assert match_path("POST", "/api/apps/shop.example.com/restart") == (
            "/api/apps/{domain}/restart",
            Permission.APPS_OPERATE,
        )
        assert match_path("GET", "/api/apps/types") == ("/api/apps/types", Permission.APPS_READ)


def signed_in(app: Any, role: str) -> tuple[TestClient, str]:
    """
    Create an account of a role and sign it in.

    Args:
        app: The application.
        role: The role.

    Returns:
        A client holding the session, and its CSRF token.
    """
    manager = get_token_manager().accounts
    username = f"{role}-user"
    account = manager.create(username, role, password=PASSWORD)
    secret = manager.begin_totp(account.id)
    codes = manager.confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    client = TestClient(app, client=("testclient", 50000))
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD, "totp_code": codes[0]}
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def concrete(path: str) -> str:
    """
    Fill a template's parameters with a value any of them accepts.

    Args:
        path: A path template.

    Returns:
        A path the route matches.
    """
    return re.sub(r"\{[^/{}]+\}", "x1", path).replace(
        "/api/nodes/x1/api/x1", "/api/nodes/x1/api/apps"
    )


@pytest.mark.parametrize("role", ["viewer", "operator", "admin", "security", "auditor"])
def test_every_route_refuses_a_role_without_its_permission(app: Any, role: str) -> None:
    """
    The role x permission x route matrix, asked of the application itself.

    Only the refusals are exercised: the request is refused before any
    handler runs, so walking every route touches nothing on this machine.
    """
    client, csrf = signed_in(app, role)
    held = permissions_for_role(role)
    served = served_routes(app)
    leaked = []
    for method, template in sorted(route_map()):
        permission = route_map()[(method, template)]
        if permission == PUBLIC or permission in held or (method, template) not in served:
            continue
        response = client.request(
            method, concrete(template), json={}, headers={CSRF_HEADER_NAME: csrf}
        )
        body = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if response.status_code != 403 or body.get("error") != "permission_denied":
            leaked.append(f"{method} {template} -> {response.status_code} {body.get('error')}")
    assert not leaked, f"{role} reached routes it may not:\n" + "\n".join(leaked)


@pytest.mark.parametrize(
    ("role", "method", "path"),
    [
        ("viewer", "GET", "/api/apps"),
        ("operator", "POST", "/api/apps/shop.example.com/restart"),
        ("operator", "POST", "/api/jobs/update"),
        ("admin", "POST", "/api/apps/inspect"),
        ("security", "PUT", "/api/config/web"),
        ("security", "GET", "/api/auth/accounts"),
        ("auditor", "GET", "/api/audit"),
        ("auditor", "GET", "/api/auth/accounts"),
    ],
)
def test_a_role_reaches_what_it_holds(
    app: Any, runner: Any, role: str, method: str, path: str
) -> None:
    """A granted route gets past the permission check (whatever the handler answers)."""
    client, csrf = signed_in(app, role)

    response = client.request(method, path, json={}, headers={CSRF_HEADER_NAME: csrf})

    assert not (
        response.status_code == 403 and response.json().get("error") == "permission_denied"
    ), response.text
