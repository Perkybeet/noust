# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Is there a new image behind the tag an application runs? (checklist A-14).

One question with one answer for a schedule, a registry's webhook and ``noust app container
check``: pull the tag, read the digest of what came, compare it with the digest that runs. An
image pinned by digest is never new, and an application that is not an image is refused.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.exceptions import DockerError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers import image_updates, update_scheduler
from tests.container_support import DIGEST, Docker

DOMAIN = "img.example.com"
IMAGE = "ghcr.io/owner/app:1.4"
OLDER = "sha256:" + "1" * 64


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker() -> Docker:
    return Docker()


def deploy_row(store: NoustStore, settings: ContainerSettings | None = None) -> None:
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="container",
            source=IMAGE,
            port=3001,
            app_path="/var/www/apps/img",
        )
    )
    ContainerStore(store).save(
        DOMAIN,
        settings or ContainerSettings(method="image", image=IMAGE, digest=DIGEST),
        actor="test",
    )


def look(store: NoustStore, docker: Docker) -> image_updates.ImageCheck:
    return image_updates.check_image(
        DOMAIN, store=store, runner=docker, secrets=SecretStore(root=store.db_path.parent / "s")
    )


class TestLookingAtATag:
    def test_the_digest_that_runs_is_nothing_new(self, store: NoustStore, docker: Docker) -> None:
        deploy_row(store)

        found = look(store, docker)

        assert found.changed is False
        assert found.deployed == found.current == DIGEST
        assert ("docker", "pull", IMAGE) in docker.calls

    def test_another_digest_is_new(self, store: NoustStore, docker: Docker) -> None:
        deploy_row(store, ContainerSettings(method="image", image=IMAGE, digest=OLDER))

        found = look(store, docker)

        assert found.changed is True
        assert found.deployed == OLDER and found.current == DIGEST
        assert OLDER in found.reason and DIGEST in found.reason

    def test_no_recorded_digest_is_taken_as_new(self, store: NoustStore, docker: Docker) -> None:
        deploy_row(store, ContainerSettings(method="image", image=IMAGE))

        assert look(store, docker).changed is True

    def test_looking_deploys_and_names_nothing(self, store: NoustStore, docker: Docker) -> None:
        deploy_row(store)

        look(store, docker)

        assert not [call for call in docker.calls if call[1:3] == ("image", "tag")]
        assert not [call for call in docker.calls if call[1] == "compose"]

    def test_an_image_pinned_by_digest_is_never_new_and_is_not_pulled(
        self, store: NoustStore, docker: Docker
    ) -> None:
        pinned = "ghcr.io/owner/app@" + DIGEST
        deploy_row(store, ContainerSettings(method="image", image=pinned, digest=OLDER))

        found = look(store, docker)

        assert found.changed is False and "pinned" in found.reason
        assert docker.docker_calls("pull") == []

    def test_a_registry_that_refuses_the_pull_says_so(
        self, store: NoustStore, docker: Docker
    ) -> None:
        deploy_row(store)
        docker.fail.add("pull")

        with pytest.raises(DockerError) as caught:
            look(store, docker)

        assert "Failed to pull" in caught.value.message

    def test_a_repository_application_has_no_tag_to_follow(
        self, store: NoustStore, docker: Docker
    ) -> None:
        deploy_row(store, ContainerSettings())

        with pytest.raises(ValidationError) as caught:
            look(store, docker)

        assert "does not run an image" in caught.value.message

    def test_an_unknown_application_is_said_to_be_unknown(
        self, store: NoustStore, docker: Docker
    ) -> None:
        with pytest.raises(Exception, match="not found"):
            look(store, docker)


class TestTheSchedule:
    def test_a_scheduled_run_updates_only_when_the_tag_moved(
        self, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy_row(store, ContainerSettings(method="image", image=IMAGE, digest=OLDER))
        from noust.core import runner as runner_module

        runner_module.set_runner(docker)
        try:
            new, why = update_scheduler._something_new(DOMAIN)
        finally:
            runner_module.set_runner(None)

        assert new is True and DIGEST in why

    def test_a_registry_that_cannot_be_asked_does_not_stop_the_update_from_saying_why(
        self, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy_row(store)
        docker.fail.add("pull")
        from noust.core import runner as runner_module

        runner_module.set_runner(docker)
        try:
            new, why = update_scheduler._something_new(DOMAIN)
        finally:
            runner_module.set_runner(None)

        assert new is True and "registry could not be asked" in why

    def test_hourly_is_a_calendar(self) -> None:
        assert update_scheduler.expand_calendar("hourly") == "*-*-* *:00:00"


class TestTheCommandLine:
    def test_check_says_what_it_found_as_json(
        self, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy_row(store, ContainerSettings(method="image", image=IMAGE, digest=OLDER))
        from noust.core import runner as runner_module

        runner_module.set_runner(docker)
        try:
            done = CliRunner().invoke(root_cli, ["app", "container", "check", DOMAIN, "--json"])
        finally:
            runner_module.set_runner(None)

        assert done.exit_code == 0, done.output
        data = json.loads(done.output)
        assert data["changed"] is True and data["current"] == DIGEST and data["deployed"] == OLDER
