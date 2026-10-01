# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving a WASM 1.x application's variables out of its unit and into its ``.env``.

The owner's central had an application whose DATABASE_URL and JWT secrets were
``Environment=`` lines in a 0644 unit, readable by any local user through
``systemctl show``, and whose builds ran without them because an update reads
the ``.env``. What is pinned:

- ``Environment=`` lines are read the way systemd reads them (quotes, escapes,
  several assignments per line, ``%%``);
- the rewritten unit keeps PORT and NODE_ENV inline, loses the rest, and loads
  the ``.env`` with ``EnvironmentFile=-`` before ``ExecStart=``;
- the migration merges into an existing ``.env`` (the unit's value wins) and
  rolls the unit and the ``.env`` back when the application does not answer;
- ``noust.inline_secrets`` is critical with a secret inline, a warning
  otherwise, and passes when no unit carries variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner, set_runner
from noust.deployers import unit_environment
from noust.deployers.helpers.env_manager import EnvManager
from noust.managers.service_manager import UNIT_MARKER

WASM_UNIT = f"""# {UNIT_MARKER}
[Unit]
Description=WASM: taller.example.com

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/var/www/apps/taller-example-com
# Environment
Environment="DATABASE_URL=postgresql://u:p%%40ss@localhost:5433/db?schema=public"
Environment="JWT_SECRET=a\\"b\\\\c"
Environment="NEXTAUTH_URL=https://taller.example.com" "FEATURE=on"
Environment="PORT=3001"
Environment="NODE_ENV=production"
ExecStart=/usr/bin/npm run start
Restart=always
"""


class TestReading:
    def test_environment_lines_are_read_the_way_systemd_reads_them(self) -> None:
        found = unit_environment.inline_variables(WASM_UNIT)

        assert found["DATABASE_URL"] == "postgresql://u:p%40ss@localhost:5433/db?schema=public"
        assert found["JWT_SECRET"] == 'a"b\\c'
        assert found["NEXTAUTH_URL"] == "https://taller.example.com"
        assert found["FEATURE"] == "on"
        assert found["PORT"] == "3001"

    def test_port_and_node_env_stay_inline(self) -> None:
        assert sorted(unit_environment.movable(WASM_UNIT)) == [
            "DATABASE_URL",
            "FEATURE",
            "JWT_SECRET",
            "NEXTAUTH_URL",
        ]
        assert unit_environment.secret_names(unit_environment.movable(WASM_UNIT)) == [
            "DATABASE_URL",
            "JWT_SECRET",
        ]


class TestRewriting:
    def test_the_unit_loads_the_env_file_and_keeps_only_what_is_nousts(self) -> None:
        env_file = Path("/var/www/apps/taller-example-com/.env")

        unit = unit_environment.rewritten(WASM_UNIT, env_file)

        assert "DATABASE_URL" not in unit and "JWT_SECRET" not in unit
        assert 'Environment="PORT=3001"' in unit
        assert 'Environment="NODE_ENV=production"' in unit
        lines = unit.splitlines()
        assert (
            lines.index(f"EnvironmentFile=-{env_file}")
            == lines.index("ExecStart=/usr/bin/npm run start") - 1
        )
        assert unit.count("EnvironmentFile=") == 1
        assert unit_environment.inline_variables(unit) == {"PORT": "3001", "NODE_ENV": "production"}

    def test_rewriting_twice_changes_nothing_more(self) -> None:
        env_file = Path("/srv/app/.env")
        once = unit_environment.rewritten(WASM_UNIT, env_file)

        assert unit_environment.rewritten(once, env_file) == once
        assert unit_environment.movable(once) == {}


# -- the migration --------------------------------------------------------------


class FakeServices:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.written: list[str] = []

    def rewrite_unit(self, name: str, content: str) -> Path:
        path = self.directory / f"{name}.service"
        path.write_text(content)
        self.written.append(content)
        return path


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    units = tmp_path / "units"
    units.mkdir()
    app_dir = tmp_path / "apps" / "taller-example-com"
    app_dir.mkdir(parents=True)
    (units / "taller-example-com.service").write_text(WASM_UNIT)
    monkeypatch.setattr(unit_environment.ServiceManager, "SYSTEMD_DIR", units)
    app = SimpleNamespace(
        id=1,
        domain="taller.example.com",
        app_type="nextjs",
        is_static=False,
        zero_downtime=False,
        layout="inplace",
        app_path=str(app_dir),
    )
    store = SimpleNamespace(get_app=lambda domain: app, get_service_by_app_id=lambda _id: None)
    monkeypatch.setattr(unit_environment, "app_root", lambda _app: app_dir)
    monkeypatch.setattr(unit_environment, "env_file_for", lambda _app: app_dir / ".env")
    runner = FakeRunner()
    set_runner(runner)
    gates: list[bool] = []

    def gate(_app: Any, _store: Any, _log: Any) -> SimpleNamespace:
        healthy = gates.pop(0) if gates else True
        return SimpleNamespace(restart_and_probe=lambda: (healthy, "" if healthy else "502 from /"))

    monkeypatch.setattr("noust.deployers.lifecycle.health_gate_for", gate)
    return SimpleNamespace(
        units=units,
        env_file=app_dir / ".env",
        store=store,
        services=FakeServices(units),
        runner=runner,
        gates=gates,
    )


def _migrate(machine: SimpleNamespace) -> Any:
    return unit_environment.migrate(
        "taller.example.com", store=machine.store, services=machine.services
    )


def test_the_variables_move_into_the_env_file_and_the_unit_loads_it(machine) -> None:
    machine.env_file.write_text("NEXTAUTH_URL=https://old.example.com\nONLY_IN_FILE=1\n")

    result = _migrate(machine)

    values = EnvManager().read_env_file(machine.env_file)
    assert values["DATABASE_URL"] == "postgresql://u:p%40ss@localhost:5433/db?schema=public"
    assert values["JWT_SECRET"] == 'a"b\\c'
    # What the process ran with wins; what only the file had stays.
    assert values["NEXTAUTH_URL"] == "https://taller.example.com"
    assert values["ONLY_IN_FILE"] == "1"
    assert "PORT" not in values
    assert oct(os.stat(machine.env_file).st_mode & 0o777) == "0o600"
    assert result.replaced == ("NEXTAUTH_URL",)
    assert result.moved == ("DATABASE_URL", "FEATURE", "JWT_SECRET", "NEXTAUTH_URL")
    unit = (machine.units / "taller-example-com.service").read_text()
    assert "DATABASE_URL" not in unit and f"EnvironmentFile=-{machine.env_file}" in unit
    assert ("chown", "www-data:www-data", str(machine.env_file)) in machine.runner.calls


def test_an_application_that_does_not_answer_gets_its_unit_and_env_back(machine) -> None:
    machine.env_file.write_text("ONLY_IN_FILE=1\n")
    machine.gates.extend([False, True])

    with pytest.raises(DeploymentError, match="runs on its previous unit again"):
        _migrate(machine)

    assert (machine.units / "taller-example-com.service").read_text() == WASM_UNIT
    assert machine.env_file.read_text() == "ONLY_IN_FILE=1\n"


def test_a_new_env_file_is_removed_again_when_it_fails(machine) -> None:
    machine.gates.extend([False, True])

    with pytest.raises(DeploymentError):
        _migrate(machine)

    assert not machine.env_file.exists()


def test_a_unit_with_nothing_to_move_is_left_alone(machine) -> None:
    (machine.units / "taller-example-com.service").write_text(
        f'# {UNIT_MARKER}\n[Service]\nEnvironment="PORT=3001"\nExecStart=/bin/true\n'
    )

    assert _migrate(machine) is None
    assert machine.services.written == []


# -- the check ------------------------------------------------------------------


def _check(tmp_path: Path, units: dict[str, str]):
    from noust.managers.server.security_checks import HardeningChecks
    from noust.managers.server.security_probe import SecurityProbe
    from tests.server_security_support import NOW, FakeHost, FakeSshd

    host = FakeHost(tmp_path / "root")
    for name, body in units.items():
        host.write(f"/etc/systemd/system/{name}.service", body)
    probe = SecurityProbe(runner=FakeSshd(host), host=host.paths, clock=lambda: NOW)
    return HardeningChecks(probe, console_port=8080)._inline_secrets_check()


def test_a_secret_inline_is_critical(tmp_path: Path) -> None:
    check = _check(tmp_path, {"taller-example-com": WASM_UNIT, "postgresql": "[Service]\n"})

    assert check.status == "fail" and check.severity == "critical"
    assert "DATABASE_URL" in check.reason and "JWT_SECRET" in check.reason
    assert check.evidence == (
        "taller-example-com.service: DATABASE_URL, FEATURE, JWT_SECRET, NEXTAUTH_URL",
    )
    assert check.fix is not None and "noust env migrate --all" in str(check.fix)


def test_only_harmless_variables_inline_is_a_warning(tmp_path: Path) -> None:
    body = f'# {UNIT_MARKER}\n[Service]\nEnvironment="FEATURE=on" "PORT=1"\nExecStart=/bin/true\n'

    check = _check(tmp_path, {"shop": body})

    assert check.status == "warn" and check.severity == "warning"


def test_units_that_load_their_env_file_pass(tmp_path: Path) -> None:
    body = unit_environment.rewritten(WASM_UNIT, Path("/srv/app/.env"))

    assert _check(tmp_path, {"taller-example-com": body}).status == "pass"
