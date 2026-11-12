# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
New versions of the images in use, over HTTP: look for them, read what updating each would do, and update.

What the console is written against, and what is defended: looking asks the registries and is the operator's
act, never a side effect of opening a page; every image row says what the last look found and what updating
it would do; updating is the application's update (the one every door queues) and is refused, with the
reason as a code, for what nobody of Noust made, for what an engine owns and for a major version that was
not accepted or has no proven copy; and changing needs the permission, sudo mode, and is in the audit trail.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.docker import names
from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
from noust.core.runner import set_runner
from noust.core.store import NoustStore, get_store
from noust.web.api import docker as docker_api
from noust.web.api import jobs as jobs_api
from noust.web.jobs import JobManager
from tests.support.docker_machine import RemoteTag
from tests.support.docker_web import ACME, DockerWorld, build

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
CHECKED = "2026-11-12T10:00:00+00:00"


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
    JobManager.reset_instance()
    built = build(tmp_path, monkeypatch)
    project = names.project_of_directory(ACME)
    machine = built.machine
    # An application's service that runs a registry image, and one of its database.
    for digit, name, image in (
        ("6", "acme-cache-1", "redis:7-alpine"),
        ("7", "acme-db-1", "postgres:16"),
    ):
        repository, _, tag = image.partition(":")
        machine.image(digit, repository, tag, "100MB")
        machine.box(
            name,
            image=image,
            project=project,
            working_dir=str(tmp_path / ACME),
            policy="unless-stopped",
        )
        machine.facts("sha256:" + digit * 64, repo_digests=[f"{repository}@{A}"])
    machine.facts("sha256:" + "2" * 64, repo_digests=[f"adminer@{A}"])
    machine.facts("sha256:" + "1" * 64, repo_digests=[f"portainer/portainer-ce@{A}"])
    # What the registries have.
    machine.remote["docker.io/library/redis:7-alpine"] = RemoteTag(B, env=["REDIS_VERSION=7.4.2"])
    machine.remote["docker.io/library/adminer:latest"] = RemoteTag(B)
    machine.remote["docker.io/library/postgres:16"] = RemoteTag(A)
    machine.remote["docker.io/portainer/portainer-ce:latest"] = RemoteTag(A)
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        set_runner(None)
        JobManager.reset_instance()
        NoustStore.reset_instance()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The updates the application's update queue was asked for; none runs."""
    asked: list[dict[str, Any]] = []

    def fake(domain: str, **kwargs: Any) -> dict[str, Any]:
        asked.append({"domain": domain, **kwargs})
        return {}

    monkeypatch.setattr(jobs_api, "update_app_job", fake)
    return asked


def row(docker: DockerWorld, tag: str, who: str = "admin") -> dict[str, Any]:
    items = docker.get(who, "/images").json()["items"]
    return next(item for item in items if tag in item["tags"])


def save(reference: str, change: str = "patch", state: str = "newer", **more: object) -> None:
    base: dict[str, object] = {
        "reference": reference,
        "state": state,
        "local_digest": A,
        "remote_digest": B,
        "current_version": None,
        "latest_version": None,
        "latest_created": None,
        "change": change,
        "floating": reference.endswith(":latest"),
        "method": "imagetools",
        "detail": "",
        "checked_at": CHECKED,
    }
    base.update(more)
    ImageVersionStore(get_store()).save(VersionRecord(**base))  # type: ignore[arg-type]


def wait_for_job(docker: DockerWorld, job_id: str) -> dict[str, Any]:
    for _ in range(500):
        job = docker.world.client.get(
            f"/api/jobs/{job_id}", headers=docker.world.as_("admin")
        ).json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("the job never finished")


def events(action: str) -> list[dict]:
    return audit.get_log().read(action=action)


class TestWhatTheRowsSay:
    def test_an_image_nobody_looked_at_says_so(self, docker: DockerWorld) -> None:
        update = row(docker, "adminer:latest")["update"]

        assert (update["state"], update["mode"]) == ("not_looked", "unknown")
        assert update["checked_at"] is None

    def test_what_the_last_look_found_is_on_the_row_with_what_updating_would_do(
        self, docker: DockerWorld
    ) -> None:
        save("redis:7-alpine", current_version="7.4.1", latest_version="7.4.2")
        save("adminer:latest", change="unknown")

        cache = row(docker, "redis:7-alpine")["update"]
        loose = row(docker, "adminer:latest")["update"]

        assert (cache["state"], cache["change"], cache["mode"]) == ("newer", "patch", "with_net")
        assert (cache["current_version"], cache["latest_version"]) == ("7.4.1", "7.4.2")
        assert cache["app"] == "shop.acme.example"
        assert cache["steps"][0] == "pull"
        assert cache["apply"] is True
        assert (loose["mode"], loose["apply"], loose["floating"]) == ("loose", False, True)

    def test_what_a_person_who_only_reads_sees_the_same_findings(self, docker: DockerWorld) -> None:
        save("adminer:latest")

        assert row(docker, "adminer:latest", who="aud")["update"]["state"] == "newer"

    def test_a_person_of_a_team_does_not_see_the_zone(self, docker: DockerWorld) -> None:
        assert docker.get("alice", "/images").status_code == 403

    def test_the_summary_says_how_many_have_a_new_version_and_how_many_a_major(
        self, docker: DockerWorld
    ) -> None:
        save("redis:7-alpine")
        save("adminer:latest")
        save("portainer/portainer-ce:latest", change="major")

        summary = docker.get("admin", "/summary").json()

        kinds = {item["kind"]: item for item in summary["attention"]}
        assert kinds["update"]["count"] == 2
        assert kinds["major"]["count"] == 1
        assert "portainer/portainer-ce:latest" in kinds["major"]["facts"]["images"]


class TestLookingForNewVersions:
    def test_it_is_a_job_that_asks_the_registries_and_keeps_what_they_said(
        self, docker: DockerWorld
    ) -> None:
        response = docker.send("admin", "POST", "/images/check", json={})

        assert response.status_code == 202, response.text
        job = wait_for_job(docker, response.json()["job_id"])
        assert job["status"] == "completed", job
        found = {item["reference"]: item for item in job["result"]["records"]}
        assert found["redis:7-alpine"]["state"] == "newer"
        assert found["redis:7-alpine"]["latest_version"] == "7.4.2"
        assert found["postgres:16"]["state"] == "current"
        assert row(docker, "adminer:latest")["update"]["state"] == "newer"
        # What has no registry to ask is said, not dropped.
        assert {item["why"] for item in job["result"]["skipped"]} == {"built_here"}

    def test_nothing_is_asked_by_opening_the_pages(self, docker: DockerWorld) -> None:
        docker.get("admin", "/images")
        docker.get("admin", "/summary")

        asked = [c for c in docker.machine.calls if c[:3] == ("docker", "buildx", "imagetools")]
        assert asked == []

    def test_it_is_on_record(self, docker: DockerWorld) -> None:
        docker.send("admin", "POST", "/images/check", json={})

        assert events("docker.check")[0]["resource"] == "docker"

    @pytest.mark.parametrize("who", ["aud", "alice", "bob"])
    def test_whoever_may_not_change_docker_may_not_ask_the_registries_with_its_accounts(
        self, docker: DockerWorld, who: str
    ) -> None:
        assert docker.send(who, "POST", "/images/check", json={}).status_code == 403
        assert [
            c for c in docker.machine.calls if c[:3] == ("docker", "buildx", "imagetools")
        ] == []

    def test_a_docker_without_buildx_says_it_cannot_ask_and_does_not_download(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.buildx = False

        response = docker.send("admin", "POST", "/images/check", json={})
        job = wait_for_job(docker, response.json()["job_id"])

        assert job["status"] == "completed", job
        assert all(item["state"] == "unknown" for item in job["result"]["records"])
        assert "download" in job["result"]["records"][0]["detail"]
        assert not [c for c in docker.machine.calls if c[:2] == ("docker", "pull")]

    def test_with_pull_it_downloads_to_compare(self, docker: DockerWorld) -> None:
        docker.machine.buildx = False

        response = docker.send("admin", "POST", "/images/check", json={"pull": True})
        job = wait_for_job(docker, response.json()["job_id"])

        assert job["status"] == "completed", job
        assert ("docker", "pull", "docker.io/library/redis:7-alpine") in docker.machine.calls


class TestWhatUpdatingWouldDo:
    def test_the_plan_of_one_image(self, docker: DockerWorld) -> None:
        save("redis:7-alpine")
        image = row(docker, "redis:7-alpine")

        plan = docker.get("admin", f"/images/{image['id']}/update").json()

        assert plan["mode"] == "with_net"
        assert plan["images"] == ["redis:7-alpine"]
        assert "copy_databases" in plan["steps"]  # the application has a database of its own

    def test_an_image_that_does_not_exist_is_not_there(self, docker: DockerWorld) -> None:
        response = docker.get("admin", "/images/sha256:" + "f" * 64 + "/update")

        assert response.status_code == 404


class TestUpdating:
    def test_an_image_of_an_application_is_the_applications_update(
        self, docker: DockerWorld, queued: list[dict[str, Any]]
    ) -> None:
        save("redis:7-alpine")
        image = row(docker, "redis:7-alpine")

        response = docker.send("admin", "POST", f"/images/{image['id']}/update", json={})

        assert response.status_code == 202, response.text
        job = wait_for_job(docker, response.json()["job_id"])
        assert job["status"] == "completed", job
        assert [item["domain"] for item in queued] == ["shop.acme.example"]
        recorded = events("docker.update")[0]
        assert recorded["resource"] == "app:shop.acme.example"
        assert recorded["details"]["images"] == ["redis:7-alpine"]

    def test_what_nobody_made_is_refused_with_the_reason_as_a_code(
        self, docker: DockerWorld, queued: list[dict[str, Any]]
    ) -> None:
        save("adminer:latest")
        image = row(docker, "adminer:latest")

        response = docker.send("admin", "POST", f"/images/{image['id']}/update", json={})

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "docker_blocked"
        assert body["reason"] == "loose"
        assert "does not know how it was created" in body["detail"]
        assert queued == []

    def test_a_major_version_of_a_database_waits_for_a_copy_that_was_proven_to_restore(
        self, docker: DockerWorld, queued: list[dict[str, Any]]
    ) -> None:
        save("postgres:16", change="major")
        image = row(docker, "postgres:16")

        response = docker.send(
            "admin", "POST", f"/images/{image['id']}/update", json={"accept_major": True}
        )

        assert response.status_code == 409
        assert response.json()["reason"] == "verified_copy"
        assert "noust backup drill shop.acme.example" in response.json()["via"]
        assert queued == []

    def test_a_major_version_is_not_taken_by_not_saying_no(
        self, docker: DockerWorld, queued: list[dict[str, Any]]
    ) -> None:
        save("redis:7-alpine", change="major")
        image = row(docker, "redis:7-alpine")

        refused = docker.send("admin", "POST", f"/images/{image['id']}/update", json={})
        accepted = docker.send(
            "admin", "POST", f"/images/{image['id']}/update", json={"accept_major": True}
        )

        assert refused.status_code == 409 and refused.json()["reason"] == "accept_major"
        assert accepted.status_code == 202, accepted.text

    def test_nothing_new_is_nothing_to_update(self, docker: DockerWorld) -> None:
        save("redis:7-alpine", state="current", change="")
        image = row(docker, "redis:7-alpine")

        response = docker.send("admin", "POST", f"/images/{image['id']}/update", json={})

        assert response.status_code == 409
        assert response.json()["reason"] == "nothing_new"

    @pytest.mark.parametrize("who", ["aud", "alice", "omar", "bob"])
    def test_whoever_may_not_change_docker_may_not_update(
        self, docker: DockerWorld, queued: list[dict[str, Any]], who: str
    ) -> None:
        save("redis:7-alpine")
        image = row(docker, "redis:7-alpine")

        response = docker.send(who, "POST", f"/images/{image['id']}/update", json={})

        assert response.status_code == 403
        assert queued == []

    def test_changing_needs_sudo_mode(
        self, docker: DockerWorld, queued: list[dict[str, Any]]
    ) -> None:
        from noust.web.server import get_token_manager

        save("redis:7-alpine")
        image = row(docker, "redis:7-alpine")
        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.post(
            f"/api/docker/images/{image['id']}/update",
            json={},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert queued == []
