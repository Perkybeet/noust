# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The fleet: a central and a node as two real processes, without ssh."""

from __future__ import annotations

import json

# Only for the fleet's --seal bootstrap (see seal_sandbox_before_serving): a real,
# throwaway process that seals a sandbox's secrets before this one serves it, so this
# process never learns the passphrase itself and starts up genuinely locked. Nothing
# this script serves ever runs through it - noust.core.runner.CommandRunner still is,
# via the fake installed below - and CONTRIBUTING.md's rule 1 is about src/noust, not this
# development and test-only script.
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .constants import LAUNCHER, OLDER_NODE_MISSING
from .sandbox import Sandbox, prepare_environment, redirect_system_paths

# ---------------------------------------------------------------------------
# The fleet: a central and a node as two real processes, without ssh
# ---------------------------------------------------------------------------
#
# Playwright's fleet suite runs two of these servers - a "central" and a "node" -
# and needs the central to reach the node exactly as it would in production: a real
# HTTP request, with the real fleet token, answered by the node's real API. The one
# thing missing on purpose is ssh: the sandbox has no sshd, no `authorized_keys` and
# no root shell to authorize a key against, and none of that is worth modelling to
# prove the fleet works. What follows replaces exactly the two things ssh would
# otherwise be needed for - `noust fleet authorize` reading this machine's own SSH
# host key and installing a key in `authorized_keys`, and the central's tunnel
# turning a node's name into a local port - and nothing else: the token is minted by
# the real TokenManager.create_fleet_token, the join code is the real JoinCode, and
# registration is the real NodeManager.add, which is what a locked central's
# _require_unlocked still refuses, and what a stopped node still fails against with a
# genuine connection error. None of this is reachable from `noust.web.api`; it is
# mounted only here, from this script, and ships in no wheel, deb, rpm or container.

#: Where a node's test-only route mints a join code (see mount_fleet_test_seam).
#: Never a real Noust path: '__test__' names it unmistakably, for anyone reading a
#: request log wondering why a "node" answers something noust.web.api does not have.
FLEET_TEST_MINT_PATH = "/__test__/fleet/mint-join-code"


def mount_fleet_test_seam(app: Any, *, console_port: int) -> None:
    """
    Add the one route that stands in for ``noust fleet authorize`` on a node.

    Real ``noust fleet authorize`` reads this machine's own SSH host key from
    ``/etc/ssh`` and installs a restricted line in the SSH user's
    ``authorized_keys``; a sandboxed, unprivileged test process may do neither. Both
    are about ssh, which this fleet E2E setup does not use at all (see the module
    comment above), so this route does only what is left: mint a fleet token, on this
    node's own store, with the very method production issues one with
    (:meth:`noust.web.auth.TokenManager.create_fleet_token`), and encode it in a real
    :class:`noust.fleet.joincode.JoinCode`, exactly as ``authorize()`` would return one.

    Mounted unconditionally - it is inert until a central's ``--fleet-node`` calls
    it - so a plain ``console_server.py`` run is unaffected.

    Args:
        app: The FastAPI app this node serves.
        console_port: This node's own bound port, embedded in the join code
            exactly as ``noust fleet authorize`` fills in the console's real port.
    """
    from fastapi import Body

    from noust import __version__
    from noust.fleet.authorize import next_token_name
    from noust.fleet.joincode import JoinCode
    from noust.fleet.models import parse_public_key
    from noust.web.server import get_token_manager
    from tests.support.fleet_support import ed25519_line

    # Not called inline in the signature below (ruff B008): a route's parameter defaults
    # are evaluated once, at import time, same as this is, so there is no difference but
    # the lint rule, which exists for defaults that are NOT meant to run once.
    body_default = Body(...)

    @app.post(FLEET_TEST_MINT_PATH, include_in_schema=False)
    def mint_join_code(body: dict[str, Any] = body_default) -> dict[str, str]:
        """Mint a fleet token and a join code for the central named in the body."""
        central = str(body["central_name"])
        node_name = str(body["node_name"])
        central_key = parse_public_key(str(body["central_key"]), what="central key")
        tokens = get_token_manager()
        issued = tokens.create_fleet_token(next_token_name(tokens, central))
        # A structurally valid host key nothing ever checks: the loopback seam
        # (set_loopback_for_testing) never opens ssh, so nothing ever verifies it
        # against a real one.
        host_key = ed25519_line(hash(node_name) & 0xFFFF, "")
        code = JoinCode(
            ssh_host_key=host_key,
            ssh_user="root",
            ssh_port=22,
            console_port=console_port,
            token=str(issued["token"]),
            noust_version=__version__,
            central_key_fp=central_key.fingerprint,
            node_name=node_name,
            token_name=str(issued["name"]),
            central=central,
        )
        return {"join_code": code.encode()}


def pretend_older_node(app: Any, version: str) -> Any:
    """
    Make this node answer as an older Noust would: its version, and 404 for what it lacks.

    Test-only (the fleet's E2E suite, through :data:`OLDER_NODE_ENV`): a central's fleet
    views must show such a node as "unsupported", with what it does not offer, rather than
    fail. Only the answers change; the rest of the node is the real one.

    Args:
        app: The node's ASGI application, with every route already mounted.
        version: The version it reports.

    Returns:
        The application to serve.
    """
    import noust.web.api.system as system_api

    system_api.__version__ = version
    body = json.dumps({"detail": "Not Found"}).encode()

    async def older(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") in OLDER_NODE_MISSING:
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)

    return older


def join_fleet_node(entries: list[str], *, display_hosts: dict[str, str] | None = None) -> None:
    """
    Register, on this central, every node named by ``--fleet-node NAME=HOST:PORT``.

    Ties together three pieces of real product code across two real processes, the
    only fake being the one line of ``noust.fleet.tunnels.set_loopback_for_testing``
    that stands in for ssh (see that function):

    1. this central generates its own key pair for the node
       (:meth:`noust.fleet.nodes.NodeManager.central_public_key`; real
       ``ssh-keygen``, faked by :class:`ConsoleRunner` above only because this
       sandbox has no real one to run and never opens the connection it is for);
    2. it asks the node's test seam to mint a join code with that key
       (:data:`FLEET_TEST_MINT_PATH`), over a genuine HTTP request to the node's
       own, already-running API;
    3. it registers the node with :meth:`noust.fleet.nodes.NodeManager.add`,
       exactly as ``noust node add`` or ``POST /api/nodes`` would: it pins the
       (fake) host key, stores the (real) token, saves the record, and then asks
       the node's real API for its version, through the tunnel manager - which,
       with the loopback seam set first, answers with the node's real address
       instead of dialling ssh, so what follows is a genuine HTTP call, with the
       genuine fleet token, straight to that node's own FastAPI process. A locked
       central's ``_require_unlocked`` still refuses every node (checked before
       the seam), and a stopped node still fails this with a real connection error.

    Must run after ``set_runner`` installs the fake ``ssh-keygen`` and before the
    server starts accepting connections that might ask for this node's tunnel.

    Args:
        entries: ``["web-2=127.0.0.1:41234", ...]``, as ``--fleet-node`` collected them.
        display_hosts: Node name to the host to *store and show* as its SSH address
            (``--showcase`` only), in place of the loopback address the tunnel actually
            dials: Settings > Servers otherwise shows ``root@127.0.0.1`` for every node,
            which is this test seam showing through, not something a documentation
            screenshot should carry (coordinator review). The tunnel keeps routing by node
            name (:func:`~noust.fleet.tunnels.set_loopback_for_testing`, below, unaffected
            by this), so the address stored is display-only either way.

    Raises:
        SystemExit: When an entry is not ``NAME=HOST:PORT``, so a typo in a test
            fails at start-up rather than in a request three tests later.
    """
    import httpx

    from noust.fleet.models import central_name
    from noust.fleet.nodes import NodeManager
    from noust.fleet.tunnels import set_loopback_for_testing

    manager = NodeManager()
    this_central = central_name()
    display_hosts = display_hosts or {}

    for entry in entries:
        name, sep, address = entry.partition("=")
        host, hsep, port_text = address.partition(":")
        if not sep or not hsep or not name or not port_text.isdigit():
            raise SystemExit(f"console_server: --fleet-node wants NAME=HOST:PORT, not {entry!r}")
        port = int(port_text)
        set_loopback_for_testing(name, port)
        public_key = manager.central_public_key(name)
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            response = client.post(
                f"http://{host}:{port}{FLEET_TEST_MINT_PATH}",
                json={"central_name": this_central, "central_key": public_key, "node_name": name},
            )
            response.raise_for_status()
            join_code = response.json()["join_code"]
        shown_host = display_hosts.get(name, host)
        manager.add(name, ssh_target=f"root@{shown_host}", join_code=join_code)
        _ENROLL_NODES.append((host, port))


#: The nodes this central was started with, which the guided enrollment's model answers
#: for: a server enrolled through the console is one of these processes under another name,
#: since the sandbox has no sshd to install anything on.
_ENROLL_NODES: list[tuple[str, int]] = []

#: The password the enrollment model refuses, as a server would (the credential check).
ENROLL_REFUSED_LOGIN = "wrong-password"

#: The address that answers no host key at all (nothing listens there).
ENROLL_SILENT_HOST = "unreachable.example.org"


def _enroll_host_key(host: str) -> str:
    """
    Give the host key a modelled server presents: the same for an address every time.

    Args:
        host: The address that was scanned or connected to.

    Returns:
        The ed25519 public key line, without a comment.
    """
    import zlib

    from tests.support.fleet_support import ed25519_line

    return ed25519_line(zlib.crc32(host.encode()), "")


def _enroll_keyscan(args: tuple[str, ...]) -> Any:
    """
    Answer ``ssh-keyscan`` for the guided enrollment, as a server with an ed25519 key does.

    Args:
        args: ``ssh-keyscan -t ed25519 -T 10 -p PORT HOST``.

    Returns:
        The key line ssh-keyscan prints, or nothing for the address that answers none.
    """
    from noust.core.runner import CommandResult

    host = args[-1]
    if host == ENROLL_SILENT_HOST:
        return CommandResult(args, 0, "", f"# {host}:22 : Connection timed out\n")
    return CommandResult(args, 0, f"{host} {_enroll_host_key(host)}\n", "")


def _enroll_ssh(args: tuple[str, ...], env: Mapping[str, str] | None) -> Any:
    """
    Answer the ssh calls of a guided enrollment: the server a central connects to.

    The commands are the ones the enroller runs (who the account is and whether Noust is
    there, ``noust fleet authorize``, ``deauthorize``). The one thing real here is the join
    code: a node of this sandbox mints it, with the host key the model presented in the
    scan, so the central registers what it would register against a real server.

    Args:
        args: The ssh argv, ``ssh ... -- HOST 'COMMAND'``.
        env: The call's environment; the password is in it, never in argv.

    Returns:
        What the server would answer.
    """
    import shlex

    from noust import __version__
    from noust.core.runner import CommandResult

    if (env or {}).get("NOUST_ENROLL_SECRET") == ENROLL_REFUSED_LOGIN:
        return CommandResult(args, 255, "", "Permission denied, please try again.\n")
    separator = args.index("--")
    host = args[separator + 1]
    remote = shlex.split(args[-1])
    if remote[:2] == ["sudo", "-n"]:
        remote = remote[2:]
    if remote[:2] == ["sh", "-c"] and remote[2].startswith("id -u"):
        return CommandResult(args, 0, f"0\nNoust {__version__}\n", "")
    if remote[:3] == ["noust", "fleet", "authorize"]:
        return _enroll_authorize(args, remote, host)
    return CommandResult(args, 0, "", "")


def _enroll_authorize(args: tuple[str, ...], remote: list[str], host: str) -> Any:
    """
    Answer ``noust fleet authorize --json`` with a join code a node of this sandbox minted.

    Args:
        args: The ssh argv, for the result.
        remote: The command the server was asked to run.
        host: The address connected to, whose scanned host key the code must carry.

    Returns:
        The JSON line the command prints, or its failure when no node can stand in.
    """
    import httpx

    from noust.core.runner import CommandResult
    from noust.fleet.joincode import JoinCode
    from noust.fleet.tunnels import set_loopback_for_testing

    if not _ENROLL_NODES:
        return CommandResult(
            args, 1, "", "noust: no server of this sandbox can stand in for that one\n"
        )
    alias = next(
        (part.split("=", 1)[1] for part in args if part.startswith("HostKeyAlias=noust-enroll-")),
        "",
    )
    node_name = alias.removeprefix("HostKeyAlias=").removeprefix("noust-enroll-")
    node_host, node_port = _ENROLL_NODES[0]
    set_loopback_for_testing(node_name, node_port)
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(
            f"http://{node_host}:{node_port}{FLEET_TEST_MINT_PATH}",
            json={
                "central_name": remote[remote.index("--name") + 1],
                "central_key": remote[remote.index("--central-key") + 1],
                "node_name": node_name,
            },
        )
        response.raise_for_status()
    code = replace(
        JoinCode.decode(response.json()["join_code"]), ssh_host_key=_enroll_host_key(host)
    )
    return CommandResult(
        args, 0, json.dumps({"join_code": code.encode(), "warnings": []}) + "\n", ""
    )


def seal_sandbox_before_serving(sandbox: Sandbox) -> None:
    """
    Seal this sandbox's secrets in a throwaway subprocess, before this one serves.

    :func:`noust.core.sealing.unlock` (which :func:`noust.core.sealing.seal_store`
    also calls, on success) remembers the derived keys in the *calling process*, so
    the process that goes on to serve requests has to be a different one for it to
    start genuinely locked - exactly like a central started after ``noust central
    seal`` on a previous run, never given the passphrase this run. The passphrase is
    read from standard input here, and passed to the child the same way, never in
    argv (see ``noust.cli.commands.central._read_passphrase``, which this mirrors).

    Args:
        sandbox: The sandbox, already prepared and with its config written.

    Raises:
        SystemExit: When standard input holds no usable passphrase, or the
            bootstrap subprocess fails (its stderr is included).
    """
    from noust.core.sealing import MIN_PASSPHRASE_LENGTH

    passphrase = sys.stdin.readline().rstrip("\r\n")
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise SystemExit(
            f"console_server: --seal needs a passphrase of {MIN_PASSPHRASE_LENGTH}+ "
            "characters on standard input"
        )
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "--internal-seal", str(sandbox.root)],
        input=passphrase,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise SystemExit(f"console_server: sealing the sandbox failed:\n{result.stderr}")


def run_internal_seal(sandbox_root: Path) -> int:
    """
    Seal an already-prepared sandbox's secrets: the ``--internal-seal`` entry point.

    Never invoked directly; :func:`seal_sandbox_before_serving` runs this same
    script with this flag as a short-lived child, precisely so the sealing happens
    outside the process that goes on to serve. See that function's docstring.

    Args:
        sandbox_root: The sandbox the parent process already created and prepared.

    Returns:
        0.
    """
    sandbox = Sandbox(sandbox_root)
    prepare_environment(sandbox)
    redirect_system_paths(sandbox)
    from noust.core import sealing
    from noust.core.secrets import secrets_dir

    passphrase = sys.stdin.readline().rstrip("\r\n")
    sealing.seal_store(secrets_dir(), passphrase)
    return 0
