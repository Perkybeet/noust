# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Certificates that stopped renewing (item 172).

certbot renews on a timer of its own, and a renewal that fails (port 80 closed, a rule that
refuses the validation, a record that moved) is silent until the certificate is close to its end;
with certificates of 45 days there is much less time to notice than with 90. This watch reads the
result of the last run (:mod:`noust.managers.cert_renewal`) every few hours and says so while there
is time, with certbot's own words: ``cert_renewal_failed``.

Three rules, those of every watch (:mod:`noust.monitor.watches`):

- **One announcement per episode**, and a reminder after three days if it still does not work. A
  renewal that works again closes the episode, so the next failure is news again.
- **Not knowing is not a change.** A server that renews some other way (no timer, no cron entry of
  Noust's), or whose systemd cannot be asked, leaves what was remembered alone and says nothing.
- **Only what certbot renews.** A server whose certificates are all uploaded has nothing for a timer
  to renew, and nothing to say about one.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from noust.core.notifications.composers_watch import compose_cert_renewal
from noust.core.runner import CommandRunner
from noust.managers.cert_renewal import FAILED, TIMER_STOPPED, RenewalStatus, read_renewal
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the problem last announced and when.
SCOPE = "cert_renewal"
ITEM = "renewal"

#: How often the last run is looked at. The timer runs twice a day.
CHECK_SECONDS = 3 * 3600

#: How long after an announcement a renewal that still does not work is said again.
RENOTIFY_SECONDS = 72 * 3600


def _renewed_names(runner: CommandRunner) -> list[str]:
    """
    Args:
        runner: The one the monitor lends the watch: asking certbot is a process like any other,
            and a watch that built its own would run it past a dry run and past a test's runner.

    Returns:
        The certificates certbot renews: the names of its lineages, not of the certificates that
        were uploaded, which nobody renews. Empty when certbot cannot be asked.
    """
    from noust.core.exceptions import NoustError
    from noust.managers.cert_manager import CertManager

    try:
        listed = CertManager(verbose=False, runner=runner).list_certificates()
    except NoustError:
        return []
    return [cert.name for cert in listed if cert.source != "custom" and cert.name]


class CertRenewalWatch(Watch):
    """Announces that certbot's renewal failed, or is not running."""

    name = "cert_renewal"
    default_interval_s = CHECK_SECONDS

    def __init__(
        self,
        renewal: Callable[[CommandRunner], RenewalStatus] | None = None,
        certificates: Callable[[], list[str]] | None = None,
    ) -> None:
        """
        Args:
            renewal: Reads how renewal is going; :func:`~noust.managers.cert_renewal.read_renewal`
                by default. Tests inject one.
            certificates: Names the certificates certbot renews; by default certbot is asked,
                through the runner the monitor lends. Tests inject one.
        """
        self._renewal = renewal or read_renewal
        self._certificates = certificates

    def check(self, services: WatchServices) -> None:
        """
        Compare what certbot did last with what was already said.

        Args:
            services: What the monitor lends.
        """
        names = (
            self._certificates()
            if self._certificates is not None
            else _renewed_names(services.runner)
        )
        if not names:
            return
        status = self._renewal(services.runner)
        if status.mechanism == "none":
            return
        problem = status.problem
        if problem is None:
            # Renewal works: the episode is over, and the next failure is news again. A run that
            # is not known to have worked (a timer that never fired) leaves things as they are.
            if status.last_ok is True and status.timer_active is not False:
                services.state.delete(SCOPE, ITEM)
            return
        now = services.clock()
        said = services.state.get_json(SCOPE, ITEM)
        if (
            isinstance(said, dict)
            and said.get("problem") == problem
            and now - float(said.get("at", 0)) < RENOTIFY_SECONDS
        ):
            return
        services.state.put_json(SCOPE, ITEM, {"problem": problem, "at": now})
        services.publish(
            partial(
                compose_cert_renewal,
                FAILED if problem == FAILED else TIMER_STOPPED,
                unit=status.unit,
                last_run=status.last_run,
                next_run=status.next_run,
                output=status.output,
                certificates=names,
            )
        )
