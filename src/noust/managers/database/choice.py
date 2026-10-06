# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which engine, in which version, a new application's database is made on.

"A PostgreSQL 17 database for this application" is how an operator thinks, not
"install the engine, then create a database on it". This is the one place that
turns that sentence into what Noust does (rule 3), for the CLI, the API and the
new-application wizard alike:

- the engine and the version asked for are **resolved** against what the server
  has: the flavour (MariaDB is not MySQL, Valkey is not Redis), the version that
  runs, and the versions the catalog says this release can be given;
- when the engine is there in that version, it is used, exactly as before;
- when it is not there but can be installed in that version, the choice says so
  and, only if the caller allowed it, installing is part of creating the
  database. Installing packages is never a side effect nobody asked for;
- when the version cannot be had, the error says which versions can.

Noust runs one instance of each engine on a host (several side by side is the
container engine work of 3.4, checklist C-03), so asking for another version of
an engine that is installed is refused with what runs, not silently answered with
what runs.

With no version and no flavour named, nothing here changes what 3.3 did: the
engine that is installed is the one used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.managers.database.engine_setup import EnginePlan, plan_engine_install
from noust.managers.database.flavours import (
    FLAVOURS,
    InstallPlan,
    catalog,
    distribution,
    resolve_flavour,
)

if TYPE_CHECKING:
    from noust.managers.database.base import BaseDatabaseManager
    from noust.managers.database.service import DatabaseService

#: A version as an operator types it: ``17``, ``11.4``, ``8.0.36``.
_VERSION = re.compile(r"\d{1,3}(?:\.\d{1,3}){0,2}")

Action = Literal["use", "install"]


def parse_version(version: str | None) -> str | None:
    """
    Check a version an operator asked for.

    Args:
        version: As typed; None or blank for none.

    Returns:
        The version stripped, or None when none was asked for.

    Raises:
        ValidationError: When it is not numbers and dots.
    """
    text = (version or "").strip()
    if not text:
        return None
    if not _VERSION.fullmatch(text):
        raise ValidationError(
            f"{version!r} is not a database version",
            details="A version is numbers and dots, such as 17, 11.4 or 8.0.",
            field="version",
        )
    return text


def version_matches(asked: str, running: str | None) -> bool:
    """
    Tell whether a running version is the one that was asked for.

    The asked version is a prefix by components: ``17`` is any 17.x, ``11.4`` is
    any 11.4.x, and ``11`` does not match 10.11.

    Args:
        asked: The version asked for, already checked.
        running: The version the engine reports, such as ``17.2``.

    Returns:
        True when the running version starts with the asked components.
    """
    if not running:
        return False
    wanted = asked.split(".")
    have = running.split(".")
    return have[: len(wanted)] == wanted


@dataclass(frozen=True)
class EngineChoice:
    """
    What an engine and a version asked for come to on this server.

    Attributes:
        asked_engine: The engine as typed (``mariadb``, ``postgresql``).
        engine: The canonical engine the manager answers to.
        flavour: The flavour asked for or installed (``mariadb``, ``valkey``).
        display_name: How it is named to a person, with its version.
        version: The version asked for, None for any.
        running_version: The version of the installed engine, None when none is.
        action: ``use`` the engine that is there, or ``install`` it first.
        plan: What installing would do, when ``action`` is ``install``.
    """

    asked_engine: str
    engine: str
    flavour: str | None
    display_name: str
    version: str | None
    running_version: str | None
    action: Action
    plan: InstallPlan | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "engine": self.engine,
            "flavour": self.flavour,
            "display_name": self.display_name,
            "version": self.version,
            "running_version": self.running_version,
            "action": self.action,
            "plan": self.plan.to_dict() if self.plan is not None else None,
        }


def _named(manager: BaseDatabaseManager, flavour: str | None, version: str | None) -> str:
    """
    Name an engine for a person.

    Args:
        manager: The engine's manager.
        flavour: A flavour, or None.
        version: A version, or None.

    Returns:
        ``PostgreSQL 17``, ``MariaDB``, ``Redis``.
    """
    base = FLAVOURS[flavour].display_name if flavour in FLAVOURS else manager.DISPLAY_NAME
    return f"{base} {version}".strip() if version else base


def resolve_choice(
    service: DatabaseService,
    engine: str,
    *,
    version: str | None = None,
    flavour: str | None = None,
    install: bool = False,
    install_hint: str = "",
) -> EngineChoice:
    """
    Decide what creating a database on an engine in a version means here.

    Reads the machine (what is installed, which version runs) and the catalog;
    changes nothing.

    Args:
        service: The database service, which resolves engines.
        engine: The engine as typed: a name or an alias (``mariadb``, ``pg``).
        version: The version asked for, None for whatever is installed.
        flavour: A flavour named explicitly, when the name is not enough.
        install: The caller allows installing the engine when it is missing.
        install_hint: How this caller says "install it as part of this", for the
            error that refuses a missing engine (``add --install``).

    Returns:
        The decision. An engine in a container is its image's: it is always
        used as it is, and a version asked for is refused.

    Raises:
        ValidationError: When the version is malformed, cannot be had on this
            release, or belongs to another flavour than the installed one.
        DatabaseEngineError: When the engine is unknown, or is missing and may
            not be installed.
    """
    asked = parse_version(version)
    manager = service.manager(engine)
    if manager.instance is not None:
        if asked is not None or flavour:
            raise ValidationError(
                "A database container's version is its image's",
                details="Noust does not choose the engine or the version of a container: "
                "its image and its compose file do.",
                field="version",
            )
        return EngineChoice(
            asked_engine=engine,
            engine=manager.ENGINE_NAME,
            flavour=None,
            display_name=manager.DISPLAY_NAME,
            version=None,
            running_version=None,
            action="use",
        )

    chosen = resolve_flavour(engine, manager.ENGINE_NAME, flavour=flavour, version=asked)
    if manager.is_installed():
        return _installed_choice(manager, engine, chosen, asked)
    return _missing_choice(manager, engine, chosen, asked, install=install, hint=install_hint)


def _installed_choice(
    manager: BaseDatabaseManager, typed: str, chosen: str | None, asked: str | None
) -> EngineChoice:
    """
    Decide for an engine that is installed.

    Args:
        manager: Its manager.
        typed: The engine as typed.
        chosen: The flavour asked for, or None.
        asked: The version asked for, or None.

    Returns:
        The decision to use it.

    Raises:
        ValidationError: When the flavour or the version is not the one that runs.
    """
    present = manager.installed_flavour()
    running = manager.get_version()
    if chosen is not None and present is not None and chosen != present:
        wanted = FLAVOURS[chosen].display_name
        have = FLAVOURS[present].display_name if present in FLAVOURS else present
        raise ValidationError(
            f"{wanted} was asked for, but this server runs {_named(manager, present, running)}",
            details=f"{have} and {wanted} cannot run side by side here (they share their port "
            "and their data directory): create the database on the one that runs, or replace "
            "it first.",
            field="engine",
        )
    if asked is not None and not version_matches(asked, running):
        raise ValidationError(
            f"{_named(manager, present, asked)} was asked for, but this server runs "
            f"{_named(manager, present, running)}",
            details=f"Noust runs one {manager.DISPLAY_NAME} per server, so a database is created "
            f"on the version that runs. Create it there (leave the version out, or ask for "
            f"{(running or '').split('.')[0] or 'the version that runs'}). Running several "
            "versions side by side is planned.",
            field="version",
        )
    return EngineChoice(
        asked_engine=typed,
        engine=manager.ENGINE_NAME,
        flavour=present,
        display_name=_named(manager, present, running),
        version=asked,
        running_version=running,
        action="use",
    )


def _missing_choice(
    manager: BaseDatabaseManager,
    typed: str,
    chosen: str | None,
    asked: str | None,
    *,
    install: bool,
    hint: str,
) -> EngineChoice:
    """
    Decide for an engine that is not installed.

    Args:
        manager: Its manager.
        typed: The engine as typed.
        chosen: The flavour asked for, or None.
        asked: The version asked for, or None.
        install: Whether installing it is allowed.
        hint: How the caller allows installing, for the error that refuses it.

    Returns:
        The decision to install it.

    Raises:
        ValidationError: When the version cannot be had on this release; the
            error lists the ones that can.
        DatabaseEngineError: When it is missing and installing is not allowed.
    """
    try:
        decided: EnginePlan = plan_engine_install(manager, typed, flavour=chosen, version=asked)
    except ValidationError as exc:
        offered = _offered(chosen or manager.ENGINE_NAME)
        details = exc.details or ""
        if offered:
            details = f"{details} Versions this server can be given: {', '.join(offered)}.".strip()
        raise ValidationError(exc.message, details=details, field="version") from exc
    shown = _named(manager, chosen, asked)
    if not install:
        flag = f" --version {asked}" if asked else ""
        name = chosen or manager.ENGINE_NAME
        raise DatabaseEngineError(
            f"{shown} is not installed",
            details=f"Install it with: noust db install {name}{flag}"
            + (f", or {hint}." if hint else "."),
        )
    return EngineChoice(
        asked_engine=typed,
        engine=manager.ENGINE_NAME,
        flavour=decided.flavour,
        display_name=decided.describe(),
        version=asked,
        running_version=None,
        action="install",
        plan=decided.plan,
    )


def _offered(flavour: str) -> list[str]:
    """
    List the versions the catalog offers a flavour on this release.

    Args:
        flavour: A flavour of :data:`~noust.managers.database.flavours.FLAVOURS`.

    Returns:
        The versions, oldest first; empty when it has none or is unknown.
    """
    try:
        os_release = distribution()
    except OSError:
        return []
    for entry in catalog(os_release, {}, apt=True):
        if entry.flavour == flavour:
            return [choice.version for choice in entry.versions]
    return []


def choices(service: DatabaseService) -> list[dict[str, Any]]:
    """
    List every engine and version a database can be created on, and how.

    What the new-application wizard offers: for each flavour, whether it is
    installed (and its version), whether it can be installed here, and the
    versions the catalog offers, each as ``installed``, ``installable`` or
    ``unavailable`` (with why).

    Args:
        service: The database service.

    Returns:
        One entry per flavour, in the catalog's order.
    """
    managers = service.host_managers()
    present = {manager.ENGINE_NAME: manager.installed_flavour() for manager in managers}
    versions = {manager.ENGINE_NAME: manager.get_version() for manager in managers}
    os_release = distribution()
    apt = bool(managers) and managers[0].runner.exists("apt-get")
    entries: list[dict[str, Any]] = []
    for flavour in catalog(os_release, present, apt=apt):
        running = versions.get(flavour.engine) if flavour.installed else None
        offered = []
        for choice in flavour.versions:
            if flavour.installed:
                state = "installed" if version_matches(choice.version, running) else "unavailable"
            else:
                state = "installable" if flavour.installable else "unavailable"
            offered.append({"version": choice.version, "state": state, "default": choice.default})
        entries.append(
            {
                "flavour": flavour.flavour,
                "engine": flavour.engine,
                "display_name": flavour.display_name,
                "installed": flavour.installed,
                "running_version": running,
                "installable": flavour.installable,
                "reason": flavour.reason,
                "versions": offered,
            }
        )
    return entries
