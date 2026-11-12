# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the Overview says about deployments that failed (items 141 and 147).

- A first deployment that failed leaves no application, so the list of what needs attention would
  never mention it: it is read from the record of the attempt (``noust.core.first_deploys``).
- A deployment that failed on an application that exists is amber while the service runs, the colour
  its own page gives it: only a service that is down is red, and that reason says so itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from noust.core.first_deploys import FirstDeploys
from noust.core.store import App, JobRecord, NoustStore
from noust.managers.overview import collect_overview, reset_cache
from noust.monitor import sampler

NOW = datetime(2026, 11, 10, 15, 30, 0)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    reset_cache()
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()
        reset_cache()


@pytest.fixture(autouse=True)
def machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sampler, "resolve_apps_root", lambda root=None: "/apps")
    monkeypatch.setattr(
        sampler,
        "read_disk",
        lambda root: sampler.Usage(used=100 * 1024**3, total=300 * 1024**3, percent=33.3),
    )


def collect(store: NoustStore, services: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return collect_overview(
        store=store,
        services=lambda: services or [],
        certificates=lambda: [],
        backups=lambda: [],
        observations=lambda: [],
        alerts=lambda: [],
        probes=lambda: [],
        metrics=None,
        updates=None,
        now=NOW,
    )


def first_deploy_failed(store: NoustStore, domain: str, error: str, job_id: str = "aa11") -> None:
    store.create_job(
        JobRecord(
            id=job_id,
            type="deploy",
            name=f"Deploy {domain}",
            domain=domain,
            status="failed",
            error=error,
            finished_at=(NOW - timedelta(minutes=5)).isoformat(),
        )
    )
    FirstDeploys(store).record(job_id, domain, origin="source", source="https://example.com/r.git")


class TestAFirstDeploymentThatFailed:
    def test_it_needs_attention_even_though_there_is_no_application(
        self, store: NoustStore
    ) -> None:
        first_deploy_failed(store, "shop.example.com", "npm ERR! code ELIFECYCLE\nnpm ERR! more")

        [item] = collect(store)["attention"]

        assert item["id"] == "app:shop.example.com"
        assert item["subject"] == {"kind": "app", "domain": "shop.example.com"}
        assert item["severity"] == "fail"
        [reason] = item["reasons"]
        assert (reason["kind"], reason["code"]) == ("first_deploy", "first_deploy_failed")
        assert reason["detail"] == "npm ERR! code ELIFECYCLE", "the first line, verbatim"
        assert reason["when"] is not None
        assert reason["actions"] == ["view_first_deploy"]

    def test_noust_s_own_sentence_comes_with_its_code(self, store: NoustStore) -> None:
        from noust.core.deploy_failures import release_unhealthy

        first_deploy_failed(
            store,
            "shop.example.com",
            str(release_unhealthy("r1", previous=None, restored=False, evidence="probe: 502")),
        )

        [item] = collect(store)["attention"]

        assert item["reasons"][0]["detail_coded"]["code"] == "deploy_failure.release_unhealthy"

    def test_it_counts_in_the_total(self, store: NoustStore) -> None:
        first_deploy_failed(store, "shop.example.com", "boom")

        assert collect(store)["attention_total"] == 1

    def test_it_stops_needing_attention_once_the_domain_has_an_application(
        self, store: NoustStore
    ) -> None:
        first_deploy_failed(store, "shop.example.com", "boom")
        store.create_app(
            App(
                domain="shop.example.com", app_type="nodejs", app_path="/srv/shop", status="running"
            )
        )

        assert [
            i for i in collect(store)["attention"] if i["reasons"][0]["kind"] == "first_deploy"
        ] == []

    def test_it_stops_needing_attention_once_it_is_put_away(self, store: NoustStore) -> None:
        first_deploy_failed(store, "shop.example.com", "boom")
        FirstDeploys(store).dismiss("aa11")

        assert collect(store)["attention"] == []


class TestADeploymentThatFailedOnAnApplicationThatRuns:
    def deployed(self, store: NoustStore, error: str = "boom") -> None:
        store.create_app(
            App(
                domain="shop.example.com",
                app_type="nodejs",
                app_path="/srv/shop",
                port=3000,
                status="running",
            )
        )
        deployment_id = store.record_deployment_start("shop.example.com", "cli")
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE deployments SET started_at = ?, finished_at = ?, status = 'failed', "
                "error = ? WHERE id = ?",
                (NOW.isoformat(), NOW.isoformat(), error, deployment_id),
            )

    def unit(self, active: str, sub: str) -> dict[str, str]:
        return {
            "name": "shop-example-com",
            "load": "loaded",
            "active": active,
            "sub": sub,
            "app": "shop.example.com",
        }

    def test_it_is_amber_when_the_service_runs(self, store: NoustStore) -> None:
        self.deployed(store)

        [item] = collect(store, [self.unit("active", "running")])["attention"]

        assert item["severity"] == "warn"
        assert item["reasons"][0]["code"] == "deploy_failed"

    def test_it_is_red_when_the_service_itself_failed(self, store: NoustStore) -> None:
        self.deployed(store)

        [item] = collect(store, [self.unit("failed", "failed")])["attention"]

        assert item["severity"] == "fail"
        assert sorted(r["code"] for r in item["reasons"]) == ["deploy_failed", "service_failed"]
