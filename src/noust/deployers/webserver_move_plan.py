# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What moving applications to the other web server would do (backlog item 105, wave 4).

``webserver`` in the configuration is the web server **new** applications are put behind, and each
application remembers its own (``apps.webserver``). Moving one is a decision about a machine that
has one pair of ports, 80 and 443, and one web server holding them, so a plan has to say more than
"this site, translated":

- **Which sites, and what each becomes.** The site of an application is translated with
  :mod:`~noust.managers.siteconf.translate`; everything the new server will not do is a finding the
  operator reads before anything is touched. A site the operator wrote is never rewritten: it moves
  only when the operator has already written its equivalent for the new server.
- **Whether the new server is serving.** When it is (it holds the ports, because the operator set
  it up so, or because an earlier move left a site behind), a site moves on its own. When it is
  not, a move is a **handover** of the ports from one server to the other, and that is only
  possible for all the sites at once: moving some would leave the others without a web server.
  The plan says which it is, and refuses the half-way one, naming the command that does it whole.
- **What would be left without a server.** In a handover, every site the old server holds that is
  not moving (an operator's, the console's public name) stops being served; the plan names each.

Planning reads and tests nothing: :func:`~noust.deployers.webserver_move.move` asks the new server
whether it accepts each translated site, and puts everything back if it does not or if a site does
not answer.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.exceptions import NoustError, ValidationError
from noust.core.notification_state import NotificationState
from noust.core.server_text import ServerText, say
from noust.core.store import App, NoustStore, Site, get_store, runs_as_instances
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.server_provision import ServerProvisioner
from noust.managers.site_policy import AUTH_DIR_NAME
from noust.managers.siteconf.translate import Finding, Translation, kind_of, translate
from noust.managers.webserver import INTERNAL_SITES, WebServerManager

_log = logging.getLogger(__name__)

#: The two web servers, as the store records them.
SERVERS = ("nginx", "apache")

#: The application types a site of Apache's cannot serve (they run as Compose stacks).
_NGINX_ONLY_TYPES = frozenset({"docker-compose", "container"})


def other_than(webserver: str) -> str:
    """
    Args:
        webserver: ``nginx`` or ``apache``.

    Returns:
        The other one.

    Raises:
        ValidationError: It is neither.
    """
    if webserver not in SERVERS:
        raise ValidationError(
            f"Unsupported web server: {webserver!r}",
            details=f"Use one of: {', '.join(SERVERS)}.",
            field="to",
        )
    return "apache" if webserver == "nginx" else "nginx"


@dataclass(frozen=True)
class SitePlan:
    """
    One site, and what moving it would do.

    Attributes:
        domain: The application's domain, or the site's.
        kind: ``app`` or ``site`` (one that belongs to no application).
        source: The web server it is behind now.
        target: The one it would move to.
        source_path: Its file now.
        target_path: Where its file would be.
        operator_site: Whether the file now is the operator's, not Noust's.
        keeps_operator_site: Whether the operator already wrote its equivalent at ``target_path``,
            which the move leaves as it is.
        translation: The site in the new server's words; None when there is no file to translate.
        text: What would be written (the translation with the account files re-pointed); empty
            when nothing is written.
        blockers: What stops this site from moving.
    """

    domain: str
    kind: str
    source: str
    target: str
    source_path: str
    target_path: str
    operator_site: bool = False
    keeps_operator_site: bool = False
    translation: Translation | None = None
    text: str = ""
    blockers: tuple[ServerText, ...] = ()

    @property
    def findings(self) -> tuple[Finding, ...]:
        """What the new site does not carry over as it was."""
        return self.translation.findings if self.translation is not None else ()

    @property
    def writes(self) -> bool:
        """Whether the move writes a file for this site."""
        return bool(self.text) and not self.keeps_operator_site

    @property
    def movable(self) -> bool:
        """Whether nothing stops this site."""
        return not self.blockers and not (self.translation is not None and self.translation.blocked)

    @property
    def losses(self) -> tuple[Finding, ...]:
        """What the operator has to accept."""
        return self.translation.losses if self.translation is not None and self.writes else ()

    def to_dict(self, *, with_text: bool = False) -> dict[str, Any]:
        """
        Args:
            with_text: Include the translated text.

        Returns:
            The site as plain data, for the API and ``--json``.
        """
        data: dict[str, Any] = {
            "domain": self.domain,
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "operator_site": self.operator_site,
            "keeps_operator_site": self.keeps_operator_site,
            "movable": self.movable,
            "blockers": [_sentence(item) for item in self.blockers],
            "findings": [item.to_dict() for item in self.findings],
            "modules": list(self.translation.modules) if self.translation else [],
        }
        if with_text:
            data["text"] = self.text
        return data


@dataclass(frozen=True)
class MovePlan:
    """
    What moving a set of sites to a web server would do.

    Attributes:
        source: The web server they are behind.
        target: The one they would move to.
        mode: ``live`` when the target is serving (a site moves on its own) or ``handover`` when it
            is not (the ports pass from one server to the other, for every site at once).
        source_running: Whether the old server is running now.
        target_installed: Whether the new one is installed.
        target_running: Whether the new one is running now.
        install_command: How to install the new one, when it is not.
        sites: What each site becomes, in the order they move.
        blockers: What stops the move as a whole.
        skipped: Sites asked for that are behind the target already.
    """

    source: str
    target: str
    mode: str
    source_running: bool
    target_installed: bool
    target_running: bool
    install_command: str
    sites: tuple[SitePlan, ...]
    blockers: tuple[ServerText, ...] = ()
    skipped: tuple[str, ...] = ()
    left_behind: tuple[str, ...] = field(default=())

    @property
    def blocked(self) -> bool:
        """Whether anything stops the move."""
        return bool(self.blockers) or any(not site.movable for site in self.sites)

    @property
    def empty(self) -> bool:
        """Whether there is nothing to move."""
        return not self.sites

    @property
    def losses(self) -> tuple[tuple[str, Finding], ...]:
        """Every loss of every site, with the site it belongs to."""
        return tuple((site.domain, loss) for site in self.sites for loss in site.losses)

    @property
    def modules(self) -> tuple[str, ...]:
        """The Apache modules the sites need."""
        found: set[str] = set()
        for site in self.sites:
            if site.translation is not None and site.writes:
                found.update(site.translation.modules)
        return tuple(sorted(found))

    def confirmation(self) -> ServerText:
        """
        Returns:
            What the operator agrees to by going ahead, as a sentence of the catalog.
        """
        code = (
            "webserver_move.confirm_handover"
            if self.mode == "handover"
            else "webserver_move.confirm_live"
        )
        return say(
            code, source=display(self.source), target=display(self.target), count=len(self.sites)
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data, for the API and ``--json``.
        """
        return {
            "source": self.source,
            "target": self.target,
            "mode": self.mode,
            "source_running": self.source_running,
            "target_installed": self.target_installed,
            "target_running": self.target_running,
            "install_command": self.install_command,
            "sites": [site.to_dict() for site in self.sites],
            "blockers": [_sentence(item) for item in self.blockers],
            "skipped": list(self.skipped),
            "left_behind": list(self.left_behind),
            "blocked": self.blocked,
            "modules": list(self.modules),
            "losses": len(self.losses),
            "confirmation": _sentence(self.confirmation()),
        }


def _sentence(text: ServerText) -> dict[str, Any]:
    """
    Args:
        text: A sentence of the catalog.

    Returns:
        It as the API sends it: the code, the values and the words in English.
    """
    return {**text.to_dict(), "text": text.text, "hint": text.hint}


@dataclass
class Managers:
    """
    The two web servers a move is between.

    Attributes:
        source: The one the sites are behind.
        target: The one they would move to.
    """

    source: WebServerManager
    target: WebServerManager

    @classmethod
    def between(cls, source: str, target: str) -> Managers:
        """
        Args:
            source: ``nginx`` or ``apache``.
            target: The other.

        Returns:
            The managers of both, over the process-wide runner and filesystem.
        """
        make = {"nginx": NginxManager, "apache": ApacheManager}
        return cls(make[source](), make[target]())


def _running(manager: WebServerManager) -> bool:
    try:
        return bool(manager.is_installed() and manager.is_running())
    except NoustError as exc:
        _log.debug("Could not tell whether %s runs: %s", manager.backend.name, exc)
        return False


def _command_to_move(target: str) -> str:
    return f"noust webserver migrate --to {target} --all"


def feature_sites() -> dict[str, str]:
    """
    Find the sites Noust sets up for a feature of nginx's own.

    Returns:
        ``{domain: "console" | "status"}`` for the console's public name and the status page's.
    """
    found: dict[str, str] = {}
    try:
        from noust.managers.console_exposure import ConsoleExposure

        state = ConsoleExposure().state()
        if state is not None:
            found[state.domain] = "console"
    except (NoustError, sqlite3.Error, OSError) as exc:
        _log.debug("Could not read the console's exposure: %s", exc)
    try:
        record = NotificationState().get_json("status-page", "site")
        if isinstance(record, dict) and record.get("domain"):
            found[str(record["domain"])] = "status"
    except (NoustError, sqlite3.Error, OSError) as exc:
        _log.debug("Could not read the status page's site: %s", exc)
    return found


class _Planner:
    """Plan a move between two web servers."""

    def __init__(self, store: NoustStore, managers: Managers) -> None:
        self.store = store
        self.managers = managers
        self.source = managers.source.backend.name
        self.target = managers.target.backend.name
        self.features = feature_sites()

    def site(self, domain: str, kind: str, app: App | None) -> SitePlan:
        """
        Plan one site.

        Args:
            domain: Its domain.
            kind: ``app`` or ``site``.
            app: The application, for the checks that are about it.

        Returns:
            What moving it would do.
        """
        source, target = self.managers.source, self.managers.target
        source_path = source.config_path(domain)
        target_path = target.config_path(domain)
        blockers: list[ServerText] = []
        blockers += self._kind_blockers(domain, app)
        if not source_path.exists():
            blockers.append(
                say("webserver_move.no_site", domain=domain, source=display(self.source))
            )
            return self._plan(domain, kind, source_path, target_path, blockers)
        operator_site = not source.site_is_noust(domain)
        keeps = False
        if operator_site:
            keeps = target.site_exists(domain) and not target.site_is_noust(domain)
            if not keeps:
                blockers.append(
                    say(
                        "webserver_move.operator_site",
                        domain=domain,
                        path=str(source_path),
                        target=display(self.target),
                        target_path=str(target_path),
                    )
                )
        text = source.get_site_config(domain) or ""
        translation = translate(
            text,
            source=kind_of(self.source),
            target=kind_of(self.target),
            domain=domain,
        )
        body = self._repointed(translation.text) if translation.text else ""
        return self._plan(
            domain,
            kind,
            source_path,
            target_path,
            blockers,
            operator_site=operator_site,
            keeps=keeps,
            translation=translation,
            text=body,
        )

    def _plan(
        self,
        domain: str,
        kind: str,
        source_path: Path,
        target_path: Path,
        blockers: Sequence[ServerText],
        *,
        operator_site: bool = False,
        keeps: bool = False,
        translation: Translation | None = None,
        text: str = "",
    ) -> SitePlan:
        return SitePlan(
            domain=domain,
            kind=kind,
            source=self.source,
            target=self.target,
            source_path=str(source_path),
            target_path=str(target_path),
            operator_site=operator_site,
            keeps_operator_site=keeps,
            translation=translation,
            text=text,
            blockers=tuple(blockers),
        )

    def _kind_blockers(self, domain: str, app: App | None) -> list[ServerText]:
        blockers: list[ServerText] = []
        if self.target == "apache" and app is not None:
            if app.app_type in _NGINX_ONLY_TYPES or app.compose_project:
                blockers.append(say("webserver_move.compose_only", domain=domain))
            if runs_as_instances(app):
                blockers.append(
                    say(
                        "webserver_move.zero_downtime",
                        domain=domain,
                        command=f"noust app zero-downtime {domain} off",
                    )
                )
        feature = self.features.get(domain)
        if feature == "console":
            blockers.append(
                say(
                    "webserver_move.console_site",
                    domain=domain,
                    command="noust console unexpose",
                )
            )
        elif feature == "status":
            blockers.append(
                say(
                    "webserver_move.status_site",
                    domain=domain,
                    command="noust probe status-page unpublish",
                )
            )
        return blockers

    def _repointed(self, text: str) -> str:
        """
        Point the files a site names inside the old server's configuration at the new one's.

        The accounts of a password-protected site are written for each web server beside its own
        configuration, so the path the translation copied is the old server's.

        Args:
            text: The translated site.

        Returns:
            The text naming the new server's account directory.
        """
        old = self.managers.source.backend.sites_available.parent / AUTH_DIR_NAME
        new = self.managers.target.backend.sites_available.parent / AUTH_DIR_NAME
        return text.replace(f"{old}/", f"{new}/")

    def left_behind(self, moving: set[str]) -> list[str]:
        """
        Find what the old server serves that is not moving.

        Args:
            moving: The domains that are.

        Returns:
            The names of the sites of the old server that are enabled and are not among them, and
            that the new server does not have a site of the operator's for.
        """
        source, target = self.managers.source, self.managers.target
        names: list[str] = []
        suffix = source.backend.config_suffix
        for entry in source.list_sites():
            if not entry.enabled or entry.domain in moving:
                continue
            names.append(entry.domain)
        for internal in sorted(INTERNAL_SITES):
            link = source.sites_enabled / f"{internal}{suffix}"
            if link.exists() or link.is_symlink():
                names.append(internal)
        covered = []
        for name in names:
            there = target.sites_available / f"{name}{target.backend.config_suffix}"
            if there.exists() and not _carries_marker(there):
                covered.append(name)
        return [name for name in names if name not in covered]


def _carries_marker(path: Path) -> bool:
    from noust.core import paths

    try:
        return paths.carries_unit_marker(path.read_text(encoding="utf-8", errors="replace")[:2000])
    except OSError:
        return False


def display(webserver: str) -> str:
    """
    Args:
        webserver: ``nginx`` or ``apache``.

    Returns:
        How a sentence names it.
    """
    return "Apache" if webserver == "apache" else "nginx"


def plan_move(
    target: str,
    domains: Sequence[str] | None = None,
    *,
    store: NoustStore | None = None,
    managers: Managers | None = None,
    provisioner: ServerProvisioner | None = None,
) -> MovePlan:
    """
    Work out what moving sites to a web server would do. Changes nothing.

    Args:
        target: The web server to move to, ``nginx`` or ``apache``.
        domains: The sites to move. None is everything that is behind the other server, which is
            what a handover is made of.
        store: The store; the process-wide one by default.
        managers: The two web servers (tests); the real ones by default.
        provisioner: Says whether the target is installed and how to install it (tests).

    Returns:
        The plan.

    Raises:
        ValidationError: ``target`` is not a web server Noust drives, or a domain asked for is not
            behind the other server nor the target.
    """
    source = other_than(target)
    store = store or get_store()
    managers = managers or Managers.between(source, target)
    planner = _Planner(store, managers)

    apps = {app.domain: app for app in store.list_apps()}
    # A site row that names an application's domain is that application's, linked or not.
    standalone = {
        site.domain: site
        for site in store.list_sites()
        if site.app_id is None and site.domain not in apps and _on(site, source)
    }
    wanted: list[tuple[str, str, App | None]] = []
    skipped: list[str] = []
    if domains is None:
        wanted = [(d, "app", a) for d, a in sorted(apps.items()) if a.webserver == source]
        wanted += [(d, "site", None) for d in sorted(standalone)]
    else:
        for raw in dict.fromkeys(domains):
            domain = raw.strip().lower()
            app = apps.get(domain)
            if app is not None and app.webserver == source:
                wanted.append((domain, "app", app))
            elif domain in standalone:
                wanted.append((domain, "site", None))
            elif (app is not None and app.webserver == target) or _site_on(store, domain, target):
                skipped.append(domain)
            else:
                raise ValidationError.from_text(
                    say(
                        "webserver_move.unknown",
                        domain=domain,
                        source=display(source),
                        command="noust webserver status",
                    ),
                    field="domains",
                )

    sites = tuple(planner.site(domain, kind, app) for domain, kind, app in wanted)
    target_installed = bool(managers.target.is_installed())
    provision = provisioner or ServerProvisioner(runner=managers.target.runner)
    install = provision.check_webserver(target)
    target_running = _running(managers.target)
    source_running = _running(managers.source)
    mode = "live" if target_running else "handover"

    blockers: list[ServerText] = []
    if not target_installed:
        blockers.append(
            say(
                "webserver_move.target_missing",
                target=display(target),
                command=install.command or f"install {target} with your package manager",
            )
        )
    left: list[str] = []
    if mode == "handover" and sites:
        everything = len(sites) == len([a for a in apps.values() if a.webserver == source]) + len(
            standalone
        )
        if not everything:
            blockers.append(
                say(
                    "webserver_move.partial_handover",
                    target=display(target),
                    count=len(sites),
                    total=len([a for a in apps.values() if a.webserver == source])
                    + len(standalone),
                    command=_command_to_move(target),
                )
            )
        elif source_running:
            left = planner.left_behind({site.domain for site in sites})
            for name in left:
                blockers.append(
                    say(
                        "webserver_move.left_behind",
                        name=name,
                        source=display(source),
                        target=display(target),
                        target_path=str(
                            managers.target.sites_available
                            / f"{name}{managers.target.backend.config_suffix}"
                        ),
                    )
                )
    return MovePlan(
        source=source,
        target=target,
        mode=mode,
        source_running=source_running,
        target_installed=target_installed,
        target_running=target_running,
        install_command=install.command,
        sites=sites,
        blockers=tuple(blockers),
        skipped=tuple(skipped),
        left_behind=tuple(left),
    )


def _on(site: Site, webserver: str) -> bool:
    return site.webserver == webserver


def _site_on(store: NoustStore, domain: str, webserver: str) -> bool:
    site = store.get_site(domain)
    return site is not None and site.webserver == webserver
