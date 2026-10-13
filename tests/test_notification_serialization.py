# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification survives being written down and read back.

The delivery ledger keeps the notification of a delivery that has to be tried
again (or held until the quiet hours end), so the structure must round-trip
without losing a fact, a line of the excerpt or the time it happened.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from noust.core.notifications.model import (
    Excerpt,
    Fact,
    Link,
    Notification,
    Section,
    State,
)
from tests.notifications_support import catalog


def _full() -> Notification:
    return Notification(
        kind="deploy_failed",
        code="deploy.failed",
        state=State.FAILED,
        locale="es",
        title="Despliegue fallido",
        subject="shop.example.com",
        summary="Se ha detenido.",
        server="web-1",
        facts=(Fact("server", "Servidor", "web-1"), Fact("commit", "Commit", "abc123", mono=True)),
        command=Fact("retry", "Reintentar", "noust update shop.example.com", mono=True),
        excerpt=Excerpt("Salida", ("linea 1", "linea 2"), omitted=3, pinned=1),
        links=(Link("console", "Abrir la consola", "https://console.example.com/apps/shop"),),
        sections=(
            Section("proc", (Fact("cpu", "CPU", "93%"),), state=State.WARNING),
            Section("sin estado", ()),
        ),
        domain="shop.example.com",
        ts=datetime(2026, 10, 13, 8, 30, 15, tzinfo=timezone.utc),
        id="11111111-2222-3333-4444-555555555555",
    )


def test_a_notification_is_json_and_comes_back_equal() -> None:
    original = _full()

    wire = json.loads(json.dumps(original.to_dict()))
    restored = Notification.from_dict(wire)

    assert restored == original


def test_the_optional_parts_may_be_absent() -> None:
    bare = Notification(
        kind="deploy_started",
        code="deploy.started",
        state=State.PROGRESS,
        locale="en",
        title="Deploying",
        subject="",
        summary="",
        server="web-1",
        ts=datetime(2026, 10, 13, tzinfo=timezone.utc),
        id="x",
    )

    restored = Notification.from_dict(json.loads(json.dumps(bare.to_dict())))

    assert restored == bare
    assert restored.command is None and restored.excerpt is None


@pytest.mark.parametrize("locale", ["en", "es"])
def test_every_canonical_notification_round_trips(locale: str) -> None:
    for code, notification in catalog(locale).items():  # type: ignore[arg-type]
        restored = Notification.from_dict(json.loads(json.dumps(notification.to_dict())))

        assert restored == notification, code


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"kind": "nonsense"},
        {"kind": "deploy_failed", "state": "exploded"},
        "not a mapping",
    ],
)
def test_a_record_that_is_not_a_notification_is_refused(broken: object) -> None:
    with pytest.raises(ValueError):
        Notification.from_dict(broken)  # type: ignore[arg-type]
