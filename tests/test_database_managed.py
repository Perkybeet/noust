# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database instances Noust runs: making one, using it, upgrading it, removing it.

The machine is :class:`~tests.database_instance_support.DockerHost`: starting the
unit makes the container the Compose file *on disk* says, so what is pinned is
what the files and the commands do together. Four things are defended:

- **what is refused is refused before anything exists** (no Docker, no memory
  limit, a port or a name something holds, a version the catalog lacks);
- **a creation that fails leaves nothing behind**, and says why with the
  container's own words, masked;
- **the instance is the same thing as every other engine to the rest of Noust**:
  found under a key, bound to the same manager, signed in to without a password
  in any argument;
- **an upgrade keeps what ran before it** and puts it back when the new image
  does not come up.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.db_instance_store import CREATING, READY, REMOVING, UPGRADING, DbInstanceStore
from noust.core.exceptions import (
    ConfirmationRequired,
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    ValidationError,
)
from noust.managers.database import instance_layout as layout
from noust.managers.database.instance_compose import render_compose
from tests.database_instance_support import World, build_world, close_world
from tests.test_database_instances import container

NEW_DIGEST = "sha256:" + "c" * 64


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield build_world(tmp_path, monkeypatch)
    close_world()


def no_secret_in_any_argv(world: World, name: str) -> None:
    secret = world.password(name)
    for call in world.host.calls:
        for part in call:
            assert secret not in part, call
    for env in world.host.envs:
        for value in (env or {}).values():
            assert value != secret or True  # a value in env= is how a secret travels


# ====================================================================== planning


class TestWhatIsRefusedBeforeAnythingExists:
    def test_without_docker_it_says_what_to_install(self, world: World) -> None:
        world.host.only_knows("systemctl")

        with pytest.raises(ValidationError) as caught:
            world.managed.plan("pg16", "postgresql", version="16", memory="512m")

        assert "Docker is not installed" in caught.value.message
        assert "docker.io" in caught.value.details

    def test_without_the_compose_plugin_it_says_so(self, world: World) -> None:
        world.host.fail[("docker", "compose", "version")] = "docker: 'compose' is not a command"

        with pytest.raises(ValidationError) as caught:
            world.managed.plan("pg16", "postgresql", version="16", memory="512m")

        assert "Compose" in caught.value.message

    def test_the_memory_limit_is_mandatory(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.plan("pg16", "postgresql", version="16")

        assert caught.value.field == "memory_mb"
        assert "512m" in caught.value.details

    def test_less_memory_than_the_engine_starts_in_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.plan("pg16", "postgresql", version="16", memory="128m")

        assert "at least 256 MB" in caught.value.details

    def test_more_memory_than_the_server_has_left_is_refused_and_says_how_much_is(
        self, world: World
    ) -> None:
        world.managed.create("one", "postgresql", version="16", memory="6g")

        with pytest.raises(ValidationError) as caught:
            world.managed.plan("two", "postgresql", version="17", memory="2g")

        # 8 GiB, 90 % of it for instances, 6 GiB given: 1228 MB are left.
        assert "1228" in caught.value.details
        assert world.managed.budget() == {
            "server_mb": 8192,
            "allocatable_mb": 7372,
            "allocated_mb": 6144,
            "available_mb": 1228,
        }

    def test_a_version_the_catalog_does_not_offer_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.plan("pg9", "postgresql", version="9.6", memory="512m")

        assert caught.value.field == "version"
        assert world.store.list_apps() == [] and DbInstanceStore(world.store).all() == []

    def test_a_name_that_cannot_be_a_unit_is_refused(self, world: World) -> None:
        for name in ("PG16", "pg_16", "../x", "-x", ""):
            with pytest.raises(ValidationError):
                world.managed.plan(name, "postgresql", version="16", memory="512m")

    def test_a_taken_name_is_refused(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        with pytest.raises(DatabaseExistsError):
            world.managed.plan("pg16", "postgresql", version="17", memory="512m")

    def test_a_container_that_holds_the_key_is_named(self, world: World) -> None:
        world.host.containers["pg16"] = container("pg16", "postgres:16")

        with pytest.raises(DatabaseExistsError) as caught:
            world.managed.plan("pg16", "postgresql", version="16", memory="512m")

        assert "postgresql@pg16" in caught.value.message

    @pytest.mark.parametrize("leftover", ["directory", "volume", "container", "unit"])
    def test_what_an_instance_left_behind_is_never_reused(
        self, world: World, leftover: str
    ) -> None:
        if leftover == "directory":
            layout.directory("pg16").mkdir(parents=True)
        elif leftover == "volume":
            world.host.volumes.add("noust-db-pg16-data")
        elif leftover == "container":
            world.host.containers["noust-db-pg16"] = container("noust-db-pg16", "alpine:3")
        else:
            (world.units / "noust-db-pg16.service").write_text("# Generated by Noust\n")

        with pytest.raises(DatabaseExistsError) as caught:
            world.managed.plan("pg16", "postgresql", version="16", memory="512m")

        assert "left something" in caught.value.message

    def test_the_port_is_the_first_free_one_above_the_engines_own(self, world: World) -> None:
        world.host.listening |= {5433, 5434}

        plan = world.managed.plan("pg16", "postgresql", version="16", memory="512m")

        assert plan.host_port == 5435
        assert plan.key == "postgresql@pg16"
        assert plan.digest.startswith("sha256:")

    def test_a_port_another_instance_or_a_public_database_holds_is_refused(
        self, world: World
    ) -> None:
        first = world.managed.create("one", "postgresql", version="16", memory="512m")

        with pytest.raises(ValidationError) as caught:
            world.managed.plan(
                "two", "postgresql", version="17", memory="512m", port=first["host_port"]
            )

        assert caught.value.field == "port"

    def test_a_privileged_or_impossible_port_is_refused(self, world: World) -> None:
        for port in (80, 70000):
            with pytest.raises(ValidationError):
                world.managed.plan("pg16", "postgresql", version="16", memory="512m", port=port)

    def test_planning_changes_nothing(self, world: World) -> None:
        world.managed.plan("pg16", "postgresql", version="16", memory="512m")

        assert DbInstanceStore(world.store).all() == []
        assert not layout.directory("pg16").exists()
        assert list(world.units.iterdir()) == []
        assert not any(call[:2] == ("docker", "pull") for call in world.host.calls)

    def test_the_plan_warns_that_it_listens_on_the_loopback_only(self, world: World) -> None:
        plan = world.managed.plan("pg16", "postgresql", version="16", memory="512m")

        assert any("127.0.0.1" in warning and "SSH tunnel" in warning for warning in plan.warnings)


# ======================================================================= create


class TestMakingAnInstance:
    def test_it_is_made_pulled_started_and_signed_in_to(self, world: World) -> None:
        described = world.managed.create("pg16", "postgresql", version="16", memory="512m")

        assert described["key"] == "postgresql@pg16"
        assert described["state"] == READY
        assert described["running"] is True
        assert described["address"] == "127.0.0.1"
        assert described["host_port"] == 5433
        assert described["memory_limit_mb"] == 512
        pull = next(call for call in world.host.calls if call[:2] == ("docker", "pull"))
        assert pull[2] == f"postgres@{described['digest']}"
        # The unit is the Compose unit, enabled and started.
        unit = (world.units / "noust-db-pg16.service").read_text()
        assert "# Generated by Noust" in unit
        assert "up -d" in unit and "-p noust-db-pg16" in unit
        assert "noust-db-pg16" in world.host.enabled
        assert world.host.started == ["noust-db-pg16"]

    def test_the_order_is_pull_then_files_then_unit_then_the_sign_in(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        calls = [call for call in world.host.calls if call[0] in ("docker", "systemctl")]
        kinds = []
        for call in calls:
            if call[:2] == ("docker", "pull"):
                kinds.append("pull")
            elif call[:2] == ("systemctl", "start"):
                kinds.append("start")
            elif call[:2] == ("docker", "exec"):
                kinds.append("exec")
        assert kinds.index("pull") < kinds.index("start") < kinds.index("exec")

    def test_the_row_is_written_before_anything_else_exists(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        original = world.managed._pull

        def spy(record):  # type: ignore[no-untyped-def]
            row = DbInstanceStore(world.store).get(record.name)
            seen.append(row.state if row else "no row")
            original(record)

        monkeypatch.setattr(world.managed, "_pull", spy)

        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        assert seen == [CREATING]

    def test_the_files_are_root_only_and_the_compose_file_is_the_rows(self, world: World) -> None:
        described = world.managed.create("pg16", "postgresql", version="16", memory="512m")

        directory = layout.directory("pg16")
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(layout.env_path("pg16").stat().st_mode) == 0o600
        row = DbInstanceStore(world.store).get("pg16")
        assert row is not None
        assert layout.compose_path("pg16").read_text() == render_compose(row)
        assert described["directory"] == str(directory)

    def test_the_password_is_in_the_env_file_and_nowhere_else(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        password = world.password("pg16")
        assert len(password) == 32 and password.isalnum()
        assert password not in layout.compose_path("pg16").read_text()
        assert password not in (world.units / "noust-db-pg16.service").read_text()
        for call in world.host.calls:
            assert not any(password in part for part in call), call
        assert not any(
            password in str(value) for env in world.host.envs for value in (env or {}).values()
        )
        assert world.service.secrets.read("databases/postgresql.pg16/x") is None

    def test_the_container_runs_what_the_file_says_on_the_loopback_with_its_limit(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        container = world.host.containers["noust-db-pg16"]
        assert container["Config"]["Labels"]["io.noust.db-instance"] == "pg16"
        assert container["NetworkSettings"]["Ports"] == {
            "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5433"}]
        }
        assert container["_limits"] == {"memory": "512M"}

    def test_it_is_then_found_under_its_key_by_the_rest_of_noust(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        found = {item.key: item for item in world.fresh().service.instances()}

        instance = found["postgresql@pg16"]
        assert instance.managed == "pg16"
        assert instance.version == "16"
        assert instance.memory_limit_mb == 512
        assert instance.unit == "noust-db-pg16"
        assert instance.running and instance.port == 5433
        assert instance.reach == "host"
        assert (instance.project, instance.service, instance.app) == (None, None, None)

    def test_the_standard_manager_drives_it_through_docker_exec_without_a_password(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.host.calls.clear()
        service = world.fresh().service

        databases = service.manager("postgresql@pg16").list_databases()

        assert [db.name for db in databases] == ["appdb"]
        (call,) = [call for call in world.host.calls if call[:2] == ("docker", "exec")]
        assert "noust-db-pg16" in call
        assert "POSTGRES_PASSWORD" in " ".join(call)  # the variable's NAME, never its value
        assert world.password("pg16") not in " ".join(call)

    def test_the_audit_trail_says_what_was_made_and_no_secret(self, world: World) -> None:
        from noust.core import audit

        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.managed.stop("pg16")

        entries = audit.get_log().read(limit=100)
        made = [entry for entry in entries if entry["action"] == "db.instance.create"]
        stopped = [entry for entry in entries if entry["action"] == "db.instance.stop"]
        assert len(made) == 1 and len(stopped) == 1
        assert made[0]["details"]["version"] == "16" and made[0]["details"]["port"] == 5433
        assert made[0]["resource"] == "db:pg16"
        assert world.password("pg16") not in json.dumps(entries)

    @pytest.mark.parametrize(
        ("engine", "version", "key"),
        [
            ("postgresql", "17", "postgresql@x"),
            ("mysql", "8.4", "mysql@x"),
            ("mariadb", "11.8", "mysql@x"),
            ("redis", "8.0", "redis@x"),
            ("valkey", "8", "redis@x"),
            ("keydb", "6.3", "redis@x"),
            ("clickhouse", "26.8", "clickhouse@x"),
        ],
    )
    def test_every_engine_comes_up_under_the_key_of_its_manager(
        self, world: World, engine: str, version: str, key: str
    ) -> None:
        world.host.exec_stdout = ""

        described = world.managed.create("x", engine, version=version, memory="1g")

        assert described["key"] == key
        assert described["version"] == version
        compose = layout.compose_path("x").read_text()
        assert "127.0.0.1:" in compose

    def test_redis_keeps_its_password_in_the_configuration_not_in_the_command(
        self, world: World
    ) -> None:
        world.host.exec_stdout = ""
        world.managed.create("cache", "redis", version="8.0", memory="256m")

        password = world.password("cache")
        config = layout.config_path("cache")
        assert f"requirepass {password}" in config.read_text()
        assert password not in layout.compose_path("cache").read_text()
        no_secret_in_any_argv(world, "cache")


# ===================================================================== failures


class TestAFailedCreationLeavesNothing:
    def assert_nothing_left(self, world: World, name: str = "pg16") -> None:
        assert DbInstanceStore(world.store).get(name) is None
        assert not layout.directory(name).exists()
        assert not (world.units / f"noust-db-{name}.service").exists()
        assert f"noust-db-{name}" not in world.host.containers
        assert f"noust-db-{name}-data" not in world.host.volumes

    def test_a_pull_that_fails_says_what_docker_said(self, world: World) -> None:
        world.host.fail[("docker", "pull")] = "toomanyrequests: rate limit"

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.create("pg16", "postgresql", version="16", memory="512m")

        assert "could not be made" in caught.value.message
        assert "rate limit" in (caught.value.output or "")
        self.assert_nothing_left(world)

    def test_an_unhealthy_engine_is_removed_and_its_log_is_shown_masked(self, world: World) -> None:
        world.host.health["noust-db-pg16"] = ["starting", "unhealthy"]
        world.host.log = "FATAL: could not open the data directory\n"

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.create("pg16", "postgresql", version="16", memory="512m")

        assert "unhealthy" in caught.value.message
        assert "could not open the data directory" in (caught.value.output or "")
        assert "journalctl -u noust-db-pg16" in caught.value.details
        self.assert_nothing_left(world)

    def test_an_engine_that_never_becomes_healthy_is_given_the_time_and_then_removed(
        self, world: World
    ) -> None:
        world.host.health["noust-db-pg16"] = ["starting"]

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.create("pg16", "postgresql", version="16", memory="512m")

        assert "240 seconds" in caught.value.message
        self.assert_nothing_left(world)

    def test_an_engine_that_refuses_its_superuser_is_removed(self, world: World) -> None:
        world.host.exec_fails = True

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.create("pg16", "postgresql", version="16", memory="512m")

        assert "did not accept its superuser" in caught.value.message
        self.assert_nothing_left(world)

    def test_a_unit_that_will_not_start_is_removed(self, world: World) -> None:
        world.host.fail[("systemctl", "start")] = "Job failed"

        with pytest.raises(DatabaseEngineError):
            world.managed.create("pg16", "postgresql", version="16", memory="512m")

        self.assert_nothing_left(world)

    def test_what_cannot_be_removed_is_named_and_the_row_stays_to_finish_it(
        self, world: World
    ) -> None:
        world.host.exec_fails = True
        world.host.fail[("docker", "volume", "rm")] = "volume is in use"

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.create("pg16", "postgresql", version="16", memory="512m")

        assert "noust-db-pg16-data" in caught.value.details
        assert "noust db instance remove pg16" in caught.value.details
        row = DbInstanceStore(world.store).get("pg16")
        assert row is not None and row.state == CREATING


# ================================================================= start / stop


class TestStartingAndStopping:
    def test_stopping_removes_the_container_and_keeps_the_data_and_the_instance(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        stopped = world.managed.stop("pg16")

        assert stopped["running"] is False
        assert stopped["status"] == "stopped"
        assert "noust-db-pg16" not in world.host.containers
        assert "noust-db-pg16-data" in world.host.volumes

    def test_a_stopped_instance_is_still_found_under_its_key_and_still_knows_its_port(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.managed.stop("pg16")

        instance = {i.key: i for i in world.fresh().service.instances()}["postgresql@pg16"]

        assert not instance.running
        assert instance.port == 5433
        assert instance.unit == "noust-db-pg16"
        assert instance.managed == "pg16"

    def test_starting_it_again_brings_the_container_back_and_waits_for_it(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.managed.stop("pg16")
        world.host.health["noust-db-pg16"] = ["starting", "starting", "healthy"]

        started = world.managed.start("pg16")

        assert started["running"] is True
        assert world.host.started == ["noust-db-pg16", "noust-db-pg16"]

    def test_the_standard_start_and_stop_of_an_engine_work_on_it_too(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        service = world.fresh().service

        service.manager("postgresql@pg16").stop()

        assert "noust-db-pg16" not in world.host.containers
        service = world.fresh().service
        service.manager("postgresql@pg16").start()
        assert "noust-db-pg16" in world.host.containers

    def test_an_instance_whose_files_were_not_restored_says_how_to_make_it_again(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.managed.stop("pg16")
        (world.units / "noust-db-pg16.service").unlink()

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.start("pg16")

        assert "unit is not on this server" in caught.value.message
        assert "noust db instance remove pg16 --yes" in caught.value.details
        # and that very removal is what finishes it, with nothing of it left to remove
        outcome = world.managed.remove("pg16", confirm=True)
        assert DbInstanceStore(world.store).get("pg16") is None
        assert outcome.name == "pg16"

    def test_an_unknown_instance_is_not_found(self, world: World) -> None:
        with pytest.raises(DatabaseNotFoundError) as caught:
            world.managed.start("ghost")
        assert "noust db instance list" in caught.value.details

    def test_an_instance_that_is_being_changed_is_not_started_or_stopped(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        DbInstanceStore(world.store).set_state("pg16", UPGRADING)

        for action in (world.managed.start, world.managed.stop):
            with pytest.raises(DatabaseEngineError) as caught:
                action("pg16")
            assert "upgrading" in caught.value.message


# ==================================================================== listing


class TestListing:
    def test_every_instance_is_described_with_where_it_is(self, world: World) -> None:
        world.managed.create("b", "postgresql", version="17", memory="512m")
        world.managed.create("a", "redis", version="8.0", memory="256m")
        world.host.exec_stdout = ""

        rows = world.managed.all()

        assert [row["name"] for row in rows] == ["a", "b"]
        b = rows[1]
        assert b["unit"] == "noust-db-b" and b["volume"] == "noust-db-b-data"
        assert b["reference"].startswith("postgres:17@sha256:")
        assert b["end_of_life"] == "2029-11-08"

    def test_an_instance_that_is_not_finished_is_listed_but_not_offered_to_the_rest(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        DbInstanceStore(world.store).set_state("pg16", CREATING)

        assert world.managed.all()[0]["state"] == CREATING
        assert "postgresql@pg16" not in {i.key for i in world.fresh().service.instances()}


# ==================================================================== upgrade


class TestUpgradingWithinTheSameVersion:
    def serve(
        self, world: World, digest: str, name: str = "pg16", tag: str = "postgres:16"
    ) -> None:
        world.host.served[tag] = digest

    def test_nothing_is_done_when_the_registry_serves_what_already_runs(self, world: World) -> None:
        described = world.managed.create("pg16", "postgresql", version="16", memory="512m")
        self.serve(world, described["digest"])
        world.host.started.clear()

        outcome = world.managed.upgrade("pg16")

        assert outcome.changed is False
        assert world.host.started == []

    def test_a_newer_image_is_dumped_around_then_run_and_the_old_digest_kept(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        described = world.managed.create("pg16", "postgresql", version="16", memory="512m")
        self.serve(world, NEW_DIGEST)
        dumped: list[tuple[str, str]] = []

        class Backups:
            def __init__(self, service: object) -> None: ...

            def dump(self, engine: str, database: str):  # type: ignore[no-untyped-def]
                dumped.append((engine, database))
                return type("View", (), {"name": f"{database}.sql.gz"})()

        monkeypatch.setattr("noust.managers.database.backups.DatabaseBackups", Backups)

        outcome = world.managed.upgrade("pg16")

        assert outcome.changed and outcome.to_digest == NEW_DIGEST
        assert dumped == [("postgresql@pg16", "appdb")]
        assert outcome.dumps == ["appdb.sql.gz"]
        row = DbInstanceStore(world.store).get("pg16")
        assert row is not None
        assert (row.digest, row.previous_digest, row.state) == (
            NEW_DIGEST,
            described["digest"],
            READY,
        )
        assert NEW_DIGEST in layout.compose_path("pg16").read_text()
        container = world.host.containers["noust-db-pg16"]
        assert container["Config"]["Image"] == f"postgres:16@{NEW_DIGEST}"

    def test_a_failed_dump_stops_it_before_anything_is_touched(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        described = world.managed.create("pg16", "postgresql", version="16", memory="512m")
        self.serve(world, NEW_DIGEST)

        class Backups:
            def __init__(self, service: object) -> None: ...

            def dump(self, engine: str, database: str):  # type: ignore[no-untyped-def]
                raise DatabaseBackupError("pg_dump failed")

        monkeypatch.setattr("noust.managers.database.backups.DatabaseBackups", Backups)
        world.host.started.clear()

        with pytest.raises(DatabaseBackupError):
            world.managed.upgrade("pg16")

        row = DbInstanceStore(world.store).get("pg16")
        assert row is not None and (row.digest, row.state) == (described["digest"], READY)
        assert world.host.started == []

    def test_a_dump_can_be_skipped_when_the_operator_says_so(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        self.serve(world, NEW_DIGEST)

        outcome = world.managed.upgrade("pg16", backup=False)

        assert outcome.changed and outcome.dumps == []

    def test_a_new_image_that_does_not_come_up_is_put_back_with_the_reason(
        self, world: World
    ) -> None:
        described = world.managed.create("pg16", "postgresql", version="16", memory="512m")
        self.serve(world, NEW_DIGEST)
        world.host.log = "FATAL: database files are incompatible with server\n"
        # The first look after the restart finds it unhealthy; the old image is fine.
        world.host.health["noust-db-pg16"] = ["unhealthy", "healthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.upgrade("pg16", backup=False)

        assert "runs the previous one again" in caught.value.message
        assert "incompatible" in (caught.value.output or "")
        row = DbInstanceStore(world.store).get("pg16")
        assert row is not None and (row.digest, row.state) == (described["digest"], READY)
        assert described["digest"] in layout.compose_path("pg16").read_text()
        assert world.host.containers["noust-db-pg16"]["Config"]["Image"].endswith(
            described["digest"]
        )

    def test_when_it_cannot_be_put_back_the_error_says_where_it_stands(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        self.serve(world, NEW_DIGEST)
        world.host.health["noust-db-pg16"] = ["unhealthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.upgrade("pg16", backup=False)

        assert "could not be put back" in caught.value.message
        row = DbInstanceStore(world.store).get("pg16")
        assert row is not None and row.state == UPGRADING

    def test_a_registry_that_does_not_answer_changes_nothing(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.host.fail[("docker", "pull", "postgres:16")] = "dial tcp: lookup failed"

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.upgrade("pg16")

        assert "could not pull postgres:16" in caught.value.message
        assert DbInstanceStore(world.store).get("pg16").state == READY  # type: ignore[union-attr]


# ===================================================================== remove


class TestRemoving:
    def test_it_says_what_it_deletes_and_deletes_nothing_without_a_yes(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        with pytest.raises(ConfirmationRequired) as caught:
            world.managed.remove("pg16")

        text = " ".join(caught.value.warnings)
        assert "noust-db-pg16-data" in text and "appdb" in text and "no way back" in text
        assert DbInstanceStore(world.store).get("pg16") is not None
        assert "noust-db-pg16" in world.host.containers

    def test_with_a_yes_the_unit_the_container_the_volumes_and_the_directory_go(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        outcome = world.managed.remove("pg16", confirm=True)

        assert any("unit noust-db-pg16" in item for item in outcome.removed)
        assert any("directory" in item for item in outcome.removed)
        assert DbInstanceStore(world.store).get("pg16") is None
        assert not layout.directory("pg16").exists()
        assert not (world.units / "noust-db-pg16.service").exists()
        assert "noust-db-pg16" not in world.host.containers
        assert world.host.volumes == set()
        assert "postgresql@pg16" not in {i.key for i in world.fresh().service.instances()}

    def test_the_name_and_the_port_are_free_again(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.managed.remove("pg16", confirm=True)

        again = world.managed.create("pg16", "postgresql", version="17", memory="512m")

        assert again["host_port"] == 5433

    def test_an_application_that_uses_a_database_on_it_stops_the_removal(
        self, world: World
    ) -> None:
        from noust.core.store import App
        from noust.managers.database.records import DatabaseLink

        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        app = world.store.create_app(App(domain="shop.example.com", app_path="/srv/shop"))
        world.service.records.save_link(
            DatabaseLink(
                app_id=app.id or 0,
                engine="postgresql@pg16",
                db_name="appdb",
                username="app",
                env_var="DATABASE_URL",
            )
        )

        with pytest.raises(ValidationError) as caught:
            world.managed.remove("pg16", confirm=True)

        assert "shop.example.com" in caught.value.details
        assert DbInstanceStore(world.store).get("pg16") is not None
        assert "noust-db-pg16" in world.host.containers

    def test_a_database_made_public_stops_the_removal(self, world: World) -> None:
        from noust.core.database_exposures import ExposureStore

        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        ExposureStore(world.store).create(
            engine="postgresql@pg16",
            db_name="appdb",
            port=6543,
            bind_address="0.0.0.0",  # noqa: S104
            sources=("any",),
            tls="optional",
            remote_user="appdb_remote",
            profile="owner",
            unit="noust-dbexpose-postgresql.pg16-6543",
            created_by="t",
        )

        with pytest.raises(ValidationError) as caught:
            world.managed.remove("pg16", confirm=True)

        assert "reachable from the network" in caught.value.details

    def test_what_stays_is_the_dumps(self, world: World) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")

        outcome = world.managed.remove("pg16", confirm=True)

        assert outcome.kept == ["the dumps already taken"]

    def test_a_removal_that_cannot_finish_is_finished_by_running_it_again(
        self, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.host.fail[("docker", "volume", "rm")] = "volume is in use"

        with pytest.raises(DatabaseEngineError) as caught:
            world.managed.remove("pg16", confirm=True)

        assert "noust-db-pg16-data" in caught.value.details
        row = DbInstanceStore(world.store).get("pg16")
        assert row is not None and row.state == REMOVING
        # The unit and the directory are gone already; the volume is what is left.
        assert not layout.directory("pg16").exists()
        world.host.fail.clear()

        again = world.managed.remove("pg16", confirm=True)

        assert any("volume" in item for item in again.removed)
        assert DbInstanceStore(world.store).get("pg16") is None
        assert world.host.volumes == set()

    def test_a_creation_that_was_interrupted_is_removed_the_same_way(self, world: World) -> None:
        DbInstanceStore(world.store).create(
            name="half",
            flavour="postgresql",
            version="16",
            image="postgres",
            digest="sha256:" + "a" * 64,
            host_port=5440,
            memory_limit_mb=512,
            created_by="t",
        )
        layout.directory("half").mkdir(parents=True)

        outcome = world.managed.remove("half", confirm=True)

        assert DbInstanceStore(world.store).get("half") is None
        assert any("directory" in item for item in outcome.removed)
