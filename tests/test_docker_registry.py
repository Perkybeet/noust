# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking a registry what a tag is now, and what a local image says of itself.

The question is the one ``docker pull`` asks, put without taking the layers: the manifest's digest, and
the config of the image behind it for its version and its date. Held here: what is asked is built from a
reference that was read and never from the text it came in; a Docker that cannot ask without downloading
says so and downloads only when it is told to; a refusal of the registry is told as a refusal and not as a
network failure; and the account the registry is asked with reaches the command through its environment.
"""

from __future__ import annotations

import json

import pytest

from noust.core.docker import registry
from noust.core.docker.versions import parse_reference
from tests.support.docker_machine import DockerMachine, RemoteTag

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def ref(text: str):  # type: ignore[no-untyped-def]
    found = parse_reference(text)
    assert found is not None
    return found


class TestWhatALocalImageSaysOfItself:
    def test_the_digest_its_registry_knows_it_by_is_the_one_of_its_own_repository(self) -> None:
        machine = DockerMachine()
        machine.facts(
            "sha256:" + "1" * 64, repo_digests=[f"redis@{DIGEST_A}", f"other/redis@{DIGEST_B}"]
        )

        found = registry.read_local_images(machine, ["sha256:" + "1" * 64])

        image = found["sha256:" + "1" * 64]
        assert image.digest_for(ref("redis:7-alpine")) == DIGEST_A
        assert image.digest_for(ref("other/redis:7")) == DIGEST_B
        assert image.digest_for(ref("ghcr.io/acme/redis:7")) is None

    def test_an_image_that_came_from_no_registry_has_no_digest(self) -> None:
        machine = DockerMachine()
        machine.facts("sha256:" + "2" * 64, repo_digests=[])

        image = registry.read_local_images(machine, ["sha256:" + "2" * 64])["sha256:" + "2" * 64]

        assert image.digest_for(ref("shop-web:noust-1")) is None

    def test_the_environment_and_the_labels_come_with_it(self) -> None:
        machine = DockerMachine()
        machine.facts(
            "sha256:" + "3" * 64,
            env=["PG_VERSION=16.4"],
            labels={"org.opencontainers.image.version": "16.4"},
            created="2026-10-01T10:00:00Z",
        )

        image = registry.read_local_images(machine, ["sha256:" + "3" * 64])["sha256:" + "3" * 64]

        assert image.env == ("PG_VERSION=16.4",)
        assert image.labels["org.opencontainers.image.version"] == "16.4"
        assert image.created == "2026-10-01T10:00:00Z"
        assert image.platform == "linux/amd64"

    def test_only_what_looks_like_an_id_is_asked(self) -> None:
        machine = DockerMachine()

        found = registry.read_local_images(machine, ["--format", "x; rm -rf /", "../etc"])

        assert found == {}
        assert machine.calls == []

    def test_nobody_to_ask_about_asks_nothing(self) -> None:
        machine = DockerMachine()

        assert registry.read_local_images(machine, []) == {}
        assert machine.calls == []


class TestAskingTheRegistry:
    def machine(self, **tags: RemoteTag) -> DockerMachine:
        machine = DockerMachine()
        for name, tag in tags.items():
            machine.remote[name.replace("__", "/").replace("_", ":")] = tag
        return machine

    def test_the_digest_of_the_tag_is_read_without_downloading_anything(self) -> None:
        machine = DockerMachine()
        machine.remote["docker.io/library/redis:7-alpine"] = RemoteTag(
            DIGEST_B, created="2026-11-09T10:00:00Z", env=["REDIS_VERSION=7.4.2"]
        )

        seen = registry.look_at_registry(machine, ref("redis:7-alpine"))

        assert seen.ok
        assert seen.digest == DIGEST_B
        assert seen.method == "imagetools"
        assert seen.created == "2026-11-09T10:00:00Z"
        assert seen.env == ("REDIS_VERSION=7.4.2",)
        pulled = [call for call in machine.calls if call[:2] == ("docker", "pull")]
        assert pulled == []
        asked = [call for call in machine.calls if call[:3] == ("docker", "buildx", "imagetools")]
        assert asked[0][-1] == "docker.io/library/redis:7-alpine"

    def test_the_image_of_a_multi_platform_tag_is_the_one_of_this_machine(self) -> None:
        machine = DockerMachine()
        machine.remote["ghcr.io/acme/api:2"] = RemoteTag(
            DIGEST_B,
            created="2026-11-09T10:00:00Z",
            env=["API_VERSION=2.3.1"],
            platforms=["linux/arm64", "linux/amd64"],
        )

        seen = registry.look_at_registry(machine, ref("ghcr.io/acme/api:2"), platform="linux/amd64")

        assert seen.ok
        assert seen.env == ("API_VERSION=2.3.1",)

    def test_a_tag_whose_config_cannot_be_read_still_has_its_digest(self) -> None:
        machine = DockerMachine()
        machine.remote["docker.io/library/redis:7"] = RemoteTag(DIGEST_B, config_readable=False)

        seen = registry.look_at_registry(machine, ref("redis:7"))

        assert seen.ok
        assert seen.digest == DIGEST_B
        assert seen.created is None
        assert seen.env == ()

    def test_a_docker_without_buildx_cannot_ask_and_says_so(self) -> None:
        machine = DockerMachine()
        machine.buildx = False

        seen = registry.look_at_registry(machine, ref("redis:7"))

        assert not seen.ok
        assert seen.why == "unsupported"
        assert "download" in seen.detail

    def test_a_registry_that_refuses_is_a_refusal_and_not_a_network_failure(self) -> None:
        machine = DockerMachine()
        machine.denied.add("ghcr.io/acme/private:1")

        seen = registry.look_at_registry(machine, ref("ghcr.io/acme/private:1"))

        assert not seen.ok
        assert seen.why == "denied"
        assert "unauthorized" in seen.detail

    def test_a_tag_the_registry_does_not_have_is_not_found(self) -> None:
        machine = DockerMachine()

        seen = registry.look_at_registry(machine, ref("acme/gone:1"))

        assert not seen.ok
        assert seen.why == "not_found"

    def test_a_registry_that_does_not_answer_says_what_docker_said(self) -> None:
        machine = DockerMachine()
        machine.script(
            ["docker", "buildx", "imagetools", "inspect"],
            exit_code=1,
            stderr="ERROR: failed to do request: dial tcp: lookup registry.example.com: no such host",
        )

        seen = registry.look_at_registry(machine, ref("registry.example.com/acme/api:1"))

        assert not seen.ok
        assert seen.why == "unreachable"
        assert "no such host" in seen.detail

    def test_the_account_reaches_the_command_through_its_environment(self) -> None:
        machine = DockerMachine()
        machine.remote["ghcr.io/acme/api:2"] = RemoteTag(DIGEST_B)

        registry.look_at_registry(
            machine, ref("ghcr.io/acme/api:2"), env={"DOCKER_CONFIG": "/run/noust/session-1"}
        )

        used = [
            env
            for call, env in zip(machine.calls, machine.envs, strict=True)
            if call[:3] == ("docker", "buildx", "imagetools")
        ]
        assert used
        assert all(env == {"DOCKER_CONFIG": "/run/noust/session-1"} for env in used)

    def test_a_pinned_reference_is_never_asked(self) -> None:
        machine = DockerMachine()

        seen = registry.look_at_registry(machine, ref(f"redis@{DIGEST_A}"))

        assert seen.ok
        assert seen.digest == DIGEST_A
        assert machine.calls == []


class TestDownloadingToCompare:
    def test_pulling_is_what_a_docker_without_buildx_is_asked_to_do_on_request(self) -> None:
        machine = DockerMachine()
        machine.buildx = False
        machine.remote["docker.io/library/redis:7"] = RemoteTag(
            DIGEST_B, created="2026-11-09T10:00:00Z", env=["REDIS_VERSION=7.4.2"]
        )

        seen = registry.look_at_registry(machine, ref("redis:7"), pull=True)

        assert seen.ok
        assert seen.method == "pull"
        assert seen.digest == DIGEST_B
        assert seen.env == ("REDIS_VERSION=7.4.2",)
        assert ("docker", "pull", "docker.io/library/redis:7") in machine.calls

    def test_a_pull_the_registry_refuses_says_why(self) -> None:
        machine = DockerMachine()
        machine.buildx = False
        machine.denied.add("docker.io/library/redis:7")

        seen = registry.look_at_registry(machine, ref("redis:7"), pull=True)

        assert not seen.ok
        assert seen.why == "denied"

    def test_without_being_told_nothing_is_downloaded_even_when_buildx_is_missing(self) -> None:
        machine = DockerMachine()
        machine.buildx = False

        registry.look_at_registry(machine, ref("redis:7"))

        assert not [call for call in machine.calls if call[:2] == ("docker", "pull")]


@pytest.mark.parametrize(
    "text",
    ["-x.example.com/app:1", "--format/app", "a b", ""],
)
def test_what_is_not_a_reference_is_not_a_command(text: str) -> None:
    assert parse_reference(text) is None


def test_the_probes_the_module_declares_only_look() -> None:
    from noust.core.runner import is_read_only

    assert is_read_only(("docker", "buildx", "imagetools", "inspect", "docker.io/library/redis:7"))
    assert is_read_only(
        (
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "--format",
            "{{json .Image}}",
            "docker.io/library/redis:7",
        )
    )
    assert is_read_only(
        (
            "docker",
            "inspect",
            "--type",
            "image",
            "--format",
            registry.IMAGE_FORMAT,
            "sha256:" + "1" * 64,
        )
    )
    assert not is_read_only(("docker", "pull", "docker.io/library/redis:7"))


def test_a_config_is_read_from_a_flat_object_or_from_a_map_of_platforms() -> None:
    flat = json.dumps(
        {"created": "2026-11-09T10:00:00Z", "config": {"Env": ["A=1"], "Labels": {"k": "v"}}}
    )
    mapped = json.dumps(
        {
            "linux/arm64": {"created": "x", "config": {"Env": ["B=2"]}},
            "linux/amd64": {"created": "y", "config": {"Env": ["A=1"]}},
        }
    )

    assert registry.read_config(flat, "linux/amd64") == (
        "2026-11-09T10:00:00Z",
        ("A=1",),
        {"k": "v"},
    )
    assert registry.read_config(mapped, "linux/amd64") == ("y", ("A=1",), {})
    assert registry.read_config("not json", "linux/amd64") is None
    assert registry.read_config("[]", "linux/amd64") is None
