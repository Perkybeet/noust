# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Cloudflare Tunnels Noust runs, and the public names each one carries.

Two tables of the networking fragment of schema v14. ``tunnels`` is one tunnel: the DNS
account that created it, the Cloudflare account it lives in, its id there, the unit that
runs its connector, and how far its creation or removal got (``state``), so an
interrupted one is finished from the row. ``tunnel_hostnames`` is a public name the
tunnel carries and the local service it is carried to. The connector's token is a secret
file (:mod:`noust.core.secrets`), never a column.

:class:`TunnelStore` is the only reader and writer; what may be routed, and how, is
:mod:`noust.managers.tunnels`'s.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore, get_store

#: The tunnel is being created: the row exists and some of what it needs may not.
CREATING = "creating"

#: The tunnel is created and its connector runs.
READY = "ready"

#: The tunnel is being taken down.
REMOVING = "removing"


@dataclass(frozen=True)
class Tunnel:
    """
    A Cloudflare Tunnel.

    Attributes:
        name: What it is called here (and at Cloudflare).
        provider: The DNS account whose token manages it.
        account_id: The Cloudflare account it lives in.
        tunnel_id: Its id at Cloudflare; empty until Cloudflare has created it.
        unit: The systemd unit that runs its connector, without the suffix.
        state: ``creating``, ``ready`` or ``removing``.
        created_at: When it was recorded.
        created_by: Who asked.
    """

    name: str
    provider: str
    account_id: str
    tunnel_id: str
    unit: str
    state: str = CREATING
    created_at: str = ""
    created_by: str | None = None

    @property
    def target(self) -> str:
        """The name a public hostname's CNAME points at."""
        return f"{self.tunnel_id}.cfargotunnel.com"

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The tunnel for the API and the CLI.
        """
        return {
            "name": self.name,
            "provider": self.provider,
            "account_id": self.account_id,
            "tunnel_id": self.tunnel_id,
            "unit": self.unit,
            "target": self.target if self.tunnel_id else "",
            "state": self.state,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class TunnelRoute:
    """
    A public name a tunnel carries.

    Attributes:
        tunnel: The tunnel.
        hostname: The public name.
        service: Where the connector sends it: ``http://localhost:80``.
        app_domain: The application it is for, when it is for one.
        created_at: When it was recorded.
    """

    tunnel: str
    hostname: str
    service: str
    app_domain: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The route for the API and the CLI.
        """
        return {
            "tunnel": self.tunnel,
            "hostname": self.hostname,
            "service": self.service,
            "app_domain": self.app_domain,
            "created_at": self.created_at,
        }


class TunnelStore:
    """
    Read and write the tunnels and their routes.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def all(self) -> list[Tunnel]:
        """
        Returns:
            Every tunnel, by name.
        """
        rows = (
            self.store._get_connection().execute("SELECT * FROM tunnels ORDER BY name").fetchall()
        )
        return [_tunnel(row) for row in rows]

    def get(self, name: str) -> Tunnel | None:
        """
        Args:
            name: The tunnel's name.

        Returns:
            It, or None when there is none of that name.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM tunnels WHERE name = ?", (name,))
            .fetchone()
        )
        return None if row is None else _tunnel(row)

    def create(self, tunnel: Tunnel) -> Tunnel:
        """
        Record a tunnel that is being created.

        Args:
            tunnel: What to keep; its ``created_at`` is set here.

        Returns:
            The recorded tunnel.

        Raises:
            sqlite3.IntegrityError: The name is taken, or the DNS account is unknown.
        """
        created = tunnel.created_at or _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO tunnels
                    (name, provider, account_id, tunnel_id, unit, state, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tunnel.name,
                    tunnel.provider,
                    tunnel.account_id,
                    tunnel.tunnel_id,
                    tunnel.unit,
                    tunnel.state,
                    created,
                    tunnel.created_by,
                ),
            )
        return replace(tunnel, created_at=created)

    def set_state(self, name: str, state: str, tunnel_id: str | None = None) -> Tunnel | None:
        """
        Record how far a creation or a removal got.

        Args:
            name: The tunnel.
            state: ``creating``, ``ready`` or ``removing``.
            tunnel_id: Its id at Cloudflare, once known.

        Returns:
            The tunnel now, or None when there is none of that name.
        """
        with self.store._transaction() as cursor:
            if tunnel_id is None:
                cursor.execute("UPDATE tunnels SET state = ? WHERE name = ?", (state, name))
            else:
                cursor.execute(
                    "UPDATE tunnels SET state = ?, tunnel_id = ? WHERE name = ?",
                    (state, tunnel_id, name),
                )
        return self.get(name)

    def delete(self, name: str) -> bool:
        """
        Forget a tunnel and its routes.

        Args:
            name: The tunnel.

        Returns:
            True when it existed.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM tunnels WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def routes(self, tunnel: str) -> list[TunnelRoute]:
        """
        Args:
            tunnel: The tunnel.

        Returns:
            Its public names, by name.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM tunnel_hostnames WHERE tunnel = ? ORDER BY hostname", (tunnel,))
            .fetchall()
        )
        return [_route(row) for row in rows]

    def route_for(self, hostname: str) -> TunnelRoute | None:
        """
        Args:
            hostname: A public name.

        Returns:
            The route that carries it, on whichever tunnel, or None.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM tunnel_hostnames WHERE hostname = ?", (hostname,))
            .fetchone()
        )
        return None if row is None else _route(row)

    def set_route(self, route: TunnelRoute) -> TunnelRoute:
        """
        Add a route, or change where an existing one goes.

        Args:
            route: What to keep; its ``created_at`` is set here when it is new.

        Returns:
            The recorded route.

        Raises:
            sqlite3.IntegrityError: The name is carried by another tunnel.
        """
        created = route.created_at or _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO tunnel_hostnames (tunnel, hostname, app_domain, service, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tunnel, hostname) DO UPDATE SET
                    app_domain = excluded.app_domain,
                    service = excluded.service
                """,
                (route.tunnel, route.hostname, route.app_domain, route.service, created),
            )
        return replace(route, created_at=created)

    def delete_route(self, tunnel: str, hostname: str) -> bool:
        """
        Args:
            tunnel: The tunnel.
            hostname: The public name.

        Returns:
            True when it existed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM tunnel_hostnames WHERE tunnel = ? AND hostname = ?",
                (tunnel, hostname),
            )
            return cursor.rowcount > 0

    def routes_of_app(self, app_domain: str) -> list[TunnelRoute]:
        """
        Args:
            app_domain: An application's primary domain.

        Returns:
            The routes made for it, on any tunnel.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM tunnel_hostnames WHERE app_domain = ? ORDER BY hostname",
                (app_domain,),
            )
            .fetchall()
        )
        return [_route(row) for row in rows]


def _tunnel(row: sqlite3.Row) -> Tunnel:
    """
    Args:
        row: A ``tunnels`` row.

    Returns:
        The tunnel it holds.
    """
    return Tunnel(
        name=row["name"],
        provider=row["provider"],
        account_id=row["account_id"],
        tunnel_id=row["tunnel_id"],
        unit=row["unit"],
        state=row["state"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


def _route(row: sqlite3.Row) -> TunnelRoute:
    """
    Args:
        row: A ``tunnel_hostnames`` row.

    Returns:
        The route it holds.
    """
    return TunnelRoute(
        tunnel=row["tunnel"],
        hostname=row["hostname"],
        service=row["service"],
        app_domain=row["app_domain"],
        created_at=row["created_at"],
    )


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
