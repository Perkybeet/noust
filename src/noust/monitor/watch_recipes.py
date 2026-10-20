# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Installed recipes that have a newer version, said before anybody looks (checklist D-15).

Coolify never updates a service it created from a template; Noust remembers the version and
revision each application was installed from, so ``noust recipe outdated`` and the catalogue's
Installed tab can say what is behind and what changed. Both wait for somebody to open them. This
watch says it with a notification (``recipe_outdated``) when the set of things that are behind
changes: a new version of a recipe that was current, a new application installed from an old
one. The same list stays quiet while nothing changes, and a week passes before it is repeated
(``monitor.recipe_renotify_days``; 0 never repeats).

The catalogue ships inside Noust, so the question costs a read of the store and no request.
"""

from __future__ import annotations

import hashlib
from functools import partial

from noust.core.notifications.composers_netobs import compose_recipes_outdated
from noust.monitor.watches import Watch, WatchServices
from noust.recipes.updates import RecipeUpdateStatus, outdated

#: The scope of what this watch remembers: the signature announced and when.
SCOPE = "recipes"
ITEM = "announced"

DEFAULT_CHECK_HOURS = 24.0
DEFAULT_RENOTIFY_DAYS = 7.0


def signature(behind: list[RecipeUpdateStatus]) -> str:
    """
    Name a set of outdated recipes, so that a change of it is noticed.

    Args:
        behind: The statuses with an update available.

    Returns:
        A short digest of ``application, installed, latest`` for each, sorted.
    """
    parts = sorted(
        f"{status.domain}|{status.installed_version}.{status.installed_revision}|"
        f"{status.latest_version}.{status.latest_revision}"
        for status in behind
    )
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


class RecipeWatch(Watch):
    """Announces installed recipes the catalogue is ahead of."""

    name = "recipes"
    default_interval_s = DEFAULT_CHECK_HOURS * 3600

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            ``monitor.recipe_check_hours`` in seconds; zero (off) when it says so.
        """
        hours = float(services.config.get("monitor.recipe_check_hours", DEFAULT_CHECK_HOURS))
        return max(hours, 0.0) * 3600

    def check(self, services: WatchServices) -> None:
        """
        List the recipes that are behind and announce a change of the list.

        Args:
            services: What the monitor lends.
        """
        behind = outdated()
        if not behind:
            services.state.delete(SCOPE, ITEM)
            return
        current = signature(behind)
        renotify = float(services.config.get("monitor.recipe_renotify_days", DEFAULT_RENOTIFY_DAYS))
        said = services.state.get_json(SCOPE, ITEM)
        now = services.clock()
        if isinstance(said, dict) and said.get("signature") == current:
            if renotify <= 0 or now - float(said.get("at", 0)) < renotify * 86_400:
                return
        services.state.put_json(SCOPE, ITEM, {"signature": current, "at": now})
        rows = [
            (
                status.domain,
                status.recipe,
                status.installed_version,
                status.latest_version or "",
            )
            for status in sorted(behind, key=lambda item: item.domain)
        ]
        services.publish(partial(compose_recipes_outdated, rows))
