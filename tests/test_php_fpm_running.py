# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A deployed PHP application is a running application, not a static site.

It is stored as static, because no unit of its own runs it, and every place
that asked ``is_static`` treated it as files nginx serves: healthy without a
look, "no service to restart", 404 on the console's start/stop/restart,
limits refused, diagnosis skipped. Each of those now asks its pool, through
:func:`noust.deployers.helpers.php_fpm.is_php_fpm`:

- its state is FPM's, the pool file's and the gate's FastCGI probe's;
- restart reloads FPM, stop moves the pool aside, start puts it back;
- limits rewrite the pool behind the gate;
- the diagnosis and the machine tally read the pool.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import app_state
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import lifecycle
from noust.deployers import php_fpm as php_module
from noust.deployers.php_fpm import control_pool
from noust.managers.diagnose import diagnose
from noust.managers.service_manager import ResourceLimits, ServiceManager
from noust.web import machine as machine_module

DOMAIN = "blog.example.com"
APP = "blog-example-com"
SERVICE = "php8.2-fpm"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store of this test's own, installed as the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    NoustStore._instance = instance
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def php(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """
    A PHP application deployed in place, its pool written, FPM running.

    ``answers`` decides what the FastCGI probe says; ``probes`` counts it.
    """
    fpm_root = tmp_path / "fpm-root"
    pool_dir = fpm_root / "etc/php/8.2/fpm/pool.d"
    pool_dir.mkdir(parents=True)
    (fpm_root / "usr/sbin").mkdir(parents=True)
    (fpm_root / "usr/sbin/php-fpm8.2").write_text("")
    monkeypatch.setattr(php_module, "FPM_ROOT", fpm_root)

    root = tmp_path / "apps" / APP
    root.mkdir(parents=True)
    (root / "index.php").write_text("<?php\n")
    pool = pool_dir / f"wasm-{APP}.conf"
    pool.write_text(f"[wasm-{APP}]\nphp_admin_value[memory_limit] = 256M\n")
    store.create_app(App(domain=DOMAIN, app_type="php-fpm", app_path=str(root), is_static=True))

    state = SimpleNamespace(answers=True, probes=0)

    def fake_probe(socket_path: Path, params: Callable[[], Any], **_kwargs: Any) -> Any:
        def probe(_url: str, **kwargs: Any) -> bool:
            state.probes += 1
            params()
            if not state.answers and kwargs.get("on_attempt"):
                kwargs["on_attempt"]("Health check attempt 1 failed: HTTP 500\nPHP Fatal error")
            return bool(state.answers)

        return probe

    monkeypatch.setattr(php_module, "fastcgi_probe", fake_probe)
    runner.script(["systemctl", "is-active", SERVICE], stdout="active\n")
    state.root = root
    state.pool = pool
    state.disabled = pool.with_name(pool.name + ".disabled")
    state.runner = runner
    state.store = store
    return state


def app_of(php: SimpleNamespace) -> App:
    """The application's row as it is now."""
    app = php.store.get_app(DOMAIN)
    assert app is not None
    return app


def state_of(php: SimpleNamespace, *, probe: bool = True) -> app_state.AppState:
    """What wasm list, the API and /events would say."""
    return app_state.resolve_state(app_of(php), ServiceManager(), probe=probe)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def test_a_pool_that_answers_is_running(php: SimpleNamespace) -> None:
    """Asked over FastCGI, as the gate asks it; not Static."""
    current = state_of(php)

    assert (current.label, current.healthy) == (app_state.RUNNING, True)
    assert php.probes == 1


def test_a_pool_that_does_not_answer_says_why(php: SimpleNamespace) -> None:
    """PHP's own error reaches the detail."""
    php.answers = False

    current = state_of(php)

    assert current.label == app_state.NOT_RESPONDING
    assert "PHP Fatal error" in current.detail


def test_fpm_down_is_a_failure_of_every_php_application(php: SimpleNamespace) -> None:
    """The pool is not asked when the master is not running."""
    php.runner.script(["systemctl", "is-active", SERVICE], stdout="failed\n", exit_code=3)

    current = state_of(php)

    assert current.label == app_state.FAILED
    assert SERVICE in current.detail and "failed" in current.detail
    assert php.probes == 0


def test_a_stopped_pool_is_stopped_and_a_missing_one_failed(php: SimpleNamespace) -> None:
    """Moved aside by a stop, or gone."""
    php.pool.rename(php.disabled)
    assert state_of(php).label == app_state.STOPPED

    php.disabled.unlink()
    missing = state_of(php)
    assert missing.label == app_state.FAILED
    assert "noust update" in missing.detail


def test_without_the_probe_the_socket_stands_for_it(php: SimpleNamespace) -> None:
    """probe=False asks nothing over FastCGI; no socket is no answer."""
    current = state_of(php, probe=False)

    assert php.probes == 0
    assert current.label == app_state.NOT_RESPONDING
    assert "/run/php/wasm-blog-example-com.sock" in current.detail


# ---------------------------------------------------------------------------
# Start, stop, restart
# ---------------------------------------------------------------------------


def test_stop_disables_the_pool_and_start_restores_it(php: SimpleNamespace) -> None:
    """The file moves aside and back; FPM tests it and reloads each time."""
    message = control_pool(app_of(php), "stop")

    assert not php.pool.exists() and php.disabled.is_file()
    assert "Disabled" in message
    assert php.runner.ran("systemctl", "reload-or-restart", SERVICE)
    assert "already disabled" in control_pool(app_of(php), "stop")

    message = control_pool(app_of(php), "start")

    assert php.pool.is_file() and not php.disabled.exists()
    assert "Enabled" in message
    assert php.runner.ran("/usr/sbin/php-fpm8.2", "-t")


def test_a_start_fpm_refuses_leaves_the_pool_aside(php: SimpleNamespace) -> None:
    """One bad pool would stop every PHP site at FPM's next reload."""
    control_pool(app_of(php), "stop")
    php.runner.script(["/usr/sbin/php-fpm8.2", "-t"], stderr="ERROR: bad pool", exit_code=78)

    with pytest.raises(ValidationError, match="rejected the pool") as failure:
        control_pool(app_of(php), "start")

    assert "bad pool" in failure.value.details
    assert php.disabled.is_file() and not php.pool.exists()


def test_restart_reloads_fpm_and_refuses_a_stopped_pool(php: SimpleNamespace) -> None:
    """Reload is FPM's only restart of one pool; the others keep answering."""
    assert "gracefully" in control_pool(app_of(php), "restart")
    assert php.runner.ran("systemctl", "reload-or-restart", SERVICE)

    control_pool(app_of(php), "stop")
    with pytest.raises(DeploymentError, match="stopped"):
        control_pool(app_of(php), "restart")


def test_wasm_stop_and_start_control_the_pool(php: SimpleNamespace) -> None:
    """Not 'Static application - no service to stop'."""
    stopped = CliRunner().invoke(root_cli, ["stop", DOMAIN])

    assert stopped.exit_code == 0, stopped.output
    assert "no service" not in stopped.output
    assert php.disabled.is_file()

    started = CliRunner().invoke(root_cli, ["start", DOMAIN])

    assert started.exit_code == 0, started.output
    assert php.pool.is_file()


def test_the_console_endpoints_control_the_pool(php: SimpleNamespace) -> None:
    """POST /api/apps/{domain}/stop|start|restart no longer 404s."""
    from noust.web.api import apps as apps_api

    stopped = apps_api._service_action(DOMAIN, "stop", "stopped")
    assert stopped.success and php.disabled.is_file()

    started = apps_api._service_action(DOMAIN, "start", "started")
    assert started.success and php.pool.is_file()

    restarted = apps_api._service_action(DOMAIN, "restart", "restarted")
    assert "reloaded" in restarted.message.lower()


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_limits_rewrite_the_pool_behind_the_gate(php: SimpleNamespace) -> None:
    """Memory shared by the workers, tasks bounding them; FPM tested and reloaded."""
    change = lifecycle.set_resource_limits(DOMAIN, ResourceLimits(memory_max_mb=2000, tasks_max=20))

    pool = php.pool.read_text()
    assert "php_admin_value[memory_limit] = 100M" in pool
    assert "pm.max_children = 20" in pool
    assert change.units == (SERVICE,) and change.restarted
    assert php.probes >= 1
    assert app_of(php).memory_max_mb == 2000


def test_limits_the_pool_cannot_answer_under_are_undone(php: SimpleNamespace) -> None:
    """The previous pool is back and the limits are not recorded."""
    before = php.pool.read_text()
    php.answers = False

    with pytest.raises(DeploymentError, match="did not answer under the new limits"):
        lifecycle.set_resource_limits(DOMAIN, ResourceLimits(memory_max_mb=64))

    assert php.pool.read_text() == before
    assert app_of(php).memory_max_mb is None


def test_a_cpu_quota_is_refused_for_a_pool(php: SimpleNamespace) -> None:
    """FPM is shared: a CPU quota cannot be given to one pool."""
    with pytest.raises(DeploymentError, match="CPU quota"):
        lifecycle.set_resource_limits(DOMAIN, ResourceLimits(cpu_quota_percent=50))


# ---------------------------------------------------------------------------
# Diagnosis and the machine tally
# ---------------------------------------------------------------------------


def test_the_diagnosis_reads_the_pool(php: SimpleNamespace) -> None:
    """A php_fpm check first, with FPM's lines about this pool."""
    php.runner.script(
        ["journalctl", "-u", SERVICE],
        stdout="x WARNING: [pool www] child 1 exited\n"
        f"y WARNING: [pool wasm-{APP}] child 2 said into stderr: PHP Fatal error\n",
    )
    php.answers = False

    result = diagnose(DOMAIN, http_get=lambda url, headers: (200, None))

    check = result.checks[0]
    assert check.name == "php_fpm" and check.status == "fail"
    assert "PHP Fatal error" in check.evidence
    assert "[pool www]" not in check.evidence
    assert result.verdict == "down"
    assert result.probable_cause is not None and "health check" in result.probable_cause
    unit = next(c for c in result.checks if c.name == "unit")
    assert unit.status == "skip" and "PHP-FPM pool" in unit.summary


def test_the_diagnosis_says_a_stopped_pool_is_stopped(php: SimpleNamespace) -> None:
    """With how to start it."""
    php.pool.rename(php.disabled)

    result = diagnose(DOMAIN, http_get=lambda url, headers: (502, None))

    assert result.probable_cause is not None and "noust start" in result.probable_cause


def test_the_machine_tally_counts_php_by_its_pool(
    php: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running, stopped or failed; never static."""
    monkeypatch.setattr(php_module, "socket_accepts", lambda path, timeout=0.4: True)
    assert machine_module._count_apps([]).running == 1

    monkeypatch.setattr(php_module, "socket_accepts", lambda path, timeout=0.4: False)
    tally = machine_module._count_apps([])
    assert (tally.failed, tally.static) == (1, 0)

    php.pool.rename(php.disabled)
    assert machine_module._count_apps([]).stopped == 1
