# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Domains whose registration is about to lapse (item 173).

The registration of a domain expires, and an expired one takes down every site under it and the
certificates with it. With ``monitor.domain_expiry`` on (off by default: the question goes to the
registry of the domain, a third party, and ``docs/PRIVACY.md`` says exactly what) this watch asks,
once a day for each registrable domain Noust serves (:class:`noust.managers.domain_registration.
DomainRegistrations`), and says ``domain_expiring`` at 30 days and at 7 days and ``domain_expired``
when it has, once each. A registration that is renewed and comes close again is news again.

The rules are those of every watch (:mod:`noust.monitor.watches`): one announcement per stage, and
not knowing is not a change: a registry that does not publish the expiry, or could not be asked, is
not a registration about to lapse.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any

from noust.core import rdap
from noust.core.domain_registrations import OK, Registration
from noust.core.notifications.composers_watch import compose_domain_registration
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the stage last announced for each domain.
SCOPE = "domain_expiry"

#: How often the watch looks. The manager asks a registry at most once a day for each domain.
CHECK_SECONDS = 6 * 3600

#: The stages a registration goes through, in order.
STAGES = ("30", "7", "expired")

#: Days before the end at which each stage begins.
_WARNING_DAYS = 30
_URGENT_DAYS = 7


def stage_of(days_left: int) -> str | None:
    """
    Say where a registration is on its way to expiring.

    Args:
        days_left: Whole days until it expires; negative once it has.

    Returns:
        ``30``, ``7`` or ``expired``; None while it is far from the end.
    """
    if days_left < 0:
        return "expired"
    if days_left <= _URGENT_DAYS:
        return "7"
    return "30" if days_left <= _WARNING_DAYS else None


def _served_names(services: WatchServices) -> list[str]:
    """
    Args:
        services: What the monitor lends.

    Returns:
        Every name Noust serves (:func:`noust.managers.domain_registration.served_names`).
    """
    from noust.managers.domain_registration import served_names

    return served_names(services.state.store)


def _manager(services: WatchServices) -> Any:
    """
    Args:
        services: What the monitor lends.

    Returns:
        The one that asks registries and keeps what they say.
    """
    from noust.managers.domain_registration import DomainRegistrations

    return DomainRegistrations(services.state.store, clock=lambda: _now(services))


def _now(services: WatchServices) -> datetime:
    """
    Args:
        services: What the monitor lends.

    Returns:
        The monitor's clock as an aware moment.
    """
    return datetime.fromtimestamp(services.clock(), tz=timezone.utc)


class DomainExpiryWatch(Watch):
    """Announces that the registration of a domain is about to lapse, or has."""

    name = "domain_expiry"
    default_interval_s = CHECK_SECONDS

    def __init__(
        self,
        manager: Callable[[WatchServices], Any] | None = None,
        names: Callable[[WatchServices], list[str]] | None = None,
    ) -> None:
        """
        Args:
            manager: Builds what asks registries and keeps the answers; the real one by default.
                Tests inject one that asks nobody.
            names: Lists the names Noust serves; the applications' by default.
        """
        self._manager = manager or _manager
        self._names = names or _served_names

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            Every few hours when ``monitor.domain_expiry`` is on; zero (off) otherwise, so that
            nothing leaves the server unless the operator asked.
        """
        on = services.config.get("monitor.domain_expiry", False)
        return CHECK_SECONDS if on is True or str(on).lower() in ("true", "yes", "on", "1") else 0

    def check(self, services: WatchServices) -> None:
        """
        Look at each domain Noust serves and say what is about to lapse.

        Args:
            services: What the monitor lends.
        """
        served = self._names(services)
        manager = self._manager(services)
        registrations = manager.check_names(served)
        manager.forget_unserved(served)
        now = _now(services)
        watched: set[str] = set()
        for registration in registrations:
            # Still watched when it cannot be read this time: not knowing is not a change.
            watched.add(registration.domain)
            days_left = registration.days_left(now) if registration.status == OK else None
            if days_left is not None:
                self._announce(services, manager, registration, days_left)
        services.state.keep_only(SCOPE, watched)

    @staticmethod
    def _announce(
        services: WatchServices, manager: Any, registration: Registration, days_left: int
    ) -> None:
        """
        Say it when the registration reached a stage not announced yet.

        Args:
            services: What the monitor lends.
            manager: What keeps the answers, for the names that belong to the domain.
            registration: What the registry said.
            days_left: Whole days until it expires.
        """
        stage = stage_of(days_left)
        said = services.state.get(SCOPE, registration.domain)
        if stage is None:
            # Far from the end again: a renewal. The next time it comes close is news.
            if said is not None:
                services.state.delete(SCOPE, registration.domain)
            return
        if said in STAGES and STAGES.index(said) >= STAGES.index(stage):
            return
        services.state.put(SCOPE, registration.domain, stage)
        services.publish(
            partial(
                compose_domain_registration,
                registration.domain,
                (registration.expires_at or "")[:10],
                days_left,
                names=manager.records.names_of(registration.domain),
                registry=rdap.registry_name(registration.rdap_base or ""),
            )
        )
