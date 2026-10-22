# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one answer to "may this port be taken" (checklist E-44).

What is pinned: every holder of a port is one source of the same registry (the machine's
own services, applications and their copies, the ports a Compose stack publishes, published
streams, databases made reachable, the engines Noust runs itself, and what the kernel says
listens), TCP and UDP are different ports, an address that cannot overlap does not conflict,
the caller's own claims never count against it, and the sentence names the holder without
naming an application a caller may not see.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.database_exposures import ExposureStore
from noust.core.db_instance_store import DbInstanceStore
from noust.core.exceptions import PortError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, AppType, NoustStore
from noust.core.stream_store import OPEN, StreamExposure, StreamStore
from noust.core.teams.scope import Scope, bind
from noust.managers import port_claims as claims_module
from noust.managers.port_claims import PortClaim, PortClaims

LISTEN_TCP = 'LISTEN 0 511 0.0.0.0:9090 0.0.0.0:* users:(("prometheus",pid=7,fd=9))\n'

pytestmark = pytest.mark.real_listeners


@pytest.fixture(autouse=True)
def config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        claims_module, "Config", lambda: SimpleNamespace(get=lambda key, default=None: default)
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    fake.script(("ss", "-ltnpH"), stdout="")
    fake.script(("ss", "-lunpH"), stdout="")
    return fake


@pytest.fixture
def claims(store: NoustStore, runner: FakeRunner) -> PortClaims:
    return PortClaims(runner=runner, store=store)


def add_stream(store: NoustStore, *, name: str = "mc", port: int = 25565, protocol: str = "tcp"):
    StreamStore(store).create(
        StreamExposure(
            name=name,
            protocol=protocol,
            listen_port=port,
            target_host="127.0.0.1",
            target_port=25565,
            state=OPEN,
        )
    )


def test_the_machines_own_services_hold_their_ports(claims: PortClaims) -> None:
    for port in (80, 443, 22, 8080):
        holder = claims.holder(port)
        assert holder is not None
        assert holder.kind == "system"


def test_a_free_port_has_no_holder(claims: PortClaims) -> None:
    assert claims.holder(4321) is None
    assert claims.require_free(4321) == 4321


def test_an_application_holds_its_port_and_the_one_after_in_zero_downtime(
    claims: PortClaims, store: NoustStore
) -> None:
    store.create_app(App(domain="shop.example.com", app_path="/srv/shop", port=3000))
    store.create_app(App(domain="api.example.com", app_path="/srv/api", port=3100))
    store.set_zero_downtime("api.example.com", True)

    assert claims.holder(3000).kind == "app"
    assert claims.holder(3100).kind == "app"
    assert claims.holder(3101).kind == "app"
    assert claims.holder(3001) is None


def test_the_sentence_names_an_application_to_whoever_sees_them_all(
    claims: PortClaims, store: NoustStore
) -> None:
    store.create_app(App(domain="shop.example.com", app_path="/srv/shop", port=3000))

    with pytest.raises(PortError) as shown:
        claims.require_free(3000)

    assert "shop.example.com" in shown.value.message


def test_a_team_is_not_told_whose_port_it_is_so_asking_is_no_way_to_list_applications(
    claims: PortClaims, store: NoustStore
) -> None:
    store.create_app(App(domain="shop.example.com", app_path="/srv/shop", port=3000))

    with bind(Scope(team_roles={1: "admin"})):
        with pytest.raises(PortError) as hidden:
            claims.require_free(3000)
        assert "another application" in hidden.value.message
        assert "shop.example.com" not in hidden.value.message + hidden.value.details

        with pytest.raises(PortError) as shown:
            claims.require_free(3000, reveal=True)
        assert "shop.example.com" in shown.value.message


def test_the_caller_can_take_back_its_own_port(claims: PortClaims, store: NoustStore) -> None:
    store.create_app(App(domain="shop.example.com", app_path="/srv/shop", port=3000))

    assert claims.require_free(3000, owner="shop.example.com") == 3000


def test_a_conflict_is_a_port_error_and_a_validation_error_naming_the_field(
    claims: PortClaims,
) -> None:
    with pytest.raises(ValidationError) as raised:
        claims.require_free(443, field="listen_port")

    assert isinstance(raised.value, PortError)
    assert raised.value.field == "listen_port"
    assert "the web server's" in raised.value.message


def test_a_published_stream_holds_its_port_in_its_protocol_only(
    claims: PortClaims, store: NoustStore
) -> None:
    add_stream(store, port=25565, protocol="tcp")

    assert claims.holder(25565, "tcp").kind == "stream"
    assert claims.holder(25565, "tcp").owner == "mc"
    assert claims.holder(25565, "udp") is None


def test_a_database_made_reachable_holds_its_public_port(
    claims: PortClaims, store: NoustStore
) -> None:
    ExposureStore(store).create(
        engine="postgresql",
        db_name="shop",
        port=25570,
        bind_address="0.0.0.0",  # noqa: S104 - a recorded binding
        sources=(),
        tls="required",
        remote_user="r",
        profile="owner",
        unit="u",
        created_by=None,
    )

    holder = claims.holder(25570)
    assert holder is not None and holder.kind == "database"
    assert "postgresql/shop" in holder.owner


def test_what_the_kernel_says_listens_is_a_holder_with_its_process(
    claims: PortClaims, runner: FakeRunner
) -> None:
    runner.script(("ss", "-ltnpH"), stdout=LISTEN_TCP)

    holder = claims.holder(9090)
    assert holder is not None and holder.kind == "process"
    assert "prometheus" in holder.owner
    with pytest.raises(PortError, match=r"already in use \(prometheus\)"):
        claims.require_free(9090)


def test_the_live_sockets_can_be_left_out(claims: PortClaims, runner: FakeRunner) -> None:
    runner.script(("ss", "-ltnpH"), stdout=LISTEN_TCP)

    assert claims.holder(9090, listening=False) is None


def test_an_address_that_cannot_overlap_does_not_conflict(
    claims: PortClaims, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 511 127.0.0.1:9090 0.0.0.0:* users:(("prometheus",pid=7,fd=9))\n',
    )

    assert claims.holder(9090, address="192.168.1.5") is None
    assert claims.holder(9090, address="127.0.0.1") is not None
    assert claims.holder(9090) is not None


def test_a_compose_stack_holds_the_ports_it_publishes(
    store: NoustStore, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create_app(
        App(
            domain="blog.example.com",
            app_path="/srv/blog",
            port=8081,
            app_type=AppType.DOCKER_COMPOSE.value,
        )
    )
    mappings = claims_module.parse_ports(["8081:80", "127.0.0.1:5433:5432", "53:53/udp"])
    monkeypatch.setattr(claims_module, "stack_published_ports", lambda app, runner=None: mappings)
    claims = PortClaims(runner=runner, store=store)

    assert claims.holder(5433).kind == "stack"
    assert claims.holder(5433, address="192.168.1.5") is None
    assert claims.holder(53, "udp").kind == "stack"
    assert claims.holder(53, "tcp") is None
    # the same stack asking again is not in its own way
    assert claims.holder(5433, owner="blog.example.com") is None


def test_a_stack_that_cannot_be_read_holds_only_what_the_store_records(
    store: NoustStore, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create_app(
        App(
            domain="blog.example.com",
            app_path="/srv/blog",
            port=8081,
            app_type=AppType.DOCKER_COMPOSE.value,
        )
    )

    def unreadable(app: App, runner: object = None) -> list[object]:
        raise OSError("no such file")

    monkeypatch.setattr(claims_module, "stack_published_ports", unreadable)
    claims = PortClaims(runner=runner, store=store)

    assert claims.holder(8081).kind == "app"
    assert claims.holder(5433) is None


def test_a_database_engine_noust_runs_holds_its_loopback_port(
    store: NoustStore, runner: FakeRunner
) -> None:
    DbInstanceStore(store).create(
        name="pg16",
        flavour="postgresql",
        version="16",
        image="postgres",
        digest="sha256:" + "a" * 64,
        host_port=5433,
        memory_limit_mb=512,
        created_by=None,
    )
    claims = PortClaims(runner=runner, store=store)

    holder = claims.holder(5433)
    assert holder is not None and holder.kind == "instance"
    assert holder.owner == "pg16"


def test_choosing_skips_everything_that_is_held(
    claims: PortClaims, store: NoustStore, runner: FakeRunner
) -> None:
    store.create_app(App(domain="a.example.com", app_path="/srv/a", port=3000))
    add_stream(store, port=3001)
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 511 0.0.0.0:3002 0.0.0.0:* users:(("node",pid=7,fd=9))\n',
    )

    assert claims.choose(preferred=3000, start=3000, end=3100) == 3003


def test_choosing_in_a_full_range_returns_none(claims: PortClaims, store: NoustStore) -> None:
    store.create_app(App(domain="a.example.com", app_path="/srv/a", port=3000))

    assert claims.choose(preferred=3000, start=3000, end=3001) is None


def test_taken_ports_is_every_claim_of_the_protocol(claims: PortClaims, store: NoustStore) -> None:
    store.create_app(App(domain="a.example.com", app_path="/srv/a", port=3000))
    add_stream(store, port=25565, protocol="udp")

    assert {3000, 80, 443} <= claims.taken_ports()
    assert 25565 not in claims.taken_ports("tcp")
    assert 25565 in claims.taken_ports("udp")


def test_a_stack_about_to_publish_a_port_another_holds_is_refused_with_the_holder(
    claims: PortClaims, store: NoustStore
) -> None:
    store.create_app(App(domain="a.example.com", app_path="/srv/a", port=3000))
    mappings = claims_module.parse_ports(["3000:80", "8443:443"])

    with pytest.raises(PortError, match="3000"):
        claims.require_stack_free("blog.example.com", mappings)


def test_a_stack_may_keep_the_ports_its_own_containers_hold(
    claims: PortClaims, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 4096 0.0.0.0:8081 0.0.0.0:* users:(("docker-proxy",pid=7,fd=9))\n',
    )
    mappings = claims_module.parse_ports(["8081:80"])

    with pytest.raises(PortError, match="8081"):
        claims.require_stack_free("blog.example.com", mappings)
    claims.require_stack_free("blog.example.com", mappings, own={(8081, "tcp")})


def test_a_published_port_that_docker_chooses_cannot_conflict(claims: PortClaims) -> None:
    mappings = claims_module.parse_ports(["80", "127.0.0.1::3000"])

    claims.require_stack_free("blog.example.com", mappings)


def test_a_claim_says_what_it_is() -> None:
    claim = PortClaim(port=22, protocol="tcp", kind="system", owner="SSH's")

    assert claim.describe() == "is SSH's"
