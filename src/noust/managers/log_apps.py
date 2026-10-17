# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which units an application logs as, for the journal's ``@app:`` field.

The journal reader (:mod:`noust.managers.server.journal`) knows the machine and nothing of the
applications Noust deploys, and must not: a query language that reached into the store would be a
second place that decides what an application is. The one mapping from an application to its units
is :meth:`ServiceManager.app_units`; this hands it to the reader, one application at a time.
"""

from __future__ import annotations

from noust.core.runner import CommandRunner
from noust.core.store import get_store
from noust.managers.server.logquery import AppUnits
from noust.managers.service_manager import ServiceManager


def app_units_resolver(*, runner: CommandRunner | None = None) -> AppUnits:
    """
    Build the function a query uses to turn ``@app:shop.example.com`` into units.

    Args:
        runner: Command runner for ``systemctl`` (the process-wide one when omitted).

    Returns:
        A function from a domain to the units it runs as; empty for a domain Noust does not
        deploy. It remembers its answers: one query may name an application twice.
    """
    store = get_store()
    services = ServiceManager(runner=runner) if runner is not None else ServiceManager()
    known: dict[str, tuple[str, ...]] = {}

    def units(domain: str) -> tuple[str, ...]:
        if domain not in known:
            app = store.get_app(domain)
            known[domain] = tuple(services.app_units(app)) if app is not None else ()
        return known[domain]

    return units
