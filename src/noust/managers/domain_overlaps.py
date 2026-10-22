# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which other sites answer on a name an application is about to take.

The store already refuses a name another application has (one row per name). What it
cannot see is the web server's own configuration: a site the operator wrote by hand, a
Compose stack's, the vhost of a service Noust does not know. Two server blocks claiming
one name is not an error to nginx - it settles it by file order and prints a warning
nobody reads - so the application added second silently serves nothing, or the one that
was there first loses the name.

This reads every other enabled site with the one parser (:mod:`noust.managers.siteconf`)
and asks the web server's own selection (:func:`~noust.managers.siteconf.route.route`)
which of its servers would answer the name, so the verdict is the web server's and not a
regular expression's:

- ``exact``: the other site names it.
- ``wildcard``: the other site holds a wildcard (``*.example.com``) that covers it, and
  adding the name takes those requests from it.
- ``covers``: the name being added is itself a wildcard, and the other site serves names
  under it; exact names win over wildcards, so it keeps them, but the operator should
  know the wildcard sits over a neighbour.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from noust.managers.siteconf import Kind, ParseError, parse
from noust.managers.siteconf.model import structure
from noust.managers.siteconf.route import route
from noust.managers.webserver import WebServerManager
from noust.validators.domain import is_wildcard, wildcard_covers

_log = logging.getLogger(__name__)

#: The steps of the web server's selection that mean "a name matched", not "no name did".
_NAME_MATCHED = {
    "server_exact": "exact",
    "server_leading": "wildcard",
    "server_trailing": "wildcard",
    "server_regex": "wildcard",
    "apache_name": "exact",
}


@dataclass(frozen=True)
class Overlap:
    """
    Another site that answers on a name.

    Attributes:
        site: The site's name (its file in the sites directory).
        name: The name being added.
        matched: The name of that site's that matched it.
        how: ``exact``, ``wildcard`` or ``covers`` (see the module docs).
    """

    site: str
    name: str
    matched: str
    how: str

    def describe(self) -> str:
        """
        Returns:
            One sentence for the operator.
        """
        if self.how == "exact":
            return f"the site {self.site} already answers on {self.name}"
        if self.how == "wildcard":
            return (
                f"the site {self.site} answers on {self.matched}, which covers {self.name}: "
                "adding it takes those requests from that site"
            )
        return (
            f"the site {self.site} serves {self.matched}, which {self.name} covers: it keeps "
            "its name (exact names win), but the wildcard sits over it"
        )


def find_overlaps(
    manager: WebServerManager, name: str, *, own: Collection[str] = ()
) -> list[Overlap]:
    """
    Find the other enabled sites that answer on a name.

    Args:
        manager: The web server manager whose sites are read.
        name: The name about to be added (a wildcard is allowed).
        own: File names of the sites that are the application's own, left out.

    Returns:
        One entry per overlapping site, in the order the sites are listed.
    """
    kind: Kind = "nginx" if manager.backend.name == "nginx" else "apache"
    found: list[Overlap] = []
    for info in manager.list_sites():
        if info.domain in own or Path(info.config_path).name in own or not info.enabled:
            continue
        try:
            text = Path(info.config_path).read_text(encoding="utf-8", errors="replace")
            model = structure(parse(text, kind))
        except (OSError, ParseError) as exc:
            _log.debug("Could not read %s to look for overlaps: %s", info.config_path, exc)
            continue
        if not is_wildcard(name):
            answer = route(model, host=name, path="/", scheme="http", port=80)
            how = next(
                (_NAME_MATCHED[step.code] for step in answer.trace if step.code in _NAME_MATCHED),
                "",
            )
            if how:
                matched = next(
                    (
                        str(step.params.get("name") or name)
                        for step in answer.trace
                        if step.code in _NAME_MATCHED
                    ),
                    name,
                )
                found.append(Overlap(info.domain, name, matched, how))
            continue
        covered = [
            held
            for server in model.servers
            for held in server.names
            if not is_wildcard(held) and wildcard_covers(name, held)
        ]
        exact = [held for server in model.servers for held in server.names if held == name]
        if exact:
            found.append(Overlap(info.domain, name, name, "exact"))
        elif covered:
            found.append(Overlap(info.domain, name, covered[0], "covers"))
    return found
