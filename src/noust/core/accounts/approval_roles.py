# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which calls hand someone more than they had, and so wait for a second person.

:mod:`noust.core.accounts.approvals` holds the table of routes; the questions a route's body has
to answer before it counts (does this call change the roles of that account?) are here, each
reading the state it is judged against from the store, the way ``rule_for`` is asked on every
call. A question answers False for a call the handler is going to refuse (an account or a role that
does not exist, a value that is not valid), so the person is told what is wrong at once instead of
being sent to wait for a decision on a call that cannot run.

What counts, and why (the line the routes below were drawn by):

- **The roles of an account, whatever route sets them.** ``PATCH`` with ``role``, ``PATCH`` with
  ``roles`` and ``PUT .../roles`` are one change. Any difference counts, reordering and taking a
  role away included: the role an account is known by is what an approval judges a decider by, so
  none of those is a harmless edit.
- **A team membership that is new or higher.** The same grant ``POST /api/access/bindings`` makes
  for a built-in team role on a whole team; two doors to one room are one rule.
- **A role that holds more than it did.** What a role holds is held by everyone who holds it, at the
  next request: adding a permission to a role is granting it to each of them. Taking permissions away
  gives nobody anything, and a role nobody holds yet is inert until somebody is given it, which is
  guarded where it happens. A widening counts whether or not the role is held yet, so an approval
  to give a role cannot be followed by widening it before it is used.
- **A sign-in source that hands out different roles, and a new one.** An identity provider decides
  which roles the people who arrive through it get (its default role, what a group means, whether a
  first sign-in is linked to an account by its e-mail address): the role grant of
  ``POST /api/auth/accounts`` made once for everyone it will ever send. Adding one always counts,
  because its defaults are roles too.

What does not count: taking a role, a binding or a membership away (permissions are a union, so it
only reduces what somebody may do, and it is the quickest way to stop an abuse), and making or
deleting a role nobody holds. The owner role is protected by
:class:`~noust.core.access.role_manager.RoleManager`, not by approval.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields
from typing import TYPE_CHECKING, Any

from noust.core.accounts import role_rules

if TYPE_CHECKING:
    from noust.core.access.roles import RoleRecord

#: The question a route's body has to answer: does this call, as it stands, need a second person.
BodyCheck = Callable[[Any, Mapping[str, str]], bool]


# ------------------------------------------------------------ roles of an account


def changes_account_roles(body: Any, params: Mapping[str, str]) -> bool:
    """
    Whether a call would leave an account with other roles than it holds.

    Both fields are read, because the handler of ``PATCH`` takes ``roles`` over ``role`` and a guard
    that read one could be passed by the other.

    Args:
        body: The call's JSON body: ``roles`` (a list), ``role`` (one), or both.
        params: The route's parameters: ``username``.

    Returns:
        True when the roles asked for are not the roles the account holds, in the same order.
    """
    if not isinstance(body, Mapping):
        return False
    from noust.core.accounts.manager import AccountManager

    account = AccountManager().find(str(params.get("username") or ""))
    if account is None:
        return False
    wanted: list[list[str]] = []
    listed = body.get("roles")
    if isinstance(listed, list) and all(isinstance(item, str) for item in listed):
        names = role_rules.normalised(listed)
        if names and all(names):
            wanted.append(names)
    one = body.get("role")
    if isinstance(one, str) and one.strip():
        wanted.append(role_rules.with_first_role(account.roles, one.strip().lower()))
    return any(tuple(names) != account.roles for names in wanted)


# --------------------------------------------------------------- team membership


def _membership(body: Any, params: Mapping[str, str]) -> tuple[str | None, str] | None:
    """
    Args:
        body: The call's JSON body: ``role``, and ``username`` when the path has none.
        params: The route's parameters: ``team``, and ``username`` for one member.

    Returns:
        The role the account has in the team now (None when it is not a member) and the role the
        call gives, or None when the handler will refuse the call (no such team, account or role).
    """
    if not isinstance(body, Mapping) or not isinstance(body.get("role"), str):
        return None
    from noust.core.accounts.manager import AccountManager
    from noust.core.teams import TeamStore
    from noust.core.teams.model import TEAM_ROLES, TeamError

    chosen = body["role"].strip().lower()
    username = params.get("username") or body.get("username")
    account = AccountManager().find(str(username or ""))
    if chosen not in TEAM_ROLES or account is None:
        return None
    store = TeamStore()
    try:
        team = store.require_team(str(params.get("team") or ""))
    except TeamError:
        return None
    return store.memberships_of(account.id).get(team.id), chosen


def _higher(current: str | None, chosen: str) -> bool:
    """
    Args:
        current: A member's role now, or None.
        chosen: The role it is to have.

    Returns:
        Whether that is a place in the team it did not have: new, or a role above the one it had.
    """
    from noust.core.teams.model import TEAM_ROLES

    return current is None or TEAM_ROLES.index(chosen) > TEAM_ROLES.index(current)


def adds_or_raises_member(body: Any, params: Mapping[str, str]) -> bool:
    """
    Whether ``POST .../members`` gives an account a place in a team it did not have.

    Args:
        body: ``username`` and ``role``.
        params: ``team``.

    Returns:
        True for a new member, or an existing one given a higher role (the call is an upsert).
    """
    found = _membership(body, params)
    return found is not None and _higher(*found)


def raises_member(body: Any, params: Mapping[str, str]) -> bool:
    """
    Whether ``PATCH .../members/{username}`` gives a member a higher role.

    Args:
        body: ``role``.
        params: ``team`` and ``username``.

    Returns:
        True when the account is a member and the role is above the one it has.
    """
    found = _membership(body, params)
    return found is not None and found[0] is not None and _higher(*found)


# --------------------------------------------------------------------- role definitions


def _editable_role(params: Mapping[str, str]) -> RoleRecord | None:
    """
    Args:
        params: The route's parameters: ``name`` or ``role``.

    Returns:
        The role as it stands, or None when there is none or it is the owner (which nobody edits:
        the handler says so).
    """
    from noust.core.access.roles import RoleStore

    role = RoleStore().get(str(params.get("name") or params.get("role") or ""))
    return None if role is None or role.protected else role


def widens_role(body: Any, params: Mapping[str, str]) -> bool:
    """
    Whether a call gives a role permissions it does not hold.

    Args:
        body: ``permissions`` (the whole new set), or nothing for a change of words only.
        params: The role: ``name`` or ``role``.

    Returns:
        True when the new set has a permission the role lacks. A set with a name that is not a
        permission is refused by the handler, so it is not a question for a second person.
    """
    from noust.core.permission_catalog import ALL_PERMISSIONS, Permission

    if not isinstance(body, Mapping):
        return False
    listed = body.get("permissions")
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        return False
    role = _editable_role(params)
    asked = {item.strip() for item in listed if item.strip()}
    if role is None or asked - ALL_PERMISSIONS:
        return False
    return bool(asked - role.held - {Permission.SELF})


def restores_permissions(_body: Any, params: Mapping[str, str]) -> bool:
    """
    Whether putting a role back to how Noust ships it gives it permissions it does not hold.

    Args:
        _body: Nothing.
        params: The role: ``name``.

    Returns:
        True when the operator took permissions out of a role Noust ships and the reset would put
        them back.
    """
    role = _editable_role(params)
    if role is None or role.shipped is None:
        return False
    return bool(role.shipped - role.held)


def accepts_offers(body: Any, params: Mapping[str, str]) -> bool:
    """
    Whether a call takes permissions a newer release offers a role the operator edited.

    Args:
        body: ``accept`` and ``dismiss``, lists of permissions on offer.
        params: The role: ``name``.

    Returns:
        True when something is accepted. Naming a permission that is not on offer is refused by the
        handler.
    """
    role = _editable_role(params)
    if role is None or not isinstance(body, Mapping):
        return False
    named: dict[str, set[str]] = {}
    for field in ("accept", "dismiss"):
        listed = body.get(field) or []
        if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
            return False
        named[field] = {item.strip() for item in listed}
    if (named["accept"] | named["dismiss"]) - set(role.offers):
        return False
    return bool(named["accept"])


# ------------------------------------------------------------------- sign-in sources

#: What a source decides about the roles of the people who arrive through it. The same names on
#: every kind of source (:data:`noust.core.accounts.federation.ADMISSION_SETTINGS`).
_HANDED_OUT = (
    "provisioning",
    "default_role",
    "default_team",
    "default_team_role",
    "link_by_email",
    "group_sync",
    "group_mappings",
)


def _source(protocol: str, name: str) -> Any:
    """
    Args:
        protocol: ``oidc``, ``saml``, ``ldap`` or ``scim``.
        name: The source's name.

    Returns:
        The source as it is configured, or None when there is none.
    """
    if protocol == "oidc":
        from noust.core.accounts.oidc_store import OidcStore

        return OidcStore().get_provider(name)
    if protocol == "saml":
        from noust.core.accounts.saml_store import SamlStore

        return SamlStore().get_provider(name)
    if protocol == "ldap":
        from noust.core.accounts.directory_store import DirectoryStore

        return DirectoryStore().get_directory(name)
    from noust.core.accounts.scim_store import ScimStore

    return ScimStore().get_source(name)


def _hands_out(source: Any) -> tuple[Any, ...]:
    """
    Args:
        source: A source.

    Returns:
        What it decides about roles, comparable.
    """
    return (
        source.provisioning,
        source.default_role,
        source.default_team,
        source.default_team_role,
        source.link_by_email,
        source.group_sync,
        tuple(tuple(sorted(mapping.to_dict().items())) for mapping in source.group_mappings),
    )


def source_changes_roles(protocol: str, parameter: str) -> BodyCheck:
    """
    Make the question for one kind of sign-in source.

    Args:
        protocol: ``oidc``, ``saml``, ``ldap`` or ``scim``.
        parameter: The route's parameter that names the source (``provider``, ``name``).

    Returns:
        A check that answers whether a ``PUT`` would make the source hand out other roles than it
        does, worked out the way the source would be saved (the settings validated and settled by
        :mod:`noust.core.accounts.federation`), so a field sent again with the value it has asks
        nothing. A source that does not exist yet always needs one: its defaults are roles too (a
        SCIM source creates every account it is told about).
    """

    def applies(body: Any, params: Mapping[str, str]) -> bool:
        name = str(params.get(parameter) or "")
        # No name is a call through a central to a server's own route, which that server judges.
        if not isinstance(body, Mapping) or not name:
            return False
        current = _source(protocol, name)
        if current is None:
            return True
        given = {key: body[key] for key in _HANDED_OUT if body.get(key) is not None}
        if not given:
            return False
        from noust.core.accounts import federation
        from noust.core.accounts.manager import AccountManager
        from noust.core.exceptions import ValidationError

        known = {field.name for field in fields(current)}
        updated = current
        try:
            for key, value in given.items():
                if key in known:
                    updated = federation.apply_admission_setting(updated, key, value)
            updated = federation.settle(updated, AccountManager())
        except ValidationError:
            return False
        return _hands_out(updated) != _hands_out(current)

    return applies
