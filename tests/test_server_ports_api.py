# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/server/ports``: who holds each port of this machine, for the console's "Network and
ports" section (4.1, task 11: open a port or see who uses one).

What is pinned: it is the registry's own answer (:class:`~noust.managers.port_claims.PortClaims`)
and not a second reading of the machine, both protocols are in it, a declared holder (an
application, a stream, the web server) is told apart from the process the kernel reports, and it
is a read: ``server.read``, never a change.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.core.stream_store import OPEN, StreamExposure, StreamStore
from noust.managers import port_claims as claims_module
from noust.web.api import server as server_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from noust.web.permissions.registry import route_map

pytestmark = pytest.mark.real_listeners

LISTEN_TCP = (
    'LISTEN 0 511 0.0.0.0:9090 0.0.0.0:* users:(("prometheus",pid=7,fd=9))\n'
    'LISTEN 0 511 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=8,fd=9))\n'
)
LISTEN_UDP = 'UNCONN 0 0 0.0.0.0:51820 0.0.0.0:* users:(("wg",pid=9,fd=7))\n'


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def client(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    runner = FakeRunner()
    runner.script(("ss", "-ltnpH"), stdout=LISTEN_TCP)
    runner.script(("ss", "-lunpH"), stdout=LISTEN_UDP)
    monkeypatch.setattr(
        claims_module, "Config", lambda: SimpleNamespace(get=lambda key, default=None: default)
    )
    monkeypatch.setattr("noust.web.api.server.ports.get_runner", lambda: runner)
    monkeypatch.setattr("noust.web.api.server.ports.get_store", lambda: store)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(server_api.router, prefix="/api/server")
    app.dependency_overrides[get_current_session] = lambda: {"sid": "operator", "type": "master"}
    return TestClient(app, raise_server_exceptions=False)


def claims_of(client: TestClient) -> list[dict]:
    response = client.get("/api/server/ports")
    assert response.status_code == 200, response.text
    return response.json()["claims"]


def test_the_ports_are_a_read_of_the_server(client: TestClient) -> None:
    assert route_map()[("GET", "/api/server/ports")] == "server.read"


def test_the_web_server_and_ssh_hold_their_ports_over_both_protocols(client: TestClient) -> None:
    held = {(claim["port"], claim["protocol"], claim["kind"]) for claim in claims_of(client)}

    assert (443, "tcp", "system") in held
    assert (443, "udp", "system") in held
    assert (22, "tcp", "system") in held


def test_the_machines_own_services_are_one_stable_word_each(client: TestClient) -> None:
    system = {
        (claim["port"], claim["protocol"]): claim["owner"]
        for claim in claims_of(client)
        if claim["kind"] == "system"
    }

    assert system[(443, "tcp")] == "web"
    assert system[(22, "tcp")] == "ssh"
    assert system[(8080, "tcp")] == "console"


def test_what_the_kernel_says_listens_is_a_process_and_names_it(client: TestClient) -> None:
    listening = {
        (claim["port"], claim["protocol"]): claim
        for claim in claims_of(client)
        if claim["kind"] == "process"
    }

    assert listening[(9090, "tcp")]["owner"] == "prometheus"
    assert listening[(9090, "tcp")]["processes"] == ["prometheus"]
    assert listening[(9090, "tcp")]["address"] == "0.0.0.0"  # noqa: S104 - the answer, never bound
    assert listening[(3000, "tcp")]["address"] == "127.0.0.1"
    assert listening[(51820, "udp")]["owner"] == "wg"


def test_an_application_and_a_stream_are_told_apart_from_a_process(
    client: TestClient, store: NoustStore
) -> None:
    store.create_app(App(domain="shop.example.net", app_path="/srv/shop", port=3000))
    StreamStore(store).create(
        StreamExposure(
            name="game",
            protocol="udp",
            listen_port=25565,
            target_host="127.0.0.1",
            target_port=25565,
            state=OPEN,
        )
    )

    claims = claims_of(client)
    by_kind = {(claim["kind"], claim["port"], claim["protocol"]): claim for claim in claims}

    assert by_kind[("app", 3000, "tcp")]["owner"] == "shop.example.net"
    assert by_kind[("stream", 25565, "udp")]["owner"] == "game"


def test_a_machine_with_nothing_listening_still_answers_with_what_noust_reserves(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    runner.script(("ss", "-ltnpH"), stdout="")
    runner.script(("ss", "-lunpH"), stdout="")
    monkeypatch.setattr("noust.web.api.server.ports.get_runner", lambda: runner)

    claims = claims_of(client)

    assert claims
    assert {claim["kind"] for claim in claims} == {"system"}


def test_every_claim_is_sorted_by_port_so_the_page_reads_it_in_order(client: TestClient) -> None:
    ports = [claim["port"] for claim in claims_of(client)]

    assert ports == sorted(ports)
