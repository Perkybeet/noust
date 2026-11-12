# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Docker's own JSON, read into typed objects (``noust.core.docker.reading``).

What is held here: the commands are argument lists asked through the runner with a deadline; they ask
Docker for each label by name (the ``Labels`` column of Docker is one string a label's value can
forge another label into, so it is never read); a line that is not JSON, or an object without what
makes it that object, is skipped and not a crash; and a sentence says why when Docker cannot be asked.
"""

from __future__ import annotations

import json

import pytest

from noust.core.docker import reading
from noust.core.docker.errors import DockerUnavailableError
from noust.core.docker.models import Container, Image, Network, Volume
from noust.core.runner import FakeRunner

SHA = "a" * 64


def ps_line(**changes: object) -> str:
    row = {
        "id": SHA,
        "name": "shop-web-1",
        "image": "nginx:1.27",
        "state": "running",
        "status": "Up 2 hours (healthy)",
        "created": "2026-11-12 10:00:00 +0000 UTC",
        "ports": "127.0.0.1:3001->3000/tcp",
        "mounts": "shop_data,/var/www/apps/shop-example-com/uploads",
        "networks": "shop_default",
        "project": "shop",
        "service": "web",
        "working_dir": "/var/www/apps/shop-example-com",
        "config_files": "/var/www/apps/shop-example-com/docker-compose.yml",
        "oneoff": "False",
        "instance": "",
        "helper": "",
    }
    row.update(changes)
    return json.dumps(row)


class TestContainers:
    def test_a_container_is_read_with_the_labels_asked_by_name(self) -> None:
        (container,) = reading.parse_containers(ps_line())

        assert container == Container(
            id=SHA,
            name="shop-web-1",
            image="nginx:1.27",
            state="running",
            status="Up 2 hours (healthy)",
            health="healthy",
            created="2026-11-12 10:00:00 +0000 UTC",
            ports="127.0.0.1:3001->3000/tcp",
            mounts=("shop_data", "/var/www/apps/shop-example-com/uploads"),
            networks=("shop_default",),
            project="shop",
            service="web",
            working_dir="/var/www/apps/shop-example-com",
            config_files=("/var/www/apps/shop-example-com/docker-compose.yml",),
            oneoff=False,
            instance="",
            helper="",
        )

    @pytest.mark.parametrize(
        ("status", "health"),
        [
            ("Up 3 minutes (healthy)", "healthy"),
            ("Up 3 minutes (unhealthy)", "unhealthy"),
            ("Up 3 seconds (health: starting)", "starting"),
            ("Up 3 minutes", ""),
            ("Exited (1) 2 minutes ago", ""),
        ],
    )
    def test_the_health_is_what_the_status_says_in_parentheses(
        self, status: str, health: str
    ) -> None:
        (container,) = reading.parse_containers(ps_line(status=status))

        assert container.health == health

    def test_the_names_of_a_container_are_the_first_and_a_prefix_is_not_a_name(self) -> None:
        (container,) = reading.parse_containers(ps_line(name="web,shop-web-1/alias"))

        assert container.name == "web"

    def test_a_line_that_is_not_json_or_has_no_id_is_skipped(self) -> None:
        text = "\n".join(["not json", "", "[1, 2]", ps_line(id=""), ps_line(name="kept")])

        assert [c.name for c in reading.parse_containers(text)] == ["kept"]

    def test_a_label_that_contains_the_text_of_another_label_is_just_its_value(self) -> None:
        """What a forged value cannot do: Docker is asked for each label, never parsed from one string."""
        forged = "x,com.docker.compose.project=shop,com.docker.compose.project.working_dir=/srv/a"
        (container,) = reading.parse_containers(ps_line(project="", instance=forged))

        assert container.project == ""
        assert container.working_dir == "/var/www/apps/shop-example-com"
        assert container.instance == forged

    def test_the_command_asks_for_every_container_and_each_label_by_name(self) -> None:
        runner = FakeRunner()
        runner.script(["docker", "ps"], stdout=ps_line())

        containers = reading.list_containers(runner)

        (call,) = runner.calls
        assert call[:5] == ("docker", "ps", "--all", "--no-trunc", "--format")
        template = call[5]
        for label in (
            "com.docker.compose.project",
            "com.docker.compose.service",
            "com.docker.compose.project.working_dir",
            "com.docker.compose.project.config_files",
            "com.docker.compose.oneoff",
            "io.noust.db-instance",
            "io.noust.helper",
        ):
            assert f'.Label "{label}"' in template
        assert len(containers) == 1


class TestImages:
    def test_an_image_is_read_by_id_with_its_tag_and_size(self) -> None:
        text = json.dumps(
            {
                "ID": f"sha256:{SHA}",
                "Repository": "noust-shop-example-com",
                "Tag": "20261112-101500-abc1234",
                "CreatedAt": "2026-11-12 10:15:00 +0000 UTC",
                "Size": "312MB",
            }
        )

        (image,) = reading.parse_images(text)

        assert image == Image(
            id=f"sha256:{SHA}",
            tags=("noust-shop-example-com:20261112-101500-abc1234",),
            created="2026-11-12 10:15:00 +0000 UTC",
            size_bytes=312_000_000,
        )

    def test_the_tags_of_one_image_are_gathered_and_an_untagged_one_has_none(self) -> None:
        rows = [
            {"ID": "sha256:1", "Repository": "shop-web", "Tag": "noust-1", "Size": "1GB"},
            {"ID": "sha256:1", "Repository": "shop-web", "Tag": "noust_previous", "Size": "1GB"},
            {"ID": "sha256:2", "Repository": "<none>", "Tag": "<none>", "Size": "10kB"},
        ]

        first, second = reading.parse_images("\n".join(json.dumps(row) for row in rows))

        assert first.tags == ("shop-web:noust-1", "shop-web:noust_previous")
        assert second.tags == ()
        assert (first.size_bytes, second.size_bytes) == (1_000_000_000, 10_000)

    @pytest.mark.parametrize(
        ("text", "size"),
        [
            ("29.98GB (63%)", 29_980_000_000),
            ("310.2MB", 310_200_000),
            ("0B", 0),
            ("N/A", None),
            ("", None),
        ],
    )
    def test_docker_counts_in_powers_of_ten(self, text: str, size: int | None) -> None:
        assert reading.parse_size(text) == size


class TestVolumesAndNetworks:
    def test_a_volume_is_read_with_the_project_label_asked_by_name(self) -> None:
        text = json.dumps(
            {
                "name": "shop_data",
                "driver": "local",
                "scope": "local",
                "mountpoint": "/var/lib/docker/volumes/shop_data/_data",
                "project": "shop",
                "volume": "data",
                "instance": "",
            }
        )

        (volume,) = reading.parse_volumes(text)

        assert volume == Volume(
            name="shop_data",
            driver="local",
            scope="local",
            mountpoint="/var/lib/docker/volumes/shop_data/_data",
            project="shop",
            key="data",
            instance="",
        )

    def test_a_network_is_read_with_whether_it_is_internal(self) -> None:
        text = json.dumps(
            {
                "id": SHA,
                "name": "shop_default",
                "driver": "bridge",
                "scope": "local",
                "internal": "false",
                "project": "shop",
                "created": "2026-11-12 10:00:00 +0000 UTC",
            }
        )

        (network,) = reading.parse_networks(text)

        assert network == Network(
            id=SHA,
            name="shop_default",
            driver="bridge",
            scope="local",
            internal=False,
            project="shop",
            created="2026-11-12 10:00:00 +0000 UTC",
        )

    def test_docker_own_networks_are_marked_as_its_own(self) -> None:
        rows = [
            {"id": "1", "name": name, "driver": driver, "scope": "local"}
            for name, driver in (("bridge", "bridge"), ("host", "host"), ("none", "null"))
        ]

        networks = reading.parse_networks("\n".join(json.dumps(row) for row in rows))

        assert all(network.builtin for network in networks)

    def test_the_commands_are_asked_through_the_runner_with_a_deadline(self) -> None:
        runner = FakeRunner()

        reading.list_volumes(runner)
        reading.list_networks(runner)
        reading.list_images(runner)

        assert [call[:3] for call in runner.calls] == [
            ("docker", "volume", "ls"),
            ("docker", "network", "ls"),
            ("docker", "image", "ls"),
        ]


class TestVolumeSizes:
    def test_the_sizes_come_from_the_verbose_disk_usage(self) -> None:
        text = json.dumps(
            {
                "Volumes": [
                    {"Name": "shop_data", "Links": "1", "Size": "2.5GB"},
                    {"Name": "orphan", "Links": "0", "Size": "0B"},
                    {"Name": "weird", "Size": "N/A"},
                ]
            }
        )

        assert reading.parse_volume_sizes(text) == {"shop_data": 2_500_000_000, "orphan": 0}

    def test_one_object_a_line_is_read_too(self) -> None:
        text = "\n".join(json.dumps({"Name": name, "Size": "1kB"}) for name in ("a", "b"))

        assert reading.parse_volume_sizes(text) == {"a": 1000, "b": 1000}

    def test_an_answer_nobody_can_read_is_no_size_and_not_an_error(self) -> None:
        assert reading.parse_volume_sizes("garbage") == {}
        assert reading.parse_volume_sizes("") == {}


class TestWhenDockerCannotBeAsked:
    def test_no_docker_is_said_as_such(self) -> None:
        runner = FakeRunner().only_knows("systemctl")

        with pytest.raises(DockerUnavailableError) as caught:
            reading.list_containers(runner)

        assert "not installed" in caught.value.message
        assert runner.calls == []

    def test_a_daemon_that_does_not_answer_carries_its_own_words(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"],
            stderr="permission denied while trying to connect to the Docker daemon socket",
            exit_code=1,
        )

        with pytest.raises(DockerUnavailableError) as caught:
            reading.list_containers(runner)

        assert "permission denied" in (caught.value.output or "")
