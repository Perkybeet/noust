# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet's rules that are not transport: who may register nodes, and how far a central may go.

Two policies live here, one per side of the fleet:

- **On a central**, :func:`node_registration_blockers`: whoever signs in to a
  central reaches every node it manages, so the central's sign-in must be at
  least as strong as the nodes' own - two-factor sign-in has to be on before
  the first node is added: the console's own, or an account's own second
  factor (an authenticator or a passkey).
  :meth:`noust.fleet.nodes.NodeManager.add` calls it itself, so the CLI and
  the API share one door.
- **On a node**, the access ceiling (:class:`FleetAccess`): the most any
  central may do on this server, whatever its operator's role there. The node
  sets it (``noust fleet authorize --access``, ``noust fleet access``) and
  enforces it where a fleet token is admitted - :func:`permits` is called by
  the permission check in :mod:`noust.web.auth` - so a compromised or
  outdated central cannot claim more than the node granted: the ceiling is
  read from this server's own store, never from a header.
  ``GET /api/auth/fleet/self`` publishes it so a central can show it and grey
  out what it may not do, but the node is the one that refuses.

The ceiling applies to every central this server trusts: it is a property of
the server ("this server may only be read from its central"), not of one
token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from noust.core.exceptions import SecurityError, ValidationError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: A ceiling's level, from the least to the most a central may do.
AccessLevel = Literal["read", "deploy", "admin"]

#: The levels, in increasing order of what they allow.
ACCESS_LEVELS: tuple[AccessLevel, ...] = ("read", "deploy", "admin")

#: What a ``deploy`` ceiling adds to reading: moving the applications that
#: already run here - start, stop, restart, update, roll back, redeploy - and
#: taking the backups that make those safe (``backups.run``; deleting backups
#: or changing where they go is ``backups.manage``, which is ``admin``).
#: Creating, deleting or configuring anything is ``admin``.
DEPLOY_PERMISSIONS = frozenset({"apps.operate", "apps.deploy", "backups.run"})

#: What no central may ever do on a node, whatever its ceiling: manage who
#: signs in here, the security settings and the audit trail. A compromised
#: central must not be able to leave itself a credential, weaken the node's
#: sign-in or erase its own tracks.
NEVER_FLEET_PERMISSIONS = frozenset({"security.manage", "accounts.manage", "audit.manage"})

#: What every signed-in principal holds over its own credential (its session,
#: its token): a central revoking or checking its own fleet token is not an
#: action on the server, so no ceiling withholds it.
SELF_PERMISSION = "self"

#: Touching how the server itself is reached - SSH keys, sshd, the firewall,
#: system accounts. Allowed to a central only when the node says so in so many
#: words (``host_access``), on top of an ``admin`` ceiling.
HOST_ACCESS_PERMISSION = "server.host_access"

#: A raw unit, a cron command, a backup hook or a raw site configuration: each
#: runs as root, so it is a way onto the host however it is labelled. A central
#: gets it only with host access, exactly like :data:`HOST_ACCESS_PERMISSION`.
ROOT_EQUIVALENT_PERMISSION = "root_equivalent"

#: Every permission that reaches the host itself, held back without host access.
HOST_PERMISSIONS = frozenset({HOST_ACCESS_PERMISSION, ROOT_EQUIVALENT_PERMISSION})


def validate_access_level(value: Any) -> AccessLevel:
    """
    Check a ceiling level.

    Args:
        value: What the operator or a node gave.

    Returns:
        The level.

    Raises:
        ValidationError: When it is not ``read``, ``deploy`` or ``admin``.
    """
    if value not in ACCESS_LEVELS:
        raise ValidationError(
            f"Unknown fleet access level: {value!r}",
            details=f"Use one of: {', '.join(ACCESS_LEVELS)}.",
            field="level",
        )
    return cast(AccessLevel, value)


@dataclass(frozen=True)
class FleetAccess:
    """
    The most a central may do on this server.

    Attributes:
        level: ``read`` (only reads), ``deploy`` (reads, plus operating and
            updating the applications already here) or ``admin`` (everything
            but the node's own accounts, security settings and audit trail,
            and what reaches the host).
        host_access: Whether a central may also reach the host itself: change
            how this server is reached (SSH keys, sshd, firewall, system
            accounts) and make root-equivalent changes (raw units, cron
            commands, backup hooks, raw site configuration). Only counts with
            an ``admin`` level.
    """

    level: AccessLevel = "admin"
    host_access: bool = False

    def __post_init__(self) -> None:
        validate_access_level(self.level)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the ceiling for JSON output and ``GET /api/auth/fleet/self``.

        Returns:
            ``{"level": ..., "host_access": ...}``.
        """
        return {"level": self.level, "host_access": self.host_access}

    @classmethod
    def from_dict(cls, data: Any) -> FleetAccess:
        """
        Read a ceiling a node published (``GET /api/auth/fleet/self``).

        Args:
            data: The decoded JSON body, or anything else.

        Returns:
            The ceiling.

        Raises:
            ValidationError: When it is not a ceiling this version knows.
        """
        if not isinstance(data, dict):
            raise ValidationError("A fleet access ceiling must be a JSON object")
        host_access = data.get("host_access", False)
        if not isinstance(host_access, bool):
            raise ValidationError("A fleet access ceiling's host_access must be true or false")
        return cls(level=validate_access_level(data.get("level")), host_access=host_access)

    def describe(self) -> str:
        """
        Say in a few words what a central may do here.

        Returns:
            Such as ``admin; host access off``.
        """
        host = "on" if self.host_access and self.level == "admin" else "off"
        return f"{self.level}; host access {host}"


#: The ceiling of a server that never set one: what 3.0 did (everything a
#: fleet token could do), without host access, which 3.0 never had.
DEFAULT_ACCESS = FleetAccess("admin", False)


def permits(access: FleetAccess, permission: str) -> bool:
    """
    Report whether a ceiling lets a central use a permission on this server.

    The caller intersects this with what the operator's own role allows; the
    ceiling only ever narrows. Fails closed: an unknown level allows nothing.

    Args:
        access: The node's ceiling (:func:`current_access`).
        permission: A ``noust.web.permissions`` permission, such as
            ``apps.deploy``.

    Returns:
        True when the ceiling allows it.
    """
    if permission == SELF_PERMISSION:
        return True
    if permission in NEVER_FLEET_PERMISSIONS:
        return False
    if permission in HOST_PERMISSIONS:
        return access.level == "admin" and access.host_access
    if access.level == "read":
        return permission.endswith(".read")
    if access.level == "deploy":
        return permission.endswith(".read") or permission in DEPLOY_PERMISSIONS
    return access.level == "admin"


def current_access(store: NoustStore | None = None) -> FleetAccess:
    """
    Read this server's ceiling for its centrals.

    Read from the store on every call, so ``noust fleet access`` takes effect
    on the next request a running console admits, without a restart.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        The ceiling; :data:`DEFAULT_ACCESS` when none was ever set.
    """
    from noust.core.store import get_store

    row = (store or get_store()).get_fleet_access()
    if row is None:
        return DEFAULT_ACCESS
    return FleetAccess(
        level=validate_access_level(row["level"]), host_access=bool(row["host_access"])
    )


def set_access(
    access: FleetAccess, *, actor: str | None = None, store: NoustStore | None = None
) -> FleetAccess:
    """
    Set this server's ceiling for its centrals.

    Only ever called from this server's own CLI: no API endpoint writes it, so
    a central can never raise the ceiling it is held to.

    Args:
        access: The new ceiling.
        actor: Who set it, for the record (``cli:<user>``).
        store: The store; the process-wide one by default.

    Returns:
        The ceiling as stored.
    """
    from noust.core.store import get_store

    target = store or get_store()
    target.set_fleet_access(access.level, access.host_access, updated_by=actor)
    return current_access(target)


def node_registration_blockers() -> list[str]:
    """
    List what stops this central from registering a node.

    Returns:
        One actionable sentence per blocker; empty when a node may be added.
    """
    try:
        from noust.web.auth import SecurityConfig, TokenManager, get_global_token_manager
    except ImportError as exc:
        return [
            "The console's dependencies are not installed, so this central has no sign-in "
            "to protect with two-factor authentication. Install them (noust web install), "
            f"then enable two-factor sign-in. Python said: {exc}"
        ]
    try:
        manager = get_global_token_manager() or TokenManager(SecurityConfig())
        enabled = manager.totp_enabled() or _second_factor_of_its_own()
    except SecurityError as exc:
        return [
            f"The two-factor sign-in state could not be read ({exc.message}). {exc.details}".strip()
        ]
    if not enabled:
        return [
            "Two-factor sign-in is not enabled on this central, and whoever signs in here "
            "reaches every node it manages. Enable it first: sign in with an account that "
            "holds its own authenticator or passkey, or enable the console's own with "
            "'noust 2fa enroll', then 'noust 2fa confirm <code>' (or Settings, Security in "
            "the console)."
        ]
    return []


def _second_factor_of_its_own() -> bool:
    """
    Report whether someone who may add nodes signs in with a second factor of their own.

    Before accounts, the console-wide TOTP was the only second factor there
    was. Now an account enrols its own (an authenticator or a passkey, and
    it may do nothing else until it has), and the master token's own passkey
    protects its sign-in as the console's TOTP does.

    Returns:
        True when the master token has a passkey, or an account that can
        sign in and may add nodes (``fleet.manage``) holds a second factor.

    Raises:
        SecurityError: When the store cannot be read: a central does not
            register nodes on a guess.
    """
    import sqlite3

    from noust.core.accounts import AccountManager
    from noust.core.accounts.passkeys import PasskeyManager
    from noust.core.store import StoreError
    from noust.web.permissions import Permission
    from noust.web.permissions.roles import permissions_for_role

    try:
        if PasskeyManager().count(None) > 0:
            return True
        return any(
            account.can_sign_in()
            and account.has_mfa
            and Permission.FLEET_MANAGE in permissions_for_role(account.role)
            for account in AccountManager().list_all()
        )
    except (StoreError, sqlite3.Error) as exc:
        raise SecurityError(
            "The accounts could not be read from the store",
            details=f"Check that the store is readable (noust store check). {exc}",
        ) from exc
