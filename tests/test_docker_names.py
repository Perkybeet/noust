# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust calls things in Docker is said in one place, and the places that used to say it use that.

The gate to Docker (``noust.core.docker``) decides whose a container, an image, a volume or a network
is from the names Noust gave them, and it is in ``core``, below the managers and the deployers that
write those names. The names are therefore defined in ``noust.core.docker.names`` and the modules
that wrote them re-use the one definition (rule 3): this holds each of those to it, so a name cannot
be changed in one place and still be the old one in the other.
"""

from __future__ import annotations

import pytest

from noust.core.docker import names
from noust.deployers.helpers import compose_retention, container_compose
from noust.managers.database import instance_layout


class TestTheNamesOfADatabaseInstance:
    def test_everything_of_an_instance_is_named_after_it(self) -> None:
        assert names.instance_unit("pg16") == "noust-db-pg16"
        assert names.instance_project("pg16") == "noust-db-pg16"
        assert names.instance_container("pg16") == "noust-db-pg16"
        assert names.instance_data_volume("pg16") == "noust-db-pg16-data"
        assert names.instance_config_volume("pg16") == "noust-db-pg16-config"

    def test_the_layout_of_an_instance_is_these_names(self) -> None:
        assert instance_layout.unit_name("pg16") == names.instance_unit("pg16")
        assert instance_layout.project_name("pg16") == names.instance_project("pg16")
        assert instance_layout.container_name("pg16") == names.instance_container("pg16")
        assert instance_layout.data_volume("pg16") == names.instance_data_volume("pg16")
        assert instance_layout.config_volume("pg16") == names.instance_config_volume("pg16")


class TestTheImagesAnApplicationKeeps:
    def test_a_container_application_keeps_one_image_per_release(self) -> None:
        assert names.image_repository("shop-example-com") == "noust-shop-example-com"
        assert (
            names.image_tag("shop-example-com", "20261112-101500-abc1234")
            == "noust-shop-example-com:20261112-101500-abc1234"
        )

    def test_the_release_of_a_tag_is_read_only_from_its_own_application(self) -> None:
        tag = "noust-shop-example-com:20261112-101500-abc1234"

        assert names.release_of_tag("shop-example-com", tag) == "20261112-101500-abc1234"
        assert names.release_of_tag("other-example-com", tag) is None
        assert names.release_of_tag("shop-example-com", "noust-shop-example-com:latest") is None
        assert (
            names.release_of_tag(
                "shop-example-com", "noust-shop-example-com:20261112-101500-abc1234\n"
            )
            is None
        )

    def test_a_stack_keeps_the_image_of_a_release_under_a_tag_of_the_release(self) -> None:
        assert names.release_tag("shop", "web", "20261112-101500-abc1234") == (
            "shop-web:noust-20261112-101500-abc1234"
        )
        assert names.repository_name("Shop.Example", "web app") == "shop.example-web-app"

    def test_the_tags_that_are_a_way_back_are_these(self) -> None:
        assert names.PREVIOUS_TAGS == (names.PREVIOUS_TAG, names.LEGACY_PREVIOUS_TAG)
        assert names.PREVIOUS_TAG == "noust_previous"
        assert names.TAG_PREFIX == "noust-"

    @pytest.mark.parametrize(
        ("reference", "way_back"),
        [
            ("shop-web:noust-20261112-101500-abc1234", True),
            ("shop-web:noust_previous", True),
            ("noust-shop-example-com:20261112-101500-abc1234", True),
            ("nginx:1.27", False),
            ("shop-web:latest", False),
            ("noust-db-pg16:latest", False),
            ("postgres@sha256:" + "a" * 64, False),
        ],
    )
    def test_an_image_is_a_way_back_by_its_tag(self, reference: str, way_back: bool) -> None:
        assert names.is_way_back(reference) is way_back


class TestTheNameOfAComposeProject:
    @pytest.mark.parametrize(
        ("directory", "project"),
        [
            ("shop-example-com", "shop-example-com"),
            ("Shop.Example.COM", "shopexamplecom"),
            ("__shop", "shop"),
            ("-9lives", "9lives"),
            ("...", ""),
        ],
    )
    def test_compose_names_it_after_its_directory(self, directory: str, project: str) -> None:
        assert names.project_of_directory(directory) == project


def test_the_modules_that_wrote_the_names_use_these() -> None:
    assert compose_retention.PREVIOUS_TAGS is names.PREVIOUS_TAGS
    assert compose_retention.release_tag is names.release_tag
    assert compose_retention.repository_name is names.repository_name
    assert container_compose.image_tag is names.image_tag
    assert container_compose.image_repository is names.image_repository
    assert container_compose.release_of_tag is names.release_of_tag
