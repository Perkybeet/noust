# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Noust release that fixes a vulnerability, announced where the operator reads (checklist L-43).

The update check already tells a security release from an ordinary one: ``release.yml`` titles it
"(security release)" when its changelog starts a line with ``SECURITY:``, and the CLI's banner,
``noust health`` and the console's About page say so. All of those wait for somebody to look. A
server nobody opens for three weeks keeps running a version with a published fix, which is what
the products this is compared with are criticised for. This watch says it with a notification,
once per release, and again every ``monitor.security_renotify_hours`` (72; 0 never) while the
server still runs a version below the fix.

It asks the update check (:class:`~noust.core.update_checker.UpdateChecker`) and nothing else, so
it respects ``updates.check: false`` (an operator who switched the check off hears nothing) and
shares the check's own cache: the extra cost is zero requests most scans.
"""

from __future__ import annotations

from functools import partial

from noust.core.exceptions import NoustError
from noust.core.notifications.composers_netobs import compose_security_release
from noust.core.update_checker import UpdateChecker, UpdateCheckInProgress
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the release announced and when.
SCOPE = "security_release"
ITEM = "announced"

DEFAULT_CHECK_HOURS = 6.0
DEFAULT_RENOTIFY_HOURS = 72.0


class SecurityReleaseWatch(Watch):
    """Announces a release that fixes a vulnerability above the running version."""

    name = "security_release"
    default_interval_s = DEFAULT_CHECK_HOURS * 3600

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            ``monitor.security_check_hours`` in seconds; zero (off) when it says so or when the
            update check is switched off.
        """
        if not UpdateChecker.enabled():
            return 0.0
        hours = float(services.config.get("monitor.security_check_hours", DEFAULT_CHECK_HOURS))
        return max(hours, 0.0) * 3600

    def check(self, services: WatchServices) -> None:
        """
        Ask the update check and announce a security release not yet announced.

        Args:
            services: What the monitor lends.
        """
        try:
            found = UpdateChecker.check()
        except (UpdateCheckInProgress, NoustError, OSError):
            # Not knowing is not a change: what was announced stays announced.
            return
        if not found.security_update or found.security is None:
            services.state.delete(SCOPE, ITEM)
            return
        renotify = float(
            services.config.get("monitor.security_renotify_hours", DEFAULT_RENOTIFY_HOURS)
        )
        said = services.state.get_json(SCOPE, ITEM)
        now = services.clock()
        if isinstance(said, dict) and said.get("version") == found.security:
            at = float(said.get("at", 0))
            if renotify <= 0 or now - at < renotify * 3600:
                return
        services.state.put_json(SCOPE, ITEM, {"version": found.security, "at": now})
        services.publish(
            partial(
                compose_security_release,
                found.security,
                found.current,
                advisory=found.advisory_url or "",
                update=found.update_command,
            )
        )
