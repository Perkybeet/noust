# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Choosing frameworks and controls: what changing them does, and doing it.

``noust compliance``, ``noust ens``, ``/api/compliance`` and the console's
Settings > Security > Compliance all call :class:`ComplianceManager`: the one
implementation of enabling a framework, setting a control, what that would cost the
people who use the server, and what is recorded (rule 3).

A change is first *planned* (:meth:`ComplianceManager.plan`): which frameworks move,
which controls change value and what each ends up as, whether it loosens anything
(the caller asks for sudo mode then, and only then), and what it would do to this
particular server, such as leaving nobody who can govern accounts. Whatever needs a
person's attention is a *warning*, and the change is applied only once they
acknowledged it (:meth:`ComplianceManager.apply`). Tightening - a framework on, a
control stricter - never asks for more than the permission to change security
settings.

The rules, enforced here and not by the callers (rule 4):

- A control cannot be set looser than an enabled framework asks.
- A control cannot be set while naming nothing it knows.
- Enabling ENS MEDIA also writes ``security.profile: ens-medium`` and disabling it
  writes ``standard``, so the setting from before frameworks never disagrees with
  the list and a Noust older than this one reads the same server the same way.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from noust.core.compliance import controls, frameworks, resolve
from noust.core.ens import profile
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.store import StoreError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Effect:
    """
    What a change does to one control.

    Attributes:
        control: The control's id.
        before: The value in force now.
        after: The value in force once the change is made.
        forced_by: The frameworks that will ask anything of it.
        clauses: For each of those frameworks, the clauses it serves.
    """

    control: str
    before: Any
    after: Any
    forced_by: tuple[str, ...] = ()
    clauses: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The effect, JSON-ready.
        """
        return {
            "control": self.control,
            "before": self.before,
            "after": self.after,
            "forced_by": list(self.forced_by),
            "clauses": self.clauses,
        }


@dataclass(frozen=True)
class Warning:
    """
    Something about this server a change would affect, which a person must see first.

    Attributes:
        id: What it is; the console words it.
        count: How many accounts or settings it concerns, when it counts them.
    """

    id: str
    count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The warning, JSON-ready.
        """
        return {"id": self.id, "count": self.count}


@dataclass(frozen=True)
class Plan:
    """
    What a requested change would do.

    Attributes:
        enable: Frameworks to turn on that are not on.
        disable: Frameworks to turn off that are on.
        settings: Control id to the value its setting will hold, for the ones
            that move.
        enabled_after: The frameworks on once it is made.
        effects: The controls whose value in force changes.
        loosens: Whether anything protects less afterwards.
        warnings: What a person must acknowledge.
    """

    enable: tuple[str, ...] = ()
    disable: tuple[str, ...] = ()
    settings: dict[str, Any] = field(default_factory=dict)
    enabled_after: tuple[str, ...] = ()
    effects: tuple[Effect, ...] = ()
    loosens: bool = False
    warnings: tuple[Warning, ...] = ()

    @property
    def empty(self) -> bool:
        """Whether the request changes nothing."""
        return not (self.enable or self.disable or self.settings)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan, JSON-ready.
        """
        return {
            "enable": list(self.enable),
            "disable": list(self.disable),
            "settings": self.settings,
            "enabled_after": list(self.enabled_after),
            "effects": [effect.to_dict() for effect in self.effects],
            "loosens": self.loosens,
            "warnings": [item.to_dict() for item in self.warnings],
        }


@dataclass(frozen=True)
class Applied:
    """
    What a change did.

    Attributes:
        enabled_before: The frameworks on before.
        enabled_after: The frameworks on after.
        settings: Control id to the value its setting now holds, for the ones changed.
    """

    enabled_before: tuple[str, ...]
    enabled_after: tuple[str, ...]
    settings: dict[str, Any]


def _known(names: Iterable[str], what: str) -> list[str]:
    """
    Args:
        names: Framework ids as typed.
        what: The request field, for the message.

    Returns:
        The ids, lower-cased and once each.

    Raises:
        ValidationError: When one is not a framework.
    """
    found: list[str] = []
    for item in names:
        name = str(item).strip().lower()
        if not frameworks.exists(name):
            raise ValidationError(
                f"Unknown compliance framework: {item!r}",
                details=f"The frameworks are: {', '.join(frameworks.FRAMEWORK_IDS)}.",
                field=what,
            )
        if name not in found:
            found.append(name)
    return found


class ComplianceManager:
    """
    Read and change the compliance frameworks and controls.

    Args:
        config: The configuration; the process-wide one by default.
        store: The store, for the accounts a change would affect.
    """

    def __init__(self, config: Any | None = None, store: Any | None = None) -> None:
        self._config = config
        self._store = store

    @property
    def config(self) -> Any:
        """The configuration the frameworks and the controls live in."""
        if self._config is None:
            from noust.core.config import Config

            self._config = Config()
        return self._config

    # ------------------------------------------------------------------ state

    def enabled(self) -> list[str]:
        """
        Returns:
            The frameworks on, in catalog order.
        """
        return resolve.enabled_ids(self.config)

    def describe(self) -> dict[str, Any]:
        """
        The whole picture, for the console and ``--json``.

        Returns:
            ``frameworks`` (each with whether it is on, why, and what enabling it
            would change), ``controls`` (each as it stands, with which framework
            forces it), ``legacy_profile`` and any unknown name in the list.
        """
        on = self.enabled()
        explicit = set(resolve.listed(self.config))
        current = {r.control.id: r for r in resolve.resolve_all(self.config)}
        listing = []
        for framework in frameworks.FRAMEWORKS:
            enabled = framework.id in on
            # On because the list says so, or only because security.profile does.
            source = ("setting" if framework.id in explicit else "profile") if enabled else None
            changes = [] if enabled else self._changes_of(framework, current)
            listing.append(
                {
                    **{k: v for k, v in framework.to_dict().items() if k != "requirements"},
                    "enabled": enabled,
                    "source": source,
                    "changes": changes,
                    "requirement_count": len(framework.all_requirements()),
                }
            )
        return {
            "frameworks": listing,
            "controls": [item.to_dict() for item in current.values()],
            "legacy_profile": profile.read_config(self.config, "security.profile", "standard"),
            "unknown": resolve.unknown_names(self.config),
        }

    @staticmethod
    def _changes_of(
        framework: frameworks.Framework, current: Mapping[str, resolve.Resolution]
    ) -> list[dict[str, Any]]:
        """
        Args:
            framework: A framework that is not on.
            current: Every control as it stands.

        Returns:
            What enabling it would change: for each control it asks something
            stricter than the value in force, from what to what and which clauses.
        """
        found = []
        for control in controls.CONTROLS:
            required = framework.requirement(control.id)
            if required is None:
                continue
            now = current[control.id].value
            if not control.satisfies(now, required.value):
                found.append(
                    {
                        "control": control.id,
                        "before": now,
                        "after": required.value,
                        "clauses": [clause.id for clause in required.clauses],
                    }
                )
        return found

    # ------------------------------------------------------------------- plan

    def plan(
        self,
        *,
        enable: Iterable[str] = (),
        disable: Iterable[str] = (),
        settings: Mapping[str, Any] | None = None,
    ) -> Plan:
        """
        Work out what a requested change would do, without doing it.

        Args:
            enable: Frameworks to turn on.
            disable: Frameworks to turn off.
            settings: Control id to the value wanted for its setting.

        Returns:
            The plan.

        Raises:
            ValidationError: For a framework or a control that does not exist, a
                value a control does not accept, a framework both enabled and
                disabled, or a control set looser than an enabled framework asks.
        """
        on_now = self.enabled()
        asked_on = _known(enable, "enable")
        asked_off = _known(disable, "disable")
        clash = set(asked_on) & set(asked_off)
        if clash:
            raise ValidationError(
                f"{', '.join(sorted(clash))} cannot be enabled and disabled at once", field="enable"
            )
        to_enable = [name for name in asked_on if name not in on_now]
        to_disable = [name for name in asked_off if name in on_now]
        after = resolve.ordered([*(name for name in on_now if name not in to_disable), *to_enable])

        moves: dict[str, Any] = {}
        loosens = bool(to_disable)
        before_resolutions = {r.control.id: r for r in resolve.resolve_all(self.config)}
        for control_id, raw in (settings or {}).items():
            if not controls.exists(control_id):
                raise ValidationError(
                    f"Unknown control: {control_id!r}",
                    details=f"The controls are: {', '.join(controls.CONTROL_IDS)}.",
                    field="controls",
                )
            control = controls.get(control_id)
            try:
                wanted = control.coerce(raw)
            except ValueError as exc:
                raise ValidationError(str(exc), field=f"controls.{control_id}") from exc
            floor = resolve.floor_for(control_id, after)
            if not control.satisfies(wanted, floor):
                forced = [name for name, _ in resolve.requirements_of(control_id, after)]
                raise ValidationError(
                    f"{control_id} cannot be set looser than {control.format(floor)}: "
                    f"{', '.join(forced)} asks for it",
                    details="Disable the framework first, or pick a value at least that strict.",
                    field=f"controls.{control_id}",
                )
            own = before_resolutions[control_id].operator
            if control.stored(wanted) != control.stored(own):
                moves[control_id] = control.stored(wanted)
                loosens = loosens or control.rank(wanted) < control.rank(own)

        effects = self._effects(before_resolutions, after, moves)
        return Plan(
            enable=tuple(to_enable),
            disable=tuple(to_disable),
            settings=moves,
            enabled_after=tuple(after),
            effects=tuple(effects),
            loosens=loosens,
            warnings=tuple(self._warnings(effects)),
        )

    @staticmethod
    def _effects(
        before: Mapping[str, resolve.Resolution], after_ids: list[str], moves: Mapping[str, Any]
    ) -> list[Effect]:
        """
        Args:
            before: Every control as it stands.
            after_ids: The frameworks on afterwards.
            moves: The settings that change.

        Returns:
            The controls whose value in force changes, with what asks for them.
        """
        found = []
        for control in controls.CONTROLS:
            old = before[control.id]
            own = control.coerce(moves[control.id]) if control.id in moves else old.operator
            new = resolve.resolve_for(control.id, after_ids, own, has_operator=True)
            if new.value == old.value:
                # Who forces a control is shown with the control; only a change of value is an effect.
                continue
            found.append(
                Effect(
                    control.id,
                    old.value,
                    new.value,
                    new.forced_by,
                    {
                        name: [clause.id for clause in required.clauses]
                        for name, required in resolve.requirements_of(control.id, after_ids)
                    },
                )
            )
        return found

    def _warnings(self, effects: list[Effect]) -> list[Warning]:
        """
        Args:
            effects: The controls that change.

        Returns:
            What the change would do to this server that a person must see.
        """
        tightened = {
            effect.control
            for effect in effects
            if controls.get(effect.control).rank(effect.after)
            > controls.get(effect.control).rank(effect.before)
        }
        found: list[Warning] = []
        if not tightened:
            return found
        accounts = self._accounts()
        if accounts is None:
            return found
        from noust.core.accounts.model import ROLE_SECURITY

        officers = [a for a in accounts if a.role == ROLE_SECURITY and a.can_sign_in()]
        if accounts and not officers and tightened & {"roles_split", "approvals"}:
            found.append(Warning("no_security_officer"))
        if "separation_of_duties" in tightened:
            conflicts = self._conflicts()
            if conflicts:
                found.append(Warning("separation_conflicts", conflicts))
        return found

    def _accounts(self) -> list[Any] | None:
        """
        Returns:
            Every account, or None when the store cannot be read (a warning is
            about what is known; an unreadable store is no reason to refuse).
        """
        from noust.core.accounts import AccountManager

        try:
            return AccountManager(self._store).list_all()
        except (StoreError, sqlite3.Error) as exc:
            # Reporting only: an unreadable store is no reason to refuse a change.
            logger.warning("Could not read the accounts to judge a compliance change: %s", exc)
            return None

    def _conflicts(self) -> int:
        """
        Returns:
            How many people hold incompatible accounts, whether or not the
            separation of duties is enforced yet.
        """
        from noust.core.accounts import AccountManager

        try:
            return len(AccountManager(self._store).separation_conflicts(always=True))
        except (StoreError, sqlite3.Error) as exc:
            logger.warning("Could not count the separation-of-duties conflicts: %s", exc)
            return 0

    # ------------------------------------------------------------------ apply

    def apply(self, change: Plan, *, acknowledge: bool = False) -> Applied:
        """
        Make a planned change: write the settings, and nothing else.

        The caller has already asked for sudo mode when the plan loosens
        something, and records the audit events with the actor it knows.

        Args:
            change: A plan from :meth:`plan`.
            acknowledge: The person saw the warnings and goes ahead.

        Returns:
            What changed.

        Raises:
            ValidationError: When the plan has warnings and they were not
                acknowledged.
            ConfigError: When the configuration cannot be written.
        """
        if change.warnings and not acknowledge:
            raise ValidationError(
                "This change needs your confirmation",
                details="; ".join(item.id for item in change.warnings)
                + ". Go through them, then confirm.",
                field="acknowledge",
            )
        config = self.config
        before = tuple(self.enabled())
        reload = getattr(config, "reload", None)
        if callable(reload):
            reload()
        # The list alone says the whole truth afterwards: a server enabled by the old
        # setting moves onto it the first time it is changed.
        config.set(resolve.SETTING_FRAMEWORKS, list(change.enabled_after))
        # The setting from before frameworks mirrors ENS MEDIA, so a Noust that does not
        # know the list reads this server the same way.
        config.set(
            "security.profile",
            profile.PROFILE_ENS_MEDIUM
            if frameworks.LEGACY_PROFILE_FRAMEWORK in change.enabled_after
            else profile.PROFILE_STANDARD,
        )
        for control_id, value in change.settings.items():
            config.set(controls.get(control_id).key, value)
        try:
            config.write()
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"The configuration could not be written: {exc}",
                details="Check that the configuration file is writable by the console.",
            ) from exc
        return Applied(
            enabled_before=before,
            enabled_after=tuple(change.enabled_after),
            settings=dict(change.settings),
        )
