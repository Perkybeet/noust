# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Editing the Compose of a stack over HTTP.

What the console is written against: judging writes nothing and says what would change, from Compose's own
rehearsal; applying is a job that keeps the document as a version first (who, why), runs the application's
update, and puts the document before back when the stack does not come up; reading a document or a version of
it is a reveal and is recorded, and the list of versions has no text; a stack whose document is somebody
else's says whose it is; and changing needs the permissions and sudo mode, and a team never has them.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.exceptions import DeploymentError
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.web.api import docker as docker_api
from noust.web.jobs import JobManager
from tests.support.compose_document import APP_NAME, DOCUMENT, DOMAIN, NEWER, make_stack
from tests.support.docker_machine import RemoteTag
from tests.support.docker_web import DockerWorld, build
from tests.support.teams_support import make_person

PROJECT = "stack-example-com"


class Updates:
    """Stands in for the application's update and for putting a stack back."""

    def __init__(self) -> None:
        self.updated: list[dict[str, Any]] = []
        self.put_back: list[dict[str, Any]] = []
        self.failure: Exception | None = None

    def update_app(self, domain: str, **kwargs: Any) -> Any:
        self.updated.append({"domain": domain, **kwargs})
        if self.failure is not None:
            raise self.failure
        return type("Outcome", (), {"deployment_id": 41, "warnings": None})()

    def put_stack_back(
        self, domain: str, app_name: str, snapshot: Any, failure: Any, **kw: Any
    ) -> None:
        self.put_back.append({"domain": domain, "snapshot": dict(snapshot)})
        failure.details = (
            f"{failure.details}\n\nThe previous version is running again and answering."
        )

    def health_gate_for(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the stack is put back through put_stack_back")


@pytest.fixture
def updates(monkeypatch: pytest.MonkeyPatch) -> Updates:
    fake = Updates()
    monkeypatch.setattr("noust.deployers.lifecycle.update_app", fake.update_app)
    monkeypatch.setattr("noust.deployers.lifecycle.health_gate_for", fake.health_gate_for)
    monkeypatch.setattr("noust.recipes.updates.put_stack_back", fake.put_stack_back)
    return fake


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
    JobManager.reset_instance()
    built = build(tmp_path, monkeypatch)
    stack = make_stack(built.world.store, tmp_path)
    built.machine.box(
        "stack-web-1",
        image="nginx:1.27",
        project=PROJECT,
        service="web",
        working_dir=stack.app_path,
        policy="unless-stopped",
    )
    for version in ("1.27", "1.28"):
        built.machine.remote[f"docker.io/library/nginx:{version}"] = RemoteTag("sha256:" + "b" * 64)
    built.machine.dry_run_output = " ✔ DRY-RUN MODE -  Container stack-web-1  Recreated   0.0s\n"
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        set_runner(None)
        JobManager.reset_instance()
        NoustStore.reset_instance()


def kept(docker: DockerWorld) -> str:
    return (recipe_source_dir(APP_NAME, docker.world.store) / COMPOSE_FILE).read_text()


def events(action: str) -> list[dict]:
    return audit.get_log().read(action=action)


def wait_for_job(docker: DockerWorld, job_id: str) -> dict[str, Any]:
    for _ in range(500):
        job = docker.world.client.get(
            f"/api/jobs/{job_id}", headers=docker.world.as_("admin")
        ).json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("the job never finished")


class TestReading:
    def test_the_document_of_a_stack_noust_keeps(self, docker: DockerWorld) -> None:
        body = docker.get("admin", f"/stacks/{PROJECT}/compose").json()

        assert (body["editable"], body["kind"], body["domain"]) == (True, "document", DOMAIN)
        assert body["text"] == DOCUMENT
        assert body["version"] is None

    def test_reading_is_a_reveal_and_is_on_record(self, docker: DockerWorld) -> None:
        docker.get("admin", f"/stacks/{PROJECT}/compose")

        recorded = events("apps.compose.document")[0]
        assert recorded["details"]["action"] == "read"
        assert recorded["resource"] == f"app:{DOMAIN}"

    @pytest.mark.parametrize("who", ["aud", "alice", "bob"])
    def test_whoever_may_not_reveal_secrets_does_not_read_it(
        self, docker: DockerWorld, who: str
    ) -> None:
        assert docker.get(who, f"/stacks/{PROJECT}/compose").status_code == 403

    def test_a_stack_nobody_made_says_it_is_brought_first(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/stacks/portainer-stack/compose")
        assert body.status_code == 404

        docker.machine.box(
            "nextcloud-app-1", project="nextcloud", working_dir="/opt/stacks/nextcloud"
        )
        found = docker.get("admin", "/stacks/nextcloud/compose").json()

        assert (found["editable"], found["why_code"], found["text"]) == (False, "bring_first", None)
        assert "noust docker stack bring nextcloud" in found["why"]

    def test_an_application_that_is_not_a_compose_stack_says_so(self, docker: DockerWorld) -> None:
        from noust.core.docker import names
        from tests.support.docker_web import ACME

        project = names.project_of_directory(ACME)
        docker.machine.boxes["acme-web-1"].project = project

        found = docker.get("admin", f"/stacks/{project}/compose").json()

        assert (found["editable"], found["why_code"]) == (False, "not_compose")


class TestJudging:
    def test_it_says_what_would_change_and_writes_nothing(self, docker: DockerWorld) -> None:
        response = docker.send(
            "admin", "POST", f"/stacks/{PROJECT}/compose/check", json={"text": NEWER}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["valid"], body["changed"], body["kind"]) == (True, True, "document")
        assert "+    image: nginx:1.28" in body["diff"]
        assert [(c["name"], c["ok"]) for c in body["checks"]] == [
            ("compose", True),
            ("privileges", True),
            ("images", True),
            ("variables", True),
        ]
        assert body["effects"]["changes"] == [
            {"kind": "container", "name": "stack-web-1", "service": "web", "action": "recreate"}
        ]
        assert kept(docker) == DOCUMENT
        assert docker.machine.changes() == []

    def test_a_document_compose_does_not_read_says_so_in_composes_words_with_the_line(
        self, docker: DockerWorld
    ) -> None:
        broken = DOCUMENT.replace('- "127.0.0.1:8080:80"', '- ["127.0.0.1:8080:80"')

        body = docker.send(
            "admin", "POST", f"/stacks/{PROJECT}/compose/check", json={"text": broken}
        ).json()

        assert body["valid"] is False
        assert body["problems"][0]["code"] == "compose"
        assert "did not find expected" in body["problems"][0]["output"]
        assert body["problems"][0]["line"] is not None

    def test_a_privileged_service_is_refused_before_anything_is_kept(
        self, docker: DockerWorld
    ) -> None:
        text = DOCUMENT.replace("nginx:1.27", "nginx:1.27\n    privileged: true")

        body = docker.send(
            "admin", "POST", f"/stacks/{PROJECT}/compose/check", json={"text": text}
        ).json()

        assert body["valid"] is False
        assert body["problems"][0]["code"] == "privileges"

    @pytest.mark.parametrize("who", ["aud", "alice", "omar"])
    def test_whoever_may_not_change_docker_does_not_judge(
        self, docker: DockerWorld, who: str
    ) -> None:
        response = docker.send(
            who, "POST", f"/stacks/{PROJECT}/compose/check", json={"text": NEWER}
        )

        assert response.status_code == 403

    def test_whoever_may_not_reveal_secrets_does_not_judge(self, docker: DockerWorld) -> None:
        # Compose resolves a document with the stack's own variables: a judgement can carry one's value.
        docker.world.people["hands"] = make_person(
            docker.world.teams, "hands", "admin", permissions=["docker.read", "docker.manage"]
        )

        response = docker.send(
            "hands", "POST", f"/stacks/{PROJECT}/compose/check", json={"text": NEWER}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "permission_denied"

    def test_a_document_larger_than_a_stack_is_refused(self, docker: DockerWorld) -> None:
        response = docker.send(
            "admin",
            "POST",
            f"/stacks/{PROJECT}/compose/check",
            json={"text": "services: {}\n" + "# x\n" * 100_000},
        )

        assert response.status_code == 422


class TestApplying:
    def test_it_is_kept_as_a_version_and_applied_by_a_job(
        self, docker: DockerWorld, updates: Updates
    ) -> None:
        response = docker.send(
            "admin",
            "PUT",
            f"/stacks/{PROJECT}/compose",
            json={"text": NEWER, "reason": "Use nginx 1.28"},
        )

        assert response.status_code == 202, response.text
        job = wait_for_job(docker, response.json()["job_id"])
        assert job["status"] == "completed", job
        assert job["result"] == {
            "domain": DOMAIN,
            "version": 2,
            "applied": True,
            "deployment_id": 41,
            "warnings": [],
        }
        assert kept(docker) == NEWER
        assert [u["domain"] for u in updates.updated] == [DOMAIN]
        listed = docker.get("admin", f"/stacks/{PROJECT}/compose/versions").json()["items"]
        assert [(v["version"], v["source"], v["reason"]) for v in listed] == [
            (2, "edit", "Use nginx 1.28"),
            (1, "found", "The document as Noust found it"),
        ]
        assert listed[0]["actor"]
        assert all("text" not in item for item in listed)

    def test_the_job_is_given_the_version_and_not_the_text(
        self, docker: DockerWorld, updates: Updates
    ) -> None:
        secret = DOCUMENT.replace("nginx:1.27", "nginx:1.28") + "# DB_PASSWORD=hunter2hunter2\n"

        response = docker.send(
            "admin", "PUT", f"/stacks/{PROJECT}/compose", json={"text": secret, "reason": "x"}
        )
        job = wait_for_job(docker, response.json()["job_id"])

        assert "hunter2hunter2" not in str(job)

    def test_when_the_stack_does_not_come_up_the_document_before_is_back_and_the_history_says_so(
        self, docker: DockerWorld, updates: Updates
    ) -> None:
        updates.failure = DeploymentError("web did not answer", details="The health gate failed.")

        response = docker.send(
            "admin", "PUT", f"/stacks/{PROJECT}/compose", json={"text": NEWER, "reason": "x"}
        )
        job = wait_for_job(docker, response.json()["job_id"])

        assert job["status"] == "failed"
        assert "running again" in job["error"]
        assert updates.put_back[0]["snapshot"] == {COMPOSE_FILE: DOCUMENT}
        versions = docker.get("admin", f"/stacks/{PROJECT}/compose/versions").json()["items"]
        assert [v["source"] for v in versions][:2] == ["revert", "edit"]

    def test_a_document_that_does_not_pass_is_refused_and_nothing_is_kept(
        self, docker: DockerWorld, updates: Updates
    ) -> None:
        text = DOCUMENT.replace("nginx:1.27", "nginx:1.27\n    privileged: true")

        response = docker.send(
            "admin", "PUT", f"/stacks/{PROJECT}/compose", json={"text": text, "reason": "x"}
        )

        assert response.status_code == 409
        assert response.json()["error"] == "docker_blocked"
        assert response.json()["reason"] == "invalid"
        assert docker.get("admin", f"/stacks/{PROJECT}/compose/versions").json()["items"] == []
        assert updates.updated == []

    def test_the_same_document_is_not_a_change(self, docker: DockerWorld, updates: Updates) -> None:
        response = docker.send(
            "admin", "PUT", f"/stacks/{PROJECT}/compose", json={"text": DOCUMENT, "reason": "x"}
        )

        assert response.status_code == 409
        assert response.json()["reason"] == "unchanged"

    def test_it_is_on_record_with_who_and_why(self, docker: DockerWorld, updates: Updates) -> None:
        response = docker.send(
            "admin", "PUT", f"/stacks/{PROJECT}/compose", json={"text": NEWER, "reason": "Use 1.28"}
        )
        wait_for_job(docker, response.json()["job_id"])

        recorded = {e["details"]["action"]: e for e in events("docker.compose")}
        assert recorded["stage"]["details"]["reason"] == "Use 1.28"
        assert recorded["apply"]["details"]["version"] == 2
        assert recorded["apply"]["resource"] == f"app:{DOMAIN}"

    @pytest.mark.parametrize("who", ["aud", "alice", "omar", "bob"])
    def test_whoever_may_not_change_docker_does_not_apply(
        self, docker: DockerWorld, updates: Updates, who: str
    ) -> None:
        response = docker.send(
            who, "PUT", f"/stacks/{PROJECT}/compose", json={"text": NEWER, "reason": "x"}
        )

        assert response.status_code == 403
        assert updates.updated == []
        assert kept(docker) == DOCUMENT

    def test_applying_needs_sudo_mode(self, docker: DockerWorld, updates: Updates) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.put(
            f"/api/docker/stacks/{PROJECT}/compose",
            json={"text": NEWER, "reason": "x"},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert updates.updated == []


class TestGoingBack:
    def edited(self, docker: DockerWorld) -> None:
        response = docker.send(
            "admin", "PUT", f"/stacks/{PROJECT}/compose", json={"text": NEWER, "reason": "Use 1.28"}
        )
        wait_for_job(docker, response.json()["job_id"])

    def test_a_version_is_read_with_what_putting_it_back_would_change(
        self, docker: DockerWorld, updates: Updates
    ) -> None:
        self.edited(docker)

        body = docker.get("admin", f"/stacks/{PROJECT}/compose/versions/1").json()

        assert body["text"] == DOCUMENT
        assert "-    image: nginx:1.28" in body["diff"]
        assert (body["source"], body["diff_against"]) == ("found", "current")

    def test_reading_a_version_is_a_reveal_the_list_is_not(
        self, docker: DockerWorld, updates: Updates
    ) -> None:
        self.edited(docker)

        assert docker.get("aud", f"/stacks/{PROJECT}/compose/versions").status_code == 200
        assert docker.get("aud", f"/stacks/{PROJECT}/compose/versions/1").status_code == 403

    def test_a_version_that_was_never_kept_is_not_there(self, docker: DockerWorld) -> None:
        assert docker.get("admin", f"/stacks/{PROJECT}/compose/versions/9").status_code == 404

    def test_a_version_is_put_back_by_the_same_road(
        self, docker: DockerWorld, updates: Updates
    ) -> None:
        self.edited(docker)

        response = docker.send(
            "admin",
            "POST",
            f"/stacks/{PROJECT}/compose/versions/1/restore",
            json={"reason": "Back to 1.27"},
        )

        assert response.status_code == 202, response.text
        job = wait_for_job(docker, response.json()["job_id"])
        assert job["status"] == "completed", job
        assert kept(docker) == DOCUMENT
        versions = docker.get("admin", f"/stacks/{PROJECT}/compose/versions").json()["items"]
        assert (versions[0]["source"], versions[0]["reason"]) == ("restore", "Back to 1.27")

    @pytest.mark.parametrize("who", ["aud", "alice"])
    def test_whoever_may_not_change_docker_does_not_put_one_back(
        self, docker: DockerWorld, updates: Updates, who: str
    ) -> None:
        self.edited(docker)

        response = docker.send(
            who, "POST", f"/stacks/{PROJECT}/compose/versions/1/restore", json={}
        )

        assert response.status_code == 403
        assert kept(docker) == NEWER
