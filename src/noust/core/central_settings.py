# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The settings that decide what a central deploys and what it does by itself: its role, what it is told to
deploy here, and when it looks for new versions of the images in use.

They are judged here, below the configuration, because :mod:`noust.central` reads its settings from
:mod:`noust.core.config` and so cannot be imported by it. The central package mirrors the first two lists
(``noust.central.ROLES`` and ``noust.central.DEPLOY_HERE_MODES``), and a test holds the copies equal.

- ``central.role``: ``server`` deploys applications here, ``hub`` only manages other servers.
- ``central.deploy_here``: whether a hub also deploys on the Docker its machine gave it. Empty is no,
  ``containers`` is yes. It is the second of two things asked on purpose, outside the console, for
  the container of a NAS to deploy (the first is the Docker socket mounted into it).
- ``central.image_check``: when the central's own scheduler asks the registries whether the images in
  use have a new version. Empty (the default) is never: it is a request to somebody else's server, so
  nothing makes it until a person says when.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from noust.core import paths
from noust.core.calendar_expr import SCHEDULE_ALIASES, Calendar, CalendarError
from noust.core.exceptions import ConfigError

__all__ = [
    "CENTRAL_DEFAULTS",
    "CENTRAL_DEPLOY_HERE",
    "CENTRAL_ROLES",
    "CENTRAL_VALIDATORS",
    "consent_from_the_console",
    "validate_central_role",
    "validate_deploy_here",
    "validate_image_check",
    "without_borrowed_consent",
]

#: What ``central.role`` may be, the default first.
CENTRAL_ROLES = ("server", "hub")

#: What ``central.deploy_here`` may be, the empty one (off) first.
CENTRAL_DEPLOY_HERE = ("", "containers")


def validate_central_role(value: Any) -> str:
    """
    Accept one of the roles a Noust can have.

    Args:
        value: Candidate role.

    Returns:
        The role, lower-cased.

    Raises:
        ConfigError: When it is not one of :data:`CENTRAL_ROLES`.
    """
    role = str(value or "").strip().lower()
    if role not in CENTRAL_ROLES:
        raise ConfigError(
            f"central.role must be one of {', '.join(CENTRAL_ROLES)}, not {value!r}",
            details=(
                "'server' deploys applications here; 'hub' only manages other "
                "servers (a central on a NAS or in a container)."
            ),
        )
    return role


def validate_deploy_here(value: Any) -> str:
    """
    Accept what a hub may be told to deploy on the machine's own Docker.

    Args:
        value: Candidate setting; ``off`` and nothing mean the same.

    Returns:
        ``containers``, or the empty string for off.

    Raises:
        ConfigError: When it is neither.
    """
    mode = str(value or "").strip().lower()
    if mode == "off":
        mode = ""
    if mode not in CENTRAL_DEPLOY_HERE:
        raise ConfigError(
            f"central.deploy_here must be 'containers' or empty, not {value!r}",
            details=(
                "'containers' lets a hub run container applications and Compose stacks on the "
                "Docker its machine gave it; empty (or 'off') keeps it a hub that deploys "
                "nothing. Nothing else is offered: a hub has no systemd or web server to run "
                "anything native."
            ),
        )
    return mode


def validate_image_check(value: Any) -> str:
    """
    Accept when the central looks for new versions of the images in use.

    Args:
        value: A schedule: one of Noust's names (``daily``, ``weekly``...) or a calendar expression
            (``Mon *-*-* 04:00:00``); ``off`` and nothing mean never.

    Returns:
        The schedule as written, or the empty string for never.

    Raises:
        ConfigError: When it is not a schedule the central can read.
    """
    text = str(value or "").strip()
    if text.lower() == "off":
        text = ""
    if not text:
        return ""
    try:
        Calendar.parse(text, aliases=SCHEDULE_ALIASES)
    except CalendarError as exc:
        raise ConfigError(
            f"central.image_check must be a schedule or empty, not {value!r}",
            details=(
                f"{exc.message}. Use daily, weekly, monthly, hourly or a calendar expression such "
                "as 'Mon *-*-* 04:00:00'; empty (or 'off') never looks. It is a request to the "
                "registries of your images, so it is never made until you say when."
            ),
        ) from exc
    return text


def without_borrowed_consent(config: dict[str, Any]) -> dict[str, Any]:
    """
    Leave out of what is written a consent that only the environment gave.

    The configuration is written whole, and what the environment overrides is in it, so any save
    from the console would copy ``NOUST_DEPLOY_HERE`` into ``config.yaml`` and the consent would
    outlive the line of the compose file that gave it: taking the line away would not turn it
    off. A value somebody set on purpose (``noust config set``, which is not the environment's
    value) is written.

    Args:
        config: The configuration about to be written.

    Returns:
        It, without ``central.deploy_here`` when that is what the environment says; the same
        mapping otherwise.
    """
    given = (paths.getenv("DEPLOY_HERE") or "").strip().lower()
    central = config.get("central")
    if not given or not isinstance(central, dict):
        return config
    if str(central.get("deploy_here") or "").strip().lower() != given:
        return config
    return {
        **config,
        "central": {key: value for key, value in central.items() if key != "deploy_here"},
    }


def consent_from_the_console(before: Any, after: Any) -> bool:
    """
    Say whether a write from the console would turn ``central.deploy_here`` on.

    Deploying on the machine's Docker is root on that machine, and it is decided where the compose
    file that mounts the socket is written, not in a browser: the console may turn it off and may
    leave it as it is, never on.

    Args:
        before: The value now (what is stored, or the environment's).
        after: The value the write asks for.

    Returns:
        True when it is on afterwards and was not before.
    """
    try:
        wanted = validate_deploy_here(after)
        now = validate_deploy_here(before)
    except ConfigError:
        # A value nobody can set is refused by the setting itself, with its own words.
        return False
    return bool(wanted) and wanted != now


#: What each setting is before anybody sets it, under ``central`` in the configuration. ``role`` is what
#: every release has been: ``server`` deploys and serves applications and may also manage other servers,
#: and ``hub`` is a central with no local deployments, where what needs a web server is refused with a
#: message instead of failing on a missing nginx (``NOUST_CENTRAL_ROLE`` overrides it, which is how the
#: container image is a hub). ``deploy_here`` is empty (no) until ``NOUST_DEPLOY_HERE`` says
#: ``containers``; the console cannot turn it on. ``image_check`` is empty (never) until a person says when.
CENTRAL_DEFAULTS: dict[str, str] = {"role": "server", "deploy_here": "", "image_check": ""}

#: How each is judged, by its dotted key, as the configuration's table of validators takes them.
CENTRAL_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "central.role": validate_central_role,
    "central.deploy_here": validate_deploy_here,
    "central.image_check": validate_image_check,
}
