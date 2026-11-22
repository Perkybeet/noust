# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose stack Noust could not bring under the check of its start for a week needs an operator.

The line ``noust health`` shows is read by whoever looks. What the bell, Home and a fleet's list of what
needs attention show is the Overview's list, so the stack that waits for more than seven days is there
too, on the row of its application, with how long it has waited and what Compose or Docker said.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core import compose_acceptance
from noust.core.store import App, AppType, NoustStore
from noust.managers.overview import collect_overview, reset_cache
from noust.monitor import sampler

SHOP = "shop.example.com"


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


def collect(store: NoustStore) -> dict[str, Any]:
    return collect_overview(
        store=store,
        services=lambda: [],
        certificates=lambda: [],
        backups=lambda: [],
        observations=lambda: [],
        alerts=lambda: [],
        probes=lambda: [],
        metrics=None,
        updates=None,
        now=datetime(2026, 11, 10, 15, 30, 0),
    )


def stack(store: NoustStore, domain: str = SHOP) -> None:
    store.create_app(
        App(domain=domain, app_type=AppType.DOCKER_COMPOSE.value, app_path=f"/apps/{domain}")
    )


def waiting(store: NoustStore, domain: str, days: int, state: str = "pending") -> None:
    """Note a stack as it has been for ``days``: the date is what the note's own clock wrote."""
    compose_acceptance.note(
        domain, state, "Docker Compose could not resolve the stack", store=store
    )
    since = (datetime.now(timezone.utc) - timedelta(days=days, minutes=5)).isoformat(
        timespec="seconds"
    )
    store._get_connection().execute(
        "UPDATE compose_gate_state SET noted_at = ? WHERE domain = ?", (since, domain)
    )
    store._get_connection().commit()


def reasons_of(overview: dict[str, Any], domain: str) -> list[dict[str, Any]]:
    return [
        reason
        for item in overview["attention"]
        if item["id"] == f"app:{domain}"
        for reason in item["reasons"]
        if reason["code"] == "compose_check_waiting"
    ]


class TestTheOverviewListsTheStackThatWaitsForAWeek:
    def test_a_stack_that_has_waited_eight_days_is_on_the_list_with_the_days_and_what_was_said(
        self, store: NoustStore
    ) -> None:
        stack(store)
        waiting(store, SHOP, 8)

        [why] = reasons_of(collect(store), SHOP)

        assert why["params"] == {"days": 8}
        assert why["detail"] == "Docker Compose could not resolve the stack"

    def test_a_stack_that_has_waited_six_days_is_not(self, store: NoustStore) -> None:
        stack(store)
        waiting(store, SHOP, 6)

        assert reasons_of(collect(store), SHOP) == []

    def test_a_stack_whose_start_was_held_back_is_not_waiting_for_the_step(
        self, store: NoustStore
    ) -> None:
        stack(store)
        waiting(store, SHOP, 20, state="refused")

        assert reasons_of(collect(store), SHOP) == []

    def test_a_note_of_an_application_that_is_gone_is_not_listed(self, store: NoustStore) -> None:
        waiting(store, SHOP, 20)

        assert collect(store)["attention"] == []

    def test_a_stack_that_was_brought_under_the_check_leaves_the_list(
        self, store: NoustStore
    ) -> None:
        stack(store)
        waiting(store, SHOP, 20)
        compose_acceptance.clear_note(SHOP, store=store)

        assert reasons_of(collect(store), SHOP) == []

    def test_a_note_that_cannot_be_read_does_not_take_the_list_down(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        stack(store)

        def broken(*, store: Any = None) -> Any:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(compose_acceptance, "notes", broken)

        assert collect(store)["attention"] == []
