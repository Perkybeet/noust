# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Applications the kernel killed for running out of memory (item 175).

The kernel counts the processes it kills for lack of memory by cgroup: ``oom_kill`` in the
``memory.events`` of an application's unit (:mod:`noust.core.cgroup_events`). This watch keeps that
count for each application and says ``app_oom_killed`` once for every rise, with the limit and the
most the application used, so the operator hears of it when it happens and not when a customer
does. The diagnosis says the same of an application that is down now (``noust diagnose``).

The rules are those of every watch (:mod:`noust.monitor.watches`):

- **One announcement per rise.** A count that did not move is not news, and the first look at an
  application only remembers what it finds: kills from before the monitor looked are not news now.
- **A unit that restarted counts again from zero.** systemd makes its cgroup again, so the count
  falls, or lands on the same number: the unit's start time, remembered with the count, says which.
- **Not knowing is not a change.** A cgroup whose ``memory.events`` cannot be read (cgroup v1, no
  memory controller) leaves what was remembered alone.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from noust.core import cgroup_events
from noust.core.notifications.composers_watch import compose_app_oom
from noust.monitor.plan import PlanBuilder, SamplingPlan
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: each application's count and when its unit started.
SCOPE = "oom"

#: How often the counters are read. They are a few bytes each, once for every application.
CHECK_SECONDS = 60


def _plans(services: WatchServices) -> dict[str, SamplingPlan]:
    """
    Args:
        services: What the monitor lends.

    Returns:
        The plan of every application that is not a preview, as the metrics collector plans them
        (systemd is asked once, for every unit): where each one's cgroup is.
    """
    store = services.state.store
    apps = [app for app in store.list_apps() if not getattr(app, "preview_parent", None)]
    return PlanBuilder(runner=services.runner).build(apps)


class OomWatch(Watch):
    """Announces that the kernel killed an application for running out of memory."""

    name = "oom"
    default_interval_s = CHECK_SECONDS

    def __init__(
        self, plans: Callable[[WatchServices], dict[str, SamplingPlan]] | None = None
    ) -> None:
        """
        Args:
            plans: Where each application's cgroup is; the metrics plans by default. Tests inject
                a tree of their own.
        """
        self._plans = plans or _plans

    def check(self, services: WatchServices) -> None:
        """
        Compare each application's count with the one remembered.

        Args:
            services: What the monitor lends.
        """
        watched: set[str] = set()
        for domain, plan in self._plans(services).items():
            if not plan.measures_resources:
                continue
            facts = cgroup_events.combine(
                cgroup_events.read_facts(target.cgroup)
                for target in plan.targets
                if target.cgroup is not None
            )
            if not facts.readable:
                # Not knowing is not a change: keep what was remembered, and keep watching it.
                watched.add(domain)
                continue
            watched.add(domain)
            started = ";".join(
                target.properties.get("ActiveEnterTimestamp", "") for target in plan.targets
            )
            before = services.state.get_json(SCOPE, domain)
            services.state.put_json(SCOPE, domain, {"count": facts.kills, "started": started})
            if not isinstance(before, dict):
                continue
            same_cgroup = before.get("started") == started
            rise = (
                facts.kills - int(before.get("count", 0))
                if same_cgroup and facts.kills >= int(before.get("count", 0))
                else facts.kills
            )
            if rise > 0:
                services.publish(
                    partial(
                        compose_app_oom,
                        domain,
                        count=rise,
                        limit=facts.limit,
                        peak=facts.peak,
                        since=started.split(";")[0],
                    )
                )
        services.state.keep_only(SCOPE, watched)
