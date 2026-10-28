# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_sampling_plan.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App
from noust.monitor.plan import PlanBuilder

CONTAINER = "0123456789abcdef" * 4


class FakeManager:
    """Names units and answers ``systemctl show`` without systemd."""

    def __init__(
        self,
        units: dict[str, list[str]],
        described: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.units = units
        self.described = described or {}
        self.asked: list[tuple[list[str], tuple[str, ...]]] = []
        self.store = SimpleNamespace(list_services=lambda: [])

    def app_units(self, app: Any, **kwargs: Any) -> list[str]:
        return self.units.get(app.domain, [])

    def describe_units(
        self, names: list[str], properties: tuple[str, ...] = ()
    ) -> dict[str, dict[str, str]]:
        self.asked.append((list(names), tuple(properties)))
        return {name: self.described[name] for name in names if name in self.described}


@pytest.fixture
def mount(tmp_path: Path) -> Path:
    """A fake cgroup v2 mount."""
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu memory\n")
    return root


def app(domain: str, **fields: Any) -> App:
    """An application row that was never stored."""
    values: dict[str, Any] = {
        "domain": domain,
        "app_type": "nextjs",
        "app_path": f"/var/www/apps/{domain.replace('.', '-')}",
        "webserver": "nginx",
    }
    values.update(fields)
    return App(**values)


def lay_unit(mount: Path, control_group: str, *, memory: int | None = 4096) -> Path:
    """Create a unit's cgroup directory with the files systemd gives it."""
    directory = mount / control_group.lstrip("/")
    directory.mkdir(parents=True, exist_ok=True)
    if memory is not None:
        (directory / "memory.current").write_text(f"{memory}\n")
    (directory / "cpu.stat").write_text("usage_usec 1\n")
    return directory


def builder(
    mount: Path,
    manager: FakeManager,
    runner: FakeRunner | None = None,
) -> PlanBuilder:
    """A plan builder over the fake tree and the fake manager."""
    return PlanBuilder(runner=runner or FakeRunner(), cgroup_mount=mount, service_manager=manager)
