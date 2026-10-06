# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the store's instances meet the containers Docker runs.

Four things are defended. **The row is the authority**: a container that wears
the label is a stranger until a row of that name exists and the container is the
one named after it. **A row without a container is a stopped instance**, listed,
addressable and startable, with the port and the credentials its row knows.
**An instance that is not finished is offered to nobody**, so no application is
given a database on one that cannot be signed in to yet, or no more. And **the
key an instance holds is its own**: a stranger that happens to be named the same
does not shadow it.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from noust.core.db_instance_store import (
    CREATING,
    READY,
    REMOVING,
    UPGRADING,
    InstanceRecord,
)
from noust.core.exceptions import DatabaseEngineError
from noust.core.runner import FakeRunner
from noust.managers.database.instance_catalog import SPECS, resolve_pin
from noust.managers.database.instance_records import assign_managed, key_of, stub_instance
from noust.managers.database.instances import DatabaseInstance, discover
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.settings import settings_for
from tests.test_database_instances import container


def row(
    name: str, flavour: str = "postgresql", version: str | None = None, **kwargs
) -> InstanceRecord:
    spec = SPECS[flavour]
    pin = resolve_pin(spec, version)
    values = {
        "id": 1,
        "name": name,
        "flavour": flavour,
        "version": pin.version,
        "image": spec.repository,
        "digest": pin.digest,
        "previous_digest": None,
        "host_port": 5441,
        "memory_limit_mb": 512,
        "state": READY,
        "created_at": "t",
        "created_by": None,
        "updated_at": "t",
    }
    values.update(kwargs)
    return InstanceRecord(**values)


def found(runner: FakeRunner, *entries: dict) -> list[DatabaseInstance]:
    runner.script(
        ["docker", "ps"],
        stdout="".join(f"{e['Id']}\t{e['Config']['Image']}\n" for e in entries),
    )
    runner.script(["docker", "inspect"], stdout=json.dumps(list(entries)))
    return discover(runner)


def labelled(name: str, record: InstanceRecord, **kwargs) -> dict:
    entry = container(
        name,
        f"{record.image}:{record.version}@{record.digest}",
        env=["POSTGRES_PASSWORD=x"],
        exposed=("5432/tcp",),
        ports={"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(record.host_port)}]},
        **kwargs,
    )
    entry["Config"]["Labels"]["io.noust.db-instance"] = record.name
    return entry


class TestAKeyIsBuiltFromTheRow:
    @pytest.mark.parametrize(
        ("flavour", "key"),
        [
            ("postgresql", "postgresql@x"),
            ("mysql", "mysql@x"),
            ("mariadb", "mysql@x"),
            ("redis", "redis@x"),
            ("valkey", "redis@x"),
            ("mongodb", "mongodb@x"),
        ],
    )
    def test_it_is_the_managers_engine_and_the_instances_name(self, flavour: str, key: str) -> None:
        assert key_of(row("x", flavour)) == key


class TestAnInstanceWhoseContainerIsGone:
    def test_a_stub_knows_its_port_its_engine_its_unit_and_where_the_password_is(self) -> None:
        stub = stub_instance(row("pg16", version="16", host_port=5441))

        assert stub.key == "postgresql@pg16"
        assert stub.state == "exited" and not stub.running
        assert stub.port == 5441 and stub.reach == "host"
        assert stub.unit == "noust-db-pg16" and stub.container == "noust-db-pg16"
        assert stub.managed == "pg16" and stub.version == "16" and stub.memory_limit_mb == 512
        assert stub.env_names == frozenset({"POSTGRES_PASSWORD"})
        assert stub.admin_user == "postgres" and stub.access == "full"
        assert (stub.project, stub.service, stub.app) == (None, None, None)

    @pytest.mark.parametrize(
        ("flavour", "variable", "image_flavour"),
        [
            ("mysql", "MYSQL_ROOT_PASSWORD", "mysql"),
            ("mariadb", "MARIADB_ROOT_PASSWORD", "mariadb"),
            ("redis", "REDIS_PASSWORD", "redis"),
            ("valkey", "VALKEY_PASSWORD", "valkey"),
            ("mongodb", "MONGO_INITDB_ROOT_PASSWORD", "mongo"),
        ],
    )
    def test_each_engines_password_variable_is_the_one_its_client_script_reads(
        self, flavour: str, variable: str, image_flavour: str
    ) -> None:
        stub = stub_instance(row("x", flavour))

        assert variable in stub.env_names
        assert stub.flavour == image_flavour
        sources = stub.launch(stub.engine).candidates or stub.launch("mongodump").candidates
        assert variable in sources or stub.engine == "redis"
        if stub.engine == "redis":
            assert stub.requires_password

    def test_mysql_is_signed_in_as_root_and_mongodb_as_noust_admin(self) -> None:
        assert stub_instance(row("m", "mysql")).admin_user == "root"
        assert stub_instance(row("d", "mongodb")).admin_user == "noust_admin"

    def test_a_row_naming_an_image_noust_does_not_recognise_is_refused(self) -> None:
        with pytest.raises(Exception, match="not an image Noust recognises"):
            stub_instance(row("pg16", image="ghcr.io/acme/postgres"))


class TestWhichContainersAreInstances:
    def test_a_container_named_after_a_row_and_labelled_is_the_instance(
        self, runner: FakeRunner
    ) -> None:
        record = row("pg16", version="16")
        containers = found(runner, labelled("noust-db-pg16", record))

        (instance,) = assign_managed(containers, [record])

        assert instance.key == "postgresql@pg16"
        assert instance.managed == "pg16" and instance.version == "16"
        assert instance.unit == "noust-db-pg16" and instance.memory_limit_mb == 512
        assert instance.container_id, "it is the real container, not a stand-in"
        assert (instance.project, instance.service, instance.app) == (None, None, None)

    def test_a_label_alone_makes_nothing_noust_s(self, runner: FakeRunner) -> None:
        record = row("pg16", version="16")
        stranger = labelled("noust-db-pg16", record)

        (kept,) = assign_managed(found(runner, stranger), [])

        assert kept.managed is None
        assert kept.key == "postgresql@noust-db-pg16"

    def test_a_label_on_a_container_not_named_after_the_row_is_a_stranger(
        self, runner: FakeRunner
    ) -> None:
        record = row("pg16", version="16")
        impostor = labelled("some-other-name", record)

        result = assign_managed(found(runner, impostor), [record])

        keys = {item.key: item for item in result}
        assert keys["postgresql@some-other-name"].managed is None
        assert keys["postgresql@pg16"].managed == "pg16"
        assert keys["postgresql@pg16"].container_id == "", "the row's own stand-in is the instance"

    def test_a_row_with_no_container_is_a_stopped_instance(self) -> None:
        (instance,) = assign_managed([], [row("pg16")])

        assert instance.key == "postgresql@pg16" and not instance.running

    def test_an_instance_being_made_or_removed_is_offered_to_nobody(
        self, runner: FakeRunner
    ) -> None:
        for state in (CREATING, REMOVING):
            record = row("pg16", state=state)
            result = assign_managed(found(runner, labelled("noust-db-pg16", record)), [record])
            assert result == [], state

    def test_an_instance_being_upgraded_is_still_used(self, runner: FakeRunner) -> None:
        record = row("pg16", state=UPGRADING)

        (instance,) = assign_managed(found(runner, labelled("noust-db-pg16", record)), [record])

        assert instance.managed == "pg16"

    def test_a_stranger_cannot_take_an_instances_key(self, runner: FakeRunner) -> None:
        record = row("pg16")
        squatter = container(
            "pg16", "postgres:15", env=["POSTGRES_PASSWORD=x"], exposed=("5432/tcp",)
        )

        result = assign_managed(found(runner, squatter), [record])

        (only,) = result
        assert only.key == "postgresql@pg16" and only.managed == "pg16"

    def test_everything_else_is_untouched_and_the_whole_is_sorted_by_key(
        self, runner: FakeRunner
    ) -> None:
        record = row("pg16")
        compose = container(
            "shop-db-1",
            "postgres:16-alpine",
            env=["POSTGRES_PASSWORD=x"],
            project="shop",
            service="db",
            exposed=("5432/tcp",),
        )

        result = assign_managed(found(runner, compose), [record])

        assert [item.key for item in result] == ["postgresql@pg16", "postgresql@shop.db"]
        assert result[1].managed is None and result[1].project == "shop"

    def test_the_label_is_read_by_discovery_and_kept_apart_from_the_rows_say(
        self, runner: FakeRunner
    ) -> None:
        record = row("pg16", version="16")

        (seen,) = found(runner, labelled("noust-db-pg16", record))

        assert seen.instance_label == "pg16"
        assert seen.managed is None, "only a row makes it managed"
        assert replace(seen, managed="pg16").to_dict()["managed"] == "pg16"
        assert "managed" not in seen.to_dict()


class TestWhatAnInstanceRefuses:
    def test_an_instances_settings_are_its_images_and_the_error_says_how_it_moves(self) -> None:
        manager = PostgresManager().bind(stub_instance(row("pg16", version="16")))

        with pytest.raises(DatabaseEngineError) as caught:
            settings_for(manager)

        assert "noust db instance upgrade pg16" in caught.value.details
        assert "noust db instance remove pg16" in caught.value.details

    def test_not_being_able_to_sign_in_points_at_the_instance_not_a_compose_file(self) -> None:
        manager = PostgresManager().bind(stub_instance(row("pg16", version="16")))

        hint = manager.access_hint()

        assert "environment file" in hint and "noust db instance remove pg16" in hint
        assert "compose file" not in hint
