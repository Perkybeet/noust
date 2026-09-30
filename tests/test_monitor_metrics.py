# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the monitor as the host of the metrics history.

The history used to exist only while a console ran. Now the ``noust-monitor``
daemon records it, and the package installs and enables the daemon by default.
What is defended:

- **The daemon hosts the collector**: it starts with the daemon, stops with it,
  and a database that cannot be opened costs history, not monitoring.
- **The package installs the monitor unless somebody said no.** A monitor that is
  installed stays as it is (enabled or disabled), one an operator disabled or
  removed stays off, a server still on WASM's names is left to its migration, and
  a machine without systemd has nothing to install into.
- **``noust monitor status`` says whether history is being recorded**, and why
  not, without creating a database to say it.
"""

from __future__ import annotations

import functools
import io
import sqlite3
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.commands import monitor as cli_monitor
from noust.core.exceptions import MonitorError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.monitor import process_monitor as process_monitor_module
from noust.monitor.process_monitor import DECLINED_MARKER_NAME, MonitorConfig, ProcessMonitor
from noust.monitor.timeseries import MetricsStore

NOW = 1_700_002_800


class FakeCollector:
    """A collector that only records how it was driven."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped = threading.Event()

    def start(self) -> None:
        self.started.set()

    def stop(self) -> None:
        self.stopped.set()


@pytest.fixture
def machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> types.SimpleNamespace:
    """
    A machine made of fixtures: a unit directory, a runtime directory, a bin.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
        runner: The fake runner, installed process-wide.

    Returns:
        The directories and the runner.
    """
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    runtime = tmp_path / "run-systemd"
    runtime.mkdir()
    monkeypatch.setattr(process_monitor_module, "SYSTEMD_DIR", unit_dir)
    monkeypatch.setattr(process_monitor_module, "SYSTEMD_RUNTIME_DIR", runtime)
    binary = tmp_path / "bin" / "noust"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent))
    return types.SimpleNamespace(
        unit_dir=unit_dir, runtime=runtime, runner=runner, declined=tmp_path / DECLINED_MARKER_NAME
    )


def monitor(machine: types.SimpleNamespace, **kwargs: Any) -> ProcessMonitor:
    """Build a monitor on the fixture machine."""
    return ProcessMonitor(
        config=MonitorConfig(),
        runner=machine.runner,
        declined_path=machine.declined,
        **kwargs,
    )


# --------------------------------------------------------------- the collector


def test_the_daemon_starts_the_collector_and_stops_it_with_itself(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording lives as long as the daemon does, and hands its lease over when it ends."""
    collector = FakeCollector()
    daemon = monitor(machine, metrics_collector=collector)  # type: ignore[arg-type]
    for name in ("_log_metrics", "_report_services", "_check_certificates", "scan_once"):
        monkeypatch.setattr(daemon, name, lambda *a, **k: None)
    monkeypatch.setattr(daemon, "_purge_old_observations", lambda: None)
    monkeypatch.setattr(process_monitor_module.time, "sleep", lambda seconds: None)

    thread = threading.Thread(target=daemon.run)
    thread.start()
    try:
        assert collector.started.wait(5)
    finally:
        daemon.stop()
        thread.join(timeout=5)

    assert collector.stopped.is_set()
    assert not thread.is_alive()


def test_a_history_database_that_cannot_open_does_not_stop_the_monitor(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observing must not depend on charting."""
    daemon = monitor(machine)
    daemon.logger = Logger(stream=io.StringIO())
    monkeypatch.setattr(
        process_monitor_module,
        "create_collector",
        lambda kind: (_ for _ in ()).throw(OSError("read-only file system")),
    )

    daemon._start_metrics()

    assert daemon._metrics is None
    daemon._stop_metrics()


def test_the_unit_runs_at_low_priority_and_still_only_writes_its_own_state(
    machine: types.SimpleNamespace,
) -> None:
    """It samples every few seconds forever: it must not compete with what it measures."""
    unit = monitor(machine)._unit_content()

    assert "\nNice=10\n" in unit
    assert "ProtectSystem=strict" in unit
    assert "StateDirectory=noust" in unit


def test_the_unit_can_write_the_audit_log_it_records_to(machine: types.SimpleNamespace) -> None:
    """
    Every command, 'monitor run' included, records to the audit log in the
    configuration directory, which ProtectSystem=strict makes read-only. The
    '-' keeps a missing directory from failing the namespace (226/NAMESPACE).
    """
    from noust.core import paths

    unit = monitor(machine)._unit_content()

    assert f"\nReadWritePaths=-{paths.config_dir()}\n" in unit


def test_the_promise_about_command_lines_names_its_one_exception() -> None:
    """The guarantee the CLI prints is true of the code that reads a PHP worker's title."""
    scope = " ".join(process_monitor_module.MONITOR_SCOPE)

    assert "PHP-FPM" in scope
    assert "command line" in scope


# -------------------------------------------------------- installing by default


def test_a_server_that_never_had_the_monitor_gets_it_enabled(
    machine: types.SimpleNamespace,
) -> None:
    """The package's first install: the unit is written, then enabled and started."""
    outcome = monitor(machine).install_by_default()

    assert outcome == "enabled"
    assert (machine.unit_dir / "noust-monitor.service").is_file()
    assert ("systemctl", "enable", "--now", "noust-monitor") in machine.runner.calls
    assert ("systemctl", "daemon-reload") in machine.runner.calls


def test_a_monitor_that_is_installed_is_left_as_it_is(machine: types.SimpleNamespace) -> None:
    """Enabled, or installed and then disabled by its operator: never touched."""
    (machine.unit_dir / "noust-monitor.service").write_text("[Service]\n")

    outcome = monitor(machine).install_by_default()

    assert outcome == "installed"
    assert machine.runner.calls == []
    assert (machine.unit_dir / "noust-monitor.service").read_text() == "[Service]\n"


def test_a_monitor_the_operator_turned_off_stays_off_across_upgrades(
    machine: types.SimpleNamespace,
) -> None:
    """Disabling remembers the choice, so an upgrade does not undo it."""
    daemon = monitor(machine)
    daemon.install_service()
    daemon.enable_service()
    daemon.disable_service()
    assert machine.declined.is_file()

    outcome = monitor(machine).install_by_default()
    machine.runner.calls.clear()
    (machine.unit_dir / "noust-monitor.service").unlink()

    assert outcome == "installed", "the unit is still there, disabled"
    assert monitor(machine).install_by_default() == "declined"
    assert machine.runner.calls == []


def test_a_monitor_the_operator_removed_stays_removed(machine: types.SimpleNamespace) -> None:
    """Uninstalling is a decision too: the unit is gone, the marker remains."""
    daemon = monitor(machine)
    daemon.install_service()
    daemon.uninstall_service()
    machine.runner.calls.clear()

    assert monitor(machine).install_by_default() == "declined"
    assert machine.runner.calls == []


def test_a_monitor_removed_before_the_marker_existed_is_not_brought_back(
    machine: types.SimpleNamespace,
) -> None:
    """3.0's uninstall left no marker; the journal still remembers the unit ran."""
    machine.runner.script(
        ["journalctl", "--unit", "noust-monitor.service"],
        stdout="Noust process monitor started\n",
    )

    assert monitor(machine).install_by_default() == "removed"
    assert not (machine.unit_dir / "noust-monitor.service").exists()
    assert machine.declined.is_file(), "the inference is kept, so it is made once"
    assert not any(call[:2] == ("systemctl", "enable") for call in machine.runner.calls)


def test_wasms_monitor_in_the_journal_counts_too(machine: types.SimpleNamespace) -> None:
    """A 2.x server that removed wasm-monitor and then moved onto Noust's names."""
    machine.runner.script(["journalctl", "--unit", "wasm-monitor.service"], stdout="started\n")

    assert monitor(machine).install_by_default() == "removed"


def test_observations_recorded_here_before_count_as_a_monitor_that_ran(
    machine: types.SimpleNamespace, tmp_path: Path
) -> None:
    """Only a monitor writes observations; the file alone is not evidence."""
    from noust.monitor.observation_store import ObservationStore

    store = ObservationStore(db_path=tmp_path / "observations.db")
    store.stats()
    assert monitor(machine, store=store).install_by_default() == "enabled"

    (machine.unit_dir / "noust-monitor.service").unlink()
    machine.declined.unlink(missing_ok=True)
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "INSERT INTO observations (observed_at, pid, process_name, signal, severity)"
        " VALUES ('2026-01-01T00:00:00', 4242, 'xmrig', 'high_cpu', 'warning')"
    )
    conn.commit()
    conn.close()

    assert monitor(machine, store=store).install_by_default() == "removed"


def test_turning_it_on_again_forgets_the_refusal(machine: types.SimpleNamespace) -> None:
    """noust monitor enable is the way back: the marker goes."""
    daemon = monitor(machine)
    daemon.install_service()
    daemon.disable_service()
    assert machine.declined.is_file()

    daemon.enable_service()

    assert not machine.declined.exists()


def test_a_server_still_on_wasms_names_is_left_to_its_migration(
    machine: types.SimpleNamespace,
) -> None:
    """Writing noust-monitor beside wasm-monitor would run two monitors."""
    (machine.unit_dir / "wasm-monitor.service").write_text("[Service]\n")

    assert monitor(machine).install_by_default() == "legacy"
    assert not (machine.unit_dir / "noust-monitor.service").exists()


def test_a_machine_without_systemd_has_nothing_to_install_into(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A container or a chroot: the console records the history while it runs."""
    monkeypatch.setattr(process_monitor_module, "SYSTEMD_RUNTIME_DIR", tmp_path / "absent")

    assert monitor(machine).install_by_default() == "no_systemd"
    assert machine.runner.calls == []


def test_without_psutil_the_monitor_is_not_enabled(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A monitor that cannot run would fail every thirty seconds forever."""
    import sys

    monkeypatch.setitem(sys.modules, "psutil", None)

    assert monitor(machine).install_by_default() == "no_psutil"
    assert not (machine.unit_dir / "noust-monitor.service").exists()


def test_a_failure_to_write_the_unit_is_an_error_the_caller_can_read(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The package hook ignores it, but the command says why."""
    monkeypatch.setattr(process_monitor_module, "write_file", lambda *a, **k: False)

    with pytest.raises(MonitorError):
        monitor(machine).install_by_default()


# ------------------------------------------------------------------------- CLI


@pytest.fixture
def cli_output(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Collect what the commands print through the logger."""
    buffer = io.StringIO()
    monkeypatch.setattr(cli_monitor, "Logger", functools.partial(Logger, stream=buffer))
    return buffer


def invoke(*args: str) -> Any:
    """Run ``noust monitor`` with the given arguments."""
    return CliRunner().invoke(cli_monitor.cli, list(args))


def test_autoenable_is_what_the_package_runs(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch, cli_output: io.StringIO
) -> None:
    """Root, installed and enabled, and a sentence saying so."""
    monkeypatch.setattr(cli_monitor, "check_root", lambda: True)

    result = invoke("autoenable")

    assert result.exit_code == 0, result.output
    assert "noust-monitor" in " ".join(" ".join(call) for call in machine.runner.calls)
    assert "records the metrics history" in cli_output.getvalue()


def test_autoenable_needs_root(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It writes a systemd unit."""
    monkeypatch.setattr(cli_monitor, "check_root", lambda: False)

    result = invoke("autoenable")

    assert result.exit_code != 0


def test_autoenable_leaves_a_disabled_monitor_alone(
    machine: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch, cli_output: io.StringIO
) -> None:
    """The message says it was left off, and how to turn it on."""
    monkeypatch.setattr(cli_monitor, "check_root", lambda: True)
    (machine.unit_dir / "noust-monitor.service").write_text("[Service]\n")

    result = invoke("autoenable")

    assert result.exit_code == 0
    assert machine.runner.calls == []
    assert "left as it is" in cli_output.getvalue()


def test_status_says_whether_history_is_being_recorded(
    machine: types.SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON carries the same status the API does."""
    database = tmp_path / "metrics.db"
    store = MetricsStore(database, clock=lambda: int(time.time()))
    store.acquire_collector("monitor", kind="daemon", interval_s=5)
    monkeypatch.setattr(cli_monitor, "default_metrics_db_path", lambda: database)
    monkeypatch.setattr(cli_monitor, "MetricsStore", lambda path: MetricsStore(path))
    machine.runner.script(["systemctl", "is-enabled"], stdout="enabled\n")
    machine.runner.script(["systemctl", "is-active"], stdout="active\n")

    from noust.cli.commands.monitor import _status_as_dict

    payload = _status_as_dict(ProcessMonitor(verbose=False, runner=machine.runner))
    assert payload["history"]["recording"] is True
    assert payload["history"]["host"] == "daemon"
    assert payload["history"]["retention_days"] == 400


def test_status_does_not_create_a_database_to_say_there_is_none(
    machine: types.SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking must not write."""
    from noust.cli.commands.monitor import _status_as_dict

    database = tmp_path / "state" / "metrics.db"
    monkeypatch.setattr(cli_monitor, "default_metrics_db_path", lambda: database)

    payload = _status_as_dict(ProcessMonitor(verbose=False, runner=machine.runner))

    assert payload["history"] is None
    assert not database.exists()


def test_systemd_stopping_the_unit_is_an_orderly_stop(
    monkeypatch: pytest.MonkeyPatch, cli_output: io.StringIO
) -> None:
    """
    SIGTERM ends a Python process on the spot; the collector must still give its
    lease up, or the next daemon waits for it to expire.
    """
    import signal

    seen: dict[str, Any] = {"stopped": False}

    class FakeMonitor:
        config = types.SimpleNamespace(scan_interval=60)

        def __init__(self, verbose: bool = False) -> None:
            pass

        def run(self) -> None:
            signal.raise_signal(signal.SIGTERM)

        def stop(self) -> None:
            seen["stopped"] = True

    monkeypatch.setattr(cli_monitor, "ProcessMonitor", FakeMonitor)
    before = signal.getsignal(signal.SIGTERM)

    code = cli_monitor._run_foreground()

    assert code == 0
    assert seen["stopped"], "SIGTERM did not reach the monitor as a stop"
    assert signal.getsignal(signal.SIGTERM) == before, "the handler was not put back"
