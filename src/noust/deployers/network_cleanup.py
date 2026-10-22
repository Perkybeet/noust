# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application leaves in the network when it is deleted.

An application's site and certificate go with it (:func:`noust.managers.webserver.delete_site_completely`).
Four more things were made for it and live elsewhere, and a deletion that forgot them left
a name pointing at nothing, a port open to a service that is gone, a route that answers 502
and a password file nobody can reach:

- the **DNS records** Noust made for its names, deleted if it made them and nothing else
  uses them (an adopted record is left, as it was found);
- the **tunnel routes** made for it, taken out of the ingress Cloudflare holds;
- the **published TCP and UDP ports** that were for it, closed;
- its **policy** (basic-authentication accounts included) and its **certificate choice**.

The one place these are listed, called by :func:`noust.deployers.lifecycle.delete_app`.
Each step is attempted whatever the one before it did, and what failed is returned as a
warning: a deletion that stopped at the first failure left the rest of the application
half there.
"""

from __future__ import annotations

import sqlite3

from noust.core.certificate_store import CertificateStore
from noust.core.exceptions import NoustError
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.deployers import access


def forget_network(app_domain: str, log: Logger) -> list[str]:
    """
    Let go of everything the network holds for a deleted application.

    Args:
        app_domain: The application's primary domain.
        log: Where each step is reported.

    Returns:
        One sentence per thing that could not be let go of; empty when all was.
    """
    if is_rehearsal():
        return []
    warnings: list[str] = []

    def failed(what: str, error: BaseException) -> None:
        warnings.append(f"{what}: {error}")
        log.warning(warnings[-1])

    try:
        for route in _tunnel_routes(app_domain):
            log.info(f"Removed the tunnel route {route}")
    except (NoustError, sqlite3.Error) as exc:
        failed("Its tunnel routes were not all removed", exc)
    try:
        for name in _streams(app_domain):
            log.info(f"Closed the published port {name}")
    except (NoustError, sqlite3.Error) as exc:
        failed("Its published ports were not all closed", exc)
    try:
        from noust.managers.dns.records import ManagedDns

        for released in ManagedDns().release(app_domain):
            log.info(f"DNS: {released.record_type} {released.hostname} {released.outcome.value}")
    except (NoustError, sqlite3.Error) as exc:
        failed("Its DNS records were not all released", exc)
    try:
        access.forget(app_domain)
        CertificateStore().forget(app_domain)
    except (NoustError, sqlite3.Error) as exc:
        failed("Its policy and certificate choice were not forgotten", exc)
    return warnings


def _tunnel_routes(app_domain: str) -> list[str]:
    """
    Take an application's routes out of the tunnels that carry them.

    Args:
        app_domain: The application's primary domain.

    Returns:
        ``tunnel/hostname`` of each route removed.

    Raises:
        NoustError: Cloudflare refused one; the earlier ones are removed.
    """
    from noust.core.tunnel_store import TunnelStore
    from noust.managers.tunnels import TunnelManager

    routes = TunnelStore().routes_of_app(app_domain)
    if not routes:
        return []
    manager = TunnelManager()
    removed: list[str] = []
    for route in routes:
        manager.remove_route(route.tunnel, route.hostname)
        removed.append(f"{route.tunnel}/{route.hostname}")
    return removed


def _streams(app_domain: str) -> list[str]:
    """
    Close the published ports that were for an application.

    Args:
        app_domain: The application's primary domain.

    Returns:
        The names that were closed.

    Raises:
        NoustError: One could not be closed; the earlier ones are.
    """
    from noust.managers.stream_exposure import StreamExposures

    streams = StreamExposures()
    closed: list[str] = []
    for exposure in streams.all():
        if exposure.app_domain == app_domain:
            streams.close(exposure.name)
            closed.append(exposure.name)
    return closed
