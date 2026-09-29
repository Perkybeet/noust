# Copyright (c) 2024-2026 Yago Lopez Prado
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
  console hides them.

:func:`role` is the one reading of the setting. :func:`require_server_role`
is the guard a feature calls where it starts, and :func:`central_info` is
what the API hands the console (``central`` in the session payload) so it
can hide what a hub does not do and show the unlock form while the secrets
are sealed and locked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

#: CLI commands a hub refuses, and the feature each belongs to, as the
#: refusal names it. Everything not listed (the console, tokens, sessions,
#: two-factor, configuration, notifications, the fleet and the central
#: itself) works the same on both roles.
HUB_REFUSED_COMMANDS: dict[str, str] = {
    **dict.fromkeys(
        ("create", "delete", "update", "start", "stop", "restart", "status", "logs", "list"),
        "Applications",
    ),
    "app": "Applications",
    "releases": "Releases",
    "preview": "Preview environments",
    "recipe": "Recipes",
    "import": "Importing applications",
    "env": "Application environments",
    "github": "GitHub deploys",
    "site": "Sites",
    "domain": "Domains",
    "cert": "Certificates",
    "db": "Databases",
    "backup": "Application backups",
    "rollback": "Rollbacks",
    "cron": "Scheduled jobs",
    "service": "Services",
    "monitor": "The process monitor",
    "diagnose": "Application diagnosis",
    "health": "The server health report",
    "setup": "Server setup (nginx, runtimes, certbot)",
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
    Refuse a local-deployment feature on a hub.

    Args:
        feature: What was asked for, as a label ("Applications").
        config: The configuration; the process-wide one by default.

    Raises:
        RoleError: When this Noust is a hub.
    """
    if not is_hub(config):
        return
    raise RoleError(
        f"{feature}: not available on this central, which is a hub",
        details=(
            "A hub (central.role = hub) manages other servers and deploys nothing "
            "itself. Pick the server in the console, or run the command against a node. "
            "On a machine that should also deploy, set 'noust config set central.role "
            "server'; a container cannot, it has no nginx or systemd."
        ),
    )


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


def central_info(config: Config | None = None) -> dict[str, Any]:
    """
    Describe the central for the console: its role and its secrets' state.

    This is the payload the API exposes as ``central`` (in the session the
    console reads after sign-in), so the console can hide what a hub does not
    do and show the unlock form while the secrets are sealed and locked.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        ``{"role": "server" | "hub", "sealed": bool, "locked": bool}``;
        ``locked`` is True only while the store is sealed and this process
        has not been given the passphrase.
    """
    from noust.core import sealing
    from noust.core.secrets import secrets_dir

    root = secrets_dir()
    sealed = sealing.is_sealed(root)
    return {
        "role": role(config),
        "sealed": sealed,
        "locked": sealed and not sealing.is_unlocked(root),
    }
