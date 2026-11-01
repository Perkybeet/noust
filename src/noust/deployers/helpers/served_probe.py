# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking a web server for a site over TLS, as a visitor's browser would.

The health gate asks an application on its own port (``127.0.0.1:3000``). After a site moves to
another web server the question is different: does the new server answer for this name, with the
certificate it presents for it? That needs the name in the TLS handshake (SNI) and in the ``Host``
header, which a request to ``https://127.0.0.1`` cannot carry, so this opens the connection itself.

Nothing leaves the machine: it connects to ``127.0.0.1``, and it does not verify the certificate
(an address is not what a certificate names, and a certificate that is not valid yet is the
operator's to renew, not a reason to put a site back).
"""

from __future__ import annotations

import http.client
import socket
from collections.abc import Callable

from noust.core import tls_policy

#: The web server's TLS port on this machine.
HTTPS_PORT = 443


def request_https(
    domain: str,
    path: str,
    *,
    timeout: float,
    port: int = HTTPS_PORT,
    method: str = "GET",
) -> int:
    """
    Request a path of a site from the web server on this machine, naming the site as a browser does.

    Args:
        domain: The name the site answers on.
        path: The path to request.
        timeout: Seconds the whole exchange may take.
        port: The web server's TLS port.
        method: The request method.

    Returns:
        The status the web server answered with.

    Raises:
        OSError: It could not connect, or the handshake failed.
        http.client.HTTPException: It answered something that is not HTTP.
    """
    context = tls_policy.loopback_client_context()
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=domain) as wrapped:
            connection = http.client.HTTPConnection(domain, timeout=timeout)
            connection.sock = wrapped
            connection.request(method, path, headers={"Host": domain})
            response = connection.getresponse()
            response.read(1024)
            return response.status


def https_check(
    domain: str, path: str, accept: Callable[[int], bool]
) -> Callable[[float], tuple[bool, str]]:
    """
    Build the check of a site over TLS, in the shape the health gate waits on.

    Args:
        domain: The name the site answers on.
        path: The path to request.
        accept: Which statuses mean the site is up.

    Returns:
        A function that, given the seconds it may take, says whether the site answered as wanted and
        what happened when it did not.
    """

    def run(timeout: float) -> tuple[bool, str]:
        try:
            status = request_https(domain, path, timeout=timeout)
        except (OSError, http.client.HTTPException) as exc:
            return False, f"https://{domain}{path} through this server: {exc}"
        if accept(status):
            return True, ""
        return False, f"https://{domain}{path} through this server answered {status}"

    return run
