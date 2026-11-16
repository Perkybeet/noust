# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A plain HTTP port that only says where the console is (item 199).

The app store of a NAS opens an application at ``http://<host>:<its declared port>`` and cannot be
told to use HTTPS, while the console speaks nothing but TLS: the icon opened a page that never loaded.
A central in a container can listen on one more port, ``NOUST_REDIRECT_PORT``, whose whole job is a
``308`` to ``https://<the same host>:<the console's port as seen from outside>/<the same path and
query>``. ``NOUST_PUBLIC_HTTPS_PORT`` is that port: the container does not know the one the
NAS published, and without it the console's own is used. Without ``NOUST_REDIRECT_PORT`` there is no
listener at all.

It is not a way in without TLS. It carries no session, no token and no page: it answers a request for
anything with the same redirect, so a token typed on it would never be sent anywhere that reads it.
It is a door in cleartext, though, and is built as one:

- What it repeats from the request is judged first against an allowlist
  (:mod:`noust.validators.redirect`): the host is a name or an address, the target a path with a query.
  A request that fails it gets an empty ``400`` that does not say what it sent.
- It reads the head of one request, up to 8 KiB and 64 fields, within five seconds, and nothing else:
  no body is parsed or used, a connection serves one request, and a method other than ``GET`` and
  ``HEAD`` is refused, because a ``308`` would make the browser send a body over TLS that has already
  crossed the network in the clear.
- At most 32 connections are served at once; the rest are closed. Whoever the console does not
  answer (``NOUST_ALLOW_IP``, :func:`noust.central.setup.allowlist`) is not answered here either.
- It touches nothing of the console: no route of ``/api``, no file, no state.

It stops with the console and a port it cannot open is said in the log without stopping anything.
"""

from __future__ import annotations

import email.utils
import logging
import re
import socket
import threading
import time
from collections.abc import Callable

from noust.core import paths
from noust.core.exceptions import NoustError, ValidationError
from noust.core.server_text import say
from noust.validators.redirect import (
    MAX_TARGET_LENGTH,
    validate_listener_port,
    validate_redirect_host,
    validate_redirect_target,
)

__all__ = ["RedirectServer", "answer", "from_environment"]

logger = logging.getLogger(__name__)

#: The names the two settings have after ``NOUST_`` (read through :func:`noust.core.paths.getenv`).
REDIRECT_PORT = "REDIRECT_PORT"
PUBLIC_HTTPS_PORT = "PUBLIC_HTTPS_PORT"

#: The most of a request that is read: its line and fields, and room for a long address.
MAX_HEAD_BYTES = 8192
#: The most header fields a request may have.
MAX_FIELDS = 64
#: How many connections are served at once.
MAX_CONNECTIONS = 32
#: How long a connection has to say its request, and to be answered.
TIME_LIMIT_SECONDS = 5.0
#: How long an answered connection is let to finish what it was sending before it is closed, so a
#: client that had more to say is not reset before it reads the answer.
LINGER_SECONDS = 0.5
#: The most that is read and thrown away in that time.
LINGER_BYTES = 65536
#: How often the accept loop looks at whether it was asked to stop.
ACCEPT_POLL_SECONDS = 0.5

_END_OF_HEAD = b"\r\n\r\n"
_METHOD = re.compile(r"[A-Z]{1,20}")
_VERSION = re.compile(r"HTTP/[0-9]\.[0-9]")
_FIELD_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

_REASONS = {
    308: "Permanent Redirect",
    400: "Bad Request",
    405: "Method Not Allowed",
    414: "URI Too Long",
    431: "Request Header Fields Too Large",
    505: "HTTP Version Not Supported",
}


def _response(status: int, *fields: tuple[str, str]) -> bytes:
    """
    Build an answer: a status, its fields, and no body.

    Args:
        status: The status code, one of :data:`_REASONS`.
        *fields: ``(name, value)`` pairs that come first, such as ``Location``.

    Returns:
        The bytes to send. Never kept alive, never cached, never with a cookie.
    """
    lines = [
        f"HTTP/1.1 {status} {_REASONS[status]}",
        *(f"{name}: {value}" for name, value in fields),
        "Cache-Control: no-store",
        "Content-Length: 0",
        "Connection: close",
        f"Date: {email.utils.formatdate(usegmt=True)}",
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def answer(data: bytes, https_port: int) -> bytes | None:
    """
    Decide what to answer to what a client sent.

    A pure function of the bytes: nothing here touches a socket, which is what lets every kind of
    request be tried without one.

    Args:
        data: What was read from the connection, up to its end or its limit.
        https_port: The console's port as the browser reaches it.

    Returns:
        The whole answer, or None when there is nothing to say (the head is not finished and
        still small: the caller waits or gives up).
    """
    end = data.find(_END_OF_HEAD)
    if end == -1:
        return _response(431) if len(data) > MAX_HEAD_BYTES else None
    if end > MAX_HEAD_BYTES:
        return _response(431)
    try:
        lines = data[:end].decode("ascii").split("\r\n")
    except UnicodeDecodeError:
        return _response(400)
    if any(_CONTROL.search(line) for line in lines):
        return _response(400)
    parts = lines[0].split(" ")
    if len(parts) != 3 or not _METHOD.fullmatch(parts[0]) or not _VERSION.fullmatch(parts[2]):
        return _response(400)
    method, target, version = parts
    if version not in ("HTTP/1.0", "HTTP/1.1"):
        return _response(505)
    if len(lines) - 1 > MAX_FIELDS:
        return _response(431)
    hosts: list[str] = []
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator or not _FIELD_NAME.fullmatch(name):
            return _response(400)
        if name.lower() == "host":
            hosts.append(value.strip(" \t"))
    if method not in ("GET", "HEAD"):
        return _response(405, ("Allow", "GET, HEAD"))
    if len(target) > MAX_TARGET_LENGTH:
        return _response(414)
    # Two Host fields are two answers to "where": whoever reads them differently is the attack.
    if len(hosts) != 1:
        return _response(400)
    try:
        wanted = validate_redirect_host(hosts[0])
        path = validate_redirect_target(target)
    except ValidationError:
        return _response(400)
    # The authority is written here, from what was validated, before the path: nothing in the path
    # can become another host, however it starts.
    port = "" if https_port == 443 else f":{https_port}"
    return _response(308, ("Location", f"https://{wanted}{port}{path}"))


class RedirectServer:
    """
    Listen on one port in a thread of the central and redirect whoever asks.

    Args:
        host: The address to listen on, the console's own.
        port: The port; 0 asks the system for one (a test's).
        https_port: The console's port as the browser reaches it.
        allow: Whether a client address is answered at all; everybody when None.
    """

    def __init__(
        self,
        host: str,
        port: int,
        https_port: int,
        allow: Callable[[str], bool] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.https_port = https_port
        self._allow = allow
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def start(self) -> None:
        """
        Open the port and start answering.

        Raises:
            NoustError: When the port cannot be opened (another program holds it).
        """
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen(16)
        except OSError as exc:
            listener.close()
            raise NoustError.from_text(
                say(
                    "redirect.port_open",
                    port=self.port,
                    reason=str(exc.strerror or exc),
                    setting=f"NOUST_{REDIRECT_PORT}",
                )
            ) from exc
        self.port = int(listener.getsockname()[1])
        listener.settimeout(ACCEPT_POLL_SECONDS)
        self._socket = listener
        self._thread = threading.Thread(target=self._serve, name="noust-redirect", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """Accept connections until :meth:`stop`, each on a thread of its own and within the limit."""
        listener = self._socket
        assert listener is not None  # noqa: S101 - start() set it
        while not self._stopping.is_set():
            try:
                connection, peer = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                # The socket was closed by stop().
                break
            if self._allow is not None and not self._allow(str(peer[0])):
                connection.close()
                continue
            if not self._slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(
                target=self._exchange, args=(connection,), name="noust-redirect-1", daemon=True
            ).start()

    def _exchange(self, connection: socket.socket) -> None:
        """
        Read one request's head, answer it, and close.

        Args:
            connection: The accepted connection.
        """
        deadline = time.monotonic() + TIME_LIMIT_SECONDS
        try:
            data = b""
            while _END_OF_HEAD not in data and len(data) <= MAX_HEAD_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                connection.settimeout(remaining)
                chunk = connection.recv(2048)
                if not chunk:
                    return
                data += chunk
            reply = answer(data, self.https_port)
            if reply is None:
                return
            connection.settimeout(max(deadline - time.monotonic(), 0.1))
            connection.sendall(reply)
            connection.shutdown(socket.SHUT_WR)
            self._linger(connection)
        except OSError as exc:
            logger.debug("A request to the redirect port did not finish: %s", exc)
        finally:
            connection.close()
            self._slots.release()

    @staticmethod
    def _linger(connection: socket.socket) -> None:
        """
        Let a client that had more to send finish, so closing does not reset it before it reads.

        What arrives is thrown away, never parsed: a body is not read, only not left unread, and
        what is discarded is bounded in bytes and in time.

        Args:
            connection: The connection that was answered and shut for writing.
        """
        deadline = time.monotonic() + LINGER_SECONDS
        left = LINGER_BYTES
        while left > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            connection.settimeout(remaining)
            try:
                chunk = connection.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            left -= len(chunk)

    def stop(self) -> None:
        """Stop answering and free the port. Safe to call twice."""
        self._stopping.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=ACCEPT_POLL_SECONDS * 4)
        self._socket = None
        self._thread = None


def from_environment(
    console_port: int, host: str, allow: Callable[[str], bool] | None = None
) -> RedirectServer | None:
    """
    Build the redirect the environment asks for.

    Args:
        console_port: The port the console listens on in this process.
        host: The address the console listens on.
        allow: Whether a client address is answered, as for the console.

    Returns:
        The server, not started; None when ``NOUST_REDIRECT_PORT`` is not set.

    Raises:
        ValidationError: One of the two ports is not a port, or the redirect would take the
            console's own.
    """
    given = paths.getenv(REDIRECT_PORT)
    if not given:
        return None
    port = validate_listener_port(f"NOUST_{REDIRECT_PORT}", given)
    public = paths.getenv(PUBLIC_HTTPS_PORT)
    https_port = (
        validate_listener_port(f"NOUST_{PUBLIC_HTTPS_PORT}", public) if public else console_port
    )
    if port == console_port:
        raise ValidationError.from_text(
            say("redirect.port_console", setting=f"NOUST_{REDIRECT_PORT}", port=console_port),
            field=f"NOUST_{REDIRECT_PORT}",
        )
    return RedirectServer(host, port, https_port, allow)
