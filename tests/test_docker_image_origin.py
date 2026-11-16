# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an image comes from, so that "nobody looked" is not "built here" (item 189).

An image the registries were never asked about and one that came from no registry both have no reference to
look at, and a page that drew them the same said the second was waiting for a look it would never have. The
origin is a closed list: ``registry`` (it has a digest a registry knows it by), ``built_noust`` (Noust named
it: an application's release, or the version a stack keeps to go back to), ``built_other`` (it has a name and
no registry behind it: somebody built it here) and ``unknown`` (Docker did not say). It is decided by the same
fact the look for new versions skips an image by, and an image that was not looked at keeps the state
``not_looked``, whatever its origin.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker import image_origin
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import Image
from noust.core.docker.owners import AppRecord, Registry
from noust.core.docker.registry import LocalImage
from noust.core.store import App, NoustStore
from noust.managers.docker_image_versions import ImageVersions
from noust.managers.docker_updates import ImageUpdates
from tests.support.docker_machine import SHOP, DockerMachine

DIGEST = "sha256:" + "d" * 64
RELEASE = "20261112-101500-abc1234"


def image(*tags: str, way_back: bool = False) -> Image:
    return Image(id="sha256:" + "1" * 64, tags=tags, way_back=way_back)


def local(*digests: str) -> LocalImage:
    return LocalImage(id="sha256:" + "1" * 64, digests=digests)


class TestWhereAnImageComesFrom:
    def test_an_image_a_registry_has_a_digest_for_came_from_it(self) -> None:
        assert (
            image_origin.origin_of(image("nginx:1.27"), local(f"nginx@{DIGEST}"))
            == image_origin.REGISTRY
        )

    def test_an_image_of_another_registry_is_one_too(self) -> None:
        found = image_origin.origin_of(
            image("ghcr.io/acme/shop:2"), local(f"ghcr.io/acme/shop@{DIGEST}")
        )

        assert found == image_origin.REGISTRY

    def test_an_image_with_a_name_and_no_registry_behind_it_was_built_here_by_somebody(
        self,
    ) -> None:
        assert image_origin.origin_of(image("shop-web:dev"), local()) == image_origin.BUILT_OTHER

    def test_a_name_that_is_not_the_one_the_registry_knows_it_by_is_not_the_registrys(
        self,
    ) -> None:
        # Pulled as nginx and tagged as the company's own: asking the registry for ``acme/nginx`` finds
        # nothing, which is what the look skips it for.
        found = image_origin.origin_of(image("acme/nginx:custom"), local(f"nginx@{DIGEST}"))

        assert found == image_origin.BUILT_OTHER

    def test_a_release_noust_named_is_noust_built_whatever_docker_says_of_it(self) -> None:
        for tags in (
            (f"noust-shop:{RELEASE}",),
            (f"shop-example-com-web:noust-{RELEASE}",),
            ("shop-example-com-web:noust_previous",),
        ):
            assert image_origin.origin_of(image(*tags), None) == image_origin.BUILT_NOUST, tags

    def test_an_image_an_application_keeps_to_go_back_to_is_noust_built(self) -> None:
        assert (
            image_origin.origin_of(image("odd:name", way_back=True), local())
            == image_origin.BUILT_NOUST
        )

    def test_an_image_docker_did_not_describe_is_not_guessed_at(self) -> None:
        assert image_origin.origin_of(image("nginx:1.27"), None) == image_origin.UNKNOWN

    def test_an_image_with_no_name_and_no_digest_is_unknown(self) -> None:
        assert image_origin.origin_of(image(), local()) == image_origin.UNKNOWN

    def test_an_image_whose_name_moved_on_still_has_the_digest_of_its_registry(self) -> None:
        assert image_origin.origin_of(image(), local(f"nginx@{DIGEST}")) == image_origin.REGISTRY

    def test_the_list_is_closed(self) -> None:
        assert image_origin.SOURCES == ("registry", "built_noust", "built_other", "unknown")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_type="docker-compose", app_path=SHOP))
    yield instance
    NoustStore.reset_instance()


def machine() -> DockerMachine:
    box = DockerMachine()
    pulled = box.image("1", "nginx", "1.27")
    box.facts(pulled, repo_digests=[f"nginx@{DIGEST}"])
    made = box.image("2", "shop-web", "dev")
    box.facts(made)
    kept = box.image("3", "shop-example-com-web", f"noust-{RELEASE}")
    box.facts(kept)
    box.image("4", "<none>", "<none>")
    for name, ident in (("web", "1"), ("tool", "2")):
        box.box(
            name,
            image="nginx:1.27" if ident == "1" else "shop-web:dev",
            working_dir="",
            policy="unless-stopped",
        )
    return box


def updates(box: DockerMachine, store: NoustStore) -> ImageUpdates:
    gate = DockerGate(
        box,
        store=store,
        registry=lambda: Registry(apps=(AppRecord("shop.example.com", SHOP),)),
        disk=lambda path: None,
    )
    return ImageUpdates(gate, versions=ImageVersions(gate, store=store, runner=box), store=store)


class TestInThePlanOfEveryImage:
    def plans(self, box: DockerMachine, store: NoustStore) -> dict[str, object]:
        listed = updates(box, store)._gate.images().items
        found = updates(box, store).plans(listed)
        return {(i.tags[0] if i.tags else "untagged"): found[i.id] for i in listed}

    def test_each_plan_says_where_its_image_comes_from(self, store: NoustStore) -> None:
        plans = self.plans(machine(), store)

        assert plans["nginx:1.27"].source == "registry"  # type: ignore[attr-defined]
        assert plans["shop-web:dev"].source == "built_other"  # type: ignore[attr-defined]
        assert plans[f"shop-example-com-web:noust-{RELEASE}"].source == "built_noust"  # type: ignore[attr-defined]
        assert plans["untagged"].source == "unknown"  # type: ignore[attr-defined]

    def test_what_nobody_looked_at_is_not_looked_at_whatever_it_is(self, store: NoustStore) -> None:
        plans = self.plans(machine(), store)

        assert plans["nginx:1.27"].state == "not_looked"  # type: ignore[attr-defined]
        assert plans["shop-web:dev"].state == "not_looked"  # type: ignore[attr-defined]

    def test_the_images_of_a_page_are_described_by_docker_in_one_process(
        self, store: NoustStore
    ) -> None:
        box = machine()

        self.plans(box, store)

        described = [c for c in box.calls if c[:4] == ("docker", "inspect", "--type", "image")]
        assert len(described) == 1

    def test_the_plan_of_one_image_says_it_too(self, store: NoustStore) -> None:
        plan = updates(machine(), store).plan_for("nginx:1.27")

        assert plan.source == "registry"
        assert plan.to_dict()["source"] == "registry"
