# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A machine with two web servers on it, for the tests of moving sites between them.

Nothing here runs a process. :class:`FakeHost` is a command runner that remembers which unit is
running and which starts at boot, which Apache modules are enabled and which sites ``a2ensite`` has
linked, and answers the configuration tests the managers ask; a test makes one of them fail by
naming it. The managers are the real ones, pointed at temporary configuration trees.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from noust.core.runner import CommandResult, FakeRunner
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import APACHE_BACKEND, NGINX_BACKEND

UNITS = {"nginx": "nginx", "apache": "apache2"}


class FakeHost(FakeRunner):
    """
    The units, modules and links of a machine with nginx and Apache.

    Attributes:
        active: Which units are running.
        boot: Which units start at boot.
        installed: Which units exist.
        modules: The Apache modules enabled.
        refuse: ``{(action, unit): message}``: a systemctl action that fails.
        broken: ``{web server: message}``: the configuration test of the whole server fails.
        rejects: ``{web server: message}``: the test of one candidate site fails.
    """

    def __init__(self, apache_available: Path, apache_enabled: Path) -> None:
        super().__init__()
        self.active = {"nginx": True, "apache2": False}
        self.boot = {"nginx": True, "apache2": False}
        self.installed = {"nginx": True, "apache2": True}
        self.modules: list[str] = []
        self.refuse: dict[tuple[str, str], str] = {}
        self.broken: dict[str, str] = {}
        self.rejects: dict[str, str] = {}
        self._apache_available = apache_available
        self._apache_enabled = apache_enabled

    def _lookup(
        self,
        argv: Sequence[str],
        user: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        result = super()._lookup(argv, user, env)
        a = tuple(str(item) for item in argv)
        if a[0] == "systemctl":
            return self._systemctl(a, result)
        if a[0] in ("a2enmod", "a2dismod"):
            for module in a[1:]:
                if a[0] == "a2enmod" and module not in self.modules:
                    self.modules.append(module)
                if a[0] == "a2dismod" and module in self.modules:
                    self.modules.remove(module)
            return result
        if a[0] in ("a2ensite", "a2dissite"):
            link = self._apache_enabled / a[1]
            if a[0] == "a2ensite":
                link.parent.mkdir(parents=True, exist_ok=True)
                if not link.is_symlink():
                    link.symlink_to(self._apache_available / a[1])
            elif link.is_symlink():
                link.unlink()
            return result
        if a[:2] == ("nginx", "-t"):
            return self._test("nginx", a, result, candidate="-c" in a)
        if a[0] == "apache2ctl":
            return self._test("apache", a, result, candidate="-f" in a)
        return result

    def _test(
        self, server: str, argv: tuple[str, ...], result: CommandResult, *, candidate: bool
    ) -> CommandResult:
        message = (self.rejects if candidate else self.broken).get(server)
        if message is None:
            return replace(result, stdout="Syntax OK" if server == "apache" else "", exit_code=0)
        return replace(result, stderr=message, exit_code=1)

    def _systemctl(self, a: tuple[str, ...], result: CommandResult) -> CommandResult:
        action = a[1]
        if action == "show":
            state = "loaded" if self.installed.get(a[-1], True) else "not-found"
            return replace(result, stdout=f"LoadState={state}\n")
        unit = a[-1]
        if action == "is-active":
            return replace(
                result,
                stdout="active\n" if self.active.get(unit) else "inactive\n",
                exit_code=0 if self.active.get(unit) else 3,
            )
        if action == "is-enabled":
            return replace(result, stdout="enabled\n" if self.boot.get(unit) else "disabled\n")
        refused = self.refuse.get((action, unit))
        if refused is not None:
            return replace(result, stderr=refused, exit_code=1)
        if action in ("start", "restart"):
            self.active[unit] = True
        elif action == "stop":
            self.active[unit] = False
        elif action == "reload" and not self.active.get(unit):
            return replace(result, stderr=f"Unit {unit} is not active", exit_code=1)
        elif action == "enable":
            self.boot[unit] = True
        elif action == "disable":
            self.boot[unit] = False
        return result

    def called(self, *prefix: str) -> list[tuple[str, ...]]:
        """
        Args:
            prefix: The start of an argv.

        Returns:
            Every call that began with it, in order.
        """
        return [call for call in self.calls if call[: len(prefix)] == prefix]


@dataclass
class TwoServers:
    """
    A machine's two web servers, over temporary trees.

    Attributes:
        host: The fake machine.
        nginx: nginx's manager.
        apache: Apache's manager.
        root: The directory holding both trees.
    """

    host: FakeHost
    nginx: NginxManager
    apache: ApacheManager
    root: Path
    probes: list[tuple[str, str | None]] = field(default_factory=list)
    unanswered: set[str] = field(default_factory=set)

    @property
    def probe(self) -> Callable[..., bool]:
        """
        Returns:
            A health probe that answers for every site but the ones named in ``unanswered``.
        """

        def ask(url: str, *, host: str | None = None, **_: object) -> bool:
            self.probes.append((url, host))
            return host not in self.unanswered

        return ask

    @property
    def tls(
        self,
    ) -> Callable[[str, str, Callable[[int], bool]], Callable[[float], tuple[bool, str]]]:
        """
        Returns:
            A TLS check that answers for every site but the ones named in ``unanswered``.
        """

        def make(
            domain: str, path: str, accept: Callable[[int], bool]
        ) -> Callable[[float], tuple[bool, str]]:
            def run(timeout: float) -> tuple[bool, str]:
                self.probes.append((f"https://{domain}{path}", domain))
                if domain in self.unanswered:
                    return False, f"https://{domain}{path}: connection refused"
                return True, ""

            return run

        return make


def two_servers(root: Path) -> TwoServers:
    """
    Build nginx's and Apache's managers over temporary trees and one fake machine.

    Args:
        root: A temporary directory.

    Returns:
        The two servers, nginx running and Apache stopped, as a machine whose operator has not
        switched yet.
    """
    nginx_backend = replace(
        NGINX_BACKEND,
        sites_available=root / "etc/nginx/sites-available",
        sites_enabled=root / "etc/nginx/sites-enabled",
        upstreams_dir=root / "etc/nginx/noust-upstreams",
        main_config=None,
    )
    apache_backend = replace(
        APACHE_BACKEND,
        sites_available=root / "etc/apache2/sites-available",
        sites_enabled=root / "etc/apache2/sites-enabled",
        main_config=None,
    )
    host = FakeHost(apache_backend.sites_available, apache_backend.sites_enabled)
    return TwoServers(
        host,
        NginxManager(backend=nginx_backend, runner=host),
        ApacheManager(backend=apache_backend, runner=host),
        root,
    )
