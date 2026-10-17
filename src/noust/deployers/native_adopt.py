# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adopt a site that already runs on nginx or Apache, without rebuilding it.

An operator who comes from another panel, or from a server set up by hand, has sites that were
never deployed by Noust: an nginx site in front of a systemd unit they wrote, a WordPress on
PHP-FPM, a folder of files. ``noust app adopt-site`` and ``POST /api/apps/adopt-site`` (checklist
B-85) register one as an application so that it shows up in the list, its state and its logs are
read, and its unit is restarted from the same place as the rest. What it does not do is the point:

- **Nothing is written outside the store.** The site, the unit, the directory and the certificate
  stay byte for byte as they were; the adoption is a row in ``apps`` and one in ``app_adoption``.
- **Nothing is built.** An adopted application has no deployer: an update, a rollback, a rebuild, a
  domain or a certificate change is refused with the reason (:func:`refuse_to_build`), because
  those would replace what the operator wrote with what Noust generates.
- **The unit is the operator's.** Noust may start, stop, restart it and read its logs
  (:attr:`~noust.managers.service_manager.ServiceManager.ADOPTED_OPERATIONS`); it never rewrites,
  hardens, limits, enables or deletes it, and a unit it ships or manages is never adopted
  (:meth:`~noust.managers.service_manager.ServiceManager.adoption_problem`).
- **Deleting it forgets it.** The unit, the site, the certificate and the files are left where
  they are (:func:`release`).

What a site is is read with the one parser (:mod:`noust.managers.siteconf`) and what its backend
is with the one topology (:class:`~noust.managers.site_topology.TopologyProbe`): the unit that
listens on the port the site proxies to is proposed, not assumed, and the operator can name
another. :func:`discover` lists the sites that could be adopted; :func:`plan_adoption` does every
check and changes nothing; :func:`adopt` writes what a plan says.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import is_rehearsal
from noust.core.native_adoption_store import (
    AdoptionKind,
    AdoptionRecord,
    adopted_units,
    forget_record,
    get_record,
    set_record,
)
from noust.core.store import AppLayout, AppStatus, AppType, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.compose_adopt import checked_directory, find_site, refuse_shared_directory
from noust.deployers.helpers.registration import StoreRegistrar
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ServiceManager
from noust.managers.site_topology import Backend, TopologyProbe, default_probe, include_reader
from noust.managers.siteconf import Kind, ParseError, SiteStructure, parse, structure
from noust.managers.siteconf.model import Location, Server
from noust.managers.webserver import WebServerManager, config_serves_tls, list_all_sites
from noust.validators.domain import DOMAIN_PATTERN, validate_domain

#: The properties asked of the unit being adopted. ``WorkingDirectory`` is where its code is.
_UNIT_FIELDS = ("Id", "ActiveState", "WorkingDirectory", "FragmentPath")

#: Names a site file may have: a name :meth:`NoustStore.set_app_site_name` records.
_SITE_NAME = DOMAIN_PATTERN


@dataclass(frozen=True)
class SiteShape:
    """
    What a site file is, read from its structure.

    Attributes:
        kind: ``proxy``, ``php`` or ``static``; None when it serves nothing Noust can adopt.
        root: The document root the site names, when it names one.
        upstream: What it hands requests to: the local address of the proxy, or the FastCGI
            address.
        backends: Every local address the site proxies to, the one that decides first.
        ssl: Whether a server of the site presents a certificate.
        reason: Why ``kind`` is None, or what else the operator should know.
    """

    kind: AdoptionKind | None
    root: str | None = None
    upstream: str | None = None
    backends: tuple[Backend, ...] = ()
    ssl: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class NativeSite:
    """
    A site on this server that no application records.

    Attributes:
        site: The file in the sites directory.
        webserver: ``nginx`` or ``apache``.
        config_path: Its absolute path.
        enabled: Whether the web server serves it.
        domains: The names it answers on.
        kind: ``proxy``, ``php`` or ``static``; None when it is not one Noust can adopt.
        root: The directory it serves, when it names one.
        upstream: What it hands requests to.
        port: The local port it proxies to.
        unit: The unit that listens on that port, proposed.
        unit_active: Whether that unit is running.
        ssl: Whether it presents a certificate.
        adoptable: Whether :func:`plan_adoption` could proceed from what is known here.
        reason: Why it is not adoptable, or what to know about it.
    """

    site: str
    webserver: str
    config_path: str
    enabled: bool
    domains: tuple[str, ...]
    kind: AdoptionKind | None
    root: str | None
    upstream: str | None
    port: int | None
    unit: str | None
    unit_active: bool | None
    ssl: bool
    adoptable: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The site as JSON-ready values.
        """
        data = dataclasses.asdict(self)
        data["domains"] = list(self.domains)
        return data


@dataclass(frozen=True)
class AdoptionPlan:
    """
    What adopting a site would record, found without changing anything.

    Attributes:
        domain: The application's domain.
        app_name: The name derived from it, by which the application is addressed.
        kind: ``proxy``, ``php`` or ``static``.
        site: The operator's site file.
        site_name: That file's name when it is not the domain, recorded so the domain resolves
            to it; None otherwise.
        webserver: ``nginx`` or ``apache``.
        enabled: Whether the web server serves the site.
        ssl: Whether it presents a certificate.
        root: The directory registered as the application's.
        port: The local port the site proxies to; None for PHP and static sites.
        upstream: What the site hands requests to.
        unit: The operator's unit; None for a site the web server alone serves.
        unit_active: Whether that unit runs now.
        warnings: What the operator should know, one sentence each.
    """

    domain: str
    app_name: str
    kind: AdoptionKind
    site: str
    site_name: str | None
    webserver: str
    enabled: bool
    ssl: bool
    root: Path
    port: int | None
    upstream: str | None
    unit: str | None
    unit_active: bool | None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as JSON-ready values.
        """
        data = dataclasses.asdict(self)
        data["root"] = str(self.root)
        data["warnings"] = list(self.warnings)
        return data


@dataclass(frozen=True)
class AdoptionResult:
    """
    What an adoption did.

    Attributes:
        plan: What was adopted.
        adopted: False for a rehearsal, which records nothing.
    """

    plan: AdoptionPlan
    adopted: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan's values, with what was done.
        """
        return {**self.plan.to_dict(), "adopted": self.adopted}


def refuse_to_build(domain: str | None = None, what: str = "build") -> ValidationError:
    """
    The refusal of anything that would replace what an adopted site's operator wrote.

    Args:
        domain: The adopted application; None where only the type is known (a deployer asked for).
        what: The operation, as a verb (``build``, ``update``, ``roll back``).

    Returns:
        The error to raise.
    """
    way_out = f"noust delete {domain}" if domain else "noust delete <domain>"
    return ValidationError(
        f"{domain} is an adopted site: Noust does not {what} it"
        if domain
        else f"Noust does not {what} an adopted site",
        details=(
            "It was running before Noust met it, and its unit, site and files are yours. Noust "
            "watches it, restarts it and reads its logs. To have Noust build and deploy it, "
            f"stop managing it ({way_out} forgets it and removes nothing) and create it from "
            "its source with noust create."
        ),
    )


# -- Reading a site -------------------------------------------------------------------------


def _managers() -> list[WebServerManager]:
    """
    Returns:
        The two web servers a site may be written for; a seam for tests.
    """
    return [NginxManager(), ApacheManager()]


def _walk(locations: Sequence[Location]) -> list[Location]:
    """
    Args:
        locations: A server's locations.

    Returns:
        Every location, nested ones included, in file order.
    """
    found: list[Location] = []
    for location in locations:
        found.append(location)
        found.extend(_walk(location.locations))
    return found


def _usable_root(value: str | None) -> str | None:
    """
    Args:
        value: A ``root`` the site names.

    Returns:
        It when it is a plain absolute directory, None when it is a variable or relative.
    """
    if not value or "$" in value or not value.startswith("/"):
        return None
    return value.rstrip("/") or "/"


def read_shape(
    text: str,
    manager: WebServerManager,
    *,
    domain: str | None,
    probe: TopologyProbe,
) -> SiteShape:
    """
    Say what a site file serves.

    Args:
        text: The file's text.
        manager: The web server that owns it.
        domain: The name being adopted; the servers answering on it decide. None considers every
            server of the file.
        probe: Says what is behind each local address.

    Returns:
        The shape. A file the web server would not read, or one that only redirects or answers
        with a fixed response, has no kind and says why.
    """
    kind: Kind = "apache" if manager.backend.name == "apache" else "nginx"
    extra = [manager.backend.upstreams_dir] if manager.backend.upstreams_dir else []
    try:
        model = structure(
            parse(text, kind),
            read_include=include_reader(manager.sites_available.parent, extra_roots=extra),
        )
    except ParseError as exc:
        return SiteShape(
            kind=None, reason=f"the web server would not read it (line {exc.line}: {exc.message})"
        )
    servers = [s for s in model.servers if domain is None or domain in s.names] or list(
        model.servers
    )
    serving = [s for s in servers if s.returns is None]
    if not serving:
        return SiteShape(
            kind=None,
            ssl=any(s.tls is not None for s in servers),
            reason="it only redirects or answers with a fixed response: nothing runs behind it",
        )
    ssl = any(s.tls is not None for s in servers) or config_serves_tls(text)
    locations = [loc for s in serving for loc in _walk(s.locations)]
    roots = [_usable_root(s.root) for s in serving] + [
        _usable_root(loc.target.root) for loc in locations
    ]
    root = next((r for r in roots if r), None)

    topology = probe.topology(_only(model, serving), certificates=False)
    local = [b for b in topology.backends if b.local and b.port is not None]
    proxied = [
        loc for loc in locations if loc.target.kind == "proxy" or loc.target.upstream is not None
    ]
    fastcgi = next((loc.target.address for loc in locations if loc.target.kind == "fastcgi"), None)

    ordered = _ordered(local, proxied)
    if ordered:
        return SiteShape(
            kind="proxy", root=root, upstream=ordered[0].address, backends=tuple(ordered), ssl=ssl
        )
    if fastcgi:
        return SiteShape(kind="php", root=root, upstream=fastcgi, ssl=ssl)
    if root:
        return SiteShape(kind="static", root=root, ssl=ssl)
    return SiteShape(
        kind=None,
        ssl=ssl,
        reason="it names no directory and no local address to hand requests to",
    )


def _only(model: SiteStructure, serving: Sequence[Server]) -> SiteStructure:
    """
    Args:
        model: A site's structure.
        serving: The servers being judged.

    Returns:
        The structure with only those servers, so a redirect-only server's addresses do not count.
    """
    return dataclasses.replace(model, servers=list(serving))


def _ordered(local: Sequence[Backend], proxied: Sequence[Location]) -> list[Backend]:
    """
    Order the local backends so the one behind the site's root comes first.

    Args:
        local: The local backends, in order of appearance.
        proxied: The locations that proxy.

    Returns:
        The backend of the location ``/`` first when there is one, then the rest.
    """
    root_ids = {loc.id for loc in proxied if loc.path == "/" and loc.modifier in ("", "=")}
    first = [b for b in local if root_ids.intersection(b.locations)]
    return [*first, *[b for b in local if b not in first]]


# -- Finding the unit -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    """What systemd says of the unit being adopted."""

    name: str
    active: bool
    working_directory: str | None


def _unit_facts(services: ServiceManager, unit: str) -> _Unit:
    """
    Read a unit's state and where its code is.

    Args:
        services: The service manager.
        unit: The unit, without ``.service``.

    Returns:
        What systemd reports; a unit it did not answer for is inactive with no directory.
    """
    answer = services.describe_units([unit], _UNIT_FIELDS).get(unit, {})
    working = answer.get("WorkingDirectory", "").strip().lstrip("-!")
    return _Unit(
        name=unit,
        active=ServiceManager.state_is_running(answer.get("ActiveState", "")),
        working_directory=working if working.startswith("/") else None,
    )


def _proposed_unit(shape: SiteShape, store: NoustStore) -> tuple[str | None, str | None]:
    """
    Pick the unit that serves the site's backend, from who owns its port.

    Args:
        shape: What the site is.
        store: The store, to tell an application of Noust's from an operator's unit.

    Returns:
        ``(unit, reason)``: the unit without ``.service`` when one listens, and otherwise why
        none can be proposed.
    """
    if not shape.backends:
        return None, None
    backend = shape.backends[0]
    owner = backend.owner
    if owner is None:
        return None, f"nothing listens on {backend.address} now"
    if owner.kind == "app":
        what = (
            "an adopted site"
            if owner.app and get_record(owner.app, store=store) is not None
            else "a Noust application"
        )
        return None, f"{owner.app} is {what} and already listens on {backend.address}"
    if owner.kind in ("compose", "container"):
        what = owner.project or owner.container or "a container"
        return None, (
            f"a container ({what}) publishes {backend.address}: adopt a Compose stack with "
            "noust app adopt"
        )
    if owner.unit:
        return owner.unit.removesuffix(".service"), None
    return None, f"{backend.address} is held by a process outside any service ({owner.process})"


# -- Discovery ------------------------------------------------------------------------------


def discover(
    *,
    managers: Sequence[WebServerManager] | None = None,
    store: NoustStore | None = None,
    probe: TopologyProbe | None = None,
    services: ServiceManager | None = None,
) -> list[NativeSite]:
    """
    List the sites on this server that no application records, and what each one is.

    Read-only: it reads the sites directory, asks ``ss`` and ``docker ps`` who holds each local
    port, and asks systemd about the proposed units.

    Args:
        managers: The web servers to look in; nginx and Apache by default.
        store: The store.
        probe: Says what is behind each local address; the process-wide one by default.
        services: The service manager.

    Returns:
        One entry per site the operator wrote that is not an application, in the order the sites
        are listed. Noust's own sites and those an application records are not here.
    """
    store = store if store is not None else get_store()
    web = list(managers) if managers is not None else _managers()
    probe = probe if probe is not None else default_probe()
    services = services if services is not None else ServiceManager()
    by_backend = {m.backend.name: m for m in web}
    taken = {a.domain for a in store.list_apps()}
    adopted = adopted_units(store)
    found: list[NativeSite] = []
    for row in list_all_sites(managers=web, store=store):
        if row.noust_managed or row.app is not None or not row.config_path:
            continue
        manager = by_backend.get(row.webserver)
        if manager is None:
            continue
        try:
            text = Path(row.config_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            # A file that cannot be read serves nothing anyone can adopt.
            continue
        domains = tuple(row.server_names)
        shape = read_shape(text, manager, domain=None, probe=probe)
        unit, why = _proposed_unit(shape, store)
        reason = shape.reason or why
        active: bool | None = None
        if unit is not None:
            problem = services.adoption_problem(unit)
            if problem is not None:
                reason, unit = f"{unit}: {problem}", None
            elif unit in adopted:
                reason, unit = f"{unit} is already an adopted site's unit", None
            else:
                active = _unit_facts(services, unit).active
        clash = sorted(set(domains) & taken)
        if clash:
            reason = f"{clash[0]} is already an application"
        adoptable = (
            shape.kind is not None
            and not clash
            and (shape.kind != "proxy" or unit is not None)
            and _SITE_NAME.fullmatch(row.name) is not None
        )
        found.append(
            NativeSite(
                site=row.name,
                webserver=row.webserver,
                config_path=row.config_path,
                enabled=row.enabled,
                domains=domains,
                kind=shape.kind,
                root=shape.root,
                upstream=shape.upstream,
                port=shape.backends[0].port if shape.backends else None,
                unit=unit,
                unit_active=active,
                ssl=shape.ssl,
                adoptable=adoptable,
                reason=reason,
            )
        )
    return found


# -- The plan -------------------------------------------------------------------------------


def _find(
    domain: str, site: str | None, managers: Sequence[WebServerManager]
) -> tuple[WebServerManager, str, str, bool]:
    """
    Find the file that serves a domain, in whichever web server has it.

    Args:
        domain: The domain.
        site: The file the operator named.
        managers: The web servers to look in.

    Returns:
        The manager, the file's name, its text and whether it is enabled.

    Raises:
        ValidationError: No file serves the domain, or more than one web server has one.
    """
    hits: list[tuple[WebServerManager, str, str, bool]] = []
    problems: list[str] = []
    for manager in managers:
        try:
            name, text, enabled = find_site(domain, manager, requested=site)
        except DeploymentError as exc:
            if site is None:
                # Looking by name, the only thing that can be wrong is that more than one file
                # answers on it: the operator picks.
                raise ValidationError(exc.message, details=exc.details, field="site") from exc
            problems.append(str(exc))
            continue
        if name is not None:
            hits.append((manager, name, text, enabled))
    if len(hits) > 1:
        raise ValidationError(
            f"More than one web server has a site that answers on {domain}",
            details=", ".join(f"{m.backend.name}: {n}" for m, n, _, _ in hits)
            + ". Adopt it from the one that serves it; the other's site is not Noust's to judge.",
        )
    if not hits:
        raise ValidationError(
            f"No site in nginx or Apache answers on {domain}",
            details=(
                problems[0]
                if site is not None and problems
                else "Adoption registers a site that already runs. List the ones that could be "
                "adopted with: noust app discover"
            ),
            field="domain",
        )
    return hits[0]


def _directory(
    given: str | None, shape: SiteShape, unit: _Unit | None, domain: str, store: NoustStore
) -> Path:
    """
    Pick the directory registered as the application's and check it.

    Args:
        given: The directory the operator named.
        shape: What the site is.
        unit: The unit's facts, for a site in front of one.
        domain: The domain being adopted.
        store: The store.

    Returns:
        The directory.

    Raises:
        ValidationError: There is none to register, or it is a system directory, Noust's, or
            another application's.
    """
    chosen = given or (unit.working_directory if unit is not None else None) or shape.root
    if not chosen or chosen == "/":
        raise ValidationError(
            f"Noust cannot tell where {domain}'s files are",
            details=(
                "Neither the site's root nor the unit's WorkingDirectory names a directory. "
                "Name it with --path (path in the API)."
            ),
        )
    try:
        path = checked_directory(Path(chosen))
        refuse_shared_directory(path, domain, store)
    except DeploymentError as exc:
        # Shared with the adoption of a stack, where it is a failed deployment; here it is the
        # request that names a directory that is not the site's.
        raise ValidationError(exc.message, details=exc.details, field="path") from exc
    return path


def plan_adoption(
    domain: str,
    *,
    site: str | None = None,
    unit: str | None = None,
    path: str | None = None,
    managers: Sequence[WebServerManager] | None = None,
    store: NoustStore | None = None,
    probe: TopologyProbe | None = None,
    services: ServiceManager | None = None,
) -> AdoptionPlan:
    """
    Find everything adopting a site would record, changing nothing.

    Args:
        domain: The domain the site answers on.
        site: The site file, when more than one could be it.
        unit: The unit the site is in front of; proposed from who listens when None.
        path: The directory of the site's files; the unit's WorkingDirectory or the site's root
            when None.
        managers: The web servers to look in; nginx and Apache by default.
        store: The store checked for the domain.
        probe: Says what is behind each local address.
        services: The service manager the unit is judged with.

    Returns:
        The plan.

    Raises:
        ValidationError: The domain is an application already, no site serves it, the site
            serves nothing Noust can adopt, the unit cannot be one, or the directory is not the
            site's alone.
    """
    domain = validate_domain(domain)
    store = store if store is not None else get_store()
    probe = probe if probe is not None else default_probe()
    services = services if services is not None else ServiceManager()
    if store.get_app(domain) is not None:
        raise ValidationError(
            f"{domain} is already an application",
            details=f"Adopting registers a site Noust has no record of. See it with: noust status {domain}",
            field="domain",
        )
    manager, file_name, text, enabled = _find(
        domain, site, list(managers) if managers is not None else _managers()
    )
    if _SITE_NAME.fullmatch(file_name) is None:
        raise ValidationError(
            f"{file_name!r} is not a site file name Noust can record",
            details="Letters, digits, '-' and '.' only. Rename the file in sites-available and its "
            "link in sites-enabled, reload the web server, and adopt it again.",
        )
    shape = read_shape(text, manager, domain=domain, probe=probe)
    if shape.kind is None:
        raise ValidationError(
            f"{domain}'s site {file_name} serves nothing Noust can adopt",
            details=(shape.reason or "It names no directory and no local address.")
            + " Adopt a site that serves files, PHP, or an application listening on this server.",
        )
    warnings: list[str] = []
    if not enabled:
        warnings.append(f"The site {file_name} is not enabled: the web server does not serve it.")

    chosen: str | None = None
    facts: _Unit | None = None
    if shape.kind == "proxy":
        proposed, why = _proposed_unit(shape, store)
        chosen = unit or proposed
        if chosen is None:
            raise ValidationError(
                f"Noust cannot tell which unit runs {domain}",
                details=(why or "Nothing is known about it")
                + ". Name the unit that runs it with --unit (unit in the API).",
            )
        chosen = chosen.removesuffix(".service")
        problem = services.adoption_problem(chosen)
        if problem is not None:
            raise ValidationError(
                f"{chosen} cannot be adopted as {domain}'s unit",
                details=f"{problem[0].upper()}{problem[1:]}. A unit Noust may restart is one you "
                "wrote by hand in /etc/systemd/system.",
            )
        taken = adopted_units(store)
        if chosen in taken:
            raise ValidationError(
                f"{chosen} already runs an adopted site",
                details=f"It is the unit of the application addressed as {taken[chosen]}.",
            )
        facts = _unit_facts(services, chosen)
        if unit and proposed and chosen != proposed:
            warnings.append(
                f"The site hands requests to {shape.upstream}, which {proposed} holds, not "
                f"{chosen}: the application's state is {chosen}'s."
            )
        if not facts.active:
            warnings.append(f"{chosen} is not running now.")
        others = [b for b in shape.backends[1:] if b.port is not None]
        for backend in others:
            holder = backend.owner.unit if backend.owner and backend.owner.unit else None
            warnings.append(
                f"The site also hands requests to {backend.address}"
                + (f" ({holder})" if holder else "")
                + ", which stays outside this application."
            )
    elif unit:
        raise ValidationError(
            f"{domain}'s site is {shape.kind}: it has no unit to name",
            details="A unit is for a site in front of an application. Leave --unit out.",
        )
    if shape.kind == "php":
        warnings.append(
            "PHP runs in the PHP-FPM every PHP site shares: Noust shows the site and its logs "
            "and never restarts the pool."
        )

    root = _directory(path, shape, facts, domain, store)
    port = shape.backends[0].port if shape.backends else None
    return AdoptionPlan(
        domain=domain,
        app_name=domain_to_app_name(domain),
        kind=shape.kind,
        site=file_name,
        site_name=file_name if file_name != domain else None,
        webserver=manager.backend.name,
        enabled=enabled,
        ssl=shape.ssl,
        root=root,
        port=port,
        upstream=shape.upstream,
        unit=chosen,
        unit_active=facts.active if facts is not None else None,
        warnings=tuple(warnings),
    )


# -- Adopting -------------------------------------------------------------------------------


def adopt(
    plan: AdoptionPlan,
    *,
    actor: str | None = None,
    store: NoustStore | None = None,
) -> AdoptionResult:
    """
    Record what a plan found: the application and what it is.

    Args:
        plan: What :func:`plan_adoption` found.
        actor: Who asked, for the record.
        store: The store written to.

    Returns:
        What was done; nothing under ``--dry-run``.

    Raises:
        ValidationError: The domain became an application meanwhile, or the record could not be
            written; nothing stays registered.
        AppBusyError: Another operation is running on the domain.
    """
    require_server_role("Applications")
    if is_rehearsal():
        return AdoptionResult(plan=plan, adopted=False)
    store = store if store is not None else get_store()
    with app_lock(plan.domain, "adopt"):
        if store.get_app(plan.domain) is not None:
            raise ValidationError(
                f"{plan.domain} became an application while it was being adopted",
                details=f"See it with: noust status {plan.domain}",
            )
        running = plan.unit_active is not False
        StoreRegistrar(store).register_app(
            domain=plan.domain,
            app_type=AppType.ADOPTED.value,
            source=str(plan.root),
            branch=None,
            port=plan.port,
            app_path=plan.root,
            webserver=plan.webserver,
            ssl_enabled=plan.ssl,
            status=(AppStatus.RUNNING if running else AppStatus.STOPPED).value,
            # No process of its own to ask: the web server serves it (the unit-less kinds).
            is_static=plan.unit is None,
            env_vars={},
            layout=AppLayout.INPLACE.value,
        )
        try:
            store.set_app_site_name(plan.domain, plan.site_name)
            set_record(
                plan.domain,
                AdoptionRecord(
                    kind=plan.kind,
                    unit=plan.unit,
                    webserver=plan.webserver,
                    site_file=plan.site,
                    root=str(plan.root),
                    upstream=plan.upstream,
                    adopted_by=actor,
                ),
                store=store,
            )
        except (NoustError, sqlite3.Error):
            # Half an adoption is an application the guards cannot tell from one Noust built.
            store.delete_app(plan.domain)
            raise
    audit.record(
        "apps.adopt_site",
        target=f"app:{plan.domain}",
        details={
            "kind": plan.kind,
            "site": plan.site,
            "unit": plan.unit,
            "root": str(plan.root),
            "webserver": plan.webserver,
        },
    )
    return AdoptionResult(plan=plan, adopted=True)


def adopt_site(
    domain: str,
    *,
    site: str | None = None,
    unit: str | None = None,
    path: str | None = None,
    actor: str | None = None,
    managers: Sequence[WebServerManager] | None = None,
    store: NoustStore | None = None,
    probe: TopologyProbe | None = None,
    services: ServiceManager | None = None,
) -> AdoptionResult:
    """
    Plan an adoption and carry it out.

    Args:
        domain: The domain the site answers on.
        site: The site file.
        unit: The unit the site is in front of.
        path: The directory of the site's files.
        actor: Who asked.
        managers: The web servers to look in.
        store: The store.
        probe: Says what is behind each local address.
        services: The service manager.

    Returns:
        What was done.

    Raises:
        ValidationError: See :func:`plan_adoption` and :func:`adopt`.
    """
    plan = plan_adoption(
        domain,
        site=site,
        unit=unit,
        path=path,
        managers=managers,
        store=store,
        probe=probe,
        services=services,
    )
    return adopt(plan, actor=actor, store=store)


# -- Letting go -----------------------------------------------------------------------------


def adoption_of(domain: str, *, store: NoustStore | None = None) -> AdoptionRecord | None:
    """
    Args:
        domain: An application's domain.
        store: The store.

    Returns:
        What it is when it is an adopted site, else None.
    """
    return get_record(domain, store=store)


def release(domain: str, *, store: NoustStore | None = None) -> tuple[str, ...]:
    """
    Forget an adopted site and leave everything it was exactly where it is.

    Args:
        domain: The adopted application.
        store: The store.

    Returns:
        One sentence per thing that was kept, for the operator.
    """
    store = store if store is not None else get_store()
    record = get_record(domain, store=store)
    app = store.get_app(domain)
    kept: list[str] = []
    if record is not None and record.unit:
        kept.append(f"The unit {record.unit}.service was not touched; it keeps running.")
    if record is not None:
        kept.append(
            f"The {record.webserver} site {record.site_file} and its certificate were not "
            "touched; they keep serving."
        )
    if app is not None and app.app_path:
        kept.append(f"{app.app_path} and everything in it was kept.")
    if not is_rehearsal():
        forget_record(domain, store=store)
    return tuple(kept)
