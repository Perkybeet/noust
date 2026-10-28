# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_shutdown.py`` that other tests build on."""

from __future__ import annotations

import pytest

from noust.web import server as server_module


def _quiet_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Keep the lifespan's side effects out of a test that only drives its edges.

    Args:
        monkeypatch: Patching helper, scoped to the test.
    """
    from noust.web import metrics_collector

    monkeypatch.setattr(metrics_collector, "start_metrics_collector", lambda: None)
    monkeypatch.setattr(metrics_collector, "stop_metrics_collector", lambda: None)

    class Tokens:
        def purge_expired_sessions(self) -> None:
            return None

    monkeypatch.setattr(server_module, "get_token_manager", Tokens)

    from noust.deployers import inspect as inspect_module

    # The sweep of the machine's temporary directory is not this suite's to run.
    monkeypatch.setattr(inspect_module, "remove_stale_checkouts", lambda: [])

    from noust.fleet import tunnels as tunnels_module

    # Nor is looking for this machine's stale tunnels.
    monkeypatch.setattr(tunnels_module, "sweep_stale_tunnels", lambda: [])
