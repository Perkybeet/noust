# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SSH tunnels from the central to each node's console, on demand.

One ``ssh -N -L 127.0.0.1:<local>:127.0.0.1:<console port>`` per node, started
through the runner as a long-lived process (rule 1): opened the first time
something needs the node, reused while it lives, reopened with backoff when
it dies, closed after a while unused, and stopped with the process that owns
it: :meth:`TunnelManager.close_all` at the console's shutdown and at exit, and
nothing opens after it. ssh runs in its own session, so a console killed
outright (SIGKILL, a crash) leaves its tunnels behind, holding ports: the next
console stops them before it opens any (:func:`stop_stale_tunnels`), and
knows them by their argv alone - ``-F /dev/null``, a key in this central's
secrets under ``fleet/nodes/<name>/`` and ``HostKeyAlias=noust-node-<name>``.

ssh runs with every option that decides its behaviour stated on the command
line and ``-F /dev/null``, so neither the operator's ``~/.ssh/config`` nor the
system's can redirect a node, add an agent, or relax host key checking. The
host key is checked against the node's own pinned ``known_hosts`` under a
fixed alias, strictly: a changed key stops the tunnel and says so, verbatim.
The algorithms are pinned too (:data:`PINNED_ALGORITHMS`): ed25519 host keys,
a post-quantum hybrid or X25519 key exchange, and AEAD ciphers only - checked
once against what this central's ssh client knows (``ssh -Q``), so an older
client drops what it lacks instead of refusing every tunnel.

On a central whose secrets are sealed, a locked process dials nothing: every
node key is ciphertext until the operator unlocks it. Once unlocked, ssh is
handed decrypted private copies of the key and the pinned host key, removed
as soon as the tunnel is up (ssh reads both once, when it connects) or has
failed.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import re
import shlex
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core import sealing
from noust.core.exceptions import NodeError, NodeRefusedError, NodeUnreachableError
from noust.core.runner import CommandRunner, ProcessHandle, get_runner
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.keys import (
    NODES_NAMESPACE,
    PRIVATE_KEY,
    NodeKeys,
    bastion_host_key_alias,
    host_key_alias,
)
from noust.fleet.link.listener import (
    PROC_NET,
    link_account_uids,
    listener_uids,
    require_trusted_listener,
)
from noust.fleet.link.records import ACTIVE, LinkRecord, LinkRecords
from noust.fleet.models import parse_public_key, validate_node_name
from noust.fleet.ssh_options import (
    DEFAULT_CONNECT_TIMEOUT,
    Bastion,
    NodeSSHOptions,
    SSHOptions,
)

logger = logging.getLogger(__name__)

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

#: Seconds ssh waits for a connection unless the server's options say otherwise.
CONNECT_TIMEOUT = DEFAULT_CONNECT_TIMEOUT

LOOPBACK = "127.0.0.1"

#: What a bastion hop is asked to connect to: ssh replaces ``%h`` and ``%p`` with the
#: server and its port, and the brackets let an IPv6 address through.
STREAM_TARGET = "[%h]:%p"

#: What a central's tunnel may negotiate, strongest first. ed25519 is the only
#: host key the join code pins; sntrup761x25519 is post-quantum hybrid (OpenSSH
#: 8.5+), curve25519-sha256 the classic fallback every node has (OpenSSH 7.4+,
#: and a node needs 7.8 for its key line anyway); the ciphers are AEAD, so the
#: MACs only matter if one ever is not, and then only encrypt-then-MAC.
PINNED_ALGORITHMS: dict[str, tuple[str, ...]] = {
    "HostKeyAlgorithms": ("ssh-ed25519",),
    "KexAlgorithms": ("sntrup761x25519-sha512@openssh.com", "curve25519-sha256"),
    "Ciphers": ("chacha20-poly1305@openssh.com", "aes256-gcm@openssh.com"),
    "MACs": ("hmac-sha2-512-etm@openssh.com", "hmac-sha2-256-etm@openssh.com"),
}

#: ``ssh -Q`` query for each pinned option.
_ALGORITHM_QUERIES = {
    "HostKeyAlgorithms": "key",
    "KexAlgorithms": "kex",
    "Ciphers": "cipher",
    "MACs": "mac",
}

#: How long ``ssh -Q`` may take. It is instantaneous; this bounds a hang.
QUERY_TIMEOUT = 10


def ssh_algorithms(runner: CommandRunner) -> dict[str, str]:
    """
    Choose the pinned algorithms this central's ssh client supports.

    Args:
        runner: The runner ``ssh -Q`` goes through.

    Returns:
        Option name to the comma-separated list to pass. When ssh cannot say
        what it supports, the whole pinned list: ssh then names what it does
        not know, verbatim, rather than this guessing.

    Raises:
        NodeError: When ssh supports none of an option's pinned algorithms.
    """
    chosen: dict[str, str] = {}
    for option, wanted in PINNED_ALGORITHMS.items():
        result = runner.run(["ssh", "-Q", _ALGORITHM_QUERIES[option]], timeout=QUERY_TIMEOUT)
        known = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if not result.success or not known:
            chosen[option] = ",".join(wanted)
            continue
        usable = [name for name in wanted if name in known]
        if not usable:
            raise NodeError(
                f"This central's ssh client supports none of the {option} Noust pins",
                details=(
                    f"Noust uses {', '.join(wanted)}. Upgrade the OpenSSH client on the "
                    "central (8.5 or later has them all), then try again."
                ),
            )
        chosen[option] = ",".join(usable)
    return chosen


def _default_algorithms() -> dict[str, str]:
    """
    The pinned algorithms, every one of them.

    Returns:
        Option name to its comma-separated list.
    """
    return {option: ",".join(names) for option, names in PINNED_ALGORITHMS.items()}


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
    record: NodeRecord,
    local_port: int,
    *,
    identity: Path,
    known_hosts: Path,
    algorithms: dict[str, str] | None = None,
    options: SSHOptions | None = None,
    bastion_known_hosts: Path | None = None,
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
        algorithms: :func:`ssh_algorithms`' choice; every pinned one when None.
        options: How this node is connected to beyond its address (its wait, its
            bastion); the defaults when None.
        bastion_known_hosts: The bastion's pinned ``known_hosts``, as ssh can read
            it; required when the options name a bastion.

    Returns:
        The argv.

    Raises:
        NodeError: When a bastion is named without its pinned host key: the central
            never connects to a host it has no key for.
    """
    chosen = options or SSHOptions()
    timeout = chosen.effective_timeout
    node_options = ssh_options(
        known_hosts,
        host_key_alias=host_key_alias(record.name),
        algorithms=algorithms,
        connect_timeout=timeout,
    )
    if chosen.bastion is not None:
        if bastion_known_hosts is None:
            raise NodeError(
                f"The bastion of {record.name} has no pinned host key",
                details="Set it again with 'noust node ssh': the central never connects to "
                "a bastion it has no host key for.",
                field="bastion_host_key",
            )
        node_options["ProxyCommand"] = bastion_proxy_command(
            chosen.bastion,
            node=record.name,
            identity=identity,
            known_hosts=bastion_known_hosts,
            algorithms=algorithms,
            connect_timeout=timeout,
        )
    return ssh_forward_argv(
        direction="-L",
        spec=f"{LOOPBACK}:{local_port}:{LOOPBACK}:{record.console_port}",
        identity=identity,
        port=record.ssh_port,
        user=record.ssh_user,
        host=record.ssh_host,
        options=node_options,
    )


def bastion_proxy_command(
    bastion: Bastion,
    *,
    node: str,
    identity: Path,
    known_hosts: Path,
    algorithms: dict[str, str] | None = None,
    connect_timeout: int = CONNECT_TIMEOUT,
) -> str:
    """
    Build the command ssh starts to reach a server through its bastion.

    Not ``ssh -J``: the ssh that ``-J`` starts is told only the account and the port,
    so under ``-F /dev/null`` it would read the operator's own keys and
    ``known_hosts`` and ask about a host it does not know. This is the whole command
    of the hop: the bastion's host key pinned and checked strictly, the node's key,
    no agent, no password, the same algorithms, and ``-W`` so the hop forwards one
    stream to the server and runs nothing.

    ssh gives the string to a shell, so every word is quoted, and a ``%`` is doubled:
    in a proxy command ssh reads ``%h`` and ``%p`` (the server and its port), and a
    path that happens to hold a percent sign is not one.

    Args:
        bastion: The bastion.
        node: The node's name, for the alias its host key is pinned under.
        identity: The private key file, as ssh can read it.
        known_hosts: The bastion's pinned ``known_hosts``, likewise.
        algorithms: :func:`ssh_algorithms`' choice; every pinned one when None.
        connect_timeout: Seconds to wait for the bastion.

    Returns:
        The command, for ``-o ProxyCommand=``.
    """
    argv = [
        "ssh",
        "-W",
        STREAM_TARGET,
        "-T",
        "-F",
        "/dev/null",
        "-i",
        str(identity),
        "-p",
        str(bastion.port),
        "-l",
        bastion.user,
    ]
    hop = ssh_options(
        known_hosts,
        host_key_alias=bastion_host_key_alias(node),
        algorithms=algorithms,
        connect_timeout=connect_timeout,
    )
    # The hop forwards a stream: it has no use for the options of a port forward.
    hop.pop("ExitOnForwardFailure", None)
    for name, value in hop.items():
        argv += ["-o", f"{name}={value}"]
    argv += ["--", bastion.host]
    # The one token ssh must read is quoted without doubling: brackets are a glob to a shell.
    return " ".join(
        shlex.quote(word) if word == STREAM_TARGET else shlex.quote(word.replace("%", "%%"))
        for word in argv
    )


def ssh_options(
    known_hosts: Path,
    *,
    host_key_alias: str | None = None,
    algorithms: dict[str, str] | None = None,
    connect_timeout: int = CONNECT_TIMEOUT,
) -> dict[str, str]:
    """
    List the ``-o`` options every Noust ssh connection carries.

    The central's tunnels to its nodes and the link a server keeps to its
    central (:mod:`noust.fleet.link.node`) are the same kind of connection and
    are hardened the same way: nothing but what is said here decides how ssh
    behaves.

    Args:
        known_hosts: The pinned ``known_hosts`` file.
        host_key_alias: The name the host key is pinned under, when it is not
            the host's own.
        algorithms: :func:`ssh_algorithms`' choice; every pinned one when None.
        connect_timeout: Seconds to wait for the connection.

    Returns:
        The options, in the order they are passed.
    """
    options = {
        "BatchMode": "yes",
        "StrictHostKeyChecking": "yes",
        "UserKnownHostsFile": str(known_hosts),
        "GlobalKnownHostsFile": "/dev/null",
    }
    if host_key_alias is not None:
        options["HostKeyAlias"] = host_key_alias
    options.update(
        {
            "CheckHostIP": "no",
            "UpdateHostKeys": "no",
            "IdentitiesOnly": "yes",
            "IdentityAgent": "none",
            "PasswordAuthentication": "no",
            "KbdInteractiveAuthentication": "no",
            "ExitOnForwardFailure": "yes",
            "ServerAliveInterval": str(SERVER_ALIVE_INTERVAL),
            "ServerAliveCountMax": str(SERVER_ALIVE_COUNT),
            "ConnectTimeout": str(connect_timeout),
            "ForwardAgent": "no",
            "ForwardX11": "no",
            "PermitLocalCommand": "no",
            "LogLevel": "ERROR",
            **(algorithms if algorithms is not None else _default_algorithms()),
        }
    )
    return options


def ssh_forward_argv(
    *,
    direction: str,
    spec: str,
    identity: Path,
    port: int,
    user: str,
    host: str,
    options: dict[str, str],
) -> list[str]:
    """
    Build the command line of an ssh that only forwards one port.

    Args:
        direction: ``-L`` (a node's console, from the central) or ``-R`` (a
            central's loopback, from a server that dials in).
        spec: The forward, ``<listen host>:<port>:<host>:<port>``.
        identity: The private key file.
        port: The SSH port.
        user: The account to log in as.
        host: The host.
        options: :func:`ssh_options`' output.

    Returns:
        The argv: no shell, no config file (``-F /dev/null``), no terminal.
    """
    argv = [
        "ssh",
        "-N",
        "-T",
        "-F",
        "/dev/null",
        "-i",
        str(identity),
        "-p",
        str(port),
        "-l",
        user,
        direction,
        spec,
    ]
    for name, value in options.items():
        argv += ["-o", f"{name}={value}"]
    return [*argv, "--", host]


def failed_at_bastion(stderr: str, bastion: Bastion | None) -> bool:
    """
    Tell whether ssh's own words are about the bastion rather than the server behind it.

    Both hops print into one stream. The bastion's host key file is named
    ``bastion_known_hosts`` and its alias ``noust-bastion-<node>``, and a refusal is
    printed with the account and host that refused; a stream the bastion would not
    open to the server ends in ``stdio forwarding failed``.

    Args:
        stderr: ssh's standard error.
        bastion: The node's bastion, if it has one.

    Returns:
        True when the failure is the bastion's.
    """
    if bastion is None:
        return False
    text = stderr.lower()
    if "bastion_known_hosts" in text or "noust-bastion-" in text:
        return True
    if "stdio forwarding failed" in text:
        return True
    return f"{bastion.user}@{bastion.host}".lower() in text and "permission denied" in text


def _explain_bastion_failure(stderr: str, record: NodeRecord, bastion: Bastion) -> str:
    """
    Say in one sentence why the hop to a bastion failed.

    Args:
        stderr: ssh's standard error.
        record: The node.
        bastion: The bastion.

    Returns:
        The sentence.
    """
    text = stderr.lower()
    where = f"{bastion.host}:{bastion.port}"
    if "remote host identification has changed" in text or "host key verification failed" in text:
        return (
            f"The SSH host key of the bastion {bastion.host} does not match the one pinned when "
            f"it was set; the tunnel to {record.name} was not opened"
        )
    if "stdio forwarding failed" in text or "administratively prohibited" in text:
        host = f"[{record.ssh_host}]" if ":" in record.ssh_host else record.ssh_host
        return (
            f"The bastion {bastion.host} would not connect to {host}:{record.ssh_port}: the "
            f'authorized_keys line it holds for this central needs permitopen="{host}:'
            f'{record.ssh_port}" (and port-forwarding); "noust node ssh {record.name} --show" '
            "prints it"
        )
    if "permission denied" in text:
        return (
            f"The bastion {bastion.address} refused this central's key: authorize the key "
            f'it holds for {record.name} there ("noust node ssh {record.name} --show" prints '
            "the line)"
        )
    if "could not resolve hostname" in text or "name or service not known" in text:
        return f"Could not resolve the bastion {bastion.host}"
    if "connection refused" in text:
        return f"Nothing accepts SSH connections on the bastion {where}"
    if "timed out" in text:
        return f"SSH to the bastion {where} timed out"
    return f"The SSH hop to the bastion {bastion.address} for {record.name} failed"


def explain_ssh_failure(
    stderr: str, record: NodeRecord, exit_code: int | None, bastion: Bastion | None = None
) -> str:
    """
    Say in one sentence why ssh failed, from its own words.

    Args:
        stderr: ssh's standard error.
        record: The node.
        exit_code: ssh's exit status, if it exited.
        bastion: The node's bastion, if it has one: a failure that is the bastion's is
            named as such instead of blaming the server behind it.

    Returns:
        The sentence. ssh's output itself goes in the error's details.
    """
    if bastion is not None and failed_at_bastion(stderr, bastion):
        return _explain_bastion_failure(stderr, record, bastion)
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
    if "no matching" in text and ("found" in text or "method" in text):
        return (
            f"{record.name}'s sshd offers none of the algorithms this central pins "
            "(ed25519 host keys, sntrup761x25519 or curve25519 key exchange, AEAD "
            "ciphers); upgrade OpenSSH on the node"
        )
    if "dry run" in text:
        return f"The tunnel to {record.name} was not opened: this is a dry run"
    if "command not found" in text:
        return "The ssh client is not installed on this central"
    status = f" (exit {exit_code})" if exit_code is not None else ""
    return f"The SSH tunnel to {record.name} ({where}) failed{status}"


#: What ssh prints about the key a node presented instead of the pinned one.
_PRESENTED_FINGERPRINT = re.compile(r"sent by the remote host is\s+(SHA256:[A-Za-z0-9+/=]+)")


def host_key_changed(stderr: str) -> bool:
    """
    Report whether ssh's own words say the node presented another host key.

    Args:
        stderr: ssh's standard error.

    Returns:
        True for a mismatch with the pinned key. The pin is written from the
        store before every connection, so a failed verification is a change,
        never a key that was simply not known yet.
    """
    text = stderr.lower()
    return "remote host identification has changed" in text or (
        "host key verification failed" in text
    )


def host_key_fingerprints(stderr: str, record: NodeRecord) -> tuple[str | None, str | None]:
    """
    Name the pinned host key and the one the node presented, by fingerprint.

    Args:
        stderr: ssh's standard error.
        record: The node, whose ``host_key`` is the pinned ``known_hosts`` line.

    Returns:
        ``(pinned, presented)``, each ``SHA256:...`` or None when it cannot
        be read.
    """
    pinned: str | None = None
    words = record.host_key.split()
    for index, word in enumerate(words[:-1]):
        if word.startswith("ssh-"):
            try:
                pinned = parse_public_key(f"{word} {words[index + 1]}").fingerprint
            except (NodeError, ValueError):
                pinned = None
            break
    match = _PRESENTED_FINGERPRINT.search(stderr)
    return pinned, match.group(1).rstrip(".") if match else None


def key_was_revoked(stderr: str, bastion: Bastion | None = None) -> bool:
    """
    Report whether ssh's own words say the node no longer authorizes this key.

    Args:
        stderr: ssh's standard error.
        bastion: The node's bastion, if it has one: its refusing the key says nothing
            about the node, which must not be marked refused for it.

    Returns:
        True for "Permission denied (publickey)": the same fact the node's
        own HTTP 401 tells the fleet token holds, and treated the same way -
        a refusal to persist and stop presenting, not an outage to retry.
    """
    if failed_at_bastion(stderr, bastion):
        return False
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
    #: The operator was told this node presented another host key; told once
    #: until a tunnel opens again.
    host_key_alerted: bool = False


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
        link_owners: The accounts whose listener on a link's port is a link's
            (:func:`~noust.fleet.link.listener.link_account_uids`).
        proc_net: The kernel's socket tables, where a link's listener is looked up.
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
        link_owners: Callable[[], frozenset[int]] = link_account_uids,
        proc_net: Path = PROC_NET,
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
        self._link_owners = link_owners
        self._proc_net = proc_net
        self._lock = threading.Lock()
        self._tunnels: dict[str, _Tunnel] = {}
        self._reaper: threading.Thread | None = None
        self._stopping = threading.Event()
        self._algorithms: dict[str, str] | None = None

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
        if self._stopping.is_set():
            # close_all ran: the console is stopping, and a tunnel opened now
            # (by a job still finishing) would be closed by nothing.
            raise NodeUnreachableError(
                f"This console is stopping; no tunnel to {node} is opened",
                details="Start the console again, then retry.",
            )
        self.reap_idle()
        self._require_unlocked()
        override = _LOOPBACK_OVERRIDE.get(node)
        if override is not None:
            return LOOPBACK, override
        link = self._link_of(node)
        if link is not None:
            return self._link_endpoint(node, link)
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

    def _link_of(self, node: str) -> LinkRecord | None:
        """
        Find the link of a server that dials in to this central.

        Args:
            node: The node's name.

        Returns:
            Its accepted link, or None for a node this central reaches over SSH.
        """
        link = LinkRecords(self.store).get(node)
        return link if link is not None and link.state == ACTIVE else None

    def _link_endpoint(self, node: str, link: LinkRecord) -> tuple[str, int]:
        """
        Return where a server that dials in answers, after checking who is listening.

        There is no ssh to start: the server keeps its own forward open, and
        what it leaves is a listener on a loopback port of this machine. The
        fleet token is sent to whatever answers there, so the listener must be
        the link account's (:func:`~noust.fleet.link.listener.require_trusted_listener`).

        Args:
            node: The node's name.
            link: Its link.

        Returns:
            ``("127.0.0.1", port)``.

        Raises:
            NodeUnreachableError: When the server is not connected, or something
                else holds its port.
        """
        tunnel = self._entry(node)
        with tunnel.lock:
            try:
                require_trusted_listener(
                    node, link.port, self._link_owners(), proc_net=self._proc_net
                )
            except NodeUnreachableError as exc:
                tunnel.last_error = exc.details or exc.message
                tunnel.failures += 1
                raise
            tunnel.local_port = link.port
            tunnel.last_used = self._clock()
            tunnel.failures = 0
            tunnel.last_error = None
            return LOOPBACK, link.port

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
        options = NodeSSHOptions(self.store).get(node)
        self.keys.pin_bastion_host_key(
            node,
            f"{bastion_host_key_alias(node)} {options.bastion.host_key}"
            if options.bastion
            else None,
        )
        try:
            return self._start(node, tunnel, record, options)
        finally:
            # ssh has read its key and known_hosts by the time it listens, or
            # it has given up; the decrypted copies are not needed any more.
            self.keys.discard_usable(node)

    def _start(
        self, node: str, tunnel: _Tunnel, record: NodeRecord, options: SSHOptions
    ) -> tuple[str, int]:
        """
        Start ssh and wait until its forward listens.

        Called with the node's lock held, by :meth:`_open`.

        Args:
            node: The node's name.
            tunnel: Its entry.
            record: The node.
            options: How it is connected to beyond its address.

        Returns:
            The local endpoint.

        Raises:
            NodeUnreachableError: When ssh exits or does not come up in time.
            NodeRefusedError: When ssh's own words say the node no longer
                authorizes this key (see :func:`key_was_revoked`); persisted
                the same way a node's HTTP 401 is, so nothing keeps
                presenting it on an endless backoff.
        """
        if self._algorithms is None:
            # Once per manager: the client does not change under a running central.
            self._algorithms = ssh_algorithms(self.runner)
        local_port = self._free_port()
        handle = self.runner.start(
            ssh_argv(
                record,
                local_port,
                identity=self.keys.usable_private_key(node),
                known_hosts=self.keys.usable_known_hosts(node),
                algorithms=self._algorithms,
                options=options,
                bastion_known_hosts=(
                    self.keys.usable_bastion_known_hosts(node) if options.bastion else None
                ),
            )
        )
        deadline = self._clock() + self.ready_timeout
        while True:
            if not handle.is_alive():
                stderr = handle.stderr_tail()
                self._failed(tunnel, stderr)
                message = explain_ssh_failure(stderr, record, handle.exit_code, options.bastion)
                if host_key_changed(stderr):
                    self._alert_host_key(tunnel, record, stderr, options.bastion)
                if key_was_revoked(stderr, options.bastion):
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
        tunnel.host_key_alerted = False
        return LOOPBACK, local_port

    def _alert_host_key(
        self, tunnel: _Tunnel, record: NodeRecord, stderr: str, bastion: Bastion | None = None
    ) -> None:
        """
        Tell the operator a node, or the bastion in front of it, presented another host key.

        The tunnel stays closed whatever happens here; every retry of the
        backoff would otherwise be another alert about the same key.

        Args:
            tunnel: The node's entry, which remembers it was told.
            record: The node.
            stderr: ssh's standard error.
            bastion: The node's bastion, if it has one.
        """
        if tunnel.host_key_alerted:
            return
        tunnel.host_key_alerted = True
        from noust.core import audit
        from noust.core.notifications.fleet import notify_node_host_key_changed

        pinned, presented = host_key_fingerprints(stderr, record)
        if bastion is not None and failed_at_bastion(stderr, bastion):
            pinned = bastion.fingerprint
            hop = "bastion"
            address = f"bastion {bastion.address}"
            command = (
                f"noust node ssh {record.name} --bastion {bastion.address} "
                f"--bastion-fingerprint {presented or 'SHA256:...'}"
            )
        else:
            hop = "node"
            address = f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}"
            command = f"noust node rekey {record.name}"
        audit.record(
            "fleet.tunnel.hostkey_changed",
            target=f"node:{record.name}",
            outcome="failure",
            details={"pinned": pinned, "presented": presented, "hop": hop},
        )
        notify_node_host_key_changed(
            record.name,
            address=address,
            pinned=pinned,
            presented=presented,
            command=command,
        )

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
        link = self._link_of(node)
        if link is not None:
            owners = listener_uids(link.port, self._proc_net)
            connected = bool(owners) and owners <= self._link_owners()
            with self._lock:
                entry = self._tunnels.get(node)
            return {
                "open": connected,
                "local_port": link.port if connected else None,
                "since": None,
                "last_error": entry.last_error if entry is not None else None,
                "failures": entry.failures if entry is not None else 0,
                "transport": "link",
            }
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


def stop_all() -> None:
    """
    Close every tunnel of this process's manager, if it has one; create none.

    What the console runs whenever it stops: its lifespan's shutdown, and
    around the server itself, so a signal that ends the process past the
    lifespan still closes them.
    """
    with _tunnels_lock:
        manager = _tunnels
    if manager is not None:
        manager.close_all()


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


# ---------------------------------------------------------------------------
# Tunnels a previous console left behind
# ---------------------------------------------------------------------------

#: Where the kernel describes processes.
PROC = Path("/proc")

#: How long a stale tunnel has to end after SIGTERM before it is killed.
STALE_TERM_SECONDS = 2.0

#: Programs whose processes hold their tunnels: a tunnel whose parent is a
#: running Noust (a CLI command, another console) is that process's, not stale.
_NOUST_PROGRAMS = frozenset({"noust"})


@dataclass(frozen=True)
class StaleTunnel:
    """
    A tunnel process no running Noust holds.

    Attributes:
        pid: Its process id.
        node: The node it forwards to.
    """

    pid: int
    node: str


def tunnel_node(argv: list[str], secrets_roots: Iterable[Path]) -> str | None:
    """
    Name the node a process's argv is a Noust tunnel to, from the argv alone.

    Args:
        argv: The process's command line.
        secrets_roots: Where this central's node keys are: its secrets
            directory, and the private copies a sealed one hands ssh.

    Returns:
        The node's name when the argv is ssh with ``-N``, ``-F /dev/null``,
        ``-i <root>/fleet/nodes/<name>/id_ed25519`` and
        ``HostKeyAlias=noust-node-<name>`` for that same name; None otherwise.
    """

    def after(flag: str) -> str | None:
        indexes = [i for i, arg in enumerate(argv[:-1]) if arg == flag]
        return argv[indexes[0] + 1] if len(indexes) == 1 else None

    if not argv or Path(argv[0]).name != "ssh" or "-N" not in argv:
        return None
    if after("-F") != "/dev/null":
        return None
    identity = after("-i")
    if identity is None:
        return None
    for root in secrets_roots:
        try:
            parts = Path(identity).relative_to(root).parts
        except ValueError:
            continue
        if len(parts) != 4 or "/".join(parts[:2]) != NODES_NAMESPACE or parts[3] != PRIVATE_KEY:
            continue
        try:
            name = validate_node_name(parts[2])
        except NodeError:
            return None
        return name if f"HostKeyAlias={host_key_alias(name)}" in argv else None
    return None


def _cmdline(proc: Path, pid: int) -> list[str] | None:
    """
    Read a process's argv.

    Args:
        proc: ``/proc``.
        pid: The process.

    Returns:
        The arguments, or None when it is gone or unreadable.
    """
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _owner_and_parent(proc: Path, pid: int) -> tuple[int, int] | None:
    """
    Read a process's real uid and parent.

    Args:
        proc: ``/proc``.
        pid: The process.

    Returns:
        ``(uid, ppid)``, or None when it is gone or unreadable.
    """
    try:
        status = (proc / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    try:
        return int(fields["Uid"].split()[0]), int(fields["PPid"].split()[0])
    except (KeyError, IndexError, ValueError):
        return None


def _is_noust(argv: list[str] | None) -> bool:
    """
    Report whether a process is Noust itself: the CLI or a console.

    Args:
        argv: Its command line.

    Returns:
        True when its program, or the script its interpreter runs, is
        ``noust``, or it runs ``python -m noust``.
    """
    if not argv:
        return False
    if any(Path(arg).name in _NOUST_PROGRAMS for arg in argv[:2]):
        return True
    return any(
        argv[i] == "-m" and argv[i + 1].split(".")[0] == "noust" for i in range(len(argv) - 1)
    )


def stop_stale_tunnels(
    secrets_roots: Iterable[Path],
    *,
    proc: Path = PROC,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    uid: int | None = None,
) -> list[StaleTunnel]:
    """
    Stop the tunnels a console that is gone left running.

    A tunnel is stale when its argv is exactly one this module builds
    (:func:`tunnel_node`), it runs as this account, and no running Noust is
    its parent: it was reparented to init or a subreaper when its console
    died. SIGTERM first; SIGKILL when it is still there after
    :data:`STALE_TERM_SECONDS`.

    Args:
        secrets_roots: Where this central's node keys are.
        proc: ``/proc``.
        kill: Sends a signal.
        sleep: Waits between checks.
        uid: Only this account's processes; the effective uid by default.

    Returns:
        The tunnels stopped.
    """
    roots = list(secrets_roots)
    account = os.geteuid() if uid is None else uid
    try:
        pids = sorted(int(entry.name) for entry in proc.iterdir() if entry.name.isdigit())
    except OSError:
        return []
    stale: list[tuple[StaleTunnel, list[str]]] = []
    for pid in pids:
        argv = _cmdline(proc, pid)
        node = tunnel_node(argv, roots) if argv else None
        if argv is None or node is None:
            continue
        owner = _owner_and_parent(proc, pid)
        if owner is None or owner[0] != account:
            continue
        parent = owner[1]
        if parent != 1 and _is_noust(_cmdline(proc, parent)):
            continue
        stale.append((StaleTunnel(pid, node), argv))
    stopped: list[StaleTunnel] = []
    for tunnel, argv in stale:
        try:
            kill(tunnel.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            logger.warning(
                "Could not stop the stale tunnel %s to %s: %s", tunnel.pid, tunnel.node, exc
            )
            continue
        waited = 0.0
        while _cmdline(proc, tunnel.pid) == argv and waited < STALE_TERM_SECONDS:
            sleep(READY_POLL)
            waited += READY_POLL
        if _cmdline(proc, tunnel.pid) == argv:
            with contextlib.suppress(ProcessLookupError):
                kill(tunnel.pid, signal.SIGKILL)
        logger.warning(
            "Stopped a tunnel to %s (PID %s) that a console no longer running left behind",
            tunnel.node,
            tunnel.pid,
        )
        stopped.append(tunnel)
    return stopped


def sweep_stale_tunnels() -> list[StaleTunnel]:
    """
    Stop the tunnels a previous console of this central left running.

    Called when the console starts, before it opens any.

    Returns:
        The tunnels stopped.
    """
    from noust.core.secrets import secrets_dir

    root = secrets_dir()
    return stop_stale_tunnels([root, sealing.plaintext_copy_dir(root)])
