# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The machine a scenario is seeded on: what systemd, the journal, certbot and the disk answer (item 171).

The battery serves the real console over a store of its own and answers what the machine would, the way
``scripts/console_server.py`` does for the end-to-end suites: a :class:`~noust.core.runner.FakeRunner`
that replies by argv, so that the diagnosis the assistant reads is made by the real ``diagnose`` over a
machine that is healthy except for what the scenario breaks. Nothing here runs a process.

Defaults are a healthy server: every application's unit runs and listens on its port, nginx and the
application answer, every certificate has two months left and the disk is at 40 %. A scenario states
only what is wrong.
"""

from __future__ import annotations

import shutil
from collections import namedtuple
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner, set_runner
from noust.core.utils import domain_to_app_name
from noust.managers import diagnose as diagnose_module
from noust.managers.cert_manager import CertManager

__all__ = ["Machine", "UnitState"]

_DiskUsage = namedtuple("_DiskUsage", "total used free")

_JOURNAL_LINES = 50


@dataclass
class UnitState:
    """
    What systemd says of one application's unit.

    Attributes:
        active: ``ActiveState``.
        sub: ``SubState``.
        result: ``Result``.
        exit_status: ``ExecMainStatus``.
        restarts: ``NRestarts``.
    """

    active: str = "active"
    sub: str = "running"
    result: str = "success"
    exit_status: str = "0"
    restarts: str = "0"

    @classmethod
    def from_seed(cls, seed: Mapping[str, Any]) -> UnitState:
        """
        Args:
            seed: The ``units.<domain>`` mapping of a scenario.

        Returns:
            The state, with the defaults of a unit that runs for what the seed leaves out.
        """
        failed = seed.get("active") == "failed"
        return cls(
            active=str(seed.get("active", "active")),
            sub=str(seed.get("sub", "failed" if failed else "running")),
            result=str(seed.get("result", "exit-code" if failed else "success")),
            exit_status=str(seed.get("exit_status", "1" if failed else "0")),
            restarts=str(seed.get("restarts", "0")),
        )


@dataclass
class Machine:
    """
    The seeded machine.

    Attributes:
        domains: The applications on it.
        journals: Each application's journal (already with what the harness planted).
        units: Each application's unit.
        listening: What ``ss`` reports: ``(port, process, pid)``.
        http: The status the application answers directly and through nginx, with the error when there is
            none.
        certificates: Days left of each certificate, by domain. A domain with none has no certificate.
        disk_percent: How full the disk is.
        oom: The kernel's lines about a process it killed for memory.
        runner: The fake runner, once installed.
    """

    domains: list[str]
    journals: dict[str, str] = field(default_factory=dict)
    units: dict[str, UnitState] = field(default_factory=dict)
    listening: list[tuple[int, str, int]] = field(default_factory=list)
    http: dict[str, tuple[int | None, str | None]] = field(
        default_factory=lambda: {"direct": (200, None), "nginx": (200, None)}
    )
    certificates: dict[str, int] = field(default_factory=dict)
    disk_percent: float = 40.0
    oom: list[str] = field(default_factory=list)
    runner: FakeRunner | None = None

    # -- building it from a scenario -------------------------------------------------------------

    @classmethod
    def seeded(
        cls, domains: list[str], seed: Mapping[str, Any], journals: Mapping[str, str]
    ) -> Machine:
        """
        Args:
            domains: Every application's domain.
            seed: The ``seed`` of a scenario, expanded.
            journals: Each application's journal text.

        Returns:
            The machine: healthy but for what the seed breaks.
        """
        machine = cls(domains=list(domains))
        machine.journals = {d: journals.get(d, "") for d in domains}
        machine.units = {
            d: UnitState.from_seed((seed.get("units") or {}).get(d, {})) for d in domains
        }
        if "listening" in seed:
            machine.listening = [
                (int(item["port"]), str(item.get("process", "node")), int(item.get("pid", 100 + n)))
                for n, item in enumerate(seed["listening"])
            ]
        else:
            machine.listening = [(3000, "node", 100)]
        for key, value in (seed.get("http") or {}).items():
            status, error = value
            machine.http[key] = (status, error)
        machine.certificates = dict.fromkeys(domains, 60)
        certificate = seed.get("certificate")
        if certificate:
            machine.certificates[str(certificate["domain"])] = int(certificate["days_left"])
        machine.disk_percent = float((seed.get("disk") or {}).get("percent_used", 40.0))
        machine.oom = list(seed.get("oom") or [])
        return machine

    # -- what the machine answers ----------------------------------------------------------------

    def journal_text(self, name: str) -> str:
        """
        Args:
            name: An application's name (its domain with dashes) or its domain.

        Returns:
            Its journal.
        """
        for domain, text in self.journals.items():
            if name in (domain, domain_to_app_name(domain)):
                return text
        return ""

    def unit_of(self, name: str) -> UnitState:
        """
        Args:
            name: An application's name or its domain.

        Returns:
            Its unit's state.
        """
        for domain, unit in self.units.items():
            if name in (domain, domain_to_app_name(domain)):
                return unit
        return UnitState()

    def _certbot(self) -> str:
        lines = ["Found the following certs:"]
        now = datetime.now(timezone.utc)
        for domain, days in self.certificates.items():
            expiry = (now + timedelta(days=days)).strftime("%Y-%m-%d")
            verdict = f"VALID: {days} days" if days > 0 else "INVALID: EXPIRED"
            lines += [
                f"  Certificate Name: {domain}",
                f"    Domains: {domain}",
                f"    Expiry Date: {expiry} 00:00:00+00:00 ({verdict})",
                f"    Certificate Path: /etc/letsencrypt/live/{domain}/fullchain.pem",
                f"    Private Key Path: /etc/letsencrypt/live/{domain}/privkey.pem",
            ]
        return "\n".join(lines) + "\n"

    def _ss(self) -> str:
        return "".join(
            f"LISTEN 0      511          127.0.0.1:{port}       0.0.0.0:*    "
            f'users:(("{process}",pid={pid},fd=18))\n'
            for port, process, pid in self.listening
        )

    def install(self, monkeypatch: pytest.MonkeyPatch, live_dir: Path) -> FakeRunner:
        """
        Make this the machine the process asks: the runner, the disk and the probes of HTTP.

        Args:
            monkeypatch: Patching helper; what it patches is put back when the test ends.
            live_dir: Where certbot keeps the files of each certificate: a folder of the test's own, with
                a chain for every certificate the machine has.

        Returns:
            The fake runner, which records every order the code gives.
        """
        runner = FakeRunner()
        main_pid = self.listening[0][2] if self.listening else 0
        for domain in self.domains:
            unit = f"{domain_to_app_name(domain)}.service"
            state = self.units[domain]
            runner.script(
                ["systemctl", "show", "-p", "FragmentPath", unit],
                stdout=f"FragmentPath=/etc/systemd/system/{unit}\n",
            )
            runner.script(
                [
                    "systemctl",
                    "show",
                    "-p",
                    "ActiveState,SubState,Result,ExecMainStatus,NRestarts",
                    unit,
                ],
                stdout=(
                    f"ActiveState={state.active}\nSubState={state.sub}\nResult={state.result}\n"
                    f"ExecMainStatus={state.exit_status}\nNRestarts={state.restarts}\n"
                ),
            )
            runner.script(
                ["systemctl", "show", "-p", "MainPID", unit], stdout=f"MainPID={main_pid}\n"
            )
            tail = "\n".join(self.journals.get(domain, "").splitlines()[-_JOURNAL_LINES:])
            runner.script(
                [
                    "journalctl",
                    "-u",
                    unit,
                    "-n",
                    str(_JOURNAL_LINES),
                    "--no-pager",
                    "-o",
                    "short-iso",
                ],
                stdout=tail + ("\n" if tail else ""),
            )
        runner.script(["ss", "-ltnpH"], stdout=self._ss())
        runner.script(
            ["journalctl", "-k", "--since", "-7d", "--grep", "oom", "-o", "short-iso"],
            stdout="".join(f"{line}\n" for line in self.oom),
        )
        runner.script(["certbot", "certificates"], stdout=self._certbot())
        for domain in self.certificates:
            (live_dir / domain).mkdir(parents=True, exist_ok=True)
            (live_dir / domain / "fullchain.pem").write_text(f"chain of {domain}\n")
        monkeypatch.setattr(CertManager, "LIVE_DIR", live_dir)
        set_runner(runner)
        self.runner = runner

        def http_get(_url: str, headers: Mapping[str, str]) -> tuple[int | None, str | None]:
            return self.http["nginx" if headers.get("Host") else "direct"]

        def disk_usage(_path: Any = "/") -> _DiskUsage:
            total = 1_000_000_000_000
            free = int(total * (1 - self.disk_percent / 100))
            return _DiskUsage(total, total - free, free)

        monkeypatch.setattr(diagnose_module, "_default_http_get", http_get)
        monkeypatch.setattr(shutil, "disk_usage", disk_usage)
        return runner

    def uninstall(self) -> None:
        """Give the process its own runner back."""
        set_runner(None)
