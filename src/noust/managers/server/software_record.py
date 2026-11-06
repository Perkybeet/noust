# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Writing down that Noust installed a piece of software: the one place that does it (item 118).

Every installer of the registry's components (cloudflared's, the WAF's, fail2ban's, Knot's, Docker's,
a database engine's, and the software manager's own for a plain package) ends by calling
:func:`record_install`, so the ledger (:class:`~noust.core.software_ledger.SoftwareLedger`) holds the
same row whichever way the piece came: the console's button on its own page, a command, a recipe that
asks for an engine, or Server > Software. Nothing else writes the ledger.

A ledger that cannot be written does not undo an install that worked. The piece is recognised by the
marks Noust leaves all the same, and the failure is in the log.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from noust.core.audit.context import current_actor
from noust.core.exceptions import NoustError
from noust.core.fs import is_rehearsal
from noust.core.software_ledger import InstalledSoftware, SoftwareLedger
from noust.managers.server import components
from noust.managers.server.components import Component
from noust.managers.server.host import HostPaths, PackageFamily

log = logging.getLogger(__name__)

__all__ = ["marks_present", "packages_named_in", "record_install"]


class Ledger(Protocol):
    """What :func:`record_install` needs of the ledger."""

    def record(
        self,
        component: str,
        *,
        packages: Sequence[str] = ...,
        repository_files: Sequence[str] = ...,
        via: str,
        actor: str | None = ...,
    ) -> InstalledSoftware:
        """Write down an install."""


def marks_present(component: Component, host: HostPaths | None = None) -> list[Path]:
    """
    Find the files Noust writes for a component that exist now.

    Args:
        component: The component.
        host: Where the system files are; the real machine by default.

    Returns:
        The files, below the host's root, in the order of the component's marks.
    """
    root = (host or HostPaths()).root
    found: list[Path] = []
    for pattern in component.marks:
        found.extend(sorted(root.glob(pattern.lstrip("/"))))
    return found


def packages_named_in(component_id: str, commands: Iterable[Sequence[str]]) -> list[str]:
    """
    Find which of a component's packages some install commands name.

    An installer that builds its own commands (EPEL first, then the package) knows what it ran
    and not which names were the component's: this reads them out, each once, in order.

    Args:
        component_id: The component's id in the registry.
        commands: The argv of the install steps.

    Returns:
        The packages of the component, on any family, that appear in the commands.
    """
    component = components.component(component_id)
    if component is None:
        return []
    known = {name for family in PackageFamily for name in component.candidates_on(family)}
    return list(dict.fromkeys(token for argv in commands for token in argv if token in known))


def record_install(
    component_id: str,
    *,
    via: str,
    packages: Sequence[str] = (),
    repository_files: Sequence[str] | None = None,
    host: HostPaths | None = None,
    ledger: Ledger | None = None,
) -> None:
    """
    Write down that Noust installed a component, or what a later install added to it.

    Args:
        component_id: The component's id in the registry.
        via: The installer that did it (:class:`~noust.managers.server.components.Installer`'s value).
        packages: What the install brought.
        repository_files: The repository source and key files Noust added, as they are on a real
            machine. When it is None, the files Noust writes for the component that exist now:
            an installer that adds a repository leaves exactly those.
        host: Where the system files are; the real machine by default.
        ledger: The ledger; the store's by default.
    """
    if is_rehearsal():
        # The store is the one write neither seam covers: a rehearsal does not install, so there
        # is nothing to write down.
        return
    component = components.component(component_id)
    if component is None:
        log.warning("Not recording the install of %s: it is not in the registry", component_id)
        return
    if repository_files is None:
        root = (host or HostPaths()).root
        repository_files = [
            str(Path("/") / path.relative_to(root)) for path in marks_present(component, host)
        ]
    actor = current_actor()
    try:
        (ledger or SoftwareLedger()).record(
            component_id,
            packages=packages,
            repository_files=repository_files,
            via=via,
            actor=actor.label if actor else None,
        )
    except (NoustError, sqlite3.Error, OSError) as exc:
        log.warning("Could not record that %s was installed: %s", component_id, exc)
