# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One package manager backend per distribution family, behind one interface.

:func:`backend_for` is the only place that maps a detected platform to a
backend, which is what keeps "which package manager is this" decided once.
"""

from __future__ import annotations

from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner
from noust.managers.server.host import HostPaths, PackageFamily, Platform
from noust.managers.server.pkg.apt import AptBackend
from noust.managers.server.pkg.base import (
    AutoUpdates,
    PackageBackend,
    PackageUpdate,
    PendingUpdates,
    RebootStatus,
    RestartProbe,
    UpdateScope,
)
from noust.managers.server.pkg.dnf import DnfBackend
from noust.managers.server.pkg.unsupported import UnsupportedBackend
from noust.managers.server.pkg.zypper import ZypperBackend


def backend_for(
    platform: Platform,
    *,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    host: HostPaths | None = None,
) -> PackageBackend:
    """
    Pick the backend for a platform.

    Args:
        platform: What the machine is.
        runner: Command runner; the process-wide one when omitted.
        fs: Filesystem seam; the process-wide one when omitted.
        host: Where the system files are.

    Returns:
        The backend for its package manager, or the one that only reports when
        updates are not managed here.
    """
    if platform.transactional or platform.family is PackageFamily.NONE:
        return UnsupportedBackend(platform, runner=runner, fs=fs, host=host)
    if platform.family is PackageFamily.APT:
        return AptBackend(platform, runner=runner, fs=fs, host=host)
    if platform.family is PackageFamily.DNF:
        return DnfBackend(platform, runner=runner, fs=fs, host=host)
    return ZypperBackend(platform, runner=runner, fs=fs, host=host)


__all__ = [
    "AutoUpdates",
    "PackageBackend",
    "PackageUpdate",
    "PendingUpdates",
    "RebootStatus",
    "RestartProbe",
    "UpdateScope",
    "backend_for",
]
