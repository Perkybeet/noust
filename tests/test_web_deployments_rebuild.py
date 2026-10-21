# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``POST /api/apps/{domain}/deployments/{id}/rebuild`` with ``no_cache``: "Retry without cache".

A deployment that failed while installing or building is tried again from its page. Trying again
with the same cache can fail the same way (a corrupt package cache, dependencies carried over from
the release that serves), so the call can ask for the build from scratch. What is defended:

- **The body is optional and additive**: a caller that sends none, or sends ``no_cache: false``,
  queues exactly the job it always did.
- **From scratch is always a build**: on releases, a release of the same commit that is still on
  disk is not activated in place of the build the operator asked for.
- **It is the same permission**: asking for more build does not need less than a rebuild does.
"""

# The pipeline and API fixtures are imported rather than replicated, so there stays one
# definition of the fake machine and of the deployments router.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.web.api import deployments as deployments_api
from tests.test_deployment_rollback import (  # noqa: F401
    INPLACE,
    Jobs,
    client,
    deployment,
    inplace_app,
    jobs,
)
from tests.test_rebuild_commit import commit_of, git  # noqa: F401
from tests.test_release_activation import two_releases  # noqa: F401
from tests.test_release_pipeline import DOMAIN, active_id, machine, root, store  # noqa: F401


class TestTheRequest:
    def test_without_a_body_the_job_is_what_it_always_was(
        self, tmp_path: Path, client: TestClient, jobs: Jobs, store: NoustStore
    ) -> None:
        inplace_app(store, tmp_path)
        target = deployment(store, "failed", commit="abc1234")

        response = client.post(f"/api/apps/{INPLACE}/deployments/{target}/rebuild")

        assert response.status_code == 202, response.text
        assert jobs.created[0]["kwargs"] == {"domain": INPLACE, "commit": "abc1234"}
        assert "without cache" not in jobs.created[0]["name"]

    def test_a_body_that_does_not_ask_changes_nothing(
        self, tmp_path: Path, client: TestClient, jobs: Jobs, store: NoustStore
    ) -> None:
        inplace_app(store, tmp_path)
        target = deployment(store, "failed", commit="abc1234")

        response = client.post(
            f"/api/apps/{INPLACE}/deployments/{target}/rebuild", json={"no_cache": False}
        )

        assert response.status_code == 202, response.text
        assert jobs.created[0]["kwargs"] == {"domain": INPLACE, "commit": "abc1234"}

    def test_no_cache_is_handed_to_the_update_and_said_in_the_job(
        self, tmp_path: Path, client: TestClient, jobs: Jobs, store: NoustStore
    ) -> None:
        inplace_app(store, tmp_path)
        target = deployment(store, "failed", commit="abc1234")

        response = client.post(
            f"/api/apps/{INPLACE}/deployments/{target}/rebuild", json={"no_cache": True}
        )

        assert response.status_code == 202, response.text
        queued = jobs.created[0]
        assert queued["func"] is deployments_api.update_app_job
        assert queued["kwargs"] == {"domain": INPLACE, "commit": "abc1234", "no_cache": True}
        assert "without cache" in queued["name"]
        assert queued["metadata"]["deployment_id"] == target

    def test_a_deployment_without_a_commit_is_still_refused_with_the_way_forward(
        self, tmp_path: Path, client: TestClient, jobs: Jobs, store: NoustStore
    ) -> None:
        inplace_app(store, tmp_path)
        target = deployment(store, "failed", commit=None)

        response = client.post(
            f"/api/apps/{INPLACE}/deployments/{target}/rebuild", json={"no_cache": True}
        )

        assert response.status_code == 409
        assert response.json()["error"] == "no_commit"
        assert jobs.created == []

    def test_a_value_that_is_not_a_boolean_is_refused(
        self, tmp_path: Path, client: TestClient, jobs: Jobs, store: NoustStore
    ) -> None:
        inplace_app(store, tmp_path)
        target = deployment(store, "failed", commit="abc1234")

        response = client.post(
            f"/api/apps/{INPLACE}/deployments/{target}/rebuild", json={"no_cache": "maybe"}
        )

        assert response.status_code == 422
        assert jobs.created == []


class TestFromScratchIsABuild:
    def test_a_commit_whose_release_is_on_disk_is_built_not_activated(
        self,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        two_releases: tuple[str, str],
    ) -> None:
        first, second = two_releases
        calls_before = list(machine.runner.calls)
        releases_before = {p.name for p in (root / "releases").iterdir()}

        lifecycle.update_app(DOMAIN, commit=commit_of(machine, first)[:9], no_cache=True)

        rebuilt = active_id(root)
        # A new release, not the one that was on disk, and a build was run for it.
        assert rebuilt not in {first, second}
        assert rebuilt.split("-")[2] == first.split("-")[2]
        assert machine.runner.calls != calls_before
        assert first in {p.name for p in (root / "releases").iterdir()}
        assert releases_before < {p.name for p in (root / "releases").iterdir()}

    def test_without_it_the_same_commit_is_still_an_instant_activation(
        self,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        two_releases: tuple[str, str],
    ) -> None:
        first, _second = two_releases
        calls_before = list(machine.runner.calls)

        lifecycle.update_app(DOMAIN, commit=commit_of(machine, first)[:9])

        assert active_id(root) == first
        assert machine.runner.calls == calls_before
