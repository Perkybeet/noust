# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Overview's list of what needs attention carries the machine's facts and the findings of Security
(item 161).

The fleet reads each server's own Overview, so a disk that is full or a database that is open to the
internet counts on a central the same way it counts on the server: one list, one place that builds
it, and a row that says where it comes from.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import NoustError
from noust.core.store import App, NoustStore
from noust.managers.overview import collect_overview, reset_cache
from noust.monitor import sampler

NOW = datetime(2026, 11, 12, 12, 0, 0)

SUMMARY: dict[str, Any] = {
    "os": {"name": "Ubuntu 24.04", "eol": {"status": "ok"}},
    "updates": {"supported": True, "pending": 5, "security": 2},
    "reboot": {"required": True, "packages": ["linux-image"]},
    "disk": {"status": "critical", "worst_mount": "/var", "worst_percent": 97.0},
    "time": {"synchronized": True},
    "swap": {"total_bytes": 0, "recommended": False},
    "system": {"failed_units": ["fail2ban.service", "shop-example-com.service"]},
}

CHECKS: list[dict[str, Any]] = [
    {
        "id": "fw.docker_bypass",
        "group": "firewall",
        "title": "Docker publishes ports around the firewall",
        "severity": "critical",
        "status": "fail",
        "reason": "Port 5432 is published on 0.0.0.0 by Docker.",
    },
    {
        "id": "upd.security_pending",
        "group": "updates",
        "title": "Security updates are pending",
        "severity": "critical",
        "status": "fail",
        "reason": "2 security updates are pending.",
    },
]


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


def collect(store: NoustStore, **collaborators: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "services": lambda: [],
        "certificates": lambda: [],
        "backups": lambda: [],
        "observations": lambda: [],
        "alerts": lambda: [],
        "probes": lambda: [],
        "metrics": None,
        "updates": None,
        "security": None,
        "now": NOW,
    }
    defaults.update(collaborators)
    return collect_overview(store=store, **defaults)


def test_the_machine_and_the_findings_join_the_applications_in_one_list(store: NoustStore) -> None:
    store.create_app(
        App(
            domain="shop.example.com",
            app_type="nextjs",
            source="https://github.com/you/app",
            port=3000,
            app_path="/var/www/apps/shop.example.com",
            status="running",
        )
    )
    units = [
        {
            "name": "shop-example-com",
            "load": "loaded",
            "active": "failed",
            "sub": "failed",
            "app": "shop.example.com",
        }
    ]

    body = collect(store, services=lambda: units, updates=lambda: SUMMARY, security=lambda: CHECKS)

    rows = {item["id"]: item for item in body["attention"]}
    assert set(rows) == {
        "app:shop.example.com",
        "machine:disk",
        "machine:reboot_required",
        "machine:security_updates",
        "machine:failed_units",
        "security:fw.docker_bypass",
    }
    # The check the machine's own fact says with its numbers is said once.
    assert "security:upd.security_pending" not in rows
    assert body["attention_total"] == 6
    assert {item["origin"] for item in body["attention"]} == {"state", "security"}


def test_a_unit_that_is_already_a_row_is_not_said_again_as_a_failed_service(
    store: NoustStore,
) -> None:
    units = [
        {
            "name": "shop-example-com",
            "load": "loaded",
            "active": "failed",
            "sub": "failed",
            "app": "shop.example.com",
        }
    ]

    body = collect(store, services=lambda: units, updates=lambda: SUMMARY)

    [failed] = [item for item in body["attention"] if item["id"] == "machine:failed_units"]
    assert failed["reasons"][0]["params"]["units"] == ["fail2ban.service"]


def test_every_row_and_every_reason_says_where_it_comes_from(store: NoustStore) -> None:
    body = collect(store, updates=lambda: SUMMARY, security=lambda: CHECKS)

    for item in body["attention"]:
        assert item["origin"] in {"state", "alert", "security"}
        assert all(reason["origin"] == item["origin"] for reason in item["reasons"])


def test_the_worst_comes_first_whatever_the_source(store: NoustStore) -> None:
    body = collect(store, updates=lambda: SUMMARY, security=lambda: CHECKS)

    severities = [item["severity"] for item in body["attention"]]
    assert severities == sorted(severities, key={"fail": 0, "warn": 1}.get)


def test_a_summary_that_cannot_be_read_costs_the_list_only_the_machine(store: NoustStore) -> None:
    def broken() -> dict[str, Any]:
        raise NoustError("apt is locked")

    body = collect(store, updates=broken, security=lambda: CHECKS)

    assert [item["id"] for item in body["attention"]] == ["security:fw.docker_bypass"]
    assert "apt is locked" in body["updates"]["error"]


def test_findings_that_cannot_be_read_cost_the_list_only_the_findings(store: NoustStore) -> None:
    def broken() -> list[dict[str, Any]]:
        raise OSError("the report is unreadable")

    body = collect(store, updates=lambda: SUMMARY, security=broken)

    assert "security:fw.docker_bypass" not in {item["id"] for item in body["attention"]}
    assert "machine:disk" in {item["id"] for item in body["attention"]}


def test_the_summary_is_read_once_for_the_figure_and_for_the_list(store: NoustStore) -> None:
    calls: list[int] = []

    def summary() -> dict[str, Any]:
        calls.append(1)
        return SUMMARY

    collect(store, updates=summary)

    assert len(calls) == 1


def test_a_server_with_no_summary_and_no_checks_lists_what_it_always_did(store: NoustStore) -> None:
    body = collect(store)

    assert body["attention"] == []
    assert body["attention_total"] == 0


class TestAnApplicationThatStoppedAnswering:
    """
    The monitor decides that (three probes in a row over a minute, the deploy's own health check) and
    keeps it in a sidecar beside the store; the list reads it, so the count does not depend on which
    page of the console happened to load the applications.
    """

    @staticmethod
    def outage(
        store: NoustStore, domain: str, *, announced: bool = True, failures: int = 4
    ) -> None:
        path = Path(store.db_path).parent / "app-reachability.json"
        path.write_text(
            json.dumps(
                {
                    domain: {
                        "first": 1_790_000_000.0,
                        "last": 1_790_000_300.0,
                        "failures": failures,
                        "announced": announced,
                        "probe": "HTTP 502 Bad Gateway",
                    }
                }
            )
        )

    @staticmethod
    def app(store: NoustStore, domain: str) -> None:
        store.create_app(
            App(
                domain=domain,
                app_type="nextjs",
                source="https://example.com/app.git",
                port=3000,
                app_path=f"/var/www/apps/{domain}",
                status="running",
            )
        )

    def test_an_announced_outage_is_a_failure_with_what_the_probe_said(
        self, store: NoustStore
    ) -> None:
        self.app(store, "shop.example.com")
        self.outage(store, "shop.example.com")

        [item] = collect(store)["attention"]

        assert item["id"] == "app:shop.example.com"
        assert item["severity"] == "fail"
        [reason] = item["reasons"]
        assert (reason["kind"], reason["code"]) == ("state", "no_answer")
        assert reason["detail"] == "HTTP 502 Bad Gateway"
        assert reason["when"] is not None
        assert reason["actions"] == ["view_log", "diagnose"]

    def test_a_run_of_failures_that_is_not_an_outage_yet_says_nothing(
        self, store: NoustStore
    ) -> None:
        self.app(store, "shop.example.com")
        self.outage(store, "shop.example.com", announced=False, failures=2)

        assert collect(store)["attention"] == []

    def test_it_joins_the_row_of_an_application_that_is_already_there(
        self, store: NoustStore
    ) -> None:
        self.app(store, "shop.example.com")
        self.outage(store, "shop.example.com")
        units = [
            {
                "name": "shop-example-com",
                "load": "loaded",
                "active": "failed",
                "sub": "failed",
                "app": "shop.example.com",
            }
        ]

        [item] = collect(store, services=lambda: units)["attention"]

        assert sorted(reason["code"] for reason in item["reasons"]) == [
            "no_answer",
            "service_failed",
        ]

    def test_an_application_that_no_longer_exists_is_not_listed(self, store: NoustStore) -> None:
        self.outage(store, "gone.example.com")

        assert collect(store)["attention"] == []

    def test_a_missing_or_damaged_sidecar_is_nothing_known_and_not_an_error(
        self, store: NoustStore
    ) -> None:
        self.app(store, "shop.example.com")
        (Path(store.db_path).parent / "app-reachability.json").write_text("{not json")

        assert collect(store)["attention"] == []
