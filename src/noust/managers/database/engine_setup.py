# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Installing an engine: the flavour asked for, the one already there, the plan.

:class:`~noust.managers.database.service.DatabaseService` calls these, and the
CLI, the API and the install job call the service; nothing else decides what
an install does (rule 3). The guard that MySQL and MariaDB, or Redis and
Valkey, are never installed side by side lives here, the one path every
install takes (rule 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.managers.database.base import APT_GET, BaseDatabaseManager
from noust.managers.database.flavours import (
    FLAVOURS,
    RELEASE_NAMES,
    EngineInstalledError,
    InstallPlan,
    catalog,
    conflict_reason,
    distribution,
    plan_install,
    release_of,
    resolve_flavour,
)


@dataclass(frozen=True)
class EnginePlan:
    """
    What an install request comes to, decided before anything is touched.

    Attributes:
        manager: The engine's manager.
        flavour: The flavour asked for; None for the install of 3.2 and
            before (no flavour, no version).
        plan: What will be installed; None when the engine already is.
        installed: The flavour already installed, if any.
    """

    manager: BaseDatabaseManager
    flavour: str | None
    plan: InstallPlan | None
    installed: str | None

    @property
    def already_installed(self) -> bool:
        """Whether the flavour asked for is the one installed."""
        return self.installed is not None

    @property
    def display_name(self) -> str:
        """The flavour's name for a person, or the engine's."""
        if self.flavour in FLAVOURS:
            return FLAVOURS[self.flavour].display_name
        return self.manager.DISPLAY_NAME

    def describe(self) -> str:
        """
        Returns:
            ``PostgreSQL 17``, ``MariaDB``, or the engine's name.
        """
        version = self.plan.version if self.plan is not None and self.flavour else None
        return f"{self.display_name} {version or ''}".strip()


@dataclass(frozen=True)
class InstallOutcome:
    """
    What an install did.

    Attributes:
        engine: The engine.
        display_name: Its name for a person, as it is now installed.
        version: The version that runs.
        already_installed: Nothing was done: it was there.
        plan: The plan followed, None when nothing was done.
        warnings: What the operator must know (upstream support ending,
            MongoDB without authorization).
    """

    engine: str
    display_name: str
    version: str | None
    already_installed: bool
    plan: InstallPlan | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "engine": self.engine,
            "display_name": self.display_name,
            "version": self.version,
            "already_installed": self.already_installed,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "warnings": list(self.warnings),
        }


def plan_engine_install(
    manager: BaseDatabaseManager,
    typed: str,
    *,
    flavour: str | None = None,
    version: str | None = None,
) -> EnginePlan:
    """
    Decide what installing an engine means on this server.

    Args:
        manager: The engine's manager.
        typed: The engine name as the operator typed it (``mariadb`` asks
            for MariaDB, ``valkey`` for Valkey).
        flavour: A flavour named explicitly.
        version: A version asked for.

    Returns:
        The plan, or that the flavour is already installed.

    Raises:
        EngineInstalledError: When the engine's other flavour is installed.
        ValidationError: When the flavour or the version cannot be had here.
    """
    # A container's engine is its image's: refused here, the one path every install takes.
    manager.refuse_in_container("install")
    chosen = resolve_flavour(typed, manager.ENGINE_NAME, flavour=flavour, version=version)
    installed = manager.installed_flavour()
    if installed is not None:
        if chosen is not None and chosen != installed:
            have = FLAVOURS[installed].display_name if installed in FLAVOURS else installed
            want = FLAVOURS[chosen].display_name
            raise EngineInstalledError(
                f"{have} is installed, so {want} cannot be installed",
                details=conflict_reason(have, want),
            )
        return EnginePlan(manager, chosen, None, installed)
    plan = plan_install(chosen, version, distribution()) if chosen else None
    return EnginePlan(manager, chosen, plan, None)


def install_engine(decided: EnginePlan) -> InstallOutcome:
    """
    Install what :func:`plan_engine_install` decided.

    Args:
        decided: The decision.

    Returns:
        What was done.

    Raises:
        DatabaseEngineError: When apt, the repository or the unit fails.
        ValidationError: When the default plan cannot be had here.
    """
    manager = decided.manager
    if decided.already_installed:
        return InstallOutcome(
            engine=manager.ENGINE_NAME,
            display_name=decided.display_name,
            version=manager.get_version(),
            already_installed=True,
        )
    manager.install(decided.plan)
    version = manager.get_version()
    warnings: list[str] = []
    notice = manager.support(version)
    if notice.status in ("ending_soon", "ended"):
        warnings.append(notice.message)
    warnings += manager.warnings()
    return InstallOutcome(
        engine=manager.ENGINE_NAME,
        display_name=manager.DISPLAY_NAME,
        version=version,
        already_installed=False,
        plan=decided.plan,
        warnings=warnings,
    )


def install_catalog(managers: Sequence[BaseDatabaseManager]) -> dict[str, Any]:
    """
    Describe what can be installed on this server, for the install dialog.

    Args:
        managers: One manager per engine.

    Returns:
        The distribution, whether apt is present, and one entry per flavour.
    """
    os_release = distribution()
    installed = {manager.ENGINE_NAME: manager.installed_flavour() for manager in managers}
    apt = bool(managers) and managers[0].runner.exists(APT_GET)
    release = release_of(os_release)
    return {
        "distribution": {
            "id": os_release.id,
            "codename": os_release.codename,
            "name": os_release.pretty_name,
            "known": release in RELEASE_NAMES,
        },
        "apt": apt,
        "flavours": [choice.to_dict() for choice in catalog(os_release, installed, apt=apt)],
    }
