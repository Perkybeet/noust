# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The seeds of a whole machine: an empty one and the one the console is developed against."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .app_tabs import (
    _tabs_diagnose_model,
    _tabs_journal_model,
    _tabs_port_model,
    _TabsApp,
    seed_app_tabs_history,
    seed_deploylinks_commit_messages,
)
from .backup_destinations import seed_showcase_nginx_error_log, seed_site_logs
from .databases import seed_release_22
from .domains import seed_domains_and_sources
from .machine import Unit
from .monitor_seed import seed_activity_audit_log, seed_job_history, seed_monitor
from .recipes_and_export import (
    seed_release_23,
    seed_showcase_app_metrics,
    seed_showcase_machine_metrics,
    seed_showcase_php_fpm_pools,
)
from .sandbox import Sandbox
from .seeding import seed_backups, seed_console_service, seed_cron, seed_overview_failed_worker
from .timeline import quiet_process_sampling, seed_app_tabs

# ---------------------------------------------------------------------------
# Seeding what the store cannot hold
# ---------------------------------------------------------------------------


def seed_empty_machine(
    sandbox: Sandbox,
) -> tuple[dict[str, Unit], dict[str, int], dict[str, str], list[str]]:
    """
    Seed a server just installed: the store, the database engines, the console, nothing else.

    No application, site, certificate, backup, job, cron entry or monitor notice: the Overview
    of a server like this offers the first application, and the first-run walk starts from it.

    Args:
        sandbox: The sandbox.

    Returns:
        The unit model, each unit's port, each unit's domain and the domains holding
        certificates - what :func:`make_runner` answers from; all but the units are empty.
    """
    from noust.core.store import get_store
    from tests.support.panel_factory import seed_empty_console_state

    store = get_store(sandbox.store_file)
    seed_empty_console_state(store)
    units: dict[str, Unit] = {}
    for engine, active in (
        ("postgresql", "active"),
        ("mysql", "active"),
        ("redis-server", "inactive"),
    ):
        units[engine] = Unit(
            active=active, pid=900 + len(engine) if active == "active" else 0, managed=False
        )
    quiet_process_sampling()
    # The web server and the certificate authority a deploy talks to, and the sources the wizard
    # can read, with no site and no certificate of their own.
    seed_domains_and_sources(
        sandbox, store, units, [], server_names=None, proggest=False, extra_zones=("sslip.io",)
    )
    units["php8.3-fpm"] = Unit(active="active", pid=912, managed=False)
    seed_console_service(sandbox, units)
    return units, {}, {}, []


def seed_machine(
    sandbox: Sandbox,
    *,
    expired_certificate: bool = False,
    showcase: bool = False,
    hostname: str | None = None,
) -> tuple[dict[str, Unit], dict[str, int], dict[str, str], list[str]]:
    """
    Seed the store, then the files and units that live beside it.

    Args:
        sandbox: The sandbox.
        expired_certificate: Seed the first certificate as expired (``--expired-certificate``).
        showcase: Seed :func:`tests.support.showcase.seed_showcase_state`'s invented agency instead
            of :func:`tests.support.panel_factory.seed_console_state`'s example.com machine
            (``--showcase``). Everything below this point reads the seeded application,
            deployment and certificate domains off the store rather than by name, so it
            seeds the showcase's machine exactly as it does the default one; only the
            2.2/2.3 features that are inherently about *tests.support.panel_factory*'s own domains
            (zero-downtime, previews, the GitHub App, backup destinations, the tabs'
            release history) are skipped, since none of the documentation screenshots need
            them.
        hostname: With ``showcase``, which of :data:`tests.support.showcase.NODES` to seed
            (``--hostname``); the agency's main server when None, matching
            :func:`tests.support.showcase.seed_showcase_state`'s own default. Ignored otherwise.

    Returns:
        The unit model, each unit's port, each unit's domain, and the domains
        holding certificates - what :func:`make_runner` answers from.
    """
    from noust.core.store import get_store
    from noust.managers.service_manager import UNIT_MARKER

    store = get_store(sandbox.store_file)
    tabs_history: dict[str, _TabsApp] = {}
    showcase_cert_alt_names: dict[str, str] | None = None
    if showcase:
        from tests.support.showcase import cert_alt_names, seed_showcase_state

        state: Any = seed_showcase_state(store, node=hostname or "fra-1")
        showcase_cert_alt_names = cert_alt_names(hostname or "fra-1")
    else:
        from tests.support.panel_factory import seed_console_state

        # First, so the tabs' old deploys get lower ids than the ones seeded as of now.
        tabs_history = seed_app_tabs_history(store)
        seed_deploylinks_commit_messages(store)
        state = seed_console_state(store)

    units: dict[str, Unit] = {}
    ports: dict[str, int] = {}
    domains: dict[str, str] = {}
    apps = {app.domain: app for app in store.list_apps()}
    for service in store.list_services():
        app = next((a for a in apps.values() if a.id == service.app_id), None)
        if app is None:
            continue
        failed = app.domain in state.failed_domains
        active = "failed" if failed else ("active" if service.status == "active" else "inactive")
        units[service.name] = Unit(
            active=active,
            enabled=service.enabled,
            pid=41000 + (service.id or 0) if active == "active" else 0,
            since=datetime.now() - timedelta(hours=6, minutes=(service.id or 0) * 7),
            restarts=5 if failed else 0,
        )
        ports[service.name] = service.port or 3000
        domains[service.name] = app.domain
        # The file that makes ServiceManager treat it as a unit Noust owns and
        # that exists, which is what start, stop and restart require.
        (sandbox.systemd_dir / f"{service.name}.service").write_text(
            f"# {UNIT_MARKER}\n"
            "[Unit]\n"
            f"Description={app.domain}\n\n"
            "[Service]\n"
            f"WorkingDirectory={service.working_directory}\n"
            f"ExecStart={service.command}\n"
            f"Environment=PORT={service.port}\n",
            encoding="utf-8",
        )

    # The database engines, answered as system units Noust does not own.
    for engine, active in (
        ("postgresql", "active"),
        ("mysql", "active"),
        ("redis-server", "inactive"),
    ):
        units[engine] = Unit(
            active=active, pid=900 + len(engine) if active == "active" else 0, managed=False
        )

    seed_backups(sandbox, state.backup_domains + state.static_domains[:1])
    seed_cron(state.domains[0])
    # Only the agency's main server carries the standalone failed worker and the one
    # benign monitor notice: "ams-3" (a couple of healthy client sites) and "lon-2" (a
    # staging server, everything green) are calmer servers by design (coordinator review),
    # not "fra-1" with its own findings copied onto every node in the fleet.
    node = hostname or "fra-1"
    showcase_calm_node = showcase and node != "fra-1"
    if not showcase_calm_node:
        seed_overview_failed_worker(
            sandbox,
            store,
            units,
            domain=state.domains[0] if showcase else "example.com",
        )
    seed_monitor(sandbox, units, showcase=showcase, showcase_notice=not showcase_calm_node)
    quiet_process_sampling()
    seed_job_history(sandbox, store, state.domains[0])
    seed_activity_audit_log(sandbox, state.domains[0])
    if showcase:
        # The three generic pieces of seed_app_tabs below (despite the name: none of
        # them reads a "tabs" domain, only the units/ports/domains models), without
        # which every application diagnoses as "down: not listening" and every running
        # one's port reads "No answer" (noust.core.app_state.resolve_state does a real
        # socket connect, and nothing in the sandbox actually listens on any port).
        _tabs_journal_model(units, domains, ports)
        _tabs_diagnose_model(units, ports)
        _tabs_port_model(units, ports)
    else:
        seed_app_tabs(sandbox, store, units, ports, domains, tabs_history)
    seed_domains_and_sources(
        sandbox,
        store,
        units,
        list(state.cert_domains),
        expired_certificate=expired_certificate,
        # The showcase's own site already carries the alias tests.support.showcase gave it
        # through store.add_domain; this is only the nginx server_name list a site's
        # own config carries (SitesTab, not the AppDomainsTab), on one of its domains
        # in place of the default machine's example.net.
        server_names=(state.domains[0], ("www",))
        if showcase
        else ("example.net", ("www", "shop", "status")),
        cert_alt_names=showcase_cert_alt_names,
        proggest=not showcase,
    )
    if not showcase:
        seed_release_22(sandbox, store, units, ports, domains)
    seed_release_23(sandbox)
    seed_site_logs(sandbox)
    if showcase:
        seed_showcase_php_fpm_pools(sandbox, store)
        seed_showcase_machine_metrics()
        seed_showcase_app_metrics(store)
        if state.failed_domains:
            seed_showcase_nginx_error_log(sandbox, state.failed_domains[0])
    # PHP 8.3's FPM, a system unit Noust does not own, running the pools recipes write.
    units["php8.3-fpm"] = Unit(active="active", pid=912, managed=False)
    seed_console_service(sandbox, units)
    return units, ports, domains, list(state.cert_domains)
