# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console's sandbox answers where the code asks the machine it runs on.

The Server area of the sandbox is a modelled VPS, and a few reads went to the developer's own
machine instead: the kernel the page names (``6.6.87.2-microsoft-standard-WSL2``), the hostname of
``GET /api/system``, and the processes, disks, memory and processors from ``psutil`` (the
Processes tab listed this machine's browsers, run by its user). They answer from the VPS now, and
what they say agrees with the VPS's own package lists, reboot notice and journal.
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import noust.monitor.metrics as metrics_module
from noust.managers.server import processes as processes_module
from scripts.console_server import host_reads
from scripts.console_server.host_reads import KERNEL_RELEASE, model_host_reads, modelled_processes
from scripts.console_server.machine import Unit
from scripts.console_server.server_host import ServerHost

HOSTNAME = "fra-1"
#: The kernel of the machine that records the video, which a page must never name.
HOST_KERNEL = "6.6.87.2-microsoft-standard-WSL2"


@pytest.fixture
def modelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Unit]]:
    """The process answering for a VPS with two applications up and one that failed."""
    units = {
        "shop-example-com": Unit(active="active", pid=2101),
        "blog-example-com": Unit(active="active", pid=2102),
        "broken-example-com": Unit(active="failed", pid=0),
        "nginx": Unit(active="active", pid=701, managed=False),
        "noust-web": Unit(active="active", pid=1204),
    }
    # Everything the model replaces is put back when the test ends.
    monkeypatch.setattr(os, "uname", os.uname)
    monkeypatch.setattr(socket, "gethostname", socket.gethostname)
    monkeypatch.setitem(sys.modules, "psutil", sys.modules["psutil"])
    if hasattr(metrics_module, "psutil"):
        monkeypatch.setattr(metrics_module, "psutil", metrics_module.psutil)
    kwdefaults = dict(processes_module.list_processes.__kwdefaults__ or {})
    monkeypatch.setattr(processes_module.list_processes, "__kwdefaults__", kwdefaults)
    host = ServerHost(tmp_path / "host", HOSTNAME, units)
    host.build()
    model_host_reads(host.root, HOSTNAME, units, cpu_percent=23.0)
    yield units


def test_the_kernel_is_the_one_the_vps_says_it_runs(
    modelled: dict[str, Unit], tmp_path: Path
) -> None:
    uname = os.uname()

    assert uname.release == KERNEL_RELEASE != HOST_KERNEL
    assert uname.machine == "x86_64"
    assert uname.nodename == HOSTNAME
    # The name every report of the host carries, whichever way it asks for it.
    assert socket.gethostname() == HOSTNAME
    # The same kernel as its reboot notice, its boot directory and its journal.
    host = ServerHost(tmp_path / "again", HOSTNAME, {})
    host.build()
    assert KERNEL_RELEASE in (host.root / "var/run/reboot-required.pkgs").read_text(
        encoding="utf-8"
    )
    assert (host.root / "boot" / f"vmlinuz-{KERNEL_RELEASE}").exists()
    assert KERNEL_RELEASE in host._journal_json(())


def test_what_the_server_area_asks_for_the_running_kernel_is_answered_by_the_model(
    modelled: dict[str, Unit],
) -> None:
    from noust.managers.server.host import running_kernel

    # The identity tab, the summary and the reboot check read `os.uname()` and this.
    assert running_kernel() == KERNEL_RELEASE


def test_the_processes_are_the_vps_s_and_none_of_this_machine_s(modelled: dict[str, Unit]) -> None:
    rows, total = processes_module.list_processes(limit=500, show_commands=True)

    assert total == len(rows)
    names = {(row.name, row.user) for row in rows}
    assert ("nginx", "www-data") in names
    assert ("postgres", "postgres") in names
    # An application that is up has its process, under the pid its unit has; one that failed has none.
    by_pid = {row.pid: row for row in rows}
    assert by_pid[2101].user == "noust-app-shop-example-com"
    assert (
        by_pid[2101].command
        == "/usr/bin/node /var/www/apps/shop-example-com/current/.next/standalone/server.js"
    )
    assert 0 not in by_pid and all("broken-example-com" not in row.user for row in rows)
    # Nothing of the developer's machine: its browsers, its shell, its account.
    assert not {"chrome", "chromium", "chrome-headless-shell", "claude", "node-gyp"} & {
        row.name for row in rows if row.name != "node"
    }
    assert {row.user for row in rows} <= {
        "root",
        "www-data",
        "postgres",
        "systemd-resolve",
        "noust-app-shop-example-com",
        "noust-app-blog-example-com",
    }


def test_the_processes_are_grouped_by_the_unit_of_their_cgroup(modelled: dict[str, Unit]) -> None:
    rows, _ = processes_module.list_processes(limit=500)

    grouped = {unit.unit: unit.processes for unit in processes_module.group_by_unit(rows)}

    assert grouped["nginx.service"] == 3
    assert grouped["shop-example-com.service"] == 1


def test_the_modelled_processes_do_not_depend_on_the_order_of_the_units() -> None:
    first = {"a-b": Unit(active="active", pid=11), "c-d": Unit(active="active", pid=12)}
    second = dict(reversed(list(first.items())))

    assert modelled_processes(first) == modelled_processes(second)


def test_the_memory_the_processors_and_the_disks_are_the_vps_s(modelled: dict[str, Unit]) -> None:
    import psutil

    assert psutil.cpu_count() == 2
    assert psutil.cpu_percent(interval=0.1) == 23.0
    assert psutil.virtual_memory().total == 2030612 * 1024
    assert psutil.swap_memory().total == 0
    mounts = {partition.mountpoint for partition in psutil.disk_partitions()}
    assert mounts == {"/", "/srv", "/boot"}
    assert psutil.disk_usage("/srv/data").percent == pytest.approx(87.8, abs=0.1)
    # What the model does not answer is still psutil: its errors and its types.
    assert issubclass(psutil.NoSuchProcess, Exception)
    assert host_reads.KERNEL_RELEASE not in HOST_KERNEL


def test_an_application_that_is_up_has_a_reading_and_none_that_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overview said "No reading" for every application: no sandboxed unit has a cgroup to read."""
    from noust.core.store import App, NoustStore
    from noust.monitor.appsampler import AppSampler
    from scripts.console_server.app_tabs import BASE_MEMORY_MB

    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    try:
        for domain, app_type in (
            ("shop.example.com", "nextjs"),
            ("down.example.com", "nextjs"),
            ("static.example.com", "static"),
        ):
            store.create_app(App(domain=domain, app_type=app_type, app_path=f"/var/www/{domain}"))
        store.record_deployment_start("shop.example.com", "cli")
        units = {
            "shop-example-com": Unit(active="active", pid=2101),
            "down-example-com": Unit(active="failed"),
        }
        domains = {"shop-example-com": "shop.example.com", "down-example-com": "down.example.com"}
        # Put back what the model replaces.
        monkeypatch.setattr(AppSampler, "sample", AppSampler.sample)

        host_reads.model_app_readings(units, domains)
        pairs = dict(AppSampler(store).sample({}, 0.0, None))

        assert set(pairs) == {"app.shop.example.com.mem.bytes", "app.shop.example.com.cpu.percent"}
        # The history's own model, at this moment: the last point of a chart is the number beside it.
        assert 0.1 <= pairs["app.shop.example.com.cpu.percent"] < 100
        memory_mb = pairs["app.shop.example.com.mem.bytes"] / 1024**2
        assert BASE_MEMORY_MB["nextjs"] - 10 < memory_mb < BASE_MEMORY_MB["nextjs"] + 120
    finally:
        store.close()
        NoustStore.reset_instance()
