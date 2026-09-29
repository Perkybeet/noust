# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SSH tunnels from the central to each node's console, on demand.

One ``ssh -N -L 127.0.0.1:<local>:127.0.0.1:<console port>`` per node, started
through the runner as a long-lived process (rule 1): opened the first time
something needs the node, reused while it lives, reopened with backoff when
it dies, closed after a while unused, and stopped with the process that owns
it.

ssh runs with every option that decides its behaviour stated on the command
line and ``-F /dev/null``, so neither the operator's ``~/.ssh/config`` nor the
system's can redirect a node, add an agent, or relax host key checking. The
host key is checked against the node's own pinned ``known_hosts`` under a
fixed alias, strictly: a changed key stops the tunnel and says so, verbatim.
"""

from __future__ import annotations

import atexit
import contextlib
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NodeError, NodeUnreachableError
from noust.core.runner import CommandRunner, ProcessHandle, get_runner
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.keys import NodeKeys, host_key_alias

#: Close a tunnel nothing has used for this long.
IDLE_SECONDS = 600.0

#: How long a new tunnel may take to authenticate and start listening.
READY_TIMEOUT = 20.0

#: First wait after a failed open, doubled after each further failure.
BACKOFF_BASE = 1.0

#: Longest wait between attempts to open a failing tunnel.
BACKOFF_MAX = 60.0

#: How often the background reaper looks for idle tunnels.
REAP_INTERVAL = 30.0

#: Seconds between readiness probes while a tunnel opens.
READY_POLL = 0.1

#: ssh's own keepalive: a dead peer is noticed within interval x count.
SERVER_ALIVE_INTERVAL = 15
SERVER_ALIVE_COUNT = 3
CONNECT_TIMEOUT = 10

LOOPBACK = "127.0.0.1"


def pick_free_port() -> int:
    """
    Ask the kernel for a free loopback port for the tunnel's local end.

    ssh has no way to report a port it chose itself for ``-L``, so one is
    chosen here and handed over. Another process could take it in between;
    ``ExitOnForwardFailure`` makes ssh exit rather than run without its
    forward, and the next attempt picks another.

    Returns:
        A port number.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def port_accepts(port: int) -> bool:
    """
    Report whether the tunnel's local end accepts connections yet.

    ssh opens its ``-L`` listener only once it has authenticated, so an
    accepted connection means the tunnel is up.

    Args:
        port: The local port.

    Returns:
        True when a connection is accepted.
    """
    try:
        with socket.create_connection((LOOPBACK, port), timeout=0.5):
            return True
    except OSError:
        return False


def ssh_argv(record: NodeRecord, keys: NodeKeys, local_port: int) -> list[str]:
    """
    Build the tunnel's ssh command line.

    Every option is here, not in a config file, so this function is the
    whole truth about how the central talks to a node.

    Args:
        record: The node.
        keys: Where the node's key and ``known_hosts`` are.
        local_port: The loopback port the tunnel listens on.

    Returns:
        The argv.
    """
    options = {
        "BatchMode": "yes",
        "StrictHostKeyChecking": "yes",
        "UserKnownHostsFile": str(keys.known_hosts_path(record.name)),
        "GlobalKnownHostsFile": "/dev/null",
        "HostKeyAlias": host_key_alias(record.name),
        "CheckHostIP": "no",
        "UpdateHostKeys": "no",
        "IdentitiesOnly": "yes",
        "IdentityAgent": "none",
        "PasswordAuthentication": "no",
        "KbdInteractiveAuthentication": "no",
        "ExitOnForwardFailure": "yes",
        "ServerAliveInterval": str(SERVER_ALIVE_INTERVAL),
        "ServerAliveCountMax": str(SERVER_ALIVE_COUNT),
        "ConnectTimeout": str(CONNECT_TIMEOUT),
        "ForwardAgent": "no",
        "ForwardX11": "no",
        "PermitLocalCommand": "no",
        "LogLevel": "ERROR",
    }
    argv = [
        "ssh",
        "-N",
        "-T",
        "-F",
        "/dev/null",
        "-i",
        str(keys.private_key_path(record.name)),
        "-p",
        str(record.ssh_port),
        "-l",
        record.ssh_user,
        "-L",
        f"{LOOPBACK}:{local_port}:{LOOPBACK}:{record.console_port}",
    ]
    for name, value in options.items():
        argv += ["-o", f"{name}={value}"]
    return [*argv, "--", record.ssh_host]


def explain_ssh_failure(stderr: str, record: NodeRecord, exit_code: int | None) -> str:
    """
    Say in one sentence why ssh failed, from its own words.

    Args:
        stderr: ssh's standard error.
        record: The node.
        exit_code: ssh's exit status, if it exited.

    Returns:
        The sentence. ssh's output itself goes in the error's details.
    """
    text = stderr.lower()
    where = f"{record.ssh_host}:{record.ssh_port}"
    if "remote host identification has changed" in text or "host key verification failed" in text:
        return (
            f"The SSH host key of {record.name} does not match the one pinned when it was "
            "added; the tunnel was not opened"
        )
    if "permission denied" in text:
        return (
            f"{record.name} refused this central's key: it was never authorized there for "
            f"{record.ssh_user}, or it was removed with 'noust fleet deauthorize'"
        )
    if "administratively prohibited" in text or "open failed" in text:
        return (
            f"{record.name} refused to forward its console port {record.console_port}: "
            "the authorized key does not permit it"
        )
    if "could not resolve hostname" in text or "name or service not known" in text:
        return f"Could not resolve {record.ssh_host}"
    if "connection refused" in text:
        return f"Nothing accepts SSH connections on {where}"
    if "timed out" in text:
        return f"SSH to {where} timed out"
    if "address already in use" in text or "cannot listen to port" in text:
        return "The tunnel's local port was taken by something else; the next attempt uses another"
    if "dry run" in text:
        return f"The tunnel to {record.name} was not opened: this is a dry run"
    if "command not found" in text:
        return "The ssh client is not installed on this central"
    status = f" (exit {exit_code})" if exit_code is not None else ""
    return f"The SSH tunnel to {record.name} ({where}) failed{status}"


def _iso(timestamp: float | None) -> str | None:
    """
    Format a wall-clock timestamp for a status payload.

    Args:
        timestamp: ``time.time()`` value, or None.

    Returns:
        ISO 8601 UTC, or None.
    """
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Tunnel:
    """What the manager knows about one node's tunnel."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    handle: ProcessHandle | None = None
    local_port: int | None = None
    since: float | None = None
    last_used: float = 0.0
    leases: int = 0
    failures: int = 0
    retry_at: float = 0.0
    last_error: str | None = None


class TunnelManager:
    """
    Opens, reuses, reopens and closes the SSH tunnels to the nodes.

    Thread-safe: each node has its own lock, so opening a slow node never
    holds up a request to another.

    Args:
        runner: Starts the ssh processes; the process-wide runner by default.
        store: Where node records are read; the process-wide store by default.
        keys: Where the per-node key and ``known_hosts`` are.
        idle_seconds: Close a tunnel unused this long.
        ready_timeout: How long a tunnel may take to come up.
        clock: Monotonic clock, for idle and backoff arithmetic.
        sleep: Pause between readiness probes.
        probe: Whether a local port accepts connections.
        free_port: Picks the local port of a new tunnel.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        store: NoustStore | None = None,
        keys: NodeKeys | None = None,
        idle_seconds: float = IDLE_SECONDS,
        ready_timeout: float = READY_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        probe: Callable[[int], bool] = port_accepts,
        free_port: Callable[[], int] = pick_free_port,
    ) -> None:
        self._runner = runner
        self._store = store
        self._keys = keys
        self.idle_seconds = idle_seconds
        self.ready_timeout = ready_timeout
        self._clock = clock
        self._sleep = sleep
        self._probe = probe
        self._free_port = free_port
        self._lock = threading.Lock()
        self._tunnels: dict[str, _Tunnel] = {}
        self._reaper: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def runner(self) -> CommandRunner:
        """The runner ssh is started through."""
        return self._runner or get_runner()

    @property
    def store(self) -> NoustStore:
        """The store node records are read from."""
        return self._store or get_store()

    @property
    def keys(self) -> NodeKeys:
        """Where each node's key and ``known_hosts`` are."""
        if self._keys is None:
            self._keys = NodeKeys(runner=self._runner)
        return self._keys

    def _entry(self, node: str) -> _Tunnel:
        """
        Find or create a node's entry.

        Args:
            node: The node's name.

        Returns:
            Its entry.
        """
        with self._lock:
            return self._tunnels.setdefault(node, _Tunnel())

    def endpoint(self, node: str) -> tuple[str, int]:
        """
        Return where the node's console answers on this machine, opening the tunnel if needed.

        Args:
            node: The node's name.

        Returns:
            ``("127.0.0.1", local_port)``.

        Raises:
            NodeError: When the node is not registered.
            NodeUnreachableError: When the tunnel cannot be opened, or failed
                recently and is waiting out its backoff. The message says why;
                the details carry ssh's stderr verbatim.
        """
        self.reap_idle()
        tunnel = self._entry(node)
        with tunnel.lock:
            if tunnel.handle is not None and tunnel.handle.is_alive():
                tunnel.last_used = self._clock()
                return LOOPBACK, int(tunnel.local_port or 0)
            if tunnel.handle is not None:
                # It was up and died: the connection dropped, the node
                # rebooted, the key was removed. Its last words say which.
                tunnel.last_error = tunnel.handle.stderr_tail() or tunnel.last_error
                tunnel.handle = None
                tunnel.local_port = None
                tunnel.since = None
            now = self._clock()
            if tunnel.failures and now < tunnel.retry_at:
                wait = int(tunnel.retry_at - now + 0.999)
                raise NodeUnreachableError(
                    f"The tunnel to {node} failed {tunnel.failures} time"
                    f"{'s' if tunnel.failures != 1 else ''}; the next attempt is in {wait}s",
                    details=tunnel.last_error or "",
                )
            return self._open(node, tunnel)

    def _open(self, node: str, tunnel: _Tunnel) -> tuple[str, int]:
        """
        Start ssh for a node and wait until its forward listens.

        Called with the node's lock held.

        Args:
            node: The node's name.
            tunnel: Its entry.

        Returns:
            The local endpoint.

        Raises:
            NodeError: When the node is not registered.
            NodeUnreachableError: When ssh exits or does not come up in time.
        """
        record = self.store.get_node(node)
        if record is None:
            raise NodeError(
                f"No node named {node} is registered on this central",
                details="List them with 'noust node list'.",
            )
        # Rewritten on every open, from the store: the pin is whatever the
        # record says, never something a previous connection left behind.
        self.keys.pin_host_key(node, record.host_key)
        local_port = self._free_port()
        handle = self.runner.start(ssh_argv(record, self.keys, local_port))
        deadline = self._clock() + self.ready_timeout
        while True:
            if not handle.is_alive():
                self._failed(tunnel, handle.stderr_tail())
                raise NodeUnreachableError(
                    explain_ssh_failure(handle.stderr_tail(), record, handle.exit_code),
                    details=handle.stderr_tail(),
                )
            if self._probe(local_port):
                break
            if self._clock() >= deadline:
                handle.terminate()
                tail = handle.stderr_tail()
                self._failed(tunnel, tail or f"No answer within {self.ready_timeout:.0f}s")
                raise NodeUnreachableError(
                    f"The tunnel to {node} did not come up within {self.ready_timeout:.0f}s",
                    details=tail,
                )
            self._sleep(READY_POLL)
        tunnel.handle = handle
        tunnel.local_port = local_port
        tunnel.since = time.time()
        tunnel.last_used = self._clock()
        tunnel.failures = 0
        tunnel.retry_at = 0.0
        return LOOPBACK, local_port

    def _failed(self, tunnel: _Tunnel, error: str) -> None:
        """
        Record a failed open and schedule the next attempt.

        Args:
            tunnel: The node's entry.
            error: ssh's stderr, or what went wrong.
        """
        tunnel.failures += 1
        tunnel.last_error = error
        tunnel.retry_at = self._clock() + min(
            BACKOFF_BASE * 2 ** (tunnel.failures - 1), BACKOFF_MAX
        )

    @contextlib.contextmanager
    def lease(self, node: str) -> Iterator[tuple[str, int]]:
        """
        Hold a node's tunnel open for as long as the block runs.

        A stream (SSE, a log WebSocket) uses the tunnel for minutes without
        calling :meth:`endpoint` again; a lease keeps the idle reaper away
        from it.

        Args:
            node: The node's name.

        Yields:
            The local endpoint.

        Raises:
            NodeError: As :meth:`endpoint`.
        """
        endpoint = self.endpoint(node)
        tunnel = self._entry(node)
        with tunnel.lock:
            tunnel.leases += 1
        try:
            yield endpoint
        finally:
            with tunnel.lock:
                tunnel.leases -= 1
                tunnel.last_used = self._clock()

    def close(self, node: str) -> None:
        """
        Stop a node's tunnel and forget its failures.

        Args:
            node: The node's name.
        """
        with self._lock:
            tunnel = self._tunnels.pop(node, None)
        if tunnel is None:
            return
        with tunnel.lock:
            if tunnel.handle is not None:
                tunnel.handle.terminate()
                tunnel.handle = None

    def close_all(self) -> None:
        """Stop every tunnel. Called at interpreter and server shutdown."""
        self._stopping.set()
        with self._lock:
            names = list(self._tunnels)
        for name in names:
            self.close(name)

    def reap_idle(self) -> list[str]:
        """
        Close the tunnels nothing has used for :attr:`idle_seconds`.

        Returns:
            The nodes whose tunnels were closed.
        """
        now = self._clock()
        with self._lock:
            candidates = list(self._tunnels.items())
        closed: list[str] = []
        for name, tunnel in candidates:
            with tunnel.lock:
                if tunnel.handle is None or tunnel.leases > 0:
                    continue
                if now - tunnel.last_used < self.idle_seconds:
                    continue
                tunnel.handle.terminate()
                tunnel.handle = None
                tunnel.local_port = None
                tunnel.since = None
            closed.append(name)
        return closed

    def status(self, node: str) -> dict[str, Any]:
        """
        Describe a node's tunnel.

        Args:
            node: The node's name.

        Returns:
            ``open``, ``local_port``, ``since`` (ISO 8601 UTC), ``last_error``
            (ssh's stderr, verbatim) and ``failures``.
        """
        with self._lock:
            tunnel = self._tunnels.get(node)
        if tunnel is None:
            return {
                "open": False,
                "local_port": None,
                "since": None,
                "last_error": None,
                "failures": 0,
            }
        with tunnel.lock:
            alive = tunnel.handle is not None and tunnel.handle.is_alive()
            if tunnel.handle is not None and not alive:
                tunnel.last_error = tunnel.handle.stderr_tail() or tunnel.last_error
            return {
                "open": alive,
                "local_port": tunnel.local_port if alive else None,
                "since": _iso(tunnel.since) if alive else None,
                "last_error": tunnel.last_error,
                "failures": tunnel.failures,
            }

    def start_reaper(self, interval: float = REAP_INTERVAL) -> None:
        """
        Close idle tunnels in the background, every ``interval`` seconds.

        Args:
            interval: Seconds between sweeps.
        """
        if self._reaper is not None:
            return

        def sweep() -> None:
            while not self._stopping.wait(interval):
                self.reap_idle()

        self._reaper = threading.Thread(target=sweep, name="noust-tunnel-reaper", daemon=True)
        self._reaper.start()


_tunnels: TunnelManager | None = None
_tunnels_lock = threading.Lock()


def get_tunnels() -> TunnelManager:
    """
    Return the process-wide tunnel manager, creating it on first use.

    It closes every tunnel when the interpreter exits; a server should also
    call :meth:`TunnelManager.close_all` from its shutdown hook.

    Returns:
        The manager.
    """
    global _tunnels
    with _tunnels_lock:
        if _tunnels is None:
            _tunnels = TunnelManager()
            _tunnels.start_reaper()
            atexit.register(_tunnels.close_all)
        return _tunnels


def set_tunnels(manager: TunnelManager | None) -> None:
    """
    Replace the process-wide tunnel manager (tests, the development server).

    Args:
        manager: The manager to install, or None to build a fresh one on next use.
    """
    global _tunnels
    with _tunnels_lock:
        _tunnels = manager
