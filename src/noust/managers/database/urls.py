# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one place a database connection URL is built.

There were five: each engine manager formatted its own, none of them encoded
the user or the password, and three of them printed the engine's default port
whatever the server really listened on. A generated password with an ``@`` or
a ``#`` in it (the old generator produced both) turned
``postgresql://app:pa@ss#w@localhost/db`` into a URL no driver parses the way
it was meant. :class:`~noust.deployers.helpers.databases.DatabaseCredentials`
already did it right; now it, every manager and every endpoint call this.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, urlencode

#: URL scheme per engine name. The registry's canonical names are the schemes
#: every driver expects, with MariaDB speaking the MySQL protocol.
SCHEMES: Mapping[str, str] = {
    "postgresql": "postgresql",
    "mysql": "mysql",
    "redis": "redis",
    "mongodb": "mongodb",
}

#: Redis's implicit account: a URL for it carries the password alone.
REDIS_DEFAULT_USER = "default"

#: What a URL shows in place of a password that must not be displayed.
MASK = "********"


def connection_url(
    engine: str,
    *,
    database: str,
    user: str | None,
    password: str | None,
    host: str = "localhost",
    port: int,
    options: Mapping[str, str] | None = None,
) -> str:
    """
    Build a connection URL with every credential percent-encoded.

    Args:
        engine: Canonical engine name (``postgresql``, ``mysql``, ``redis``,
            ``mongodb``); anything else is used as the scheme unchanged.
        database: Database name, or a Redis slot number.
        user: User name. For Redis, None or ``default`` means the password
            alone (``requirepass``).
        password: The password, or None for a URL without one.
        host: Host the client connects to. An IPv6 address is bracketed.
        port: The port the server really listens on, never an assumed one.
        options: Query parameters, such as MongoDB's ``authSource``.

    Returns:
        ``<scheme>://<user>:<password>@<host>:<port>/<database>[?options]``.
    """
    scheme = SCHEMES.get(engine, engine)
    name = user
    if engine == "redis" and (not user or user == REDIS_DEFAULT_USER):
        name = ""

    credentials = ""
    if name or password:
        credentials = quote(name or "", safe="")
        if password is not None:
            credentials += ":" + quote(password, safe="")
        credentials += "@"

    address = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"{scheme}://{credentials}{address}:{port}/{quote(str(database), safe='')}"
    if options:
        url += "?" + urlencode(dict(options))
    return url


def masked(url: str, password: str | None) -> str:
    """
    Hide a password inside a URL built by :func:`connection_url`.

    Args:
        url: The URL.
        password: The password it carries, as given to :func:`connection_url`.

    Returns:
        The URL with the encoded password replaced by :data:`MASK`.
    """
    if not password:
        return url
    return url.replace(":" + quote(password, safe="") + "@", ":" + MASK + "@", 1)
