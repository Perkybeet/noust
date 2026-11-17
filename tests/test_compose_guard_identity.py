# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stack runs as its application's project and uses what is its own.

Compose names everything after the project: with the name of another application's project it
recreates that stack's services, removes the rest with ``--remove-orphans`` and mounts its
volumes. A volume or a network that is ``external``, a ``container_name`` and an ``image`` can be
another application's or Noust's own just as well. The store, which knows each application's
project, decides whose each is; what belongs to another is refused with no exception that can
allow it, what belongs to nobody is something the stack is given from outside.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.docker import names
from noust.core.docker.owners import OwnerIndex, Registry
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.compose_guard import GuardContext, inspect_compose
from noust.deployers.helpers.compose_owners import Owners

APP = Path("/var/www/apps/stack-example-com")


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store with two other applications: one that runs from its directory, one adopted."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(
            domain="other.example.com",
            app_type="docker-compose",
            app_path="/var/www/apps/other-example-com",
        )
    )
    adopted = App(domain="shop.example.com", app_type="docker-compose", app_path="/opt/shop")
    instance.create_app(adopted)
    instance.set_app_compose_project("shop.example.com", "proggest")
    yield instance
    NoustStore.reset_instance()


def judged(store: NoustStore, document: dict[str, Any], **context: Any) -> Any:
    """What the guard finds in ``document`` for the application ``stack.example.com``."""
    owners = Owners.of_store(store, "stack.example.com")
    guard = GuardContext(
        APP, domain="stack.example.com", project="stack-example-com", owners=owners, **context
    )
    return inspect_compose(document, APP, context=guard)


def document(**parts: Any) -> dict[str, Any]:
    """A compose file with one service and what else it is given."""
    service = parts.pop("service", {})
    return {"services": {"web": {"image": "nginx", **service}}, **parts}


class TestTheProject:
    """The project is the application's; a name of another's is refused."""

    @pytest.mark.parametrize("name", ["other-example-com", "proggest"])
    def test_the_project_of_another_application_cannot_be_taken(
        self, store: NoustStore, name: str
    ) -> None:
        findings = judged(store, document(name=name))

        taken = [s for s in findings.refused if "belongs to" in s]
        assert len(taken) == 1, findings
        assert taken[0] in findings.hard
        assert "Compose would take over what that project has" in taken[0]

    def test_what_noust_runs_for_a_database_engine_is_noust_s(self) -> None:
        owners = Owners(OwnerIndex(Registry(engines=("pg16",))), "stack.example.com")

        assert owners.other("project", names.instance_project("pg16")) == "Noust"
        assert owners.other("volume", names.instance_data_volume("pg16")) == "Noust"
        assert owners.other("container", names.instance_container("pg16")) == "Noust"

    def test_a_name_that_is_not_the_applications_own_is_refused_for_a_new_stack(
        self, store: NoustStore
    ) -> None:
        findings = judged(store, document(name="my-project"))

        assert findings.refused == [
            "the compose file names its own project my-project (name: or COMPOSE_PROJECT_NAME) "
            "and this application's is stack-example-com: everything the stack makes is named "
            "after its project"
        ]
        assert findings.hard == set()

    def test_the_applications_own_name_is_nothing(self, store: NoustStore) -> None:
        assert not judged(store, document(name="stack-example-com"))

    def test_a_name_in_the_project_env_is_judged_like_one_in_the_file(
        self, store: NoustStore
    ) -> None:
        findings = judged(store, document(), env_project="other-example-com")

        assert any(s in findings.hard for s in findings.refused)

    def test_a_name_made_of_variables_is_not_one_that_can_be_read(self, store: NoustStore) -> None:
        assert not judged(store, document(name="${PROJECT:-other-example-com}"))

    def test_the_pinned_project_of_an_adopted_stack_is_its_own(self, store: NoustStore) -> None:
        owners = Owners.of_store(store, "shop.example.com")
        context = GuardContext(Path("/opt/shop"), domain="shop.example.com", project="proggest")

        findings = inspect_compose(document(name="proggest"), Path("/opt/shop"), context=context)

        assert not findings
        assert owners.other("project", "proggest") is None


class TestWhatTheStackDoesNotMake:
    """A volume or a network that is external belongs to somebody."""

    @pytest.mark.parametrize(
        ("kind", "name"),
        [
            ("volumes", "other-example-com_data"),
            ("networks", "other-example-com_default"),
            ("volumes", "proggest_db"),
            ("networks", "proggest_default"),
            ("volumes", "noust-db-pg16-data"),
        ],
    )
    def test_one_of_another_application_or_of_noust_is_refused_for_good(
        self, store: NoustStore, kind: str, name: str
    ) -> None:
        findings = judged(
            store,
            document(**{kind: {"x": {"external": True, "name": name}}}),
        )

        assert len(findings.refused) == 1, findings
        assert "belongs to" in findings.refused[0]
        assert findings.refused[0] in findings.hard

    def test_the_key_names_it_when_the_file_gives_no_name(self, store: NoustStore) -> None:
        findings = judged(store, document(volumes={"other-example-com_data": {"external": True}}))

        assert findings.refused[0] in findings.hard

    def test_the_legacy_spelling_of_external_is_read_too(self, store: NoustStore) -> None:
        findings = judged(
            store, document(volumes={"d": {"external": {"name": "other-example-com_data"}}})
        )

        assert findings.refused[0] in findings.hard

    @pytest.mark.parametrize("kind", ["volumes", "networks"])
    def test_one_that_is_nobodys_is_something_given_from_outside(
        self, store: NoustStore, kind: str
    ) -> None:
        findings = judged(store, document(**{kind: {"shared": {"external": True}}}))

        assert len(findings.refused) == 1, findings
        assert "which this stack does not make" in findings.refused[0]
        assert findings.hard == set()

    def test_one_the_applications_own_project_made_is_its_own(self, store: NoustStore) -> None:
        findings = judged(
            store,
            document(volumes={"d": {"external": True, "name": "stack-example-com_data"}}),
        )

        assert not findings

    def test_a_volume_the_stack_makes_is_nothing(self, store: NoustStore) -> None:
        assert not judged(store, document(volumes={"data": {}}, service={"volumes": ["data:/d"]}))


class TestTheNamesAServiceClaims:
    """A container name and an image can be another application's."""

    def test_a_container_name_of_another_application_is_refused_for_good(
        self, store: NoustStore
    ) -> None:
        findings = judged(store, document(service={"container_name": "other-example-com-web-1"}))

        assert len(findings.refused) == 1
        assert findings.refused[0] in findings.hard

    @pytest.mark.parametrize(
        "image",
        [
            "other-example-com-web",
            "other-example-com-web:noust-20261116-120000-abc1234",
            "other-example-com-web@sha256:" + "a" * 64,
            "noust-other-example-com:20261116-120000-abc1234",
            "proggest-api",
        ],
    )
    def test_an_image_another_application_built_is_refused_for_good(
        self, store: NoustStore, image: str
    ) -> None:
        findings = judged(store, document(service={"image": image}))

        assert len(findings.refused) == 1, findings
        assert "built" in findings.refused[0]
        assert findings.refused[0] in findings.hard

    @pytest.mark.parametrize(
        "image",
        [
            "nginx",
            "ghcr.io/example/api:1.4",
            "registry.example.com:5000/team/api@sha256:" + "b" * 64,
            "stack-example-com-web",
            "${IMAGE}",
        ],
    )
    def test_any_other_image_is_nothing(self, store: NoustStore, image: str) -> None:
        assert not judged(store, document(service={"image": image}))

    def test_a_container_name_that_is_nobodys_is_nothing(self, store: NoustStore) -> None:
        assert not judged(store, document(service={"container_name": "my-reverse-proxy"}))


class TestEveryCommandPassesTheProject:
    """``-p`` is always there; a stack that names its own project keeps the name it has."""

    def deployer(self, tmp_path: Path, store: NoustStore, text: str, env: str = "") -> Any:
        compose = DockerComposeDeployer(runner=FakeRunner())
        app = tmp_path / "stack"
        app.mkdir()
        (app / "docker-compose.yml").write_text(text)
        if env:
            (app / ".env").write_text(env)
        compose.configure("stack.example.com", "src", app_path=app)
        compose.store = store
        compose.compose_path = app / "docker-compose.yml"
        return compose

    def test_a_plain_stack_is_passed_the_project_compose_derives(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = self.deployer(tmp_path, store, "services: {web: {image: x}}\n")

        assert compose._compose("ps")[:4] == ["docker", "compose", "-p", "stack"]

    def test_a_stack_that_names_its_own_project_is_passed_that_name(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = self.deployer(tmp_path, store, "name: Billing_App\nservices: {web: {image: x}}\n")

        assert compose._compose("ps")[:4] == ["docker", "compose", "-p", "billing_app"]

    def test_a_name_in_the_project_env_is_passed_too(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = self.deployer(
            tmp_path, store, "services: {web: {image: x}}\n", env="COMPOSE_PROJECT_NAME=shop\n"
        )

        assert compose._compose("ps")[:4] == ["docker", "compose", "-p", "shop"]

    def test_the_project_the_store_pins_wins_over_what_the_file_says(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        store.create_app(App(domain="stack.example.com", app_type="docker-compose"))
        store.set_app_compose_project("stack.example.com", "proggest")
        compose = self.deployer(tmp_path, store, "name: other-example-com\nservices: {}\n")

        assert compose._compose("ps")[:4] == ["docker", "compose", "-p", "proggest"]

    def test_a_name_made_of_variables_is_not_guessed(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = self.deployer(tmp_path, store, "name: ${PROJECT}\nservices: {web: {image: x}}\n")

        assert "-p" not in compose._compose("ps")


class TestTheDeployerHoldsTheIdentityInBothDirections:
    """A stack that already had a free external network goes on; one of another's never does."""

    def deployer(self, tmp_path: Path, store: NoustStore, said: list[str] | None = None) -> Any:
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout="", stderr="no", exit_code=1)
        compose = DockerComposeDeployer(runner=runner)
        compose.configure("stack.example.com", "src", app_path=tmp_path / "stack")
        compose.store = store
        if said is not None:
            compose.logger.attach_sink(said.append)
        return compose

    FREE = document(networks={"proxy": {"external": True}})
    TAKEN = document(volumes={"d": {"external": True, "name": "other-example-com_data"}})

    def test_a_new_stack_that_joins_a_network_nobody_made_is_refused(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        with pytest.raises(DeploymentError, match="asks for root"):
            self.deployer(tmp_path, store)._check_host_privileges(self.FREE, accepted=[])

    def test_the_stack_that_had_it_goes_on_with_a_warning(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        said: list[str] = []
        compose = self.deployer(tmp_path, store, said)

        compose._check_host_privileges(self.FREE, accepted=compose._refusals_in(self.FREE))

        assert any("which this stack does not make" in line for line in said)

    def test_an_exception_allows_it(self, tmp_path: Path, store: NoustStore) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="the proxy", store=store
        )

        self.deployer(tmp_path, store)._check_host_privileges(self.FREE, accepted=[])

    def test_what_belongs_to_another_application_is_never_allowed(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="trust me", store=store
        )

        with pytest.raises(DeploymentError, match="reaches what no stack may reach"):
            self.deployer(tmp_path, store)._check_host_privileges(self.TAKEN, accepted=[])

    def test_a_stack_that_already_used_it_is_not_stopped(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = self.deployer(tmp_path, store)

        compose._check_host_privileges(self.TAKEN, accepted=compose._refusals_in(self.TAKEN))
