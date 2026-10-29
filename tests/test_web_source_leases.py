# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/sources/leases``: the central hands a server the access to clone, and nobody else may.

What is pinned: only a central (a fleet token) can lease; the token is kept sealed, never answered
back and never in the audit trail; what is not a lease is refused with the validator's words; a
server's operator can read what it holds and when it ends, with the last call of each central;
and every route of the area has a permission and a scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.integrations.github.leases import SourceLeases
from noust.web.api.deps import install_error_handlers, require_auth
from noust.web.api.source_leases import router
from noust.web.permissions.registry import permission_for_route
from noust.web.permissions.scopes import INSTANCE, rule_for

TOKEN = "ghs_leased_by_nas"

CENTRAL: dict[str, Any] = {
    "type": "api_token",
    "fleet": True,
    "token_name": "fleet-nas",
    "permissions": {"apps.deploy", "apps.read"},
}
PERSON: dict[str, Any] = {
    "type": "session",
    "username": "yago",
    "account_id": 1,
    "permissions": ["apps.deploy", "apps.read"],
}

LEASE: dict[str, Any] = {
    "key": "github.com:77:0",
    "host": "github.com",
    "installation_id": 77,
    "repositories": ["you/app", "you/api"],
    "token": TOKEN,
    "expires_in": 3600,
    "permissions": {"contents": "read"},
}


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def client_as(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/sources")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def central() -> TestClient:
    return client_as(CENTRAL)


@pytest.fixture
def person() -> TestClient:
    return client_as(PERSON)


class TestTheCentralLeases:
    def test_a_lease_is_kept_and_answered_without_its_token(self, central: TestClient) -> None:
        response = central.put("/api/sources/leases", json=LEASE)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["central"] == "nas" and body["key"] == "github.com:77:0"
        assert body["repositories"] == ["you/api", "you/app"]
        assert body["permissions"] == {"contents": "read"}
        assert 3500 < body["expires_in"] <= 3600 and body["ended"] is False
        assert TOKEN not in response.text
        (held,) = SourceLeases().list()
        assert SourceLeases().token_of(held) == TOKEN

    def test_leasing_again_replaces_it(self, central: TestClient) -> None:
        central.put("/api/sources/leases", json=LEASE)

        central.put(
            "/api/sources/leases",
            json={**LEASE, "token": "ghs_second", "repositories": ["you/app"]},
        )

        (held,) = SourceLeases().list()
        assert held.repositories == ("you/app",)
        assert SourceLeases().token_of(held) == "ghs_second"

    def test_what_is_not_a_lease_is_refused_in_the_validators_words_and_keeps_nothing(
        self, central: TestClient
    ) -> None:
        for wrong in (
            {"token": "ghs a\nb"},
            {"repositories": ["../x"]},
            {"expires_in": 99999},
            {"permissions": {"contents": "write"}},
            {"host": "https://github.com"},
        ):
            response = central.put("/api/sources/leases", json={**LEASE, **wrong})
            assert response.status_code in (400, 422), (wrong, response.text)
            assert TOKEN not in response.text and "ghs a" not in response.text

        assert SourceLeases().list() == []

    def test_a_lease_that_misses_a_field_is_refused(self, central: TestClient) -> None:
        for name in ("key", "host", "installation_id", "repositories", "token", "expires_in"):
            body = {k: v for k, v in LEASE.items() if k != name}
            assert central.put("/api/sources/leases", json=body).status_code == 422, name

    def test_the_central_takes_back_a_lease_or_all_of_its_own(self, central: TestClient) -> None:
        central.put("/api/sources/leases", json=LEASE)
        central.put("/api/sources/leases", json={**LEASE, "key": "second", "token": "ghs_two"})

        one = central.delete("/api/sources/leases", params={"key": "second"})
        assert one.json() == {"dropped": 1}
        assert [lease.key for lease in SourceLeases().list()] == ["github.com:77:0"]

        everything = central.delete("/api/sources/leases")
        assert everything.json() == {"dropped": 1}
        assert SourceLeases().list() == []

    def test_a_central_cannot_take_back_another_centrals_lease(self, central: TestClient) -> None:
        SourceLeases().hold(
            central="edge",
            key="github.com:77:0",
            host="github.com",
            installation_id=77,
            repositories=["you/app"],
            token="ghs_edge",
            expires_in=3600,
        )

        assert central.delete("/api/sources/leases").json() == {"dropped": 0}
        assert [lease.central for lease in SourceLeases().list()] == ["edge"]


class TestNobodyElseLeases:
    def test_a_person_cannot_lease_a_server_a_token(self, person: TestClient) -> None:
        response = person.put("/api/sources/leases", json=LEASE)

        assert response.status_code == 403
        assert response.json()["coded"]["code"] == "sources.central_only"
        assert SourceLeases().list() == []

    def test_a_person_cannot_drop_a_central_leases(
        self, person: TestClient, central: TestClient
    ) -> None:
        central.put("/api/sources/leases", json=LEASE)

        response = person.delete("/api/sources/leases")

        assert response.status_code == 403
        assert len(SourceLeases().list()) == 1


class TestWhatAServersOperatorSees:
    def test_the_leases_with_when_each_ends_and_never_the_token(
        self, central: TestClient, person: TestClient
    ) -> None:
        central.put("/api/sources/leases", json=LEASE)

        body = person.get("/api/sources/leases").json()

        assert [(i["central"], i["repositories"]) for i in body["items"]] == [
            ("nas", ["you/api", "you/app"])
        ]
        assert TOKEN not in str(body)

    def test_a_lease_that_ended_says_so(self, central: TestClient, person: TestClient) -> None:
        SourceLeases(clock=lambda: 1.0).hold(
            central="nas",
            key="old",
            host="github.com",
            installation_id=77,
            repositories=["you/app"],
            token="ghs_old",
            expires_in=60,
        )

        (item,) = person.get("/api/sources/leases").json()["items"]

        assert item["ended"] is True and item["expires_in"] == 0


class TestEveryRouteIsClassified:
    def test_each_route_has_a_permission_and_the_servers_scope(self) -> None:
        routes: list[Any] = [r for r in router.routes if hasattr(r, "methods")]
        assert routes
        for route in routes:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                template = "/api/sources" + route.path
                assert permission_for_route(method, template) is not None, (method, template)
                rule = rule_for(method, template)
                assert rule is not None and rule.scoping == INSTANCE, (method, template)
