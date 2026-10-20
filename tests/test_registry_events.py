# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading what a container registry says was pushed (checklist A-14).

GitHub Container Registry's ``registry_package`` event and Docker Hub's payload, two spellings of
the same image compared as one, and the token of the address Docker Hub is given.
"""

from __future__ import annotations

import pytest

from noust.core.registry_events import (
    RegistryEvent,
    parse_dockerhub,
    parse_ghcr,
    registry_hook_token,
    token_matches,
    why_not_for,
)
from noust.validators.container import split_reference

DIGEST = "sha256:" + "a" * 64


class TestTakingAReferenceApart:
    @pytest.mark.parametrize(
        ("reference", "expected"),
        [
            ("nginx", ("docker.io", "library/nginx", "latest", None)),
            ("nginx:1.27", ("docker.io", "library/nginx", "1.27", None)),
            ("library/nginx", ("docker.io", "library/nginx", "latest", None)),
            ("owner/app:2", ("docker.io", "owner/app", "2", None)),
            ("docker.io/owner/app", ("docker.io", "owner/app", "latest", None)),
            ("index.docker.io/library/nginx:1", ("docker.io", "library/nginx", "1", None)),
            ("ghcr.io/owner/app:1.4", ("ghcr.io", "owner/app", "1.4", None)),
            ("ghcr.io/owner/app", ("ghcr.io", "owner/app", "latest", None)),
            ("localhost:5000/app:dev", ("localhost:5000", "app", "dev", None)),
            ("ghcr.io/o/a@" + DIGEST, ("ghcr.io", "o/a", None, DIGEST)),
            ("ghcr.io/o/a:3@" + DIGEST, ("ghcr.io", "o/a", "3", DIGEST)),
        ],
    )
    def test_two_spellings_of_one_image_are_one(
        self, reference: str, expected: tuple[str, str, str | None, str | None]
    ) -> None:
        assert split_reference(reference) == expected


GHCR = {
    "action": "published",
    "registry_package": {
        "package_type": "CONTAINER",
        "name": "app",
        "package_version": {
            "version": DIGEST,
            "package_url": "ghcr.io/owner/app:1.4",
            "container_metadata": {"tag": {"name": "1.4", "digest": DIGEST}},
        },
    },
}


class TestGitHubContainerRegistry:
    def test_a_published_version_is_an_image_that_was_pushed(self) -> None:
        event = parse_ghcr(GHCR)

        assert event == RegistryEvent("ghcr", "ghcr.io", "owner/app", "1.4", DIGEST)
        assert event is not None and event.reference == "ghcr.io/owner/app:1.4"

    def test_the_name_comes_back_lower_case_whatever_the_package_was_called(self) -> None:
        payload = {
            **GHCR,
            "registry_package": {
                **GHCR["registry_package"],
                "package_version": {
                    "package_url": "ghcr.io/Owner/App:Stable",
                    "container_metadata": {"tag": {"name": "Stable"}},
                },
            },
        }

        event = parse_ghcr(payload)

        assert event is not None and (event.repository, event.tag) == ("owner/app", "stable")

    def test_the_tag_comes_from_the_address_when_the_metadata_has_none(self) -> None:
        payload = {
            **GHCR,
            "registry_package": {
                **GHCR["registry_package"],
                "package_version": {"package_url": "ghcr.io/owner/app:2.0"},
            },
        }

        event = parse_ghcr(payload)

        assert event is not None and event.tag == "2.0" and event.digest is None

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {**GHCR, "action": "deleted"},
            {**GHCR, "registry_package": {**GHCR["registry_package"], "package_type": "npm"}},
            {**GHCR, "registry_package": {"package_type": "CONTAINER"}},
            {
                **GHCR,
                "registry_package": {
                    "package_type": "CONTAINER",
                    "package_version": {"package_url": "ghcr.io/owner/app"},
                },
            },
        ],
    )
    def test_anything_that_is_not_a_tagged_image_published_is_nothing(
        self, payload: dict[str, object]
    ) -> None:
        assert parse_ghcr(payload) is None


class TestDockerHub:
    def test_a_push_names_a_repository_and_a_tag(self) -> None:
        event = parse_dockerhub(
            {"push_data": {"tag": "latest"}, "repository": {"repo_name": "owner/app"}}
        )

        assert event == RegistryEvent("dockerhub", "docker.io", "owner/app", "latest")

    def test_an_official_image_is_in_library(self) -> None:
        event = parse_dockerhub({"push_data": {"tag": "1"}, "repository": {"repo_name": "nginx"}})

        assert event is not None and event.repository == "library/nginx"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"push_data": {"tag": "x"}},
            {"repository": {"repo_name": "o/a"}},
            {"push_data": {}, "repository": {"repo_name": "o/a"}},
        ],
    )
    def test_a_payload_without_an_image_is_nothing(self, payload: dict[str, object]) -> None:
        assert parse_dockerhub(payload) is None


class TestWhetherItIsTheApplicationsImage:
    event = RegistryEvent("ghcr", "ghcr.io", "owner/app", "1.4")

    def test_the_same_image_at_the_same_tag_is(self) -> None:
        assert why_not_for("ghcr.io/owner/app:1.4", self.event) is None

    def test_a_tag_left_out_is_latest(self) -> None:
        assert (
            why_not_for(
                "ghcr.io/owner/app", RegistryEvent("ghcr", "ghcr.io", "owner/app", "latest")
            )
            is None
        )

    def test_another_tag_is_not(self) -> None:
        reason = why_not_for("ghcr.io/owner/app:1.3", self.event)

        assert reason is not None and "1.3" in reason and "1.4" in reason

    def test_another_image_is_not(self) -> None:
        assert why_not_for("ghcr.io/owner/other:1.4", self.event) is not None

    def test_another_registry_is_not(self) -> None:
        assert why_not_for("docker.io/owner/app:1.4", self.event) is not None

    def test_an_image_pinned_by_digest_never_follows_anything(self) -> None:
        reason = why_not_for("ghcr.io/owner/app@" + DIGEST, self.event)

        assert reason is not None and "digest" in reason


class TestTheToken:
    def test_it_is_made_from_the_secret_and_the_domain_and_says_nothing_of_the_secret(self) -> None:
        token = registry_hook_token("s3cret", "shop.example.com")

        assert len(token) == 40 and "s3cret" not in token
        assert token == registry_hook_token("s3cret", "shop.example.com")
        assert token != registry_hook_token("s3cret", "other.example.com")
        assert token != registry_hook_token("rotated", "shop.example.com")

    def test_only_the_right_one_matches(self) -> None:
        token = registry_hook_token("s3cret", "shop.example.com")

        assert token_matches("s3cret", "shop.example.com", token)
        assert token_matches("s3cret", "shop.example.com", token.upper())
        assert not token_matches("s3cret", "shop.example.com", token[:-1] + "0")
        assert not token_matches("s3cret", "shop.example.com", "")
