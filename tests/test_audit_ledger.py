# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The host action ledger: what Noust actually changed on the machine.

The filesystem seam and the command runner are the only two ways Noust
changes the machine, so listening there records all of it: each change,
linked to the request, command or job that caused it, and nothing that only
looked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import noust.core.runner as runner_module
from noust.core.audit import bind, get_log, ledger
from noust.core.audit.ledger import install_ledger, on_execution, uninstall_ledger
from noust.core.audit.log import AuditLog
from noust.core.audit.settings import AuditSettings
from noust.core.fs import DryRunFileSystem, RealFileSystem, set_fs


@pytest.fixture(autouse=True)
def installed() -> None:
    install_ledger()


def host_events(action: str = "host.fs") -> list[dict]:
    return list(reversed(get_log().read(action=action, limit=1000)))


def test_every_kind_of_file_change_is_recorded(tmp_path: Path) -> None:
    fs = RealFileSystem()
    directory = tmp_path / "site"
    fs.make_dir(directory)
    fs.write_text(directory / "a.conf", "x")
    fs.chmod(directory / "a.conf", 0o600)
    fs.rename(directory / "a.conf", directory / "b.conf")
    fs.symlink(directory / "b.conf", directory / "current")
    fs.remove(directory / "current")
    fs.remove_tree(directory)

    operations = [(entry["details"]["op"], entry["resource"]) for entry in host_events()]
    assert operations == [
        ("mkdir", str(directory)),
        ("write", str(directory / "a.conf")),
        ("chmod", str(directory / "a.conf")),
        ("rename", str(directory / "a.conf")),
        ("symlink", str(directory / "current")),
        ("remove", str(directory / "current")),
        ("remove_tree", str(directory)),
    ]
    rename = host_events()[3]
    assert rename["details"]["to"] == str(directory / "b.conf")


def test_changes_are_linked_to_what_caused_them(tmp_path: Path) -> None:
    with bind(correlation_id="req-42"):
        RealFileSystem().write_text(tmp_path / "unit.service", "[Unit]\n")
    assert host_events()[0]["corr"] == "req-42"


def test_a_rehearsal_changes_and_records_nothing(tmp_path: Path) -> None:
    set_fs(DryRunFileSystem())
    from noust.core.fs import get_fs

    get_fs().write_text(tmp_path / "x", "y")
    set_fs(None)
    assert host_events() == []


def test_the_trail_s_own_files_are_not_recorded(tmp_path: Path) -> None:
    log = get_log()
    log.append("apps.update")
    RealFileSystem().write_text(log.path.parent / "audit-ship.json", "{}")
    assert host_events() == []


def test_off_records_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = AuditLog(
        tmp_path / "audit" / "web-audit.log", settings=AuditSettings(host_activity="off")
    )
    monkeypatch.setattr("noust.core.audit.get_log", lambda: log)
    RealFileSystem().write_text(tmp_path / "x", "y")
    assert log.read(action="host.fs") == []


def test_one_request_records_at_most_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger, "MAX_PER_CORRELATION", 3)
    with bind(correlation_id="deploy-1"):
        for index in range(6):
            RealFileSystem().write_text(tmp_path / f"f{index}", "x")
    assert len(host_events()) == 3
    (truncated,) = host_events("host.truncated")
    assert truncated["details"] == {"limit": 3}
    assert truncated["corr"] == "deploy-1"


class TestExecutions:
    def test_a_process_that_changes_the_system_is_recorded(self) -> None:
        with bind(correlation_id="req-7"):
            on_execution(
                ("systemctl", "restart", "shop-example-com.service"),
                exit_code=0,
                duration=0.25,
            )
        (entry,) = host_events("host.exec")
        assert entry["resource"] == "systemctl"
        assert entry["details"]["argv"] == ["systemctl", "restart", "shop-example-com.service"]
        assert entry["details"]["exit_code"] == 0
        assert entry["details"]["duration_ms"] == 250
        assert entry["corr"] == "req-7"

    def test_read_only_probes_are_left_out(self) -> None:
        on_execution(("systemctl", "is-active", "nginx"), exit_code=0)
        assert host_events("host.exec") == []

    def test_all_includes_them(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = AuditLog(
            tmp_path / "audit" / "web-audit.log", settings=AuditSettings(host_activity="all")
        )
        monkeypatch.setattr("noust.core.audit.get_log", lambda: log)
        on_execution(("systemctl", "is-active", "nginx"), exit_code=0)
        (entry,) = log.read(action="host.exec")
        assert entry["details"]["read_only"] is True

    def test_the_runner_hook_is_used_when_the_runner_has_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INTEGRATION POINT with workstream B4: CommandRunner.add_execution_listener."""
        added: list = []
        uninstall_ledger()
        monkeypatch.setattr(runner_module, "add_execution_listener", added.append, raising=False)
        assert install_ledger() is True
        assert added == [on_execution]

    def test_an_execution_reported_as_one_object(self) -> None:
        from types import SimpleNamespace

        on_execution(SimpleNamespace(argv=("nginx", "-s", "reload"), exit_code=1, duration=0.1))
        (entry,) = host_events("host.exec")
        assert entry["details"]["argv"] == ["nginx", "-s", "reload"]
        assert entry["details"]["exit_code"] == 1
