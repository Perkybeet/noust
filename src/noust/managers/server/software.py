# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The software of the server: what is installed, who depends on it, and installing and removing it (item 118).

The pages that said "nginx is missing" and the one button that removed an engine each knew a part of
this. :class:`SoftwareManager` is the one place that answers it, from the registry
(:mod:`noust.managers.server.components`) and the ledger of what Noust installed
(:class:`~noust.core.software_ledger.SoftwareLedger`), and it is where the guards are (rule 4): the
console, the command line and a job all install and remove through it, so a refusal is the same
whoever asks.

**A removal is refused, with its reason, when:**

- the component is one of those that are never removed (Noust and what it is built on, systemd,
  sshd, the firewall, git, curl, gnupg);
- something depends on it (the sites behind nginx, the tunnels cloudflared runs, the databases an
  engine holds), or Noust cannot tell what does;
- the package manager says removing its packages would take another component along that is never
  removed or that something depends on (``apt remove`` of a package Noust depends on takes Noust
  with it);
- this Noust is a hub, which has no software of its own, or the system is not one whose packages
  Noust changes (an image-based one).

It is **never a purge**: what a package left in ``/etc`` stays. A component whose origin is unknown
(no row in the ledger, none of the marks Noust leaves) is removed only with the operator's explicit
confirmation, because Noust did not put it there and other things on the system may use it.

The refusal is recorded too: ``server.software_remove`` with the outcome ``denied``, and what
finished or failed is recorded where it happens, not when somebody asked.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from noust.central import is_hub, require_server_role
from noust.core import audit
from noust.core.audit.context import current_actor
from noust.core.exceptions import NoustError
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.core.server_text import ServerText, say
from noust.core.software_ledger import InstalledSoftware, SoftwareLedger
from noust.core.teams.model import ResourceNotFoundError
from noust.managers.server import components as registry
from noust.managers.server.component_dependents import Dependent
from noust.managers.server.components import Component, Installer, Severity
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    ServerError,
    SoftwareRefusedError,
    UnsupportedHostError,
)
from noust.managers.server.facts import FactCache
from noust.managers.server.host import HostPaths, PackageFamily, Platform, detect_platform
from noust.managers.server.pkg import PackageBackend, backend_for
from noust.managers.server.pkg.base import INSTALL_TIMEOUT

log = logging.getLogger(__name__)

__all__ = [
    "REMOVE_TIMEOUT",
    "TTL_INVENTORY",
    "Blocker",
    "Impact",
    "InstallOptions",
    "InstallResult",
    "Inventory",
    "RemoveResult",
    "SoftwareItem",
    "SoftwareManager",
]

#: How long a list of the software stays fresh, in seconds: asking the package manager and every
#: store is a second's work, and a page that is open does not need it on every look.
TTL_INVENTORY = 60.0

#: Deadline of taking packages out.
REMOVE_TIMEOUT = 900

#: The audit events of this area (``core/audit/catalog.py``).
INSTALL_EVENT = "server.software_install"
REMOVE_EVENT = "server.software_remove"

#: Where Noust's own repository files live. A file of the ledger or of a mark is taken back out
#: only from here and only when it carries Noust's name: the rows are read back by a removal that
#: deletes what they name, so what they may name is closed at the one place that deletes.
_REPOSITORY_DIRECTORIES = (
    "/etc/apt/sources.list.d",
    "/usr/share/keyrings",
    "/etc/yum.repos.d",
    "/etc/zypp/repos.d",
    "/etc/pki/rpm-gpg",
)

#: What a file Noust wrote for a repository is called, directly under one of those directories.
_NOUST_FILE_PREFIXES = ("noust-", "RPM-GPG-KEY-noust-")


class Ledger(Protocol):
    """What the manager needs of the ledger of installed software."""

    def record(
        self,
        component: str,
        *,
        packages: Sequence[str] = ...,
        repository_files: Sequence[str] = ...,
        via: str,
        actor: str | None = ...,
    ) -> InstalledSoftware:
        """Write down an install."""

    def get(self, component: str) -> InstalledSoftware | None:
        """Read what Noust installed for a component."""

    def all(self) -> dict[str, InstalledSoftware]:
        """Read every row."""

    def forget(self, component: str) -> bool:
        """Drop a component that was removed."""


@dataclass(frozen=True)
class Blocker:
    """
    A reason a component is not removed now.

    Attributes:
        reason: ``never``, ``dependents``, ``takes_never``, ``takes_in_use``, ``not_installed``,
            ``cannot_tell``.
        text: The sentence, with its code and its fix, for the console to translate.
        dependents: What depends on the component, for ``dependents`` and ``takes_in_use``.
    """

    reason: str
    text: ServerText
    dependents: tuple[Dependent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The blocker for the API and the CLI.
        """
        return {
            "reason": self.reason,
            "text": self.text.text,
            "hint": self.text.hint,
            "code": self.text.code,
            "params": dict(self.text.params),
            "dependents": [item.to_dict() for item in self.dependents],
        }


@dataclass(frozen=True)
class SoftwareItem:
    """
    One component, as the page shows it.

    Attributes:
        id: The registry's id.
        purpose: The sentence that says what Noust uses it for.
        severity: ``never``, ``critical_while`` or ``removable``.
        installed: Whether it is on the machine.
        version: The version, when it is installed and the package manager says one.
        origin: ``noust`` (the ledger has it, or a file Noust writes is there), ``unknown``, or
            None when it is not installed.
        installable: Whether Noust installs it on this system.
        removable: Whether Noust would remove it now: installed, not a never, nothing depends on it.
        dependents: What depends on it.
        packages: The packages of it that are installed.
        installed_at: When Noust installed it, from the ledger.
        installed_by: As whom.
        installed_via: Through which installer.
    """

    id: str
    purpose: ServerText
    severity: str
    installed: bool
    version: str | None
    origin: str | None
    installable: bool
    removable: bool
    dependents: tuple[Dependent, ...] = ()
    packages: tuple[str, ...] = ()
    installed_at: str | None = None
    installed_by: str | None = None
    installed_via: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The item for the API and the CLI.
        """
        return {
            "id": self.id,
            "purpose": self.purpose.text,
            "purpose_code": self.purpose.code,
            "severity": self.severity,
            "installed": self.installed,
            "version": self.version,
            "origin": self.origin,
            "installable": self.installable,
            "removable": self.removable,
            "dependents": [item.to_dict() for item in self.dependents],
            "packages": list(self.packages),
            "installed_at": self.installed_at,
            "installed_by": self.installed_by,
            "installed_via": self.installed_via,
        }


@dataclass(frozen=True)
class Inventory:
    """
    The software of the server.

    Attributes:
        managed: Whether Noust changes this system's packages. When it does not (an image-based
            system, no package manager) the list is still the registry's, and ``reason`` says why
            nothing can be installed or removed.
        reason: Why it is not managed, when it is not.
        family: The package manager, ``apt``, ``dnf``, ``zypper`` or ``none``.
        items: One per component, in the order the page shows them.
    """

    managed: bool
    reason: str | None
    family: str
    items: tuple[SoftwareItem, ...]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The inventory for the API and the CLI.
        """
        return {
            "managed": self.managed,
            "reason": self.reason,
            "family": self.family,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class Impact:
    """
    What removing a component would do, decided before anything is touched.

    Attributes:
        id: The component.
        installed: Whether it is on the machine.
        origin: ``noust``, ``unknown`` or None.
        severity: ``never``, ``critical_while`` or ``removable``.
        dependents: What depends on it.
        removes: Its own packages that are installed, which are the ones named to the package manager.
        also_removes: What else the package manager says would go with them.
        repository_files: The repository source and key files Noust would take back out.
        blockers: Why it cannot be removed now; none means it can.
        needs_confirmation: Its origin is unknown, so removing it needs the operator's yes.
    """

    id: str
    installed: bool
    origin: str | None
    severity: str
    dependents: tuple[Dependent, ...] = ()
    removes: tuple[str, ...] = ()
    also_removes: tuple[str, ...] = ()
    repository_files: tuple[str, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    needs_confirmation: bool = False

    @property
    def removable(self) -> bool:
        """Whether nothing stands in the way."""
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The impact for the API and the CLI.
        """
        return {
            "id": self.id,
            "installed": self.installed,
            "origin": self.origin,
            "severity": self.severity,
            "dependents": [item.to_dict() for item in self.dependents],
            "removes": list(self.removes),
            "also_removes": list(self.also_removes),
            "repository_files": list(self.repository_files),
            "blockers": [item.to_dict() for item in self.blockers],
            "removable": self.removable,
            "needs_confirmation": self.needs_confirmation,
        }


@dataclass(frozen=True)
class InstallOptions:
    """
    What an installer may be told beyond which component.

    Attributes:
        epel: The operator agreed to enable EPEL, where Knot or fail2ban come from it.
        ignore: Addresses fail2ban must never ban (the client's own and the console's).
    """

    epel: bool = False
    ignore: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallResult:
    """
    What installing a component did.

    Attributes:
        id: The component.
        installed_now: False when it was there already and nothing was done.
        packages: What is installed of it now.
        repository_files: The repository files Noust added.
        rehearsal: Nothing was changed: this is what would have been.
    """

    id: str
    installed_now: bool
    packages: tuple[str, ...] = ()
    repository_files: tuple[str, ...] = ()
    rehearsal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API and the CLI.
        """
        return {
            "id": self.id,
            "installed_now": self.installed_now,
            "packages": list(self.packages),
            "repository_files": list(self.repository_files),
            "rehearsal": self.rehearsal,
        }


@dataclass(frozen=True)
class RemoveResult:
    """
    What removing a component did.

    Attributes:
        id: The component.
        removed: The packages that were taken out.
        also_removed: What went with them, as the package manager said it would.
        withdrawn: The repository files that were taken back out.
        rehearsal: Nothing was changed: this is what would have been.
    """

    id: str
    removed: tuple[str, ...] = ()
    also_removed: tuple[str, ...] = ()
    withdrawn: tuple[str, ...] = ()
    rehearsal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API and the CLI.
        """
        return {
            "id": self.id,
            "removed": list(self.removed),
            "also_removed": list(self.also_removed),
            "withdrawn": list(self.withdrawn),
            "rehearsal": self.rehearsal,
        }


#: What installs a component whose installer is not the package manager: each takes the manager,
#: the component and the options, and installs it. Built on first use so that importing this module
#: loads none of them.
Adapter = Callable[["SoftwareManager", Component, InstallOptions], None]


@dataclass
class _Facts:
    """What one look at the machine found, so a list does not ask twice."""

    versions: dict[str, str] = field(default_factory=dict)


class SoftwareManager:
    """The one place that lists, installs and removes the server's software."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        platform: Platform | None = None,
        ledger: Ledger | None = None,
        cache: FactCache | None = None,
        components: Sequence[Component] | None = None,
        adapters: Mapping[Installer, Adapter] | None = None,
    ) -> None:
        """
        Args:
            runner: Command runner; the process-wide one when omitted, resolved at each use so
                ``--dry-run`` applies.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            platform: What the machine is; detected on first use when omitted.
            ledger: The ledger of installed software; the store's by default.
            cache: The fact cache the list is kept in; a foreground one when omitted.
            components: The registry; the one of :mod:`noust.managers.server.components` by
                default. Only tests give another.
            adapters: What installs a component whose installer is not the package manager;
                the existing installers by default.
        """
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self._platform = platform
        self._ledger = ledger
        self.cache = cache or FactCache(background=False)
        self._components = tuple(components) if components is not None else registry.COMPONENTS
        self._adapters = adapters

    # -- what the manager works with ------------------------------------------------------------

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    @property
    def platform(self) -> Platform:
        """What the machine is, detected on first use."""
        if self._platform is None:
            self._platform = detect_platform(self._runner, self.host)
        return self._platform

    @property
    def ledger(self) -> Ledger:
        """The ledger of what Noust installed."""
        if self._ledger is None:
            self._ledger = SoftwareLedger()
        return self._ledger

    @property
    def backend(self) -> PackageBackend:
        """The package manager's backend, which refuses what this system does not allow."""
        return backend_for(self.platform, runner=self._runner, fs=self._fs, host=self.host)

    def component(self, component_id: str) -> Component:
        """
        Find a component by the id a request named.

        Args:
            component_id: The id.

        Returns:
            The component.

        Raises:
            ResourceNotFoundError: The registry has none of that id (the API answers 404).
        """
        for item in self._components:
            if item.id == component_id:
                return item
        raise ResourceNotFoundError(
            f"There is no software called {component_id}",
            details="List what Noust manages with: noust server software list",
        )

    def _managed(self) -> None:
        """
        Refuse where Noust has no software to manage.

        Raises:
            RoleError: This Noust is a hub.
            UnsupportedHostError: The system is not one whose packages Noust changes.
        """
        require_server_role("Software")
        if self.platform.transactional or self.platform.family is PackageFamily.NONE:
            raise UnsupportedHostError(
                "Noust does not change this system's software",
                self.platform.why_updates_unsupported(),
            )

    # -- reading ----------------------------------------------------------------------------------

    def inventory(self, *, refresh: bool = False) -> Inventory:
        """
        List every component: installed or not, its version, who installed it, who depends on it.

        Kept for :data:`TTL_INVENTORY` seconds; an install or a removal makes it stale at once.

        Args:
            refresh: Ask again whatever the age of the last answer.

        Returns:
            The inventory. On a hub there is none: it is empty and says so.

        Raises:
            ServerError: The package manager could not be asked which packages are installed.
        """
        if is_hub():
            return Inventory(
                False, "This central is a hub: it has no software of its own", "none", ()
            )
        if refresh:
            self.cache.invalidate("software.inventory")
        fact = self.cache.get("software.inventory", self._build_inventory, TTL_INVENTORY, wait=True)
        if fact is None or fact.value is None:
            raise ServerError(
                "Could not read the server's software",
                (fact.error if fact is not None else None) or "The package manager did not answer.",
            )
        value: Inventory = fact.value
        return value

    def _build_inventory(self) -> Inventory:
        """
        Returns:
            The inventory, asked of the machine.
        """
        platform = self.platform
        managed = not platform.transactional and platform.family is not PackageFamily.NONE
        reason = None if managed else platform.why_updates_unsupported()
        looked = self._look()
        ledger = self.ledger.all()
        items = tuple(
            self._item(component, looked, ledger.get(component.id))
            for component in self._components
        )
        return Inventory(managed, reason, platform.family.value, items)

    def _look(self) -> _Facts:
        """
        Ask the package manager the version of every package the registry names.

        Returns:
            What it found.
        """
        family = self.platform.family
        names = sorted(
            {name for component in self._components for name in component.candidates_on(family)}
        )
        if not names or family is PackageFamily.NONE:
            return _Facts()
        return _Facts(self.backend.installed_versions(names))

    def _item(
        self, component: Component, looked: _Facts, row: InstalledSoftware | None
    ) -> SoftwareItem:
        """
        Describe one component.

        Args:
            component: The component.
            looked: What the package manager said.
            row: What the ledger holds for it.

        Returns:
            The item.
        """
        family = self.platform.family
        installed_packages = tuple(
            name for name in component.candidates_on(family) if name in looked.versions
        )
        installed, version = self._presence(component, installed_packages, looked)
        origin = self._origin(component, row) if installed else None
        dependents, trouble = self._dependents(component) if installed else ((), None)
        removable = (
            installed
            and component.severity is not Severity.NEVER
            and not dependents
            and trouble is None
        )
        return SoftwareItem(
            id=component.id,
            purpose=say(component.purpose),
            severity=component.severity.value,
            installed=installed,
            version=version,
            origin=origin,
            installable=component.installable_on(family),
            removable=removable,
            dependents=dependents,
            packages=installed_packages,
            installed_at=row.installed_at if row else None,
            installed_by=row.actor if row else None,
            installed_via=row.via if row else None,
        )

    def _presence(
        self, component: Component, installed_packages: tuple[str, ...], looked: _Facts
    ) -> tuple[bool, str | None]:
        """
        Say whether a component is installed, and which version.

        Args:
            component: The component.
            installed_packages: Its packages the package manager lists.
            looked: What it said.

        Returns:
            Whether it is there: an engine by its manager, anything else by a package or a program
            on the path; and the version of its first package.
        """
        if component.engine is not None:
            return self._engine_presence(component)
        version = looked.versions.get(installed_packages[0]) if installed_packages else None
        if installed_packages:
            return True, version
        return any(self.runner.exists(program) for program in component.binaries), None

    def _engine_presence(self, component: Component) -> tuple[bool, str | None]:
        """
        Args:
            component: A database engine's component.

        Returns:
            Whether the engine is installed, and its version, as its own manager says.
        """
        from noust.managers.database.registry import get_db_manager

        manager = get_db_manager(component.engine or "")
        if manager is None or not manager.is_installed():
            return False, None
        return True, manager.get_version()

    def _origin(self, component: Component, row: InstalledSoftware | None) -> str:
        """
        Say who put an installed component there.

        Args:
            component: The component.
            row: What the ledger holds for it.

        Returns:
            ``noust`` when the ledger has it or a file Noust writes for it is there, else
            ``unknown``: what was installed before the ledger existed is recognised by its marks,
            never assumed to be Noust's.
        """
        if row is not None or self._marks_present(component):
            return "noust"
        return "unknown"

    def _marks_present(self, component: Component) -> list[Path]:
        """
        Args:
            component: The component.

        Returns:
            The files Noust writes for it that exist, below the host's root.
        """
        found: list[Path] = []
        for pattern in component.marks:
            relative = pattern.lstrip("/")
            found.extend(sorted(self.host.root.glob(relative)))
        return found

    def _dependents(self, component: Component) -> tuple[tuple[Dependent, ...], ServerText | None]:
        """
        Ask who depends on a component.

        Args:
            component: The component.

        Returns:
            Who depends on it, and, when the question could not be answered, the sentence that
            says so (the dependents are then not known, which is not "none").
        """
        if component.dependents is None:
            return (), None
        try:
            return tuple(component.dependents()), None
        except (NoustError, sqlite3.Error, OSError) as exc:
            log.warning("Could not tell what depends on %s: %s", component.id, exc)
            return (), say("software.refused.cannot_tell", component=component.id, reason=str(exc))

    # -- what removing would do ----------------------------------------------------------------

    def impact(self, component_id: str) -> Impact:
        """
        Say what removing a component would do and whether it may, touching nothing.

        This is the one decision: :meth:`remove` asks it and refuses on its blockers.

        Args:
            component_id: The component.

        Returns:
            The impact. A component that is not installed has one blocker, ``not_installed``.

        Raises:
            ResourceNotFoundError: The registry has none of that id.
            RoleError: This Noust is a hub.
            UnsupportedHostError: The system's packages are not changed by Noust.
            ServerError: The package manager could not be asked.
        """
        component = self.component(component_id)
        self._managed()
        family = self.platform.family
        looked = self._look()
        installed_packages = tuple(
            name for name in component.candidates_on(family) if name in looked.versions
        )
        installed, _version = self._presence(component, installed_packages, looked)
        row = self.ledger.get(component.id)
        origin = self._origin(component, row) if installed else None
        blockers: list[Blocker] = []
        if not installed:
            blockers.append(
                Blocker(
                    "not_installed", say("software.refused.not_installed", component=component.id)
                )
            )
        if component.severity is Severity.NEVER:
            # Each of them says why in a sentence of its own, with no value to put in.
            reason = (
                say(component.never_reason)
                if component.never_reason
                else say("software.refused.never", component=component.id)
            )
            blockers.append(Blocker("never", reason))
        dependents, trouble = self._dependents(component) if installed else ((), None)
        if trouble is not None:
            blockers.append(Blocker("cannot_tell", trouble))
        elif dependents and component.severity is not Severity.NEVER:
            blockers.append(
                Blocker(
                    "dependents",
                    say(
                        "software.refused.dependents",
                        component=component.id,
                        dependents=_sentence_of(dependents),
                    ),
                    dependents,
                )
            )
        also: tuple[str, ...] = ()
        if installed and installed_packages and not blockers:
            also, collateral = self._collateral(component, installed_packages)
            blockers.extend(collateral)
        return Impact(
            id=component.id,
            installed=installed,
            origin=origin,
            severity=component.severity.value,
            dependents=dependents,
            removes=installed_packages,
            also_removes=also,
            repository_files=tuple(str(path) for path in self._withdrawable(component, row)),
            blockers=tuple(blockers),
            needs_confirmation=installed and origin == "unknown",
        )

    def _collateral(
        self, component: Component, own: tuple[str, ...]
    ) -> tuple[tuple[str, ...], list[Blocker]]:
        """
        Ask the package manager what else removing a component's packages would take.

        Args:
            component: The component.
            own: Its packages that are installed.

        Returns:
            What else would go, and the blockers that follow from it: another component that is
            never removed, or that something depends on.
        """
        family = self.platform.family
        try:
            gone = self.backend.simulate_remove(own)
        except ServerError as exc:
            return (), [
                Blocker(
                    "cannot_tell",
                    say("software.refused.cannot_tell", component=component.id, reason=exc.message),
                )
            ]
        own_names = set(component.candidates_on(family))
        also = tuple(name for name in gone if name not in own_names)
        blockers: list[Blocker] = []
        seen: set[str] = set()
        for name in also:
            owner = registry.owner_of(name, family)
            if owner is None or owner.id == component.id or owner.id in seen:
                continue
            seen.add(owner.id)
            if owner.severity is Severity.NEVER:
                blockers.append(
                    Blocker(
                        "takes_never",
                        say("software.refused.takes_never", component=component.id, other=owner.id),
                    )
                )
                continue
            dependents, trouble = self._dependents(owner)
            if trouble is not None:
                blockers.append(Blocker("cannot_tell", trouble))
            elif dependents:
                blockers.append(
                    Blocker(
                        "takes_in_use",
                        say(
                            "software.refused.takes_in_use",
                            component=component.id,
                            other=owner.id,
                            dependents=_sentence_of(dependents),
                        ),
                        dependents,
                    )
                )
        return also, blockers

    def _withdrawable(self, component: Component, row: InstalledSoftware | None) -> list[Path]:
        """
        Name the repository files Noust would take back out with a component.

        Args:
            component: The component.
            row: What the ledger holds for it.

        Returns:
            The files of the ledger and the marks that exist, from Noust's own directories and
            with Noust's own names only, whatever a row says.
        """
        wanted = [self.host.at(path) for path in (row.repository_files if row else ())]
        wanted.extend(self._marks_present(component))
        kept: dict[Path, None] = {}
        for path in wanted:
            if self._is_noust_repository_file(path) and (path.exists() or path.is_symlink()):
                kept.setdefault(path, None)
        return list(kept)

    def _is_noust_repository_file(self, path: Path) -> bool:
        """
        Args:
            path: A file below the host's root.

        Returns:
            Whether it is directly in one of the repository directories and named as Noust names
            what it writes there.
        """
        return any(
            path.parent == self.host.at(directory) and path.name.startswith(_NOUST_FILE_PREFIXES)
            for directory in _REPOSITORY_DIRECTORIES
        )

    # -- installing ---------------------------------------------------------------------------------

    def install(
        self,
        component_id: str,
        options: InstallOptions | None = None,
        *,
        on_output: Callable[[str], None] | None = None,
    ) -> InstallResult:
        """
        Install a component, unless it is there.

        Args:
            component_id: The component.
            options: What an installer is told beyond the component.
            on_output: Receives what the package manager prints, verbatim.

        Returns:
            What was done; ``installed_now`` is False when it was there already.

        Raises:
            ResourceNotFoundError: The registry has none of that id.
            RoleError: This Noust is a hub.
            UnsupportedHostError: The system's packages are not changed by Noust.
            SoftwareRefusedError: Noust does not install it on this system.
            ServerError: The package manager failed; the error carries its own words.
        """
        component, there = self._check_install(component_id)
        if there is not None:
            return InstallResult(component.id, False, there)
        family = self.platform.family
        before = set(self._marks_present(component))
        try:
            self._run_installer(component, options or InstallOptions(), on_output)
        except (NoustError, OSError) as exc:
            self._record(INSTALL_EVENT, component, "failure", reason=_first_line(exc))
            raise
        finally:
            self.cache.invalidate("software.inventory")
        if is_rehearsal():
            return InstallResult(component.id, False, rehearsal=True)
        installed_now = self.backend.installed_versions(component.candidates_on(family))
        brought = tuple(name for name in component.candidates_on(family) if name in installed_now)
        added = tuple(
            str(self._relative(path))
            for path in self._marks_present(component)
            if path not in before
        )
        self._remember(component, brought, added)
        self._record(INSTALL_EVENT, component, "ok", packages=list(brought))
        return InstallResult(component.id, True, brought, added)

    def check_install(self, component_id: str) -> None:
        """
        Refuse now what :meth:`install` would refuse, changing nothing.

        For a caller that queues the install and would rather answer at once than let a job fail.
        The refusal is recorded here, as it is when ``install`` makes it.

        Args:
            component_id: The component.

        Raises:
            ResourceNotFoundError: The registry has none of that id.
            RoleError: This Noust is a hub.
            UnsupportedHostError: The system's packages are not changed by Noust.
            SoftwareRefusedError: Noust does not install it on this system.
        """
        self._check_install(component_id)

    def _check_install(self, component_id: str) -> tuple[Component, tuple[str, ...] | None]:
        """
        Judge an install.

        Args:
            component_id: The component.

        Returns:
            The component, and its installed packages when it is there already (nothing is
            left to refuse then), else None.

        Raises:
            ResourceNotFoundError: The registry has none of that id.
            RoleError: This Noust is a hub.
            UnsupportedHostError: The system's packages are not changed by Noust.
            SoftwareRefusedError: Noust does not install it on this system.
        """
        component = self.component(component_id)
        self._managed()
        family = self.platform.family
        looked = self._look()
        packages = tuple(
            name for name in component.candidates_on(family) if name in looked.versions
        )
        if self._presence(component, packages, looked)[0]:
            return component, packages
        if not component.installable_on(family):
            error = SoftwareRefusedError(
                f"Noust does not install {component.id} on this system",
                "Install it with the system's package manager; Noust manages it from there.",
                reason="not_installable",
            )
            self._record(INSTALL_EVENT, component, "denied", reason=error.message)
            raise error
        return component, None

    def _run_installer(
        self,
        component: Component,
        options: InstallOptions,
        on_output: Callable[[str], None] | None,
    ) -> None:
        """
        Run what installs a component.

        Args:
            component: The component.
            options: What the installer is told.
            on_output: Receives what the package manager prints.

        Raises:
            ServerError: The package manager failed.
        """
        if component.installer is Installer.PACKAGE:
            packages = list(component.packages_on(self.platform.family))
            result = self.backend.install(packages, timeout=INSTALL_TIMEOUT)
            if on_output is not None:
                for line in f"{result.stdout}\n{result.stderr}".splitlines():
                    if line.strip():
                        on_output(line)
            if not result.success:
                raise ServerError(
                    f"Could not install {component.id} (exit code {result.exit_code})",
                    "The package manager's own output is below.",
                    output="\n".join(
                        part for part in (result.stdout.strip(), result.stderr.strip()) if part
                    ),
                )
            return
        adapters = self._adapters if self._adapters is not None else _adapters()
        adapter = adapters.get(component.installer)
        if adapter is None:
            raise UnsupportedHostError(
                f"Noust has no installer for {component.id} here",
                "Install it with the system's package manager; Noust manages it from there.",
            )
        adapter(self, component, options)

    def _relative(self, path: Path) -> Path:
        """
        Args:
            path: A file below the host's root.

        Returns:
            The path as it is on a real machine.
        """
        return Path("/") / path.relative_to(self.host.root)

    def _remember(
        self, component: Component, packages: tuple[str, ...], repository_files: tuple[str, ...]
    ) -> None:
        """
        Write the install in the ledger.

        A ledger that cannot be written does not undo an install that worked: the install is
        recognised by its marks all the same, and the failure is in the log.

        Args:
            component: The component.
            packages: What is installed of it.
            repository_files: The repository files Noust added.
        """
        actor = current_actor()
        try:
            self.ledger.record(
                component.id,
                packages=packages,
                repository_files=repository_files,
                via=component.installer.value,
                actor=actor.label if actor else None,
            )
        except (NoustError, sqlite3.Error, OSError) as exc:
            log.warning("Could not record that %s was installed: %s", component.id, exc)

    # -- removing --------------------------------------------------------------------------------------

    def remove(
        self,
        component_id: str,
        *,
        confirm_unknown_origin: bool = False,
        on_output: Callable[[str], None] | None = None,
    ) -> RemoveResult:
        """
        Remove a component, if nothing stands in the way. Never a purge.

        Args:
            component_id: The component.
            confirm_unknown_origin: The operator knows Noust did not install it and other things
                on the system may use it.
            on_output: Receives what the package manager prints, verbatim.

        Returns:
            What was removed, what went with it and the repository files taken back out.

        Raises:
            ResourceNotFoundError: The registry has none of that id.
            RoleError: This Noust is a hub.
            UnsupportedHostError: The system's packages are not changed by Noust.
            SoftwareRefusedError: A guard refused; its ``reason`` says which.
            ConfirmationRequiredError: Its origin is unknown and ``confirm_unknown_origin`` is
                false.
            ServerError: The package manager failed; the error carries its own words.
        """
        component = self.component(component_id)
        decided = self.check_remove(component_id, confirm_unknown_origin=confirm_unknown_origin)
        try:
            withdrawn = self._take_out(component, decided, on_output)
        except (NoustError, OSError) as exc:
            self._record(REMOVE_EVENT, component, "failure", reason=_first_line(exc))
            raise
        finally:
            self.cache.invalidate("software.inventory")
        if is_rehearsal():
            return RemoveResult(
                component.id, decided.removes, decided.also_removes, withdrawn, rehearsal=True
            )
        self.ledger.forget(component.id)
        self._record(
            REMOVE_EVENT,
            component,
            "ok",
            packages=list(decided.removes),
            withdrawn=list(withdrawn),
            origin=decided.origin,
        )
        return RemoveResult(component.id, decided.removes, decided.also_removes, withdrawn)

    def check_remove(self, component_id: str, *, confirm_unknown_origin: bool = False) -> Impact:
        """
        Refuse now what :meth:`remove` would refuse, changing nothing.

        This is the guard, and the only one: a removal asks it first, and a caller that queues the
        removal asks it to answer at once instead of letting a job fail. A refusal is recorded
        here, as ``denied``, whoever asked.

        Args:
            component_id: The component.
            confirm_unknown_origin: The operator knows Noust did not install it and other things
                on the system may use it.

        Returns:
            What removing it would do, when nothing stands in the way.

        Raises:
            ResourceNotFoundError: The registry has none of that id.
            RoleError: This Noust is a hub.
            UnsupportedHostError: The system's packages are not changed by Noust.
            SoftwareRefusedError: A guard refused; its ``reason`` says which.
            ConfirmationRequiredError: Its origin is unknown and ``confirm_unknown_origin`` is
                false.
            ServerError: The package manager could not be asked.
        """
        component = self.component(component_id)
        decided = self.impact(component_id)
        if decided.blockers:
            blocker = decided.blockers[0]
            error = SoftwareRefusedError(
                blocker.text.text,
                blocker.text.hint or "",
                reason=blocker.reason,
                dependents=[item.to_dict() for item in blocker.dependents],
                blockers=[item.text.text for item in decided.blockers],
                coded=blocker.text,
            )
            self._record(REMOVE_EVENT, component, "denied", reason=blocker.reason)
            raise error
        if decided.needs_confirmation and not confirm_unknown_origin:
            confirmation = ConfirmationRequiredError(
                f"Noust did not install {component.id}",
                "Other things on the system may use it. Repeat the request confirming it to "
                "remove it anyway.",
                required={"unknown_origin": True, "removes": list(decided.removes)},
            )
            confirmation.coded = say("software.refused.unknown_origin", component=component.id)
            self._record(REMOVE_EVENT, component, "denied", reason="unknown_origin")
            raise confirmation
        return decided

    def _take_out(
        self,
        component: Component,
        decided: Impact,
        on_output: Callable[[str], None] | None,
    ) -> tuple[str, ...]:
        """
        Take a component out, and the repository files Noust added for it.

        Args:
            component: The component.
            decided: What :meth:`impact` found.
            on_output: Receives what the package manager prints.

        Returns:
            The repository files that were taken back out.

        Raises:
            ServerError: The package manager failed.
        """
        if component.engine is not None:
            # The engine's own manager removes it behind its own guard (the databases it holds)
            # and takes back the repositories it added: one removal of an engine, not two.
            from noust.managers.database.registry import get_db_manager

            manager = get_db_manager(component.engine)
            if manager is not None:
                manager.uninstall(purge=False)
            return ()
        names = list(decided.removes)
        result = self.backend.remove(names, timeout=REMOVE_TIMEOUT)
        if on_output is not None:
            for line in f"{result.stdout}\n{result.stderr}".splitlines():
                if line.strip():
                    on_output(line)
        if not result.success:
            raise ServerError(
                f"Could not remove {component.id} (exit code {result.exit_code})",
                "Nothing else was changed. The package manager's own output is below.",
                output="\n".join(
                    part for part in (result.stdout.strip(), result.stderr.strip()) if part
                ),
            )
        withdrawn: list[str] = []
        for path in self._withdrawable(component, self.ledger.get(component.id)):
            self.fs.remove(path)
            withdrawn.append(str(self._relative(path)))
        return tuple(withdrawn)

    # -- the record ---------------------------------------------------------------------------------------

    def _record(self, event: str, component: Component, outcome: str, **details: Any) -> None:
        """
        Write the audit event, as whoever is acting now.

        Args:
            event: ``server.software_install`` or ``server.software_remove``.
            component: The component.
            outcome: ``ok``, ``failure`` or ``denied``.
            **details: What to say about it.
        """
        if is_rehearsal():
            return
        audit.record(event, target=f"software:{component.id}", outcome=outcome, details=details)


def _sentence_of(dependents: Sequence[Dependent]) -> str:
    """
    Args:
        dependents: What depends on a component.

    Returns:
        Them as one line of names by kind, for a sentence's value: ``application: a.example.com,
        b.example.com; tunnel: main``.
    """
    return "; ".join(f"{item.kind}: {', '.join(item.names)}" for item in dependents)


def _first_line(exc: Exception) -> str:
    """
    Args:
        exc: What went wrong.

    Returns:
        Its message on one line, for the audit trail.
    """
    message = exc.message if isinstance(exc, NoustError) else str(exc)
    return " ".join(message.split())


def _adapters() -> dict[Installer, Adapter]:
    """
    Returns:
        What installs each component whose installer is not the package manager: the code that
        already did it, called as it was (rule 3).
    """
    from noust.managers.server import software_installers as installers

    return installers.ADAPTERS
