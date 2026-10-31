# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the sandbox answers where the code asks the machine it runs on.

The Server area reads its files below a host tree and asks its tools through the runner, both
modelled in :mod:`.server_host`. A few reads go to the machine itself, in the process, and they
showed the developer's own: ``os.uname()`` (the kernel the page names, ``6.6.87.2-microsoft-
standard-WSL2``, and the hostname of ``GET /api/system``), and ``psutil`` for the processes, the
disks, the memory and the processors (the Processes tab listed this machine's browsers, run by its
user). They are answered here, from the same VPS: an Ubuntu 24.04 of two processors and two
gigabytes, whose kernel is the one its package lists, its reboot notice and its journal name.
"""

from __future__ import annotations

import os
import socket
import sys
import time
import types
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .machine import Unit

#: The kernel the modelled VPS runs: the one ``/var/run/reboot-required``, the journal's first
#: line and ``needrestart`` say, while ``6.8.0-47-generic`` is installed and waiting for a reboot.
KERNEL_RELEASE = "6.8.0-45-generic"
KERNEL_VERSION = "#45-Ubuntu SMP PREEMPT_DYNAMIC Fri Aug  2 17:49:21 UTC 2024"
ARCHITECTURE = "x86_64"

#: What ``/proc/meminfo`` of the host tree says: the modelled VPS's memory.
_MEMORY_TOTAL = 2030612 * 1024
_MEMORY_AVAILABLE = 918332 * 1024
_CPUS = 2

#: The modelled disks, as ``StorageManager.mounts`` answers them: mount point, device, type,
#: size and what is used, in bytes.
_GIB = 1024**3
_DISKS = (
    ("/", "/dev/vda1", "ext4", 78 * _GIB, int(55.6 * _GIB)),
    ("/srv", "/dev/vdb", "xfs", 40 * _GIB, int(35.1 * _GIB)),
    ("/boot", "/dev/vda16", "ext4", 881 * 1024**2, 118 * 1024**2),
)

#: Seconds the modelled VPS has been up, as the host tree's ``/proc/uptime`` says.
_UPTIME = 1051234.56


def modelled_uname(hostname: str) -> os.uname_result:
    """
    Args:
        hostname: The name the rest of the console reports.

    Returns:
        What ``os.uname()`` says on the modelled VPS.
    """
    return os.uname_result(("Linux", hostname, KERNEL_RELEASE, KERNEL_VERSION, ARCHITECTURE))


@dataclass(frozen=True)
class ModelledProcess:
    """
    One process of the modelled VPS.

    Attributes:
        pid: Its id.
        name: What ``ps`` calls it.
        user: The account it runs as.
        command: Its argv.
        cpu: CPU it uses, as a percentage.
        rss_mib: Resident memory, in MiB.
        unit: The systemd unit whose cgroup it is in; None for init.
        status: ``sleeping`` or ``running``.
    """

    pid: int
    name: str
    user: str
    command: tuple[str, ...]
    cpu: float
    rss_mib: float
    unit: str | None
    status: str = "sleeping"


#: What a server of this kind runs besides the applications of Noust.
_BASE = (
    ModelledProcess(1, "systemd", "root", ("/sbin/init",), 0.0, 13.2, None),
    ModelledProcess(
        301,
        "systemd-journal",
        "root",
        ("/usr/lib/systemd/systemd-journald",),
        0.1,
        28.4,
        "systemd-journald.service",
    ),
    ModelledProcess(
        412,
        "systemd-resolve",
        "systemd-resolve",
        ("/usr/lib/systemd/systemd-resolved",),
        0.0,
        11.9,
        "systemd-resolved.service",
    ),
    ModelledProcess(540, "cron", "root", ("/usr/sbin/cron", "-f", "-P"), 0.0, 2.8, "cron.service"),
    ModelledProcess(
        612,
        "sshd",
        "root",
        ("sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups",),
        0.0,
        6.3,
        "ssh.service",
    ),
    ModelledProcess(
        701,
        "nginx",
        "root",
        ("nginx: master process /usr/sbin/nginx -g daemon on; master_process on;",),
        0.0,
        9.4,
        "nginx.service",
    ),
    ModelledProcess(
        702, "nginx", "www-data", ("nginx: worker process",), 0.4, 14.8, "nginx.service"
    ),
    ModelledProcess(
        703, "nginx", "www-data", ("nginx: worker process",), 0.3, 14.6, "nginx.service"
    ),
    ModelledProcess(
        815,
        "postgres",
        "postgres",
        (
            "/usr/lib/postgresql/16/bin/postgres",
            "-D",
            "/var/lib/postgresql/16/main",
            "-c",
            "config_file=/etc/postgresql/16/main/postgresql.conf",
        ),
        0.6,
        62.0,
        "postgresql@16-main.service",
    ),
    ModelledProcess(
        933,
        "fail2ban-server",
        "root",
        ("/usr/bin/python3", "/usr/bin/fail2ban-server", "-xf", "start"),
        0.1,
        31.7,
        "fail2ban.service",
    ),
    ModelledProcess(
        1204,
        "python3",
        "root",
        ("/usr/bin/python3", "-m", "noust", "web", "serve"),
        1.2,
        96.5,
        "noust-web.service",
    ),
)


def modelled_processes(units: dict[str, Unit]) -> list[ModelledProcess]:
    """
    Args:
        units: The runner's unit model, whose running applications have each a process.

    Returns:
        What the modelled VPS runs: its services and one process for each application of Noust
        that is up, under the pid ``systemctl show`` answers for it.
    """
    found = list(_BASE)
    for name, unit in sorted(units.items()):
        if not unit.managed or unit.active != "active" or not unit.pid or name.startswith("noust-"):
            continue
        # Deterministic, and different for each: what an application of this size takes.
        weight = sum(name.encode()) % 100
        found.append(
            ModelledProcess(
                unit.pid,
                "node",
                f"noust-app-{name}"[:32],
                ("/usr/bin/node", f"/var/www/apps/{name}/current/.next/standalone/server.js"),
                round(0.4 + weight / 40, 1),
                round(120 + weight * 1.6, 1),
                f"{name}.service",
                "running" if weight % 7 == 0 else "sleeping",
            )
        )
    return found


class _Psutil(types.ModuleType):
    """``psutil`` as the modelled VPS answers it; whatever it does not model is the real module's."""

    def __init__(self, real: Any, processes: list[ModelledProcess], cpu_percent: float) -> None:
        super().__init__("psutil")
        self._real = real
        self._processes = processes
        self._cpu_percent = cpu_percent
        self._boot = time.time() - _UPTIME

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def process_iter(self, attrs: list[str] | None = None, ad_value: Any = None) -> Any:
        """Yield the modelled processes, each carrying the attributes asked for."""
        for process in self._processes:
            every = {
                "pid": process.pid,
                "name": process.name,
                "username": process.user,
                "cmdline": list(process.command),
                "cpu_percent": process.cpu,
                "memory_percent": process.rss_mib * 1024**2 / _MEMORY_TOTAL * 100,
                "memory_info": SimpleNamespace(rss=int(process.rss_mib * 1024**2)),
                "status": process.status,
            }
            wanted = every if attrs is None else {key: every[key] for key in attrs if key in every}
            yield SimpleNamespace(info=wanted, pid=process.pid)

    def cpu_count(self, logical: bool = True) -> int:
        """The processors of the modelled VPS."""
        return _CPUS

    def cpu_percent(self, interval: float | None = None, percpu: bool = False) -> Any:
        """The load of the modelled VPS, held where the top bar holds it."""
        return [self._cpu_percent] * _CPUS if percpu else self._cpu_percent

    def virtual_memory(self) -> Any:
        """The memory of the modelled VPS."""
        used = _MEMORY_TOTAL - _MEMORY_AVAILABLE
        return SimpleNamespace(
            total=_MEMORY_TOTAL,
            available=_MEMORY_AVAILABLE,
            used=used,
            free=210044 * 1024,
            percent=round(used / _MEMORY_TOTAL * 100, 1),
        )

    def swap_memory(self) -> Any:
        """The modelled VPS has no swap, which its checks say."""
        return SimpleNamespace(total=0, used=0, free=0, percent=0.0)

    def disk_partitions(self, all: bool = False) -> list[Any]:
        """The disks of the modelled VPS."""
        return [
            SimpleNamespace(device=device, mountpoint=mount, fstype=fstype, opts="rw")
            for mount, device, fstype, _total, _used in _DISKS
        ]

    def disk_usage(self, path: str) -> Any:
        """What the modelled disk that holds ``path`` holds."""
        holds = [
            row
            for row in _DISKS
            if str(path) == row[0] or str(path).startswith(row[0].rstrip("/") + "/")
        ]
        mount, _device, _fstype, total, used = max(
            holds, key=lambda row: len(row[0]), default=_DISKS[0]
        )
        return SimpleNamespace(
            total=total, used=used, free=total - used, percent=round(used / total * 100, 1)
        )

    def boot_time(self) -> float:
        """When the modelled VPS started."""
        return self._boot


def model_app_readings(units: dict[str, Unit], domains: Mapping[str, str]) -> None:
    """
    Say what each application that is up uses now, from the model its history is written with.

    The collector reads an application's cgroup, which no sandboxed unit has, so the overview and the
    list of applications said "No reading" for the memory and the CPU of every one. The reading is
    the history's own model (:func:`.app_tabs.tabs_reading`) at this moment, so the last point of a
    chart is the number beside it.

    Args:
        units: The runner's unit model.
        domains: Each unit's application.
    """
    from noust.core.store import get_store
    from noust.monitor.appsampler import AppSampler

    from .app_tabs import BASE_MEMORY_MB, tabs_reading

    def sample(
        self: AppSampler, plans: Any, now: float, elapsed: float | None
    ) -> list[tuple[str, float]]:
        store = get_store()
        moment = time.time()
        pairs: list[tuple[str, float]] = []
        for unit_name, domain in domains.items():
            unit = units.get(unit_name)
            app = store.get_app(domain) if unit is not None and unit.active == "active" else None
            base = BASE_MEMORY_MB.get(app.app_type) if app is not None else None
            if base is None:
                continue
            starts = sorted(
                int(datetime.fromisoformat(record.started_at).timestamp())
                for record in store.list_deployments(domain)
                if record.started_at
            )
            cpu, memory_mb = tabs_reading(moment, starts, base)
            pairs.append((f"app.{domain}.mem.bytes", memory_mb * 1024 * 1024))
            pairs.append((f"app.{domain}.cpu.percent", cpu))
        return pairs

    AppSampler.sample = sample  # type: ignore[method-assign]


def model_host_reads(
    host_root: Path,
    hostname: str,
    units: dict[str, Unit],
    cpu_percent: float = 12.0,
    domains: Mapping[str, str] | None = None,
) -> None:
    """
    Answer from the modelled VPS what the code reads from the machine it runs on.

    Args:
        host_root: The modelled host's root (:class:`.server_host.ServerHost`'s), where the cgroup
            of each process is written for ``group=unit``.
        domains: Each unit's application, whose memory and CPU are read from the model too.
        hostname: The name the rest of the console reports.
        units: The runner's unit model.
        cpu_percent: The processor load the top bar holds.
        domains: Each unit's application, whose memory and CPU are read from the model too.
    """
    # Global, as ``os.getloadavg`` is patched: nothing else in this process needs the real values.
    os.uname = lambda: modelled_uname(hostname)  # type: ignore[assignment]
    # The audit export, the ENS report, the migration check and the metrics' labels name the host with a bare
    # `socket.gethostname()`, which `use_fixed_hostname` only stands in for in the modules it knows of.
    socket.gethostname = lambda: hostname

    import psutil as real_psutil

    processes = modelled_processes(units)
    proxy = _Psutil(real_psutil, processes, cpu_percent)
    sys.modules["psutil"] = proxy
    # What imported it before this ran holds the real module: the metrics the monitor reads.
    import noust.monitor.metrics as metrics_module

    if hasattr(metrics_module, "psutil"):
        metrics_module.psutil = proxy

    for process in processes:
        directory = host_root / "proc" / str(process.pid)
        directory.mkdir(parents=True, exist_ok=True)
        slice_name = "system.slice/" + process.unit if process.unit else "init.scope"
        (directory / "cgroup").write_text(f"0::/{slice_name}\n", encoding="utf-8")
    # Where ``list_processes`` reads the cgroups from: the tree above, not this machine's ``/proc``.
    from noust.managers.server import processes as processes_module

    processes_module.list_processes.__kwdefaults__["proc_root"] = host_root / "proc"  # type: ignore[index]
    if domains is not None:
        model_app_readings(units, domains)
    if domains is not None:
        model_app_readings(units, domains)
