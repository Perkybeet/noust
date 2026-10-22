# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
KeyDB as an engine Noust runs (checklist C-08).

KeyDB speaks Redis's protocol and keeps its files, so the Redis manager drives
it; what is its own is the image (pinned by tag and digest like every other),
the program names inside it, the configuration file its image reads and the
variable the password lives in. It is container-only: no distribution
packages it, so asking to install it on the host says to make an instance.
"""

from __future__ import annotations

import re

import pytest
import yaml

from noust.core.exceptions import ValidationError
from noust.managers.database import flavours, instance_compose
from noust.managers.database.instance_catalog import offered, resolve_pin, spec_for
from noust.managers.database.instance_records import stub_instance
from noust.managers.database.instances import image_engine
from noust.managers.database.redis import RedisManager
from tests.database_instance_support import World, build_world, close_world
from tests.test_database_instance_compose import PASSWORD, record


class TestTheCatalog:
    def test_keydb_is_offered_as_a_redis_family_image_pinned_by_digest(self) -> None:
        spec = spec_for("keydb")
        pin = resolve_pin(spec, None)

        assert spec.engine == "redis"
        assert spec.display_name == "KeyDB"
        assert spec.repository == "eqalpha/keydb"
        assert spec.container_port == 6379
        assert spec.data_path(pin.version) == "/data"
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", pin.digest)
        assert pin.version == "6.3" and pin.tag == "latest"
        assert spec.platforms == ("amd64", "arm64")

    def test_it_is_listed_with_the_other_flavours_for_the_api_and_the_cli(self) -> None:
        entry = next(item for item in offered() if item["flavour"] == "keydb")

        assert entry["engine"] == "redis"
        assert entry["display_name"] == "KeyDB"
        assert [version["version"] for version in entry["versions"]] == ["6.3"]
        # The project publishes no end-of-support date: none is invented.
        assert entry["versions"][0]["end_of_life"] is None

    def test_discovery_knows_its_image_as_keydb(self) -> None:
        match = image_engine("eqalpha/keydb:latest@sha256:" + "a" * 64)

        assert match is not None
        assert (match.engine, match.flavour, match.official) == ("redis", "keydb", False)
        assert image_engine("docker.io/eqalpha/keydb") is not None
        assert image_engine("someone/keydb-wrapper") is None

    def test_it_is_a_flavour_noust_names_but_never_installs_on_the_host(self) -> None:
        assert flavours.FLAVOURS["keydb"].container_only is True
        with pytest.raises(ValidationError) as caught:
            flavours.resolve_flavour("keydb", "redis", flavour="keydb")
        assert "noust db instance create" in (caught.value.details or "")
        with pytest.raises(ValidationError) as refused:
            flavours.plan_install("keydb", None, flavours.OsRelease.__new__(flavours.OsRelease))
        assert "--engine keydb" in (refused.value.details or "")

    def test_the_install_dialog_does_not_list_it(self) -> None:
        names = [
            choice.flavour
            for choice in flavours.catalog(flavours.read_os_release(flavours.HOST), {}, apt=True)
        ]
        assert "keydb" not in names
        assert {"redis", "valkey"} <= set(names)


class TestTheComposeProject:
    def test_it_starts_keydb_from_the_configuration_noust_writes(self) -> None:
        document = instance_compose.build_document(record("keydb", image_tag="latest"))
        service = document["services"]["db"]

        assert service["command"] == ["keydb-server", "/etc/keydb/keydb.conf"]
        assert "./redis.conf:/etc/keydb/keydb.conf:ro" in service["volumes"] or any(
            volume.endswith(":/etc/keydb/keydb.conf:ro") for volume in service["volumes"]
        )
        assert service["ports"] == ["127.0.0.1:55432:6379"]
        assert service["image"].startswith("eqalpha/keydb:latest@sha256:")
        assert service["deploy"]["resources"]["limits"]["memory"].endswith("M")

    def test_its_health_check_signs_in_with_the_variable_it_owns(self) -> None:
        test = instance_compose.build_document(record("keydb"))["services"]["db"]["healthcheck"][
            "test"
        ]

        assert test[0] == "CMD-SHELL"
        assert "keydb-cli ping" in test[1]
        assert "$$KEYDB_PASSWORD" in test[1]

    def test_the_password_is_in_the_environment_file_and_the_configuration_only(self) -> None:
        env = instance_compose.render_env("keydb", PASSWORD)
        config = instance_compose.render_engine_config(
            record("keydb", memory_limit_mb=512), PASSWORD
        )
        compose = instance_compose.render_compose(record("keydb"))

        assert f"KEYDB_PASSWORD={PASSWORD}" in env
        assert config is not None and f"requirepass {PASSWORD}" in config
        assert "maxmemory 384mb" in config and "maxmemory-policy noeviction" in config
        assert PASSWORD not in compose
        assert yaml.safe_load(compose)["services"]["db"]["env_file"]

    def test_it_serves_tls_on_its_one_port_like_the_rest_of_the_family(self) -> None:
        assert instance_compose.tls_supported("keydb", "6.3", "required") is None
        reason = instance_compose.tls_supported("keydb", "6.3", "optional")
        assert reason is not None and "KeyDB" in reason


class TestTheClient:
    def test_it_reaches_the_server_with_the_cli_keydb_ships(self) -> None:
        instance = stub_instance(record("keydb"), state="running")

        argv = instance.exec_argv(["redis-cli", "PING"])

        # sh <mode> <target> <variables the password may be in> <programs> <arguments...>
        assert argv[-1] == "PING"
        assert argv[-2] == "redis-cli,valkey-cli,keydb-cli"
        assert "KEYDB_PASSWORD" in argv[-3].split(",")
        assert argv[-4] == "REDISCLI_AUTH"

    def test_the_manager_names_it_and_keeps_redis_dates_out_of_it(self) -> None:
        manager = RedisManager().bind(stub_instance(record("keydb"), state="running"))

        assert manager.DISPLAY_NAME == "KeyDB"
        assert manager.EOL_FAMILY == ""
        assert manager.ENGINE_NAME.startswith("redis@")


class TestMakingOne:
    @pytest.fixture
    def world(self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
        built = build_world(tmp_path, monkeypatch)  # type: ignore[arg-type]
        try:
            yield built
        finally:
            close_world()

    def test_an_instance_comes_up_under_the_redis_key_and_keeps_its_password_out_of_argv(
        self, world: World
    ) -> None:
        world.host.exec_stdout = ""

        described = world.managed.create("kv", "keydb", version="6.3", memory="256m")

        assert described["key"] == "redis@kv"
        assert described["version"] == "6.3"
        assert described["flavour"] == "keydb" if "flavour" in described else True
        password = world.password("kv")
        for argv in world.host.calls:
            assert password not in " ".join(str(part) for part in argv)
