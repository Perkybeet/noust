"""
Which processes count as another package manager holding the lock.

Kept apart from test_server_updates.py, whose fixtures replace
running_processes with a stand-in.
"""

from __future__ import annotations

import pytest


class TestTheIdleUnattendedUpgradesHelper:
    """Ubuntu keeps unattended-upgrade-shutdown running; it holds no lock."""

    @staticmethod
    def _processes(*commands: list[str]) -> list[object]:
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                info={
                    "pid": 10_000 + index,
                    "name": command[1].rsplit("/", 1)[-1][:15],
                    "cmdline": command,
                }
            )
            for index, command in enumerate(commands)
        ]

    def test_the_resident_shutdown_helper_does_not_count_as_busy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import psutil

        from noust.managers.server.pkg import base

        helper = [
            "/usr/bin/python3",
            "/usr/share/unattended-upgrades/unattended-upgrade-shutdown",
            "--wait-for-signal",
        ]
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: self._processes(helper))

        assert base.running_processes(("unattended-upgr", "apt", "dpkg")) == []

    def test_a_real_unattended_upgrade_run_still_does(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import psutil

        from noust.managers.server.pkg import base

        running = ["/usr/bin/python3", "/usr/bin/unattended-upgrade"]
        helper = [
            "/usr/bin/python3",
            "/usr/share/unattended-upgrades/unattended-upgrade-shutdown",
            "--wait-for-signal",
        ]
        monkeypatch.setattr(
            psutil, "process_iter", lambda attrs=None: self._processes(running, helper)
        )

        assert base.running_processes(("unattended-upgr",)) == ["unattended-upgr"]
