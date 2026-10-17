# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who comes in from an outside identity source, and as whom: one admission for every protocol.

OpenID Connect, SAML, a directory over LDAP and SCIM provisioning all end at the same
question once the source has vouched for a person (rule 3: one implementation of each
thing). :class:`Admission` answers it, in order:

1. **Is the person acceptable to this server?** The e-mail is verified when the source is
   asked to, belongs to an allowed domain when there are any, and the source used one of
   the methods required. No account exists yet? Nobody comes in this way: the first
   account is made by the operator, so a source can never make the first administrator.
2. **Who is it?** By the pair the source guarantees, ``(issuer, subject)`` of one protocol,
   in ``account_identities``. Never by e-mail alone.
3. **A person never seen before** is linked to an existing account by their verified
   e-mail only where the source is explicitly trusted for that and the account already
   holds a second factor. Otherwise the provisioning policy: ``off`` (an administrator
   links them), ``invite`` (the invited account of that e-mail is activated) or ``auto``
   (an account is made with the source's default role, in its default team).
4. **Their groups**, when asked to, set the instance role, the team memberships and the
   roles held at a project or an environment, on every sign-in: what the source no longer
   grants is taken back, and what an administrator set by hand is left alone.

A group never gives ``security``, ``auditor`` or a team ``owner``: those are given by a
person, so no claim can make someone their own auditor.

What differs between protocols is only how a person is vouched for (a signed token, a
signed assertion, a bind that succeeded, a directory the organisation pushes) and the
name of the audit events; both are parameters here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from noust.core.access.model import AccessError
from noust.core.accounts.manager import AccountManager
from noust.core.accounts.model import (
    ROLE_ADMIN,
    STATUS_DISABLED,
    STATUS_INVITED,
    Account,
    AccountError,
    validate_username,
)
from noust.core.accounts.oidc_model import (
    PROVISIONING_AUTO,
    PROVISIONING_INVITE,
    GroupMapping,
    IdentityClaims,
    OidcRefused,
    parse_mappings,
    team_role_or_none,
    validate_default_role,
    validate_domains,
    validate_label,
    validate_methods,
    validate_provisioning,
    vouch_email,
)
from noust.core.accounts.oidc_store import Identity, OidcStore
from noust.core.audit import record as audit_record
from noust.core.audit.actor import Actor
from noust.core.exceptions import ValidationError
from noust.core.teams.model import INSTANCE_ONLY_ROLES, TEAM_ROLES, TeamConflictError, TeamError
from noust.core.teams.repository import TeamStore

if TYPE_CHECKING:
    from noust.web.permissions.access import AccessManager

logger = logging.getLogger(__name__)

_USERNAME_JUNK = re.compile(r"[^a-z0-9._-]+")

#: The settings every source takes the same way, validated in one place.
ADMISSION_SETTINGS: tuple[str, ...] = (
    "label",
    "enabled",
    "require_email_verified",
    "assume_email_verified",
    "provisioning",
    "default_role",
    "default_team",
    "default_team_role",
    "link_by_email",
    "allowed_domains",
    "require_amr",
    "group_sync",
    "group_mappings",
)


class Source(Protocol):
    """
    What :class:`Admission` needs to know about an identity source.

    The attributes are read only here; a source is a frozen record of the
    operator's settings.
    """

    @property
    def name(self) -> str:
        """Its short name, in the audit log."""

    @property
    def label(self) -> str:
        """What a person reads about it."""

    @property
    def protocol(self) -> str:
        """``oidc``, ``saml``, ``ldap`` or ``scim``."""

    @property
    def provisioning(self) -> str:
        """``off``, ``invite`` or ``auto``."""

    @property
    def default_role(self) -> str:
        """The role a new account starts with."""

    @property
    def default_team(self) -> str | None:
        """The team a new account joins."""

    @property
    def default_team_role(self) -> str | None:
        """Its role there."""

    @property
    def link_by_email(self) -> bool:
        """Whether a verified e-mail may link a first sign-in to an account."""

    @property
    def allowed_domains(self) -> tuple[str, ...]:
        """The e-mail domains that may come in; any when empty."""

    @property
    def require_email_verified(self) -> bool:
        """Whether the e-mail must be vouched for."""

    @property
    def assume_email_verified(self) -> bool:
        """Whether a source that says nothing about it is trusted."""

    @property
    def require_amr(self) -> tuple[str, ...]:
        """Authentication methods one of which the source must report."""

    @property
    def group_sync(self) -> bool:
        """Whether the groups decide roles, memberships and bindings."""

    @property
    def group_mappings(self) -> tuple[GroupMapping, ...]:
        """What each group means."""


S = TypeVar("S")


def vouched_email(source: Source, person: IdentityClaims) -> str | None:
    """
    The e-mail of a person, when the source's word for it is good enough.

    Every decision that rests on an address (the domain allow-list, finding an
    account, activating an invitation, naming a new account's person) uses this
    and nothing else.

    Args:
        source: The source.
        person: Who the source vouched for.

    Returns:
        The address when the source says it is verified, or says nothing and is
        assumed to be authoritative; None otherwise.
    """
    return vouch_email(person, assume=source.assume_email_verified)


def apply_admission_setting(source: S, setting: str, value: Any) -> S:
    """
    Change one of the settings every source has in common.

    Args:
        source: A source (a frozen dataclass) before.
        setting: One of :data:`ADMISSION_SETTINGS`.
        value: The new value, as typed.

    Returns:
        The source after, the value validated.

    Raises:
        ValidationError: When the value is refused.
        KeyError: When the setting is not one of :data:`ADMISSION_SETTINGS`.
    """
    if setting == "label":
        name = getattr(source, "name", "")
        return replace(source, label=validate_label(value, name))  # type: ignore[type-var]
    if setting in ("enabled", "require_email_verified", "assume_email_verified"):
        return replace(source, **{setting: bool(value)})  # type: ignore[type-var]
    if setting in ("link_by_email", "group_sync"):
        return replace(source, **{setting: bool(value)})  # type: ignore[type-var]
    if setting == "provisioning":
        return replace(source, provisioning=validate_provisioning(str(value)))  # type: ignore[type-var]
    if setting == "default_role":
        return replace(source, default_role=validate_default_role(str(value)))  # type: ignore[type-var]
    if setting == "default_team":
        team = (str(value).strip().lower() if value else "") or None
        return replace(source, default_team=team)  # type: ignore[type-var]
    if setting == "default_team_role":
        return replace(source, default_team_role=team_role_or_none(value))  # type: ignore[type-var]
    if setting == "allowed_domains":
        return replace(source, allowed_domains=tuple(validate_domains(value)))  # type: ignore[type-var]
    if setting == "require_amr":
        return replace(source, require_amr=tuple(validate_methods(value)))  # type: ignore[type-var]
    if setting == "group_mappings":
        return replace(source, group_mappings=tuple(parse_mappings(value)))  # type: ignore[type-var]
    raise KeyError(setting)


def settle(source: S, accounts: AccountManager) -> S:
    """
    Make a source's settings agree with each other after a change.

    Args:
        source: The source after its settings were applied.
        accounts: The account manager, whose policy may refuse what a source hands out.

    Returns:
        The source, its default team role consistent with its default team.

    Raises:
        ValidationError: When the compliance controls forbid it to hand out the
            administrator role.
    """
    updated = source
    team = getattr(updated, "default_team", None)
    team_role = getattr(updated, "default_team_role", None)
    if team is None:
        updated = replace(updated, default_team_role=None)  # type: ignore[type-var]
    elif team_role is None:
        updated = replace(updated, default_team_role="viewer")  # type: ignore[type-var]
    if accounts.policy().requires("sso_no_admin"):
        refuse_admin(updated)  # type: ignore[arg-type]
    return updated


def refuse_admin(source: Source) -> None:
    """
    Args:
        source: A source about to be saved.

    Raises:
        ValidationError: When it would hand out ``admin`` where a control forbids
            that single sign-in does.
    """
    gives = source.default_role == ROLE_ADMIN and source.provisioning == PROVISIONING_AUTO
    gives = gives or any(mapping.role == ROLE_ADMIN for mapping in source.group_mappings)
    if gives:
        raise ValidationError(
            "This server's compliance controls do not let an identity source hand out the "
            "admin role",
            details=(
                "Administrators are made by a person (noust user set-role), so that granting "
                "privileged rights is a recorded decision."
            ),
            field="default_role",
        )


def check_references(source: Source, accounts: AccountManager) -> None:
    """
    Check that the teams, projects, environments and roles a source names exist.

    Args:
        source: A source about to be saved.
        accounts: The account manager, for the store.

    Raises:
        ValidationError: When it names something that does not exist.
    """
    teams = TeamStore(accounts.store)
    slugs = {mapping.team for mapping in source.group_mappings if mapping.team}
    if source.default_team:
        slugs.add(source.default_team)
    for slug in sorted(slugs):
        try:
            teams.require_team(slug)
        except TeamError as exc:
            raise ValidationError(exc.message, details=exc.details, field="team") from exc
    bound = [mapping for mapping in source.group_mappings if mapping.bind]
    if not bound:
        return
    from noust.web.permissions.access import AccessManager, custom_permissions

    access = AccessManager(accounts.store)
    custom = custom_permissions(access.data)
    for mapping in bound:
        if mapping.bind not in TEAM_ROLES and mapping.bind not in custom:
            raise ValidationError(
                f"The mapping of {mapping.group!r} names the role {mapping.bind!r}, which "
                "does not exist",
                details="List the roles with 'noust access role list'.",
                field="group_mappings",
            )
        try:
            access.bound_scope(
                team=mapping.team, project=mapping.project, environment=mapping.environment
            )
        except (TeamError, ValidationError) as exc:
            raise ValidationError(
                exc.message, details=getattr(exc, "details", ""), field="group_mappings"
            ) from exc


def link_command(source: Source) -> str:
    """
    Args:
        source: A source.

    Returns:
        The command an administrator links a person with, for a refusal's advice.
    """
    return f"noust auth {source.protocol} link"


class Admission:
    """
    Decide whether the person a source vouched for comes in, and as whom.

    Args:
        accounts: The account manager.
        data: The identity store.
        clock: The time source.
    """

    def __init__(
        self, accounts: AccountManager, data: OidcStore, clock: Callable[[], float]
    ) -> None:
        self.accounts = accounts
        self.data = data
        self._clock = clock

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    @staticmethod
    def no_accounts() -> OidcRefused:
        """
        Returns:
            The refusal while the server has no account.
        """
        return OidcRefused(
            "no_accounts",
            "This server has no accounts yet",
            details=(
                "The first account is made by the operator, never by a source: "
                "noust user create NAME --role admin."
            ),
        )

    # --------------------------------------------------------------- decide

    def decide(
        self, source: Source, person: IdentityClaims, client_ip: str
    ) -> tuple[list[str], Account, Identity]:
        """
        Args:
            source: The source.
            person: Who the source vouched for.
            client_ip: Where the sign-in came from.

        Returns:
            What happened, the account and its identity.

        Raises:
            OidcRefused: With a reason, when the policy says no.
        """
        email = vouched_email(source, person)
        if source.require_email_verified and email is None:
            raise OidcRefused(
                "email_unverified",
                "The source does not vouch for this person's e-mail",
                details=(
                    "Verify the address at the source; or, for a directory the organisation "
                    "controls that never says, set assume-email-verified."
                ),
            )
        if source.allowed_domains:
            domain = email.rsplit("@", 1)[-1] if email else ""
            if domain not in source.allowed_domains:
                raise OidcRefused(
                    "domain_not_allowed", "This e-mail's domain is not allowed to sign in"
                )
        if source.require_amr and not set(person.amr) & set(source.require_amr):
            raise OidcRefused(
                "amr_missing",
                "The source did not say it used a method this server requires",
                details=f"Required one of: {', '.join(source.require_amr)}.",
            )
        if not self.accounts.any_exist():
            raise self.no_accounts()

        notes: list[str] = []
        actor = self.actor(source, client_ip)
        identity = self.data.find_identity(person.issuer, person.subject, source.protocol)
        if identity is None:
            account, identity = self.first_sign_in(source, person, actor, notes)
        else:
            found = self.accounts.get(identity.account_id)
            if found is None:
                raise OidcRefused("account_missing", "The linked account no longer exists")
            account = found
        self.require_usable(account)
        mapped = self.sync_groups(
            source, set(person.groups), account, identity.mapped_role, actor, notes
        )
        self.data.touch_identity(
            identity.id, email=person.email, client_ip=client_ip, mapped_role=mapped
        )
        refreshed = self.accounts.get(account.id)
        return notes, refreshed or account, identity

    @staticmethod
    def actor(source: Source, client_ip: str) -> Actor:
        """
        Args:
            source: The source.
            client_ip: Where the request came from.

        Returns:
            Who acts in the audit trail while the source admits somebody.
        """
        return Actor(
            kind="system",
            name=f"{source.protocol}:{source.name}",
            via=source.protocol,
            source=client_ip,
        )

    def require_usable(self, account: Account) -> None:
        """
        Args:
            account: The account the person resolved to.

        Raises:
            OidcRefused: ``disabled``, ``invited`` or ``locked``.
        """
        if account.status == STATUS_DISABLED:
            raise OidcRefused("disabled", "The account is disabled")
        if account.status == STATUS_INVITED:
            raise OidcRefused("invited", "The account has not accepted its invitation yet")
        if not account.can_sign_in(self.now()):
            raise OidcRefused("locked", "The account is locked")

    def first_sign_in(
        self, source: Source, person: IdentityClaims, actor: Actor, notes: list[str]
    ) -> tuple[Account, Identity]:
        """
        Resolve a person this server has never seen to an account, or refuse.

        Args:
            source: The source.
            person: Who the source vouched for.
            actor: Who is acting, for the audit trail.
            notes: Filled with what happens.

        Returns:
            The account and the identity just linked.

        Raises:
            OidcRefused: ``ambiguous_email``, ``link_needs_second_factor``,
                ``already_linked_account``, ``not_invited``, ``email_taken`` or
                ``no_account``.
        """
        email = vouched_email(source, person)
        if source.link_by_email and email:
            # Only an account that may sign in is linked: a link to a disabled or locked one
            # would stay behind a refusal.
            mine = [a for a in self.accounts.find_by_person(email) if a.can_sign_in(self.now())]
            if len(mine) > 1:
                raise OidcRefused(
                    "ambiguous_email",
                    "More than one account belongs to this e-mail",
                    details=f"Link the right one: {link_command(source)}.",
                )
            if len(mine) == 1:
                account = mine[0]
                if not account.has_mfa:
                    raise OidcRefused(
                        "link_needs_second_factor",
                        "The account of this e-mail has no second factor yet, so it is not "
                        "linked automatically",
                        details="Its owner enrols one first, or an administrator links it: "
                        f"{link_command(source)}.",
                    )
                if self.linked_at(account, person.issuer):
                    raise OidcRefused(
                        "already_linked_account",
                        "The account of this e-mail is already linked to another person at "
                        "this source",
                    )
                identity = self.link(source, person, account, actor)
                notes.append(f"linked to {account.username} by verified e-mail")
                return account, identity
        if source.provisioning in (PROVISIONING_INVITE, PROVISIONING_AUTO) and email:
            invited = [a for a in self.accounts.find_by_person(email) if a.status == STATUS_INVITED]
            if len(invited) > 1:
                raise OidcRefused(
                    "ambiguous_email",
                    "More than one invitation is waiting for this e-mail",
                    details=f"Link the right account: {link_command(source)}.",
                )
            if len(invited) == 1:
                account = self.accounts.activate_external(invited[0].id)
                identity = self.link(source, person, account, actor)
                self.audit(
                    f"auth.{source.protocol}.provision",
                    actor,
                    account.username,
                    "invited account activated",
                )
                notes.append(f"activated the invited account {account.username}")
                return account, identity
        if source.provisioning == PROVISIONING_INVITE:
            raise OidcRefused(
                "not_invited",
                "No invitation is waiting for this e-mail",
                details="Invite the person first: noust user invite NAME --person-ref E-MAIL.",
            )
        if source.provisioning == PROVISIONING_AUTO:
            if email and self.accounts.find_by_person(email):
                raise OidcRefused(
                    "email_taken",
                    "An account of this e-mail exists and is not linked to this person",
                    details=f"Link it: {link_command(source)}, or turn on link-by-email.",
                )
            account = self.create(source, person, actor)
            identity = self.link(source, person, account, actor)
            self.audit(
                f"auth.{source.protocol}.provision",
                actor,
                account.username,
                f"created as {account.role}",
            )
            notes.append(f"created the account {account.username} as {account.role}")
            return account, identity
        raise OidcRefused(
            "no_account",
            "No account is linked to this person",
            details=(
                f"An administrator links it: {link_command(source)} SOURCE USER --subject "
                "SUBJECT, or sets the source's provisioning to invite or auto."
            ),
        )

    def linked_at(self, account: Account, issuer: str) -> bool:
        """
        Args:
            account: An account.
            issuer: A source's issuer.

        Returns:
            Whether the account already has a person linked at that issuer.
        """
        return account.id in self.data.accounts_linked_to(issuer)

    def link(
        self, source: Source, person: IdentityClaims, account: Account, actor: Actor
    ) -> Identity:
        """
        Args:
            source: The source.
            person: Who the source vouched for.
            account: The account they are.
            actor: Who is acting.

        Returns:
            The identity.
        """
        identity = self.data.link_identity(
            account.id,
            provider=source.name,
            issuer=person.issuer,
            subject=person.subject,
            email=person.email,
            created_by=actor.label,
            protocol=source.protocol,
        )
        self.audit(
            f"auth.{source.protocol}.link",
            actor,
            account.username,
            f"subject {person.subject[:64]}",
        )
        return identity

    def create(
        self, source: Source, person: IdentityClaims, actor: Actor, *, invited: bool = False
    ) -> Account:
        """
        Make the account a first sign-in becomes.

        Args:
            source: The source, whose default role and team it follows.
            person: Who the source vouched for.
            actor: Who is acting.
            invited: Make it ``invited`` instead of active: a directory told us about the
                person, and their first sign-in at an identity provider activates it.

        Returns:
            The new account.

        Raises:
            OidcRefused: ``team_missing`` when the default team is gone;
                ``account_refused`` when separation of duties refuses the role.
        """
        role = source.default_role
        if self.accounts.policy().requires("sso_no_admin") and role == ROLE_ADMIN:
            role = "viewer"
        team: tuple[int, str] | None = None
        if source.default_team:
            try:
                found = TeamStore(self.accounts.store).require_team(source.default_team)
            except TeamError as exc:
                raise OidcRefused(
                    "team_missing",
                    f"The default team {source.default_team!r} of this source does not exist",
                    details="Create it, or change the source's default team.",
                ) from exc
            team = (found.id, source.default_team_role or "viewer")
        make = self.accounts.provision_external if invited else self.accounts.create_external
        try:
            return make(
                self.username_for(person),
                role,
                display_name=person.name,
                person_ref=vouched_email(source, person),
                created_by=actor.label,
                team=team,
            )
        except (AccountError, ValidationError) as exc:
            raise OidcRefused("account_refused", exc.message, details=exc.details) from exc

    def username_for(self, person: IdentityClaims) -> str:
        """
        Invent a free username for a new account.

        Args:
            person: Who the source vouched for.

        Returns:
            A valid username not used by any account.

        Raises:
            OidcRefused: ``username`` when none could be found.
        """
        local = person.email.split("@", 1)[0] if person.email else ""
        for source in (person.username, local, f"sso-{person.subject[:12]}"):
            # A username that is an address (Microsoft's) is its part before the @.
            base = _USERNAME_JUNK.sub("-", (source or "").split("@", 1)[0].lower()).strip("-._")[
                :40
            ]
            if len(base) < 2:
                continue
            for attempt in range(0, 50):
                candidate = base if attempt == 0 else f"{base}-{attempt + 1}"
                try:
                    name = validate_username(candidate)
                except ValidationError:
                    break
                if self.accounts.find(name) is None:
                    return name
        raise OidcRefused("username", "No username could be made for this person")

    # --------------------------------------------------------------- groups

    def sync_groups(
        self,
        source: Source,
        groups: set[str],
        account: Account,
        mapped_role: str | None,
        actor: Actor,
        notes: list[str],
    ) -> str | bool | None:
        """
        Set the account's role, team memberships and scoped roles from the source's groups.

        Recomputed on every sign-in, so a person removed from a group at the source
        loses what it gave at their next sign-in. What an administrator set by hand
        is never touched: a role changed since the mapping gave it, a membership or
        a binding somebody else added.

        Args:
            source: The source.
            groups: The person's groups.
            account: The account.
            mapped_role: The role a mapping gave last time, which the person's
                identity (or SCIM record) remembers so that taking the group away
                can take the role back without touching one set by hand.
            actor: Who is acting.
            notes: Filled with what changed.

        Returns:
            The role a mapping now gives (to be remembered), None when none
            does, or False when the sync is off and nothing changes.

        Raises:
            OidcRefused: ``role_conflict`` when separation of duties refuses the
                role the groups give; the sign-in fails closed.
        """
        if not source.group_sync:
            return False
        current = account
        mapped: str | None = mapped_role
        if account.role not in INSTANCE_ONLY_ROLES:
            wanted = next(
                (m.role for m in source.group_mappings if m.role and m.group in groups), None
            )
            if wanted == ROLE_ADMIN and self.accounts.policy().requires("sso_no_admin"):
                notes.append("the admin role is not given by groups on this server")
                wanted = None
            if wanted is not None:
                if account.role != wanted:
                    current = self.set_role(account, wanted, actor, "groups of the source", notes)
                mapped = wanted
            elif mapped is not None:
                if account.role == mapped and mapped != source.default_role:
                    current = self.set_role(
                        account, source.default_role, actor, "no longer in the group", notes
                    )
                mapped = None
        self.sync_teams(source, groups, current, actor, notes)
        self.sync_bindings(source, groups, current, actor, notes)
        return mapped

    def set_role(
        self, account: Account, role: str, actor: Actor, why: str, notes: list[str]
    ) -> Account:
        """
        Args:
            account: The account.
            role: The role to give.
            actor: Who is acting.
            why: For the record.
            notes: Filled with the change.

        Returns:
            The account after.

        Raises:
            OidcRefused: ``role_conflict`` when separation of duties refuses it.
        """
        try:
            changed = self.accounts.set_role(account.username, role)
        except AccountError as exc:
            raise OidcRefused("role_conflict", exc.message, details=exc.details) from exc
        self.audit("user.role_change", actor, changed.username, f"{account.role} to {role}: {why}")
        notes.append(f"role {account.role} to {role}")
        return changed

    def sync_teams(
        self,
        source: Source,
        groups: set[str],
        account: Account,
        actor: Actor,
        notes: list[str],
    ) -> None:
        """
        Args:
            source: The source.
            groups: The person's groups.
            account: The account, with the role it has now.
            actor: Who is acting.
            notes: Filled with the changes.
        """
        if account.role in INSTANCE_ONLY_ROLES:
            return
        creator = f"{source.protocol}:{source.name}"
        desired: dict[str, str] = {}
        for mapping in source.group_mappings:
            if mapping.team and not mapping.bind and mapping.group in groups:
                role = mapping.team_role or "viewer"
                held = desired.get(mapping.team)
                if held is None or TEAM_ROLES.index(role) > TEAM_ROLES.index(held):
                    desired[mapping.team] = role
        teams = TeamStore(self.accounts.store)
        have = self.data.team_memberships(account.id)
        for slug, role in sorted(desired.items()):
            existing = have.get(slug)
            if existing is not None and (existing[1] != creator or existing[0] == role):
                continue
            try:
                teams.add_member(slug, account.username, role, created_by=creator)
            except (TeamError, ValidationError) as exc:
                notes.append(f"team {slug} not joined: {exc.message}")
                continue
            self.audit("team.member.add", actor, f"{slug}/{account.username}", f"as {role}")
            notes.append(f"joined team {slug} as {role}")
        for slug, (_role, by) in sorted(have.items()):
            if by != creator or slug in desired:
                continue
            try:
                teams.remove_member(slug, account.username)
            except TeamConflictError as exc:
                notes.append(f"team {slug} kept: {exc.message}")
                continue
            self.audit(
                "team.member.remove", actor, f"{slug}/{account.username}", "no longer in the group"
            )
            notes.append(f"left team {slug}")

    def access(self) -> AccessManager:
        """
        Returns:
            The access manager over this store, which validates and writes a binding.
        """
        from noust.web.permissions.access import AccessManager

        return AccessManager(self.accounts.store)

    def sync_bindings(
        self,
        source: Source,
        groups: set[str],
        account: Account,
        actor: Actor,
        notes: list[str],
    ) -> None:
        """
        Give and take back the roles a mapping holds at a project, an environment or a team.

        A role is taken back only if this source gave it (``created_by``), and one
        given by hand at the same scope is never replaced.

        Args:
            source: The source.
            groups: The person's groups.
            account: The account, with the role it has now.
            actor: Who is acting.
            notes: Filled with the changes.
        """
        if account.role in INSTANCE_ONLY_ROLES:
            return
        wanted = [m for m in source.group_mappings if m.bind and m.group in groups]
        creator = f"{source.protocol}:{source.name}"
        access = self.access()
        mine = [
            b
            for b in access.data.list_bindings(account_id=account.id)
            if b.created_by == creator and b.scope_kind != "instance"
        ]
        if not wanted and not mine:
            return
        desired: dict[tuple[str, int], tuple[str, GroupMapping]] = {}
        for mapping in wanted:
            try:
                scope = access.bound_scope(
                    team=mapping.team, project=mapping.project, environment=mapping.environment
                )
            except (TeamError, ValidationError) as exc:
                notes.append(f"{mapping.bind} for {mapping.group} not given: {exc.message}")
                continue
            if scope is not None and scope not in desired:
                desired[scope] = (mapping.bind or "", mapping)
        held = {
            (b.scope_kind, b.scope_id): b for b in access.data.list_bindings(account_id=account.id)
        }
        for (kind, scope_id), (role, mapping) in sorted(desired.items(), key=lambda x: str(x[0])):
            existing = held.get((kind, scope_id))
            if existing is not None and (existing.created_by != creator or existing.role == role):
                continue
            where = "/".join(p for p in (mapping.team, mapping.project, mapping.environment) if p)
            try:
                access.bind(
                    account.username,
                    role,
                    team=mapping.team,
                    project=mapping.project,
                    environment=mapping.environment,
                    actor=creator,
                )
            except (AccessError, AccountError, TeamError, ValidationError) as exc:
                notes.append(f"{role} on {where} not given: {exc.message}")
                continue
            self.audit("access.bind", actor, account.username, f"{role} on {where}")
            notes.append(f"{role} on {where}")
        for binding in mine:
            if (binding.scope_kind, binding.scope_id) in desired:
                continue
            access.unbind(binding.id)
            self.audit(
                "access.unbind",
                actor,
                account.username,
                f"{binding.role} on {binding.scope or binding.scope_kind}: no longer in the group",
            )
            notes.append(f"lost {binding.role} on {binding.scope or binding.scope_kind}")

    # ---------------------------------------------------------------- audit

    @staticmethod
    def audit(event: str, actor: Actor, target: str, detail: str) -> None:
        """
        Args:
            event: A catalog event.
            actor: Who acted.
            target: What it was done to.
            detail: One sentence.
        """
        audit_record(event, actor=actor, target=f"account:{target}", details={"detail": detail})
