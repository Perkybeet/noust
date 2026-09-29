# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unlocking a running central from a shell, over a local UNIX socket.

The keys of a sealed store live in the memory of the process that serves the
console, so unlocking has to reach that process. The console has its own
form for it; this is the other door, for an operator with a shell on the
central (``docker exec -it noust noust central unlock``, or a passphrase
piped on standard input from a script): a UNIX socket in the state
directory, 0600 in a 0700 directory, that answers only its own user and
root (checked with ``SO_PEERCRED`` as well as by the file's mode). Whoever
can connect to it can already read the sealed files and guess at them
offline, so the socket gives nothing away that the disk does not.

The protocol is one JSON line each way:

- ``{"action": "unlock", "passphrase": "..."}`` answers
  ``{"ok": true, "sealed": ..., "locked": ...}`` or
  ``{"ok": false, "error": "...", "details": "..."}``.
- ``{"action": "status"}`` answers the same state without changing it.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
from pathlib import Path
from typing import Any

from noust.core import paths, sealing
from noust.core.exceptions import NoustError
from noust.core.fs import SECRET_MODE, FileSystem, get_fs

logger = logging.getLogger(__name__)

#: The socket's name in the state directory.
SOCKET_NAME = "central.sock"

#: Largest request read: a passphrase is short, and nothing else is sent.
MAX_REQUEST_BYTES = 8192

#: How long one exchange may take; scrypt takes a fraction of a second.
EXCHANGE_TIMEOUT = 30.0

#: How often the accept loop looks at whether it was asked to stop.
ACCEPT_POLL_SECONDS = 0.5


class UnlockUnavailableError(NoustError):
    """No running central answers on the unlock socket."""


def socket_path() -> Path:
    """
    Return where the running central listens for unlock requests.

    Returns:
        ``<state dir>/central.sock``.
    """
    return paths.state_dir() / SOCKET_NAME


def _state(root: Path) -> dict[str, Any]:
    """
    Describe a secrets directory's seal for a reply.

    Args:
        root: The secrets directory.

    Returns:
        ``{"sealed": bool, "locked": bool}``.
    """
    sealed = sealing.is_sealed(root)
    return {"sealed": sealed, "locked": sealed and not sealing.is_unlocked(root)}


def handle_request(raw: bytes, root: Path) -> dict[str, Any]:
    """
    Answer one request. Pure apart from the unlock itself, so it is tested
    without a socket.

    Args:
        raw: The request line.
        root: The secrets directory to unlock.

    Returns:
        The reply.
    """
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "The request is not JSON", "details": ""}
    if not isinstance(request, dict):
        return {"ok": False, "error": "The request is not a JSON object", "details": ""}
    action = request.get("action")
    if action == "status":
        return {"ok": True, **_state(root)}
    if action != "unlock":
        return {"ok": False, "error": f"Unknown action {action!r}", "details": ""}
    passphrase = request.get("passphrase")
    if not isinstance(passphrase, str) or not passphrase:
        return {"ok": False, "error": "No passphrase was sent", "details": ""}
    try:
        sealing.unlock(root, passphrase)
    except NoustError as exc:
        return {"ok": False, "error": exc.message, "details": exc.details}
    logger.info("The sealed secrets were unlocked over the local socket")
    return {"ok": True, **_state(root)}


def _peer_is_trusted(connection: socket.socket) -> bool:
    """
    Check that the other end of a connection is this user or root.

    Args:
        connection: The accepted connection.

    Returns:
        True for the same uid or root; also True where ``SO_PEERCRED`` does
        not exist, since the socket's mode already limits it to this user.
    """
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        return True
    credentials = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", credentials)
    return uid in (0, os.getuid())


def _read_line(connection: socket.socket) -> bytes:
    """
    Read one request line.

    Args:
        connection: The connection.

    Returns:
        The line, without its newline; what arrived if it was closed early.
    """
    data = b""
    while b"\n" not in data and len(data) < MAX_REQUEST_BYTES:
        chunk = connection.recv(MAX_REQUEST_BYTES - len(data))
        if not chunk:
            break
        data += chunk
    return data.split(b"\n", 1)[0]


class UnlockServer:
    """
    Listen on the unlock socket in a background thread of the central.

    Args:
        root: The secrets directory it unlocks.
        path: Where to listen; :func:`socket_path` by default.
        fs: The filesystem seam, for the socket file's mode and removal.
    """

    def __init__(self, root: Path, path: Path | None = None, fs: FileSystem | None = None) -> None:
        self.root = root
        self.path = path or socket_path()
        self._fs = fs or get_fs()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        """
        Bind the socket and start answering.

        Raises:
            NoustError: When the socket cannot be created (a path too long
                for a UNIX socket, a read-only directory).
        """
        self._fs.remove(self.path, missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.path))
            self._fs.chmod(self.path, SECRET_MODE)
            server.listen(4)
        except OSError as exc:
            server.close()
            raise NoustError(
                f"Cannot listen for unlock requests on {self.path}",
                details=f"{exc.strerror or exc}. Unlock from the console instead.",
            ) from exc
        server.settimeout(ACCEPT_POLL_SECONDS)
        self._socket = server
        self._thread = threading.Thread(target=self._serve, name="noust-unlock", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """Accept connections until :meth:`stop`, one exchange each."""
        server = self._socket
        assert server is not None  # noqa: S101 - start() set it
        while not self._stopping.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                # The socket was closed by stop().
                break
            with connection:
                self._exchange(connection)

    def _exchange(self, connection: socket.socket) -> None:
        """
        Answer one connection.

        Args:
            connection: The accepted connection.
        """
        connection.settimeout(EXCHANGE_TIMEOUT)
        try:
            if not _peer_is_trusted(connection):
                reply: dict[str, Any] = {
                    "ok": False,
                    "error": "Only the central's own user may unlock it",
                    "details": "",
                }
            else:
                reply = handle_request(_read_line(connection), self.root)
            connection.sendall(json.dumps(reply).encode("utf-8") + b"\n")
        except OSError as exc:
            logger.warning("An unlock request failed: %s", exc)

    def stop(self) -> None:
        """Stop answering and remove the socket file."""
        self._stopping.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=ACCEPT_POLL_SECONDS * 4)
        self._fs.remove(self.path, missing_ok=True)


def send_request(request: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """
    Send one request to the running central and return its reply.

    Args:
        request: The request.
        path: The socket; :func:`socket_path` by default.

    Returns:
        The reply.

    Raises:
        UnlockUnavailableError: When no central answers there.
    """
    target = path or socket_path()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(EXCHANGE_TIMEOUT)
    try:
        client.connect(str(target))
        client.sendall(json.dumps(request).encode("utf-8") + b"\n")
        reply = _read_line(client)
    except OSError as exc:
        raise UnlockUnavailableError(
            "No running central answers here",
            details=(
                f"Nothing listens on {target} ({exc.strerror or exc}). Start it with "
                "'noust central run', or unlock from the console."
            ),
        ) from exc
    finally:
        client.close()
    try:
        answer = json.loads(reply.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnlockUnavailableError(
            "The central answered something that is not a reply", details=repr(reply[:80])
        ) from exc
    if not isinstance(answer, dict):
        raise UnlockUnavailableError("The central answered something that is not a reply")
    return answer
