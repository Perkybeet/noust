# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for how an application in zero-downtime mode is counted, sampled and diagnosed.

Its idle instance is stopped by design, and after a failed gate it is
failed; neither says anything about the application, which the serving
instance answers for. And its instances live in the slice systemd makes for
the template, not beside every other unit in ``system.slice``, which is
where their CPU and memory are read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.nginx_manager import NginxManager
from noust.monitor.collector import MetricsCollector
from noust.monitor.plan import PlanBuilder, unit_cgroup_path
from noust.monitor.timeseries import MetricsStore
from noust.web.machine import AppTally, read_machine
from tests.test_bluegreen_units import (
    BASE,
    DOMAIN,
    PORT,
    bg_app,
    install_template,
    nginx,
    roomy_disk,
    store,
    unit_dir,
)

__all__ = ["nginx", "store", "unit_dir"]  # fixtures, imported for pytest


def listed(runner: FakeRunner, green: str, blue: str) -> None:
    """Script the one listing systemd gives: green's and blue's ACTIVE and SUB."""
    runner.script(
        ["systemctl", "list-units"],
        stdout="UNIT LOAD ACTIVE SUB DESCRIPTION\n"
        f"{BASE}@green.service loaded {green} WASM\n"
        f"{BASE}@blue.service loaded {blue} WASM\n",
    )


def test_an_app_whose_idle_instance_is_stopped_counts_as_running(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: NoustStore
) -> None:
    root = tmp_path / "apps" / BASE
    bg_app(store, root, color="green")
    install_template(root)
    listed(runner, green="active running", blue="inactive dead")

    assert read_machine(apps_root="/tmp").apps == AppTally(running=1, failed=0, stopped=0, static=0)


def test_an_app_whose_idle_instance_failed_its_gate_counts_as_running(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: NoustStore
) -> None:
    """The failed instance never took traffic; the one that serves is fine."""
    root = tmp_path / "apps" / BASE
    bg_app(store, root, color="green")
    install_template(root)
    listed(runner, green="active running", blue="failed failed")

    assert read_machine(apps_root="/tmp").apps == AppTally(running=1, failed=0, stopped=0, static=0)


def test_an_app_whose_serving_instance_failed_counts_as_failed(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: NoustStore
) -> None:
    root = tmp_path / "apps" / BASE
    bg_app(store, root, color="blue")
    install_template(root)
    listed(runner, green="active running", blue="failed failed")

    assert read_machine(apps_root="/tmp").apps == AppTally(running=0, failed=1, stopped=0, static=0)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_a_plain_unit_is_read_beside_the_others(tmp_path: Path) -> None:
    assert unit_cgroup_path(tmp_path, "shop-example-com") == tmp_path / "shop-example-com.service"


def test_an_instance_is_read_in_the_slice_of_its_template(tmp_path: Path) -> None:
    """systemd puts <prefix>@<instance> in system-<escaped prefix>.slice; a dash is \\x2d."""
    assert unit_cgroup_path(tmp_path, "bg-example-com@green") == (
        tmp_path / "system-bg\\x2dexample\\x2dcom.slice" / "bg-example-com@green.service"
    )


def test_both_instances_of_a_blue_green_app_are_sampled(
    tmp_path: Path,
    runner: FakeRunner,
    unit_dir: Path,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The serving one, and the old one while it drains: the application is their sum."""
    monkeypatch.setattr("noust.monitor.sampler.psutil", None)
    mount = tmp_path / "cgroup"
    (mount / "cgroup.controllers").parent.mkdir(parents=True)
    (mount / "cgroup.controllers").write_text("cpu memory\n")
    slice_dir = mount / "system.slice" / "system-bg\\x2dexample\\x2dcom.slice"
    for color, memory in (("green", 3000), ("blue", 1000)):
        unit = slice_dir / f"{BASE}@{color}.service"
        unit.mkdir(parents=True)
        (unit / "memory.current").write_text(f"{memory}\n")
        (unit / "cpu.stat").write_text("usage_usec 1\n")
    metrics = MetricsStore(tmp_path / "metrics.db")
    root = tmp_path / "apps" / BASE
    app = bg_app(store, root, color="green")
    ticks = iter(float(n) for n in range(100))
    collector = MetricsCollector(
        metrics,
        planner=PlanBuilder(runner=runner, cgroup_mount=mount),
        apps_source=lambda: [app],
        clock=lambda: next(ticks),
    )

    snapshot: dict[str, Any] = collector.sample_once()

    assert app.domain == DOMAIN
    assert snapshot[f"app.{DOMAIN}.mem.bytes"] == 4000
    assert collector.plans[DOMAIN].kind == "blue_green"


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


def test_the_diagnosis_names_the_unit_an_interrupted_switch_left_running(
    tmp_path: Path,
    runner: FakeRunner,
    unit_dir: Path,
    nginx: NginxManager,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turning the mode on was cut short: the app's own unit still runs on blue's port."""
    from noust.managers import diagnose as diagnose_module

    root = tmp_path / "apps" / BASE
    bg_app(store, root, color="green")
    install_template(root)
    nginx.write_upstream(DOMAIN, PORT + 1)
    monkeypatch.setattr(diagnose_module, "NginxManager", lambda **kwargs: nginx)
    runner.script(
        ["systemctl", "show", "-p", "ActiveState,UnitFileState", f"{BASE}.service"],
        stdout="ActiveState=active\nUnitFileState=enabled\n",
    )

    result = diagnose_module.diagnose(
        DOMAIN,
        runner=runner,
        store=store,
        http_get=lambda url, headers: (200, None),
        disk_usage=roomy_disk,  # type: ignore[arg-type]
    )

    check = next(check for check in result.checks if check.name == "blue_green")
    assert check.status == "warn"
    assert f"{BASE}.service" in check.summary
    assert f"systemctl disable --now {BASE}.service" in check.evidence
