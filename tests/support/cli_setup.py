# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_setup.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import setup as setup_cmd
from noust.core.runner import FakeRunner


@dataclass
class Invocation:
    """
    What one run of the CLI produced.

    Attributes:
        exit_code: The status the process would have exited with.
        output: Everything the user would have seen.
        exception: The exception that escaped, if any.
    """

    exit_code: int
    output: str
    exception: BaseException | None


#: Type of the ``wasm`` fixture, for the signatures below.
Wasm = Callable[..., Invocation]


@pytest.fixture
def wasm() -> Wasm:
    """
    Run the real command tree the way the console script does.

    Returns:
        A callable taking the arguments after the program name.
    """
    cli_runner = CliRunner()

    def invoke(*args: str) -> Invocation:
        result = cli_runner.invoke(root_cli, list(args))
        return Invocation(
            exit_code=result.exit_code,
            output=result.output,
            exception=result.exception,
        )

    return invoke


class _FakeSummary(dict):
    """A setup summary with nothing installed."""


def _empty_summary() -> dict[str, Any]:
    """
    Build the summary a bare machine produces.

    Returns:
        The same shape :class:`~noust.core.dependencies.DependencyChecker`
        returns.
    """
    return _FakeSummary(
        system_ready=False,
        webserver=None,
        nodejs={"installed": False, "version": None, "package_managers": {}},
        python={"installed": False, "version": None, "package_managers": {}},
        missing_required=[],
        missing_optional=[],
        recommendations=[],
    )


class _FakeChecker:
    """Stands in for the dependency checker, so no probing reaches the machine."""

    def __init__(self, verbose: bool = False, runner: Any = None):
        """
        Args:
            verbose: Ignored; matches the real signature.
            runner: Ignored; matches the real signature.
        """
        self.verbose = verbose

    def get_setup_summary(self) -> dict[str, Any]:
        """
        Report a machine with nothing installed.

        Returns:
            The summary.
        """
        return _empty_summary()

    def get_version(self, command: str, version_flag: str = "--version") -> str:
        """
        Report a plausible version for any command.

        Args:
            command: Program name.
            version_flag: Ignored.

        Returns:
            A version string.
        """
        return f"{command} 1.0"


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: FakeRunner) -> dict[str, Any]:
    """
    Point setup at a sandbox and make the machine look empty.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        tmp_path: Per-test temporary directory.
        runner: The fake process-wide runner, whose ``exists`` answers which
            package manager the machine has.

    Returns:
        A dict holding the sandboxed paths and the set of installed programs,
        which a test mutates to describe the machine it wants.
    """
    installed: set[str] = set()
    monkeypatch.setattr(setup_cmd, "command_exists", lambda name: name in installed)
    # The package manager is asked of the runner, like everywhere else that
    # needs to know which one this is.
    monkeypatch.setattr(runner, "exists", lambda name: name in installed)
    monkeypatch.setattr(setup_cmd.os, "geteuid", lambda: 0)
    monkeypatch.setattr(setup_cmd, "DEFAULT_APPS_DIR", tmp_path / "var/www/apps")
    monkeypatch.setattr(setup_cmd, "DEFAULT_LOG_DIR", tmp_path / "var/log/wasm")
    monkeypatch.setattr(setup_cmd, "DEFAULT_CONFIG_PATH", tmp_path / "etc/wasm/config.yaml")
    monkeypatch.setattr(setup_cmd, "MAN_PAGE_DIR", tmp_path / "usr/share/man/man1")

    import noust.core.config as core_config
    import noust.core.dependencies as dependencies
    import noust.core.utils as core_utils

    monkeypatch.setattr(dependencies, "DependencyChecker", _FakeChecker)
    monkeypatch.setattr(
        core_utils, "get_system_info", lambda: {"os": "Test Linux", "kernel": "6.0.0-test"}
    )

    saved: dict[str, Any] = {}
    # What Config.save() answers. A fixture that always says True cannot tell a
    # wizard that checks the result from one that ignores it, which is how the
    # unchecked save survived a review with green tests.
    outcome = {"save": True}

    class _FakeConfig:
        """Records what setup asked to persist, without touching /etc."""

        def set(self, key: str, value: Any) -> None:
            """
            Record a configuration value.

            Args:
                key: Dotted configuration key.
                value: Value to store.
            """
            saved[key] = value

        def save(self) -> bool:
            """
            Report what the test asked this save to do.

            Returns:
                True unless the test set ``prepared["save_succeeds"] = False``.
            """
            return outcome["save"]

    monkeypatch.setattr(core_config, "Config", _FakeConfig)

    class _Prepared(dict):
        """A dict whose ``save_succeeds`` key drives the fake Config."""

        def __setitem__(self, key: str, value: Any) -> None:
            """
            Store a value, forwarding the save outcome to the fake Config.

            Args:
                key: Setting name.
                value: New value.
            """
            if key == "save_succeeds":
                outcome["save"] = bool(value)
            super().__setitem__(key, value)

    return _Prepared(
        installed=installed,
        saved=saved,
        root=tmp_path,
        save_succeeds=True,
    )
