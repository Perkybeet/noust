# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A node admits a central's request with the operator's role, within its ceiling.

Accounts live on the central; a node learns who acts through the headers the
central sends over the tunnel. A central before 4.1 names a role, and the node looks it up in its
own list of roles (:mod:`noust.web.permissions.roles`); an unknown role only reads. A 4.1 central
says what the person may do (``X-Noust-Actor-Permissions``), because a role the operator edited
does not mean the same on two servers. Either way the node intersects it with its own ceiling
(:func:`noust.fleet.policy.permits`), which never grants what no central may have.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.access.role_manager import RoleManager
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import policy
from noust.fleet.policy import NEVER_FLEET_PERMISSIONS
from noust.web.auth import (
    FLEET_ACTOR_HEADER,
    FLEET_ACTOR_PERMISSIONS_HEADER,
    FLEET_ACTOR_ROLE_HEADER,
    FLEET_ACTOR_SCOPE_HEADER,
)
from noust.web.permissions import Permission
from noust.web.permissions.roles import ADMIN, ADMIN_STANDARD, OPERATOR, VIEWER
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_node_security import client_from, fleet_headers
from tests.support.web_auth import make_config


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A node's application, its configuration inside the sandbox."""
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    return create_app(make_config(sandbox))


@pytest.fixture
def fleet_token(app: Any) -> str:
    """The token ``noust fleet authorize`` mints for a central."""
    return str(get_token_manager().create_fleet_token("fleet-nas")["token"])


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own, where the node's ceiling is read from."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def admitted(app: Any, token: str, **headers: str) -> dict[str, Any]:
    """
    Ask the node who the central's request is.

    Args:
        app: The node's application.
        token: The fleet token.
        **headers: The central's actor headers.

    Returns:
        The node's ``/api/auth/session`` answer.
    """
    response = client_from(app).get(
        "/api/auth/session",
        headers=fleet_headers(token, **{FLEET_ACTOR_HEADER: "maria", **headers}),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_the_role_is_granted_from_the_nodes_own_table(app: Any, fleet_token: str) -> None:
    session = admitted(app, fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "operator"})

    assert session["role"] == "operator"
    assert set(session["permissions"]) == set(OPERATOR)


def test_a_standard_node_gives_the_role_admin_everything_its_ceiling_lets_a_central_do(
    app: Any, fleet_token: str
) -> None:
    # Item 83: the node decides what "admin" holds, by its own controls. Reading accounts and the
    # audit trail comes with it; governing them never reaches a central (NEVER_FLEET_PERMISSIONS).
    session = admitted(app, fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "admin"})

    held = set(session["permissions"])
    assert Permission.ACCOUNTS_READ in held and Permission.AUDIT_READ in held
    assert not held & {
        Permission.SECURITY_MANAGE,
        Permission.ACCOUNTS_MANAGE,
        Permission.AUDIT_MANAGE,
    }


def test_a_node_whose_administrator_is_saved_split_gives_admin_the_ens_matrix(
    app: Any, fleet_token: str
) -> None:
    RoleManager().update("admin", permissions=sorted(ADMIN))

    session = admitted(app, fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "admin"})

    held = set(session["permissions"])
    assert Permission.ACCOUNTS_READ not in held and Permission.AUDIT_READ not in held
    assert held <= set(ADMIN)


def test_a_framework_on_the_node_does_not_cut_what_its_roles_hold(
    app: Any, fleet_token: str
) -> None:
    # A framework is judged on the roles as they are; it takes nothing from them behind the back.
    config = Config()
    headers = {FLEET_ACTOR_ROLE_HEADER: "admin"}

    standard = set(admitted(app, fleet_token, **headers)["permissions"])
    config.set("security.roles_split", True)
    with_the_control = set(admitted(app, fleet_token, **headers)["permissions"])
    RoleManager().update("admin", permissions=sorted(ADMIN))
    split = set(admitted(app, fleet_token, **headers)["permissions"])
    RoleManager().reset("admin")
    put_back = set(admitted(app, fleet_token, **headers)["permissions"])

    assert with_the_control == standard
    assert split < standard
    assert put_back == standard


# ---------------------------------------------------- a 4.1 central says what the person may do


def says(*permissions: str) -> dict[str, str]:
    return {FLEET_ACTOR_PERMISSIONS_HEADER: ",".join(permissions)}


def test_a_central_that_says_what_the_person_may_do_is_believed_within_the_ceiling(
    app: Any, fleet_token: str
) -> None:
    # A role the operator edited on the central: not what this node's "operator" means.
    session = admitted(
        app, fleet_token, **says("apps.read", "apps.deploy", "databases.write", "self")
    )

    assert set(session["permissions"]) == {"apps.read", "apps.deploy", "databases.write", "self"}


def test_what_no_central_may_have_is_never_granted_whatever_the_central_says(
    app: Any, fleet_token: str
) -> None:
    session = admitted(
        app,
        fleet_token,
        **says(*sorted(NEVER_FLEET_PERMISSIONS), "apps.read", "apps.manage"),
    )

    held = set(session["permissions"])
    assert not held & NEVER_FLEET_PERMISSIONS
    assert {"apps.read", "apps.manage"} <= held


def test_what_reaches_the_host_waits_for_the_node_to_say_so(app: Any, fleet_token: str) -> None:
    session = admitted(
        app, fleet_token, **says("apps.read", "root_equivalent", "server.host_access")
    )

    assert set(session["permissions"]) == {"apps.read", "self"}


def test_the_nodes_ceiling_narrows_what_the_central_says(
    app: Any, fleet_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(policy, "current_access", lambda store=None: policy.FleetAccess("read"))

    session = admitted(app, fleet_token, **says("apps.read", "apps.deploy", "apps.manage"))

    assert set(session["permissions"]) == {"apps.read", "self"}


def test_what_the_central_says_wins_over_the_name_and_the_scope(app: Any, fleet_token: str) -> None:
    session = admitted(
        app,
        fleet_token,
        **{
            FLEET_ACTOR_ROLE_HEADER: "admin",
            FLEET_ACTOR_SCOPE_HEADER: "admin",
            **says("apps.read"),
        },
    )

    assert set(session["permissions"]) == {"apps.read", "self"}
    assert session["scope"] == "read"


def test_a_permission_this_release_does_not_know_is_left_out_not_guessed(
    app: Any, fleet_token: str
) -> None:
    # A central newer than the node may name a permission the node cannot judge.
    session = admitted(app, fleet_token, **says("apps.read", "teleport.anywhere"))

    assert set(session["permissions"]) == {"apps.read", "self"}


def test_a_person_who_may_do_nothing_holds_only_the_nodes_own_check_of_the_token(
    app: Any, fleet_token: str
) -> None:
    session = admitted(app, fleet_token, **{FLEET_ACTOR_PERMISSIONS_HEADER: ""})

    assert set(session["permissions"]) == {"self"}


@pytest.mark.parametrize("header", ["apps.read;drop", "Apps.Read", "a" * 80, ",".join(["x"] * 65)])
def test_a_malformed_list_of_permissions_is_refused(
    app: Any, fleet_token: str, header: str
) -> None:
    response = client_from(app).get(
        "/api/auth/session",
        headers=fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "maria", FLEET_ACTOR_PERMISSIONS_HEADER: header}
        ),
    )

    assert response.status_code == 400


def test_a_permission_the_ceiling_withheld_says_so_when_the_central_named_it(
    app: Any, fleet_token: str
) -> None:
    denied = client_from(app).post(
        "/api/services",
        json={"name": "worker", "command": "/usr/bin/true"},
        headers=fleet_headers(
            fleet_token,
            **{
                FLEET_ACTOR_HEADER: "maria",
                **says("apps.read", "root_equivalent", "server.manage"),
            },
        ),
    )

    assert denied.status_code == 403
    assert (
        denied.json()["detail"]
        == "This server does not let a central use the 'root_equivalent' permission"
    )


def test_the_role_wins_over_the_scope(app: Any, fleet_token: str) -> None:
    session = admitted(
        app, fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "viewer", FLEET_ACTOR_SCOPE_HEADER: "admin"}
    )

    assert set(session["permissions"]) == set(VIEWER)
    assert session["scope"] == "read"


def test_an_unknown_role_only_reads(app: Any, fleet_token: str) -> None:
    session = admitted(app, fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "superuser"})

    assert session["role"] == "viewer"
    assert set(session["permissions"]) == set(VIEWER)


def test_a_malformed_role_is_refused(app: Any, fleet_token: str) -> None:
    response = client_from(app).get(
        "/api/auth/session",
        headers=fleet_headers(fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "Admin; drop"}),
    )

    assert response.status_code == 400


def test_the_nodes_ceiling_narrows_the_role(
    app: Any, fleet_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(policy, "current_access", lambda store=None: policy.FleetAccess("read"))

    session = admitted(app, fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "admin"})

    assert set(session["permissions"]) <= {p for p in ADMIN_STANDARD if p.endswith(".read")} | {
        Permission.SELF
    }
    denied = client_from(app).post(
        "/api/jobs/update",
        json={},
        headers=fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "maria", FLEET_ACTOR_ROLE_HEADER: "admin"}
        ),
    )
    assert denied.status_code == 403
    assert denied.json()["error"] == "permission_denied"


def test_a_security_officer_still_cannot_reach_the_nodes_credentials(
    app: Any, fleet_token: str
) -> None:
    response = client_from(app).get(
        "/api/auth/tokens",
        headers=fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "maria", FLEET_ACTOR_ROLE_HEADER: "security"}
        ),
    )

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_a_permission_the_ceiling_withholds_says_how_the_node_grants_it(
    app: Any, fleet_token: str
) -> None:
    # The default ceiling is admin without host access: a raw unit is root-equivalent.
    denied = client_from(app).post(
        "/api/services",
        json={"name": "worker", "command": "/usr/bin/true"},
        headers=fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "maria", FLEET_ACTOR_ROLE_HEADER: "admin"}
        ),
    )

    assert denied.status_code == 403
    body = denied.json()
    assert body["error"] == "permission_denied"
    assert (
        body["detail"] == "This server does not let a central use the 'root_equivalent' permission"
    )
    assert "noust fleet access --level admin --host-access on" in body["hint"]


def test_a_permission_the_role_lacks_still_names_the_role(app: Any, fleet_token: str) -> None:
    denied = client_from(app).post(
        "/api/services",
        json={"name": "worker", "command": "/usr/bin/true"},
        headers=fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "maria", FLEET_ACTOR_ROLE_HEADER: "operator"}
        ),
    )

    assert denied.status_code == 403
    assert "which operator does not hold" in denied.json()["detail"]


@pytest.mark.parametrize(
    ("permission", "level"),
    [("apps.deploy", "deploy"), ("backups.run", "deploy"), ("server.manage", "admin")],
)
def test_the_hint_names_the_lowest_ceiling_that_grants_the_permission(
    permission: str, level: str
) -> None:
    from noust.web.permissions.enforce import _ceiling_hint

    hint = _ceiling_hint(permission)

    assert hint == (
        f"To let this central do it, run 'noust fleet access --level {level}' "
        "on this server, as root."
    )
