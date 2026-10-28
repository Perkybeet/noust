# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_notification_renderers.py`` that other tests build on."""

from __future__ import annotations

from noust.core.notifications.model import Fact, Link, Notification, State
from tests.support.notifications_support import FIXED_TS


def notification(**overrides: object) -> Notification:
    fields: dict = {
        "kind": "deploy_failed",
        "code": "deploy.failed",
        "state": State.FAILED,
        "locale": "en",
        "title": "Deploy failed",
        "subject": "shop.example.com",
        "summary": "It stopped at an error.",
        "server": "web-1",
        "facts": (Fact("server", "Server", "web-1"),),
        "links": (Link("console", "Open in the console", "https://c.example.com/apps/x"),),
        "ts": FIXED_TS,
        "id": "00000000-0000-4000-8000-000000000001",
    }
    fields.update(overrides)
    return Notification(**fields)
