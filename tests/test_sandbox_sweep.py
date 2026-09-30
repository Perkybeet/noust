"""
Build units a dead process left behind are swept when the console or the monitor starts.

A sandboxed build runs in a transient unit (``noust-build-<app>-<hex>``) that
``--collect`` unloads when it ends and ``RuntimeMaxSec`` stops if the process
that started it dies. What can still be left: a unit systemd kept as failed, a
unit still active past its own deadline, and the 0600 environment file (the
build's secrets, on tmpfs) of a unit whose owner was killed before it could
remove it. The sweep takes care of the three and touches nothing that is
still inside its deadline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import paths
from noust.core.runner import FakeRunner
from noust.deployers.helpers import sandbox as build_sandbox

LISTED = (
    "noust-build-shop-example-com-1a2b3c4d.service loaded failed failed noust build\n"
    "noust-build-blog-example-com-5e6f7a8b.service loaded active running noust build\n"
    "noust-build-api-example-com-9c0d1e2f.service loaded active running noust build\n"
)

NOW_US = 10_000_000_000


def show(active: str, entered_s_ago: float, runtime_max_s: int) -> str:
    return (
        f"ActiveState={active}\n"
        f"ActiveEnterTimestampMonotonic={int(NOW_US - entered_s_ago * 1_000_000)}\n"
        f"RuntimeMaxUSec={runtime_max_s * 1_000_000}\n"
    )


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "sandbox-run"
    directory.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_RUNTIME_DIR", directory)
    return directory


@pytest.fixture
def machine() -> FakeRunner:
    runner = FakeRunner()
    runner.script(["systemctl", "list-units"], stdout=LISTED)
    runner.script(
        ["systemctl", "show", "noust-build-shop-example-com-1a2b3c4d.service"],
        stdout=show("failed", 4000, 960),
    )
    runner.script(
        ["systemctl", "show", "noust-build-blog-example-com-5e6f7a8b.service"],
        stdout=show("active", 5000, 960),
    )
    runner.script(
        ["systemctl", "show", "noust-build-api-example-com-9c0d1e2f.service"],
        stdout=show("active", 30, 960),
    )
    return runner


def test_failed_units_are_reset_and_overdue_ones_stopped(
    machine: FakeRunner, runtime: Path
) -> None:
    swept = build_sandbox.sweep_orphaned_builds(machine, now_us=lambda: NOW_US)

    assert ("systemctl", "reset-failed", "noust-build-shop-example-com-1a2b3c4d.service") in (
        machine.calls
    )
    assert ("systemctl", "stop", "noust-build-blog-example-com-5e6f7a8b.service") in machine.calls
    assert not any(
        "noust-build-api-example-com-9c0d1e2f.service" in c
        for c in machine.calls
        if c[1] in ("stop", "reset-failed")
    )
    assert swept.reset == ["noust-build-shop-example-com-1a2b3c4d.service"]
    assert swept.stopped == ["noust-build-blog-example-com-5e6f7a8b.service"]


def test_the_secrets_of_a_unit_that_is_gone_are_removed(machine: FakeRunner, runtime: Path) -> None:
    import os
    import time

    gone = runtime / "noust-build-old-example-com-00000000.env"
    running = runtime / "noust-build-api-example-com-9c0d1e2f.env"
    starting = runtime / "noust-build-new-example-com-11111111.env"
    other = runtime / "empty"
    for path in (gone, running, starting, other):
        path.write_text("SECRET=1\n")
    old = time.time() - 3600
    for path in (gone, running, other):
        os.utime(path, (old, old))

    swept = build_sandbox.sweep_orphaned_builds(machine, now_us=lambda: NOW_US)

    assert not gone.exists()
    # A live unit's, a build that is starting right now, and what is not an
    # environment file are left alone.
    assert running.exists() and starting.exists() and other.exists()
    assert swept.files == [gone.name]


def test_a_machine_without_systemd_sweeps_nothing(runtime: Path) -> None:
    runner = FakeRunner()
    runner.script(["systemctl", "list-units"], stderr="System has not been booted", exit_code=1)

    swept = build_sandbox.sweep_orphaned_builds(runner, now_us=lambda: NOW_US)

    assert (swept.reset, swept.stopped, swept.files) == ([], [], [])
    assert runner.calls == [runner.calls[0]]


def test_a_process_that_is_not_root_has_no_sandbox_to_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build_sandbox, "running_as_root", lambda: False)

    assert build_sandbox.sweep_at_start() is None


def test_the_sweep_at_start_uses_the_process_runner(
    machine: FakeRunner, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.runner import set_runner

    monkeypatch.setattr(build_sandbox, "running_as_root", lambda: True)
    set_runner(machine)
    try:
        report = build_sandbox.sweep_at_start()
    finally:
        set_runner(None)

    assert report is not None
    assert report.reset == ["noust-build-shop-example-com-1a2b3c4d.service"]
