# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long an image build may take is the application's to say (checklist A-48).

The Docker builds had 1800 seconds fixed: an image that took longer could not be built at all.
A stack and a container take the build block's ``timeout`` and ``cache`` like every type that
Noust builds, and the server's ``deploy.build_timeout`` when the application says nothing; the
build and the wait of the command that brings the containers up use the same number.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import delivery
from noust.core.container_store import ContainerSettings
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers.build_config import set_operator_build
from noust.deployers.helpers.container_image import ImageBuilder

DOMAIN = "stack.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def row(store: NoustStore, kind: str, tmp_path: Path) -> App:
    path = tmp_path / "apps" / "stack"
    path.mkdir(parents=True)
    return store.create_app(App(domain=DOMAIN, app_type=kind, app_path=str(path), port=3000))


@pytest.mark.parametrize("kind", ["docker-compose", "container"])
def test_the_block_an_operator_stores_for_an_image_build_is_accepted(
    store: NoustStore, tmp_path: Path, kind: str
) -> None:
    row(store, kind, tmp_path)

    build = set_operator_build(
        DOMAIN, "build:\n  timeout: 7200\n  cache: false\n", actor="t", store=store
    )

    assert build.timeout == 7200 and build.cache is False


def test_a_key_that_an_image_build_has_no_use_for_is_refused_with_the_reason(
    store: NoustStore, tmp_path: Path
) -> None:
    row(store, "container", tmp_path)

    with pytest.raises(ValidationError) as caught:
        set_operator_build(DOMAIN, "build:\n  install: npm ci\n", actor="t", store=store)

    assert caught.value.field == "build.install"


def test_a_stack_waits_as_long_as_its_block_says_or_the_server_does(
    store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = row(store, "docker-compose", tmp_path)
    monkeypatch.setattr(
        delivery,
        "current",
        lambda **_: delivery.DeliverySettings(build_timeout=5400),
    )
    deployer = DockerComposeDeployer(runner=FakeRunner())
    deployer.configure(DOMAIN, "src", app_path=Path(app.app_path))

    assert deployer.build_timeout() == 5400

    set_operator_build(DOMAIN, "build:\n  timeout: 7200\n", actor="t", store=store)

    assert deployer.build_timeout() == 7200


def test_the_stack_builds_without_its_cache_when_the_block_says_so(
    store: NoustStore, tmp_path: Path
) -> None:
    app = row(store, "docker-compose", tmp_path)
    (Path(app.app_path) / "docker-compose.yml").write_text(
        "services:\n  web:\n    build: .\n    ports: ['3000:80']\n"
    )
    set_operator_build(DOMAIN, "build:\n  cache: false\n", actor="t", store=store)
    runner = FakeRunner()
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(DOMAIN, "src", app_path=Path(app.app_path))
    deployer._discover_compose_file()

    deployer._build_images()

    build = next(call for call in runner.calls if "build" in call)
    assert "--no-cache" in build


class TimedRunner(FakeRunner):
    """A runner that remembers the time each streamed command was given."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[int] = []

    def stream(self, argv: Any, **kwargs: Any) -> Any:
        self.timeouts.append(kwargs["timeout"])
        return super().stream(argv, **kwargs)


def test_a_dockerfile_build_is_given_the_time_its_builder_was(
    tmp_path: Path, store: NoustStore
) -> None:
    context = tmp_path / "src"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\n")
    runner = TimedRunner()
    builder = ImageBuilder(
        runner=runner,
        logger=Logger(),
        app_name="stack",
        domain=DOMAIN,
        app_path=context,
        work_dir=tmp_path / "work",
        build_timeout=9000,
    )

    builder.build(ContainerSettings(), "noust-stack:r1")

    assert runner.timeouts == [9000]
