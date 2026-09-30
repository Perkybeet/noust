"""
Which processes count as another package manager holding the lock.

Kept apart from test_server_updates.py, whose fixtures replace
running_processes with a stand-in.
"""

from __future__ import annotations

from pathlib import Path

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


class TestResidentDaemonsCountOnlyWithTheLock:
    """packagekitd stays up after every apt run; a fleet update was refused for it."""

    @staticmethod
    def _processes(*entries: tuple[int, str]) -> list[object]:
        from types import SimpleNamespace

        return [
            SimpleNamespace(info={"pid": pid, "name": name, "cmdline": [f"/usr/libexec/{name}"]})
            for pid, name in entries
        ]

    @staticmethod
    def _locks(tmp_path, holder: int, *files) -> Path:
        import os

        lines = []
        for index, path in enumerate(files, start=1):
            status = os.stat(path)
            device = f"{os.major(status.st_dev):02x}:{os.minor(status.st_dev):02x}"
            lines.append(f"{index}: POSIX  ADVISORY  WRITE {holder} {device}:{status.st_ino} 0 EOF")
        table = tmp_path / "locks"
        table.write_text("\n".join(lines) + "\n")
        return table

    def test_the_lock_table_names_who_holds_a_lock_file(self, tmp_path) -> None:
        from noust.managers.server.pkg import base

        lock = tmp_path / "lock-frontend"
        lock.write_text("")
        other = tmp_path / "unrelated"
        other.write_text("")

        table = self._locks(tmp_path, 4242, lock)
        assert base.lock_owner_pids([str(lock)], locks=table) == {4242}
        assert base.lock_owner_pids([str(other)], locks=table) == set()
        assert base.lock_owner_pids(["/nonexistent/lock"], locks=table) == set()

    def test_a_lock_the_table_cannot_attribute_rules_nothing_out(self, tmp_path) -> None:
        from noust.managers.server.pkg import base

        lock = tmp_path / "lock"
        lock.write_text("")

        # Open file description locks are listed with pid -1.
        assert base.lock_owner_pids([str(lock)], locks=self._locks(tmp_path, -1, lock)) is None
        assert base.lock_owner_pids([str(lock)], locks=tmp_path / "missing") is None

    def test_a_pid_file_names_the_running_transaction(self, tmp_path) -> None:
        import os

        from noust.managers.server.pkg import base

        pid_file = tmp_path / "zypp.pid"
        pid_file.write_text(f"{os.getpid()}\n")

        assert base.lock_owner_pids([], [str(pid_file)]) == {os.getpid()}

    def test_an_idle_packagekitd_is_not_busy_and_one_holding_the_lock_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import psutil

        from noust.managers.server.pkg import base

        monkeypatch.setattr(
            psutil, "process_iter", lambda attrs=None: self._processes((900, "packagekitd"))
        )
        names = ("apt", "dpkg", "packagekitd")

        assert base.running_processes(names, holding=set()) == []
        assert base.running_processes(names, holding={900}) == ["packagekitd"]
        # A lock nobody can attribute: counted, as before.
        assert base.running_processes(names, holding=None) == ["packagekitd"]

    def test_whatever_holds_the_lock_counts_and_apt_counts_by_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import psutil

        from noust.managers.server.pkg import base

        monkeypatch.setattr(
            psutil,
            "process_iter",
            lambda attrs=None: self._processes((901, "apt-get"), (902, "python3")),
        )

        assert base.running_processes(("apt", "dpkg"), holding={902}) == ["apt-get", "python3"]

    def test_the_backends_declare_their_locks(self) -> None:
        from noust.managers.server.pkg.apt import AptBackend
        from noust.managers.server.pkg.dnf import DnfBackend
        from noust.managers.server.pkg.zypper import ZypperBackend

        assert "/var/lib/dpkg/lock-frontend" in AptBackend.LOCK_FILES
        assert "/var/lib/rpm/.rpm.lock" in DnfBackend.LOCK_FILES
        assert "/run/zypp.pid" in ZypperBackend.PID_FILES
