# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_packages.py`` that other tests build on."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from noust.core.runner import CommandResult, FakeRunner
from noust.managers.server.host import PackageFamily, Platform

DOMAIN = "shop.example.com"
APT = Platform(family=PackageFamily.APT, program="apt-get")


class HostRunner(FakeRunner):
    """dpkg-query and rpm as a host answers, and a package manager that installs into it."""

    def __init__(self, installed: tuple[str, ...] = (), *, fail_install: bool = False) -> None:
        super().__init__()
        self.installed = set(installed)
        self.fail_install = fail_install

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        kwargs.pop("check", None)
        result = super().run(argv, **kwargs)
        args = [str(part) for part in argv]
        if args[0] == "dpkg-query":
            names = args[4:]
            known = "".join(f"{name} ii \n" for name in names if name in self.installed)
            return replace(result, stdout=known, exit_code=0 if known else 1)
        if args[0] == "rpm":
            names = args[4:]
            known = "".join(f"{name}\n" for name in names if name in self.installed)
            return replace(result, stdout=known, exit_code=0 if known else 1)
        if "install" in args and args[0] in ("apt-get", "dnf", "zypper"):
            if self.fail_install:
                return replace(
                    result,
                    stdout="Reading package lists...",
                    stderr="E: Unable to locate package nonexistent-lib",
                    exit_code=100,
                )
            wanted = [arg for arg in args[args.index("install") + 1 :] if not arg.startswith("-")]
            self.installed.update(wanted)
            return replace(result, stdout=f"Setting up {', '.join(wanted)}", exit_code=0)
        return result

    def installs(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "install" in call]

    def probes(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] in ("dpkg-query", "rpm")]


class Config:
    """A configuration that answers the policy key and nothing else."""

    def __init__(self, allow: Any = None) -> None:
        self.allow = allow if allow is not None else []

    def get(self, key: str, default: Any = None) -> Any:
        return self.allow if key == "security.app_packages" else default
