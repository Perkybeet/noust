# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Managers for Noust."""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from noust.managers.apache_manager import ApacheManager
    from noust.managers.backup_manager import BackupError, BackupManager
    from noust.managers.base_manager import BaseManager
    from noust.managers.cert_manager import CertManager
    from noust.managers.cron_manager import CronJob, CronManager
    from noust.managers.nginx_manager import NginxManager
    from noust.managers.rollback_manager import RollbackManager
    from noust.managers.service_manager import ServiceManager
    from noust.managers.source_manager import SourceManager

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "ApacheManager": "noust.managers.apache_manager",
        "BackupError": "noust.managers.backup_manager",
        "BackupManager": "noust.managers.backup_manager",
        "RollbackManager": "noust.managers.rollback_manager",
        "BaseManager": "noust.managers.base_manager",
        "CertManager": "noust.managers.cert_manager",
        "CronJob": "noust.managers.cron_manager",
        "CronManager": "noust.managers.cron_manager",
        "NginxManager": "noust.managers.nginx_manager",
        "ServiceManager": "noust.managers.service_manager",
        "SourceManager": "noust.managers.source_manager",
    },
)

__all__ = [
    "ApacheManager",
    "BackupError",
    "BackupManager",
    "BaseManager",
    "CertManager",
    "CronJob",
    "CronManager",
    "NginxManager",
    "RollbackManager",
    "ServiceManager",
    "SourceManager",
]
