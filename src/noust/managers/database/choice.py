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

The host's engine is the primary one, and it is what a database is made on
unless another version, another flavour or an instance is asked for. Beside it
there can be **instances** Noust runs in containers (checklist C-03,
:mod:`noust.managers.database.managed`), and a version the host does not run is
answered by the instance that does: by itself when exactly one runs it, by name
(``instance``) when the operator says which. What is never done is guessing
between two, or making an instance as a side effect: a version nothing runs is
refused with the command that makes one.

With no version and no flavour named, nothing here changes what 3.3 did: the
engine that is installed is the one used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from noust.core.db_instance_store import READY, DbInstanceStore, InstanceRecord
from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.package_family import PackageFamily, detect_family
from noust.managers.database.engine_setup import EnginePlan, plan_engine_install
from noust.managers.database.flavours import (
    FLAVOURS,
    InstallPlan,
    catalog,
    distribution,
    resolve_flavour,
)
from noust.managers.database.instance_catalog import SPECS, spec_for
from noust.managers.database.instance_records import key_of

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
        instance: The name of the instance Noust runs that the database is made
            on, when it is not the host's engine; ``engine`` is then its key.
    """

    asked_engine: str
    engine: str
    flavour: str | None
    display_name: str
    version: str | None
    running_version: str | None
    action: Action
    plan: InstallPlan | None = field(default=None)
    instance: str | None = None

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
            "instance": self.instance,
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
    instance: str | None = None,
) -> EngineChoice:
    """
    Decide what creating a database on an engine in a version means here.

    Reads the machine (what is installed, which version runs), the instances
    Noust runs and the catalog; changes nothing.

    Args:
        service: The database service, which resolves engines.
        engine: The engine as typed: a name or an alias (``mariadb``, ``pg``), or
            an instance's key (``postgresql@pg17``).
        version: The version asked for, None for whatever is installed.
        flavour: A flavour named explicitly, when the name is not enough.
        install: The caller allows installing the engine when it is missing.
        install_hint: How this caller says "install it as part of this", for the
            error that refuses a missing engine (``add --install``).
        instance: The name of an instance Noust runs to make the database on,
            instead of the host's engine.

    Returns:
        The decision. The host's engine when it is what was asked for (or all
        that was), the instance that runs the version when the host does not,
        and an engine in a container Docker Compose runs as it is: its version
        is its image's, so one asked for is refused.

    Raises:
        ValidationError: When the version is malformed, cannot be had on this
            release, or belongs to another flavour than the installed one, when
            no instance runs what the host does not, when several do, or when
            the instance named is not one that can hold it.
        DatabaseEngineError: When the engine is unknown, or is missing and may
            not be installed.
    """
    asked = parse_version(version)
    if instance:
        return _named_instance(service, engine, instance, asked, flavour)
    manager = service.manager(engine)
    if manager.instance is not None:
        if getattr(manager.instance, "managed", None) is not None:
            return _managed_choice(manager, engine, asked)
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
        try:
            return _installed_choice(manager, engine, chosen, asked)
        except ValidationError as refusal:
            # The host runs another version, or the other flavour: an instance of
            # the one asked for is what answers it, the host's engine staying primary.
            found = _matching_instance(service, manager.ENGINE_NAME, chosen, asked)
            if found is not None:
                return _instance_choice(found, engine, asked)
            raise _with_instance_hint(refusal, manager, chosen, asked) from refusal
    if asked is not None or chosen is not None:
        found = _matching_instance(service, manager.ENGINE_NAME, chosen, asked)
        if found is not None:
            return _instance_choice(found, engine, asked)
    try:
        return _missing_choice(manager, engine, chosen, asked, install=install, hint=install_hint)
    except DatabaseEngineError as refusal:
        names = [row.name for row in _ready_rows(service, manager.ENGINE_NAME)]
        if names:
            refusal.details = (
                f"{refusal.details} Or make it on an instance Noust runs: --instance {names[0]}"
                + (f" (instances: {', '.join(names)})" if len(names) > 1 else "")
            ).strip()
        raise


def _ready_rows(service: DatabaseService, engine: str | None = None) -> list[InstanceRecord]:
    """
    List the instances Noust runs that are finished, optionally of one engine.

    Args:
        service: The database service, whose store holds the rows.
        engine: The engine's canonical name, or None for every engine.

    Returns:
        The rows, by name.
    """
    return [
        row
        for row in DbInstanceStore(service.store).all()
        if row.state == READY and (engine is None or spec_for(row.flavour).engine == engine)
    ]


def _matching_instance(
    service: DatabaseService, engine: str, flavour: str | None, asked: str | None
) -> InstanceRecord | None:
    """
    Find the instance that runs what the host does not.

    Args:
        service: The database service.
        engine: The engine's canonical name.
        flavour: The flavour asked for, or None.
        asked: The version asked for, or None.

    Returns:
        The one instance that matches, or None when none does.

    Raises:
        ValidationError: Several match: Noust does not choose between them.
    """
    found = [
        row
        for row in _ready_rows(service, engine)
        if (flavour is None or row.flavour == flavour)
        and (asked is None or version_matches(asked, row.version))
    ]
    if len(found) > 1:
        names = ", ".join(row.name for row in found)
        raise ValidationError(
            f"{len(found)} instances run what was asked for",
            details=f"They are {names}: say which with --instance (instance in the API).",
            field="instance",
        )
    return found[0] if found else None


def _instance_choice(row: InstanceRecord, typed: str, asked: str | None) -> EngineChoice:
    """
    Decide to use an instance.

    Args:
        row: The instance's row.
        typed: The engine as typed.
        asked: The version asked for, or None.

    Returns:
        The decision: its key is the engine the database is made on.
    """
    return EngineChoice(
        asked_engine=typed,
        engine=key_of(row),
        flavour=row.flavour,
        display_name=f"{spec_for(row.flavour).display_name} {row.version} (instance {row.name})",
        version=asked,
        running_version=row.version,
        action="use",
        instance=row.name,
    )


def _named_instance(
    service: DatabaseService, typed: str, name: str, asked: str | None, flavour: str | None
) -> EngineChoice:
    """
    Decide to use the instance the operator named.

    Args:
        service: The database service.
        typed: The engine as typed; it must be the instance's.
        name: The instance's name.
        asked: The version asked for, or None.
        flavour: A flavour named explicitly, or None.

    Returns:
        The decision.

    Raises:
        ValidationError: There is no such instance, or it runs another engine,
            another flavour or another version than what was asked for.
    """
    rows = _ready_rows(service)
    row = next((candidate for candidate in rows if candidate.name == name), None)
    if row is None:
        known = ", ".join(candidate.name for candidate in rows) or "none"
        raise ValidationError(
            f"There is no database instance called {name} that is ready",
            details=f"Instances: {known}. Make one with: noust db instance create {name} "
            "--engine ENGINE --version VERSION --memory 512m",
            field="instance",
        )
    spec = spec_for(row.flavour)
    base = typed.partition("@")[0]
    canonical = service.manager(base).ENGINE_NAME
    if canonical != spec.engine:
        raise ValidationError(
            f"{name} runs {spec.display_name}, not {base}",
            details="Name an instance of that engine: noust db instance list",
            field="instance",
        )
    chosen = resolve_flavour(base, canonical, flavour=flavour, version=asked)
    if chosen is not None and chosen != row.flavour and (flavour or base != canonical):
        raise ValidationError(
            f"{FLAVOURS[chosen].display_name} was asked for, but {name} runs {spec.display_name}",
            details="MariaDB is not MySQL and Valkey is not Redis: name an instance of the one "
            "you want.",
            field="engine",
        )
    if asked is not None and not version_matches(asked, row.version):
        raise ValidationError(
            f"{spec.display_name} {asked} was asked for, but {name} runs "
            f"{spec.display_name} {row.version}",
            details="An instance has the version it was made with. Name another, or make one: "
            f"noust db instance create NEW --engine {row.flavour} --version {asked} "
            f"--memory {spec.default_memory_mb}m",
            field="version",
        )
    return _instance_choice(row, typed, asked)


def _managed_choice(manager: BaseDatabaseManager, typed: str, asked: str | None) -> EngineChoice:
    """
    Decide for an instance addressed by its key.

    Args:
        manager: The manager bound to the instance.
        typed: The key as typed.
        asked: The version asked for, or None.

    Returns:
        The decision to use it.

    Raises:
        ValidationError: The version asked for is not the instance's.
    """
    found = manager.instance
    if found is None:  # the caller checked
        raise ValidationError(f"{typed} is not an instance", field="engine")
    if asked is not None and not version_matches(asked, found.version or ""):
        raise ValidationError(
            f"{manager.DISPLAY_NAME} {asked} was asked for, but {found.managed} runs "
            f"{manager.DISPLAY_NAME} {found.version}",
            details="An instance has the version it was made with.",
            field="version",
        )
    return EngineChoice(
        asked_engine=typed,
        engine=manager.ENGINE_NAME,
        flavour=None,
        display_name=f"{manager.DISPLAY_NAME} {found.version} (instance {found.managed})",
        version=asked,
        running_version=found.version,
        action="use",
        instance=found.managed,
    )


def _with_instance_hint(
    refusal: ValidationError, manager: BaseDatabaseManager, flavour: str | None, asked: str | None
) -> ValidationError:
    """
    Add, to the refusal of a version or flavour the host does not run, how to run it.

    Args:
        refusal: The refusal.
        manager: The engine's manager.
        flavour: The flavour asked for, or None.
        asked: The version asked for, or None.

    Returns:
        The same refusal, its details saying how to run it as an instance.
    """
    name = flavour or manager.ENGINE_NAME
    spec = SPECS.get(name)
    if spec is None:
        return refusal
    version = f" --version {asked}" if asked else ""
    refusal.details = (
        f"{refusal.details} Or run it beside the host's as an instance Noust keeps in a "
        f"container: noust db instance create NAME --engine {name}{version} "
        f"--memory {spec.default_memory_mb}m"
    ).strip()
    return refusal


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
            details=f"{have} and {wanted} cannot both be installed here (they share their port "
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
            f"{(running or '').split('.')[0] or 'the version that runs'}).",
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
    versions the catalog offers, each as ``installed``, ``installable``,
    ``instance`` (an instance Noust runs has it, whose name is given) or
    ``unavailable`` (with why). A version only an instance runs is offered too.
    ``instances`` lists those of the flavour, and ``instance_versions`` the
    versions one could be made in.

    Args:
        service: The database service.

    Returns:
        One entry per flavour, in the catalog's order.
    """
    managers = service.host_managers()
    present = {manager.ENGINE_NAME: manager.installed_flavour() for manager in managers}
    versions = {manager.ENGINE_NAME: manager.get_version() for manager in managers}
    os_release = distribution()
    apt = bool(managers) and detect_family(managers[0].runner)[0] is not PackageFamily.NONE
    rows = _ready_rows(service)
    entries: list[dict[str, Any]] = []
    for flavour in catalog(os_release, present, apt=apt):
        running = versions.get(flavour.engine) if flavour.installed else None
        mine = [row for row in rows if row.flavour == flavour.flavour]
        offered: list[dict[str, Any]] = []
        for choice in flavour.versions:
            if flavour.installed:
                state = "installed" if version_matches(choice.version, running) else "unavailable"
            else:
                state = "installable" if flavour.installable else "unavailable"
            held = next((row for row in mine if version_matches(choice.version, row.version)), None)
            if held is not None and state != "installed":
                state = "instance"
            offered.append(
                {
                    "version": choice.version,
                    "state": state,
                    "default": choice.default,
                    "instance": held.name if held is not None and state == "instance" else None,
                }
            )
        listed: set[str] = {str(item["version"]) for item in offered}
        for row in mine:
            if not any(version_matches(version, row.version) for version in listed):
                offered.append(
                    {
                        "version": row.version,
                        "state": "instance",
                        "default": False,
                        "instance": row.name,
                    }
                )
                listed.add(row.version)
        spec = SPECS.get(flavour.flavour)
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
                "instances": [
                    {"name": row.name, "key": key_of(row), "version": row.version} for row in mine
                ],
                "instance_versions": [pin.version for pin in spec.pins] if spec else [],
            }
        )
    return entries
