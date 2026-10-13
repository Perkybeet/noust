# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server's own security posture lacks, said once, with the fix.

``noust health`` and ``GET /api/system/health`` already report what is broken.
This reports what is not broken and that an operator who read the security
model (docs/SECURITY-MODEL.md) would want to know about, each as a sentence and
the command that fixes it:

- **Backups that leave unencrypted** (checklist L-25). A backup holds every
  application's ``.env`` and database dumps; a remote destination that is not
  encrypted sends them to somebody else's disk in clear. New remote
  destinations are encrypted unless the operator says otherwise; the ones made
  before are named here.
- **A central whose keys are not sealed** (L-04). A central holds the SSH keys
  and tokens that reach every server it manages. ``noust central seal`` keeps
  them under a passphrase that is never stored; until it is run they are files
  readable by whoever reads the disk.
- **A security release that is out** (L-43). The last update check, read from
  the cache and never from the network, says a release newer than the one
  running fixes a vulnerability.

Findings are warnings. Nothing here is a reason for the verdict to be an error:
a server without them is exposed, not down. The identity and unit-hardening
findings that belong to applications are reported where applications are.
"""

from __future__ import annotations

from dataclasses import dataclass

from noust.core import sealing
from noust.core.secrets import secrets_dir
from noust.core.store import get_store


@dataclass(frozen=True)
class Finding:
    """
    One thing the posture lacks.

    Attributes:
        code: A stable identifier.
        summary: What is wrong, as a sentence.
        fix: The command that resolves it.
    """

    code: str
    summary: str
    fix: str

    def __str__(self) -> str:
        """The warning as ``noust health`` prints it: what, then how to fix it."""
        return f"{self.summary} Fix: {self.fix}"


def _unencrypted_backups() -> list[Finding]:
    """
    Name the remote backup destinations that send backups in clear.

    Returns:
        One finding naming them all, or none.
    """
    open_ones = sorted(
        destination.name
        for destination in get_store().list_backup_destinations()
        if destination.backend != "local" and not destination.encrypted
    )
    if not open_ones:
        return []
    count = len(open_ones)
    return [
        Finding(
            "backups_unencrypted",
            f"{count} remote backup destination{'s' if count != 1 else ''} send backups "
            f"unencrypted ({', '.join(open_ones)}): they hold every application's .env and "
            "database dumps.",
            f"noust backup destination update {open_ones[0]} --encrypt",
        )
    ]


def _unsealed_central() -> list[Finding]:
    """
    Say when the keys that reach other servers are stored unsealed.

    Returns:
        A finding when this Noust manages servers and its secrets are not
        sealed, else none.
    """
    managed = len(get_store().list_nodes())
    if managed == 0 or sealing.is_sealed(secrets_dir()):
        return []
    return [
        Finding(
            "central_unsealed",
            f"The SSH keys and tokens that reach {managed} server{'s' if managed != 1 else ''} "
            "are stored unsealed: whoever reads this disk can use them.",
            "noust central seal",
        )
    ]


def _security_update() -> list[Finding]:
    """
    Say when the last update check found a security release above this one.

    Returns:
        A finding when it did, else none; the network is never asked.
    """
    from noust.core.update_checker import UpdateChecker

    if not UpdateChecker.enabled():
        return []
    check = UpdateChecker.last_known()
    if check is None or not check.security_update:
        return []
    where = f" ({check.advisory})" if check.advisory else ""
    return [
        Finding(
            "security_update",
            f"Noust {check.security} fixes a vulnerability{where} and this server runs "
            f"{check.current}.",
            check.update_command,
        )
    ]


def collect() -> list[Finding]:
    """
    List what this server's security posture lacks.

    Returns:
        The findings, most urgent first: a security release, then the keys,
        then the backups.

    Raises:
        NoustError: The store cannot be read.
    """
    return [*_security_update(), *_unsealed_central(), *_unencrypted_backups()]
