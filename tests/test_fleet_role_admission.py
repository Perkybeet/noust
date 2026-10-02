"""
A node admits a central's request with the operator's role, within its ceiling.

Accounts live on the central; a node learns who acts through the headers the
central sends over the tunnel. The node never trusts what the central says the
operator may do: it looks the role up in its own table
(:mod:`noust.web.permissions.roles`) and intersects that with its own ceiling
(:func:`noust.fleet.policy.permits`), and an unknown role only reads.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import policy
from noust.web.auth import FLEET_ACTOR_HEADER, FLEET_ACTOR_ROLE_HEADER, FLEET_ACTOR_SCOPE_HEADER
from noust.web.permissions import Permission
from noust.web.permissions.roles import ADMIN, OPERATOR, VIEWER
from noust.web.server import create_app, get_token_manager
from tests.test_fleet_node_security import client_from, fleet_headers
from tests.test_web_auth import make_config


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

    assert set(session["permissions"]) <= {p for p in ADMIN if p.endswith(".read")} | {
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
