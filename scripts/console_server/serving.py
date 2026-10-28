# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the server does before it serves: bind, sign-in factors, the console build, the hostname
and the machine's figures."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------


def bind(host: str, port: int) -> socket.socket:
    """
    Bind the listening socket before the server starts.

    Binding here, rather than letting uvicorn do it, is what makes ``--port
    0`` race-free: the port printed is the port held.

    Args:
        host: Loopback address.
        port: Port, or 0 for a free one.

    Returns:
        The listening socket.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def enable_totp(backup_codes: int | None = None) -> tuple[str, list[str]]:
    """
    Turn on two-factor sign-in with a freshly enrolled secret.

    Args:
        backup_codes: How many backup codes to enrol, when not the usual count.

    Returns:
        The secret, so a test can compute codes, and the backup codes.
    """
    from noust.core import totp
    from noust.web.auth import tokens
    from noust.web.server import get_token_manager

    manager = get_token_manager()
    secret = manager.begin_totp_enrollment()
    usual = tokens.BACKUP_CODE_COUNT
    # Only this enrolment: a later "new backup codes" gives the usual count, as in production.
    if backup_codes is not None:
        tokens.BACKUP_CODE_COUNT = backup_codes
    try:
        codes = manager.confirm_totp_enrollment(totp.totp_now(secret))
    finally:
        tokens.BACKUP_CODE_COUNT = usual
    if codes is None:
        raise RuntimeError("two-factor enrolment did not accept its own code")
    return secret, codes


def use_console_build(static_dir: Path) -> None:
    """
    Point the server at a console build other than the committed one.

    Development and E2E only: parallel work on the console builds into private
    directories, because two builds racing into ``noust/web/static`` would serve
    each other half-written chunks. Production always serves the committed build.

    Args:
        static_dir: A directory holding a Vite build (``index.html`` + ``assets/``).

    Raises:
        SystemExit: When the directory holds no build.
    """
    from noust.web import server

    static_dir = static_dir.resolve()
    if not (static_dir / "index.html").is_file():
        raise SystemExit(f"{static_dir} holds no console build (no index.html)")
    server.STATIC_DIR = static_dir
    server.ASSETS_DIR = static_dir / "assets"
    server.INDEX_HTML = static_dir / "index.html"


def use_fixed_hostname(hostname: str) -> None:
    """
    Make the console report a fixed hostname instead of this machine's own.

    Development and screenshots only: a recording or a review screenshot should not carry
    the developer's real machine name. The name is read with a bare ``socket.gethostname()``
    in the modules patched below - the machine snapshot, the session answer and TOTP account
    name, the Overview's header and the fleet's default names - none of which uses ``socket``
    for anything else, so each gets a stand-in bound to its own name, and the real module is untouched for
    everything else in the process.

    Args:
        hostname: The name to report.
    """
    import noust.fleet.models as fleet_models
    import noust.managers.overview as overview_module
    import noust.web.api.auth as auth_api
    import noust.web.machine as machine_module

    fixed = SimpleNamespace(gethostname=lambda: hostname)
    machine_module.socket = fixed  # type: ignore[assignment]
    auth_api.socket = fixed  # type: ignore[assignment]
    # The Overview's header (3.1) and the fleet's default names read it too.
    overview_module.socket = fixed  # type: ignore[assignment]
    fleet_models.socket = fixed  # type: ignore[assignment]


def use_fixed_machine_stats(
    *,
    cpu_percent: float,
    memory_percent: float,
    disk_percent: float,
    uptime_s: float,
    net_rx_bytes_s: float = 4_200.0,
    net_tx_bytes_s: float = 1_800.0,
    load_1m: float = 0.8,
) -> None:
    """
    Make the console report fixed CPU, memory, disk, network, load and uptime readings
    instead of this machine's real ones (``--showcase`` only).

    Three independent readers need this, not one:

    - Every server in the showcase's fleet is really the same sandboxed host running
      several processes side by side, so without this, ``psutil`` answers each node's
      machine snapshot (:func:`noust.web.machine.read_machine`, which the REST endpoint
      and the ``/events`` push both go through) with nearly the same numbers - not a fleet
      of different machines, three readings of the one machine running the demo
      (coordinator review, on the fleet page).
    - :class:`~noust.web.metrics_collector.MetricsCollector` samples the same real
      ``psutil`` independently, every couple of seconds, for the Overview's charts - left
      unpatched, its genuinely noisy (often near-idle) readings make a chart's last few
      points visibly disagree with the top bar's own number (coordinator review, CPU
      dropping to 0% beside a top bar reading in the 20-40s). Patching both to the same
      fixed numbers is what makes a chart's tail, the top bar and every node's Fleet row
      agree, the way one real, particular machine would.
    - Both of those read the load average with a bare ``os.getloadavg()``, never through
      ``psutil``, and this sandbox's real one is this whole *development machine's* -
      running every other showcase process, several browsers and whatever else the
      person capturing screenshots is doing at the time - which is how a "Load" reading
      as high as 8 got into a screenshot next to others reading 0.7 (coordinator review).
      ``os.getloadavg`` is patched globally (harmless: nothing else in this sandboxed
      process needs the real figure) rather than on just these two modules' own
      ``import os``, which is the whole standard library module either way.

    ``psutil.net_io_counters()`` is a running total, not a live reading, so the fake counts
    up from zero at whatever rate is given rather than returning a fixed pair - the
    collector's own rate is a delta between two calls, and two identical totals would read
    as no traffic at all.

    Args:
        cpu_percent: CPU utilisation to report, 0-100.
        memory_percent: Memory used, 0-100; used/total are synthesised to match.
        disk_percent: Disk used, 0-100; used/total are synthesised to match.
        uptime_s: Seconds since boot to report.
        net_rx_bytes_s: Inbound network rate to hold steady.
        net_tx_bytes_s: Outbound network rate to hold steady.
        load_1m: The 1-minute load average to hold steady, comfortably under 1 on a
            machine this size; the 5- and 15-minute figures are held a little lower still,
            the shape a load that has been steady for a while actually has.
    """
    import os as os_module

    import noust.monitor.sampler as sampler_module

    os_module.getloadavg = lambda: (load_1m, load_1m * 0.9, load_1m * 0.8)

    mem_total = 16 * 1024**3
    disk_total = 480 * 1024**3
    boot_time = time.time() - uptime_s
    counters_from = time.time()

    def net_io_counters() -> Any:
        elapsed = max(0.0, time.time() - counters_from)
        return SimpleNamespace(
            bytes_recv=int(net_rx_bytes_s * elapsed), bytes_sent=int(net_tx_bytes_s * elapsed)
        )

    fixed = SimpleNamespace(
        cpu_percent=lambda interval=None: cpu_percent,
        virtual_memory=lambda: SimpleNamespace(
            used=int(mem_total * memory_percent / 100), total=mem_total, percent=memory_percent
        ),
        swap_memory=lambda: SimpleNamespace(used=int(mem_total * 0.015)),
        disk_usage=lambda path=None: SimpleNamespace(
            used=int(disk_total * disk_percent / 100), total=disk_total, percent=disk_percent
        ),
        boot_time=lambda: boot_time,
        net_io_counters=net_io_counters,
    )
    # The one place the machine is read: the header strip, the chart collector
    # and the monitor's resource scan all go through the sampler.
    sampler_module.psutil = fixed
