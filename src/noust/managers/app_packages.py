# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
System packages an application needs: a library to build against, a tool to call.

An application that resizes images wants ``libvips``, one that renders PDFs wants a font and a
converter. Noust builds natively on the host, so those are packages of the host, and installing
them makes root run the package manager. That is why the list is the operator's and nobody
else's: it is stored apart from the build block (:mod:`noust.core.app_packages_store`), which a
repository may write, and ``noust.yaml`` has no key for it. A repository is untrusted input; it
can ask for nothing here.

Three things stand between a name and the package manager:

- **The role.** Declaring packages is host access (the API route asks for it and for sudo mode),
  never a team's, whatever that team may do with its own applications.
- **The policy.** ``security.app_packages`` is a list of names and globs (``libvips*``,
  ``fonts-*``); when it is not empty a package outside it is refused, whoever asks. Empty allows
  any package the distribution has.
- **The name.** One word of letters, digits and ``+._-``, never an option and never a version
  or a path, so what reaches ``apt-get`` as an argument is a package name and nothing else.

Installing is the host's own package manager through
:func:`~noust.managers.server.pkg.backend_for`, the one place that knows how each family installs,
without what the packages recommend (a library is wanted, not the daemon it suggests). It only
adds: a package is never removed by Noust, because another application or the system may use it.
The packages are installed when they are declared and again, if any is missing, before each
update builds, so a rebuilt or restored server brings them back by itself.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from noust.central import require_server_role
from noust.core import app_packages_store, audit
from noust.core.applock import app_lock
from noust.core.config import Config
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import NoustStore, get_store, is_compose_backed
from noust.managers.server.errors import UnsupportedHostError
from noust.managers.server.host import HostPaths, Platform, detect_platform
from noust.managers.server.pkg import PackageBackend, backend_for
from noust.managers.server.pkg.base import INSTALL_TIMEOUT

__all__ = [
    "MAX_PACKAGES",
    "POLICY_KEY",
    "AppPackages",
    "PackageChange",
    "PackageState",
    "policy",
    "validate_package",
    "validate_packages",
]

#: The configuration key naming the packages applications may declare.
POLICY_KEY = "security.app_packages"

#: What a package name is: a word of letters, digits and ``+._-`` that does not begin with a
#: symbol, so it can never be read as an option, a path or a version pin.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,99}")

#: What a policy entry is: a name, or a glob over names.
_PATTERN = re.compile(r"[A-Za-z0-9*?\[\]][A-Za-z0-9+._*?\[\]-]{0,99}")

#: The most packages one application may declare: a list this long is a mistake.
MAX_PACKAGES = 50


def validate_package(name: str) -> str:
    """
    Check that a text is a package name.

    Args:
        name: What the operator typed.

    Returns:
        The name, stripped.

    Raises:
        ValidationError: It is not a name of letters, digits and ``+._-`` starting with a
            letter or digit; versions, paths, options and spaces are not names.
    """
    text = name.strip() if isinstance(name, str) else ""
    if not _NAME.fullmatch(text):
        raise ValidationError(
            f"{name!r} is not a package name",
            details="A package name is letters, digits and + . _ - only, starting with a letter "
            "or a digit (libvips-dev, fonts-noto-core). A version, a path or an option is not "
            "a name.",
            field="packages",
        )
    return text


def validate_packages(names: Sequence[str]) -> tuple[str, ...]:
    """
    Check a list of package names and drop the repeated ones.

    Args:
        names: What the operator gave.

    Returns:
        The names, in the order given, each once.

    Raises:
        ValidationError: A name is not one, or there are more than :data:`MAX_PACKAGES`.
    """
    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(validate_package(name), None)
    if len(seen) > MAX_PACKAGES:
        raise ValidationError(
            f"{len(seen)} packages are too many for one application",
            details=f"The most one application declares is {MAX_PACKAGES}.",
            field="packages",
        )
    return tuple(seen)


def policy(config: Any | None = None) -> tuple[str, ...]:
    """
    Read the packages this server lets applications declare.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        The names and globs; empty when the server sets none, which allows any package.
    """
    source = config if config is not None else Config()
    get = getattr(source, "get", None)
    raw = get(POLICY_KEY, []) if callable(get) else []
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        entry for item in raw if _PATTERN.fullmatch(entry := str(item).strip()) is not None
    )


def _allowed(name: str, allow: Collection[str]) -> bool:
    """
    Args:
        name: A package name.
        allow: The policy.

    Returns:
        Whether the policy lets it be declared.
    """
    return not allow or any(fnmatchcase(name, pattern) for pattern in allow)


@dataclass(frozen=True)
class PackageState:
    """
    What an application declares and what the host has.

    Attributes:
        domain: The application.
        declared: The names declared, in order.
        installed: Those the host has.
        missing: Those it lacks, which the next update (or ``install``) installs.
        refused: Those the policy no longer allows, which nothing installs until it does or
            they are removed.
        policy: The names and globs applications may declare; empty for any.
        manager: The host's package manager (``apt``, ``dnf``, ``zypper``) or ``none``.
        supported: Whether Noust installs packages on this host.
        updated_at: When the list last changed.
        updated_by: Who changed it.
    """

    domain: str
    declared: tuple[str, ...]
    installed: tuple[str, ...]
    missing: tuple[str, ...]
    refused: tuple[str, ...]
    policy: tuple[str, ...]
    manager: str
    supported: bool
    updated_at: str | None = None
    updated_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The state as plain data, for the API and ``--json``.
        """
        return {
            "domain": self.domain,
            "declared": list(self.declared),
            "installed": list(self.installed),
            "missing": list(self.missing),
            "refused": list(self.refused),
            "policy": list(self.policy),
            "manager": self.manager,
            "supported": self.supported,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


@dataclass(frozen=True)
class PackageChange:
    """
    What a change of the list, or an installation, did.

    Attributes:
        state: How the application stands afterwards.
        installed_now: The packages this call installed.
        output: What the package manager printed, verbatim, when it ran.
    """

    state: PackageState
    installed_now: tuple[str, ...] = ()
    output: str = ""


class AppPackages:
    """
    The system packages applications declare, and their installation.
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        config: Any | None = None,
        platform: Platform | None = None,
        host: HostPaths | None = None,
    ) -> None:
        """
        Args:
            store: The store; the process-wide one by default.
            runner: Runs the package manager; the process-wide one by default.
            fs: The filesystem seam; the process-wide one by default.
            config: The configuration the policy is read from.
            platform: What the host is; read from it when None.
            host: Where the system files are, for tests.
        """
        self._store = store
        self._runner = runner
        self._fs = fs
        self._config = config
        self._platform = platform
        self._host = host or HostPaths()

    # ------------------------------------------------------------ wiring

    @property
    def store(self) -> NoustStore:
        """The application store."""
        return self._store or get_store()

    @property
    def runner(self) -> CommandRunner:
        """The command runner."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam."""
        return self._fs or get_fs()

    @property
    def platform(self) -> Platform:
        """What the host is."""
        if self._platform is None:
            self._platform = detect_platform(self.runner, self._host)
        return self._platform

    def _backend(self) -> PackageBackend:
        """
        Returns:
            The package manager backend of this host.
        """
        return backend_for(self.platform, runner=self._runner, fs=self._fs, host=self._host)

    def _policy(self) -> tuple[str, ...]:
        """
        Returns:
            The policy in force.
        """
        return policy(self._config)

    # ------------------------------------------------------------ reading

    def _owner_of(self, domain: str) -> str:
        """
        Name the application whose list applies to a domain.

        Args:
            domain: An application's domain.

        Returns:
            The domain itself, or the application a preview belongs to: a preview is built and
            run with the packages its parent needs.
        """
        app = self.store.get_app(domain)
        return app.preview_parent if app is not None and app.preview_parent else domain

    def _installed_of(self, names: Sequence[str]) -> set[str]:
        """
        Ask the host which of some packages it has. Changes nothing.

        Args:
            names: Package names.

        Returns:
            The ones installed; none when the host's packages are not managed here.
        """
        if not names:
            return set()
        try:
            backend = self._backend()
            argv = backend.installed_argv(names)
        except UnsupportedHostError:
            return set()
        return backend.installed_names(backend.probe(argv), names)

    def state(self, domain: str) -> PackageState:
        """
        Say what an application declares and what the host has of it.

        Args:
            domain: The application's domain.

        Returns:
            Its state.

        Raises:
            NoustError: The application is unknown.
        """
        app = self.store.get_app(domain)
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        record = app_packages_store.get(domain, store=self.store)
        declared = record.packages if record is not None else ()
        allow = self._policy()
        platform = self.platform
        supported = platform.updates_supported
        have = self._installed_of(declared) if supported else set()
        return PackageState(
            domain=domain,
            declared=declared,
            installed=tuple(name for name in declared if name in have),
            missing=tuple(name for name in declared if name not in have),
            refused=tuple(name for name in declared if not _allowed(name, allow)),
            policy=allow,
            manager=platform.family.value,
            supported=supported,
            updated_at=record.updated_at if record is not None else None,
            updated_by=record.updated_by if record is not None else None,
        )

    # ------------------------------------------------------------ changing

    def _refuse_unsuitable(self, domain: str) -> None:
        """
        Refuse an application whose process does not run on the host's own packages.

        Args:
            domain: The application's domain.

        Raises:
            NoustError: It is unknown.
            ValidationError: It is a preview, a stack or a container.
        """
        app = self.store.get_app(domain)
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        if app.preview_parent:
            raise ValidationError(
                f"{domain} is a preview",
                details=f"It uses the packages of {app.preview_parent}: declare them there.",
            )
        if is_compose_backed(app.app_type):
            raise ValidationError(
                f"{domain} runs as {app.app_type}, in containers of its own",
                details="What it needs is installed in its image: put the packages in its "
                "Dockerfile.",
            )

    def set(
        self,
        domain: str,
        packages: Sequence[str],
        *,
        actor: str,
        install: bool = True,
        logger: Logger | None = None,
    ) -> PackageChange:
        """
        Declare the packages an application needs, replacing the list, and install them.

        Args:
            domain: The application's domain.
            packages: The names; an empty list forgets them.
            actor: Who asks, for the record.
            install: Install what is missing now. Off, the next update does.
            logger: Where progress goes.

        Returns:
            What was done.

        Raises:
            ValidationError: A name is not one, the policy refuses it, or the application
                is a preview or runs in containers.
            DeploymentError: The package manager failed, or does not install here.
            AppBusyError: Another operation is running on the application.
        """
        require_server_role("Applications")
        self._refuse_unsuitable(domain)
        names = validate_packages(packages)
        allow = self._policy()
        refused = [name for name in names if not _allowed(name, allow)]
        if refused:
            raise ValidationError(
                f"This server does not let applications declare: {', '.join(refused)}",
                details=f"{POLICY_KEY} allows: {', '.join(allow)}. Declare packages from that "
                "list, or ask the security officer to extend it: noust config set "
                f"{POLICY_KEY} '[{', '.join([*allow, *refused])}]'.",
                field="packages",
            )
        with app_lock(domain, "packages"):
            previous = app_packages_store.get(domain, store=self.store)
            before = previous.packages if previous is not None else ()
            # Installed first: a list the host could not satisfy is not recorded as what the
            # application needs, so a failed install leaves things as they were.
            installed_now, output = (
                self._install(domain, names, actor=actor, logger=logger)
                if (install and names)
                else ((), "")
            )
            app_packages_store.put(domain, names, updated_by=actor, store=self.store)
            if names != before:
                audit.record(
                    "apps.packages",
                    target=f"app:{domain}",
                    details={"packages": list(names), "previous": list(before), "by": actor},
                )
        return PackageChange(self.state(domain), installed_now, output)

    def install(self, domain: str, *, actor: str, logger: Logger | None = None) -> PackageChange:
        """
        Install the declared packages the host lacks.

        Args:
            domain: The application's domain.
            actor: Who asks, for the record.
            logger: Where progress goes.

        Returns:
            What was done; nothing when nothing is missing.

        Raises:
            DeploymentError: The package manager failed, or does not install here.
            ValidationError: The policy no longer allows a declared package.
            AppBusyError: Another operation is running on the application.
        """
        require_server_role("Applications")
        self._refuse_unsuitable(domain)
        record = app_packages_store.get(domain, store=self.store)
        names = record.packages if record is not None else ()
        with app_lock(domain, "packages"):
            installed_now, output = self._install(domain, names, actor=actor, logger=logger)
        return PackageChange(self.state(domain), installed_now, output)

    def ensure(self, domain: str, *, logger: Logger | None = None) -> tuple[str, ...]:
        """
        Make sure the packages an application needs are on the host, before it is built.

        What a deployment calls. An application that declares none costs one read of the
        store and no process.

        Args:
            domain: The application being built; a preview takes its parent's list.
            logger: Where progress goes.

        Returns:
            The packages installed now.

        Raises:
            DeploymentError: The package manager failed, or does not install here: the update
                stops before it builds, and what served before keeps serving.
            ValidationError: The policy no longer allows a declared package.
        """
        owner = self._owner_of(domain)
        record = app_packages_store.get(owner, store=self.store)
        if record is None or not record.packages:
            return ()
        installed_now, _output = self._install(
            owner, record.packages, actor="deploy", logger=logger
        )
        return installed_now

    def forget(self, domain: str) -> None:
        """
        Forget an application's list, as its deletion does. Installed packages stay.

        Args:
            domain: The application's domain.
        """
        app_packages_store.put(domain, (), updated_by=None, store=self.store)

    # ------------------------------------------------------------ installing

    def _install(
        self, domain: str, names: Sequence[str], *, actor: str, logger: Logger | None
    ) -> tuple[tuple[str, ...], str]:
        """
        Install the packages among some that the host lacks.

        Args:
            domain: The application they are for.
            names: The declared names.
            actor: Who asks, for the record.
            logger: Where progress goes.

        Returns:
            The packages installed and the package manager's output; nothing and no output
            when all were there.

        Raises:
            ValidationError: The policy no longer allows one of them.
            DeploymentError: The host's packages are not managed by Noust, or the package
                manager failed (its own output is in the error).
        """
        if not names:
            return (), ""
        allow = self._policy()
        refused = [name for name in names if not _allowed(name, allow)]
        if refused:
            raise ValidationError(
                f"This server no longer lets applications declare: {', '.join(refused)}",
                details=f"{POLICY_KEY} allows: {', '.join(allow)}. Remove them from "
                f"{domain}'s list (noust app packages remove), or extend the policy.",
                field="packages",
            )
        platform = self.platform
        if not platform.updates_supported:
            raise DeploymentError(
                f"{domain} needs system packages and this server does not install them",
                details=f"{platform.why_updates_unsupported()} Install by hand: {', '.join(names)}",
            )
        have = self._installed_of(names)
        missing = tuple(name for name in names if name not in have)
        if not missing:
            return (), ""
        backend = self._backend()
        if logger is not None:
            logger.info(f"Installing system packages for {domain}: {', '.join(missing)}")
        result = self.runner.run(
            backend.install_lean_argv(missing), env=backend.env(), timeout=INSTALL_TIMEOUT
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if not result.success:
            raise DeploymentError(
                f"Could not install the system packages {domain} needs: {', '.join(missing)}",
                details="The package manager refused; its own output is below. Check the "
                "names exist for this distribution (noust app packages show lists them).",
                output=output,
            )
        audit.record(
            "apps.packages_installed",
            target=f"app:{domain}",
            details={"packages": list(missing), "by": actor, "manager": platform.family.value},
        )
        return missing, output
