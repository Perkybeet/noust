# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The disk page's Docker cleanup is a plan: what would go, whose it is and how much it holds.

An application that keeps three versions of itself keeps three images, and only the newest has a
container. The other two are what ``noust rollback`` goes back to, and Docker (and the page that asked
it) called them unused: the tag that protected them (``noust_previous``) is the one an update uses
while it runs, and the images of the releases an application keeps are named another way. A cleanup
that took them took the application's way back. What is held here is that no version an application
keeps is ever offered as unused, that what is offered says whose it is and how much it holds, that a
cleanup never plans a volume, and that the amount the page says could be reclaimed is what could.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.docker.owners import AppRecord, Registry
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.managers.server.storage import CLEANUP_ACTIONS, StorageManager
from tests.support.server_support import make_host, platform_for

APP = "/var/www/apps/shop-example-com"
R1, R2, R3 = (
    "20261110-090000-aaaaaaa",
    "20261111-090000-bbbbbbb",
    "20261112-090000-ccccccc",
)


def image(image_id: str, repository: str, tag: str, size: str) -> str:
    return json.dumps(
        {"ID": f"sha256:{image_id * 64}"[:71], "Repository": repository, "Tag": tag, "Size": size}
    )


@pytest.fixture
def machine(tmp_path: Path) -> FakeRunner:
    """A server with a container application on three versions, and two images nobody keeps."""
    runner = FakeRunner()
    runner.script(
        ["docker", "image", "ls"],
        stdout="\n".join(
            [
                image("1", "noust-shop-example-com", R1, "300MB"),
                image("2", "noust-shop-example-com", R2, "310MB"),
                image("3", "noust-shop-example-com", R3, "320MB"),
                image("4", "scratch", "1", "5MB"),
                image("5", "<none>", "<none>", "40MB"),
                image("6", "shop-example-com-worker", f"noust-{R2}", "200MB"),
                image("7", "shop-example-com-worker", "noust_previous", "200MB"),
            ]
        ),
    )
    runner.script(
        ["docker", "ps"],
        stdout=json.dumps(
            {
                "id": "e" * 64,
                "name": "shop-example-com-app-1",
                "image": f"noust-shop-example-com:{R3}",
                "state": "running",
                "project": "shop-example-com",
                "working_dir": APP,
            }
        ),
    )
    runner.script(
        ["docker", "system", "df"],
        stdout="\n".join(
            [
                json.dumps(
                    {
                        "Type": "Images",
                        "TotalCount": "7",
                        "Size": "1.4GB",
                        "Reclaimable": "1.1GB (78%)",
                    }
                ),
                json.dumps(
                    {
                        "Type": "Build Cache",
                        "TotalCount": "9",
                        "Size": "2GB",
                        "Reclaimable": "1.5GB",
                    }
                ),
                json.dumps(
                    {
                        "Type": "Local Volumes",
                        "TotalCount": "2",
                        "Size": "8GB",
                        "Reclaimable": "6GB",
                    }
                ),
            ]
        ),
    )
    return runner


def manager(runner: FakeRunner, tmp_path: Path) -> StorageManager:
    return StorageManager(
        runner=runner,
        host=make_host(tmp_path / "root"),
        platform=platform_for("apt"),
        fs=RecordingFileSystem(),
        release_apps=lambda: [],
        backups_usage=lambda: {},
        log_paths=lambda: [],
        registry=lambda: Registry(apps=(AppRecord("shop.example.com", APP),)),
    )


def short(digit: str) -> str:
    return digit * 12


class TestTheVersionsAnApplicationKeeps:
    def test_none_of_them_is_offered_as_unused(self, machine: FakeRunner, tmp_path: Path) -> None:
        offered = manager(machine, tmp_path).docker_unused_images()

        # What is left is the image with no name and the one nobody uses: the two that are nobody's.
        assert sorted(i["repository"] + ":" + i["tag"] for i in offered) == [
            "<none>:<none>",
            "scratch:1",
        ]
        assert short("1") not in {i["id"] for i in offered}
        assert short("2") not in {i["id"] for i in offered}
        assert short("6") not in {i["id"] for i in offered}
        assert short("7") not in {i["id"] for i in offered}

    def test_what_is_offered_says_whose_it_is_and_how_much_it_holds(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        offered = {i["repository"]: i for i in manager(machine, tmp_path).docker_unused_images()}

        scratch = offered["scratch"]
        assert scratch["owner"] == {"kind": "nobody", "subject": "", "project": ""}
        assert (scratch["size"], scratch["size_bytes"]) == ("5MB", 5_000_000)

    @pytest.mark.parametrize("digit", ["1", "2", "6", "7"])
    def test_a_version_cannot_be_planned_or_removed_by_naming_its_id(
        self, machine: FakeRunner, tmp_path: Path, digit: str
    ) -> None:
        from noust.core.exceptions import ValidationError

        storage = manager(machine, tmp_path)

        with pytest.raises(ValidationError, match="not in the list of unused images") as planned:
            storage.plan_cleanup("docker-image", target=short(digit))
        with pytest.raises(ValidationError, match="not in the list of unused images"):
            storage.cleanup("docker-image", confirm=True, target=short(digit))

        assert "go back to" in planned.value.message
        assert not machine.ran("docker", "image", "rm")

    def test_the_version_of_a_container_application_says_whose_it_is_in_the_refusal(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as refused:
            manager(machine, tmp_path).plan_cleanup("docker-image", target=short("1"))

        assert "shop.example.com keeps" in refused.value.message
        assert "noust releases keep" in (refused.value.details or "")

    def test_the_newest_version_in_use_is_not_offered_either(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        offered = {i["id"] for i in manager(machine, tmp_path).docker_unused_images()}

        assert short("3") not in offered

    def test_an_image_nobody_keeps_and_nothing_runs_is_planned_with_its_owner_and_size(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        plan = manager(machine, tmp_path).plan_cleanup("docker-image", target=short("4"))

        (entry,) = plan.entries
        assert (entry.name, entry.size_bytes, entry.reason) == ("scratch:1", 5_000_000, "unused")
        assert entry.owner == {"kind": "nobody", "subject": "", "project": ""}
        assert plan.items == (short("4"),)
        assert plan.needs_confirmation is True

    def test_it_is_removed_by_the_id_docker_listed_without_force(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        result = manager(machine, tmp_path).cleanup("docker-image", confirm=True, target=short("4"))

        assert ("docker", "image", "rm", f"sha256:{'4' * 64}") in machine.calls
        assert result.removed == [short("4")]
        assert not any("--force" in call or "-f" in call for call in machine.calls if "rm" in call)


class TestWhatThePlanSays:
    def test_the_dangling_images_are_planned_by_name_and_never_a_version(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        plan = manager(machine, tmp_path).plan_cleanup("docker-dangling-images")

        assert [(e.name, e.size_bytes, e.reason) for e in plan.entries] == [
            (short("5"), 40_000_000, "dangling")
        ]
        assert plan.commands == ("docker image prune -f",)

    def test_the_build_cache_is_planned_with_what_it_holds(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        plan = manager(machine, tmp_path).plan_cleanup("docker-build-cache")

        (entry,) = plan.entries
        assert (entry.name, entry.size_bytes, entry.reason) == (
            "Docker build cache",
            1_500_000_000,
            "build-cache",
        )

    def test_no_cleanup_ever_plans_a_volume(self, machine: FakeRunner, tmp_path: Path) -> None:
        """A volume holds data; it is named, one by one, through the gate, and never swept."""
        storage = manager(machine, tmp_path)
        assert not any("volume" in action for action in CLEANUP_ACTIONS)

        for action in ("docker-build-cache", "docker-dangling-images"):
            plan = storage.plan_cleanup(action)
            assert all("volume" not in command for command in plan.commands)
            assert all(entry.reason != "volume" for entry in plan.entries)

    def test_the_plan_can_be_turned_into_json(self, machine: FakeRunner, tmp_path: Path) -> None:
        from dataclasses import asdict

        plan = manager(machine, tmp_path).plan_cleanup("docker-image", target=short("4"))

        body = json.loads(json.dumps(asdict(plan), default=str))

        assert body["entries"][0]["owner"]["kind"] == "nobody"


class TestWhatThePageSays:
    def candidates(self, machine: FakeRunner, tmp_path: Path) -> dict[str, Any]:
        found = manager(machine, tmp_path)._docker_candidates()
        return {c.id: c for c in found}

    def test_the_reclaimable_images_are_the_ones_that_could_be_removed(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        """Docker says 1.1GB is reclaimable: that counts the versions. What could go is 45MB."""
        images = self.candidates(machine, tmp_path)["docker-images"]

        assert images.reclaimable_bytes == 45_000_000
        assert images.action is None
        assert "keeps to go back to" in images.detail

    def test_the_versions_kept_are_shown_and_never_offered(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        kept = self.candidates(machine, tmp_path)["docker-kept-images"]

        assert kept.action is None and kept.reclaimable_bytes is None
        assert kept.size_bytes == 300_000_000 + 310_000_000 + 320_000_000 + 400_000_000
        assert "5 images" in kept.detail

    def test_the_volumes_are_shown_as_before_and_never_offered(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        volumes = self.candidates(machine, tmp_path)["docker-volumes"]

        assert (volumes.action, volumes.reclaimable_bytes) == (None, None)
        assert volumes.size_bytes == 8_000_000_000

    def test_without_docker_there_is_nothing_to_say(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("journalctl")

        assert manager(runner, tmp_path)._docker_candidates() == []
        assert manager(runner, tmp_path).docker_unused_images() == []
