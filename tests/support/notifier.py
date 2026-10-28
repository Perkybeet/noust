# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_notifier.py`` that other tests build on."""

from __future__ import annotations

import io
from pathlib import Path
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.notifier import NotificationEvent

DISCORD_URL = "https://discord.test/api/webhooks/1/discord-secret"


@pytest.fixture
def config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """
    Point the global config file at the sandbox and reset the singleton.

    Args:
        sandbox: Isolated filesystem root.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        A fresh configuration carrying only the defaults.
    """
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "wasm" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Resolve every hostname to a public address instead of a real one.

    The SSRF guard in noust.core.notifier resolves every destination before
    dispatch; the suite never opens a real socket, so every test that
    dispatches a notification needs a deterministic stand-in for DNS. Tests
    of the guard itself, in TestSSRFGuard below, override this per test to
    point a host at a forbidden address instead.
    """
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


class CapturingOpener:
    """
    Stands in for ``urllib.request.urlopen``.

    Attributes:
        requests: Every request that would have been sent, in order.
        timeouts: The timeout passed with each call, failed calls included.
        errors: URL prefix mapped to the exception raised instead of sending.
    """

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.timeouts: list[float | None] = []
        self.errors: dict[str, Exception] = {}

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        """
        Record a request, or fail the way urlopen would.

        Args:
            request: The request the notifier built.
            timeout: The deadline the notifier asked for.

        Returns:
            A closeable stand-in for the HTTP response.
        """
        self.timeouts.append(timeout)
        for prefix, error in self.errors.items():
            if request.full_url.startswith(prefix):
                raise error
        self.requests.append(request)
        return io.BytesIO(b"ok")


def make_event(**overrides: object) -> NotificationEvent:
    """
    Build a valid event, overriding only what a test cares about.

    Args:
        overrides: Field values that replace the defaults.

    Returns:
        The event.
    """
    fields: dict = {
        "kind": "deploy_success",
        "title": "Deployed example.com",
        "body": "wasm-example.com is running",
        "domain": "example.com",
    }
    fields.update(overrides)
    return NotificationEvent(**fields)
