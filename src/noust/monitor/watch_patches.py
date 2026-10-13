# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Operating system updates that are waiting (checklist K-22).

``noust server updates list`` and the console already know how to ask the
package manager what is pending. Nobody asks every day. This watch asks once a
day (``monitor.os_patches_check_hours``, 24; 0 turns it off) and tells the
operator when there is something, through the same channels as everything else:

- when **security updates** are waiting, and again whenever the set of them
  changes or a week passes without them being installed;
- when only ordinary updates are waiting, once for each new set, but never more
  than once a week: they are information, not an alarm;
- when the check **cannot be made** (the package database is locked or broken),
  once for each stretch of failures, so a server that has not been able to look
  for updates for a month is not a server that quietly has none.

Nothing is installed here, ever: ``noust server updates apply`` is the operator's.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from functools import partial
from typing import Any

from noust.core.exceptions import NoustError
from noust.core.notifications.composers_ops import (
    compose_os_patches,
    compose_os_patches_check_failed,
)
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers.
SCOPE = "patches"

DEFAULT_CHECK_HOURS = 24.0

#: An unchanged set of updates is announced again after this long.
RENOTIFY_SECONDS = 7 * 86_400


def signature(packages: list[Any]) -> str:
    """
    Fingerprint a set of pending updates.

    Args:
        packages: The pending updates (objects with ``name``, ``candidate``
            and ``security``).

    Returns:
        A short digest that changes when a package, its candidate version or
        its security flag does: a new version of a package already pending is
        a new thing to know.
    """
    lines = sorted(f"{p.name}={p.candidate}:{int(bool(p.security))}" for p in packages)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


class PatchWatch(Watch):
    """Announces updates of the operating system that are waiting."""

    name = "patches"
    default_interval_s = DEFAULT_CHECK_HOURS * 3600

    def __init__(self, pending: Callable[[WatchServices], Any] | None = None) -> None:
        """
        Args:
            pending: Returns the server's :class:`PendingUpdates` (and, when
                it can, whether a reboot is due as ``(pending, reboot)``); the
                updates manager's by default. Tests inject one.
        """
        self._pending = pending

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            ``monitor.os_patches_check_hours`` in seconds; zero turns it off.
        """
        hours = float(services.config.get("monitor.os_patches_check_hours", DEFAULT_CHECK_HOURS))
        return max(hours, 0) * 3600

    def _read(self, services: WatchServices) -> tuple[Any, bool | None] | None:
        """
        Ask the package manager what is pending.

        Args:
            services: What the monitor lends, for the runner.

        Returns:
            The pending updates and whether a reboot is due; None when updates
            are not managed on this machine at all (nothing to announce, and
            no failure either).
        """
        if self._pending is not None:
            found = self._pending(services)
            if found is None:
                return None
            return found if isinstance(found, tuple) else (found, None)
        from noust.managers.server.updates import UpdatesManager

        manager = UpdatesManager(runner=services.runner)
        if not manager.platform.updates_supported:
            return None
        pending = manager.pending()
        try:
            reboot: bool | None = manager.reboot_status().required
        except NoustError:
            reboot = None
        return pending, reboot

    def check(self, services: WatchServices) -> None:
        """
        Ask what is pending and say so when it is news.

        Args:
            services: What the monitor lends.
        """
        state = services.state
        try:
            found = self._read(services)
        except NoustError as exc:
            if state.get(SCOPE, "check_failed") is None:
                state.put(SCOPE, "check_failed", str(services.clock()))
                evidence = "\n".join(
                    part for part in (exc.message, exc.output or exc.details) if part
                )
                services.publish(partial(compose_os_patches_check_failed, evidence))
            return
        state.delete(SCOPE, "check_failed")
        if found is None:
            return
        pending, reboot = found
        packages = [
            p for p in pending.packages if getattr(p, "kind", "package") == "package"
        ] or list(pending.packages)
        if not packages:
            state.delete(SCOPE, "announced")
            return

        security = [p for p in packages if p.security]
        digest = signature(packages)
        said = state.get_json(SCOPE, "announced") or {}
        age = services.clock() - float(said.get("at", 0) or 0)
        changed = said.get("signature") != digest
        if security:
            news = changed or age >= RENOTIFY_SECONDS
        else:
            news = (changed and age >= RENOTIFY_SECONDS) or not said
        if not news:
            return
        state.put_json(SCOPE, "announced", {"signature": digest, "at": services.clock()})
        names = [p.name for p in security] + [p.name for p in packages if not p.security]
        services.publish(
            partial(
                compose_os_patches,
                len(packages),
                len(security),
                packages=names,
                reboot_required=reboot,
            )
        )
