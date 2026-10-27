# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which web server what is deployed sits behind, and when the setting may change (backlog item 105).

``webserver`` in the configuration is the web server **new** applications are put behind. Every
application remembers its own in ``apps.webserver``, so the setting never moves one: changing it to
Apache with 25 applications on nginx left those 25 where they were and sent the next to Apache,
while nginx kept ports 80 and 443. Nobody was told.

The setting is refused, at the one place every way of changing it passes (``Config.set``: the
command line, ``PUT /api/config/webserver`` and the generic ``PATCH /api/config``), while
applications or sites of Noust's are still behind a server other than the one asked for, and the
refusal says how many are where. Moving an application from one server to the other is a migration
of its own, and it is what makes the change possible.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from noust.core.exceptions import NoustError, WebserverInUseError
from noust.core.store import NoustStore, get_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebserverUsage:
    """
    What sits behind each web server, as Noust records it.

    Attributes:
        applications: Applications by the web server each remembers.
        sites: Sites that belong to no application (an operator's, or a status page's) by their
            web server; an application's own site is counted with the application.
    """

    applications: dict[str, int]
    sites: dict[str, int]

    def behind_other_than(self, webserver: str) -> tuple[int, int]:
        """
        Args:
            webserver: A web server.

        Returns:
            How many applications and how many sites are behind any other.
        """
        return (
            sum(count for name, count in self.applications.items() if name != webserver),
            sum(count for name, count in self.sites.items() if name != webserver),
        )


def usage(store: NoustStore | None = None) -> WebserverUsage:
    """
    Count what is behind each web server.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        The counts.
    """
    store = store or get_store()
    applications: dict[str, int] = {}
    for app in store.list_apps():
        applications[app.webserver] = applications.get(app.webserver, 0) + 1
    sites: dict[str, int] = {}
    for site in store.list_sites():
        if site.app_id is None:
            sites[site.webserver] = sites.get(site.webserver, 0) + 1
    return WebserverUsage(applications, sites)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def describe(applications: int, sites: int) -> str:
    """
    Args:
        applications: A number of applications.
        sites: A number of sites.

    Returns:
        Them as a person says it: ``25 applications and 1 site``, ``nothing`` for none.
    """
    parts = [
        _count(number, noun)
        for number, noun in ((applications, "application"), (sites, "site"))
        if number
    ]
    return " and ".join(parts) or "nothing"


def refuse_switch(current: str, new: str, store: NoustStore | None = None) -> None:
    """
    Refuse to change the web server of new applications while the other one still serves sites.

    Args:
        current: The web server the setting names now.
        new: The one it is being changed to.
        store: The store; the process-wide one by default.

    Raises:
        WebserverInUseError: Applications or sites are still behind a server other than ``new``;
            the message and details say how many are on each.
    """
    if new == current:
        return
    try:
        use = usage(store)
    except (sqlite3.Error, OSError, NoustError) as exc:
        # The setting only decides where the next application goes; not being able to count must
        # not make it impossible to change, and the answer is not worth a guess.
        logger.debug("What sits behind each web server was not counted: %s", exc)
        return
    apps, sites = use.behind_other_than(new)
    if not apps and not sites:
        return
    others = sorted({*use.applications, *use.sites} - {new})
    behind = " and ".join(others) if others else current
    there = describe(apps, sites)
    ours = describe(use.applications.get(new, 0), use.sites.get(new, 0))
    verb = "is" if apps + sites == 1 else "are"
    raise WebserverInUseError(
        f"{there} {verb} still behind {behind}, which holds ports 80 and 443",
        details=(
            f"This setting is the web server new applications are put behind; each application "
            f"keeps its own, and nothing here moves one. Changing it to {new} now would send the "
            f"next application to {new} while {behind} holds the ports. Behind {new} today: "
            f"{ours}. Move what is behind {behind} first, or leave the setting as it is."
        ),
        field="webserver",
    )
