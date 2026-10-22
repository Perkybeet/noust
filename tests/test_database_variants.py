# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PostgreSQL with an extension already installed: the image variants of an instance (4.0, C-10).

PostGIS and pgvector are files on disk, so an instance that needs one runs the project's own image.
A variant is that image: the same engine and the same majors, another repository and its own tags.
What is defended:

- **a variant is pinned like the engine** (a digest shaped as one, the tag it was read from), and
  the catalog says which processors its image is published for;
- **everything about the instance is the engine's**: its key, its ports, its health probe; only the
  image is another, in the Compose file, in the pull, in the upgrade and in a move to a newer version;
- **a request the server cannot keep is refused first**: an unknown variant, a variant of an engine
  that has none, an image that does not exist for this processor.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import ValidationError
from noust.managers.database import instance_layout as layout
from noust.managers.database import managed as managed_module
from noust.managers.database.instance_catalog import SPECS, offered, resolve_pin, with_variant
from tests.database_instance_support import World, build_world, close_world
from tests.database_migration_support import Lab, build_lab

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    yield built
    close_world()


def service_of(name: str) -> dict[str, Any]:
    return yaml.safe_load(layout.compose_path(name).read_text())["services"]["db"]


class TestTheCatalog:
    def test_postgresql_offers_pgvector_and_postgis(self) -> None:
        assert [variant.name for variant in SPECS["postgresql"].variants] == ["pgvector", "postgis"]

    def test_no_other_engine_has_variants(self) -> None:
        assert [name for name, spec in SPECS.items() if spec.variants] == ["postgresql"]

    def test_every_variant_pin_is_a_digest_with_the_tag_it_was_read_from(self) -> None:
        for variant in SPECS["postgresql"].variants:
            for pin in variant.pins:
                assert DIGEST.fullmatch(pin.digest), (variant.name, pin.version)
                assert pin.tag, f"{variant.name} {pin.version} says no tag"

    def test_a_variant_offers_the_engines_own_majors(self) -> None:
        majors = [pin.version for pin in SPECS["postgresql"].pins]

        for variant in SPECS["postgresql"].variants:
            assert [pin.version for pin in variant.pins] == majors

    def test_each_has_one_default_and_it_is_the_newest(self) -> None:
        for variant in SPECS["postgresql"].variants:
            assert variant.default_pin().version == "18"

    def test_the_listing_names_the_repository_the_tag_and_the_processors(self) -> None:
        [entry] = [item for item in offered() if item["flavour"] == "postgresql"]

        by_name = {variant["name"]: variant for variant in entry["variants"]}
        assert by_name["pgvector"]["image"] == "pgvector/pgvector"
        assert by_name["pgvector"]["platforms"] == ["amd64", "arm64"]
        assert by_name["postgis"]["platforms"] == ["amd64"], "the project publishes amd64 only"
        assert {v["version"]: v["tag"] for v in by_name["postgis"]["versions"]}["17"] == "17-3.5"
        assert all(item["variants"] == [] for item in offered() if item["flavour"] != "postgresql")


class TestResolvingOne:
    def test_none_is_the_engines_own_image(self) -> None:
        assert with_variant(SPECS["postgresql"], None) is SPECS["postgresql"]

    def test_a_variant_is_the_same_engine_from_another_image(self) -> None:
        spec = with_variant(SPECS["postgresql"], "pgvector")

        assert (spec.flavour, spec.engine, spec.container_port) == (
            "postgresql",
            "postgresql",
            5432,
        )
        assert (
            spec.repository == "pgvector/pgvector"
            and spec.display_name == "PostgreSQL with pgvector"
        )
        assert resolve_pin(spec, "17").tag == "pg17"

    def test_the_data_path_is_the_engines_so_18_keeps_its_new_one(self) -> None:
        spec = with_variant(SPECS["postgresql"], "pgvector")

        assert spec.data_path("17") == "/var/lib/postgresql/data"
        assert spec.data_path("18") == "/var/lib/postgresql"

    def test_an_unknown_variant_lists_the_ones_there_are(self) -> None:
        with pytest.raises(ValidationError) as caught:
            with_variant(SPECS["postgresql"], "timescaledb")

        assert caught.value.field == "variant" and "pgvector, postgis" in caught.value.details

    def test_an_engine_without_variants_says_so(self) -> None:
        with pytest.raises(ValidationError) as caught:
            with_variant(SPECS["mysql"], "pgvector")

        assert "has no variants" in caught.value.details


class TestMakingOne:
    def test_the_plan_names_the_projects_image_and_warns_about_dumps(self, world: World) -> None:
        plan = world.managed.plan(
            "vec", "postgresql", version="17", memory="1g", variant="pgvector"
        )

        assert plan.variant == "pgvector" and plan.tag == "pg17"
        assert plan.key == "postgresql@vec" and plan.flavour == "postgresql"
        assert plan.to_dict()["reference"].startswith("pgvector/pgvector:pg17@sha256:")
        assert any("another project" in warning for warning in plan.warnings)

    def test_the_container_is_made_from_the_variants_image_pinned_by_digest(
        self, world: World
    ) -> None:
        described = world.managed.create(
            "vec", "postgresql", version="17", memory="1g", variant="pgvector"
        )

        pin = resolve_pin(with_variant(SPECS["postgresql"], "pgvector"), "17")
        assert service_of("vec")["image"] == f"pgvector/pgvector:pg17@{pin.digest}"
        assert f"pgvector/pgvector@{pin.digest}" in world.host.images
        assert described["variant"] == "pgvector" and described["key"] == "postgresql@vec"
        assert described["display_name"] == "PostgreSQL with pgvector"
        assert described["reference"] == f"pgvector/pgvector:pg17@{pin.digest}"

    def test_the_row_remembers_the_variant_and_the_tag(self, world: World) -> None:
        world.managed.create("vec", "postgresql", version="17", memory="1g", variant="postgis")

        record = world.managed.get("vec")

        assert (record.variant, record.image_tag, record.image) == (
            "postgis",
            "17-3.5",
            "postgis/postgis",
        )
        assert record.tag == "17-3.5" and record.version == "17"

    def test_an_instance_of_the_engines_own_image_has_none(self, world: World) -> None:
        world.managed.create("plain", "postgresql", version="17", memory="1g")

        record = world.managed.get("plain")

        assert (record.variant, record.image_tag, record.tag) == (None, None, "17")

    def test_everything_but_the_image_is_the_engines(self, world: World) -> None:
        world.managed.create("plain", "postgresql", version="17", memory="1g")
        world.managed.create("vec", "postgresql", version="17", memory="1g", variant="pgvector")

        plain, vec = service_of("plain"), service_of("vec")

        assert vec["healthcheck"]["test"] == plain["healthcheck"]["test"]
        assert vec["command"] == plain["command"]
        assert vec["volumes"][0].split(":")[1] == plain["volumes"][0].split(":")[1]

    def test_an_unknown_variant_is_refused_before_anything_is_made(self, world: World) -> None:
        with pytest.raises(ValidationError):
            world.managed.create("vec", "postgresql", memory="1g", variant="timescaledb")

        assert world.managed.records.all() == []

    def test_a_variant_of_an_engine_that_has_none_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.plan("db", "mariadb", memory="1g", variant="pgvector")

        assert caught.value.field == "variant"


class TestAnImageForAnotherProcessor:
    def test_postgis_is_refused_on_an_arm_server_saying_what_to_use(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(managed_module, "host_architecture", lambda: "arm64")

        with pytest.raises(ValidationError) as caught:
            world.managed.plan("geo", "postgresql", memory="1g", variant="postgis")

        assert "arm64" in caught.value.message and "amd64 only" in caught.value.details
        assert caught.value.field == "variant"

    def test_pgvector_is_published_for_both_and_goes_on(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(managed_module, "host_architecture", lambda: "arm64")

        plan = world.managed.plan("vec", "postgresql", memory="1g", variant="pgvector")

        assert plan.variant == "pgvector"

    def test_the_processors_are_named_the_way_registries_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for machine, name in (("x86_64", "amd64"), ("aarch64", "arm64"), ("riscv64", "riscv64")):
            monkeypatch.setattr("platform.machine", lambda machine=machine: machine)
            assert managed_module.host_architecture() == name


class TestUpgradingOne:
    def test_the_newest_image_is_looked_for_under_the_variants_tag(self, world: World) -> None:
        world.managed.create("vec", "postgresql", version="17", memory="1g", variant="pgvector")
        pin = resolve_pin(with_variant(SPECS["postgresql"], "pgvector"), "17")
        world.host.served["pgvector/pgvector:pg17"] = pin.digest

        outcome = world.managed.upgrade("vec", backup=False)

        pulled = [call for call in world.host.calls if call[:2] == ("docker", "pull")]
        assert ("docker", "pull", "pgvector/pgvector:pg17") in pulled
        assert outcome.changed is False


class TestTheStore:
    def test_a_tag_that_could_be_an_option_or_a_second_image_is_refused(self, world: World) -> None:
        from noust.core.db_instance_store import DbInstanceStore

        store = DbInstanceStore(world.store)
        for tag in ("-v", "pg17@sha256:abc", "pg 17", "a" * 129, ""):
            with pytest.raises(ValidationError):
                store.create(
                    name="bad",
                    flavour="postgresql",
                    version="17",
                    image="postgres",
                    digest="sha256:" + "a" * 64,
                    host_port=5440,
                    memory_limit_mb=256,
                    created_by=None,
                    variant="pgvector",
                    image_tag=tag,
                )
        assert store.get("bad") is None


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    yield from build_lab(tmp_path, monkeypatch)


class TestMovingToANewerVersion:
    def test_the_new_instance_runs_the_same_variant(self, lab: Lab) -> None:
        lab.managed.create("vec16", "postgresql", version="16", memory="1g", variant="pgvector")
        lab.service.create("postgresql@vec16", "blog_db")
        lab.service.refresh_instances()

        plan = lab.migrations.plan("vec16", "17")
        lab.migrations.run("vec16", "17")

        assert plan.new_instance["variant"] == "pgvector" and plan.target == "pgvector17"
        record = lab.managed.get("pgvector17")
        assert (record.variant, record.image, record.image_tag) == (
            "pgvector",
            "pgvector/pgvector",
            "pg17",
        )

    def test_an_engine_of_the_hosts_moves_to_the_engines_own_image(self, lab: Lab) -> None:
        lab.migrations.run("postgresql", "17", memory="1g")

        record = lab.managed.get("postgresql17")

        assert (record.variant, record.image, record.image_tag) == (None, "postgres", None)
