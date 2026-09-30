"""
Helpers shared by the tests of the server-management workstream.

The outputs the managers parse are files under ``tests/fixtures/server``: what
apt, dnf, zypper, docker, timedatectl and the rest really print (captured on an
Ubuntu 24.04 machine where the tool exists, written from the tool's documented
format where it does not), never strings typed into the assertions.
"""

from __future__ import annotations

import os
import types
from dataclasses import replace
from pathlib import Path

import pytest

from noust.core.runner import CommandResult, FakeRunner
from noust.managers.server.host import HostPaths, OsRelease, PackageFamily, Platform

FIXTURES = Path(__file__).parent / "fixtures" / "server"


def fixture(*parts: str) -> str:
    """
    Read a captured tool output.

    Args:
        *parts: Path below ``tests/fixtures/server``.

    Returns:
        The file's text.
    """
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


def platform_for(family: str, **overrides) -> Platform:
    """
    Build the platform of a machine of one family.

    Args:
        family: ``apt``, ``dnf``, ``zypper`` or ``none``.
        **overrides: Fields of :class:`Platform` to change.

    Returns:
        The platform.
    """
    defaults = {
        "apt": {
            "family": PackageFamily.APT,
            "program": "apt-get",
            "os": OsRelease(
                id="ubuntu", version_id="24.04", codename="noble", pretty_name="Ubuntu 24.04.5 LTS"
            ),
        },
        "dnf": {
            "family": PackageFamily.DNF,
            "program": "dnf",
            "os": OsRelease(
                id="rocky", version_id="9.5", pretty_name="Rocky Linux 9.5 (Blue Onyx)"
            ),
        },
        "zypper": {
            "family": PackageFamily.ZYPPER,
            "program": "zypper",
            "os": OsRelease(
                id="opensuse-leap", version_id="15.6", pretty_name="openSUSE Leap 15.6"
            ),
        },
        "none": {
            "family": PackageFamily.NONE,
            "os": OsRelease(id="alpine", pretty_name="Alpine Linux"),
        },
    }[family]
    return Platform(**{**defaults, **overrides})


def make_host(root: Path) -> HostPaths:
    """
    Build a fake system root with the directories the managers read from.

    Args:
        root: An empty directory, usually ``tmp_path``.

    Returns:
        The paths below it.
    """
    for directory in (
        "etc/apt/apt.conf.d",
        "etc/dnf",
        "var/lib/apt/periodic",
        "var/lib/apt/lists",
        "var/run",
        "run/systemd/system",
        "run/systemd/shutdown",
        "proc/sys/kernel/random",
        "proc/sys/vm",
        "boot",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    return HostPaths(root)


class SequencedRunner(FakeRunner):
    """
    A FakeRunner that can answer the same command differently each time.

    Following a unit asks the journal and systemd the same questions over and
    over, and the point of the tests is what changes between the answers.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sequences: list[tuple[tuple[str, ...], list[CommandResult]]] = []

    def sequence(self, prefix, results: list[dict]) -> SequencedRunner:
        """
        Answer a command with each result in turn; the last one repeats.

        Args:
            prefix: Leading arguments that identify the command.
            results: ``script``-style keyword dicts (stdout, stderr, exit_code).

        Returns:
            This runner.
        """
        argv = tuple(prefix)
        built = [CommandResult(argv=argv, **{"exit_code": 0, **result}) for result in results]
        self._sequences.append((argv, built))
        return self

    def _lookup(self, argv, user=None, env=None):
        base = super()._lookup(argv, user, env)
        recorded = tuple(str(a) for a in argv)
        for prefix, results in reversed(self._sequences):
            if recorded[: len(prefix)] == prefix:
                result = results.pop(0) if len(results) > 1 else results[0]
                return replace(result, argv=recorded)
        return base


@pytest.fixture
def no_package_manager_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make the search for a running package manager find none.

    The real one looks at the developer's own process table, where an apt or a
    cloud-init may well be running while the tests do.

    Args:
        monkeypatch: Patching helper, scoped to the test.
    """
    monkeypatch.setattr("noust.managers.server.pkg.base.running_processes", lambda names, **_: [])


def make_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store) -> types.SimpleNamespace:
    """
    Build a whole server made of fixtures: apt, systemd, timedatectl, two disks.

    Args:
        tmp_path: Per-test temporary directory; the fake system root is below it.
        monkeypatch: Patching helper, scoped to the test.
        store: A store for the power schedule.

    Returns:
        A namespace with ``ctx`` (the :class:`ServerContext`, with no background
        work), ``runner`` (the FakeRunner scripted with what the tools print) and
        ``host`` (the fake system root).
    """
    from noust.core.fs import RecordingFileSystem
    from noust.managers.server.context import ServerContext
    from noust.managers.server.facts import FactCache
    from noust.managers.server.updates import RecordStore

    host = make_host(tmp_path / "root")
    host.os_release.write_text(
        'PRETTY_NAME="Ubuntu 24.04.5 LTS"\nVERSION_ID="24.04"\nID=ubuntu\nVERSION_CODENAME=noble\n'
    )
    host.proc_uptime.write_text("90000.5 1.0\n")
    host.boot_id.write_text("d875e599-869f-4722-96c9-6f9b3e1b5356\n")
    (host.root / "proc").mkdir(exist_ok=True)
    (host.root / "proc/meminfo").write_text("MemTotal: 1048576 kB\nMemAvailable: 524288 kB\n")

    runner = FakeRunner()
    runner.script(["apt-get", "-s"], stdout=fixture("apt", "upgrade-simulation.txt"))
    runner.script(["needrestart", "-b"], stdout=fixture("apt", "needrestart-batch.txt"))
    runner.script(["dpkg-query"], stdout="ii ")
    runner.script(["apt-config", "dump"], stdout=fixture("apt", "config-dump-unattended.txt"))
    runner.script(["systemctl", "is-enabled"], stdout="enabled\n")
    runner.script(["timedatectl", "show"], stdout=fixture("system", "timedatectl-show.txt"))
    runner.script(["swapon"], stdout="")
    runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))
    runner.script(["systemctl", "is-system-running"], stdout="degraded\n", exit_code=1)
    runner.script(
        ["systemctl", "--failed"],
        stdout="nginx.service loaded failed failed A high performance web server\n"
        "backup.service loaded failed failed Backup\n",
    )
    runner.script(["findmnt", "--verify"], stdout="Success, no errors or warnings detected\n")

    def disk_partitions(all=False):
        return [
            types.SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4", opts="rw"),
            types.SimpleNamespace(device="/dev/sdb1", mountpoint="/data", fstype="ext4", opts="rw"),
        ]

    usages = {
        "/": types.SimpleNamespace(
            total=100 * 1024**3, used=71 * 1024**3, free=29 * 1024**3, percent=71.0
        ),
        "/data": types.SimpleNamespace(
            total=50 * 1024**3, used=46 * 1024**3, free=4 * 1024**3, percent=92.0
        ),
    }
    monkeypatch.setattr(
        "noust.managers.server.storage._psutil",
        lambda: types.SimpleNamespace(
            disk_partitions=disk_partitions, disk_usage=lambda p: usages[p]
        ),
    )
    # Inodes for the disks, and room for shutil.disk_usage, which reads statvfs too.
    roomy = 100 * 1024**3 // 4096
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda path: types.SimpleNamespace(
            f_files=1000,
            f_ffree=900,
            f_frsize=4096,
            f_blocks=roomy,
            f_bfree=roomy // 2,
            f_bavail=roomy // 2,
        ),
    )
    monkeypatch.setattr("noust.managers.server.pkg.base.running_processes", lambda names, **_: [])

    ctx = ServerContext(
        runner=runner,
        fs=RecordingFileSystem(),
        host=host,
        platform=platform_for("apt"),
        cache=FactCache(background=False),
        records=RecordStore(tmp_path / "os-updates"),
        store=store,
    )
    return types.SimpleNamespace(ctx=ctx, runner=runner, host=host)


class RecordingAudit:
    """
    Stands in for :func:`noust.core.audit.record`: keeps what it was told.

    Patch it with ``monkeypatch.setattr("noust.core.audit.record", audit.record)``;
    the endpoints and jobs call it through the module, so the patch is seen.
    """

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(
        self,
        event: str,
        *,
        actor=None,
        target: str | None = None,
        outcome: str = "ok",
        details: dict | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Keep one event."""
        self.records.append(
            {"event": event, "target": target, "outcome": outcome, "details": details or {}}
        )

    def events(self) -> list[str]:
        """
        Returns:
            The names of the events recorded, in order.
        """
        return [record["event"] for record in self.records]
