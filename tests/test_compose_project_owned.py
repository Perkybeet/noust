# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whose a Compose project is: the application's own name, recorded by the store.

Compose names the project after the directory of the compose file, so two applications whose file is in a
subdirectory of the same name (``docker/compose.yml``) ran as one project: the second recreated the
first's services, mounted its volumes and, with ``--remove-orphans``, removed the rest. A new application
now gets its own name as its project, recorded in the store, and every command and its unit pass it with
``-p``; what a stack already had is read from the containers of the project that run from the application's
tree, and not from whatever carries the label.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker import grants
from noust.core.runner import FakeRunner
from noust.core.store import App, AppType, NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import compose_privileges
from noust.deployers.helpers.registration import StoreRegistrar
from tests.support.docker_grants_samples import FIRST, SECOND, WEB, ps_row


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def stack(root: Path, store: NoustStore, domain: str) -> DockerComposeDeployer:
    """A deployer whose compose file is in ``docker/``, as two applications' can be."""
    app = root / domain.replace(".", "-")
    (app / "docker").mkdir(parents=True)
    (app / "docker" / "compose.yml").write_text("services:\n  web:\n    image: nginx\n")
    deployer = DockerComposeDeployer(runner=FakeRunner())
    deployer.configure(domain, "src", app_path=app, compose_file="docker/compose.yml")
    deployer.store = store
    deployer._discover_compose_file()
    return deployer


class TestANewApplicationRunsAsItself:
    def test_two_applications_with_the_file_in_one_subdirectory_run_as_two_projects(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        first = stack(tmp_path, store, "shop.example.com")
        second = stack(tmp_path, store, "evil.example.org")

        compose_privileges.own_project(first, None)
        compose_privileges.own_project(second, None)

        assert first._compose("up")[:3] == ["docker", "compose", "-p"]
        assert first._compose("up")[3] == "shop-example-com"
        assert second._compose("up")[3] == "evil-example-org"

    def test_the_project_is_what_the_guard_judges_the_file_against(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        deployer = stack(tmp_path, store, "shop.example.com")

        compose_privileges.own_project(deployer, None)

        assert compose_privileges.guard_context(deployer).project == "shop-example-com"

    def test_an_application_that_exists_keeps_the_project_it_has(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        deployer = stack(tmp_path, store, "shop.example.com")
        existing = store.create_app(
            App(
                domain="shop.example.com",
                app_type=AppType.DOCKER_COMPOSE.value,
                app_path=str(deployer.app_path),
            )
        )

        compose_privileges.own_project(deployer, existing)

        # What Compose derives from the directory of the file, as it always did: renaming a
        # project is renaming its volumes.
        assert deployer._compose("up")[3] == "docker"

    def test_the_store_records_the_project_of_a_new_row_and_keeps_that_of_an_old_one(
        self, store: NoustStore
    ) -> None:
        def register(project: str) -> App:
            return StoreRegistrar(store).register_app(
                domain="shop.example.com",
                app_type=AppType.DOCKER_COMPOSE.value,
                source="src",
                branch=None,
                port=None,
                app_path=Path("/var/www/apps/shop-example-com"),
                webserver="nginx",
                ssl_enabled=False,
                status="running",
                is_static=False,
                env_vars={},
                compose_project=project,
            )

        created = register("shop-example-com")
        again = register("other")

        assert created.compose_project == "shop-example-com"
        assert again.domain == "shop.example.com"
        stored = store.get_app("shop.example.com")
        assert stored is not None and stored.compose_project == "shop-example-com"


class TestWhatAStackHadIsWhatRunsFromItsTree:
    PROJECT = "docker"

    def machine(self, tree: Path, elsewhere: Path) -> FakeRunner:
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"],
            stdout="\n".join(
                [
                    ps_row(
                        FIRST,
                        "mine",
                        project=self.PROJECT,
                        service="web",
                        working_dir=str(tree / "docker"),
                    ),
                    ps_row(
                        SECOND,
                        "theirs",
                        project=self.PROJECT,
                        service="web",
                        working_dir=str(elsewhere / "docker"),
                    ),
                ]
            ),
        )
        runner.script(["docker", "inspect"], stdout=WEB)
        return runner

    def test_a_container_of_the_project_that_runs_from_another_tree_is_not_the_stacks(
        self, tmp_path: Path
    ) -> None:
        tree, elsewhere = tmp_path / "mine", tmp_path / "theirs"
        runner = self.machine(tree, elsewhere)

        found = grants.read_project_grants(runner, self.PROJECT, within=tree)

        inspection = next(call for call in runner.calls if call[:2] == ("docker", "inspect"))
        assert FIRST in inspection and SECOND not in inspection
        assert len(found) >= 0

    def test_without_a_tree_every_container_of_the_project_counts_as_before(
        self, tmp_path: Path
    ) -> None:
        runner = self.machine(tmp_path / "mine", tmp_path / "theirs")

        grants.read_project_grants(runner, self.PROJECT)

        inspection = next(call for call in runner.calls if call[:2] == ("docker", "inspect"))
        assert FIRST in inspection and SECOND in inspection

    def test_a_container_that_says_nothing_of_where_it_ran_is_not_proven_to_be_another_s(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"], stdout=ps_row(FIRST, "mine", project=self.PROJECT, service="web")
        )
        runner.script(["docker", "inspect"], stdout=WEB)

        grants.read_project_grants(runner, self.PROJECT, within=tmp_path)

        inspection = next(call for call in runner.calls if call[:2] == ("docker", "inspect"))
        assert FIRST in inspection

    def test_the_tree_itself_and_what_is_below_it_are_inside(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"],
            stdout="\n".join(
                [
                    ps_row(
                        FIRST, "a", project=self.PROJECT, service="web", working_dir=str(tmp_path)
                    ),
                    ps_row(
                        SECOND,
                        "b",
                        project=self.PROJECT,
                        service="web",
                        working_dir=str(tmp_path / "docker"),
                    ),
                ]
            ),
        )
        runner.script(["docker", "inspect"], stdout=WEB)

        grants.read_project_grants(runner, self.PROJECT, within=tmp_path)

        inspection = next(call for call in runner.calls if call[:2] == ("docker", "inspect"))
        assert FIRST in inspection and SECOND in inspection

    def test_a_sibling_directory_with_the_same_prefix_is_not_inside(self, tmp_path: Path) -> None:
        tree = tmp_path / "shop"
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"],
            stdout=ps_row(
                FIRST, "a", project=self.PROJECT, service="web", working_dir=str(tmp_path / "shop2")
            ),
        )
        runner.script(["docker", "inspect"], stdout=WEB)

        grants.read_project_grants(runner, self.PROJECT, within=tree)

        assert [call for call in runner.calls if call[:2] == ("docker", "inspect")] == []


class TestWhichProjectATreeRunsAs:
    """What the labels of the containers made from a tree say its project is, when no file can."""

    def listing(self, runner: FakeRunner, *rows: str) -> FakeRunner:
        runner.script(["docker", "ps"], stdout="\n".join(rows))
        return runner

    def test_the_project_of_the_containers_made_from_the_tree_is_the_one_asked_for(
        self, tmp_path: Path
    ) -> None:
        runner = self.listing(
            FakeRunner(),
            ps_row(FIRST, "a", project="legacy-shop", service="web", working_dir=str(tmp_path)),
            ps_row(
                SECOND,
                "b",
                project="legacy-shop",
                service="db",
                working_dir=str(tmp_path / "docker"),
            ),
        )

        assert grants.read_tree_projects(runner, tmp_path) == ("legacy-shop",)

    def test_another_tree_s_containers_and_the_ones_that_say_nothing_of_where_they_ran_are_not_the_trees(
        self, tmp_path: Path
    ) -> None:
        # A container with no working directory is not proven to be another stack's when the project
        # is known, but it is no proof of whose project it is when the project is what is asked.
        runner = self.listing(
            FakeRunner(),
            ps_row(
                FIRST,
                "a",
                project="theirs",
                service="web",
                working_dir=str(tmp_path / "elsewhere"),
            ),
            ps_row(SECOND, "b", project="nobody", service="web"),
        )

        assert grants.read_tree_projects(runner, tmp_path / "mine") == ()

    def test_a_container_made_for_run_is_not_the_stack_s(self, tmp_path: Path) -> None:
        runner = self.listing(
            FakeRunner(),
            ps_row(
                FIRST,
                "a",
                project="hook",
                service="migrate",
                oneoff=True,
                working_dir=str(tmp_path),
            ),
        )

        assert grants.read_tree_projects(runner, tmp_path) == ()

    def test_two_projects_are_both_said_so_that_nobody_guesses(self, tmp_path: Path) -> None:
        runner = self.listing(
            FakeRunner(),
            ps_row(FIRST, "a", project="old-name", service="web", working_dir=str(tmp_path)),
            ps_row(SECOND, "b", project="new-name", service="web", working_dir=str(tmp_path)),
        )

        assert grants.read_tree_projects(runner, tmp_path) == ("new-name", "old-name")

    def test_a_label_that_cannot_be_a_project_is_never_returned(self, tmp_path: Path) -> None:
        runner = self.listing(
            FakeRunner(),
            ps_row(FIRST, "a", project="x; reboot", service="web", working_dir=str(tmp_path)),
        )

        assert grants.read_tree_projects(runner, tmp_path) == ()

    def test_docker_that_does_not_answer_is_said_and_not_taken_for_none(
        self, tmp_path: Path
    ) -> None:
        from noust.core.docker.errors import DockerUnavailableError

        runner = FakeRunner()
        runner.script(["docker", "ps"], exit_code=1, stderr="Cannot connect to the Docker daemon")

        with pytest.raises(DockerUnavailableError):
            grants.read_tree_projects(runner, tmp_path)
