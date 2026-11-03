# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who depends on a piece of the server's software, asked at the moment of asking.

Each function is the registry's answer (:mod:`noust.managers.server.components`) to "what would stop
working if this went": it reads the store or the manager that already knows (the web server's sites,
the tunnels, the published ports, the zones of Noust's own DNS), and writes no query of its own for
something another module answers. A function **raises** when it cannot tell, and the software manager
turns that into a refusal: not knowing what depends on a package is not knowing that nothing does.

A dependent is a name, never a sentence: the console says it in its own words.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Dependent",
    "apache_users",
    "backup_destination_users",
    "certbot_users",
    "dns_zone_users",
    "docker_users",
    "mongodb_users",
    "mysql_users",
    "nginx_users",
    "node_users",
    "php_users",
    "postgresql_users",
    "redis_users",
    "stream_users",
    "tunnel_users",
    "waf_users",
]


@dataclass(frozen=True)
class Dependent:
    """
    Something that stops working without a component.

    Attributes:
        kind: What sort of thing: ``application``, ``site``, ``database``, ``tunnel``,
            ``published_port``, ``certificate``, ``zone``, ``destination``, ``instance``,
            ``console``.
        names: Which ones, by the name the operator knows them by.
    """

    kind: str
    names: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The dependent for the API and the CLI.
        """
        return {"kind": self.kind, "names": list(self.names)}


def _found(kind: str, names: list[str]) -> list[Dependent]:
    """
    Args:
        kind: The kind of dependent.
        names: Their names; none means nothing depends.

    Returns:
        One dependent of that kind, or none.
    """
    return [Dependent(kind, tuple(sorted(names)))] if names else []


def _behind(webserver: str) -> list[Dependent]:
    """
    Args:
        webserver: ``nginx`` or ``apache``.

    Returns:
        The applications and the sites of no application that are behind it.
    """
    from noust.core.webserver_switch import names_behind

    applications, sites = names_behind(webserver)
    return [*_found("application", applications), *_found("site", sites)]


def nginx_users() -> list[Dependent]:
    """
    Returns:
        What is behind nginx: applications, sites, the published ports (nginx's stream blocks)
        and the console when it is published through it.
    """
    from noust.core.console_exposure_store import console_published

    found = _behind("nginx")
    found += stream_users()
    if console_published():
        found.append(Dependent("console", ("the console",)))
    return found


def apache_users() -> list[Dependent]:
    """
    Returns:
        What is behind Apache: applications and sites.
    """
    return _behind("apache")


def certbot_users() -> list[Dependent]:
    """
    Returns:
        The certificates certbot issued and renews. An uploaded certificate is not certbot's.
    """
    from noust.core.certificate_store import SOURCE_CUSTOM
    from noust.managers.cert_manager import CertManager

    names = [c.name for c in CertManager().list_certificates() if c.source != SOURCE_CUSTOM]
    return _found("certificate", names)


def _applications_of(types: frozenset[str] | set[str] | tuple[str, ...]) -> list[str]:
    """
    Args:
        types: Application types.

    Returns:
        The domains of the applications of those types.
    """
    from noust.core.store import get_store

    return [app.domain for app in get_store().list_apps() if app.app_type in types]


def node_users() -> list[Dependent]:
    """
    Returns:
        The applications that build or run on the server's Node.js: the Node types and monorepos.
    """
    from noust.core.dependencies import NODE_RUNTIME_TYPES

    return _found("application", _applications_of(NODE_RUNTIME_TYPES))


def php_users() -> list[Dependent]:
    """
    Returns:
        The applications that run on PHP-FPM.
    """
    from noust.deployers.helpers.php_fpm import PHP_FPM_TYPE

    return _found("application", _applications_of({PHP_FPM_TYPE}))


def docker_users() -> list[Dependent]:
    """
    Returns:
        The applications that run as containers or Compose projects, and the database engines
        Noust runs in containers beside the host's.
    """
    from noust.core.db_instance_store import DbInstanceStore
    from noust.core.store import COMPOSE_BACKED_TYPES

    instances = [record.name for record in DbInstanceStore().all()]
    return [
        *_found("application", _applications_of(COMPOSE_BACKED_TYPES)),
        *_found("instance", instances),
    ]


def _databases_of(engine: str) -> list[Dependent]:
    """
    Args:
        engine: A host engine's name.

    Returns:
        The databases it holds (the store's and the engine's own), or, when it runs and cannot
        be read, a dependent that says so: it is not known that it holds nothing.
    """
    from noust.core.exceptions import EngineInUseError
    from noust.managers.database import engine_removal
    from noust.managers.database.registry import get_db_manager

    manager = get_db_manager(engine)
    if manager is None:
        return []
    try:
        return _found("database", engine_removal.databases_held(manager))
    except EngineInUseError as exc:
        return [Dependent("unreadable", (exc.message,))]


def postgresql_users() -> list[Dependent]:
    """
    Returns:
        The databases PostgreSQL holds.
    """
    return _databases_of("postgresql")


def mysql_users() -> list[Dependent]:
    """
    Returns:
        The databases MySQL or MariaDB holds.
    """
    return _databases_of("mysql")


def redis_users() -> list[Dependent]:
    """
    Returns:
        The databases Redis or Valkey holds.
    """
    return _databases_of("redis")


def mongodb_users() -> list[Dependent]:
    """
    Returns:
        The databases MongoDB holds.
    """
    return _databases_of("mongodb")


def tunnel_users() -> list[Dependent]:
    """
    Returns:
        The Cloudflare tunnels, each of which runs its connector.
    """
    from noust.core.tunnel_store import TunnelStore

    return _found("tunnel", [tunnel.name for tunnel in TunnelStore().all()])


def stream_users() -> list[Dependent]:
    """
    Returns:
        The published TCP and UDP ports, each an nginx ``stream`` block.
    """
    from noust.core.stream_store import StreamStore

    return _found("published_port", [exposure.name for exposure in StreamStore().all()])


def waf_users() -> list[Dependent]:
    """
    Returns:
        The applications whose site has the web application firewall on.
    """
    from noust.managers.site_policy import SitePolicyManager

    manager = SitePolicyManager()
    return _found(
        "application",
        [name for name in manager.records.domains_with_policy() if manager.get(name).waf],
    )


def dns_zone_users() -> list[Dependent]:
    """
    Returns:
        The zones Noust's own name server answers for.
    """
    from noust.core.authdns_store import AuthDnsStore

    return _found("zone", [zone.name for zone in AuthDnsStore().zones()])


def backup_destination_users() -> list[Dependent]:
    """
    Returns:
        The remote backup destinations, which are uploaded to with rclone.
    """
    from noust.core.store import get_store

    return _found("destination", [item.name for item in get_store().list_backup_destinations()])
