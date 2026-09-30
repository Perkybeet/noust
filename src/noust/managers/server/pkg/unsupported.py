# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The backend for a system whose packages Noust does not change.

pacman and apk are out of scope, and a transactional system (MicroOS, an
image-based Fedora) installs packages through a transaction and a reboot that
Noust does not drive. On all of them the page still says what it can - whether a
reboot is due, what the system is - and every action answers with the reason it
is not offered, instead of an error from a tool that is not there.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from noust.managers.server.errors import UnsupportedHostError
from noust.managers.server.host import natural_key, newest_installed_kernel, running_kernel
from noust.managers.server.pkg.base import (
    COMMON_ENV,
    AutoUpdates,
    PackageBackend,
    PendingUpdates,
    RebootStatus,
    RestartProbe,
    UpdateScope,
    iso_from_mtime,
)


class UnsupportedBackend(PackageBackend):
    """Reports state, refuses actions, and says why."""

    name = "none"

    def env(self) -> dict[str, str]:
        return dict(COMMON_ENV)

    def _refuse(self) -> UnsupportedHostError:
        """
        Build the refusal every action shares.

        Returns:
            The error to raise.
        """
        return UnsupportedHostError(
            "Updates cannot be managed on this system",
            self.platform.why_updates_unsupported(),
        )

    def list_updates(self) -> PendingUpdates:
        return PendingUpdates(security_scope=False, notes=[self.platform.why_updates_unsupported()])

    def removals(self, scope: UpdateScope, *, full: bool) -> list[str]:
        return []

    def refresh_argv(self) -> list[str]:
        raise self._refuse()

    def refresh_timeout(self) -> int:
        raise self._refuse()

    def upgrade_argv(self, scope: UpdateScope, packages: Sequence[str], *, full: bool) -> list[str]:
        raise self._refuse()

    def install_argv(self, packages: Sequence[str]) -> list[str]:
        raise self._refuse()

    def repair_commands(self) -> list[list[str]]:
        return []

    def clean_cache_argv(self) -> list[str] | None:
        return None

    def restart_probe(self) -> RestartProbe:
        for flag in (self.host.reboot_required, self.host.zypper_reboot_needed):
            if flag.exists():
                return RestartProbe(
                    reboot=RebootStatus(
                        required=True,
                        reasons=("The system left a reboot-needed flag",),
                        since=iso_from_mtime(flag),
                        source=str(flag),
                    ),
                    available=False,
                )
        newest = newest_installed_kernel(self.host)
        running = running_kernel()
        if newest and newest != running and natural_key(newest) > natural_key(running):
            return RestartProbe(
                reboot=RebootStatus(
                    required=True,
                    reasons=(f"The running kernel is {running}; {newest} is installed",),
                    source="kernel",
                ),
                available=False,
            )
        return RestartProbe(available=False)

    def auto_status(self) -> AutoUpdates:
        return AutoUpdates(mechanism="none", detail=self.platform.why_updates_unsupported())

    def set_auto(
        self, enabled: bool, security_only: bool, on_line: Callable[[str], None]
    ) -> list[str]:
        raise self._refuse()
