# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The machine the runner answers for: its units, programs and journal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# ---------------------------------------------------------------------------
# The machine the runner answers for
# ---------------------------------------------------------------------------


@dataclass
class Unit:
    """
    One systemd unit of the modelled machine.

    Attributes:
        active: ``active``, ``inactive`` or ``failed``.
        enabled: Whether it starts at boot.
        pid: Main PID while active.
        since: When it last changed state.
        restarts: How many times systemd restarted it.
        managed: Whether it is one of Noust's units (listed by ``list-units``).
    """

    active: str
    enabled: bool = True
    pid: int = 0
    since: datetime = field(default_factory=datetime.now)
    restarts: int = 0
    managed: bool = True

    @property
    def sub(self) -> str:
        """The sub-state systemd reports beside the active state."""
        return {"active": "running", "failed": "failed"}.get(self.active, "dead")


def _foreign_unit_lines(units: dict[str, Unit], patterns: list[str]) -> list[str]:
    """
    Extra ``systemctl list-units`` lines for the machine's non-Noust units.

    ``_list_units`` only walks units Noust would create (it skips
    ``unit.managed is False`` entries): the model's postgresql/mysql/redis-server/nginx
    exist for direct queries such as ``is-active nginx``, not for a full listing. The
    show-all-units toggle (``GET /api/services?noust_only=false``) asks
    ``ServiceManager.list_services(all_services=True)``, which lists with
    ``patterns=["*"]`` and does its own managed/foreign split in Python, so it needs those
    units to appear here too - this is the console's only foreign, always-visible unit for
    that flow.

    Args:
        units: The modelled machine's units.
        patterns: The patterns ``systemctl list-units`` was called with.

    Returns:
        One ``systemctl list-units`` line per foreign unit, when ``patterns`` is the
        unrestricted ``["*"]`` an all-units listing sends; empty otherwise, so a scoped
        listing (``noust-*`` and friends) is unaffected.
    """
    if patterns != ["*"]:
        return []
    return [
        f"{name}.service loaded {unit.active} {unit.sub} {name}"
        for name, unit in sorted(units.items())
        if not unit.managed
    ]


def _git_commit_subject_line(args: tuple[str, ...]) -> str | None:
    """
    Fake ``git log -1 --format=%s``, which the deploy pipeline runs to record
    a deployment's ``commit_message``.

    The modelled machine never really clones anything - every seeded app's
    checkout already exists on disk without a real ``.git`` history - so a
    fixed sentence is enough to answer the one invocation that matters:
    demoing that a deployment's history carries a commit subject at all.
    Nothing here reads it back for content.

    Args:
        args: The full argv the runner was asked to execute.

    Returns:
        The fake subject line, or None when ``args`` is not this exact call.
    """
    if args[:4] == ("git", "log", "-1", "--format=%s"):
        return "Seed data for the console demo"
    return None


#: Programs the modelled machine has on PATH.
INSTALLED_PROGRAMS = (
    "systemctl",
    "systemd-analyze",
    "journalctl",
    "nginx",
    "certbot",
    "openssl",
    "git",
    "node",
    "npm",
    "psql",
    "pg_dump",
    # A dump's check (pg_restore --list), answered from the model below.
    "pg_restore",
    "mysql",
    "mysqldump",
    "redis-cli",
    "tar",
    # Backup destinations copy over rclone; the model answers it from a directory per remote.
    "rclone",
    # The fleet's own key pair (NodeKeys.ensure_keypair), faked in ConsoleRunner
    # below: this sandbox never opens a real SSH connection with it.
    "ssh-keygen",
)

#: What each database client prints for ``--version``.
CLIENT_VERSIONS = {
    "psql": "psql (PostgreSQL) 16.4 (Ubuntu 16.4-0ubuntu0.24.04.2)\n",
    "mysql": "mysql  Ver 8.0.39-0ubuntu0.24.04.2 for Linux on x86_64 ((Ubuntu))\n",
    "redis-cli": "redis-cli 7.0.15\n",
}

#: What a Next.js app writes to the journal, for the logs views.
JOURNAL_LINES = (
    "Started {unit}.service - {domain}.",
    "   ▲ Next.js 15.2.4",
    "   - Local:        http://localhost:{port}",
    " ✓ Starting...",
    " ✓ Ready in 412ms",
    "GET / 200 in 38ms",
    "GET /api/health 200 in 3ms",
    "GET /_next/static/chunks/main-app.js 200 in 2ms",
)

#: What a crashing one writes before systemd gives up on it.
FAILED_JOURNAL_LINES = (
    "Started {unit}.service - {domain}.",
    "Error: Cannot find module '/var/www/apps/{domain}/current/.next/standalone/server.js'",
    "    at Module._resolveFilename (node:internal/modules/cjs/loader:1225:15)",
    "{unit}.service: Main process exited, code=exited, status=1/FAILURE",
    "{unit}.service: Failed with result 'exit-code'.",
    "{unit}.service: Scheduled restart job, restart counter is at 5.",
    "{unit}.service: Start request repeated too quickly.",
)
