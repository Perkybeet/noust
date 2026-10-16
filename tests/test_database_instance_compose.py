# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an instance is made from: the catalog's pins, and the Compose project written for it.

Two things are defended. The **catalog** offers only official images pinned by
tag and by digest, each one recognised by discovery as the engine it is, so a
container Noust makes is found again. The **Compose file** is the same bytes for
the same row and says, for every flavour, what the design promises: published
on 127.0.0.1 only, with a memory limit on the container and in the engine, the
image by digest, and the superuser's password in no argument, no command and no
Compose file.
"""

from __future__ import annotations

import re

import pytest
import yaml

from noust.core.db_instance_store import READY, InstanceRecord
from noust.core.exceptions import ValidationError
from noust.managers.database.flavours import FLAVOURS
from noust.managers.database.instance_catalog import (
    SPECS,
    offered,
    pin_of,
    resolve_pin,
    spec_for,
)
from noust.managers.database.instance_compose import (
    PASSWORD_VARIABLES,
    build_document,
    engine_command,
    render_compose,
    render_engine_config,
    render_env,
)
from noust.managers.database.instances import ENGINE_PORTS, image_engine
from noust.managers.database.registry import DatabaseRegistry

PASSWORD = "Zq8TnW3kLm0PxV7cYb2RdF5hGj9A"


def record(flavour: str = "postgresql", version: str | None = None, **kwargs) -> InstanceRecord:
    spec = spec_for(flavour)
    pin = resolve_pin(spec, version)
    values = {
        "id": 1,
        "name": "inst",
        "flavour": flavour,
        "version": pin.version,
        "image": spec.repository,
        "digest": pin.digest,
        "previous_digest": None,
        "host_port": 55432,
        "memory_limit_mb": 1024,
        "state": READY,
        "created_at": "2026-10-06T10:00:00+00:00",
        "created_by": "admin",
        "updated_at": "2026-10-06T10:00:00+00:00",
    }
    values.update(kwargs)
    return InstanceRecord(**values)


class TestTheCatalog:
    def test_every_flavour_is_one_noust_names_and_one_a_manager_speaks(self) -> None:
        for name, spec in SPECS.items():
            assert name in FLAVOURS
            assert FLAVOURS[name].engine == spec.engine
            assert DatabaseRegistry.canonical(spec.engine) == spec.engine
            assert ENGINE_PORTS[spec.engine] == spec.container_port

    def test_every_pin_is_a_tag_and_a_digest(self) -> None:
        seen: set[str] = set()
        for spec in SPECS.values():
            for pin in spec.pins:
                assert re.fullmatch(r"sha256:[0-9a-f]{64}", pin.digest), (spec.flavour, pin)
                assert re.fullmatch(r"\d{1,3}(?:\.\d{1,3})?", pin.version), (spec.flavour, pin)
                assert pin.digest not in seen, "two tags cannot be one image"
                seen.add(pin.digest)

    def test_each_flavour_has_one_default_and_its_versions_are_distinct(self) -> None:
        for spec in SPECS.values():
            assert sum(pin.default for pin in spec.pins) == 1, spec.flavour
            versions = [pin.version for pin in spec.pins]
            assert len(versions) == len(set(versions))
            assert spec.default_pin() in spec.pins

    def test_every_image_is_recognised_by_discovery_as_the_engine_it_runs(self) -> None:
        # A container Noust makes is found again by the image it was made from.
        for spec in SPECS.values():
            for pin in spec.pins:
                match = image_engine(f"{spec.repository}:{pin.version}@{pin.digest}")
                assert match is not None, (spec.repository, pin.version)
                assert match.engine == spec.engine
                expected = {"mariadb": "mariadb", "valkey": "valkey", "keydb": "keydb", "mysql": "mysql"}
                assert match.flavour == expected.get(spec.flavour, match.flavour)

    def test_memory_floors_are_below_what_an_interface_suggests(self) -> None:
        for spec in SPECS.values():
            assert 0 < spec.min_memory_mb <= spec.default_memory_mb

    def test_the_default_version_is_used_when_none_is_asked_for(self) -> None:
        assert resolve_pin(spec_for("postgresql"), None).version == "18"
        assert resolve_pin(spec_for("postgresql"), "").version == "18"

    def test_a_version_is_exact_or_a_prefix_by_components(self) -> None:
        assert resolve_pin(spec_for("postgresql"), "16").version == "16"
        assert resolve_pin(spec_for("mysql"), "8").version == "8.4"
        assert resolve_pin(spec_for("mariadb"), "11.4").version == "11.4"
        assert resolve_pin(spec_for("valkey"), "8").version == "8"

    def test_an_ambiguous_version_is_refused_with_its_candidates(self) -> None:
        with pytest.raises(ValidationError) as caught:
            resolve_pin(spec_for("mariadb"), "11")
        assert "11.4" in str(caught.value.details) and "11.8" in str(caught.value.details)

    def test_a_version_not_offered_lists_the_ones_that_are(self) -> None:
        with pytest.raises(ValidationError) as caught:
            resolve_pin(spec_for("postgresql"), "9.6")
        assert caught.value.field == "version"
        assert "18" in str(caught.value.details)

    def test_an_alias_and_an_unknown_engine(self) -> None:
        assert spec_for("pg").flavour == "postgresql"
        assert spec_for("Mongo").flavour == "mongodb"
        with pytest.raises(ValidationError) as caught:
            spec_for("clickhouse")
        assert "postgresql" in str(caught.value.details)

    def test_a_version_the_catalog_dropped_is_not_found_but_a_row_still_runs(self) -> None:
        assert pin_of(spec_for("postgresql"), "13") is None
        assert pin_of(spec_for("postgresql"), "17") is not None

    def test_the_listing_says_what_it_offers(self) -> None:
        entries = {entry["flavour"]: entry for entry in offered()}

        assert set(entries) == set(SPECS)
        postgres = entries["postgresql"]
        assert postgres["image"] == "postgres"
        assert [v["version"] for v in postgres["versions"]] == ["15", "16", "17", "18"]
        assert [v["version"] for v in postgres["versions"] if v["default"]] == ["18"]
        assert all(v["digest"].startswith("sha256:") for v in postgres["versions"])
        assert postgres["versions"][1]["end_of_life"] == "2028-11-09"


@pytest.mark.parametrize("flavour", list(SPECS))
class TestTheComposeFile:
    def document(self, flavour: str, **kwargs) -> dict:
        return build_document(record(flavour, **kwargs))

    def test_the_port_is_published_on_the_loopback_only(self, flavour: str) -> None:
        service = self.document(flavour)["services"]["db"]

        assert service["ports"] == [f"127.0.0.1:55432:{SPECS[flavour].container_port}"]

    def test_the_memory_limit_is_on_the_container(self, flavour: str) -> None:
        service = self.document(flavour, memory_limit_mb=768)["services"]["db"]

        assert service["deploy"]["resources"]["limits"]["memory"] == "768M"

    def test_the_image_is_the_tag_and_the_digest(self, flavour: str) -> None:
        item = record(flavour)
        service = build_document(item)["services"]["db"]

        assert service["image"] == f"{item.image}:{item.version}@{item.digest}"
        assert "latest" not in service["image"]

    def test_the_container_is_named_and_labelled_so_it_is_found_again(self, flavour: str) -> None:
        service = self.document(flavour)["services"]["db"]

        assert service["container_name"] == "noust-db-inst"
        assert service["labels"]["io.noust.db-instance"] == "inst"
        assert service["labels"]["io.noust.managed"] == "true"
        assert service["restart"] == "unless-stopped"

    def test_the_data_is_in_a_named_volume_of_noust(self, flavour: str) -> None:
        document = self.document(flavour)

        assert "noust-db-inst-data" in document["volumes"]
        assert document["volumes"]["noust-db-inst-data"]["name"] == "noust-db-inst-data"
        assert any(
            volume.startswith("noust-db-inst-data:")
            for volume in document["services"]["db"]["volumes"]
        )

    def test_it_has_a_health_check_that_gives_the_engine_time_to_start(self, flavour: str) -> None:
        check = self.document(flavour)["services"]["db"]["healthcheck"]

        assert check["test"][0] in ("CMD", "CMD-SHELL")
        assert check["start_period"] and check["retries"] >= 6

    def test_no_secret_is_in_the_compose_file(self, flavour: str) -> None:
        item = record(flavour)
        text = render_compose(item)

        assert PASSWORD not in text
        assert "PASSWORD=" not in text and "requirepass" not in text
        assert text.startswith("# Generated by Noust.")

    def test_a_dollar_is_never_left_for_compose_to_interpolate(self, flavour: str) -> None:
        text = render_compose(record(flavour))

        assert re.search(r"(?<!\$)\$(?!\$)", text) is None

    def test_the_same_row_is_the_same_bytes_and_parses_back(self, flavour: str) -> None:
        item = record(flavour)

        assert render_compose(item) == render_compose(item)
        assert yaml.safe_load(render_compose(item)) == build_document(item)

    def test_the_password_variable_noust_reads_is_the_one_the_env_file_sets(
        self, flavour: str
    ) -> None:
        text = render_env(flavour, PASSWORD)

        assert f"{PASSWORD_VARIABLES[flavour]}={PASSWORD}\n" in text
        assert text.startswith("# Generated by Noust.")

    def test_the_engine_is_started_to_fit_its_limit(self, flavour: str) -> None:
        command = engine_command(flavour, 1024)

        assert command is not None
        assert PASSWORD not in " ".join(command)


class TestWhatEachEngineIsTold:
    def test_postgresql_gets_a_quarter_of_the_limit_as_shared_buffers(self) -> None:
        assert engine_command("postgresql", 1024) == ["postgres", "-c", "shared_buffers=256MB"]
        assert engine_command("postgresql", 256) == ["postgres", "-c", "shared_buffers=64MB"]

    def test_the_innodb_pool_is_half_the_limit_and_the_program_is_the_images(self) -> None:
        assert engine_command("mysql", 1024) == ["mysqld", "--innodb-buffer-pool-size=512M"]
        assert engine_command("mariadb", 512) == ["mariadbd", "--innodb-buffer-pool-size=256M"]

    def test_mongodb_gets_half_the_limit_but_never_less_than_the_least_it_takes(self) -> None:
        assert engine_command("mongodb", 2048) == ["mongod", "--wiredTigerCacheSizeGB", "1.00"]
        assert engine_command("mongodb", 512) == ["mongod", "--wiredTigerCacheSizeGB", "0.25"]

    def test_redis_and_valkey_read_their_configuration_from_a_file(self) -> None:
        assert engine_command("redis", 256) == [
            "redis-server",
            "/usr/local/etc/redis/redis.conf",
        ]
        assert engine_command("valkey", 256) == [
            "valkey-server",
            "/usr/local/etc/valkey/valkey.conf",
        ]

    def test_the_redis_password_and_ceiling_are_in_the_file_not_the_command(self) -> None:
        text = render_engine_config(record("redis", memory_limit_mb=512), PASSWORD)

        assert text is not None
        assert f"requirepass {PASSWORD}" in text
        assert "maxmemory 384mb" in text
        assert "maxmemory-policy noeviction" in text
        assert "appendonly yes" in text
        assert PASSWORD not in " ".join(engine_command("redis", 512) or [])

    def test_the_redis_file_is_mounted_read_only_and_the_others_have_none(self) -> None:
        redis = build_document(record("redis"))["services"]["db"]["volumes"]
        postgres = build_document(record("postgresql"))["services"]["db"]["volumes"]

        assert "./engine.conf:/usr/local/etc/redis/redis.conf:ro" in redis
        assert not any("engine.conf" in volume for volume in postgres)
        assert render_engine_config(record("postgresql"), PASSWORD) is None

    def test_the_health_check_of_redis_hands_the_password_over_by_the_containers_environment(
        self,
    ) -> None:
        check = build_document(record("redis"))["services"]["db"]["healthcheck"]["test"]

        assert "$$REDIS_PASSWORD" in check[1]
        assert PASSWORD not in " ".join(check)

    def test_postgresql_eighteen_keeps_its_data_one_level_up(self) -> None:
        old = build_document(record("postgresql", "17"))["services"]["db"]["volumes"][0]
        new = build_document(record("postgresql", "18"))["services"]["db"]["volumes"][0]

        assert old == "noust-db-inst-data:/var/lib/postgresql/data"
        assert new == "noust-db-inst-data:/var/lib/postgresql"

    def test_mongodb_names_the_volume_docker_would_otherwise_leave_anonymous(self) -> None:
        document = build_document(record("mongodb"))

        assert "noust-db-inst-config" in document["volumes"]
        assert "noust-db-inst-config:/data/configdb" in document["services"]["db"]["volumes"]
        assert "noust-db-inst-data:/data/db" in document["services"]["db"]["volumes"]

    def test_mongodb_is_given_its_administrator_by_name(self) -> None:
        text = render_env("mongodb", PASSWORD)

        assert "MONGO_INITDB_ROOT_USERNAME=noust_admin" in text
        assert f"MONGO_INITDB_ROOT_PASSWORD={PASSWORD}" in text

    def test_a_password_an_env_file_would_misread_is_refused(self) -> None:
        for bad in ("a b", "a=b", "a#b", 'a"b', "a$b", "a\nb", ""):
            with pytest.raises(ValueError):
                render_env("postgresql", bad)
        with pytest.raises(ValueError):
            render_engine_config(record("redis"), "a b")


class TestAConnectionStringOfAnInstance:
    """A key says where a database is; the scheme is the engine's."""

    @pytest.mark.parametrize(
        ("key", "scheme"),
        [
            ("postgresql@pg16", "postgresql"),
            ("mysql@maria", "mysql"),
            ("redis@cache", "redis"),
            ("mongodb@docs", "mongodb"),
            ("postgresql@proggest.postgres", "postgresql"),
        ],
    )
    def test_the_scheme_is_the_engines_not_the_keys(self, key: str, scheme: str) -> None:
        from noust.managers.database.urls import connection_url

        url = connection_url(key, database="app", user="u", password="p", port=5441)

        assert url.startswith(f"{scheme}://u:p@localhost:5441/")

    def test_a_redis_instance_still_takes_its_password_alone(self) -> None:
        from noust.managers.database.urls import connection_url

        url = connection_url("redis@cache", database="0", user="default", password="s3", port=6380)

        assert url == "redis://:s3@localhost:6380/0"

    def test_a_redis_instance_is_linked_as_redis_url_and_the_others_as_database_url(self) -> None:
        from noust.managers.database.records import default_env_var

        assert default_env_var("redis@cache") == "REDIS_URL"
        assert default_env_var("redis") == "REDIS_URL"
        assert default_env_var("postgresql@pg16") == "DATABASE_URL"
        assert default_env_var("mongodb@docs") == "DATABASE_URL"
