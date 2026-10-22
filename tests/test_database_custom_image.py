# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An instance from an image, a command and arguments of the operator's own (4.0, the Dokploy gap).

Noust offers the official images, pinned by digest by Noust. Sometimes the operator wants another:
a build of their own with an extension, a vendor's, a flag the engine needs. That is allowed, with
the price said and accepted, and with the part that makes the official ones safe kept: **the
operator pins it**, by digest, because a tag is a promise and a digest is a file. What is defended:

- **an image is a digest or it is refused**, with how to get the digest, and it is shaped like a
  reference Docker accepts and not like an option or a second image;
- **the price is said and accepted first**: a request for an image, a command or arguments is a
  plan that carries the warning and a creation that refuses without the confirmation;
- **everything about the instance is the engine's** except what the operator changed: its ports,
  its volumes, its health probe, its memory limits, and the arguments Noust itself passes;
- **a command or an argument never carries a secret to a process list**, and cannot be a shell;
- **the row remembers it all**, so a restart, an upgrade and a repair write the same file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.db_instance_store import CUSTOM_VARIANT, DbInstanceStore
from noust.core.exceptions import ConfirmationRequired, ValidationError
from noust.core.runner import FakeRunner
from noust.managers.database import instance_layout as layout
from noust.managers.database import managed as managed_module
from noust.managers.database.instance_catalog import (
    SPECS,
    custom_spec,
    parse_custom_image,
    spec_of_record,
)
from noust.managers.database.instances import discover
from tests.database_instance_support import World, build_world, close_world
from tests.test_database_instances import container

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
IMAGE = f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    yield built
    close_world()


def service_of(name: str) -> dict[str, Any]:
    return yaml.safe_load(layout.compose_path(name).read_text())["services"]["db"]


class TestTheReference:
    def test_a_reference_is_a_repository_a_tag_and_the_digest_that_pins_it(self) -> None:
        image = parse_custom_image(IMAGE)

        assert (image.repository, image.tag, image.digest) == (
            "ghcr.io/acme/pg-timescale",
            "17.2",
            DIGEST,
        )

    def test_the_tag_is_optional_and_the_digest_is_not(self) -> None:
        assert parse_custom_image(f"acme/pg@{DIGEST}").tag is None

        with pytest.raises(ValidationError) as caught:
            parse_custom_image("ghcr.io/acme/pg-timescale:17.2")

        assert caught.value.field == "image"
        assert "digest" in caught.value.message.lower()
        assert "sha256:" in caught.value.details

    @pytest.mark.parametrize(
        "reference",
        [
            "",
            "-v",
            f"--privileged@{DIGEST}",
            f"https://ghcr.io/acme/pg@{DIGEST}",
            f"acme/pg x@{DIGEST}",
            f"ACME/pg@{DIGEST}",
            f"acme/pg@sha256:{'z' * 64}",
            f"acme/pg@{DIGEST}\n",
            f"acme/pg@{DIGEST}@{DIGEST}",
            "a" * 300,
        ],
    )
    def test_what_could_be_an_option_or_a_second_image_is_refused(self, reference: str) -> None:
        with pytest.raises(ValidationError):
            parse_custom_image(reference)

    def test_it_is_the_engine_from_another_image_with_one_version_the_operator_names(self) -> None:
        spec = custom_spec(SPECS["postgresql"], parse_custom_image(IMAGE), "17")

        assert (spec.flavour, spec.engine, spec.container_port) == (
            "postgresql",
            "postgresql",
            5432,
        )
        assert spec.repository == "ghcr.io/acme/pg-timescale"
        assert [(pin.version, pin.digest, pin.tag) for pin in spec.pins] == [("17", DIGEST, "17.2")]
        assert spec.data_path("17") == "/var/lib/postgresql/data"
        assert spec.data_path("18") == "/var/lib/postgresql"
        assert "your own image" in spec.display_name

    def test_the_version_is_numbers_and_dots(self) -> None:
        with pytest.raises(ValidationError) as caught:
            custom_spec(SPECS["postgresql"], parse_custom_image(IMAGE), "latest")

        assert caught.value.field == "version"


class TestTheStore:
    def test_the_row_remembers_the_image_the_command_and_the_arguments(self, world: World) -> None:
        store = DbInstanceStore(world.store)

        record = store.create(
            name="own",
            flavour="postgresql",
            version="17",
            image="ghcr.io/acme/pg-timescale",
            digest=DIGEST,
            host_port=5440,
            memory_limit_mb=512,
            created_by=None,
            variant=CUSTOM_VARIANT,
            image_tag="17.2",
            command=["postgres", "-c", "max_connections=77"],
        )
        again = store.get("own")

        assert again == record
        assert again.custom_image is True
        assert again.command == ("postgres", "-c", "max_connections=77")
        assert again.args == ()
        assert again.reference == f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"
        assert again.to_dict()["command"] == ["postgres", "-c", "max_connections=77"]

    def test_arguments_are_kept_apart_from_a_command(self, world: World) -> None:
        store = DbInstanceStore(world.store)

        record = store.create(
            name="own",
            flavour="postgresql",
            version="17",
            image="postgres",
            digest=DIGEST,
            host_port=5440,
            memory_limit_mb=512,
            created_by=None,
            args=["-c", "max_connections=77"],
        )

        assert record.args == ("-c", "max_connections=77") and record.command == ()
        assert record.custom_image is False

    def test_a_command_and_arguments_together_are_refused(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            DbInstanceStore(world.store).create(
                name="own",
                flavour="postgresql",
                version="17",
                image="postgres",
                digest=DIGEST,
                host_port=5440,
                memory_limit_mb=512,
                created_by=None,
                command=["postgres"],
                args=["-c", "x=1"],
            )

        assert "command" in caught.value.message.lower()

    @pytest.mark.parametrize(
        "words",
        [
            [""],
            ["a\nb"],
            ["a\x00b"],
            ["a" * 513],
            ["x"] * 65,
            "postgres -c x=1",
            [1],
            ["--requirepass", "hunter2"],
            ["--requirepass=hunter2"],
            ["--masterauth", "hunter2"],
        ],
    )
    def test_a_word_that_is_not_one_is_refused_and_a_secret_does_not_go_in_argv(
        self, world: World, words: Any
    ) -> None:
        with pytest.raises(ValidationError):
            DbInstanceStore(world.store).create(
                name="own",
                flavour="redis",
                version="8.0",
                image="redis",
                digest=DIGEST,
                host_port=6390,
                memory_limit_mb=256,
                created_by=None,
                args=words,
            )

        assert DbInstanceStore(world.store).get("own") is None

    def test_changing_them_later_is_a_validated_setter(self, world: World) -> None:
        store = DbInstanceStore(world.store)
        store.create(
            name="own",
            flavour="postgresql",
            version="17",
            image="postgres",
            digest=DIGEST,
            host_port=5440,
            memory_limit_mb=512,
            created_by=None,
        )

        assert store.set_launch("own", command=None, args=["-c", "work_mem=8MB"]) is True
        assert store.get("own").args == ("-c", "work_mem=8MB")
        assert store.set_launch("own", command=["postgres"], args=None) is True
        assert store.get("own").command == ("postgres",) and store.get("own").args == ()
        with pytest.raises(ValidationError):
            store.set_launch("own", command=["postgres"], args=["-c", "x=1"])
        assert store.set_launch("own", command=None, args=None) is True
        assert store.get("own").command == () and store.get("own").args == ()
        assert store.set_launch("ghost", command=None, args=None) is False

    def test_the_image_is_replaced_whole_by_the_operator(self, world: World) -> None:
        store = DbInstanceStore(world.store)
        store.create(
            name="own",
            flavour="postgresql",
            version="17",
            image="ghcr.io/acme/pg-timescale",
            digest=DIGEST,
            host_port=5440,
            memory_limit_mb=512,
            created_by=None,
            variant=CUSTOM_VARIANT,
            image_tag="17.2",
        )

        assert store.set_image("own", image="ghcr.io/acme/pg-timescale", tag="17.3", digest=OTHER)
        row = store.get("own")

        assert (row.image_tag, row.digest, row.previous_digest) == ("17.3", OTHER, DIGEST)
        with pytest.raises(ValidationError):
            store.set_image("own", image="-x", tag="1", digest=OTHER)
        with pytest.raises(ValidationError):
            store.set_image("own", image="acme/pg", tag="1", digest="latest")


class TestTheComposeFile:
    def test_the_operators_arguments_follow_the_ones_noust_passes(self, world: World) -> None:
        world.managed.create(
            "plain",
            "postgresql",
            version="17",
            memory="1g",
            accept_untested=True,
            args=["-c", "max_connections=77"],
        )
        world.managed.create("ref", "postgresql", version="17", memory="1g")

        mine, reference = service_of("plain")["command"], service_of("ref")["command"]

        assert mine == [*reference, "-c", "max_connections=77"]
        assert mine[:3] == ["postgres", "-c", "shared_buffers=256MB"]

    def test_a_command_is_the_whole_command_and_noust_adds_nothing(self, world: World) -> None:
        world.managed.create(
            "own",
            "postgresql",
            version="17",
            memory="1g",
            accept_untested=True,
            command=["postgres", "-c", "max_connections=33"],
        )

        assert service_of("own")["command"] == ["postgres", "-c", "max_connections=33"]

    def test_everything_else_is_the_engines(self, world: World) -> None:
        world.managed.create("ref", "postgresql", version="17", memory="1g")
        world.managed.create(
            "own",
            "postgresql",
            version="17",
            memory="1g",
            accept_untested=True,
            image=IMAGE,
        )

        own, ref = service_of("own"), service_of("ref")

        assert own["image"] == f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"
        assert own["healthcheck"] == ref["healthcheck"]
        assert own["command"] == ref["command"]
        assert own["ports"][0].split(":")[0] == "127.0.0.1"
        assert own["volumes"][0].split(":")[1] == ref["volumes"][0].split(":")[1]
        assert own["deploy"] == ref["deploy"]

    def test_the_redis_family_gets_its_arguments_too(self, world: World) -> None:
        world.host.exec_stdout = ""
        world.managed.create(
            "cache",
            "redis",
            version="8.0",
            memory="256m",
            accept_untested=True,
            args=["--maxmemory-policy", "allkeys-lru"],
        )

        command = service_of("cache")["command"]

        assert command[0] == "redis-server" and command[-2:] == [
            "--maxmemory-policy",
            "allkeys-lru",
        ]


class TestMakingOne:
    def test_the_plan_carries_the_price_and_asks_for_the_confirmation(self, world: World) -> None:
        plan = world.managed.plan("own", "postgresql", version="17", memory="1g", image=IMAGE)

        assert plan.custom is True and plan.requires_confirmation is True
        assert plan.image == "ghcr.io/acme/pg-timescale" and plan.digest == DIGEST
        assert plan.to_dict()["reference"] == f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"
        said = " ".join(plan.warnings)
        assert "not tested" in said and "your" in said
        assert DIGEST in said or "pinned" in said

    def test_the_plan_of_an_official_image_asks_for_nothing(self, world: World) -> None:
        plan = world.managed.plan("ref", "postgresql", version="17", memory="1g")

        assert plan.custom is False and plan.requires_confirmation is False

    @pytest.mark.parametrize(
        "extra",
        [{"image": IMAGE}, {"args": ["-c", "x=1"]}, {"command": ["postgres"]}],
    )
    def test_without_the_confirmation_nothing_is_made(
        self, world: World, extra: dict[str, Any]
    ) -> None:
        with pytest.raises(ConfirmationRequired) as caught:
            world.managed.create("own", "postgresql", version="17", memory="1g", **extra)

        assert any("not tested" in warning for warning in caught.value.warnings)
        assert world.managed.records.all() == []
        assert not layout.directory("own").exists()

    def test_an_image_needs_the_version_it_claims_to_be(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.plan("own", "postgresql", memory="1g", image=IMAGE)

        assert caught.value.field == "version"

    def test_an_image_and_a_variant_are_two_answers_to_one_question(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.plan(
                "own", "postgresql", version="17", memory="1g", image=IMAGE, variant="pgvector"
            )

        assert caught.value.field == "image"

    def test_the_container_runs_exactly_the_digest_the_operator_pinned(self, world: World) -> None:
        described = world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )

        assert f"ghcr.io/acme/pg-timescale@{DIGEST}" in world.host.images
        assert described["variant"] == CUSTOM_VARIANT and described["custom_image"] is True
        assert described["reference"] == f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"
        assert described["key"] == "postgresql@own"
        record = world.managed.get("own")
        assert (record.image, record.image_tag, record.digest) == (
            "ghcr.io/acme/pg-timescale",
            "17.2",
            DIGEST,
        )

    def test_the_audit_trail_says_it_was_not_the_catalog(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        monkeypatch.setattr(
            world.service,
            "audit",
            lambda event, target, *a, **kw: events.append({"event": event, "target": target, **kw}),
        )

        world.managed.create(
            "own",
            "postgresql",
            version="17",
            memory="1g",
            image=IMAGE,
            accept_untested=True,
            args=None,
        )

        made = next(e for e in events if e["event"] == "db.instance.create")
        assert made["variant"] == CUSTOM_VARIANT

    def test_a_pull_that_fails_leaves_nothing(self, world: World) -> None:
        world.host.fail[("docker", "pull")] = "manifest unknown"

        with pytest.raises(Exception, match="could not be made"):
            world.managed.create(
                "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
            )

        assert world.managed.records.all() == []


class TestOneThatIsMade:
    def test_the_spec_is_rebuilt_from_the_row(self, world: World) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )

        spec = spec_of_record(world.managed.get("own"))

        assert spec.repository == "ghcr.io/acme/pg-timescale"
        assert spec.default_pin().digest == DIGEST

    def test_an_official_row_gets_the_catalogs_spec(self, world: World) -> None:
        world.managed.create("ref", "postgresql", version="17", memory="1g")

        assert spec_of_record(world.managed.get("ref")).repository == "postgres"

    def test_the_description_says_what_the_operator_chose(self, world: World) -> None:
        world.managed.create(
            "own",
            "postgresql",
            version="17",
            memory="1g",
            accept_untested=True,
            args=["-c", "max_connections=77"],
        )

        described = world.managed.describe(world.managed.get("own"))

        assert described["args"] == ["-c", "max_connections=77"]
        assert described["command"] == [] and described["custom_image"] is False
        assert described["customised"] is True


class TestUpgradingOne:
    def test_an_instance_on_an_image_of_the_operators_is_never_moved_to_what_a_registry_serves(
        self, world: World
    ) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )

        with pytest.raises(ValidationError) as caught:
            world.managed.upgrade("own", backup=False)

        assert caught.value.field == "image"
        assert "--image" in caught.value.details
        assert world.managed.get("own").digest == DIGEST

    def test_the_operator_names_the_next_image_and_it_is_pinned_the_same_way(
        self, world: World
    ) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )
        newer = f"ghcr.io/acme/pg-timescale:17.3@{OTHER}"

        outcome = world.managed.upgrade("own", backup=False, image=newer, accept_untested=True)

        assert outcome.changed is True and outcome.to_digest == OTHER
        assert service_of("own")["image"] == f"ghcr.io/acme/pg-timescale:17.3@{OTHER}"
        row = world.managed.get("own")
        assert (row.digest, row.previous_digest, row.image_tag) == (OTHER, DIGEST, "17.3")

    def test_the_same_image_is_nothing_to_do(self, world: World) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )

        outcome = world.managed.upgrade("own", backup=False, image=IMAGE, accept_untested=True)

        assert outcome.changed is False

    def test_naming_an_image_needs_the_confirmation_again(self, world: World) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )

        with pytest.raises(ConfirmationRequired):
            world.managed.upgrade("own", backup=False, image=f"acme/pg@{OTHER}")

        assert world.managed.get("own").digest == DIGEST

    def test_a_catalog_instance_does_not_take_an_image_here(self, world: World) -> None:
        world.managed.create("ref", "postgresql", version="17", memory="1g")

        with pytest.raises(ValidationError) as caught:
            world.managed.upgrade("ref", backup=False, image=IMAGE, accept_untested=True)

        assert caught.value.field == "image"

    def test_an_image_that_does_not_come_up_puts_the_previous_one_back(self, world: World) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )
        world.host.health["noust-db-own"] = ["unhealthy", "healthy"]

        with pytest.raises(Exception, match="runs the previous one again"):
            world.managed.upgrade(
                "own", backup=False, image=f"acme/pg@{OTHER}", accept_untested=True
            )

        assert world.managed.get("own").digest == DIGEST
        assert service_of("own")["image"] == f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"


def labelled(name: str, image: str, **labels: str) -> dict[str, Any]:
    entry = container(name, image, env=["POSTGRES_PASSWORD=x"], exposed=("5432/tcp",))
    entry["Config"]["Labels"].update(labels)
    return entry


OWN_LABELS = {
    layout.MANAGED_LABEL: "true",
    layout.INSTANCE_LABEL: "own",
    layout.FLAVOUR_LABEL: "postgresql",
}


class TestDiscoveryOfAnImageNobodyKnows:
    """A container is a database by what Noust wrote on it, not only by the name of its image."""

    @staticmethod
    def host(runner: FakeRunner, *entries: dict[str, Any]) -> FakeRunner:
        runner.script(
            ["docker", "ps"],
            stdout="".join(
                f"{e['Id']}\t{e['Config']['Image']}\t"
                f"{e['Config']['Labels'].get(layout.MANAGED_LABEL, '')}\n"
                for e in entries
            ),
        )
        runner.script(["docker", "inspect"], stdout=json.dumps(list(entries)))
        return runner

    def test_the_listing_asks_docker_for_the_label_too(self, runner: FakeRunner) -> None:
        self.host(runner)

        discover(runner)

        (listing,) = [call for call in runner.calls if call[:3] == ("docker", "ps", "-a")]
        assert any(layout.MANAGED_LABEL in part for part in listing)

    def test_a_container_noust_made_is_a_database_whatever_its_image_is_called(
        self, runner: FakeRunner
    ) -> None:
        self.host(runner, labelled("noust-db-own", "ghcr.io/acme/pg-timescale:17.2", **OWN_LABELS))

        (found,) = discover(runner)

        assert found.key == "postgresql@noust-db-own"
        assert (found.engine, found.flavour, found.official) == ("postgresql", "postgres", False)
        assert found.instance_label == "own"

    def test_the_same_image_without_the_labels_is_not_one(self, runner: FakeRunner) -> None:
        self.host(runner, labelled("stranger", "ghcr.io/acme/pg-timescale:17.2"))

        assert discover(runner) == []

    @pytest.mark.parametrize(
        "labels",
        [
            {**OWN_LABELS, layout.FLAVOUR_LABEL: "nginx"},
            {**OWN_LABELS, layout.MANAGED_LABEL: "false"},
            {layout.MANAGED_LABEL: "true", layout.FLAVOUR_LABEL: "postgresql"},
            {**OWN_LABELS, layout.FLAVOUR_LABEL: ""},
        ],
    )
    def test_a_label_that_does_not_say_what_it_must_is_not_taken_on_trust(
        self, runner: FakeRunner, labels: dict[str, str]
    ) -> None:
        self.host(runner, labelled("odd", "ghcr.io/acme/pg-timescale:17.2", **labels))

        assert discover(runner) == []

    def test_a_catalog_image_is_still_known_by_its_name(self, runner: FakeRunner) -> None:
        self.host(runner, labelled("plain", "postgres:17-alpine"))

        assert [item.key for item in discover(runner)] == ["postgresql@plain"]

    def test_the_mongo_and_postgres_flavours_are_spelled_as_discovery_spells_them(
        self, runner: FakeRunner
    ) -> None:
        entry = labelled(
            "noust-db-doc",
            "ghcr.io/acme/mongo-custom:8",
            **{**OWN_LABELS, layout.INSTANCE_LABEL: "doc", layout.FLAVOUR_LABEL: "mongodb"},
        )
        self.host(runner, entry)

        (found,) = discover(runner)

        assert (found.engine, found.flavour) == ("mongodb", "mongo")


class TestAnInstanceOfAnImageNobodyKnows:
    def test_it_is_listed_bound_and_managed_like_any_other(self, world: World) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )

        instances = {item.key: item for item in world.service.instances()}

        found = instances["postgresql@own"]
        assert found.managed == "own" and found.running is True
        assert found.image.startswith("ghcr.io/acme/pg-timescale:17.2@sha256:")

    def test_one_that_is_down_is_still_listed_from_its_row(self, world: World) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )
        world.managed.stop("own")

        instances = {item.key: item for item in world.service.instances()}

        assert instances["postgresql@own"].running is False
