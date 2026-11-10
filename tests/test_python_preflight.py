# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the pre-flight of a Python deployment asks of the server (item 156).

It asked for ``pip3``, and nothing in a deployment runs it: the dependencies are installed by the
pip the virtual environment was seeded with (``venv/bin/python -m pip``), which ``python3 -m venv``
brings. On the rpm family the package does not depend on ``python3-pip``, so a server without the
system's ``pip3`` refused a deployment that would have worked. The pre-flight now asks for what the
deployment uses: ``python3``, and that it can make an environment with pip in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.deployers.helpers import preflight
from noust.deployers.python import PythonDeployer

DOMAIN = "shop.example.com"
PROBE = ("python3", "-c", "import ensurepip, venv")


def deployer_for(runner: FakeRunner, project: Path) -> PythonDeployer:
    """
    Args:
        runner: What runs its commands.
        project: The project's directory.

    Returns:
        A configured Python deployer.
    """
    (project / "requirements.txt").write_text("flask\n")
    instance = PythonDeployer(runner=runner)
    instance.configure(DOMAIN, "src", app_path=project)
    return instance


class TestWhatItAsksFor:
    def test_the_systems_pip_is_not_a_requirement(self) -> None:
        assert PythonDeployer.SYSTEM_DEPS == ["python3"]

    def test_a_server_with_python_and_no_pip3_is_not_missing_anything(self) -> None:
        runner = FakeRunner().only_knows("python3")

        assert preflight.missing_programs(runner, PythonDeployer.SYSTEM_DEPS) == []

    def test_a_server_without_python_still_is(self) -> None:
        runner = FakeRunner().only_knows("pip3")

        assert preflight.missing_programs(runner, PythonDeployer.SYSTEM_DEPS) == ["python3"]


class TestTheEnvironmentItMakes:
    def test_python_that_can_seed_an_environment_is_nothing_to_report(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("python3", "apt-get")
        deployer = deployer_for(runner, tmp_path)

        assert deployer._toolchain_issues() == []
        assert PROBE in runner.calls

    def test_python_that_cannot_says_what_to_install_on_this_server(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("python3", "apt-get")
        runner.script(
            PROBE,
            stderr="Traceback (most recent call last):\nModuleNotFoundError: No module named 'ensurepip'",
            exit_code=1,
        )
        deployer = deployer_for(runner, tmp_path)

        (issue,) = deployer._toolchain_issues()

        assert issue == (
            "python3 cannot make a virtual environment with pip in it "
            "(ModuleNotFoundError: No module named 'ensurepip'). "
            "Install it with: apt install python3-venv python3-pip"
        )

    def test_on_the_rpm_family_it_is_the_package_that_has_the_wheels(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("python3", "dnf")
        runner.script(PROBE, stderr="ModuleNotFoundError: No module named 'ensurepip'", exit_code=1)
        deployer = deployer_for(runner, tmp_path)

        (issue,) = deployer._toolchain_issues()

        assert issue.endswith("Install it with: dnf install python3-pip")

    def test_the_preflight_reports_it_with_every_other_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner().only_knows("python3", "apt-get")
        runner.script(PROBE, stderr="ModuleNotFoundError: No module named 'venv'", exit_code=1)
        deployer = deployer_for(runner, tmp_path)
        self.nothing_else_wrong(monkeypatch)

        with pytest.raises(DeploymentError, match="Pre-flight") as refused:
            deployer.pre_flight_check()

        assert "python3 cannot make a virtual environment" in str(refused.value.details)
        assert "pip3" not in str(refused.value.details)

    def test_with_no_python_the_missing_program_is_the_issue_and_nothing_is_probed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner().only_knows("apt-get")
        deployer = deployer_for(runner, tmp_path)
        self.nothing_else_wrong(monkeypatch)

        with pytest.raises(DeploymentError, match="Pre-flight") as refused:
            deployer.pre_flight_check()

        assert "Missing system dependencies: python3" in str(refused.value.details)
        assert PROBE not in runner.calls

    def test_a_server_that_can_deploys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No pip3 on it: the rpm family without python3-pip, whose venv still seeds one.
        runner = FakeRunner().only_knows("python3", "dnf")
        deployer = deployer_for(runner, tmp_path)
        self.nothing_else_wrong(monkeypatch)

        assert deployer.pre_flight_check() is True

    @staticmethod
    def nothing_else_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(preflight, "repository_unreachable", lambda runner, source: [])
        monkeypatch.setattr(preflight, "insufficient_disk_space", lambda directory: [])
        monkeypatch.setattr(preflight, "over_disk_limit", lambda app, runner: [])
        monkeypatch.setattr(preflight, "port_taken", lambda *a, **k: [])
        monkeypatch.setattr(preflight, "webserver_down", lambda manager, name: [])
