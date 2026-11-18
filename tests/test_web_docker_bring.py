# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing a stack to Noust over HTTP.

What the console is written against: a preview says what would be registered and what is not changed and
registers nothing; bringing does it, as the application the person named; what cannot be adopted is refused
first with the reason as a code, and Compose's own words, when its rehearsal says starting the stack the way
Noust would changes something, are returned verbatim; and changing needs the permission, the one for
applications, sudo mode, and is in the audit trail.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import audit
from noust.core.runner import set_runner
from noust.core.server_text import say
from noust.core.store import NoustStore
from noust.deployers import compose_adopt
from noust.deployers.compose_adopt import AdoptionRefusedError
from noust.web.api import docker as docker_api
from noust.web.jobs import JobManager
from tests.support.docker_bring import Adoption
from tests.support.docker_web import DockerWorld, build


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "stacks" / "nextcloud"
    path.mkdir(parents=True)
    (path / "compose.yaml").write_text("services: {}\n")
    return path


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: Path) -> Iterator[DockerWorld]:
    JobManager.reset_instance()
    built = build(tmp_path, monkeypatch)
    for name, service, policy in (
        ("nextcloud-app-1", "app", "unless-stopped"),
        ("nextcloud-db-1", "db", "on-failure"),
    ):
        built.machine.box(
            name,
            image="nextcloud:29-apache" if service == "app" else "postgres:16",
            project="nextcloud",
            service=service,
            working_dir=str(folder),
            config_files=str(folder / "compose.yaml"),
            policy=policy,
        )
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        set_runner(None)
        JobManager.reset_instance()
        NoustStore.reset_instance()


def events(action: str) -> list[dict]:
    return audit.get_log().read(action=action)


class TestThePreview:
    def test_it_says_what_would_be_registered_and_registers_nothing(
        self, docker: DockerWorld, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={"preview": True})

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["stack"], body["name"], body["adopted"]) == ("nextcloud", "nextcloud", False)
        assert body["folder"] == str(folder)
        assert body["compose_file"] == "compose.yaml"
        assert body["recreates"] == []
        assert body["rehearsal"].startswith("[+] Running")
        assert [item["container"] for item in body["restart_loss"]] == ["nextcloud-db-1"]
        assert adoption.adopted == []
        assert events("docker.bring") == []

    def test_the_name_is_the_only_thing_typed(
        self, docker: DockerWorld, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        docker.send(
            "admin",
            "POST",
            "/stacks/nextcloud/bring",
            json={"preview": True, "name": "cloud.example.com"},
        )

        assert adoption.planned[0]["domain"] == "cloud.example.com"
        assert adoption.planned[0]["path"] == folder


class TestThePreviewAsksNoSudoMode:
    """Looking at what bringing a stack would do changes nothing, so it is read like the rest of the zone (item 189)."""

    PREVIEW = "/api/docker/stacks/nextcloud/bring/preview"

    @pytest.fixture
    def no_sudo(self) -> dict[str, str]:
        """An administrator's token that can never be in sudo mode: it reads, it cannot change."""
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )
        return {"Authorization": f"Bearer {issued['token']}"}

    def test_it_is_read_with_the_permission_to_read_docker_and_without_sudo_mode(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch, no_sudo: dict[str, str]
    ) -> None:
        Adoption(monkeypatch)

        for headers in (no_sudo, docker.world.as_("aud")):
            response = docker.world.client.post(self.PREVIEW, json={}, headers=headers)

            assert response.status_code == 200, response.text
            assert response.json()["adopted"] is False

    def test_what_is_applied_still_asks_for_sudo_mode(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch, no_sudo: dict[str, str]
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.world.client.post(
            "/api/docker/stacks/nextcloud/bring", json={}, headers=no_sudo
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert adoption.adopted == []

    def test_the_schema_says_it_is_a_read_so_a_central_asks_nobody_to_confirm_for_it(
        self, docker: DockerWorld
    ) -> None:
        paths = docker.world.client.get(
            "/api/openapi.json", headers=docker.world.as_("admin")
        ).json()["paths"]

        preview = paths["/api/docker/stacks/{name}/bring/preview"]["post"]
        apply = paths["/api/docker/stacks/{name}/bring"]["post"]
        assert preview["x-noust-permission"] == "docker.read"
        assert "x-noust-requires-elevation" not in preview
        assert apply["x-noust-requires-elevation"] is True

    def test_a_person_of_a_team_is_refused_it_is_the_machines(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        for who in ("alice", "omar", "bob"):
            response = docker.world.client.post(
                self.PREVIEW, json={}, headers=docker.world.as_(who)
            )
            assert response.status_code == 403, who

    def test_it_says_what_the_bring_one_does_with_the_same_words(
        self, docker: DockerWorld, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        previewed = docker.send("admin", "POST", "/stacks/nextcloud/bring/preview", json={})
        asked_by_the_old_way = docker.send(
            "admin", "POST", "/stacks/nextcloud/bring", json={"preview": True}
        )

        assert previewed.status_code == 200, previewed.text
        assert previewed.json() == asked_by_the_old_way.json()

    def test_what_cannot_be_brought_is_refused_with_its_reason_as_a_code_and_in_no_sudo(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch, no_sudo: dict[str, str]
    ) -> None:
        Adoption(monkeypatch)
        for box in docker.machine.boxes.values():
            if box.project == "nextcloud":
                box.working_dir = "/var/lib/portainer/compose/12"
                box.config_files = "/var/lib/portainer/compose/12/docker-compose.yml"

        response = docker.world.client.post(self.PREVIEW, json={}, headers=no_sudo)

        assert response.status_code == 409
        assert response.json()["reason"] == "no_folder"

    def test_a_stack_that_is_not_there_is_not_there(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        response = docker.send("admin", "POST", "/stacks/nothing/bring/preview", json={})

        assert response.status_code == 404


class TestThePreviewChangesNothing:
    """The preview is the rehearsal (``up --dry-run``): what the runner is given never changes anything."""

    @pytest.fixture
    def stack(self, folder: Path) -> Path:
        (folder / "compose.yaml").write_text(
            "services:\n  app:\n    image: nextcloud:29-apache\n"
            "    ports:\n      - '127.0.0.1:8081:80'\n  db:\n    image: postgres:16\n"
        )
        return folder

    def test_every_command_the_runner_is_given_only_looks_and_the_stack_is_rehearsed(
        self, docker: DockerWorld, stack: Path
    ) -> None:
        from noust.core.runner import is_read_only

        docker.machine.calls.clear()

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring/preview", json={})

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["adopted"], body["deployment_id"]) == (False, None)
        # Nothing the runner was given changes anything: every command is one a dry run lets through.
        assert docker.machine.changes() == []
        assert all(is_read_only(call) for call in docker.machine.calls), [
            call for call in docker.machine.calls if not is_read_only(call)
        ]
        # ... and one of them is the rehearsal itself, which is the whole of the proof.
        rehearsed = [call for call in docker.machine.calls if "--dry-run" in call]
        assert rehearsed, docker.machine.calls
        assert rehearsed[0][:2] == ("docker", "compose") and rehearsed[0][-1] == "--no-build"

    def test_nothing_is_registered_written_or_started(
        self, docker: DockerWorld, stack: Path
    ) -> None:
        from noust.core.store import get_store

        before = {app.domain for app in get_store().list_apps()}
        files = sorted(p.name for p in stack.iterdir())

        docker.send("admin", "POST", "/stacks/nextcloud/bring/preview", json={})

        assert {app.domain for app in get_store().list_apps()} == before
        assert sorted(p.name for p in stack.iterdir()) == files
        assert events("docker.bring") == []
        assert not [c for c in docker.machine.calls if c[:2] == ("systemctl", "enable")]


class TestThePreviewDoesNotShowSecrets:
    """What Compose and the checkout say can carry a credential: the preview is read by whoever reads Docker."""

    SOURCE = "https://deploy:hunter2hunter2@repo.example.com/org/nextcloud.git"

    def test_a_credential_in_where_updates_would_come_from_is_masked(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch, source=self.SOURCE)

        body = docker.send("aud", "POST", "/stacks/nextcloud/bring/preview", json={}).json()

        assert "hunter2hunter2" not in str(body)
        assert body["source"].startswith("https://") and "repo.example.com" in body["source"]

    # A token as the user of the address, with no password (Azure DevOps and others take it so), is
    # not a password and no pattern of a provider knows it: the user of an http(s) address is hidden
    # whatever it looks like. The token below is invented and has no provider's prefix on purpose.
    TOKEN = "pq7rb3kw2hvxn5jsd6c4mfzg2u5yvtlaeo3idxqwk5jb2n4rt7la"

    @pytest.mark.parametrize(
        "source",
        [
            f"https://{TOKEN}@dev.azure.com/org/project/_git/nextcloud",
            "https://deploytoken@repo.example.com/org/nextcloud.git",
            f"http://{TOKEN}@repo.example.com/org/nextcloud.git",
        ],
    )
    def test_a_token_that_is_the_user_of_the_address_is_masked_with_no_password_in_it(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        Adoption(monkeypatch, source=source)

        body = docker.send("aud", "POST", "/stacks/nextcloud/bring/preview", json={}).json()

        user = source.split("//", 1)[1].split("@", 1)[0]
        assert user not in str(body)
        assert body["source"].startswith(source.split("//", 1)[0] + "//***@")
        assert body["source"].endswith(source.split("@", 1)[1])

    def test_the_sentence_of_a_warning_is_sent_with_its_code_and_values(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentence = say("compose_adopt.no_repository", path="/srv/nextcloud")
        Adoption(monkeypatch, warnings=(sentence.text,), warnings_coded=(sentence,))

        body = docker.send("aud", "POST", "/stacks/nextcloud/bring/preview", json={}).json()

        assert body["warnings"] == [sentence.text]
        assert body["warnings_coded"] == [
            {"code": "compose_adopt.no_repository", "params": {"path": "/srv/nextcloud"}}
        ]

    def test_an_ssh_address_has_no_secret_and_stays(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch, source="git@repo.example.com:org/nextcloud.git")

        body = docker.send("aud", "POST", "/stacks/nextcloud/bring/preview", json={}).json()

        assert body["source"] == "git@repo.example.com:org/nextcloud.git"

    def test_a_credential_in_composes_own_words_is_masked_everywhere_they_are_shown(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        words = f"pull access denied for {self.SOURCE}: password=hunter2hunter2"
        Adoption(
            monkeypatch,
            dry_run=f" DRY-RUN MODE -  Container nextcloud-app-1  Running\n{words}",
            changes=(f"Container nextcloud-app-1 Recreated ({words})",),
            warnings=(f"Accepted: {words}",),
        )

        body = docker.send("aud", "POST", "/stacks/nextcloud/bring/preview", json={}).json()

        assert "hunter2hunter2" not in str(body)
        assert "DRY-RUN MODE" in body["rehearsal"] and "nextcloud-app-1" in body["rehearsal"]

    def test_a_secret_the_stacks_own_env_holds_is_masked_wherever_compose_printed_it(
        self, docker: DockerWorld, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (folder / ".env").write_text("NEXTCLOUD_ADMIN_PASSWORD=correct-horse-battery\n")
        Adoption(monkeypatch, dry_run="invalid value: correct-horse-battery for app")

        body = docker.send("aud", "POST", "/stacks/nextcloud/bring/preview", json={}).json()

        assert "correct-horse-battery" not in str(body)

    def test_composes_words_in_a_refusal_are_masked_too_and_stay_composes(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: object, **kwargs: object) -> None:
            raise AdoptionRefusedError(
                "Starting the stack as Noust would changes what runs",
                details="Compose would recreate nextcloud-app-1.",
                output=f"Container nextcloud-app-1  Recreated  pulling {self.SOURCE}",
                changes=("Container nextcloud-app-1 Recreated",),
            )

        monkeypatch.setattr(compose_adopt, "plan_adoption", refuse)

        response = docker.send("aud", "POST", "/stacks/nextcloud/bring/preview", json={})

        assert response.status_code == 409
        assert "hunter2hunter2" not in response.text
        assert "Recreated" in response.json()["output"]


class TestBringing:
    def test_it_registers_the_stack_as_the_application_named_and_is_on_record(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={"name": "cloud"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["adopted"], body["deployment_id"], body["name"]) == (True, 7, "cloud")
        assert [plan.domain for plan in adoption.adopted] == ["cloud"]
        recorded = events("docker.bring")[0]
        assert recorded["resource"] == "app:cloud"
        assert recorded["details"]["stack"] == "nextcloud"

    def test_nothing_that_runs_is_touched(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        docker.send("admin", "POST", "/stacks/nextcloud/bring", json={})

        assert docker.machine.changes() == []


class TestWhatCannotBeBrought:
    def test_a_stack_that_is_already_nousts_says_so_as_a_code(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.docker import names
        from tests.support.docker_web import ACME

        Adoption(monkeypatch)
        project = names.project_of_directory(ACME)
        docker.machine.boxes["acme-web-1"].project = project

        response = docker.send("admin", "POST", f"/stacks/{project}/bring", json={})

        assert response.status_code == 409
        assert response.json()["error"] == "docker_blocked"
        assert response.json()["reason"] == "already_noust"

    def test_a_folder_that_is_not_on_this_server_says_so_as_a_code(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)
        for box in docker.machine.boxes.values():
            if box.project == "nextcloud":
                box.working_dir = "/var/lib/portainer/compose/12"
                box.config_files = "/var/lib/portainer/compose/12/docker-compose.yml"

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={})

        assert response.status_code == 409
        assert response.json()["reason"] == "no_folder"

    def test_composes_own_words_are_returned_verbatim_when_its_rehearsal_would_change_something(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: object, **kwargs: object) -> None:
            raise AdoptionRefusedError(
                "Starting the stack as Noust would changes what runs",
                details="Compose would recreate nextcloud-app-1.",
                output=" ✔ DRY-RUN MODE -  Container nextcloud-app-1  Recreated   0.0s",
                changes=("Container nextcloud-app-1 Recreated",),
            )

        monkeypatch.setattr(compose_adopt, "plan_adoption", refuse)

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={})

        assert response.status_code == 409
        assert "Recreated" in response.json()["output"]

    def test_a_stack_that_is_not_there_is_not_there(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        assert docker.send("admin", "POST", "/stacks/nothing/bring", json={}).status_code == 404


class TestWhoMayDoIt:
    @pytest.mark.parametrize("who", ["aud", "alice", "omar", "bob"])
    def test_whoever_may_not_change_docker_is_refused(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch, who: str
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.send(who, "POST", "/stacks/nextcloud/bring", json={})

        assert response.status_code == 403
        assert adoption.adopted == []

    def test_changing_needs_sudo_mode(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.server import get_token_manager

        Adoption(monkeypatch)
        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.post(
            "/api/docker/stacks/nextcloud/bring",
            json={},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"


class TestWhatTheStackRunsWith:
    """A stack that runs with what gives the server away is adopted only by who accepts it (finding 2)."""

    def test_accepting_it_tells_the_adoption_who_accepted(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.send(
            "admin", "POST", "/stacks/nextcloud/bring", json={"accept_host_access": True}
        )

        assert response.status_code == 200, response.text
        assert adoption.planned[0]["accept_host_access"] is True
        assert adoption.planned[0]["accepted_by"]

    def test_without_saying_so_nothing_is_accepted(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        docker.send("admin", "POST", "/stacks/nextcloud/bring", json={})

        assert adoption.planned[0]["accept_host_access"] is False
        assert adoption.planned[0]["accepted_by"] == ""

    def test_the_refusal_with_the_lines_is_a_409_the_console_shows(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*_args: object, **_kwargs: object) -> None:
            raise AdoptionRefusedError(
                "Adopting nextcloud puts under Noust a stack whose containers have what is on "
                "this server",
                details="- service app mounts the Docker socket",
                host_access=("service app mounts the Docker socket",),
            )

        monkeypatch.setattr(compose_adopt, "plan_adoption", refuse)

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring/preview", json={})

        assert response.status_code == 409
        assert "mounts the Docker socket" in response.json()["hint"]
        assert response.json()["host_access"] == ["service app mounts the Docker socket"]

    def test_whoever_changes_docker_without_being_root_equivalent_cannot_accept_it(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.support.teams_support import make_person

        docker.world.people["manager"] = make_person(
            docker.world.teams,
            "manny",
            "admin",
            permissions=["docker.read", "docker.manage", "apps.manage", "apps.read"],
        )
        adoption = Adoption(monkeypatch)

        allowed = docker.send("manager", "POST", "/stacks/nextcloud/bring", json={})
        refused = docker.send(
            "manager", "POST", "/stacks/nextcloud/bring", json={"accept_host_access": True}
        )

        assert allowed.status_code == 200, allowed.text
        assert refused.status_code == 403
        assert len(adoption.planned) == 1
