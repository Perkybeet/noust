# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central: a Noust whose job is to manage other servers.

Every Noust can manage a fleet; what this package adds is the *role* that
says whether it also deploys applications itself (``central.role``):

- ``server`` (the default): the Noust every release has been. It deploys and
  serves applications, and may also manage other servers.
- ``hub``: a central with no local deployments - the container on a NAS, or a
  small VPS whose only job is the fleet. Applications, sites, certificates,
  databases and the rest of what needs nginx and systemd *here* are refused
  with a message instead of failing half-way on a missing nginx; the
  console hides them. What a hub does keep is the GitHub App (``noust github``):
  it deploys nothing itself, but it receives the pushes of the repositories its
  servers deploy and hands them on, and leases those servers the access to clone.

:func:`role` is the one reading of the setting. What a Noust can do is one
table (:mod:`noust.central.features`): :func:`require_server_role` is the guard
a function calls where it starts, by its key in that table, and
:func:`central_info` is what the API hands the console (``central`` in the
session payload): the role, whether to show the unlock form while the secrets
are sealed and locked, and for each function whether it works here and why
not, so the console offers what works and nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from noust.central import features
from noust.core.config import Config
from noust.core.exceptions import ConfigError, NoustError

if TYPE_CHECKING:
    import click

#: A Noust that deploys applications here.
ROLE_SERVER = "server"
#: A central with no local deployments.
ROLE_HUB = "hub"
#: Every role, the default first.
ROLES: tuple[str, ...] = (ROLE_SERVER, ROLE_HUB)

#: CLI commands a hub refuses, and the function of :mod:`noust.central.features` each belongs to:
#: the refusal names it as that function's label does. Everything not listed (the console, tokens,
#: sessions, two-factor, configuration, notifications, the fleet and the central itself) works the
#: same on both roles.
HUB_REFUSED_COMMANDS: dict[str, str] = {
    **dict.fromkeys(
        ("create", "delete", "update", "start", "stop", "restart", "status", "logs", "list"),
        "applications",
    ),
    "app": "applications",
    "releases": "releases",
    "preview": "preview_environments",
    "recipe": "recipes",
    "import": "importing_applications",
    "env": "application_environments",
    "site": "sites",
    "console": "console_exposure",
    "domain": "domains",
    "cert": "certificates",
    "db": "databases",
    "backup": "application_backups",
    "rollback": "rollbacks",
    "cron": "scheduled_jobs",
    "service": "services",
    "monitor": "process_monitor",
    "diagnose": "application_diagnosis",
    "health": "server_health",
    "setup": "server_setup",
}


class RoleError(NoustError):
    """A feature was asked of a Noust whose role does not have it."""


def role(config: Config | None = None) -> str:
    """
    Return this Noust's role.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        ``"server"`` or ``"hub"``.

    Raises:
        ConfigError: When ``central.role`` (or ``NOUST_CENTRAL_ROLE``, which
            overrides it) holds anything else: guessing a role would either
            refuse everything or let a hub try to run nginx.
    """
    value = str((config or Config()).get("central.role", ROLE_SERVER) or ROLE_SERVER)
    normalised = value.strip().lower()
    if normalised not in ROLES:
        raise ConfigError(
            f"central.role must be one of {', '.join(ROLES)}, not {value!r}",
            details=(
                "Fix it with 'noust config set central.role server' (or hub), or correct "
                "NOUST_CENTRAL_ROLE in the environment."
            ),
        )
    return normalised


def is_hub(config: Config | None = None) -> bool:
    """
    Report whether this Noust is a hub.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        True when ``central.role`` is ``hub``.
    """
    return role(config) == ROLE_HUB


def require_server_role(feature: str, config: Config | None = None) -> None:
    """
    Refuse a function that needs the server role on a hub.

    Args:
        feature: The function's key in :data:`noust.central.features.FEATURES`
            (``"native_deploy"``). What the refusal calls it is the label the
            table gives it.
        config: The configuration; the process-wide one by default.

    Raises:
        RoleError: When the function needs the server role and this Noust is a
            hub.
        KeyError: When the table has no such key, on every machine and not only
            on a hub.
    """
    needed = features.feature(feature)
    if features.Need.ROLE not in needed.needs or not is_hub(config):
        return
    refusal = features.hub_refusal(needed)
    raise RoleError(refusal.text, details=refusal.hint or "")


def refuse_command_on_hub(name: str, ctx: click.Context | None = None) -> None:
    """
    Refuse a CLI command a hub does not have.

    Called by the root command group when it resolves a subcommand, so every
    command is covered by one check rather than by one per command.

    Args:
        name: The command's canonical name.
        ctx: The root context. A command aimed at another server (a ``node``
            parameter, when the fleet adds one) runs there, not here, and is
            not refused.

    Raises:
        RoleError: When this Noust is a hub and the command deploys locally.
    """
    feature = HUB_REFUSED_COMMANDS.get(name)
    if feature is None:
        return
    if ctx is not None and ctx.params.get("node"):
        return
    require_server_role(feature)


def feature_states(config: Config | None = None) -> dict[str, dict[str, Any]]:
    """
    Say what each function of the table can do on this Noust and this machine.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        :func:`noust.central.features.published` over this role and what the machine has
        (:func:`noust.central.machine.read_machine_facts`, which asks the machine itself).

    Raises:
        ConfigError: When ``central.role`` holds something that is not a role.
    """
    from noust.central import machine

    return features.published(role=role(config), facts=machine.read_machine_facts())


def central_info(config: Config | None = None, *, with_features: bool = True) -> dict[str, Any]:
    """
    Describe the central for the console: its role, its secrets' state and what it can do.

    This is the payload the API exposes as ``central`` (in the session the
    console reads after sign-in), so the console can offer only what works here
    and show the unlock form while the secrets are sealed and locked.

    Args:
        config: The configuration; the process-wide one by default.
        with_features: Say what each function can do here, which looks at the
            machine (cached; see :mod:`noust.central.machine`). Unlocking a
            sealed central asks for the state alone.

    Returns:
        ``{"role": "server" | "hub", "sealed": bool, "locked": bool, "features":
        {key: {"available": bool, "reason": {...} | None}}}``; ``locked`` is
        True only while the store is sealed and this process has not been given
        the passphrase, and ``features`` (left out without ``with_features``) is
        :func:`noust.central.features.published` over this role and this machine.
    """
    from noust.core import sealing
    from noust.core.secrets import secrets_dir

    root = secrets_dir()
    sealed = sealing.is_sealed(root)
    this_role = role(config)
    info: dict[str, Any] = {
        "role": this_role,
        "sealed": sealed,
        "locked": sealed and not sealing.is_unlocked(root),
    }
    if with_features:
        info["features"] = feature_states(config)
    return info
