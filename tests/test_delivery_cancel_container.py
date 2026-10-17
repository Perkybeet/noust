# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cancelling the update of a container application and of a monorepo on releases (checklist B-05).

A container application's image is built or pulled by the Docker daemon and its compose file is
written once that is done, so a build an operator stops leaves the release that serves exactly as
it was: no failed release is recorded, and no half-built image is kept. A monorepo on releases
builds in a release of its own; stopping the build removes it and leaves ``current`` where it was.
"""

# The harnesses are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import app_lock
from noust.core.cancellation import operation_window, request_cancel
from noust.core.exceptions import DeploymentCancelled
from noust.core.runner import CommandCancelled, FakeRunner
from noust.core.store import NoustStore
from tests.container_support import APP, DOMAIN, Docker, root_of
from tests.test_container_deployer import (  # noqa: F401  (pytest resolves fixtures by name)
    certs,
    docker,
    make,
    releases,
    running_image,
    serving_now,
    store,
    update,
    web,
)


WHO = "token:ci"


def stop_when(docker: Docker, domain: str, words: tuple[str, ...]) -> None:
    """
    Make the Docker an operator stops while a command that holds these words runs.

    Args:
        docker: The fake.
        domain: The application the request is for.
        words: Words of the command during which the operator asks to cancel.
    """
    run, stream = docker.run, docker.stream

    def arm(argv: Any) -> None:
        if all(word in tuple(argv) for word in words):
            request_cancel(domain, by=WHO)
            raise CommandCancelled(f"Cancelled: {' '.join(argv)}")

    def cancelling_run(argv: Any, **kwargs: Any) -> Any:
        arm(argv)
        return run(argv, **kwargs)

    def cancelling_stream(argv: Any, **kwargs: Any) -> Any:
        arm(argv)
        return stream(argv, **kwargs)

    docker.run = cancelling_run  # type: ignore[method-assign]
    docker.stream = cancelling_stream  # type: ignore[method-assign]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = root_of(tmp_path)
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "Dockerfile").write_text("FROM scratch\nEXPOSE 9000\n")
    return root


def deployed(make, docker, store, tmp_path):  # type: ignore[no-untyped-def]
    deployer = make(None, source="https://example.com/shop.git", replace_existing=True)
    deployer.source_already_fetched = True
    deployer.deploy()
    serving_now(docker, tmp_path)
    return releases(store)[0][0]


class TestAContainerBuildThatIsStopped:
    def test_what_serves_is_left_exactly_as_it_was(self, make, docker, store, tmp_path, tree):
        first = deployed(make, docker, store, tmp_path)
        image = running_image(tmp_path)
        compose = (root_of(tmp_path) / "compose.noust.yaml").read_text()
        stop_when(docker, DOMAIN, ("docker", "build"))

        with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update"):
            with pytest.raises(DeploymentCancelled, match="cancelled by token:ci"):
                update(store, docker).update()

        assert running_image(tmp_path) == image
        assert (root_of(tmp_path) / "compose.noust.yaml").read_text() == compose
        # No release was made, failed or otherwise, and the image it had begun is not kept.
        assert [release for release, _ in releases(store)] == [first]
        assert {i for i in docker.images if i.startswith("noust-")} == {image}
        row = store.list_deployments(DOMAIN)[0]
        assert row.cancelled is True and row.cancelled_by == WHO

    def test_a_registry_pull_can_be_stopped_too(self, make, docker, store, tmp_path):
        from tests.test_container_deployer import IMAGE

        make(IMAGE).deploy()
        serving_now(docker, tmp_path)
        image = running_image(tmp_path)
        stop_when(docker, DOMAIN, ("docker", "pull"))

        with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update"):
            with pytest.raises(DeploymentCancelled):
                update(store, docker).update()

        assert running_image(tmp_path) == image
        assert len(releases(store)) == 1
        assert APP in str(root_of(tmp_path))
