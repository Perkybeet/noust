# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a database container's "no password" warning depends on: who can reach it.

A Redis or MongoDB without credentials on a Compose network is the image's
default and the stack's network is the boundary, so it is not an alarm. The
same instance published on the server's loopback is open to every process on
it, and published on any other address to whatever reaches that port. The
answer comes from what Docker publishes, in one place
(:attr:`DatabaseInstance.reach`), and both engines word it from the same two
halves (:meth:`DatabaseInstance.reach_words`).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from noust.core.runner import FakeRunner
from noust.managers.database.instances import DatabaseInstance, PublishedPort, discover
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.redis import RedisManager
from tests.test_database_instances import container

ANYWHERE = "0.0.0.0"  # noqa: S104 - what Docker reports for "every address"


def redis(*, ports: tuple[PublishedPort, ...] = (), **changes) -> DatabaseInstance:
    """A Redis in a Compose service, started with no password."""
    return DatabaseInstance(
        key="redis@attalaya-arennalabs-com.redis",
        engine="redis",
        flavour="redis",
        container="attalaya-redis",
        container_id="r",
        image="redis:8-alpine",
        project="attalaya-arennalabs-com",
        service="redis",
        state="running",
        exposed=(6379,),
        published=ports,
        **changes,
    )


def mongo(*, ports: tuple[PublishedPort, ...] = (), **changes) -> DatabaseInstance:
    """A MongoDB in a Compose service."""
    return DatabaseInstance(
        key="mongodb@shop.mongo",
        engine="mongodb",
        flavour="mongo",
        container="shop-mongo-1",
        container_id="m",
        image="mongo:7",
        project="shop",
        service="mongo",
        state="running",
        exposed=(27017,),
        published=ports,
        **changes,
    )


def published(host_ip: str, host_port: int, container_port: int = 6379) -> PublishedPort:
    return PublishedPort(container_port=container_port, host_ip=host_ip, host_port=host_port)


class TestReach:
    """One answer, from what Docker publishes of the engine's own port."""

    def test_nothing_published_is_its_networks_only(self) -> None:
        instance = redis()
        assert instance.reach == "network"
        assert instance.reach_words() is None

    def test_another_port_published_does_not_open_the_engines(self) -> None:
        # A web UI beside Redis, published: the engine's own port is still unpublished.
        instance = redis(ports=(published(ANYWHERE, 8081, container_port=8081),))
        assert instance.reach == "network"

    @pytest.mark.parametrize("address", ["127.0.0.1", "127.0.1.1", "::1"])
    def test_loopback_is_this_server(self, address: str) -> None:
        instance = redis(ports=(published(address, 6380),))
        assert instance.reach == "host"
        shown = f"[{address}]" if ":" in address else address
        assert instance.reach_words() == (
            f"is published on {shown}:6380",
            "any process on this server",
        )

    @pytest.mark.parametrize("address", [ANYWHERE, "::", "10.0.0.5", "203.0.113.7"])
    def test_any_other_address_is_whatever_reaches_the_port(self, address: str) -> None:
        instance = redis(ports=(published(address, 6380),))
        assert instance.reach == "public"
        shown = f"[{address}]" if ":" in address else address
        assert instance.reach_words() == (
            f"is published on {shown}:6380",
            "anything that reaches port 6380 of this server",
        )

    def test_the_widest_publication_decides_and_is_the_one_named(self) -> None:
        instance = redis(ports=(published("127.0.0.1", 6380), published(ANYWHERE, 6390)))
        assert instance.reach == "public"
        assert instance.reach_words() == (
            f"is published on {ANYWHERE}:6390",
            "anything that reaches port 6390 of this server",
        )

    def test_a_value_that_is_not_an_address_is_not_taken_for_loopback(self) -> None:
        assert redis(ports=(published("localhost", 6380),)).reach == "public"

    def test_sharing_the_servers_network_is_the_engines_own_port(self) -> None:
        instance = redis(host_network=True)
        assert instance.reach == "public"
        assert instance.reach_words() == (
            "shares the server's network",
            "anything that reaches port 6379 of this server",
        )

    def test_docker_inspect_is_read_for_it(self, runner: FakeRunner) -> None:
        entries = [
            container(
                "bound",
                "redis:7-alpine",
                project="p",
                service="bound",
                exposed=("6379/tcp",),
                ports={"6379/tcp": [{"HostIp": "127.0.0.1", "HostPort": "6380"}]},
            ),
            container("inner", "redis:7-alpine", project="p", service="inner"),
            container("shared", "redis:7-alpine", project="p", service="shared"),
        ]
        entries[2]["HostConfig"]["NetworkMode"] = "host"
        runner.script(
            ["docker", "ps"], stdout="\n".join(f"{e['Id']}\tredis:7-alpine" for e in entries)
        )
        runner.script(["docker", "inspect"], stdout=json.dumps(entries))

        found = {item.service: item for item in discover(runner)}

        assert {name: item.reach for name, item in found.items()} == {
            "bound": "host",
            "inner": "network",
            "shared": "public",
        }


class TestARedisWithoutAPassword:
    """The server says it takes commands from nobody in particular; who can ask is the instance's."""

    @pytest.fixture(autouse=True)
    def answers(self, runner: FakeRunner) -> None:
        runner.script(["docker", "exec"], stdout="default\n")

    def test_on_its_own_network_it_is_not_a_warning_and_is_not_even_asked(
        self, runner: FakeRunner
    ) -> None:
        assert RedisManager().bind(redis()).warnings() == []
        assert runner.calls == []

    def test_on_the_servers_loopback_any_process_can_reach_it(self) -> None:
        (warning,) = RedisManager().bind(redis(ports=(published("127.0.0.1", 6380),))).warnings()

        assert warning == (
            "Redis in attalaya-redis has no password and is published on 127.0.0.1:6380: any "
            "process on this server can read and change every key. Start it with --requirepass "
            "or REDIS_PASSWORD in the compose file and recreate the container."
        )

    def test_published_on_every_address_it_is_whatever_reaches_the_port(self) -> None:
        (warning,) = RedisManager().bind(redis(ports=(published(ANYWHERE, 6380),))).warnings()

        assert warning == (
            f"Redis in attalaya-redis has no password and is published on {ANYWHERE}:6380: "
            "anything that reaches port 6380 of this server can read and change every key. "
            "Start it with --requirepass or REDIS_PASSWORD in the compose file and recreate "
            "the container."
        )

    def test_with_a_password_it_is_not_a_warning_wherever_it_is_published(
        self, runner: FakeRunner
    ) -> None:
        guarded = redis(ports=(published(ANYWHERE, 6380),), command_password="x")
        assert RedisManager().bind(guarded).warnings() == []
        assert runner.calls == []

    def test_the_server_that_answers_as_someone_else_is_not_a_warning(
        self, runner: FakeRunner
    ) -> None:
        runner.script(["docker", "exec"], stdout="app\n")
        exposed = redis(ports=(published(ANYWHERE, 6380),))
        assert RedisManager().bind(exposed).warnings() == []

    def test_the_hosts_own_redis_keeps_its_warning(self, runner: FakeRunner) -> None:
        runner.script(["redis-cli"], stdout="default\n")
        manager = RedisManager()
        manager._known_password = lambda: None  # type: ignore[method-assign]

        (warning,) = manager.warnings()

        assert warning.startswith("Redis accepts every command without a password")
        assert "noust db user-password default --engine redis" in warning


class TestAMongoDBWithoutAuthorization:
    """A container's server is asked as nobody; the fix is the compose file's."""

    @staticmethod
    def open_server(runner: FakeRunner) -> None:
        runner.script(["docker", "exec"], stdout="{ databases: [], ok: 1 }\n")

    @staticmethod
    def guarded_server(runner: FakeRunner) -> None:
        runner.script(
            ["docker", "exec"],
            stderr="MongoServerError[Unauthorized]: Command listDatabases requires authentication",
            exit_code=1,
        )

    def test_on_its_own_network_it_is_not_a_warning_and_is_not_even_asked(
        self, runner: FakeRunner
    ) -> None:
        self.open_server(runner)

        assert MongoDBManager().bind(mongo()).warnings() == []
        assert runner.calls == []

    def test_on_the_servers_loopback_any_process_can_reach_it(self, runner: FakeRunner) -> None:
        self.open_server(runner)
        instance = mongo(ports=(published("127.0.0.1", 27018, 27017),))

        (warning,) = MongoDBManager().bind(instance).warnings()

        assert warning.startswith(
            "MongoDB in shop-mongo-1 runs without authorization and is published on "
            "127.0.0.1:27018: any process on this server can read and change every database."
        )

    def test_published_on_every_address_it_is_whatever_reaches_the_port(
        self, runner: FakeRunner
    ) -> None:
        self.open_server(runner)
        instance = mongo(ports=(published(ANYWHERE, 27018, 27017),))

        (warning,) = MongoDBManager().bind(instance).warnings()

        assert warning.startswith(
            f"MongoDB in shop-mongo-1 runs without authorization and is published on "
            f"{ANYWHERE}:27018: anything that reaches port 27018 of this server can read "
            "and change every database."
        )

    def test_the_fix_is_the_compose_files_and_says_it_needs_an_empty_volume(
        self, runner: FakeRunner
    ) -> None:
        self.open_server(runner)
        instance = mongo(ports=(published(ANYWHERE, 27018, 27017),))

        (warning,) = MongoDBManager().bind(instance).warnings()

        assert "/etc/mongod.conf" not in warning
        assert "MONGO_INITDB_ROOT_USERNAME and MONGO_INITDB_ROOT_PASSWORD" in warning
        assert "compose file" in warning
        assert "only on an empty data volume" in warning

    def test_one_that_demands_authentication_is_not_a_warning_wherever_it_is_published(
        self, runner: FakeRunner
    ) -> None:
        self.guarded_server(runner)
        instance = mongo(ports=(published(ANYWHERE, 27018, 27017),))

        assert MongoDBManager().bind(instance).warnings() == []

    def test_a_server_that_cannot_be_asked_is_not_accused(self, runner: FakeRunner) -> None:
        runner.script(["docker", "exec"], stderr="no such container", exit_code=1)
        instance = mongo(ports=(published(ANYWHERE, 27018, 27017),))

        assert MongoDBManager().bind(instance).warnings() == []

    def test_the_question_carries_no_credentials(self, runner: FakeRunner) -> None:
        # Signed in as the root account it would answer whatever the server enforces.
        self.open_server(runner)
        rooted = replace(
            mongo(ports=(published(ANYWHERE, 27018, 27017),)),
            env_names=frozenset({"MONGO_INITDB_ROOT_USERNAME", "MONGO_INITDB_ROOT_PASSWORD"}),
            settings={"MONGO_INITDB_ROOT_USERNAME": "root"},
        )

        MongoDBManager().bind(rooted).warnings()

        assert runner.inputs and all("auth(" not in (text or "") for text in runner.inputs)

    def test_the_hosts_own_mongodb_keeps_its_warning(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.database import mongodb as mongodb_module

        conf = tmp_path / "mongod.conf"
        conf.write_text("net:\n  port: 27017\n")
        monkeypatch.setattr(mongodb_module, "MONGOD_CONF", conf)

        (warning,) = MongoDBManager().warnings()

        assert "protect nothing" in warning and "/etc/mongod.conf" in warning
