# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Previews of a Docker Compose application (checklist B-31, B-32).

A preview of a stack is the same repository in a directory of its own, run from a copy of
its compose file that cannot collide with the original's: one published web port on the
preview's own loopback port, no fixed names, host paths redirected under its directory,
and volumes that are its own. The copy is written beside the original, made again at every
update, and never written over a file the repository owns.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import DockerComposeDeployer, stack_deployer
from noust.deployers.helpers import compose_preview
from noust.deployers.helpers.compose_preview import (
    DATA_DIR,
    derived_name,
    is_derived,
    isolate,
    original_of,
    render,
)

PARENT = "shop.example.com"
PREVIEW = "pr-7-shop-example-com.previews.example.com"
PORT = 4321

STACK: dict[str, Any] = {
    "name": "shop",
    "services": {
        "web": {
            "build": ".",
            "image": "shop-web:latest",
            "container_name": "shop-web",
            "ports": ["8080:3000", "9229:9229"],
            "depends_on": ["db"],
            "volumes": [
                "/srv/shop/uploads:/app/uploads",
                "./static:/app/static",
                "../shared:/app/shared",
                "cache:/app/cache",
                "/etc/shop/license.key:/app/license.key:ro",
            ],
        },
        "worker": {"build": ".", "ports": ["9000:9000"]},
        "db": {
            "image": "postgres:16",
            "container_name": "shop-db",
            "volumes": ["pgdata:/var/lib/postgresql/data", "/var/lib/shop-db:/backup"],
        },
    },
    "volumes": {"pgdata": {"external": True, "name": "shop_pgdata"}, "cache": {}},
    "networks": {"edge": {"external": True, "name": "traefik"}},
}


def isolated(
    document: Any = None, *, files: tuple[str, ...] = ("/etc/shop/license.key",)
) -> compose_preview.IsolatedStack:
    return isolate(
        document if document is not None else STACK,
        port=PORT,
        app_path=Path("/var/www/apps/pr-7"),
        compose_dir=Path("/var/www/apps/pr-7"),
        is_file=lambda path: path in files,
    )


class TestWhatIsIsolated:
    def test_only_the_web_root_is_published_on_the_previews_loopback_port(self) -> None:
        result = isolated()

        services = result.document["services"]
        assert services["web"]["ports"] == [f"127.0.0.1:{PORT}:3000"]
        assert "ports" not in services["worker"]
        assert (result.service, result.port, result.container_port) == ("web", PORT, 3000)

    def test_the_project_name_and_fixed_container_names_go(self) -> None:
        result = isolated()

        assert "name" not in result.document
        services = result.document["services"]
        assert "container_name" not in services["web"]
        assert "container_name" not in services["db"]

    def test_a_built_service_does_not_name_an_image_two_projects_would_tag(self) -> None:
        services = isolated().document["services"]

        assert "image" not in services["web"]
        assert services["db"]["image"] == "postgres:16"  # only pulled: shared, harmless

    def test_host_paths_are_redirected_under_the_previews_own_directory(self) -> None:
        volumes = isolated().document["services"]["web"]["volumes"]
        data = f"/var/www/apps/pr-7/{DATA_DIR}"

        assert f"{data}/srv/shop/uploads:/app/uploads" in volumes
        # Relative to the compose file, outside the checkout: it is the original's too.
        assert f"{data}/var/www/apps/shared:/app/shared" in volumes
        assert f"{data}/var/lib/shop-db:/backup" in isolated().document["services"]["db"]["volumes"]

    def test_the_previews_own_files_named_volumes_and_a_file_are_left_alone(self) -> None:
        volumes = isolated().document["services"]["web"]["volumes"]

        assert "./static:/app/static" in volumes
        assert "cache:/app/cache" in volumes
        # A key file is input, not data: redirecting it would mount an empty directory.
        assert "/etc/shop/license.key:/app/license.key:ro" in volumes

    def test_the_long_form_of_a_bind_is_redirected_too(self) -> None:
        document = {
            "services": {
                "web": {
                    "ports": ["3000:3000"],
                    "volumes": [{"type": "bind", "source": "/srv/data", "target": "/data"}],
                }
            }
        }

        volumes = isolated(document).document["services"]["web"]["volumes"]

        assert volumes == [
            {"type": "bind", "source": f"/var/www/apps/pr-7/{DATA_DIR}/srv/data", "target": "/data"}
        ]

    def test_a_mount_made_of_a_variable_is_said_not_to_be_isolated(self) -> None:
        document = {
            "services": {"web": {"ports": ["3000:3000"], "volumes": ["${DATA_DIR}/up:/data"]}}
        }

        result = isolated(document)

        assert result.document["services"]["web"]["volumes"] == ["${DATA_DIR}/up:/data"]
        assert any("DATA_DIR" in warning for warning in result.warnings)

    def test_volumes_and_networks_that_exist_outside_the_project_become_its_own(self) -> None:
        document = isolated().document

        assert document["volumes"]["pgdata"] == {}
        assert document["networks"]["edge"] == {}

    def test_the_original_document_is_not_changed(self) -> None:
        before = yaml.safe_dump(STACK)

        isolated()

        assert yaml.safe_dump(STACK) == before

    def test_what_was_done_is_said_for_the_deployment_log(self) -> None:
        changes = "\n".join(isolated().changes)

        assert "published on 127.0.0.1:4321" in changes
        assert "web: dropped its fixed container name" in changes
        assert "volume pgdata" in changes


class TestWhatCannotRunBesideItsOriginal:
    def test_the_hosts_network_is_refused(self) -> None:
        document = {"services": {"web": {"network_mode": "host", "ports": ["3000:3000"]}}}

        with pytest.raises(DeploymentError, match="host's network") as caught:
            isolated(document)

        assert caught.value.details

    def test_a_stack_without_a_web_port_has_nothing_to_open(self) -> None:
        with pytest.raises(DeploymentError, match="no web port") as caught:
            isolated({"services": {"worker": {"image": "x"}}})

        assert caught.value.details

    def test_a_document_without_services_is_refused(self) -> None:
        with pytest.raises(DeploymentError, match="No services"):
            isolated({"version": "3"})


class TestTheFileItIsWrittenTo:
    def test_it_is_named_beside_the_original(self) -> None:
        assert derived_name("docker-compose.yml") == "docker-compose.noust-preview.yml"
        assert derived_name("deploy/compose.prod.yaml") == "deploy/compose.prod.noust-preview.yml"
        assert is_derived("deploy/compose.prod.noust-preview.yml")
        assert not is_derived("compose.yml")

    def test_it_says_which_file_it_came_from(self) -> None:
        text = render(isolated(), domain=PREVIEW, original="deploy/compose.yaml")

        assert original_of(text) == "deploy/compose.yaml"
        assert PREVIEW in text.splitlines()[0]
        assert yaml.safe_load(text)["services"]["web"]["ports"] == [f"127.0.0.1:{PORT}:3000"]

    def test_a_file_that_is_not_ours_names_no_original(self) -> None:
        assert original_of("services:\n  web:\n    image: nginx\n") is None


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test's directory, installed as the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


def write_stack(app_path: Path, document: dict[str, Any] | None = None) -> Path:
    app_path.mkdir(parents=True, exist_ok=True)
    path = app_path / "docker-compose.yml"
    path.write_text(
        yaml.safe_dump(document or {"services": {"web": {"image": "x", "ports": ["8080:80"]}}})
    )
    return path


def preview_deployer(
    app_path: Path, runner: FakeRunner, *, port: int | None = PORT, **options: Any
) -> DockerComposeDeployer:
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(
        PREVIEW, str(app_path), app_path=app_path, port=port, preview_parent=PARENT, **options
    )
    return deployer


class TestTheDeployerRunsTheCopy:
    def test_a_preview_runs_the_copy_and_the_repository_file_is_untouched(
        self, tmp_path: Path, runner: FakeRunner
    ) -> None:
        original = write_stack(tmp_path)
        before = original.read_text()
        deployer = preview_deployer(tmp_path, runner)

        deployer._discover_compose_file()

        assert deployer.compose_path == tmp_path / "docker-compose.noust-preview.yml"
        assert original.read_text() == before
        copy = yaml.safe_load(deployer.compose_path.read_text())
        assert copy["services"]["web"]["ports"] == [f"127.0.0.1:{PORT}:80"]
        assert "-f" in deployer._compose("up") and str(deployer.compose_path) in deployer._compose(
            "up"
        )

    def test_without_a_parent_nothing_is_derived(self, tmp_path: Path, runner: FakeRunner) -> None:
        write_stack(tmp_path)
        deployer = DockerComposeDeployer(runner=runner)
        deployer.configure(PARENT, str(tmp_path), app_path=tmp_path)

        deployer._discover_compose_file()

        assert deployer.compose_path == tmp_path / "docker-compose.yml"
        assert not (tmp_path / "docker-compose.noust-preview.yml").exists()

    def test_the_copy_is_made_again_from_the_file_the_branch_has_now(
        self, tmp_path: Path, runner: FakeRunner
    ) -> None:
        write_stack(tmp_path)
        preview_deployer(tmp_path, runner)._discover_compose_file()
        write_stack(tmp_path, {"services": {"web": {"image": "y", "ports": ["8080:5000"]}}})
        # What an update does: the unit names the copy, the original is found through its marker.
        update = preview_deployer(tmp_path, runner, compose_file="docker-compose.noust-preview.yml")

        update._discover_compose_file()

        copy = yaml.safe_load((tmp_path / "docker-compose.noust-preview.yml").read_text())
        assert copy["services"]["web"]["ports"] == [f"127.0.0.1:{PORT}:5000"]
        assert update.compose_path == tmp_path / "docker-compose.noust-preview.yml"

    def test_a_named_compose_file_in_a_subdirectory_has_its_copy_beside_it(
        self, tmp_path: Path, runner: FakeRunner
    ) -> None:
        (tmp_path / "deploy").mkdir()
        (tmp_path / "deploy" / "prod.yml").write_text(
            yaml.safe_dump({"services": {"web": {"build": "..", "ports": ["80:80"]}}})
        )
        deployer = preview_deployer(tmp_path, runner, compose_file="deploy/prod.yml")

        deployer._discover_compose_file()

        assert deployer.compose_path == tmp_path / "deploy" / "prod.noust-preview.yml"

    def test_the_port_is_the_one_recorded_when_none_is_given(
        self, tmp_path: Path, runner: FakeRunner, store: NoustStore
    ) -> None:
        write_stack(tmp_path)
        store.create_app(
            App(
                domain=PREVIEW,
                app_type="docker-compose",
                source="s",
                port=5555,
                app_path=str(tmp_path),
            )
        )
        deployer = preview_deployer(tmp_path, runner, port=None)

        deployer._discover_compose_file()

        copy = yaml.safe_load(deployer.compose_path.read_text())
        assert copy["services"]["web"]["ports"] == ["127.0.0.1:5555:80"]

    def test_without_a_port_it_says_so(self, tmp_path: Path, runner: FakeRunner) -> None:
        write_stack(tmp_path)
        deployer = preview_deployer(tmp_path, runner, port=None)

        with pytest.raises(DeploymentError, match="needs a port"):
            deployer._discover_compose_file()

    def test_a_file_of_the_repository_with_the_name_of_the_copy_is_never_written_over(
        self, tmp_path: Path, runner: FakeRunner
    ) -> None:
        write_stack(tmp_path)
        theirs = tmp_path / "docker-compose.noust-preview.yml"
        theirs.write_text("services: {}\n")
        deployer = preview_deployer(tmp_path, runner)

        with pytest.raises(DeploymentError, match="in the repository"):
            deployer._discover_compose_file()

        assert theirs.read_text() == "services: {}\n"

    def test_a_link_with_the_name_of_the_copy_is_never_followed(
        self, tmp_path: Path, runner: FakeRunner
    ) -> None:
        write_stack(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}-outside.yml"
        outside.write_text("untouched\n")
        (tmp_path / "docker-compose.noust-preview.yml").symlink_to(outside)
        deployer = preview_deployer(tmp_path, runner)

        with pytest.raises(DeploymentError, match="in the repository"):
            deployer._discover_compose_file()

        assert outside.read_text() == "untouched\n"

    def test_a_stack_that_cannot_run_beside_its_original_fails_the_deploy_with_the_reason(
        self, tmp_path: Path, runner: FakeRunner
    ) -> None:
        write_stack(tmp_path, {"services": {"web": {"network_mode": "host"}}})
        deployer = preview_deployer(tmp_path, runner)

        with pytest.raises(DeploymentError, match="host's network"):
            deployer._discover_compose_file()

    def test_the_site_proxies_the_preview_not_the_routes_of_the_original(
        self, tmp_path: Path, runner: FakeRunner
    ) -> None:
        write_stack(tmp_path)
        (tmp_path / "noust.nginx.yaml").write_text(
            "routes:\n  - path: /api\n    port: 9000\n  - path: /\n    port: 8080\n"
        )
        deployer = preview_deployer(tmp_path, runner)
        deployer._discover_compose_file()
        deployer._parse_compose_services()

        template, context = deployer._site_template(with_ssl=False)

        assert template == "proxy"
        assert context["port"] == PORT


class TestTheRowIsLinkedFromTheStart:
    def test_a_registered_preview_is_a_preview_without_a_backup_before_every_push(
        self, tmp_path: Path, runner: FakeRunner, store: NoustStore
    ) -> None:
        write_stack(tmp_path)
        deployer = preview_deployer(
            tmp_path, runner, env_secret_marks={"API_KEY": True}, database_env={"DATABASE_URL": "x"}
        )
        deployer._discover_compose_file()
        deployer._parse_compose_services()

        deployer._register_app()

        row = store.get_app(PREVIEW)
        assert row is not None
        assert row.preview_parent == PARENT
        assert row.port == PORT
        assert row.env_secret_marks == {"API_KEY": True}
        assert row.backup_before_update is False
        # The database made for the stack is one of its variables.
        assert deployer.env_vars["DATABASE_URL"] == "x"

    def test_the_deployer_that_updates_a_preview_runs_its_copy_on_its_port(
        self, tmp_path: Path, runner: FakeRunner, store: NoustStore
    ) -> None:
        write_stack(tmp_path)
        row = store.create_app(
            App(
                domain=PREVIEW,
                app_type="docker-compose",
                source="s",
                port=PORT,
                app_path=str(tmp_path),
                preview_parent=PARENT,
            )
        )

        deployer = stack_deployer(row)

        assert deployer.preview_parent == PARENT
        assert deployer.port == PORT

    def test_the_deployer_of_an_ordinary_stack_is_not_a_preview(
        self, tmp_path: Path, runner: FakeRunner, store: NoustStore
    ) -> None:
        write_stack(tmp_path)
        row = store.create_app(
            App(
                domain=PARENT,
                app_type="docker-compose",
                source="s",
                port=8080,
                app_path=str(tmp_path),
            )
        )

        deployer = stack_deployer(row)

        assert deployer.preview_parent is None
