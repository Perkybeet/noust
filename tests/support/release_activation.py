# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_release_activation.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.deployers import lifecycle
from tests.support.release_pipeline import GOOD_SERVER, active_id, deploy_new, node_tree, update


@pytest.fixture
def two_releases(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str]:
    """
    Deploy v1, update to v2, and point the activation at the fake machine.

    Returns:
        The ids of the first and the second release; the second is active.
    """
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    first = active_id(root)
    machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))
    update(machine, monkeypatch)
    second = active_id(root)

    def probe(url: str, **kwargs: Any) -> bool:
        machine.probes.append(url)
        server = root / "current" / "server.js"
        if "throw" in server.read_text():
            kwargs["on_attempt"]("Health check attempt 1 failed: [Errno 111] Connection refused")
            return False
        return True

    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine.services)
    monkeypatch.setattr(lifecycle, "SourceManager", lambda **kwargs: machine.git)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", probe)
    machine.services.restarts.clear()
    machine.probes.clear()
    return first, second
