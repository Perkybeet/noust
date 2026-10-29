# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Making an instance of a version without support, and of an image Noust pins (4.1, item 103).

On a server in memory (:mod:`tests.support.database_instance_support`): Docker, systemd and the
registry answer from what the test holds, and the Compose file Noust writes is what starts the
container. What is defended:

- **the plan says it before anything is made**: the date, and that the operator has to confirm;
- **nothing is made without the confirmation**, and the refusal names the request field
  (``unsupported_ok``) and carries a sentence the console translates;
- **the container runs the digest the catalog pins**, never the tag a registry can move;
- **an image given by registry and tag is pinned when it is made**: Noust pulls the tag, reads
  the digest, records it, and starts the container from the digest. A registry that does not
  say which digest it served leaves nothing behind.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ConfirmationRequired, ValidationError
from noust.managers.database import instance_layout as layout
from noust.managers.database import managed as managed_module
from noust.managers.database.instance_catalog import SPECS, parse_custom_image
from tests.support.database_instance_support import World, build_world, close_world

OWN_DIGEST = "sha256:" + "ab" * 32
REGISTRY = "ghcr.io/acme/pg-timescale"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    yield built
    close_world()


class TestThePlanOfAnOlderVersion:
    def test_it_says_the_date_and_that_the_operator_has_to_confirm(self, world: World) -> None:
        plan = world.managed.plan("old", "mysql", version="5.7", memory="1g")

        assert plan.unsupported is True
        assert (plan.support, plan.end_of_life) == ("ended", "2023-10-31")
        assert plan.requires_unsupported_ok is True
        said = plan.to_dict()
        assert said["unsupported"] is True and said["end_of_life"] == "2023-10-31"
        assert said["requires_unsupported_ok"] is True
        assert any("2023-10-31" in warning for warning in plan.warnings)

    def test_a_current_version_asks_for_nothing(self, world: World) -> None:
        plan = world.managed.plan("new", "mysql", version="8.4", memory="1g")

        assert plan.unsupported is False and plan.requires_unsupported_ok is False
        assert plan.to_dict()["end_of_life"] == "2032-04-30"

    def test_a_processor_the_older_image_is_not_published_for_is_refused_with_why(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(managed_module, "host_architecture", lambda: "arm64")

        with pytest.raises(ValidationError) as caught:
            world.managed.plan("old", "mysql", version="5.7", memory="1g")

        assert "arm64" in caught.value.message and caught.value.field == "version"
        assert "amd64" in caught.value.details
        # Its sibling that is published for both is fine on the same server.
        assert world.managed.plan("old", "mysql", version="8.0", memory="1g").unsupported

    def test_an_image_of_the_operators_that_says_it_is_an_old_version_is_judged_by_that(
        self, world: World
    ) -> None:
        plan = world.managed.plan(
            "own",
            "mysql",
            version="5.7",
            memory="1g",
            image=f"ghcr.io/acme/mysql:5.7@{OWN_DIGEST}",
        )

        assert plan.unsupported is True and plan.custom is True


class TestMakingOneWithoutSupport:
    def test_without_the_confirmation_nothing_is_made_and_the_error_names_the_field(
        self, world: World
    ) -> None:
        with pytest.raises(ConfirmationRequired) as caught:
            world.managed.create("old", "mysql", version="5.7", memory="1g")

        assert caught.value.field == "unsupported_ok"
        assert caught.value.coded is not None
        assert caught.value.coded.code == "database_servers.unsupported_ended"
        assert dict(caught.value.coded.params) == {
            "name": "MySQL",
            "version": "5.7",
            "end_of_life": "2023-10-31",
        }
        assert "2023-10-31" in caught.value.message
        assert world.managed.records.all() == []
        assert not layout.directory("old").exists()

    def test_with_it_the_instance_runs_the_digest_the_catalog_pins(self, world: World) -> None:
        described = world.managed.create(
            "old", "mysql", version="5.7", memory="1g", unsupported_ok=True
        )

        pin = next(pin for pin in SPECS["mysql"].pins if pin.version == "5.7")
        assert described["digest"] == pin.digest
        assert f"mysql@{pin.digest}" in world.host.images
        assert (described["support"], described["end_of_life"]) == ("ended", "2023-10-31")
        assert described["key"] == "mysql@old"

    def test_the_audit_trail_says_it_was_made_without_support(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        monkeypatch.setattr(
            world.service,
            "audit",
            lambda event, target, *a, **kw: events.append({"event": event, **kw}),
        )

        world.managed.create("old", "mysql", version="8.0", memory="1g", unsupported_ok=True)

        made = next(e for e in events if e["event"] == "db.instance.create")
        assert made["unsupported"] is True and made["end_of_life"] == "2026-04-30"

    def test_a_version_that_is_in_the_table_says_when_its_support_ends(self, world: World) -> None:
        with pytest.raises(ConfirmationRequired) as caught:
            world.managed.create("old", "postgresql", version="14", memory="1g")

        assert caught.value.coded is not None
        assert caught.value.coded.code in {
            "database_servers.unsupported_ended",
            "database_servers.unsupported_ending",
        }
        assert "2026-11-12" in caught.value.message

    def test_an_image_of_the_operators_needs_both_confirmations_one_at_a_time(
        self, world: World
    ) -> None:
        image = f"ghcr.io/acme/mysql:5.7@{OWN_DIGEST}"

        with pytest.raises(ConfirmationRequired) as unsupported:
            world.managed.create(
                "own", "mysql", version="5.7", memory="1g", image=image, accept_untested=True
            )
        with pytest.raises(ConfirmationRequired) as untested:
            world.managed.create(
                "own", "mysql", version="5.7", memory="1g", image=image, unsupported_ok=True
            )

        assert unsupported.value.field == "unsupported_ok"
        assert untested.value.field == "accept_untested"
        assert world.managed.records.all() == []

    def test_the_older_instance_lives_beside_a_current_one_of_the_same_engine(
        self, world: World
    ) -> None:
        world.managed.create("current", "mysql", version="8.4", memory="1g")
        world.managed.create("old", "mysql", version="8.0", memory="1g", unsupported_ok=True)

        assert {item["key"] for item in world.managed.all()} == {"mysql@current", "mysql@old"}
        assert len({item["host_port"] for item in world.managed.all()}) == 2


class TestTheReferenceOfAnImageNoustPins:
    def test_a_tag_without_a_digest_is_accepted_when_noust_pins_it(self) -> None:
        image = parse_custom_image("ghcr.io/acme/pg:17.2", pin_later=True)

        assert (image.repository, image.tag, image.digest) == ("ghcr.io/acme/pg", "17.2", "")

    def test_but_a_tag_is_still_needed(self) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_custom_image("ghcr.io/acme/pg", pin_later=True)

        assert caught.value.field == "image"
        assert "tag" in caught.value.message.lower()

    def test_a_digest_given_is_kept(self) -> None:
        image = parse_custom_image(f"acme/pg:1@{OWN_DIGEST}", pin_later=True)

        assert image.digest == OWN_DIGEST

    def test_by_default_a_digest_is_still_mandatory(self) -> None:
        with pytest.raises(ValidationError):
            parse_custom_image("ghcr.io/acme/pg:17.2")


class TestPinningAtCreation:
    def test_the_plan_says_the_digest_is_read_when_it_is_made(self, world: World) -> None:
        plan = world.managed.plan(
            "own", "postgresql", version="17", memory="1g", image=f"{REGISTRY}:17.2"
        )

        assert plan.custom is True and plan.digest == ""
        said = plan.to_dict()
        assert said["digest_pending"] is True
        assert said["reference"] == f"{REGISTRY}:17.2"
        assert any("digest" in warning and "pinned" in warning for warning in plan.warnings)

    def test_creating_pulls_the_tag_and_the_row_keeps_the_digest_it_was(self, world: World) -> None:
        world.host.served[f"{REGISTRY}:17.2"] = OWN_DIGEST

        described = world.managed.create(
            "own",
            "postgresql",
            version="17",
            memory="1g",
            image=f"{REGISTRY}:17.2",
            accept_untested=True,
        )

        assert described["digest"] == OWN_DIGEST
        assert described["reference"] == f"{REGISTRY}:17.2@{OWN_DIGEST}"
        record = world.managed.get("own")
        assert (record.image, record.image_tag, record.digest) == (REGISTRY, "17.2", OWN_DIGEST)
        # The container is started from the digest, never from the tag a registry can move.
        assert f"{REGISTRY}@{OWN_DIGEST}" in world.host.images

    def test_a_registry_that_does_not_say_the_digest_leaves_nothing(self, world: World) -> None:
        with pytest.raises(Exception, match="digest"):
            world.managed.create(
                "own",
                "postgresql",
                version="17",
                memory="1g",
                image=f"{REGISTRY}:17.2",
                accept_untested=True,
            )

        assert world.managed.records.all() == []
        assert not layout.directory("own").exists()

    def test_a_pull_that_fails_leaves_nothing(self, world: World) -> None:
        world.host.fail[("docker", "pull")] = "manifest unknown"

        with pytest.raises(Exception, match="pull"):
            world.managed.create(
                "own",
                "postgresql",
                version="17",
                memory="1g",
                image=f"{REGISTRY}:17.2",
                accept_untested=True,
            )

        assert world.managed.records.all() == []

    def test_the_confirmation_is_asked_before_anything_is_pulled(self, world: World) -> None:
        with pytest.raises(ConfirmationRequired):
            world.managed.create(
                "own", "postgresql", version="17", memory="1g", image=f"{REGISTRY}:17.2"
            )

        assert not any(tuple(call[:2]) == ("docker", "pull") for call in world.host.calls)
