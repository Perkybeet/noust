# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing a new person into a team: an account, or an invitation, with its place.

An account created the way every account was before teams joins the default
team. A person brought in for a team joins that team only, and these are the
two doors (an account with a password, an invitation they answer themselves).
The CLI and the API both come through here, so a person cannot be left with an
account and no team, or in a team of the wrong kind of account.

The accounts manager is passed in rather than imported: this package is
imported by the store, and the accounts import the store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.exceptions import ValidationError
from noust.core.teams.model import (
    TEAM_ROLE_VIEWER,
    Membership,
    Team,
    refuse_instance_only_member,
    validate_team_role,
)
from noust.core.teams.repository import TeamStore

if TYPE_CHECKING:
    from noust.core.accounts.manager import AccountManager
    from noust.core.accounts.model import Account


def _plan(
    accounts: AccountManager, username: str, role: str | None, team: str, team_role: str | None
) -> tuple[Team, str]:
    """
    Check everything that can be refused before an account is written.

    Args:
        accounts: The account manager.
        username: The account to create.
        role: Its instance role.
        team: The team it joins.
        team_role: Its role there; ``viewer`` when omitted.

    Returns:
        The team and the team role.

    Raises:
        TeamNotFoundError: When the team does not exist.
        ValidationError: When the account exists already, the team role is
            unknown, or the instance role never joins a team.
    """
    chosen = validate_team_role(team_role or TEAM_ROLE_VIEWER)
    if accounts.find(username) is not None:
        raise ValidationError(
            f"{username!r} already has an account",
            details=(
                f"Add it to the team with 'noust team add-member {team} {username}'; "
                "an invitation to a team is for a person who does not have an account yet."
            ),
            field="username",
        )
    if role:
        refuse_instance_only_member(username, role.strip().lower())
    return TeamStore(accounts.store).require_team(team), chosen


def create_in_team(
    accounts: AccountManager,
    username: str,
    role: str,
    *,
    password: str,
    team: str,
    team_role: str | None = None,
    display_name: str | None = None,
    person_ref: str | None = None,
    created_by: str | None = None,
) -> tuple[Account, Membership]:
    """
    Create an account with a password that belongs to one team and not the default.

    Args:
        accounts: The account manager.
        username: Sign-in name.
        role: Its instance role.
        password: Its password, checked against the policy.
        team: The team it joins.
        team_role: Its role there; ``viewer`` when omitted.
        display_name: How the console greets them.
        person_ref: Who the account belongs to.
        created_by: Who is creating it.

    Returns:
        The account and its membership.

    Raises:
        TeamNotFoundError: When the team does not exist.
        ValidationError: When anything is refused before the account exists.
    """
    owner, chosen = _plan(accounts, username, role, team, team_role)
    account = accounts.create(
        username,
        role,
        password=password,
        display_name=display_name,
        person_ref=person_ref,
        created_by=created_by,
        join_default_team=False,
    )
    membership = TeamStore(accounts.store).add_member(
        owner.slug, account.username, chosen, created_by=created_by
    )
    return account, membership


def invite_to_team(
    accounts: AccountManager,
    username: str,
    role: str,
    *,
    team: str,
    team_role: str | None = None,
    display_name: str | None = None,
    person_ref: str | None = None,
    created_by: str | None = None,
    expires_hours: float | None = None,
    email: str | None = None,
) -> tuple[Account, str, Membership]:
    """
    Invite a person to one team: they set their own password, and join only it.

    Args:
        accounts: The account manager.
        username: The account to create.
        role: Its instance role.
        team: The team it joins.
        team_role: Its role there; ``viewer`` when omitted.
        display_name: How the console greets them.
        person_ref: Who the account belongs to.
        created_by: Who is inviting.
        expires_hours: How long the code lasts; the manager's default when omitted.
        email: Where the invitation is mailed; kept on the new account.

    Returns:
        The account, the invitation code (shown once) and the membership.

    Raises:
        TeamNotFoundError: When the team does not exist.
        ValidationError: When anything is refused before the account exists.
    """
    owner, chosen = _plan(accounts, username, role, team, team_role)
    options = {} if expires_hours is None else {"expires_hours": expires_hours}
    account, code = accounts.invite(
        username,
        role,
        display_name=display_name,
        person_ref=person_ref,
        created_by=created_by,
        join_default_team=False,
        email=email,
        **options,
    )
    membership = TeamStore(accounts.store).add_member(
        owner.slug, account.username, chosen, created_by=created_by
    )
    return account, code, membership
