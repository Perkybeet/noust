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

On a central whose secrets are sealed, a locked process dials nothing: every
node key is ciphertext until the operator unlocks it. Once unlocked, ssh is
handed decrypted private copies of the key and the pinned host key, removed
as soon as the tunnel is up (ssh reads both once, when it connects) or has
failed.
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
from pathlib import Path
from typing import Any

from noust.core import sealing
from noust.core.exceptions import NodeError, NodeRefusedError, NodeUnreachableError
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

#: Test seam: node name to a local port its tunnel resolves to without dialling ssh.
#: Empty in every process but the one ``scripts/console_server.py`` builds for the fleet's
#: end-to-end coverage, where a second sandboxed backend stands in for a node and ssh cannot
#: run in the sandbox at all. Never set from product code or from configuration: the only
#: caller is :func:`set_loopback_for_testing`, and the only thing that calls that is
#: ``console_server.py`` itself. See ``endpoint()``, the one place this is read.
_LOOPBACK_OVERRIDE: dict[str, int] = {}


def set_loopback_for_testing(node: str, port: int | None) -> None:
    """
    Make a node's tunnel resolve straight to a local port, without ssh.

    Test seam, not a feature: ``scripts/console_server.py`` is the only caller, when it runs
    two sandboxed backends side by side to stand in for a central and a node for Playwright.
    Every other check ``endpoint()`` normally does - the node being registered, this central
    being unlocked - still runs; only the ssh dial itself is skipped.

    Args:
        node: The node's name.
        port: The local loopback port already answering for it, or None to remove the
            override and go back to dialling ssh.
    """
    if port is None:
        _LOOPBACK_OVERRIDE.pop(node, None)
    else:
        _LOOPBACK_OVERRIDE[node] = port


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


def ssh_argv(
    record: NodeRecord, local_port: int, *, identity: Path, known_hosts: Path
) -> list[str]:
    """
    Build the tunnel's ssh command line.

    Every option is here, not in a config file, so this function is the
    whole truth about how the central talks to a node.

    Args:
        record: The node.
        local_port: The loopback port the tunnel listens on.
        identity: The node's private key file, as ssh can read it
            (:meth:`NodeKeys.usable_private_key`).
        known_hosts: The node's pinned ``known_hosts``, likewise.

    Returns:
        The argv.
    """
    options = {
        "BatchMode": "yes",
        "StrictHostKeyChecking": "yes",
        "UserKnownHostsFile": str(known_hosts),
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
        str(identity),
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


def key_was_revoked(stderr: str) -> bool:
    """
    Report whether ssh's own words say the node no longer authorizes this key.

    Args:
        stderr: ssh's standard error.

    Returns:
        True for "Permission denied (publickey)": the same fact the node's
        own HTTP 401 tells the fleet token holds, and treated the same way -
        a refusal to persist and stop presenting, not an outage to retry.
    """
    return "permission denied" in stderr.lower()


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
            SecretsLockedError: When this central's secrets are sealed and it
                has not been unlocked; nothing is dialled.
        """
        self.reap_idle()
        self._require_unlocked()
        override = _LOOPBACK_OVERRIDE.get(node)
        if override is not None:
            return LOOPBACK, override
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

    def _require_unlocked(self) -> None:
        """
        Refuse to hand out a tunnel while this central is locked.

        Checked before anything else, so a locked central neither starts ssh
        nor counts a failure against the node: there is no backoff to wait
        out once it is unlocked. A tunnel that was already up is refused too;
        a locked central uses nothing its keys opened.

        Raises:
            SecretsLockedError: When the secrets are sealed and locked.
        """
        root = self.keys.secrets.root
        if sealing.is_sealed(root) and not sealing.is_unlocked(root):
            raise sealing.SecretsLockedError(
                "This central is locked: its secrets are sealed, so no node can be reached",
                details=(
                    "Unlock it in the console, or with 'noust central unlock' on the "
                    "central (docker exec -it noust noust central unlock in a container)."
                ),
            )

    def on_unlocked(self) -> None:
        """
        Forget every tunnel's backoff: the central was just unlocked.

        Registered with :func:`noust.core.sealing.add_unlock_listener`. Tunnels
        open lazily, on the next request that needs one; what the unlock
        changes is that a node which failed while the central could not read
        its keys is dialled again at once instead of after its backoff.
        """
        with self._lock:
            entries = list(self._tunnels.values())
        for tunnel in entries:
            with tunnel.lock:
                tunnel.failures = 0
                tunnel.retry_at = 0.0

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
        try:
            return self._start(node, tunnel, record)
        finally:
            # ssh has read its key and known_hosts by the time it listens, or
            # it has given up; the decrypted copies are not needed any more.
            self.keys.discard_usable(node)

    def _start(self, node: str, tunnel: _Tunnel, record: NodeRecord) -> tuple[str, int]:
        """
        Start ssh and wait until its forward listens.

        Called with the node's lock held, by :meth:`_open`.

        Args:
            node: The node's name.
            tunnel: Its entry.
            record: The node.

        Returns:
            The local endpoint.

        Raises:
            NodeUnreachableError: When ssh exits or does not come up in time.
            NodeRefusedError: When ssh's own words say the node no longer
                authorizes this key (see :func:`key_was_revoked`); persisted
                the same way a node's HTTP 401 is, so nothing keeps
                presenting it on an endless backoff.
        """
        local_port = self._free_port()
        handle = self.runner.start(
            ssh_argv(
                record,
                local_port,
                identity=self.keys.usable_private_key(node),
                known_hosts=self.keys.usable_known_hosts(node),
            )
        )
        deadline = self._clock() + self.ready_timeout
        while True:
            if not handle.is_alive():
                stderr = handle.stderr_tail()
                self._failed(tunnel, stderr)
                message = explain_ssh_failure(stderr, record, handle.exit_code)
                if key_was_revoked(stderr):
                    # Mirrors NodeClient.mark_refused(): persisted at the
                    # point of failure, not left to whichever caller's
                    # except block runs next, so nothing overwrites it back
                    # to a merely-retriable "unreachable".
                    self.store.set_node_status(node, "refused")
                    raise NodeRefusedError(message, details=stderr)
                raise NodeUnreachableError(message, details=stderr)
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
        endpoint, release = self.hold(node)
        try:
            yield endpoint
        finally:
            release()

    def hold(self, node: str) -> tuple[tuple[str, int], Callable[[], None]]:
        """
        Take a lease on a node's tunnel, for a caller that cannot use a ``with`` block.

        An async stream opens its upstream in one place and ends it in a
        background task or a ``finally`` far away; this is :meth:`lease`
        split in two for it.

        Args:
            node: The node's name.

        Returns:
            The local endpoint, and the function that returns the lease. It
            may be called more than once; only the first call counts.

        Raises:
            NodeError: As :meth:`endpoint`.
        """
        endpoint = self.endpoint(node)
        tunnel = self._entry(node)
        with tunnel.lock:
            tunnel.leases += 1
        once = threading.Lock()

        def release() -> None:
            if not once.acquire(blocking=False):
                return
            # The entry, not the name: a tunnel closed and reopened meanwhile
            # is another entry, whose leases are not this one's to return.
            with tunnel.lock:
                tunnel.leases = max(0, tunnel.leases - 1)
                tunnel.last_used = self._clock()

        return endpoint, release

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
            sealing.add_unlock_listener(_tell_tunnels_unlocked)
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
    sealing.add_unlock_listener(_tell_tunnels_unlocked)


def _tell_tunnels_unlocked() -> None:
    """Tell the process-wide tunnel manager, whichever it is now, of an unlock."""
    with _tunnels_lock:
        manager = _tunnels
    if manager is not None:
        manager.on_unlocked()
