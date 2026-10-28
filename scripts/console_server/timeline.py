# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

""" "Investigate this stretch" (3.2, flow F1): the CPU peak of a release and the seed of the tabs
that read it."""

from __future__ import annotations

import time
from typing import Any

from .app_tabs import (
    TABS_LIVE_APP,
    TABS_MIGRATE_APPS,
    TABS_RELEASE_APP,
    _tabs_diagnose_model,
    _tabs_inplace_app,
    _tabs_journal_model,
    _tabs_metrics,
    _tabs_port_model,
    _tabs_release_app,
    _tabs_slow_live_builds,
    _TabsApp,
    seed_deploy_actions,
)
from .machine import Unit
from .sandbox import Sandbox
from .seeding import seed_backups
from .sources import seed_sources_and_delivery

# ---------------------------------------------------------------------------
# Console: "Investigate this stretch" (3.2, flow F1). A CPU peak of the release
# app half an hour ago with the processes behind it, minute by minute, and the
# unit failure the monitor saw then; the collector's own process ranking is
# switched off, so this sandbox never records the development machine's
# processes (or their command lines).
# ---------------------------------------------------------------------------

#: How long ago the seeded peak was, in minutes, and how many minutes it lasted.
TIMELINE_PEAK_MINUTES_AGO = 40
TIMELINE_PEAK_LENGTH = 10


def quiet_process_sampling() -> None:
    """Keep the collector from ranking this development machine's own processes."""
    import noust.monitor.process_samples as process_samples

    process_samples.read_process_table = lambda: []  # type: ignore[assignment]


def seed_timeline_peak(domain: str) -> None:
    """
    Write the processes of a CPU peak, and what the monitor saw during it.

    Args:
        domain: The application whose build caused the peak.
    """
    from noust.monitor.process_samples import RANK_CPU, RANK_MEMORY, ProcessSample
    from noust.monitor.timeseries import EVENT_UNIT_FAILED
    from noust.web.metrics_collector import get_metrics_store

    quiet_process_sampling()
    store = get_metrics_store()
    unit = domain.replace(".", "-")
    first = (int(time.time()) // 60 - TIMELINE_PEAK_MINUTES_AGO) * 60
    rows: list[ProcessSample] = []
    for minute in range(TIMELINE_PEAK_LENGTH):
        ts = first + minute * 60
        build = 92.0 - abs(minute - TIMELINE_PEAK_LENGTH / 2) * 6
        cpu = [
            (
                "node",
                4211,
                "www-data",
                build,
                610,
                domain,
                f"{unit}.service",
                "node /var/www/apps/tienda-example-org/current/node_modules/.bin/next build",
            ),
            (
                "postgres",
                990,
                "postgres",
                14.0,
                880,
                None,
                "postgresql@16-main.service",
                "postgres: 16/main: tienda tienda 127.0.0.1(51122) SELECT",
            ),
            ("nginx", 1021, "www-data", 3.1, 42, None, "nginx.service", "nginx: worker process"),
        ]
        memory = [
            cpu[1],
            cpu[0],
            (
                "redis-server",
                977,
                "redis",
                0.4,
                210,
                None,
                "redis-server.service",
                "/usr/bin/redis-server 127.0.0.1:6379",
            ),
        ]
        for rank, ranked in ((RANK_CPU, cpu), (RANK_MEMORY, memory)):
            for position, (name, pid, user, percent, mb, app, owner, command) in enumerate(
                ranked, start=1
            ):
                rows.append(
                    ProcessSample(
                        ts=ts,
                        rank=rank,
                        position=position,
                        pid=pid,
                        name=name,
                        user=user,
                        cpu_percent=percent,
                        memory_bytes=mb * 1024 * 1024,
                        memory_percent=round(mb / 16_384 * 100, 2),
                        app=app,
                        owner_kind="unit",
                        owner=owner,
                        command=command,
                    )
                )
    store.record_process_samples(rows)
    store.record_monitor_event(
        EVENT_UNIT_FAILED,
        unit,
        detail=f"systemd reports {unit} failed: result exit-code, main process exit status 1.",
        reason="failed",
        ts=first + (TIMELINE_PEAK_LENGTH - 2) * 60,
    )


def seed_app_tabs(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    history: dict[str, _TabsApp],
) -> None:
    """
    Seed what an application's tabs need beyond :func:`seed_console_state`.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        history: What :func:`seed_app_tabs_history` recorded first.
    """
    _tabs_release_app(sandbox, store, units, ports, domains, history[TABS_RELEASE_APP])
    live_root = _tabs_inplace_app(
        sandbox, store, units, ports, domains, TABS_LIVE_APP, history[TABS_LIVE_APP]
    )
    for domain in TABS_MIGRATE_APPS:
        _tabs_inplace_app(sandbox, store, units, ports, domains, domain, history[domain])
    # The in-place app rolls back to a backup: give it the two a machine would have.
    seed_backups(sandbox, [TABS_LIVE_APP])
    _tabs_metrics(TABS_RELEASE_APP, history[TABS_RELEASE_APP].starts, base_mb=182)
    _tabs_metrics(TABS_LIVE_APP, history[TABS_LIVE_APP].starts, base_mb=96)
    seed_timeline_peak(TABS_RELEASE_APP)
    _tabs_slow_live_builds(live_root)
    seed_deploy_actions(sandbox, store, units, ports, domains, history)
    seed_sources_and_delivery(sandbox, store)
    _tabs_journal_model(units, domains, ports)
    _tabs_diagnose_model(units, ports)
    _tabs_port_model(units, ports)
