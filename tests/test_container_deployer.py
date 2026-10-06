# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploying, updating and going back on a container application.

Every test runs the real deployer, store, web server manager (over a temporary
tree) and release bookkeeping; only processes are faked, by a Docker that
remembers its images (``tests/container_support.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from noust.core.container_store import (
    ContainerSettings,
    ContainerStore,
    build_secret_name,
    registry_secret_name,
)
from noust.core.exceptions import DeploymentError, DockerError, RolledBackError
from noust.core.secrets import SecretStore
from noust.core.store import AppStatus, AppType, NoustStore, ReleaseStatus
from noust.deployers import container as container_module
from noust.deployers import docker_compose
from noust.deployers.container import ContainerDeployer
from noust.deployers.docker_compose import stack_deployer
from noust.deployers.helpers.container_compose import COMPOSE_FILE, image_tag
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND
from tests.container_support import (
    APP,
    DIGEST,
    DOMAIN,
    Docker,
    FakeServices,
    install_fakes,
    root_of,
)

IMAGE = ContainerSettings(
    method="image",
    image="ghcr.io/owner/app:1.4",
    container_port=8080,
)
PASSWORD = "hunter2-registry-password"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    yield from install_fakes(monkeypatch, store)


@pytest.fixture
def web(tmp_path: Path, docker: Docker) -> NginxManager:
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def certs(tmp_path: Path, docker: Docker) -> CertManager:
    manager = CertManager()
    manager.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    manager.LIVE_DIR = manager.LETSENCRYPT_DIR / "live"
    manager.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]
    return manager


@pytest.fixture
def make(
    tmp_path: Path, docker: Docker, web: NginxManager, certs: CertManager
) -> Callable[..., ContainerDeployer]:
    """Build a configured deployer, wired to this test's web server."""

    def build(settings: ContainerSettings | None = None, **options) -> ContainerDeployer:
        deployer = ContainerDeployer(runner=docker)
        deployer.configure(
            DOMAIN,
            options.pop("source", "ghcr.io/owner/app:1.4"),
            port=options.pop("port", 3001),
            ssl=False,
            app_path=root_of(tmp_path),
            container=settings,
            **options,
        )
        deployer.webserver_manager = lambda: web  # type: ignore[method-assign]
        deployer.cert_manager = certs
        deployer._create_systemd_service = lambda **kwargs: None  # type: ignore[method-assign]
        return deployer

    return build


def compose_text(tmp_path: Path) -> dict:
    return yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())


def running_image(tmp_path: Path) -> str:
    return compose_text(tmp_path)["services"]["app"]["image"]


def releases(store: NoustStore) -> list[tuple[str, str]]:
    """``(id, status)`` of the application's releases, newest first."""
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return [(row.id, row.status) for row in store.list_releases(app.id)]


def update(store: NoustStore, docker: Docker, *, commit: str | None = None) -> ContainerDeployer:
    """The deployer the lifecycle builds to update the application; it has not run yet."""
    app = store.get_app(DOMAIN)
    assert app is not None
    deployer = stack_deployer(app, runner=docker)
    assert isinstance(deployer, ContainerDeployer)
    deployer.previous_commit = commit
    return deployer


def serving_now(docker: Docker, tmp_path: Path) -> None:
    """Make the fake report what the compose file on disk runs as the running container."""
    docker.serving = running_image(tmp_path)


class TestDeployingARegistryImage:
    def test_it_pulls_the_image_names_it_for_the_release_and_runs_that_name(
        self, make, docker, store, tmp_path
    ):
        make(IMAGE).deploy()

        pull = docker.docker_calls("pull")
        assert [call[-1] for call in pull] == ["ghcr.io/owner/app:1.4"]
        release = releases(store)[0][0]
        tag = image_tag(APP, release)
        assert ("docker", "image", "tag", "sha256:" + "c" * 64, tag) in docker.calls
        service = compose_text(tmp_path)["services"]["app"]
        assert service["image"] == tag
        assert service["pull_policy"] == "never"
        assert service["ports"] == ["127.0.0.1:3001:8080"]
        assert "build" not in service

    def test_the_unit_starts_it_behind_the_health_gate_and_the_site_proxies_to_it(
        self, make, docker, store, tmp_path, web
    ):
        deployer = make(IMAGE)
        deployer.deploy()

        assert FakeServices.started == [APP]
        site = web.config_path(DOMAIN).read_text()
        assert "127.0.0.1:3001" in site
        row = store.get_app(DOMAIN)
        assert row is not None
        assert row.app_type == AppType.CONTAINER.value
        assert row.port == 3001
        assert row.status == AppStatus.RUNNING.value
        assert row.source == "ghcr.io/owner/app:1.4"

    def test_the_settings_and_the_digest_are_kept(self, make, docker, store):
        make(IMAGE).deploy()

        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None
        assert kept.image == "ghcr.io/owner/app:1.4"
        assert kept.digest == DIGEST

    def test_the_release_is_recorded_active_and_the_deployment_names_it(self, make, docker, store):
        deployer = make(IMAGE)
        deployer.deploy()

        [(release, status)] = releases(store)
        assert status == ReleaseStatus.ACTIVE.value
        record = store.get_release(store.get_app(DOMAIN).id, release)  # type: ignore[union-attr]
        assert record is not None
        assert record.path == image_tag(APP, release)
        history = store.list_deployments(DOMAIN)
        assert history[0].release_id == release
        assert history[0].status == "success"

    def test_the_port_the_image_exposes_is_used_when_none_was_given(
        self, make, docker, store, tmp_path
    ):
        docker.exposed = {"3000/tcp": {}}

        make(replace(IMAGE, container_port=None)).deploy()

        assert compose_text(tmp_path)["services"]["app"]["ports"] == ["127.0.0.1:3001:3000"]
        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.container_port == 3000

    def test_the_environment_file_exists_for_the_container_to_read(self, make, docker, tmp_path):
        make(IMAGE, env_vars={"GREETING": "hello"}).deploy()

        env = root_of(tmp_path) / ".env"
        assert "GREETING" in env.read_text()
        assert env.stat().st_mode & 0o777 == 0o600

    def test_no_environment_still_leaves_an_empty_file_with_the_right_mode(
        self, make, docker, tmp_path
    ):
        make(IMAGE).deploy()

        env = root_of(tmp_path) / ".env"
        assert env.exists() and env.stat().st_mode & 0o777 == 0o600

    def test_resource_limits_become_the_containers_limits(self, make, docker, tmp_path):
        make(IMAGE, memory_max_mb=512, cpu_quota_percent=100, tasks_max=100).deploy()

        service = compose_text(tmp_path)["services"]["app"]
        assert service["deploy"]["resources"]["limits"] == {"memory": "512M", "cpus": "1.00"}
        assert service["pids_limit"] == 100

    def test_the_limits_are_recorded_on_the_application(self, make, docker, store):
        make(IMAGE, memory_max_mb=512, resource_limits_given=True).deploy()

        row = store.get_app(DOMAIN)
        assert row is not None and row.memory_max_mb == 512

    def test_a_hook_runs_in_a_container_of_the_new_image(self, make, docker, store):
        deployer = make(IMAGE)
        deployer.deploy()
        store.set_app_hooks(
            DOMAIN, "hooks:\n  pre_deploy:\n    - run: ./migrate.sh\n", updated_by="test"
        )

        update(store, docker).update()

        runs = [c for c in docker.compose_calls() if c[:2] == ("run", "--rm")]
        assert runs and runs[-1][-1] == "./migrate.sh" and "app" in runs[-1]

    def test_without_a_port_one_on_loopback_is_found_and_the_site_proxies_to_it(
        self, make, docker, store, tmp_path, web
    ):
        make(IMAGE, port=None).deploy()

        row = store.get_app(DOMAIN)
        assert row is not None and row.port is not None
        service = compose_text(tmp_path)["services"]["app"]
        assert service["ports"] == [f"127.0.0.1:{row.port}:8080"]
        assert f"127.0.0.1:{row.port}" in web.config_path(DOMAIN).read_text()

    def test_a_port_another_application_owns_is_not_given_again(
        self, make, docker, store, tmp_path
    ):
        from noust.core.store import App

        taken = store.create_app(App(domain="other.example.com", app_type="nodejs", port=3000))

        make(IMAGE, port=None).deploy()

        row = store.get_app(DOMAIN)
        assert taken.port == 3000
        assert row is not None and row.port != 3000

    def test_the_apache_web_server_is_refused_plainly(self, make):
        with pytest.raises(DeploymentError):
            make(IMAGE, webserver="apache")


class TestADockerfile:
    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        root = root_of(tmp_path)
        root.mkdir(parents=True)
        (root / ".git").mkdir()
        (root / "Dockerfile").write_text("FROM scratch\nEXPOSE 9000\n")
        return root

    def deployer(self, make, **options) -> ContainerDeployer:
        deployer = make(
            None, source="https://example.com/shop.git", replace_existing=True, **options
        )
        deployer.source_already_fetched = True
        return deployer

    def test_it_builds_the_dockerfile_into_the_image_of_the_release(
        self, make, docker, store, tmp_path, tree
    ):
        docker.commit = "ab12cd3"

        self.deployer(make).deploy()

        build = docker.docker_calls("build")[0]
        [(release, _)] = releases(store)
        assert release.endswith("-ab12cd3")
        assert build[build.index("--tag") + 1] == image_tag(APP, release)
        assert build[build.index("--file") + 1] == str(tree / "Dockerfile")
        assert running_image(tmp_path) == image_tag(APP, release)
        # Built here, so nothing is pulled.
        assert docker.docker_calls("pull") == []

    def test_the_port_the_dockerfile_exposes_is_the_containers(
        self, make, docker, store, tmp_path, tree
    ):
        self.deployer(make).deploy()

        assert compose_text(tmp_path)["services"]["app"]["ports"] == ["127.0.0.1:3001:9000"]
        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.container_port == 9000

    def test_an_env_file_that_is_a_link_in_the_repository_is_not_followed(
        self, make, docker, tmp_path, tree
    ):
        outside = tmp_path / "host-secret"
        outside.write_text("ROOT_ONLY=1\n")
        (tree / ".env").symlink_to(outside)

        self.deployer(make).deploy()

        env = tree / ".env"
        assert not env.is_symlink()
        assert env.read_text() == ""
        assert outside.read_text() == "ROOT_ONLY=1\n"

    def test_a_port_the_operator_gave_is_not_overridden(self, make, docker, tmp_path, tree):
        deployer = make(
            ContainerSettings(container_port=4000),
            source="https://example.com/shop.git",
            replace_existing=True,
        )
        deployer.source_already_fetched = True
        deployer.deploy()

        assert compose_text(tmp_path)["services"]["app"]["ports"] == ["127.0.0.1:3001:4000"]

    def test_a_build_argument_and_a_secret_reach_the_build_and_the_secret_not_argv(
        self, make, docker, store, tmp_path, tree
    ):
        secrets = SecretStore(root=tmp_path / "store" / "secrets")
        secrets.write(build_secret_name(APP, "npmrc"), "//registry:_authToken=SECRETVALUE")
        settings = ContainerSettings(
            build_args={"NODE_ENV": "production"}, build_secrets=("npmrc",), target="runtime"
        )
        deployer = make(settings, source="https://example.com/shop.git", replace_existing=True)
        deployer.source_already_fetched = True

        deployer.deploy()

        build = docker.docker_calls("build")[0]
        assert "NODE_ENV=production" in build
        assert build[build.index("--target") + 1] == "runtime"
        assert f"id=npmrc,src={secrets.path(build_secret_name(APP, 'npmrc'))}" in build
        assert "SECRETVALUE" not in " ".join(" ".join(c) for c in docker.calls)

    def test_a_repository_without_a_dockerfile_is_built_by_railpack_when_it_is_installed(
        self, make, docker, store, tmp_path, tree
    ):
        (tree / "Dockerfile").unlink()
        docker.script(["railpack", "--version"], stdout="railpack version 0.40.1")

        self.deployer(make).deploy()

        assert any(call[:2] == ("railpack", "prepare") for call in docker.calls)
        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.method == "railpack"

    def test_the_method_an_operator_chose_is_never_second_guessed(
        self, make, docker, tmp_path, tree
    ):
        (tree / "Dockerfile").unlink()
        docker.script(["railpack", "--version"], stdout="railpack version 0.40.1")
        deployer = make(
            ContainerSettings(method="dockerfile"),
            source="https://example.com/shop.git",
            replace_existing=True,
        )
        deployer.source_already_fetched = True

        with pytest.raises(DeploymentError) as caught:
            deployer.deploy()

        assert "No Dockerfile" in caught.value.message
        assert not any(call[:2] == ("railpack", "prepare") for call in docker.calls)


class TestAFailedFirstDeployment:
    def test_a_container_that_does_not_answer_undoes_the_application(
        self, make, docker, store, tmp_path, web
    ):
        docker.healthy = False

        with pytest.raises(DeploymentError) as caught:
            make(IMAGE).deploy()

        assert "did not pass its health check" in caught.value.message
        assert store.get_app(DOMAIN) is None
        assert not root_of(tmp_path).exists()
        assert not web.config_path(DOMAIN).exists()
        # The image the release made goes with it.
        assert docker.images == set()

    def test_the_account_it_was_given_is_forgotten_with_it(self, make, docker, store, tmp_path):
        secrets = SecretStore(root=tmp_path / "store" / "secrets")
        secrets.write(registry_secret_name(APP), PASSWORD)
        docker.healthy = False
        settings = replace(
            IMAGE,
            registry_server="ghcr.io",
            registry_username="ci",
            registry_secret=registry_secret_name(APP),
        )

        with pytest.raises(DeploymentError):
            make(settings).deploy()

        assert secrets.read(registry_secret_name(APP)) is None

    def test_a_failed_pull_forgets_it_too_though_nothing_was_deployed(
        self, make, docker, store, tmp_path
    ):
        secrets = SecretStore(root=tmp_path / "store" / "secrets")
        secrets.write(registry_secret_name(APP), PASSWORD)
        docker.fail.add("pull")
        settings = replace(
            IMAGE,
            registry_server="ghcr.io",
            registry_username="ci",
            registry_secret=registry_secret_name(APP),
        )

        with pytest.raises(DockerError):
            make(settings).deploy()

        assert store.get_app(DOMAIN) is None
        assert secrets.read(registry_secret_name(APP)) is None

    def test_the_gate_reads_the_check_the_operator_chose_before_the_first_start(
        self, make, docker, store, monkeypatch
    ):
        probed: list[str] = []

        def probe(url: str, **kwargs) -> bool:
            probed.append(url)
            return True

        monkeypatch.setattr(docker_compose, "wait_until_healthy", probe)

        make(IMAGE, initial_health=("/healthz", "200", 30)).deploy()

        assert probed and probed[0].endswith("/healthz")


class TestUpdating:
    @pytest.fixture
    def deployed(self, make, docker, store, tmp_path) -> str:
        make(IMAGE).deploy()
        serving_now(docker, tmp_path)
        return releases(store)[0][0]

    def test_an_update_pulls_again_and_runs_a_new_release(self, deployed, docker, store, tmp_path):
        update(store, docker).update()

        assert len(docker.docker_calls("pull")) == 2
        ids = [release for release, _ in releases(store)]
        assert len(ids) == 2 and deployed in ids
        assert running_image(tmp_path) == image_tag(APP, ids[0])
        assert running_image(tmp_path) != image_tag(APP, deployed)

    def test_the_new_release_is_active_and_the_old_one_superseded(
        self, deployed, docker, store, tmp_path
    ):
        update(store, docker).update()

        statuses = dict(releases(store))
        assert statuses[deployed] == ReleaseStatus.SUPERSEDED.value
        assert [s for s in statuses.values() if s == ReleaseStatus.ACTIVE.value] == ["active"]

    def test_the_old_image_is_kept_so_going_back_is_instant(
        self, deployed, docker, store, tmp_path
    ):
        update(store, docker).update()

        assert image_tag(APP, deployed) in docker.images

    def test_the_container_is_recreated_from_the_compose_file_behind_the_gate(
        self, deployed, docker, store, tmp_path
    ):
        update(store, docker).update()

        assert any(c[0] == "up" and "--remove-orphans" in c for c in docker.compose_calls())

    def test_the_update_is_recorded_with_the_release_it_built(
        self, deployed, docker, store, tmp_path
    ):
        update(store, docker).update()

        newest = store.list_deployments(DOMAIN)[0]
        assert newest.release_id == releases(store)[0][0]
        assert newest.status == "success"

    def test_a_pull_that_fails_recreates_nothing_and_leaves_the_release_that_serves(
        self, deployed, docker, store, tmp_path
    ):
        docker.fail.add("pull")
        before = compose_text(tmp_path)

        with pytest.raises(DockerError):
            update(store, docker).update()

        assert compose_text(tmp_path) == before
        assert not [c for c in docker.compose_calls() if c[0] == "up"]
        assert dict(releases(store))[deployed] == ReleaseStatus.ACTIVE.value

    def test_a_missing_compose_file_is_written_again_for_the_release_that_serves(
        self, deployed, docker, store, tmp_path
    ):
        (root_of(tmp_path) / COMPOSE_FILE).unlink()

        update(store, docker).update()

        assert (root_of(tmp_path) / COMPOSE_FILE).is_file()

    def test_an_application_without_settings_says_so(self, make, docker, store, tmp_path):
        store.create_app(
            container_module.App(
                domain=DOMAIN, app_type="container", app_path=str(root_of(tmp_path))
            )
        )

        with pytest.raises(DeploymentError) as caught:
            update(store, docker).update()

        assert "no container settings" in caught.value.message


class TestAnUpdateThatDoesNotAnswer:
    @pytest.fixture
    def deployed(self, make, docker, store, tmp_path) -> str:
        make(IMAGE).deploy()
        serving_now(docker, tmp_path)
        return releases(store)[0][0]

    def failing(self, docker: Docker) -> None:
        """The new container does not answer; the one that served does."""
        docker.healthy = False
        docker.on_up.append(lambda args: setattr(docker, "healthy", "--no-build" in args))

    def test_what_served_is_put_back_by_its_image(self, deployed, docker, store, tmp_path):
        self.failing(docker)

        with pytest.raises(RolledBackError) as caught:
            update(store, docker).update()

        assert "are running again" in caught.value.message
        # The compose file names the release that served, not the one that failed.
        assert running_image(tmp_path) == image_tag(APP, deployed)
        ups = [c for c in docker.compose_calls() if c[0] == "up"]
        assert "--no-build" in ups[-1]

    def test_going_back_needs_no_build_and_no_pull(self, deployed, docker, store, tmp_path):
        self.failing(docker)
        pulls = len(docker.docker_calls("pull"))

        with pytest.raises(RolledBackError):
            update(store, docker).update()

        # One pull for the failed release; none to go back.
        assert len(docker.docker_calls("pull")) == pulls + 1
        assert docker.docker_calls("build") == []

    def test_the_release_that_failed_is_marked_and_its_image_removed(
        self, deployed, docker, store, tmp_path
    ):
        self.failing(docker)

        with pytest.raises(RolledBackError):
            update(store, docker).update()

        statuses = dict(releases(store))
        failed = [release for release, status in statuses.items() if status == "failed"]
        assert len(failed) == 1 and failed[0] != deployed
        assert statuses[deployed] == ReleaseStatus.ACTIVE.value
        assert image_tag(APP, failed[0]) not in docker.images
        assert image_tag(APP, deployed) in docker.images

    def test_the_error_carries_the_probes_and_what_the_container_printed(
        self, deployed, docker, store, tmp_path
    ):
        self.failing(docker)

        with pytest.raises(RolledBackError) as caught:
            update(store, docker).update()

        assert "listening on 8080" in caught.value.details
        assert "journal line" in caught.value.details

    def test_the_application_is_running_again_afterwards(self, deployed, docker, store, tmp_path):
        self.failing(docker)

        with pytest.raises(RolledBackError):
            update(store, docker).update()

        row = store.get_app(DOMAIN)
        assert row is not None and row.status == AppStatus.RUNNING.value

    def test_the_failed_update_is_in_the_history_as_failed(self, deployed, docker, store, tmp_path):
        self.failing(docker)

        with pytest.raises(RolledBackError):
            update(store, docker).update()

        assert store.list_deployments(DOMAIN)[0].status in ("failed", "rolled_back")


class TestRetention:
    def test_images_past_the_retention_are_removed_with_their_records(
        self, make, docker, store, tmp_path
    ):
        make(IMAGE).deploy()
        store.set_keep_releases(DOMAIN, 2)
        for _ in range(3):
            serving_now(docker, tmp_path)
            update(store, docker).update()

        ids = [release for release, _ in releases(store)]
        # The two newest, which hold the active release and the one a rollback goes to.
        assert len(ids) == 2
        assert docker.images >= {image_tag(APP, release) for release in ids}
        assert len({i for i in docker.images if i.startswith(f"noust-{APP}:")}) == 2

    def test_the_release_a_rollback_goes_to_is_kept_whatever_the_retention(
        self, make, docker, store, tmp_path
    ):
        make(IMAGE).deploy()
        store.set_keep_releases(DOMAIN, 1)
        serving_now(docker, tmp_path)

        update(store, docker).update()

        assert len(releases(store)) == 2
        assert len({i for i in docker.images if i.startswith(f"noust-{APP}:")}) == 2


class TestLimits:
    @pytest.fixture
    def deployed(self, make, docker, store, tmp_path) -> None:
        make(IMAGE).deploy()
        serving_now(docker, tmp_path)

    def test_new_limits_rewrite_the_file_and_recreate_the_container(
        self, deployed, docker, store, tmp_path
    ):
        from noust.managers.service_manager import ResourceLimits

        ok, _ = update(store, docker).change_limits(ResourceLimits(memory_max_mb=256), restart=True)

        assert ok is True
        service = compose_text(tmp_path)["services"]["app"]
        assert service["deploy"]["resources"]["limits"] == {"memory": "256M"}
        assert any(c[0] == "up" for c in docker.compose_calls())

    def test_without_a_restart_only_the_file_changes(self, deployed, docker, store, tmp_path):
        from noust.managers.service_manager import ResourceLimits

        update(store, docker).change_limits(ResourceLimits(memory_max_mb=256), restart=False)

        assert not [c for c in docker.compose_calls() if c[0] == "up"]
        assert compose_text(tmp_path)["services"]["app"]["deploy"]

    def test_limits_the_container_cannot_start_under_are_taken_back(
        self, deployed, docker, store, tmp_path
    ):
        from noust.managers.service_manager import ResourceLimits

        # Only the first recreation (under the new limits) does not answer.
        recreations: list[int] = []
        docker.healthy = False
        docker.on_up.append(
            lambda args: (recreations.append(1), setattr(docker, "healthy", len(recreations) > 1))
        )
        ok, evidence = update(store, docker).change_limits(
            ResourceLimits(memory_max_mb=64), restart=True
        )

        assert ok is False and evidence
        assert len(recreations) == 2
        assert "deploy" not in compose_text(tmp_path)["services"]["app"]


class TestTakingItDown:
    def test_every_image_of_the_application_goes(self, make, docker, store, tmp_path):
        make(IMAGE).deploy()
        serving_now(docker, tmp_path)
        update(store, docker).update()
        assert any(i.startswith(f"noust-{APP}:") for i in docker.images)

        app = store.get_app(DOMAIN)
        assert app is not None
        removed = stack_deployer(app, runner=docker).remove_kept_images()

        assert not [i for i in docker.images if i.startswith(f"noust-{APP}:")]
        assert len([r for r in removed if r.startswith(f"noust-{APP}:")]) == 2
