# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a tag says of a version, and what a moved tag is to the one that runs.

The rules held here are the ones the images page and the update plan stand on: a reference is read the
way Docker reads it (a registry, a repository, a tag, a digest); a tag that carries a major version
cannot jump to another major by moving, and one that carries none (``latest``) can; and the version of
what runs is read from its own labels and environment before it is guessed from the tag. None of it
asks anything: the registry is another module.
"""

from __future__ import annotations

import pytest

from noust.core.docker import versions
from noust.core.docker.versions import ImageRef, classify, parse_reference, parse_version, tag_shape


class TestReadingAReference:
    @pytest.mark.parametrize(
        ("text", "registry", "repository", "tag", "digest"),
        [
            ("redis", "docker.io", "library/redis", "latest", ""),
            ("redis:7-alpine", "docker.io", "library/redis", "7-alpine", ""),
            ("louislam/uptime-kuma:1", "docker.io", "louislam/uptime-kuma", "1", ""),
            ("ghcr.io/acme/api:2.3.1", "ghcr.io", "acme/api", "2.3.1", ""),
            (
                "registry.example.com:5000/team/app:v4",
                "registry.example.com:5000",
                "team/app",
                "v4",
                "",
            ),
            ("docker.io/library/postgres:16", "docker.io", "library/postgres", "16", ""),
            ("localhost/app", "localhost", "app", "latest", ""),
            ("redis@sha256:" + "a" * 64, "docker.io", "library/redis", "", "sha256:" + "a" * 64),
            (
                "ghcr.io/acme/api:2@sha256:" + "b" * 64,
                "ghcr.io",
                "acme/api",
                "2",
                "sha256:" + "b" * 64,
            ),
        ],
    )
    def test_a_reference_is_split_the_way_docker_splits_it(
        self, text: str, registry: str, repository: str, tag: str, digest: str
    ) -> None:
        assert parse_reference(text) == ImageRef(registry, repository, tag, digest)

    @pytest.mark.parametrize(
        "text",
        ["", "  ", "sha256:" + "a" * 64, "a" * 12, "UPPER/case:1", "has space:1", "-x:1", "x:y:z"],
    )
    def test_what_is_not_a_reference_is_none(self, text: str) -> None:
        assert parse_reference(text) is None

    def test_a_reference_is_named_with_the_tag_docker_gives_one_that_has_none(self) -> None:
        assert versions.with_default_tag("redis") == "redis:latest"
        assert versions.with_default_tag("redis:7") == "redis:7"
        assert versions.with_default_tag("ghcr.io/a/b") == "ghcr.io/a/b:latest"
        assert versions.with_default_tag("registry.example.com:5000/a") == (
            "registry.example.com:5000/a:latest"
        )
        assert versions.with_default_tag("redis@sha256:" + "a" * 64) == "redis@sha256:" + "a" * 64

    def test_a_pinned_reference_is_one_with_a_digest(self) -> None:
        assert parse_reference("redis@sha256:" + "a" * 64).pinned  # type: ignore[union-attr]
        assert not parse_reference("redis:7").pinned  # type: ignore[union-attr]

    def test_the_registry_of_a_reference_names_the_account_to_ask_with(self) -> None:
        assert parse_reference("redis").registry == "docker.io"  # type: ignore[union-attr]
        assert versions.registry_aliases("docker.io") >= {"docker.io", "index.docker.io"}
        assert versions.registry_aliases("ghcr.io") == frozenset({"ghcr.io"})


class TestWhatATagSaysOfAVersion:
    @pytest.mark.parametrize(
        ("tag", "numbers", "suffix", "floating"),
        [
            ("7-alpine", (7,), "-alpine", False),
            ("29-apache", (29,), "-apache", False),
            ("7.4.2-alpine", (7, 4, 2), "-alpine", False),
            ("17.2", (17, 2), "", False),
            ("v4.8.4", (4, 8, 4), "", False),
            ("1", (1,), "", False),
            ("2026.11.1", (2026, 11, 1), "", False),
            ("latest", (), "", True),
            ("stable", (), "", True),
            ("alpine", (), "", True),
            ("bookworm", (), "", True),
            ("", (), "", True),
        ],
    )
    def test_the_numbers_a_tag_carries(
        self, tag: str, numbers: tuple[int, ...], suffix: str, floating: bool
    ) -> None:
        shape = tag_shape(tag)

        assert (shape.numbers, shape.suffix, shape.floating) == (numbers, suffix, floating)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("17.2", (17, 2)),
            ("7.4.2-alpine", (7, 4, 2)),
            ("v1.23.16", (1, 23, 16)),
            ("16", (16,)),
            ("16.4 (Debian 16.4-1)", (16, 4)),
            ("latest", None),
            ("", None),
            ("1.2.3.4.5", (1, 2, 3, 4)),
        ],
    )
    def test_a_version_is_read_from_the_front_of_a_text(
        self, text: str, expected: tuple[int, ...] | None
    ) -> None:
        assert parse_version(text) == expected


class TestWhatARunningImageSaysOfItself:
    def test_the_label_of_the_image_says_what_version_it_is(self) -> None:
        found = versions.version_of_image(
            "louislam/uptime-kuma",
            tag="1",
            labels={"org.opencontainers.image.version": "1.23.11"},
            env=[],
        )

        assert found == (1, 23, 11)

    def test_a_version_in_the_environment_is_read_by_the_name_of_the_repository(self) -> None:
        env = ["PATH=/usr/bin", "PG_MAJOR=16", "PG_VERSION=16.4-1.pgdg120+1", "LANG=en_US.utf8"]

        assert versions.version_of_image("library/postgres", tag="latest", labels={}, env=env) == (
            16,
            4,
        )

    @pytest.mark.parametrize(
        ("repository", "variable", "value", "expected"),
        [
            ("library/redis", "REDIS_VERSION", "7.4.1", (7, 4, 1)),
            ("library/nextcloud", "NEXTCLOUD_VERSION", "29.0.9", (29, 0, 9)),
            # A Debian epoch ("1:") is not part of the version.
            ("library/mariadb", "MARIADB_VERSION", "1:11.5.2+maria~ubu2404", (11, 5, 2)),
            ("library/mysql", "MYSQL_VERSION", "8.4.3-1.el9", (8, 4, 3)),
            ("library/mongo", "MONGO_VERSION", "7.0.14", (7, 0, 14)),
        ],
    )
    def test_the_variables_the_official_images_set(
        self, repository: str, variable: str, value: str, expected: tuple[int, ...] | None
    ) -> None:
        found = versions.version_of_image(
            repository, tag="latest", labels={}, env=[f"{variable}={value}"]
        )

        assert found == expected

    def test_a_variable_of_another_program_is_not_the_version_of_this_one(self) -> None:
        # The image of a web app that bundles node says its node's version, not its own.
        found = versions.version_of_image(
            "acme/shop",
            tag="latest",
            labels={},
            env=["NODE_VERSION=20.11.0", "YARN_VERSION=1.22.19"],
        )

        assert found is None

    def test_a_tag_with_a_number_is_the_last_word_when_the_image_says_nothing(self) -> None:
        assert versions.version_of_image("library/redis", tag="7-alpine", labels={}, env=[]) == (7,)

    def test_a_floating_tag_and_a_silent_image_say_no_version(self) -> None:
        assert versions.version_of_image("library/redis", tag="latest", labels={}, env=[]) is None

    def test_the_label_wins_over_the_environment(self) -> None:
        found = versions.version_of_image(
            "library/redis",
            tag="7",
            labels={"org.opencontainers.image.version": "7.2.5"},
            env=["REDIS_VERSION=7.0.0"],
        )

        assert found == (7, 2, 5)


class TestWhatAMovedTagIsToTheOneThatRuns:
    @pytest.mark.parametrize(
        ("current", "latest", "expected"),
        [
            ((16, 4), (17, 2), "major"),
            ((7, 4, 1), (7, 4, 2), "patch"),
            ((7, 4, 1), (7, 5, 0), "minor"),
            ((29, 0, 9), (29, 0, 11), "patch"),
            ((1, 23, 11), (1, 23, 16), "patch"),
            ((7,), (7, 4, 2), "minor"),
            ((8,), (7, 9), "major"),
            ((17, 2), (17, 2), "patch"),
        ],
    )
    def test_two_versions_are_compared_by_where_they_first_differ(
        self, current: tuple[int, ...], latest: tuple[int, ...], expected: str
    ) -> None:
        assert classify(current, latest, tag_shape("latest")) == expected

    def test_a_tag_that_pins_the_major_cannot_become_another_major_by_moving(self) -> None:
        # postgres:16 is 16.x tomorrow whatever the registry says about its newest.
        assert classify(None, None, tag_shape("16")) == "minor"
        assert classify(None, None, tag_shape("7-alpine")) == "minor"

    def test_a_tag_that_pins_the_minor_moves_by_a_patch(self) -> None:
        assert classify(None, None, tag_shape("16.4")) == "patch"
        assert classify(None, None, tag_shape("1.27.3")) == "patch"

    def test_a_floating_tag_with_no_versions_to_compare_cannot_say(self) -> None:
        assert classify(None, None, tag_shape("latest")) == "unknown"
        assert classify((16, 4), None, tag_shape("latest")) == "unknown"
        assert classify(None, (17, 2), tag_shape("latest")) == "unknown"

    def test_the_major_a_pinned_tag_says_is_the_one_that_decides_even_without_the_images_words(
        self,
    ) -> None:
        assert classify((16, 4), (16, 6), tag_shape("16")) == "minor"
        assert classify(None, (17, 2), tag_shape("16")) == "minor"


class TestFloatingTags:
    @pytest.mark.parametrize("tag", ["latest", "stable", "alpine", "lts", "main", ""])
    def test_a_tag_with_no_number_is_one_that_moves(self, tag: str) -> None:
        assert tag_shape(tag).floating

    @pytest.mark.parametrize("tag", ["1", "7-alpine", "17.2", "v4.8.4", "2026.11.1"])
    def test_a_tag_with_a_number_is_pinned_to_a_line(self, tag: str) -> None:
        assert not tag_shape(tag).floating
