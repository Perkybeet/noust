# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The text MySQL and MariaDB replication is made of: account hosts, statements and status.

Pure text in, text out. The two servers agree on what replication is and disagree on every word
for it: ``CHANGE MASTER TO`` against ``CHANGE REPLICATION SOURCE TO``, ``MASTER_USE_GTID`` against
``SOURCE_AUTO_POSITION``, ``Slave_IO_Running`` against ``Replica_IO_Running``. The differences are
here, once, so the manager that drives them reads the same for both
(:mod:`~noust.managers.database.replication_mysql`).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping

from noust.managers.database.replication_base import literal

__all__ = [
    "account_host",
    "change_source",
    "dump_options",
    "parse_version",
    "parse_vertical",
    "pick",
    "start_statement",
    "status_statement",
    "stop_statement",
]

#: ``11.4.5-MariaDB-ubu2404``, ``8.4.3``, ``10.11.6-MariaDB-0+deb12u1``.
_VERSION = re.compile(r"(?:5\.5\.5-)?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")

#: One line of ``\G`` output: ``  Slave_IO_Running: Yes``.
_FIELD = re.compile(r"\s*(?P<key>[A-Za-z_]+):\s?(?P<value>.*)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """
    Read a server's version.

    Args:
        text: What ``SELECT VERSION()`` printed.

    Returns:
        ``(major, minor, patch)``, or None when it is not a version.
    """
    match = _VERSION.match(text.strip())
    if match is None:
        return None
    return int(match.group("major")), int(match.group("minor")), int(match.group("patch"))


def account_host(source: str) -> str:
    """
    Write the host part of the replication account for the network a replica connects from.

    Args:
        source: An address or a network in CIDR form (as ``pg_hba.replica_address`` gives it).

    Returns:
        The address for a single host, or ``network/netmask`` for an IPv4 network, which both
        servers accept as an account host.

    Raises:
        ValueError: When it is an IPv6 network wider than one host (no server has a form for it).
    """
    network = ipaddress.ip_network(source, strict=False)
    if network.num_addresses == 1:
        return str(network.network_address)
    if network.version == 4:
        return f"{network.network_address}/{network.netmask}"
    raise ValueError("An IPv6 network cannot be the host of a MySQL account; give one address")


def forget_binary_log(version: tuple[int, int, int]) -> str:
    """
    Write the statement that empties a MySQL server's binary log and its GTID history.

    A replica is loaded from a dump whose position is set by ``SET @@GLOBAL.GTID_PURGED``, which
    needs the history empty. ``RESET MASTER`` was renamed in 8.4 (and in 8.0.32, which has both).

    Args:
        version: The server's version.

    Returns:
        The statement.
    """
    if version >= (8, 4, 0):
        return "RESET BINARY LOGS AND GTIDS;"
    return "RESET MASTER;"


def start_statement(mariadb: bool) -> str:
    """
    Args:
        mariadb: Whether the server is MariaDB.

    Returns:
        The statement that starts replicating.
    """
    return "START SLAVE;" if mariadb else "START REPLICA;"


def stop_statement(mariadb: bool, thread: str = "") -> str:
    """
    Args:
        mariadb: Whether the server is MariaDB.
        thread: ``IO_THREAD`` or ``SQL_THREAD`` to stop only that one.

    Returns:
        The statement that stops replicating.
    """
    verb = "SLAVE" if mariadb else "REPLICA"
    return f"STOP {verb}{' ' + thread if thread else ''};"


def status_statement(mariadb: bool) -> str:
    """
    Args:
        mariadb: Whether the server is MariaDB.

    Returns:
        The statement that reports on replicating (MySQL 8.4 removed the old spelling).
    """
    return "SHOW SLAVE STATUS" if mariadb else "SHOW REPLICA STATUS"


def change_source(
    mariadb: bool,
    *,
    host: str,
    port: int,
    account: str,
    password: str,
    authority: str,
) -> str:
    """
    Write the statement that points a server at its primary, over verified TLS.

    Args:
        mariadb: Whether the server is MariaDB.
        host: The primary's name; the certificate must cover it.
        port: The primary's port.
        account: The replication account.
        password: Its password.
        authority: The file of the authority the primary's certificate is checked against.

    Returns:
        The statement. It carries the password: it goes to the server on standard input.
    """
    if mariadb:
        return (
            f"CHANGE MASTER TO MASTER_HOST = {literal(host)}, MASTER_PORT = {int(port)}, "
            f"MASTER_USER = {literal(account)}, MASTER_PASSWORD = {literal(password)}, "
            "MASTER_USE_GTID = slave_pos, MASTER_SSL = 1, "
            f"MASTER_SSL_CA = {literal(authority)}, MASTER_SSL_VERIFY_SERVER_CERT = 1, "
            "MASTER_CONNECT_RETRY = 10;"
        )
    return (
        f"CHANGE REPLICATION SOURCE TO SOURCE_HOST = {literal(host)}, SOURCE_PORT = {int(port)}, "
        f"SOURCE_USER = {literal(account)}, SOURCE_PASSWORD = {literal(password)}, "
        "SOURCE_AUTO_POSITION = 1, SOURCE_SSL = 1, "
        f"SOURCE_SSL_CA = {literal(authority)}, SOURCE_SSL_VERIFY_SERVER_CERT = 1, "
        "SOURCE_CONNECT_RETRY = 10;"
    )


def dump_options(mariadb: bool, authority: str) -> list[str]:
    """
    Write the client options that connect over verified TLS.

    Args:
        mariadb: Whether the client is MariaDB's.
        authority: The file of the authority the server's certificate is checked against.

    Returns:
        The options, for ``mysql`` and ``mysqldump`` alike.
    """
    if mariadb:
        return ["--ssl", f"--ssl-ca={authority}", "--ssl-verify-server-cert"]
    return ["--ssl-mode=VERIFY_IDENTITY", f"--ssl-ca={authority}"]


def parse_vertical(text: str) -> dict[str, str]:
    """
    Read the first row of a status printed one field per line (``mysql -E``).

    Args:
        text: The client's output.

    Returns:
        The row's fields, empty when the output holds no row (a server that replicates nothing).
    """
    row: dict[str, str] = {}
    seen_row = False
    for line in text.splitlines():
        if line.startswith("***"):
            if seen_row:
                break
            seen_row = True
            continue
        match = _FIELD.fullmatch(line)
        if seen_row and match is not None:
            row[match.group("key")] = match.group("value").strip()
    return row


def pick(row: Mapping[str, str], *names: str) -> str:
    """
    Read a field of a status by whichever of its names the server uses.

    ``Slave_IO_Running`` is ``Replica_IO_Running`` on MySQL 8.0.22 and later, and
    ``Master_SSL_Allowed`` is ``Source_SSL_Allowed``.

    Args:
        row: The status.
        *names: The names to try, in order.

    Returns:
        The first one present, or an empty string.
    """
    for name in names:
        if name in row:
            return row[name]
    return ""
