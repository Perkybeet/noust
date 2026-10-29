# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node does when its central leases it access to GitHub, over the real node application.

The real fleet token, the real routes, the real ceiling. What is pinned: a central at the ``deploy``
scope that its node's ceiling allows is taken; the same central held to ``read`` (by its node or by
the scope it speaks at) is not; a person, even the master token, cannot put a token here; and
the audit trail names the lease and never the token.
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
from noust.integrations.github.leases import SourceLeases
from noust.web.auth import FLEET_ACTOR_HEADER, FLEET_ACTOR_SCOPE_HEADER
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_node_security import client_from, fleet_headers
from tests.support.web_auth import make_config

TOKEN = "ghs_admission_token"
LEASE = {
    "key": "github.com:77:0",
    "host": "github.com",
    "installation_id": 77,
    "repositories": ["you/app"],
    "token": TOKEN,
    "expires_in": 3600,
}


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    return create_app(make_config(sandbox))


@pytest.fixture
def fleet_token(app: Any) -> str:
    return str(get_token_manager().create_fleet_token("fleet-nas")["token"])


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def speaking(token: str, scope: str = "deploy") -> dict[str, str]:
    """What the keeper sends: its actor and the scope it speaks at."""
    return fleet_headers(
        token, **{FLEET_ACTOR_HEADER: "noust-fleet-sources", FLEET_ACTOR_SCOPE_HEADER: scope}
    )


def lease(app: Any, headers: dict[str, str]) -> Any:
    return client_from(app).put("/api/sources/leases", json=LEASE, headers=headers)


class TestACentralLeases:
    @pytest.mark.parametrize("level", ["deploy", "admin"])
    def test_a_central_at_the_deploy_scope_is_taken_when_the_ceiling_allows(
        self, app: Any, fleet_token: str, store: NoustStore, level: str
    ) -> None:
        policy.set_access(policy.FleetAccess(level), store=store)  # type: ignore[arg-type]

        response = lease(app, speaking(fleet_token))

        assert response.status_code == 200, response.text
        assert TOKEN not in response.text
        (held,) = SourceLeases().list()
        assert held.central == "nas" and SourceLeases().token_of(held) == TOKEN

    def test_a_node_that_lets_its_central_only_read_refuses_it(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        policy.set_access(policy.FleetAccess("read"), store=store)

        response = lease(app, speaking(fleet_token))

        assert response.status_code == 403
        assert SourceLeases().list() == []

    def test_a_central_speaking_at_the_read_scope_cannot_lease(
        self, app: Any, fleet_token: str
    ) -> None:
        assert lease(app, speaking(fleet_token, "read")).status_code == 403
        assert lease(app, fleet_headers(fleet_token)).status_code == 403
        assert SourceLeases().list() == []

    def test_the_central_reads_what_it_leased_at_the_read_scope(
        self, app: Any, fleet_token: str
    ) -> None:
        lease(app, speaking(fleet_token))

        response = client_from(app).get(
            "/api/sources/leases", headers=speaking(fleet_token, "read")
        )

        assert response.status_code == 200
        assert [i["key"] for i in response.json()["items"]] == ["github.com:77:0"]
        assert TOKEN not in response.text


class TestACentralForwardsAnEvent:
    EVENT = {
        "kind": "push",
        "delivery": "d-1",
        "host": "github.com",
        "default_branch": "main",
        "event": {
            "forge": "github",
            "repository": "you/app",
            "clone_url": "https://github.com/you/app.git",
            "branch": "main",
            "head_sha": "a" * 40,
        },
    }

    def forward(self, app: Any, headers: dict[str, str]) -> Any:
        return client_from(app).post("/api/sources/events", json=self.EVENT, headers=headers)

    def test_a_central_at_the_deploy_scope_is_heard_when_the_ceiling_allows(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        policy.set_access(policy.FleetAccess("deploy"), store=store)

        response = self.forward(app, speaking(fleet_token))

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ignored"

    def test_a_node_that_lets_its_central_only_read_does_not_deploy_for_it(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        policy.set_access(policy.FleetAccess("read"), store=store)

        assert self.forward(app, speaking(fleet_token)).status_code == 403

    def test_a_central_speaking_at_the_read_scope_cannot_deploy(
        self, app: Any, fleet_token: str
    ) -> None:
        assert self.forward(app, speaking(fleet_token, "read")).status_code == 403

    def test_the_master_token_is_not_a_central(self, app: Any) -> None:
        master = str(get_token_manager().create_api_token("owner", scope="admin")["token"])

        response = self.forward(app, fleet_headers(master))

        assert response.status_code == 403
        assert response.json()["coded"]["code"] == "sources.central_only"


class TestNobodyElse:
    def test_the_master_token_is_not_a_central_and_leases_nothing(self, app: Any) -> None:
        master = str(get_token_manager().create_api_token("owner", scope="admin")["token"])

        response = lease(app, fleet_headers(master))

        assert response.status_code == 403
        assert response.json()["coded"]["code"] == "sources.central_only"
        assert SourceLeases().list() == []

    def test_a_lease_is_audited_by_name_and_never_with_the_token(
        self, app: Any, fleet_token: str, tmp_path: Path
    ) -> None:
        lease(app, speaking(fleet_token))

        from noust.core import audit

        events = list(audit.get_log().read(action="sources.lease.hold"))
        assert events
        assert TOKEN not in str(events)
        assert events[-1]["details"]["repositories"] == 1
