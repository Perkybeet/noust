# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Compose project Noust writes for a container application.

One service, the image of one release, published on 127.0.0.1 only. The
file is generated from the store's settings and nothing in it comes from the
repository, so it is held to what the compose guard refuses in anyone else's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from noust.core.container_store import ContainerSettings
from noust.core.exceptions import ValidationError
from noust.deployers.docker_compose import parse_services
from noust.deployers.helpers.compose_guard import inspect_compose
from noust.deployers.helpers.compose_ports import is_headless_stack, published_port
from noust.deployers.helpers.container_compose import (
    COMPOSE_FILE,
    GENERATED_MARKER,
    SERVICE,
    ContainerPlan,
    build_document,
    image_repository,
    image_tag,
    release_of_tag,
    render_compose,
)
from noust.managers.service_manager import ResourceLimits

RELEASE = "20261006-101500-ab12cd3"


def plan(**overrides) -> ContainerPlan:
    settings = overrides.pop("settings", ContainerSettings(container_port=8080))
    values = {
        "app_name": "shop-example-com",
        "domain": "shop.example.com",
        "release_id": RELEASE,
        "host_port": 3001,
        "settings": settings,
        "limits": ResourceLimits(),
    }
    values.update(overrides)
    return ContainerPlan(**values)


class TestNames:
    def test_an_image_is_named_after_the_application_and_the_release(self):
        assert image_repository("shop-example-com") == "noust-shop-example-com"
        assert image_tag("shop-example-com", RELEASE) == f"noust-shop-example-com:{RELEASE}"

    def test_the_release_is_read_back_from_a_tag(self):
        assert release_of_tag("shop-example-com", f"noust-shop-example-com:{RELEASE}") == RELEASE
        assert release_of_tag("shop-example-com", "noust-other:" + RELEASE) is None
        assert release_of_tag("shop-example-com", "nginx:1.27") is None
        assert release_of_tag("shop-example-com", "noust-shop-example-com:latest") is None

    def test_the_file_has_its_own_name_so_a_repositorys_compose_file_is_left_alone(self):
        assert COMPOSE_FILE == "compose.noust.yaml"
        assert COMPOSE_FILE not in {"compose.yaml", "docker-compose.yml"}


class TestTheService:
    def test_it_runs_the_image_of_the_release_and_never_builds_or_pulls(self):
        service = build_document(plan())["services"][SERVICE]

        assert service["image"] == f"noust-shop-example-com:{RELEASE}"
        # The image was built or pulled by Noust, behind the health gate; a
        # restart of the unit must not fetch anything.
        assert service["pull_policy"] == "never"
        assert "build" not in service

    def test_the_port_is_published_on_loopback_only(self):
        service = build_document(plan(host_port=3017))["services"][SERVICE]

        assert service["ports"] == ["127.0.0.1:3017:8080"]

    def test_the_container_port_is_the_methods_default_when_none_is_set(self):
        service = build_document(plan(settings=ContainerSettings(method="railpack")))["services"][
            SERVICE
        ]

        assert service["ports"] == ["127.0.0.1:3001:3000"]

    def test_the_environment_is_the_applications_env_file(self):
        service = build_document(plan())["services"][SERVICE]

        assert service["env_file"] == [".env"]
        assert "environment" not in service

    def test_railpack_images_are_told_their_port(self):
        service = build_document(
            plan(settings=ContainerSettings(method="railpack", container_port=4000))
        )["services"][SERVICE]

        assert service["environment"] == {"PORT": "4000"}

    def test_the_restart_policy_is_the_applications(self):
        service = build_document(plan(settings=ContainerSettings(restart_policy="always")))[
            "services"
        ][SERVICE]

        assert service["restart"] == "always"

    def test_the_default_restart_brings_it_back_after_a_reboot(self):
        assert build_document(plan())["services"][SERVICE]["restart"] == "unless-stopped"

    def test_it_is_labelled_with_the_application_and_the_release(self):
        labels = build_document(plan())["services"][SERVICE]["labels"]

        assert labels["io.noust.app"] == "shop.example.com"
        assert labels["io.noust.release"] == RELEASE
        assert labels["io.noust.managed"] == "true"

    def test_a_registry_image_says_where_it_came_from(self):
        settings = ContainerSettings(method="image", image="ghcr.io/owner/app:1.4")
        labels = build_document(plan(settings=settings))["services"][SERVICE]["labels"]

        assert labels["io.noust.source"] == "ghcr.io/owner/app:1.4"


class TestVolumes:
    def test_each_declared_path_is_a_named_volume(self):
        settings = ContainerSettings(volumes=("/data", "/var/lib/app"))
        document = build_document(plan(settings=settings))

        assert document["services"][SERVICE]["volumes"] == [
            "data:/data",
            "var-lib-app:/var/lib/app",
        ]
        assert document["volumes"] == {"data": {}, "var-lib-app": {}}

    def test_no_volumes_no_section(self):
        document = build_document(plan())

        assert "volumes" not in document
        assert "volumes" not in document["services"][SERVICE]

    def test_two_paths_that_slug_alike_keep_their_own_volume(self):
        settings = ContainerSettings(volumes=("/a/b", "/a-b"))
        document = build_document(plan(settings=settings))

        names = [entry.split(":")[0] for entry in document["services"][SERVICE]["volumes"]]
        assert len(set(names)) == 2


class TestLimits:
    def test_the_limits_of_the_application_become_the_containers(self):
        limits = ResourceLimits(memory_max_mb=512, cpu_quota_percent=150, tasks_max=200)
        service = build_document(plan(limits=limits))["services"][SERVICE]

        assert service["deploy"] == {"resources": {"limits": {"memory": "512M", "cpus": "1.50"}}}
        assert service["pids_limit"] == 200

    def test_no_limits_no_section(self):
        service = build_document(plan())["services"][SERVICE]

        assert "deploy" not in service
        assert "pids_limit" not in service

    def test_a_limit_alone(self):
        service = build_document(plan(limits=ResourceLimits(memory_max_mb=256)))["services"][
            SERVICE
        ]

        assert service["deploy"] == {"resources": {"limits": {"memory": "256M"}}}


class TestWhatIsWritten:
    def test_the_file_says_it_is_generated(self):
        text = render_compose(plan())

        assert text.startswith(f"# {GENERATED_MARKER}")
        assert "noust app container" in text

    def test_it_is_valid_yaml_that_compose_reads_as_a_web_service(self):
        document = yaml.safe_load(render_compose(plan(host_port=3001)))
        services = parse_services(document)

        assert [service.name for service in services] == [SERVICE]
        assert not is_headless_stack(services)
        assert published_port(services[0]) == 3001

    def test_nothing_in_it_is_root_on_the_host(self):
        settings = ContainerSettings(volumes=("/data",), restart_policy="always")
        document = yaml.safe_load(render_compose(plan(settings=settings)))

        findings = inspect_compose(document, Path("/var/www/apps/shop-example-com"))

        assert not findings

    def test_a_dollar_sign_cannot_reach_compose_interpolation(self):
        # Compose substitutes ${...} in the file; nothing in a reference or a
        # domain can hold one, and the file is checked for it all the same.
        text = render_compose(plan())

        assert "$" not in text

    def test_the_same_plan_writes_the_same_file(self):
        assert render_compose(plan()) == render_compose(plan())

    def test_a_port_that_is_not_one_is_refused_before_it_is_written(self):
        with pytest.raises(ValidationError):
            build_document(plan(host_port=0))
