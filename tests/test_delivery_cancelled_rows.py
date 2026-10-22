# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A cancelled deployment is read as cancelled everywhere, not as a failure.

Its row says ``failed`` (the column's CHECK cannot say anything else on a store
from before 4.0) and carries when and by whom it was cancelled. Whatever counts
failures, lists what needs attention or tells an operator the last deployment
broke has to look at that, or an operator who stopped a build on purpose is told
the application has a failed deploy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from noust.core import delivery_store
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.managers import overview
from noust.managers.diagnose import diagnose
from noust.web.api.apps import _last_deployment_out
from noust.web.api.deployments import _to_out

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store with one application and two deployments: one failed, one cancelled.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000))
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def finish(store: NoustStore, *, cancelled: bool) -> int:
    """
    Record a deployment that ended failed, and cancelled when asked.

    Args:
        store: The store.
        cancelled: Whether an operator cancelled it.

    Returns:
        Its id.
    """
    deployment = store.record_deployment_start(DOMAIN, "panel")
    store.finish_deployment(deployment, "failed", error="The update was cancelled by token:ci")
    if cancelled:
        delivery_store.mark_deployment_cancelled(deployment, "token:ci", store=store)
    return deployment


def test_the_api_says_cancelled_and_by_whom(store: NoustStore) -> None:
    deployment = finish(store, cancelled=True)
    record = store.get_deployment(deployment)
    assert record is not None

    out = _to_out(record, {}, {})

    assert out.status == "failed"
    assert (out.cancelled, out.cancelled_by) == (True, "token:ci")
    assert out.cancelled_at is not None
    plain = store.get_deployment(finish(store, cancelled=False))
    assert plain is not None
    assert _to_out(plain, {}, {}).cancelled is False


def test_the_applications_list_carries_cancelled_on_the_last_deployment(
    store: NoustStore,
) -> None:
    """The list a fleet reads says it: a failed status alone would be taken for a fault."""
    cancelled = store.get_deployment(finish(store, cancelled=True))
    failed = store.get_deployment(finish(store, cancelled=False))

    out = _last_deployment_out(cancelled)
    plain = _last_deployment_out(failed)

    assert out is not None
    assert (out.status, out.cancelled) == ("failed", True)
    assert plain is not None
    assert (plain.status, plain.cancelled) == ("failed", False)


def test_the_days_counts_keep_cancelled_apart_from_failed(store: NoustStore) -> None:
    finish(store, cancelled=False)
    finish(store, cancelled=True)
    finish(store, cancelled=True)

    counts = store.count_deployments_since("2000-01-01T00:00:00")

    assert counts == {"failed": 1, "cancelled": 2}
    figure = overview._deploys(store, datetime.now())
    assert (figure["failed"], figure["cancelled"], figure["total"]) == (1, 2, 3)


def test_a_cancelled_deployment_does_not_put_the_application_on_the_attention_list(
    store: NoustStore,
) -> None:
    finish(store, cancelled=True)
    reasons: list[tuple[str, str]] = []

    overview._add_deployments(
        store, {DOMAIN}, lambda key, item, title, reason: reasons.append((key, title))
    )

    assert reasons == []


def test_a_failed_deployment_still_does(store: NoustStore) -> None:
    finish(store, cancelled=False)
    reasons: list[tuple[str, str]] = []

    overview._add_deployments(
        store, {DOMAIN}, lambda key, item, title, reason: reasons.append((key, title))
    )

    assert reasons == [(f"app:{DOMAIN}", DOMAIN)]


def test_diagnose_does_not_blame_a_cancelled_deployment(store: NoustStore) -> None:
    finish(store, cancelled=True)

    diagnosis = diagnose(
        DOMAIN,
        runner=FakeRunner(),
        store=store,
        http_get=lambda url, headers: (200, None),
        disk_usage=lambda path: type("Usage", (), {"total": 100, "used": 10, "free": 90})(),
    )

    last = next(check for check in diagnosis.checks if check.name == "last_deployment")
    assert last.status == "ok"
    assert "cancelled by token:ci" in last.summary
    assert "what served before still serves" in last.summary
