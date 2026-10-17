# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The four things the network keeps up on its own, hourly, without anyone asking.

- **DNS records nobody uses.** A record Noust made for a name that lost its last
  application stays a while (an update that removes and adds a name must not delete the
  record in between) and then goes: :meth:`~noust.managers.dns.records.ManagedDns.sweep`.
- **Cloudflare's address ranges.** A site that trusts the visitor address Cloudflare
  forwards (``real_ip: cloudflare``) trusts only Cloudflare's ranges; Cloudflare adds one
  now and then, and a list a month old sends the CDN's new addresses to the application as
  the visitors. Fetched again when it is a week old and something uses it, and every site
  that does is rendered again.
- **The tailnet's certificate.** The certificate Tailscale issued for this server's name
  (:func:`noust.deployers.private_access.tailnet_certificate`) lives ninety days; it is asked for
  again when thirty are left and the sites that serve it reload it.

The monitor runs sandboxed and read-only for ``/etc/nginx``, so it does not do this itself:
it asks whether there is something to do (:func:`work_due`, which reads two tables and a
file) and, when there is, starts :func:`refresh_command`'s transient unit, which runs
:func:`maintain` as root outside the sandbox, once at a time (the unit name is the lock).
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from noust.core import paths
from noust.core.dns_store import DnsStore
from noust.core.exceptions import NoustError
from noust.managers import trusted_proxies
from noust.managers.site_policy import SitePolicyManager

logger = logging.getLogger(__name__)

#: The module the transient unit runs.
MODULE = "noust.managers.network_maintenance"

#: Seconds the monitor waits between asking whether there is something to do.
CHECK_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class Maintenance:
    """
    What a maintenance pass did.

    Attributes:
        released: ``hostname: outcome`` for each DNS record that was swept.
        ranges: What refreshing the ranges came to (see
            :func:`noust.deployers.access.refresh_trusted_ranges`), or None when they
            did not need it.
        renewed: The uploaded certificates that were asked for or signed again (the tailnet's
            and the internal authority's).
        errors: What failed, one sentence each; the other step ran anyway.
    """

    released: dict[str, str] = field(default_factory=dict)
    ranges: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()
    renewed: tuple[str, ...] = ()


def _uses_cloudflare_ranges() -> bool:
    """
    Returns:
        Whether any application's policy trusts Cloudflare's ranges.
    """
    manager = SitePolicyManager()
    return any(
        manager.get(name).real_ip == "cloudflare" for name in manager.records.domains_with_policy()
    )


def work_due(now: datetime | None = None) -> bool:
    """
    Say whether a maintenance pass has something to do.

    Cheap on purpose: the monitor asks every hour on every server, and most servers have
    no DNS accounts and no Cloudflare policy.

    Args:
        now: The current time; the clock by default.

    Returns:
        True when a DNS record is waiting to be swept, the ranges something uses are stale or
        a certificate of the tailnet is about to expire.
    """
    from noust.deployers import internal_certificates, private_access

    try:
        if any(record.unreferenced_since for record in DnsStore().list_records()):
            return True
        if _uses_cloudflare_ranges() and trusted_proxies.is_stale(now):
            return True
        return private_access.renewal_due(now) or internal_certificates.renewal_due()
    except (NoustError, sqlite3.Error, OSError) as exc:
        logger.debug("Could not tell whether network maintenance is due: %s", exc)
        return False


def maintain() -> Maintenance:
    """
    Sweep the DNS records nobody uses and refresh the ranges when they are due.

    Returns:
        What was done. A step that fails is reported and the other still runs.
    """
    from noust.deployers import access, internal_certificates, private_access
    from noust.managers.dns.records import ManagedDns

    errors: list[str] = []
    released: dict[str, str] = {}
    ranges: dict[str, Any] | None = None
    try:
        for result in ManagedDns().sweep():
            released[result.hostname] = result.outcome.value
    except (NoustError, sqlite3.Error) as exc:
        errors.append(f"The DNS sweep failed: {exc}")
    try:
        if _uses_cloudflare_ranges() and trusted_proxies.is_stale():
            ranges = access.refresh_trusted_ranges()
    except (NoustError, sqlite3.Error) as exc:
        errors.append(f"Cloudflare's ranges were not refreshed: {exc}")
    renewed: list[str] = []
    try:
        renewed = private_access.renew_tailnet_certificates()
    except (NoustError, sqlite3.Error) as exc:
        errors.append(f"The tailnet's certificate was not renewed: {exc}")
    try:
        renewed.extend(internal_certificates.renew_due())
    except (NoustError, sqlite3.Error) as exc:
        errors.append(f"The internal authority's certificates were not renewed: {exc}")
    return Maintenance(released, ranges, tuple(errors), tuple(renewed))


def refresh_command(python: str | None = None) -> list[str]:
    """
    The ``systemd-run`` command that runs a maintenance pass in a unit of its own.

    Args:
        python: The interpreter; the one running this code by default.

    Returns:
        The argv.
    """
    argv = [
        "systemd-run",
        f"--unit={paths.NETWORK_MAINTENANCE_UNIT}",
        "--description=Noust network maintenance",
        "--collect",
        "--quiet",
        "--no-block",
        "--property=Nice=10",
    ]
    data_dir = paths.getenv(paths.DATA_DIR_ENV)
    if data_dir:
        argv.append(f"--setenv={paths.DATA_DIR_ENV}={data_dir}")
    return [*argv, "--", python or sys.executable, "-m", MODULE]


def main() -> int:
    """
    Run one maintenance pass; what the transient unit executes.

    Returns:
        0 when every step ran, 1 when one failed (the journal has the sentences).
    """
    result = maintain()
    lines = [f"DNS {hostname}: {outcome}" for hostname, outcome in sorted(result.released.items())]
    if result.ranges is not None:
        lines.append(
            f"Cloudflare ranges: {result.ranges['count']} ({result.ranges['source']}), "
            f"applied to {len(result.ranges['applied'])} site(s)"
        )
    lines.extend(f"Certificate renewed: {name}" for name in result.renewed)
    # The transient unit's output is the journal's: one line per thing done or failed.
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    sys.stderr.write("".join(f"{error}\n" for error in result.errors))
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
