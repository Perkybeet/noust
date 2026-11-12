# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking at the registries for a new version of every image a container runs.

Held here: what is asked is what runs (one question per image however many containers share it); what
the registry answers is compared with the digest the running image came with and not with a local tag;
what is a new version says what kind (a patch, a minor, a major) and whether the tag is one that moves;
a database whose new version cannot be read is treated as a major one; what has no registry (a build on
this machine, a digest pin) is left out and says why; an account Noust keeps for the registry is used
for it and for no other; and nothing is downloaded unless the operator asked for it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import Registry
from noust.core.docker_image_checks import ImageVersionStore
from noust.core.fs import RealFileSystem
from noust.core.registry_store import RegistryStore
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.docker_image_versions import ImageVersions
from noust.managers.registry_logins import RegistryManager
from tests.support.docker_machine import DockerMachine, RemoteTag

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
NOW = "2026-11-12T10:00:00+00:00"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield NoustStore(tmp_path / "noust.db")
    NoustStore.reset_instance()


def run_with(
    machine: DockerMachine, store: NoustStore, tmp_path: Path, **options: object
) -> ImageVersions:
    registries = RegistryManager(
        store=RegistryStore(store),
        secrets=SecretStore(root=tmp_path / "secrets", fs=RealFileSystem()),
        runner=machine,
        fs=RealFileSystem(),
    )
    gate = DockerGate(machine, store=store, registry=lambda: Registry(), disk=lambda p: None)
    return ImageVersions(
        gate,
        store=store,
        runner=machine,
        registries=registries,
        clock=lambda: NOW,
        **options,  # type: ignore[arg-type]
    )


def running(machine: DockerMachine, name: str, image: str, digit: str, **facts: object) -> str:
    """A container of an image the host has, as ``docker pull`` left it."""
    repository, _, tag = image.partition(":")
    image_id = machine.image(digit, repository, tag or "latest")
    machine.box(name, image=image, policy="unless-stopped")
    machine.facts(image_id, **facts)
    return image_id


def checked(versions: ImageVersions, **kwargs: object) -> dict[str, object]:
    run = versions.check(**kwargs)  # type: ignore[arg-type]
    return {record.reference: record for record in run.records}


def test_a_new_patch_is_found_with_what_it_is_to_the_one_that_runs(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(
        machine,
        "cache",
        "redis:7-alpine",
        "1",
        repo_digests=[f"redis@{A}"],
        env=["REDIS_VERSION=7.4.1"],
    )
    machine.remote["docker.io/library/redis:7-alpine"] = RemoteTag(
        B, created="2026-11-09T10:00:00Z", env=["REDIS_VERSION=7.4.2"]
    )

    found = checked(run_with(machine, store, tmp_path))["redis:7-alpine"]

    assert (found.state, found.change, found.floating) == ("newer", "patch", False)  # type: ignore[attr-defined]
    assert (found.current_version, found.latest_version) == ("7.4.1", "7.4.2")  # type: ignore[attr-defined]
    assert found.latest_created == "2026-11-09T10:00:00Z"  # type: ignore[attr-defined]
    assert (found.local_digest, found.remote_digest) == (A, B)  # type: ignore[attr-defined]
    assert found.method == "imagetools"  # type: ignore[attr-defined]


def test_a_tag_that_still_points_at_what_runs_is_current(store: NoustStore, tmp_path: Path) -> None:
    machine = DockerMachine()
    running(machine, "front", "nginx:1.27", "2", repo_digests=[f"nginx@{A}"])
    machine.remote["docker.io/library/nginx:1.27"] = RemoteTag(A)

    found = checked(run_with(machine, store, tmp_path))["nginx:1.27"]

    assert (found.state, found.change) == ("current", "")  # type: ignore[attr-defined]


def test_a_floating_tag_that_changed_major_is_a_major_one(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(
        machine,
        "db",
        "postgres:latest",
        "3",
        repo_digests=[f"postgres@{A}"],
        env=["PG_VERSION=16.4-1.pgdg120+1"],
    )
    machine.remote["docker.io/library/postgres:latest"] = RemoteTag(
        B, env=["PG_VERSION=17.2-1.pgdg120+1"]
    )

    found = checked(run_with(machine, store, tmp_path))["postgres:latest"]

    assert (found.state, found.change, found.floating) == ("newer", "major", True)  # type: ignore[attr-defined]


def test_a_tag_that_pins_the_major_is_never_a_major_by_moving(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(machine, "app", "nextcloud:29-apache", "4", repo_digests=[f"nextcloud@{A}"])
    machine.remote["docker.io/library/nextcloud:29-apache"] = RemoteTag(
        B, env=["NEXTCLOUD_VERSION=29.0.11"]
    )

    found = checked(run_with(machine, store, tmp_path))["nextcloud:29-apache"]

    assert (found.state, found.change) == ("newer", "minor")  # type: ignore[attr-defined]


def test_a_database_whose_new_version_cannot_be_read_is_treated_as_a_major_one(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(machine, "db", "mysql:latest", "5", repo_digests=[f"mysql@{A}"])
    machine.remote["docker.io/library/mysql:latest"] = RemoteTag(B, config_readable=False)

    found = checked(run_with(machine, store, tmp_path))["mysql:latest"]

    assert (found.state, found.change) == ("newer", "major")  # type: ignore[attr-defined]
    assert "database" in found.detail  # type: ignore[attr-defined]


def test_a_program_that_is_not_a_database_whose_new_version_cannot_be_read_says_it_cannot(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(machine, "tools", "adminer:latest", "6", repo_digests=[f"adminer@{A}"])
    machine.remote["docker.io/library/adminer:latest"] = RemoteTag(B, config_readable=False)

    found = checked(run_with(machine, store, tmp_path))["adminer:latest"]

    assert (found.state, found.change) == ("newer", "unknown")  # type: ignore[attr-defined]


def test_a_tag_the_registry_does_not_have_is_unknown_with_the_registrys_words(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(machine, "old", "acme/gone:1", "7", repo_digests=[f"acme/gone@{A}"])

    found = checked(run_with(machine, store, tmp_path))["acme/gone:1"]

    assert found.state == "unknown"  # type: ignore[attr-defined]
    assert "not found" in found.detail  # type: ignore[attr-defined]


def test_what_was_built_here_and_what_a_digest_pins_have_no_registry_to_ask(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(machine, "built", "shop-web:latest", "8", repo_digests=[])
    machine.box("pinned", image=f"redis@{A}", policy="unless-stopped")

    run = run_with(machine, store, tmp_path).check()

    assert run.records == []
    assert {(item.reference, item.why) for item in run.skipped} == {
        ("shop-web:latest", "built_here"),
        (f"redis@{A}", "pinned"),
    }
    assert not [call for call in machine.calls if call[:3] == ("docker", "buildx", "imagetools")]


def test_one_image_many_containers_is_one_question(store: NoustStore, tmp_path: Path) -> None:
    machine = DockerMachine()
    running(machine, "cache-a", "redis:7-alpine", "1", repo_digests=[f"redis@{A}"])
    machine.box("cache-b", image="redis:7-alpine", policy="unless-stopped")
    machine.remote["docker.io/library/redis:7-alpine"] = RemoteTag(B)

    run_with(machine, store, tmp_path).check()

    asked = [
        c
        for c in machine.calls
        if c[:4] == ("docker", "buildx", "imagetools", "inspect") and "--format" not in c
    ]
    assert len(asked) == 1


def test_nothing_is_downloaded_unless_the_operator_asks(store: NoustStore, tmp_path: Path) -> None:
    machine = DockerMachine()
    machine.buildx = False
    running(machine, "cache", "redis:7", "1", repo_digests=[f"redis@{A}"])
    machine.remote["docker.io/library/redis:7"] = RemoteTag(B)

    found = checked(run_with(machine, store, tmp_path))["redis:7"]

    assert found.state == "unknown"  # type: ignore[attr-defined]
    assert "download" in found.detail  # type: ignore[attr-defined]
    assert not [call for call in machine.calls if call[:2] == ("docker", "pull")]


def test_with_pull_a_docker_without_buildx_downloads_and_compares(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    machine.buildx = False
    running(
        machine, "cache", "redis:7", "1", repo_digests=[f"redis@{A}"], env=["REDIS_VERSION=7.4.1"]
    )
    machine.remote["docker.io/library/redis:7"] = RemoteTag(B, env=["REDIS_VERSION=7.4.2"])

    found = checked(run_with(machine, store, tmp_path), pull=True)["redis:7"]

    assert (found.state, found.change, found.method) == ("newer", "patch", "pull")  # type: ignore[attr-defined]


def test_the_account_kept_for_a_registry_is_used_for_that_registry_only(
    store: NoustStore, tmp_path: Path
) -> None:
    machine = DockerMachine()
    running(machine, "api", "ghcr.io/acme/api:2", "1", repo_digests=[f"ghcr.io/acme/api@{A}"])
    running(machine, "cache", "redis:7", "2", repo_digests=[f"redis@{A}"])
    machine.remote["ghcr.io/acme/api:2"] = RemoteTag(B)
    machine.remote["docker.io/library/redis:7"] = RemoteTag(B)
    versions = run_with(machine, store, tmp_path)
    versions.registries.add("ghcr-acme", "ghcr", "acme-bot", "ghp_tOpSeCrEtToKeN1234567890abcdef")

    versions.check()

    logins = [call for call in machine.calls if call[:2] == ("docker", "login")]
    assert [call[2] for call in logins] == ["ghcr.io"]
    by_ref = {
        call[-1]: env
        for call, env in zip(machine.calls, machine.envs, strict=True)
        if call[:4] == ("docker", "buildx", "imagetools", "inspect") and "--format" not in call
    }
    assert by_ref["ghcr.io/acme/api:2"] is not None
    assert "DOCKER_CONFIG" in by_ref["ghcr.io/acme/api:2"]  # type: ignore[operator]
    assert not by_ref["docker.io/library/redis:7"]
    assert "ghp_tOpSeCrEtToKeN1234567890abcdef" not in "\n".join(" ".join(c) for c in machine.calls)


def test_what_nothing_runs_any_more_is_forgotten_and_what_was_found_is_kept(
    store: NoustStore, tmp_path: Path
) -> None:
    ImageVersionStore(store).save(_stale("mongo:7"))
    machine = DockerMachine()
    running(machine, "cache", "redis:7", "1", repo_digests=[f"redis@{A}"])
    machine.remote["docker.io/library/redis:7"] = RemoteTag(B)

    run_with(machine, store, tmp_path).check()

    assert [row.reference for row in ImageVersionStore(store).all()] == ["redis:7"]


def _stale(reference: str):  # type: ignore[no-untyped-def]
    from noust.core.docker_image_checks import VersionRecord

    return VersionRecord(
        reference, "newer", A, B, None, None, None, "patch", False, "imagetools", "", NOW
    )


def test_the_records_of_a_look_say_when_it_was(store: NoustStore, tmp_path: Path) -> None:
    machine = DockerMachine()
    running(machine, "cache", "redis:7", "1", repo_digests=[f"redis@{A}"])
    machine.remote["docker.io/library/redis:7"] = RemoteTag(B)

    run = run_with(machine, store, tmp_path).check()

    assert run.checked_at == NOW
    assert run.records[0].checked_at == NOW
    assert run.to_dict()["checked_at"] == NOW


def test_progress_is_told_image_by_image(store: NoustStore, tmp_path: Path) -> None:
    machine = DockerMachine()
    running(machine, "cache", "redis:7", "1", repo_digests=[f"redis@{A}"])
    running(machine, "front", "nginx:1.27", "2", repo_digests=[f"nginx@{A}"])
    machine.remote["docker.io/library/redis:7"] = RemoteTag(B)
    machine.remote["docker.io/library/nginx:1.27"] = RemoteTag(A)
    said: list[tuple[int, int, str]] = []

    run_with(machine, store, tmp_path).check(
        on_progress=lambda done, total, ref: said.append((done, total, ref))
    )

    assert said == [(1, 2, "nginx:1.27"), (2, 2, "redis:7")]
