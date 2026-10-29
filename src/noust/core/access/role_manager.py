# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Define, change, duplicate, put back and delete roles: the one place the rules about roles live.

:class:`~noust.core.access.roles.RoleStore` stores. This decides (rule 4: the CLI, the console and
the identity providers all reach it, and none repeats a rule):

- **The owner is protected.** It is not edited, duplicated over, put back or deleted: it holds
  every permission there is so that a server is never left without somebody who can do everything.
  That every server also keeps an account that has it is
  :class:`~noust.core.accounts.manager.AccountManager`'s guard, where the accounts are written.
- **A role Noust ships can be edited, duplicated and put back**, never deleted. Edited, it is the
  operator's: it receives nothing in silence from a newer release, and offers are made
  (:meth:`RoleManager.settle_offers`). Put back, it follows Noust again.
- **A role the operator defines** has a slug of its own (never a shipped name) and is deleted only
  when nobody holds it, at the whole server or at a team, a project or an environment.
- **What a role may hold** is permissions that exist, and, where a framework keeps governing and
  running apart, not both (:mod:`noust.core.access.combination`), for the role and for every
  account that holds it.

Whoever calls passes ``actor`` (a label for the record); the audit event is the caller's, as it is
for every other change in ``access``.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.access.builtin import ROLE_OWNER, SHIPPED, separation_conflict
from noust.core.access.combination import (
    refuse_conflicting_definition,
    refuse_conflicting_roles,
)
from noust.core.access.model import (
    MAX_DESCRIPTION,
    MAX_LABEL,
    MAX_PERMISSIONS,
    MAX_ROLES,
    AccessConflictError,
    AccessError,
    clean_text,
    validate_role_name,
)
from noust.core.access.roles import (
    KIND_CUSTOM,
    KIND_SEEDED,
    Holder,
    RoleRecord,
    RoleStore,
)
from noust.core.exceptions import ValidationError
from noust.core.permission_catalog import ALL_PERMISSIONS, DESCRIPTIONS, Permission
from noust.core.server_text import say

if TYPE_CHECKING:
    from noust.core.store import NoustStore


@dataclass(frozen=True)
class RoleView:
    """
    A role with what a screen shows beside it.

    Attributes:
        role: The role.
        holders: The accounts that hold it.
        bindings: How many bindings at a team, a project or an environment name it.
        governing: The permissions that govern the server it holds, when it also runs it.
        running: The permissions that run the server it holds, when it also governs it.
    """

    role: RoleRecord
    holders: tuple[Holder, ...]
    bindings: int
    governing: tuple[str, ...] = ()
    running: tuple[str, ...] = ()

    @property
    def joins_duties(self) -> bool:
        """Whether it joins governing the server with running it."""
        return bool(self.governing and self.running)

    @property
    def can_edit(self) -> bool:
        """Whether its permissions, label and description may change."""
        return not self.role.protected

    @property
    def can_delete(self) -> bool:
        """Whether it may be deleted (when nobody holds it)."""
        return self.role.kind == KIND_CUSTOM

    @property
    def can_reset(self) -> bool:
        """Whether it can go back to how Noust ships it: it is one Noust ships and differs."""
        shipped = SHIPPED.get(self.role.name)
        return (
            self.role.kind == KIND_SEEDED
            and shipped is not None
            and (
                self.role.edited
                or self.role.label != shipped.label
                or self.role.description != shipped.description
            )
        )

    @property
    def can_duplicate(self) -> bool:
        """Whether it can be copied into a role of one's own."""
        return True

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The role and what goes with it, as the API and ``--json`` show it.
        """
        return {
            **self.role.to_dict(),
            "holders": [holder.username for holder in self.holders],
            "holder_count": len(self.holders),
            "bindings": self.bindings,
            "joins_duties": self.joins_duties,
            "governing": list(self.governing),
            "running": list(self.running),
            "can_edit": self.can_edit,
            "can_delete": self.can_delete,
            "can_reset": self.can_reset,
            "can_duplicate": self.can_duplicate,
        }


class RoleManager:
    """
    The list of roles, and the rules for changing it.

    Args:
        store: The store; the process-wide one by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], float] | None = None
    ) -> None:
        self._store = store
        self._clock = clock or time.time
        self.roles = RoleStore(store)

    # --------------------------------------------------------------------- reading

    def view(self, name: str) -> RoleView:
        """
        Args:
            name: A role.

        Returns:
            The role with its holders and what it joins.

        Raises:
            AccessNotFoundError: When there is none.
        """
        return self._view(self.roles.require(name))

    def views(self) -> list[RoleView]:
        """
        Returns:
            Every role with its holders and what it joins, in the order of the list.
        """
        return [self._view(role) for role in self.roles.list_roles()]

    def _view(self, role: RoleRecord) -> RoleView:
        conflict = separation_conflict(role.held) if not role.protected else None
        governing, running = conflict if conflict is not None else ([], [])
        return RoleView(
            role=role,
            holders=tuple(self.roles.holders(role.name)),
            bindings=self.roles.bound_count(role.name),
            governing=tuple(governing),
            running=tuple(running),
        )

    @staticmethod
    def catalog() -> dict[str, str]:
        """
        Returns:
            Every permission there is with what it allows, by name.
        """
        return {name: DESCRIPTIONS.get(name, "") for name in sorted(ALL_PERMISSIONS)}

    # --------------------------------------------------------------------- writing

    def create(
        self,
        name: str,
        permissions: Iterable[str],
        *,
        label: str | None = None,
        description: str | None = None,
        actor: str | None = None,
    ) -> RoleRecord:
        """
        Define a role of one's own.

        Args:
            name: Its slug.
            permissions: What it holds.
            label: What the console shows; the name by default.
            description: What it is for.
            actor: Who defines it.

        Returns:
            The role.

        Raises:
            ValidationError: For a name or a permission that is not valid.
            AccessError: When the set joins governing with running where a framework forbids it.
            AccessConflictError: When the name is taken or there are too many roles.
        """
        slug = validate_role_name(name)
        held = self._validated(permissions, f"The role {slug}")
        if self.roles.count_custom() >= MAX_ROLES:
            raise AccessConflictError.from_text(say("roles.too_many", max=MAX_ROLES, command=_LIST))
        try:
            self.roles.insert_custom(
                slug,
                clean_text(label, limit=MAX_LABEL, what="label") or slug,
                clean_text(description, limit=MAX_DESCRIPTION, what="description"),
                held,
                now=self._clock(),
                actor=actor,
            )
        except sqlite3.IntegrityError as exc:
            raise AccessConflictError.from_text(
                say("roles.name_taken", name=slug, command=f"noust access role show {slug}"),
                field="name",
            ) from exc
        return self.roles.require(slug)

    def update(
        self,
        name: str,
        *,
        permissions: Iterable[str] | None = None,
        label: str | None = None,
        description: str | None = None,
        actor: str | None = None,
    ) -> RoleRecord:
        """
        Change a role; every account that holds it holds the change at its next request.

        A role Noust ships becomes the operator's the first time its permissions change: it
        stops following Noust, and what a newer release ships for it is offered from then on.
        Changing only its label or description leaves it following Noust.

        Args:
            name: The role.
            permissions: Its new permissions, when they change.
            label: Its new label.
            description: Its new description.
            actor: Who changes it.

        Returns:
            The role as it is now.

        Raises:
            AccessNotFoundError: When there is no such role.
            AccessError: When it is the owner, or the change would join duties for the role or for
                an account that holds it.
        """
        current = self.roles.require(name)
        self._refuse_owner(current)
        held = (
            self._validated(permissions, f"The role {current.name}")
            if permissions is not None
            else None
        )
        new_label = (
            current.label
            if label is None
            else clean_text(label, limit=MAX_LABEL, what="label") or current.name
        )
        new_description = (
            current.description
            if description is None
            else clean_text(description, limit=MAX_DESCRIPTION, what="description")
        )
        if held is not None and frozenset(held) != current.held:
            self._check_holders(current.name, frozenset(held))
        return self._save(current, new_label, new_description, held, actor=actor)

    def _save(
        self,
        current: RoleRecord,
        label: str,
        description: str,
        permissions: list[str] | None,
        *,
        actor: str | None,
    ) -> RoleRecord:
        """
        Write a role's definition, working out whether a role Noust ships stays following it.

        Args:
            current: The role as it is.
            label: Its label.
            description: Its description.
            permissions: Its new permissions, or None to keep what it holds.
            actor: Who changes it.

        Returns:
            The role as it is now.
        """
        held = frozenset(permissions) if permissions is not None else current.held
        shipped = current.shipped
        base: frozenset[str] | None = None
        edited = False
        if current.kind == KIND_SEEDED and shipped is not None:
            if current.follows_noust and held == current.held:
                # Only the words changed: it still follows Noust, and holds what Noust ships.
                held = shipped
            else:
                # The operator has looked at this release's version of the role: what it ships
                # beyond what they keep is a decision made, not something new to offer.
                base = shipped
                default = SHIPPED[current.name]
                edited = not (
                    held == shipped
                    and label == default.label
                    and description == default.description
                )
        self.roles.save(
            current.name,
            label=label,
            description=description,
            permissions=held,
            base=base,
            edited=edited,
            now=self._clock(),
            actor=actor,
        )
        return self.roles.require(current.name)

    def duplicate(
        self, name: str, new_name: str, *, label: str | None = None, actor: str | None = None
    ) -> RoleRecord:
        """
        Copy a role into a role of one's own.

        Args:
            name: The role to copy; any, the owner included.
            new_name: The copy's slug.
            label: Its label; the original's with " (copy)" by default.
            actor: Who copies it.

        Returns:
            The copy.

        Raises:
            AccessNotFoundError: When there is no such role.
            ValidationError: For a name that is not valid.
            AccessConflictError: When the name is taken or there are too many roles.
            AccessError: When the copy would join duties where a framework forbids it.
        """
        source = self.roles.require(name)
        wanted = clean_text(label, limit=MAX_LABEL, what="label") if label else None
        return self.create(
            new_name,
            source.held - {Permission.SELF},
            label=wanted or f"{source.label} (copy)"[:MAX_LABEL],
            description=source.description,
            actor=actor,
        )

    def reset(self, name: str, *, actor: str | None = None) -> RoleRecord:
        """
        Put a role Noust ships back to how it comes.

        Args:
            name: The role.
            actor: Who puts it back.

        Returns:
            The role as it is now: following Noust again.

        Raises:
            AccessNotFoundError: When there is no such role.
            AccessError: When it is the owner or a role of one's own, or putting it back would join
                duties for an account that holds it.
        """
        current = self.roles.require(name)
        self._refuse_owner(current)
        if current.kind != KIND_SEEDED:
            raise AccessError.from_text(
                say(
                    "roles.reset_custom",
                    name=current.name,
                    command=f"noust access role remove {current.name}",
                )
            )
        shipped = SHIPPED[current.name]
        if current.edited:
            self._check_holders(current.name, shipped.permissions)
        self.roles.save(
            current.name,
            label=shipped.label,
            description=shipped.description,
            permissions=shipped.permissions,
            base=[],
            edited=False,
            now=self._clock(),
            actor=actor,
        )
        return self.roles.require(current.name)

    def delete(self, name: str) -> RoleRecord:
        """
        Delete a role of one's own that nobody holds.

        Args:
            name: The role.

        Returns:
            The role as it was.

        Raises:
            AccessNotFoundError: When there is none.
            AccessError: When it is one Noust ships (it can be put back, not deleted).
            AccessConflictError: While an account holds it, or a binding names it.
        """
        current = self.roles.require(name)
        self._refuse_owner(current)
        if current.kind != KIND_CUSTOM:
            raise AccessError.from_text(
                say(
                    "roles.seeded_kept",
                    name=current.name,
                    command=f"noust access role reset {current.name}",
                )
            )
        count = len(self.roles.holders(current.name)) + self.roles.bound_count(current.name)
        if count:
            raise AccessConflictError.from_text(
                say(
                    "roles.in_use",
                    name=current.name,
                    count=count,
                    command=f"noust access role show {current.name}",
                )
            )
        self.roles.delete_custom(current.name)
        return current

    def settle_offers(
        self,
        name: str,
        *,
        accept: Iterable[str] = (),
        dismiss: Iterable[str] = (),
        actor: str | None = None,
    ) -> RoleRecord:
        """
        Take or leave the permissions a newer release ships for a role the operator edited.

        Args:
            name: The role.
            accept: Offered permissions to add to it.
            dismiss: Offered permissions to leave out, which stops them being offered.
            actor: Who decides.

        Returns:
            The role as it is now.

        Raises:
            AccessNotFoundError: When there is no such role.
            AccessError: When a permission named is not on offer, or taking them would join duties
                for the role or for an account that holds it.
        """
        current = self.roles.require(name)
        self._refuse_owner(current)
        offered = set(current.offers)
        taking, leaving = {str(p).strip() for p in accept}, {str(p).strip() for p in dismiss}
        for permission in sorted((taking | leaving) - offered):
            raise AccessError.from_text(
                say(
                    "roles.offer_unknown",
                    permission=permission,
                    name=current.name,
                    command=f"noust access role show {current.name}",
                ),
                field="permission",
            )
        held = current.held | taking
        if taking:
            refuse_conflicting_definition(f"The role {current.name}", held)
            self._check_holders(current.name, held)
        self.roles.save(
            current.name,
            label=current.label,
            description=current.description,
            permissions=held,
            base=frozenset(current.base) | taking | leaving,
            edited=True,
            now=self._clock(),
            actor=actor,
        )
        return self.roles.require(current.name)

    # ----------------------------------------------------------------------- rules

    @staticmethod
    def _refuse_owner(role: RoleRecord) -> None:
        """
        Args:
            role: The role about to change.

        Raises:
            AccessError: When it is the owner.
        """
        if role.name == ROLE_OWNER:
            raise AccessError.from_text(say("roles.owner_protected"))

    @staticmethod
    def _validated(permissions: Iterable[str] | None, subject: str) -> list[str]:
        """
        Check the permissions of a role.

        Args:
            permissions: Permission names as typed.
            subject: What holds them, for the message.

        Returns:
            The set, sorted, with ``self`` (everyone's own session) included.

        Raises:
            ValidationError: When one does not exist, there are none, or too many.
            AccessError: When the set joins governing with running where a framework forbids it.
        """
        cleaned = {str(name).strip() for name in permissions or () if str(name).strip()}
        unknown = sorted(cleaned - ALL_PERMISSIONS)
        if unknown:
            raise ValidationError.from_text(
                say(
                    "roles.permissions_unknown",
                    names=", ".join(unknown),
                    command="noust access permissions",
                ),
                field="permissions",
            )
        cleaned.discard(Permission.SELF)
        if not cleaned:
            raise ValidationError.from_text(say("roles.permissions_none"), field="permissions")
        if len(cleaned) > MAX_PERMISSIONS:
            raise ValidationError.from_text(
                say("roles.permissions_many", max=MAX_PERMISSIONS), field="permissions"
            )
        refuse_conflicting_definition(subject, cleaned)
        return sorted(cleaned | {Permission.SELF})

    def _check_holders(self, role: str, permissions: frozenset[str]) -> None:
        """
        Refuse a new definition that would put an account at the wrong side of a control.

        Args:
            role: The role being redefined.
            permissions: What it would hold.

        Raises:
            AccessError: When an account that holds it would then join duties.
        """
        table = {**self.roles.permission_map(), role: permissions}
        for holder in self.roles.holders(role):
            names = self.roles.roles_of(holder.account_id)
            refuse_conflicting_roles(holder.username, names or [role], table)


#: The command that lists the roles, said by the errors that point at it.
_LIST = "noust access role list"
