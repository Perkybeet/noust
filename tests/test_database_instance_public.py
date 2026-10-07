# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A database on an instance Noust runs made public, the way the host's engines are (checklist C-22).

An instance is published on 127.0.0.1 by Noust and nothing else, so the public
port is a proxy to that loopback port, exactly as for the host's engine, and the
instance itself stays private. What is pinned is where the one difference sits:
a container Noust did not make is still refused (its compose file publishes its
ports), and TLS cannot be required of an account inside an instance, so it is
plaintext only when the operator says so.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.db_instance_store import DbInstanceStore
from noust.core.exceptions import ValidationError
from noust.core.runner import set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.database import public_access
from noust.managers.database.instance_catalog import SPECS
from noust.managers.database.instance_records import assign_managed
from noust.managers.database.instances import DatabaseInstance
from noust.managers.database.public_access import PublicAccess
from noust.managers.database.service import DatabaseService
from tests.database_public_support import PublicHostRunner, fresh_state, public_engine
from tests.test_database_public import World

KEY = "postgresql@pg16"
DATABASE = "appdb"


def build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tls_blocker: str | None = "no TLS here",
    foreign: bool = False,
) -> World:
    """A server with a PostgreSQL instance Noust made on port 5441."""
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "state" / "noust.db")
    units = tmp_path / "systemd"
    units.mkdir()
    proxy = tmp_path / "systemd-socket-proxyd"
    proxy.write_text("#!/bin/sh\n")
    monkeypatch.setattr(public_access, "SYSTEMD_DIR", units)
    monkeypatch.setattr(public_access, "PROXY_CANDIDATES", (proxy,))
    state = fresh_state()
    base = public_engine(tmp_path, state, tls_blocker=tls_blocker)

    class Instance(base):  # type: ignore[valid-type, misc]
        def server_port(self) -> int:
            return self.instance.port if self.instance is not None else 5432

    spec = SPECS["postgresql"]
    pin = spec.pins[1]
    DbInstanceStore(store).create(
        name="pg16",
        flavour="postgresql",
        version=pin.version,
        image=spec.repository,
        digest=pin.digest,
        host_port=5441,
        memory_limit_mb=512,
        created_by="t",
    )
    DbInstanceStore(store).set_state("pg16", "ready")

    def found() -> list[DatabaseInstance]:
        managed = assign_managed([], DbInstanceStore(store).all())
        if foreign:
            from dataclasses import replace

            return [replace(item, managed=None, unit=None) for item in managed]
        return [replace_state(item) for item in managed]

    def replace_state(item: DatabaseInstance) -> DatabaseInstance:
        from dataclasses import replace

        return replace(item, state="running")

    runner = PublicHostRunner(ufw="active")
    set_runner(runner)
    service = DatabaseService(
        resolve=lambda name: Instance() if name.lower() in ("postgresql", "pg") else None,
        engines=lambda: ["postgresql"],
        instances=found,
        store=store,
        secrets=SecretStore(root=tmp_path / "state" / "secrets"),
    )
    access = PublicAccess(service, runner=runner)
    return World(access, service, runner, state, Instance, units, store)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build(tmp_path, monkeypatch)
    built.engine.dbs[DATABASE] = {"owner": "x", "size": None}
    try:
        yield built
    finally:
        built.store.close()
        NoustStore.reset_instance()
        set_runner(None)


def open_it(world: World, **kwargs: Any) -> Any:
    return world.access.expose(KEY, DATABASE, confirm=True, tls="optional", **kwargs)


class TestAnInstanceIsPublicLikeAnyEngine:
    def test_the_plan_forwards_to_the_instances_loopback_port(self, world: World) -> None:
        plan = world.access.plan(KEY, DATABASE, tls="optional")

        assert plan.target == "127.0.0.1:5441"
        assert plan.engine == KEY
        # The unit is spelled where '@' is not allowed: a unit name's '@' means a template.
        assert plan.unit == "noust-dbexpose-postgresql.pg16-5433"
        assert "@" not in plan.unit

    def test_the_proxy_is_wired_to_the_instances_own_unit_and_forwards_to_its_port(
        self, world: World
    ) -> None:
        open_it(world)

        service = (world.units / "noust-dbexpose-postgresql.pg16-5433.service").read_text()
        assert "Requires=noust-dbexpose-postgresql.pg16-5433.socket noust-db-pg16" in service
        assert "127.0.0.1:5441" in service
        socket_unit = (world.units / "noust-dbexpose-postgresql.pg16-5433.socket").read_text()
        assert "ListenStream=0.0.0.0:5433" in socket_unit

    def test_the_record_and_the_connection_name_the_instance_and_the_engines_scheme(
        self, world: World
    ) -> None:
        outcome = open_it(world)

        assert outcome.exposure.engine == KEY
        assert outcome.connection["url"].startswith("postgresql://appdb_remote:")
        assert outcome.connection["port"] == 5433
        (listed,) = world.access.listing()
        assert listed.engine == KEY and listed.db_name == DATABASE

    def test_closing_it_removes_what_opening_made(self, world: World) -> None:
        open_it(world)

        outcome = world.access.make_private(KEY, DATABASE)

        assert outcome.removed
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert "appdb_remote" not in world.state["accounts"]

    def test_the_instance_does_not_say_it_can_require_tls_and_so_plaintext_is_a_choice(
        self, world: World
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            world.access.plan(KEY, DATABASE)

        assert caught.value.field == "tls"
        assert "--allow-plaintext" in caught.value.details

    def test_the_plan_says_the_plaintext_it_accepts(self, world: World) -> None:
        plan = world.access.plan(KEY, DATABASE, tls="optional")

        assert any("cross the network in clear" in warning for warning in plan.warnings)


class TestAContainerNoustDidNotMakeIsStillRefused:
    def test_its_compose_file_publishes_its_ports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = build(tmp_path, monkeypatch, foreign=True)
        try:
            built.engine.dbs[DATABASE] = {"owner": "x", "size": None}
            with pytest.raises(ValidationError, match="compose file"):
                built.access.plan(KEY, DATABASE, tls="optional")
        finally:
            built.store.close()
            NoustStore.reset_instance()
            set_runner(None)
