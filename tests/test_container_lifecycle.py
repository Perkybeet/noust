# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container application through the one update, the releases and the deletion.

``lifecycle.update_app`` is the only update, ``list_releases`` and
``activate_release`` the only way to go back to a release, and ``delete_app``
the only deletion: a container application is reached through them like any
other, and what is specific to it (an image per release, no source for a
registry image, secrets that go with it) is asserted here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from noust.core.config import Config
from noust.core.container_store import ContainerSettings, registry_secret_name
from noust.core.exceptions import (
    DeploymentError,
    RolledBackError,
    SchemaChangedError,
    SourceError,
    ValidationError,
)
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.deployers.container import ContainerDeployer
from noust.deployers.helpers.container_compose import COMPOSE_FILE, image_tag
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ResourceLimits
from noust.managers.webserver import NGINX_BACKEND, SiteDeletion
from tests.container_support import APP, DOMAIN, Docker, FakeServices, install_fakes, root_of

IMAGE = ContainerSettings(method="image", image="ghcr.io/owner/app:1.4", container_port=8080)


@pytest.fixture(autouse=True)
def apps_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    apps = tmp_path / "apps"
    monkeypatch.setattr(Config, "apps_directory", property(lambda _self: apps))
    return apps


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    yield from install_fakes(monkeypatch, store)


@pytest.fixture
def machine(docker: Docker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The backup, the units and the sites, faked, around the real lifecycle."""
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        lifecycle,
        "RollbackManager",
        lambda verbose=False: SimpleNamespace(
            create_pre_deploy_backup=lambda **kw: calls.append(("backup", kw.get("domain")))
        ),
    )
    monkeypatch.setattr(lifecycle, "SourceManager", lambda verbose=False: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "ServiceManager", FakeServices)
    sites: list[str] = []

    def delete_site(domain: str, **kwargs: Any) -> SiteDeletion:
        sites.append(domain)
        return SiteDeletion(domain=domain, nginx_removed=True, certificate_removed=True)

    monkeypatch.setattr(lifecycle, "delete_site_completely", delete_site)
    return SimpleNamespace(calls=calls, sites=sites, docker=docker)


@pytest.fixture
def deploy(tmp_path: Path, docker: Docker, store: NoustStore) -> Callable[..., ContainerDeployer]:
    web = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    certs = CertManager()
    certs.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    certs.LIVE_DIR = certs.LETSENCRYPT_DIR / "live"
    certs.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]

    def run(settings: ContainerSettings = IMAGE, **options: Any) -> ContainerDeployer:
        deployer = ContainerDeployer(runner=docker)
        deployer.configure(
            DOMAIN,
            settings.image or "",
            port=3001,
            ssl=False,
            app_path=root_of(tmp_path),
            container=settings,
            **options,
        )
        deployer.webserver_manager = lambda: web  # type: ignore[method-assign]
        deployer.cert_manager = certs
        deployer._create_systemd_service = lambda **kwargs: None  # type: ignore[method-assign]
        deployer.deploy()
        docker.serving = yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())["services"][
            "app"
        ]["image"]
        return deployer

    return run


def release_ids(store: NoustStore) -> list[str]:
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return [row.id for row in store.list_releases(app.id)]


def running(tmp_path: Path) -> str:
    return yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())["services"]["app"][
        "image"
    ]


class TestTheOneUpdate:
    def test_a_registry_image_is_updated_by_pulling_it_again(
        self, deploy, machine, docker, store, tmp_path
    ):
        deploy()
        docker.docker_calls("pull").clear()
        phases: list[str] = []

        outcome = lifecycle.update_app(
            DOMAIN, on_phase=lambda i, n, message: phases.append(message)
        )

        assert outcome.app_type == "container"
        assert "Pulling the image again" in phases
        assert len(docker.docker_calls("pull")) == 2
        assert len(release_ids(store)) == 2
        assert ("backup", DOMAIN) in machine.calls

    def test_an_image_has_no_source_to_change(self, deploy, machine, docker, tmp_path):
        deploy()

        with pytest.raises(ValidationError) as caught:
            lifecycle.update_app(DOMAIN, source="https://example.com/other.git")

        assert caught.value.field == "source"
        assert "noust app container set" in caught.value.details

    def test_an_image_has_no_commit_to_rebuild(self, deploy, machine, docker, tmp_path):
        deploy()

        with pytest.raises(SourceError) as caught:
            lifecycle.update_app(DOMAIN, commit="abc1234")

        assert "no commit abc1234 to deploy" in caught.value.message

    def test_what_the_update_changed_is_a_container_not_a_unit(
        self, deploy, machine, docker, store, tmp_path
    ):
        deploy()

        outcome = lifecycle.update_app(DOMAIN)

        # The containers were recreated by the deployer: nothing for the caller to restart.
        assert outcome.is_static is True
        assert FakeServices.restarted == []

    def test_a_failed_update_leaves_what_served_and_says_so(
        self, deploy, machine, docker, store, tmp_path
    ):
        deploy()
        before = running(tmp_path)
        docker.healthy = False
        docker.on_up.append(lambda args: setattr(docker, "healthy", "--no-build" in args))

        with pytest.raises(RolledBackError):
            lifecycle.update_app(DOMAIN)

        assert running(tmp_path) == before


class TestTheReleases:
    @pytest.fixture
    def two(self, deploy, machine, docker, store, tmp_path) -> tuple[str, str]:
        deploy()
        first = release_ids(store)[0]
        lifecycle.update_app(DOMAIN)
        second = release_ids(store)[0]
        docker.serving = running(tmp_path)
        assert first != second
        return first, second

    def test_every_release_is_listed_with_its_image(self, two, store):
        first, second = two

        listed = lifecycle.list_releases(DOMAIN)

        assert [r.id for r in listed] == [second, first]
        assert [r.active for r in listed] == [True, False]
        assert all(r.on_disk for r in listed)

    def test_a_release_whose_image_was_pruned_cannot_be_activated(self, two, docker, store):
        first, _ = two
        docker.images.discard(image_tag(APP, first))

        listed = {r.id: r for r in lifecycle.list_releases(DOMAIN)}
        assert listed[first].on_disk is False
        with pytest.raises(DeploymentError) as caught:
            lifecycle.activate_release(DOMAIN, first)
        assert "no longer on this server" in caught.value.message
        assert "--commit" in caught.value.details

    def test_going_back_recreates_the_container_from_the_old_image_without_building(
        self, two, docker, store, tmp_path
    ):
        first, second = two
        builds = len(docker.docker_calls("build"))
        pulls = len(docker.docker_calls("pull"))

        outcome = lifecycle.activate_release(DOMAIN, first)

        assert outcome.changed and outcome.went_back
        assert outcome.release.id == first and outcome.previous is not None
        assert outcome.previous.id == second
        assert running(tmp_path) == image_tag(APP, first)
        assert len(docker.docker_calls("build")) == builds
        assert len(docker.docker_calls("pull")) == pulls

    def test_the_rollback_is_recorded_as_one_and_what_it_replaced_is_marked(
        self, two, docker, store
    ):
        first, second = two

        outcome = lifecycle.activate_release(DOMAIN, first)

        statuses = {r.id: r.status for r in lifecycle.list_releases(DOMAIN)}
        assert statuses[first] == "active"
        assert statuses[second] == "rolled_back"
        history = store.list_deployments(DOMAIN)
        assert history[0].id == outcome.deployment_id
        assert history[0].status == "success"
        assert history[1].status == "rolled_back"

    def test_with_no_release_named_it_goes_to_the_one_before(self, two, docker, store, tmp_path):
        first, _ = two

        outcome = lifecycle.activate_release(DOMAIN)

        assert outcome.release.id == first

    def test_the_active_release_is_left_alone(self, two, docker):
        _, second = two
        recreations = len([c for c in docker.compose_calls() if c[0] == "up"])

        outcome = lifecycle.activate_release(DOMAIN, second)

        assert outcome.changed is False and outcome.deployment_id is None
        assert len([c for c in docker.compose_calls() if c[0] == "up"]) == recreations

    def test_nothing_earlier_than_the_oldest_is_said(self, deploy, machine, docker, store):
        deploy()

        with pytest.raises(DeploymentError) as caught:
            lifecycle.activate_release(DOMAIN)

        assert "oldest" in caught.value.message

    def test_a_release_that_does_not_answer_is_failed_and_the_one_that_served_is_back(
        self, two, docker, store, tmp_path
    ):
        first, second = two
        # The first recreation (the old release) does not answer; going back does.
        recreations: list[int] = []
        docker.healthy = False
        docker.on_up.append(
            lambda args: (recreations.append(1), setattr(docker, "healthy", len(recreations) > 1))
        )

        with pytest.raises(RolledBackError):
            lifecycle.activate_release(DOMAIN, first)

        statuses = {r.id: r.status for r in lifecycle.list_releases(DOMAIN)}
        assert statuses[first] == "failed"
        assert statuses[second] == "active"
        assert running(tmp_path) == image_tag(APP, second)

    def test_going_back_past_a_schema_change_needs_a_yes(self, two, docker, store):
        first, second = two
        newest = store.list_deployments(DOMAIN)[0]
        assert newest.id is not None
        store.record_deployment_hooks(newest.id, hooks=None, schema_changed=True)

        with pytest.raises(SchemaChangedError):
            lifecycle.activate_release(DOMAIN, first)

        # Confirmed, it goes back: Noust puts code back, never a database.
        assert lifecycle.activate_release(DOMAIN, first, schema_changed_ok=True).changed

    def test_a_deployment_can_be_gone_back_to_through_its_release(
        self, two, docker, store, tmp_path
    ):
        first, second = two
        old = next(r for r in store.list_deployments(DOMAIN) if r.release_id == first)
        assert old.id is not None

        availability = lifecycle.rollback_availability(store.list_deployments(DOMAIN))
        assert availability[old.id] is None

        outcome = lifecycle.rollback_to_deployment(DOMAIN, old.id)

        assert outcome.release_id == first
        assert running(tmp_path) == image_tag(APP, first)

    def test_the_deployment_of_a_pruned_image_says_to_rebuild_its_commit(self, two, docker, store):
        first, _ = two
        old = next(r for r in store.list_deployments(DOMAIN) if r.release_id == first)
        docker.images.discard(image_tag(APP, first))

        availability = lifecycle.rollback_availability([old])

        assert old.id is not None
        assert "pruned" in (availability[old.id] or "")

    def test_the_deployment_that_is_live_cannot_be_gone_back_to(self, two, docker, store):
        _, second = two
        live = next(r for r in store.list_deployments(DOMAIN) if r.release_id == second)

        availability = lifecycle.rollback_availability([live])

        assert live.id is not None
        assert "already live" in (availability[live.id] or "")


class TestTheLimits:
    def test_limits_go_in_the_compose_file_and_the_row(
        self, deploy, machine, docker, store, tmp_path
    ):
        deploy()

        change = lifecycle.set_resource_limits(
            DOMAIN, ResourceLimits(memory_max_mb=384, tasks_max=64), restart=True
        )

        service = yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())["services"]["app"]
        assert service["deploy"]["resources"]["limits"] == {"memory": "384M"}
        assert service["pids_limit"] == 64
        app = store.get_app(DOMAIN)
        assert app is not None and app.memory_max_mb == 384
        assert change.restarted is True

    def test_limits_it_cannot_run_under_are_refused_and_taken_back(
        self, deploy, machine, docker, store, tmp_path
    ):
        deploy()
        recreations: list[int] = []
        docker.healthy = False
        docker.on_up.append(
            lambda args: (recreations.append(1), setattr(docker, "healthy", len(recreations) > 1))
        )

        with pytest.raises(DeploymentError) as caught:
            lifecycle.set_resource_limits(DOMAIN, ResourceLimits(memory_max_mb=64), restart=True)

        assert "previous ones are back" in caught.value.message
        service = yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())["services"]["app"]
        assert "deploy" not in service
        app = store.get_app(DOMAIN)
        assert app is not None and app.memory_max_mb is None


class TestTheDeletion:
    def test_containers_images_account_and_files_all_go(
        self, deploy, machine, docker, store, tmp_path
    ):
        secrets = SecretStore(root=tmp_path / "store" / "secrets")
        secrets.write(registry_secret_name(APP), "hunter2")
        deploy()
        lifecycle.update_app(DOMAIN)
        root = root_of(tmp_path)

        outcome = lifecycle.delete_app(DOMAIN)

        assert outcome.containers_stopped
        assert any("down" in call for call in docker.compose_calls())
        assert [i for i in docker.images if i.startswith(f"noust-{APP}:")] == []
        assert secrets.read(registry_secret_name(APP)) is None
        assert not root.exists()
        assert store.get_app(DOMAIN) is None

    def test_volumes_stay_unless_asked_for(self, deploy, machine, docker):
        deploy()

        lifecycle.delete_app(DOMAIN)

        down = next(c for c in docker.compose_calls() if "down" in c)
        assert "--volumes" not in down

    def test_volumes_go_when_asked(self, deploy, machine, docker):
        deploy()

        outcome = lifecycle.delete_app(DOMAIN, remove_volumes=True)

        down = next(c for c in docker.compose_calls() if "down" in c)
        assert "--volumes" in down and outcome.volumes_removed
