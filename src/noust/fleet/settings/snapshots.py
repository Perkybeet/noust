# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node holds of a setting, read from its own managers, to fingerprint it.

A node does not keep the value the central pushed: the setting lives where its own managers keep
it, and it can change there by its operator's hand. What the node keeps is a fingerprint of what
it held right after the push (:mod:`noust.fleet.settings.managed`), and this is how it looks again
later: each reader returns the setting as the node stores it, credentials included (they take part
in the fingerprint and go nowhere else), or None when there is no such setting.

A reader returns what the node's route *accepts*, not bookkeeping the node changes by itself
(when it was last tested, when it was updated): the fingerprint must change when an operator
changes the setting and only then.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from noust.core.secrets import SecretStore
from noust.core.store import NoustStore

__all__ = ["READERS", "take"]

Reader = Callable[[str, NoustStore | None, SecretStore | None], dict[str, Any] | None]


def _channel(
    name: str, store: NoustStore | None, secrets: SecretStore | None
) -> dict[str, Any] | None:
    """
    Args:
        name: A channel.
        store: Unused: a channel lives in the configuration.
        secrets: Unused.

    Returns:
        Its block of the configuration as stored, credentials opened; None when there is none.
    """
    from noust.core.config import Config

    config = Config()
    config.reload()
    found = config.get(f"notifications.channels.{name}")
    return dict(found) if isinstance(found, Mapping) else None


def _alert_rule(
    name: str, store: NoustStore | None, secrets: SecretStore | None
) -> dict[str, Any] | None:
    """
    Args:
        name: A rule.
        store: The node's store.
        secrets: Unused.

    Returns:
        The rule without the figures the monitor keeps; None when there is none.
    """
    from noust.core.alerts import AlertRules

    rule = AlertRules(store).get(name)
    if rule is None:
        return None
    data = rule.to_dict()
    for key in ("id", "created_at", "updated_at"):
        data.pop(key, None)
    return data


def _backup_destination(
    name: str, store: NoustStore | None, secrets: SecretStore | None
) -> dict[str, Any] | None:
    """
    Args:
        name: A destination.
        store: The node's store.
        secrets: Where its credentials are.

    Returns:
        Its record and credentials without when it was made or changed; None when there is none.
    """
    from noust.managers.backup_destinations import BackupDestinationManager, _secret_namespace

    manager = BackupDestinationManager(secrets=secrets, store=store)
    record = manager.get(name)
    if record is None:
        return None
    return {
        "backend": record.backend,
        "encrypted": record.encrypted,
        "settings": dict(record.settings),
        "credentials": manager.secrets.read_protected_json(_secret_namespace(name)),
    }


def _registry(
    name: str, store: NoustStore | None, secrets: SecretStore | None
) -> dict[str, Any] | None:
    """
    Args:
        name: A login.
        store: The node's store.
        secrets: Where its password is.

    Returns:
        Its kind, host, user and password, without the figures of its last test; None when there
        is none.
    """
    from noust.core.registry_store import RegistryStore, secret_name_for

    login = RegistryStore(store).get(name)
    if login is None:
        return None
    return {
        "kind": login.kind,
        "server": login.server,
        "username": login.username,
        "password": (secrets or SecretStore()).read(secret_name_for(name)),
    }


def _variable(
    name: str, store: NoustStore | None, secrets: SecretStore | None
) -> dict[str, Any] | None:
    """
    Args:
        name: A shared variable of the server.
        store: The node's store.
        secrets: Unused.

    Returns:
        Its value, mark and audience; None when there is none.
    """
    from noust.core.variables.model import SERVER
    from noust.core.variables.store import VariableStore

    variable = VariableStore(store).get(SERVER, 0, name)
    if variable is None:
        return None
    return {"value": variable.value, "secret": variable.secret, "audience": variable.audience}


def _console_link(
    name: str, store: NoustStore | None, secrets: SecretStore | None
) -> dict[str, Any] | None:
    """
    Args:
        name: The console link.
        store: Unused: the address lives in the configuration.
        secrets: Unused.

    Returns:
        The address the notification links open at, as stored; None when none is set.
    """
    from noust.core.config import Config

    config = Config()
    config.reload()
    url = config.get("notifications.console_url")
    return {"url": str(url)} if url else None


def _policy(
    name: str, store: NoustStore | None, secrets: SecretStore | None
) -> dict[str, Any] | None:
    """
    Args:
        name: The policy.
        store: Unused: the four fields live in the configuration.
        secrets: Unused.

    Returns:
        The four fields of the policy and nothing else of the configuration (the folders and the
        web server beside them are the machine's, and changing one must not make the policy the
        operator's own); None for a name that is not the one policy.
    """
    from noust.core.config import Config
    from noust.fleet.settings import policy

    if name != policy.NAME:
        return None
    config = Config()
    config.reload()
    return policy.read(config)


#: How a node reads each kind of setting.
READERS: dict[str, Reader] = {
    "channel": _channel,
    "alert_rule": _alert_rule,
    "backup_destination": _backup_destination,
    "registry": _registry,
    "variable": _variable,
    "console_link": _console_link,
    "policy": _policy,
}


def take(
    resource: str,
    name: str,
    *,
    store: NoustStore | None = None,
    secrets: SecretStore | None = None,
) -> dict[str, Any] | None:
    """
    Read what this node holds of a setting.

    Args:
        resource: The kind.
        name: The setting.
        store: The node's store; the process-wide one by default.
        secrets: Its secret store; the process-wide one by default.

    Returns:
        The setting as the node stores it; None when it holds none of that name.

    Raises:
        KeyError: For a kind the node has no reader for (a kind newer than this node).
    """
    return READERS[resource](name, store, secrets)
