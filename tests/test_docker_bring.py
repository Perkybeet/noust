# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing a stack that already runs to Noust, from the Docker zone.

The work is ``compose_adopt`` (adopting touches nothing that runs); what is held here is what the zone adds
to it. The stack is the one Docker listed, and its directory and its compose file are what its containers
say, never what a person typed (the name the application will have is the only thing typed); only what nobody
of Noust made is brought; what cannot be adopted is said before anything is tried (a folder that is not on
this server, a stack started from several files); and the preview says what is registered and what is not
changed before it is done.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.docker import names
from noust.core.docker.errors import DockerBlockedError, DockerNotFoundError, DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import AppRecord, Registry
from noust.core.exceptions import DomainError
from noust.core.store import App, NoustStore
from noust.core.teams import Scope
from noust.managers.docker_bring import StackBringer, proposed_name
from tests.support.docker_bring import Adoption
from tests.support.docker_machine import SHOP, DockerMachine


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_type="docker-compose", app_path=SHOP))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "stacks" / "nextcloud"
    path.mkdir(parents=True)
    (path / "compose.yaml").write_text("services: {}\n")
    return path


def machine(folder: Path, files: str | None = None) -> DockerMachine:
    box = DockerMachine()
    listed = files if files is not None else str(folder / "compose.yaml")
    for name, service, policy in (
        ("nextcloud-app-1", "app", "unless-stopped"),
        ("nextcloud-db-1", "db", "on-failure"),
    ):
        box.box(
            name,
            project="nextcloud",
            service=service,
            working_dir=str(folder),
            config_files=listed,
            mounts=f"nextcloud_{service}",
            networks="nextcloud_default",
            policy=policy,
        )
    box.box(
        "nextcloud-setup-1",
        project="nextcloud",
        service="setup",
        working_dir=str(folder),
        config_files=listed,
        state="exited",
        status="Exited (0) 30 days ago",
        exit_code=0,
        policy="no",
    )
    box.volume("nextcloud_app", project="nextcloud", volume="app")
    box.volume("nextcloud_db", project="nextcloud", volume="db")
    box.network("nextcloud_default", project="nextcloud")
    box.box("shop-web-1", project="shop-example-com", working_dir=SHOP, policy="unless-stopped")
    box.box(
        "noust-db-pg16",
        project=names.instance_project("pg16"),
        image="postgres:16",
        instance="pg16",
    )
    return box


def bringer(box: DockerMachine, store: NoustStore, **options: Any) -> StackBringer:
    gate = DockerGate(
        box,
        store=store,
        registry=lambda: Registry(apps=(AppRecord("shop.example.com", SHOP),), engines=("pg16",)),
        disk=lambda path: None,
        **options,
    )
    return StackBringer(gate, store=store, runner=box)


class TestWhatIsFoundNotTyped:
    def test_the_directory_and_the_file_are_what_the_containers_say(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        bringer(machine(folder), store).preview("nextcloud")

        asked = adoption.planned[0]
        assert asked["path"] == folder
        assert asked["compose_file"] == "compose.yaml"
        assert asked["domain"] == "nextcloud"

    def test_a_file_in_a_subdirectory_is_named_from_the_stacks_directory(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (folder / "deploy").mkdir()
        (folder / "deploy" / "prod.yml").write_text("services: {}\n")
        adoption = Adoption(monkeypatch)

        bringer(machine(folder, files=str(folder / "deploy" / "prod.yml")), store).preview(
            "nextcloud"
        )

        assert adoption.planned[0]["compose_file"] == "deploy/prod.yml"

    def test_the_name_in_the_application_list_is_the_only_thing_typed(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        done = bringer(machine(folder), store).bring("nextcloud", name="cloud.example.com")

        assert adoption.planned[0]["domain"] == "cloud.example.com"
        assert done.plan.domain == "cloud.example.com"
        assert done.adopted is True
        assert done.deployment_id == 7

    def test_what_is_typed_for_the_stack_is_not_looked_for_unless_docker_listed_it(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        for typed in ("../nextcloud", "nextcloud; rm -rf /", "other"):
            with pytest.raises(DockerNotFoundError):
                bringer(machine(folder), store).preview(typed)

        assert adoption.planned == []


class TestWhatIsBrought:
    def test_only_what_nobody_of_noust_made(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        with pytest.raises(DockerBlockedError) as caught:
            bringer(machine(folder), store).preview("shop-example-com")

        assert caught.value.reason == "already_noust"
        assert "shop.example.com" in str(caught.value)
        assert adoption.planned == []

    def test_a_database_engines_stack_is_its_own_door(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        with pytest.raises(DockerOwnedError):
            bringer(machine(folder), store).preview(names.instance_project("pg16"))

    def test_a_person_of_a_team_does_not_find_it(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        with pytest.raises(DockerNotFoundError):
            bringer(
                machine(folder), store, scope=Scope.nothing(), sees_app=lambda domain: False
            ).preview("nextcloud")


class TestWhatCannotBeAdopted:
    def test_a_folder_that_is_not_on_this_server_is_said_first(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)
        box = machine(folder)
        for item in box.boxes.values():
            if item.project == "nextcloud":
                item.working_dir = "/var/lib/portainer/compose/12"
                item.config_files = "/var/lib/portainer/compose/12/docker-compose.yml"

        with pytest.raises(DockerBlockedError) as caught:
            bringer(box, store).preview("nextcloud")

        assert caught.value.reason == "no_folder"
        assert "/var/lib/portainer/compose/12" in caught.value.message
        assert adoption.planned == []

    def test_a_stack_started_from_several_files_is_said_first(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)
        listed = f"{folder / 'compose.yaml'},{folder / 'compose.override.yaml'}"

        with pytest.raises(DockerBlockedError) as caught:
            bringer(machine(folder, files=listed), store).preview("nextcloud")

        assert caught.value.reason == "several_files"
        assert adoption.planned == []

    def test_a_file_outside_the_folder_is_said_first(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        with pytest.raises(DockerBlockedError) as caught:
            bringer(machine(folder, files="/etc/elsewhere/compose.yaml"), store).preview(
                "nextcloud"
            )

        assert caught.value.reason == "file_outside"

    def test_a_name_an_application_has_is_taken(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        with pytest.raises(DockerBlockedError) as caught:
            bringer(machine(folder), store).preview("nextcloud", name="shop.example.com")

        assert caught.value.reason == "name_taken"
        assert adoption.planned == []

    def test_a_name_that_cannot_be_a_domain_is_refused(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        with pytest.raises(DomainError):
            bringer(machine(folder), store).preview("nextcloud", name="not a name!")

        assert adoption.planned == []


class TestWhatThePreviewSays:
    def test_it_says_what_was_checked_what_is_registered_and_what_is_not_changed(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        shown = bringer(machine(folder), store).preview("nextcloud")

        assert shown.name == "nextcloud"
        assert shown.folder == str(folder)
        assert shown.compose_file == "compose.yaml"
        assert shown.counts == {"containers": 3, "jobs": 1, "volumes": 2, "networks": 1}
        assert shown.recreates == []  # the rehearsal changes nothing
        assert shown.adopted is False
        assert "no repository" in shown.warnings[0]

    def test_what_would_not_come_back_after_a_restart_is_said_with_the_stack(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        shown = bringer(machine(folder), store).preview("nextcloud")

        assert [(item.container, item.policy) for item in shown.restart_loss] == [
            ("nextcloud-db-1", "on-failure")
        ]

    def test_what_the_rehearsal_would_change_is_said_in_composes_words(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch, changes=("Container nextcloud-app-1 Recreated",))

        shown = bringer(machine(folder), store).preview("nextcloud", accept_recreate=True)

        assert shown.recreates == ["Container nextcloud-app-1 Recreated"]

    def test_a_preview_registers_nothing(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        bringer(machine(folder), store).preview("nextcloud")

        assert adoption.adopted == []

    def test_what_to_call_it_is_proposed_from_the_project(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        assert bringer(machine(folder), store).preview("nextcloud").name == "nextcloud"


class TestBringing:
    def test_it_adopts_the_plan_it_was_shown(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        done = bringer(machine(folder), store).bring("nextcloud", accept_recreate=True)

        assert adoption.planned[0]["accept_recreate"] is True
        assert [plan.app_path for plan in adoption.adopted] == [folder]
        assert done.adopted is True

    def test_nothing_runs_that_would_change_the_stack(
        self, store: NoustStore, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)
        box = machine(folder)

        bringer(box, store).bring("nextcloud")

        assert box.changes() == []


class TestTheName:
    @pytest.mark.parametrize(
        ("project", "expected"),
        [
            ("nextcloud", "nextcloud"),
            ("My_Stack", "my-stack"),
            ("proggest", "proggest"),
            ("a" * 64, None),
            ("_", None),
        ],
    )
    def test_what_to_call_it_in_the_application_list(
        self, project: str, expected: str | None
    ) -> None:
        assert proposed_name(project) == expected
