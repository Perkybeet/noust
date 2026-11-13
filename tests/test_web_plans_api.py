# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The plan of an operation, over the API (item 183).

The machine is the one of ``test_plans_update``: the real managers over a runner that can be asked what
it was given. What is held here is what only the API adds: the permission a plan is read with, that it
asks for nothing a person has to confirm, that it leaves no job and no row behind, that the schema says
which operations have one, and that an application of another team does not exist for it.
"""

# The fixtures are imported rather than replicated, so there stays one definition of the machine.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.plans.registry import PLAN_EXTENSION, PLANNED
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.jobs import get_job_manager
from noust.web.server import create_app, get_token_manager
from tests.support.permission_routes import signed_in
from tests.support.plans_machine import (  # noqa: F401  (fixtures, found by name)
    DOMAIN,
    git,
    machine,
    node_tree,
    root,
    served,
    store,
    two_releases,
    unit_dirs,
)
from tests.support.release_pipeline import GOOD_SERVER


@pytest.fixture
def app(tmp_path: Path, served) -> FastAPI:
    """The console, over the machine."""
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """A client signed in with the master token."""
    signed = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed


def history(served) -> list:
    """The deployments of the application."""
    return served.store.list_deployments(DOMAIN)


def test_the_plan_of_an_update_is_the_one_shape_with_its_sentences_coded(
    client: TestClient, served, tmp_path: Path
) -> None:
    served.git.publish(node_tree(tmp_path / "next", server=GOOD_SERVER + "// next\n"))

    response = client.post(f"/api/apps/{DOMAIN}/update/plan", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation"] == "app.update" and body["subject"] == DOMAIN
    assert body["effect"]["code"] == "plans.update.release"
    assert body["effect"]["text"].startswith(f"Builds {DOMAIN}")
    assert [c["text"] for c in body["commands"] if c["check"]] == ["GET http://127.0.0.1:3100/"]
    assert body["unknown"] and all(
        item["code"].startswith("plans.unknown.") for item in body["unknown"]
    )
    assert body["needs"] == {"elevation": False, "approval": False, "confirmations": []}


def test_an_update_plan_takes_the_body_of_the_operation_it_plans(
    client: TestClient, served, tmp_path: Path
) -> None:
    """The body of ``POST /api/jobs/update`` can be sent as it is, with the domain in the path."""
    served.git.publish(node_tree(tmp_path / "next", server=GOOD_SERVER + "// next\n"))

    response = client.post(
        f"/api/apps/{DOMAIN}/update/plan",
        json={"force": True, "no_cache": False, "frozen_ok": True},
    )

    assert response.status_code == 200, response.text


def test_what_a_plan_does_not_cover_is_refused_and_not_ignored(client: TestClient, served) -> None:
    response = client.post(f"/api/apps/{DOMAIN}/update/plan", json={"image_tag": "1.2"})

    assert response.status_code == 400
    assert "image_tag" in response.text


def test_a_plan_asks_for_no_sudo_mode_and_leaves_no_job_and_no_row(
    client: TestClient, served, tmp_path: Path
) -> None:
    served.git.publish(node_tree(tmp_path / "next", server=GOOD_SERVER + "// next\n"))
    rows, jobs = len(history(served)), len(get_job_manager().get_all_jobs(limit=500))

    first = client.post(f"/api/apps/{DOMAIN}/update/plan", json={})
    target = next(r for r in history(served) if r.release_id == served.first)
    second = client.post(f"/api/apps/{DOMAIN}/deployments/{target.id}/rollback/plan")

    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert len(history(served)) == rows and len(get_job_manager().get_all_jobs(limit=500)) == jobs
    assert served.runner.changes == []


def test_the_plan_of_going_back_is_the_one_shape_too(client: TestClient, served) -> None:
    target = next(r for r in history(served) if r.release_id == served.first)

    response = client.post(f"/api/apps/{DOMAIN}/deployments/{target.id}/rollback/plan", json={})

    body = response.json()
    assert response.status_code == 200, response.text
    assert (
        body["operation"] == "app.rollback" and body["effect"]["code"] == "plans.rollback.instant"
    )
    assert {item["code"] for item in body["findings"]} >= {"plans.found.database_stays"}


def test_an_application_that_does_not_exist_is_a_404_and_so_is_a_deployment_of_another(
    client: TestClient, served
) -> None:
    assert client.post("/api/apps/nobody.example.com/update/plan", json={}).status_code == 404
    assert client.post(f"/api/apps/{DOMAIN}/deployments/9999/rollback/plan").status_code == 404


def test_a_viewer_can_read_a_plan_and_still_cannot_apply_what_it_plans(
    app: FastAPI, served, tmp_path: Path
) -> None:
    viewer, csrf = signed_in(app, "viewer")
    headers = {CSRF_HEADER_NAME: csrf}

    plan = viewer.post(f"/api/apps/{DOMAIN}/update/plan", json={}, headers=headers)
    apply = viewer.post("/api/jobs/update", json={"domain": DOMAIN, "force": True}, headers=headers)

    assert plan.status_code == 200, plan.text
    assert apply.status_code == 403


def test_the_schema_says_which_operations_have_a_plan_and_where(app: FastAPI) -> None:
    schema = app.openapi()

    for planned in PLANNED:
        operation = schema["paths"][planned.path][planned.method.lower()]
        assert operation[PLAN_EXTENSION] == {
            "method": planned.plan_method,
            "path": planned.plan_path,
            "operation": planned.key,
        }
        plan = schema["paths"][planned.plan_path][planned.plan_method.lower()]
        expected = "docker.read" if planned.key.startswith("docker.") else "apps.read"
        assert plan["x-noust-permission"] == expected
        assert "x-noust-requires-elevation" not in plan
        assert PLAN_EXTENSION not in plan


@pytest.mark.parametrize("action", ["restart", "stop", "start"])
def test_the_plan_of_an_action_on_the_service_is_the_one_shape(
    client: TestClient, served, action: str
) -> None:
    response = client.post(f"/api/apps/{DOMAIN}/{action}/plan")

    body = response.json()
    assert response.status_code == 200, response.text
    assert (
        body["operation"] == f"app.{action}" and body["effect"]["code"] == f"plans.service.{action}"
    )
    assert served.runner.changes == []


def test_the_plan_of_new_limits_shows_the_unit_it_would_rewrite_and_takes_the_operations_body(
    client: TestClient, served
) -> None:
    response = client.post(
        f"/api/apps/{DOMAIN}/limits/plan", json={"memory_max_mb": 512, "restart": False}
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert [f["path"].rsplit("/", 1)[-1] for f in body["files"]] == ["rel-example-com.service"]
    assert {"kind": "add", "text": "MemoryMax=512M"} in body["files"][0]["lines"]


def test_the_plan_of_a_deletion_says_what_stays_and_takes_its_query_as_a_body(
    client: TestClient, served
) -> None:
    response = client.post(f"/api/apps/{DOMAIN}/delete/plan", json={"remove_ssl": True})

    codes = [item["code"] for item in response.json()["findings"]]
    assert response.status_code == 200, response.text
    assert "plans.found.delete_files_kept" in codes
    assert "plans.found.delete_certificate_kept" not in codes
    assert response.json()["needs"]["elevation"] is True, "the deletion it plans asks for sudo mode"


def test_a_certificate_there_is_none_of_is_a_404(client: TestClient, served) -> None:
    assert client.post("/api/certs/nobody.example.com/renew/plan").status_code == 404
