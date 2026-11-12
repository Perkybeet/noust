# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The policy of a server: the four settings of "General" that say how it behaves, as one setting of the fleet.

Whether a server asks for new releases, the address its certificate notices go to, how many
backups it keeps of each application and how long a deployment may build are not what the machine
is but how it is run, so a central may set them for every server and give one an exception. They
are one setting called ``general`` because they are one form and one route on the server
(``PUT /api/config/policy``): the three typed routes of the old form write fields of the machine
beside the one that is policy (the backup folder, the builds that run at once), and the first of
them has no typed route at all, so a push of this setting never goes through a loose
``PATCH /api/config``, which a push is not allowed to use.

This is the one place that knows which configuration key each field is. The server's route reads
and writes through it, and so does the reader a node fingerprints its setting with
(:mod:`noust.fleet.settings.snapshots`), so what the node says it holds is exactly what the route
shows. A server whose operator never touched any of the four is not "customised": the first push
may take it without asking, which is what keeps a central from overwriting a choice somebody made.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.delivery import DEFAULT_MAX_MINUTES, DeliverySettings
from noust.core.exceptions import NoustError, ValidationError

__all__ = ["FIELDS", "KEYS", "NAME", "defaults", "is_customized", "read", "write"]

#: The one name of the policy: there is one for a server.
NAME = "general"

#: Each field of the policy and the configuration key that holds it.
KEYS: dict[str, str] = {
    "updates_check": "updates.check",
    "ssl_email": "ssl.email",
    "backup_max_per_app": "backup.max_per_app",
    "delivery_max_minutes": "deploy.max_minutes",
}

#: The fields, in the order a form shows them.
FIELDS: tuple[str, ...] = tuple(KEYS)


def defaults() -> dict[str, Any]:
    """
    Returns:
        What a server does of each field when nobody chose anything.
    """
    from noust.managers.backup_manager import BackupManager

    return {
        "updates_check": bool(DEFAULT_CONFIG["updates"]["check"]),
        "ssl_email": str(DEFAULT_CONFIG["ssl"]["email"]),
        "backup_max_per_app": BackupManager.DEFAULT_MAX_BACKUPS,
        "delivery_max_minutes": DEFAULT_MAX_MINUTES,
    }


def read(config: Config) -> dict[str, Any]:
    """
    Read the policy the way the server itself reads each key.

    A value that does not hold (a hand-edited file) is read as the default: the key's own
    validator is what refuses it when it is written, and a server must not stop for it.

    Args:
        config: The server's configuration, as it stands on disk.

    Returns:
        The four fields.
    """
    from noust.managers.backup_manager import BackupManager

    try:
        kept = BackupManager.configured_max_backups(config)
    except (TypeError, ValueError):
        kept = BackupManager.DEFAULT_MAX_BACKUPS
    return {
        "updates_check": bool(config.get(KEYS["updates_check"], True)),
        "ssl_email": str(config.get(KEYS["ssl_email"], "") or ""),
        "backup_max_per_app": kept,
        "delivery_max_minutes": DeliverySettings.read(config).max_minutes,
    }


def is_customized(values: Mapping[str, Any]) -> bool:
    """
    Args:
        values: A server's policy: :func:`read`.

    Returns:
        Whether any field is something other than what the server does of itself, which is what
        tells a server whose operator chose from one that never did.
    """
    return any(values.get(field) != value for field, value in defaults().items())


def write(config: Config, values: Mapping[str, Any]) -> None:
    """
    Set the four keys, each through the rule it has.

    The caller writes the file. A value one of the keys refuses leaves nothing set: the
    configuration is read again from its file, so an earlier field of the same request is not
    left in memory for whoever reads next.

    Args:
        config: The server's configuration.
        values: The four fields.

    Raises:
        ValidationError: A key refuses its value; it says the key and the range in the key's own
            words and names the field of the policy.
    """
    for field, key in KEYS.items():
        try:
            config.set(key, values[field])
        except NoustError as exc:
            config.reload()
            raise ValidationError(exc.message, exc.details, field=field) from exc
