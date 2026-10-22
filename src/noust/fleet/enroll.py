# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Guided enrollment: a central brings a server into the fleet with a credential used once.

The manual way is three steps across two machines: ``noust node key`` on the
central, ``noust fleet authorize`` on the server, ``noust node add`` back on the
central. This does the same three, from the central, for an operator who has an
address and a way in (a password, or a key) and nothing installed on the server:

1. the central generates its key for the server (``NodeManager.central_public_key``);
2. it connects over SSH with the credential, to the host key the operator
   confirmed, installs Noust there if it is missing (its own copy of
   ``packaging/install.sh``, sent over the connection and run: nothing is
   downloaded by the server) and runs ``noust fleet authorize`` as root;
3. it takes the join code that prints and completes the join exactly as
   ``noust node add`` does (``NodeManager.add``).

The result is the one the manual way gives: a ``noust-tunnel`` account with no
shell, a key restricted to the console's port, a pinned host key, a token, the
ceiling the operator chose. What the central keeps of the credential is nothing.
It is held in memory for the duration of the call, handed to ssh through its
environment (a password or a passphrase, by a helper script that only prints
that variable) or through a file in a private, memory-backed directory (a key),
and the directory is removed on every way out, a failure included. It is never
in an argument list, a log line, a job's result, the audit trail or an
approval's record.

The host key is never taken on trust. The operator is shown the fingerprint of
what answers (:meth:`NodeEnroller.scan_host_key`) and gives it back; the
connection is made with that key and no other, and the join code the server then
prints must carry the same one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets as token_source
import shlex
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core import audit, sealing
from noust.core.exceptions import NodeError, NoustError
from noust.core.fs import SECRET_MODE, FileSystem, get_fs
from noust.core.runner import EXIT_NOT_FOUND, CommandResult, CommandRunner, get_runner
from noust.core.secrets import secrets_dir
from noust.core.store import NodeRecord
from noust.fleet.joincode import JoinCode
from noust.fleet.models import (
    PublicKey,
    central_name,
    parse_public_key,
    parse_ssh_target,
    validate_node_name,
    validate_port,
    validate_ssh_user,
)
from noust.fleet.nodes import NodeManager
from noust.fleet.policy import validate_access_level
from noust.fleet.tunnels import (
    CONNECT_TIMEOUT,
    SERVER_ALIVE_COUNT,
    SERVER_ALIVE_INTERVAL,
    ssh_algorithms,
)

logger = logging.getLogger(__name__)

#: Where the central's own copy of the installer is, when the package carries one.
INSTALLER_PATHS: tuple[Path, ...] = (Path("/usr/share/noust/install.sh"),)

#: The oldest Noust a server may already run: the one with the tunnel account.
MINIMUM_NODE_VERSION = (3, 1, 0)

#: Seconds ``ssh-keyscan`` may take.
KEYSCAN_TIMEOUT = 20
#: Seconds a short remote command may take.
REMOTE_TIMEOUT = 120
#: Seconds the installer (packages, setup) may take on the server.
INSTALL_TIMEOUT = 1800
#: Seconds ``noust fleet authorize`` may take.
AUTHORIZE_TIMEOUT = 300

#: What the probe prints when Noust is not on the server.
MISSING = "NOUST_MISSING"

#: Longest private key text accepted: an RSA 4096 key in OpenSSH format is about 3.4 KB.
MAX_PRIVATE_KEY = 16384

#: How much of a failing command's output goes in an error.
OUTPUT_TAIL = 2000

_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_VERSION = re.compile(r"Noust (\d+)\.(\d+)\.(\d+)")

#: ``log(message, level)``: the job's log, or a terminal.
Log = Callable[[str, str], None]


def _quiet(_message: str, _level: str = "info") -> None:
    """Say nothing."""


@dataclass(repr=False)
class Credential:
    """
    What lets the central in once: a password, or a private key.

    Attributes:
        password: The account's password.
        private_key: A private key in OpenSSH or PEM format, as text.
        passphrase: The key's passphrase, when it has one.
    """

    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None

    def __repr__(self) -> str:
        """Describe it without any of it."""
        kind = "key" if self.private_key else "password" if self.password else "none"
        return f"Credential({kind}, ***)"

    __str__ = __repr__

    def validate(self) -> None:
        """
        Check there is exactly one way in, and that a key looks like one.

        Raises:
            NodeError: When there is neither a password nor a key, or both, or the
                key is not a private key.
        """
        if bool(self.password) == bool(self.private_key):
            raise NodeError(
                "Give a password or a private key, one of them",
                details="The central uses it once to authorize itself on the server and "
                "does not keep it.",
                field="credential",
            )
        if self.private_key:
            key = self.private_key
            if len(key) > MAX_PRIVATE_KEY or not _PRIVATE_KEY.search(key):
                raise NodeError(
                    "That is not a private key",
                    details="Paste the whole file, from '-----BEGIN OPENSSH PRIVATE KEY-----' "
                    "to its END line (the private half, not the .pub file).",
                    field="private_key",
                )
        if self.passphrase and not self.private_key:
            raise NodeError("A passphrase goes with a private key", field="passphrase")

    @property
    def secret(self) -> str | None:
        """What ssh will ask for: the password, or the key's passphrase."""
        return self.password or self.passphrase or None

    def secrets(self) -> list[str]:
        """
        List every value to keep out of a log.

        Returns:
            The password, the passphrase and the key, those that are set.
        """
        return [value for value in (self.password, self.passphrase, self.private_key) if value]


@dataclass
class EnrollRequest:
    """
    What the operator asked for.

    Attributes:
        name: The name the server gets on this central.
        host: Its address.
        port: Where its sshd listens.
        ssh_user: The account the credential is for. ``root``, or an account
            with passwordless ``sudo``.
        host_key_fingerprint: The ed25519 host key's fingerprint, as the operator
            confirmed it (``SHA256:...``).
        credential: The credential.
        access: The ceiling the server sets for this central: ``read``,
            ``deploy`` or ``admin``.
        host_access: Whether the central may also reach the host itself there.
        install: Install Noust when it is missing. False refuses instead.
        replace_token: The server may already hold a token for this central:
            revoke it and issue a new one.
        retire_key: A public key (``ssh-ed25519 AAAA...``) to take out of the
            account's ``authorized_keys`` once the server has joined: the one-time
            login a provisioned server was created with, so nothing is left behind
            that the central no longer holds the other half of.
    """

    name: str
    host: str
    host_key_fingerprint: str
    credential: Credential
    port: int = 22
    ssh_user: str = "root"
    access: str = "admin"
    host_access: bool = False
    install: bool = True
    replace_token: bool = False
    retire_key: str | None = None

    def __repr__(self) -> str:
        """Describe it without the credential."""
        return (
            f"EnrollRequest(name={self.name!r}, host={self.host!r}, port={self.port}, "
            f"ssh_user={self.ssh_user!r}, access={self.access!r})"
        )


@dataclass(frozen=True)
class HostKeyScan:
    """
    What answered at an address.

    Attributes:
        host: The address that was asked.
        port: Its port.
        key: The ed25519 host key.
    """

    host: str
    port: int
    key: PublicKey

    @property
    def fingerprint(self) -> str:
        """The key's ``SHA256:...``, as ``ssh-keygen -l`` prints it."""
        return self.key.fingerprint

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and the CLI.

        Returns:
            The address, the key type and its fingerprint.
        """
        return {
            "host": self.host,
            "port": self.port,
            "key_type": self.key.key_type,
            "fingerprint": self.fingerprint,
        }


@dataclass
class _Session:
    """Everything one enrollment needs to open an SSH connection."""

    request: EnrollRequest
    directory: Path
    known_hosts: Path
    identity: Path | None
    askpass: Path | None
    algorithms: dict[str, str]
    sudo: bool = False
    secret_values: list[str] = field(default_factory=list)


def enroll_alias(node: str) -> str:
    """
    Name the host key entry of an enrollment's ``known_hosts`` file.

    A name of its own, apart from the pin a registered node gets
    (:func:`~noust.fleet.keys.host_key_alias`): the two never meet, and the one
    here exists only while the enrollment runs.

    Args:
        node: The node's name.

    Returns:
        ``noust-enroll-<node>``.
    """
    return f"noust-enroll-{validate_node_name(node)}"


def find_installer(paths: Sequence[Path] = INSTALLER_PATHS) -> Path | None:
    """
    Find this central's own copy of the installer.

    Args:
        paths: Where the package puts it.

    Returns:
        The file, or None when this central was not installed from a package.
    """
    for path in paths:
        if path.is_file():
            return path
    return None


def _tail(text: str) -> str:
    """
    Keep the end of a command's output for an error.

    Args:
        text: The output.

    Returns:
        At most :data:`OUTPUT_TAIL` characters of its end, trimmed.
    """
    text = text.strip()
    return text if len(text) <= OUTPUT_TAIL else "..." + text[-OUTPUT_TAIL:]


def _explain(stderr: str) -> str:
    """
    Say what an ssh failure most likely means, after ssh's own words.

    ssh's output is shown as it is; the hint only names what to look at.

    Args:
        stderr: What ssh printed.

    Returns:
        The output and one sentence of advice.
    """
    lowered = stderr.lower()
    if "permission denied" in lowered:
        hint = (
            "The server refused the credential. Check the account name and the password or "
            "key; root login with a password must be allowed by sshd (PermitRootLogin)."
        )
    elif "host key verification failed" in lowered or "identification has changed" in lowered:
        hint = (
            "What answered is not the server whose fingerprint you confirmed. Nothing was sent. "
            "Check the address and scan the host key again."
        )
    elif "connection refused" in lowered or "timed out" in lowered or "no route" in lowered:
        hint = "The server did not answer on that address and port. Check both, and its firewall."
    elif "could not resolve" in lowered or "name or service not known" in lowered:
        hint = "The name does not resolve from this central."
    else:
        hint = "See ssh's own words above."
    return f"{_tail(stderr)}\n{hint}".strip()


class NodeEnroller:
    """
    Enroll servers from this central.

    Args:
        manager: The node registry the join completes through; a new one by default.
        runner: The runner ssh goes through; the process-wide one by default.
        fs: The filesystem seam the credential's files are written through.
        installer: Where the central's copy of the installer is; looked up by default.
    """

    def __init__(
        self,
        manager: NodeManager | None = None,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        installer: Path | None = None,
    ) -> None:
        self._manager = manager
        self._runner = runner
        self._fs = fs
        self._installer = installer

    @property
    def manager(self) -> NodeManager:
        """The node registry."""
        if self._manager is None:
            self._manager = NodeManager(runner=self._runner)
        return self._manager

    @property
    def runner(self) -> CommandRunner:
        """The runner."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam."""
        return self._fs or get_fs()

    # -- The host key ------------------------------------------------------

    def scan_host_key(self, host: str, port: int = 22) -> HostKeyScan:
        """
        Ask an address for its ed25519 host key, for the operator to confirm.

        Nothing is trusted by this: it only shows what answers. The enrollment
        itself connects to the key whose fingerprint the operator gives back.

        Args:
            host: The address.
            port: Its sshd port.

        Returns:
            The key.

        Raises:
            NodeError: When the address is malformed, nothing answers, or the
                server has no ed25519 host key.
        """
        target = parse_ssh_target(host)
        if target.user is not None or target.port is not None:
            raise NodeError(
                f"Give the address alone, not {host!r}",
                details="The account and the port are separate fields.",
                field="host",
            )
        port = validate_port(port, "SSH port")
        result = self.runner.run(
            ["ssh-keyscan", "-t", "ed25519", "-T", "10", "-p", str(port), target.host],
            timeout=KEYSCAN_TIMEOUT,
        )
        if result.exit_code == EXIT_NOT_FOUND:
            raise NodeError(
                "ssh-keyscan is not installed on this central",
                details="Install the OpenSSH client (openssh-client), then try again.",
            )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and not line.startswith("#"):
                return HostKeyScan(
                    host=target.host,
                    port=port,
                    key=parse_public_key(f"{fields[1]} {fields[2]}", what="server's host key"),
                )
        raise NodeError(
            f"{target.host}:{port} gave no ed25519 host key",
            details=(
                f"{_tail(result.stderr) or 'Nothing answered.'}\nCheck the address and the "
                "port, and that its sshd offers an ed25519 host key "
                "(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub on the server)."
            ),
        )

    # -- Enrollment --------------------------------------------------------

    def enroll(
        self, request: EnrollRequest, *, actor: str | None = None, log: Log = _quiet
    ) -> NodeRecord:
        """
        Enroll a server, from the first connection to the registered node.

        Args:
            request: The server and the credential.
            actor: Who asked, for the server's audit log (an actor label); the
                registry's default when None.
            log: Told each step, and the installer's output, as ``(message, level)``.

        Returns:
            The registered node, reachable.

        Raises:
            NodeError: At any step, with what to do. Nothing is registered, the
                server's authorization is withdrawn when it was made, and the
                credential's files are removed.
        """
        self.validate(request)
        name = request.name
        audit.record(
            "fleet.enroll.start",
            target=f"node:{name}",
            details={
                "host": request.host,
                "port": request.port,
                "ssh_user": request.ssh_user,
                "access": request.access,
                "host_access": request.host_access,
                "host_key": request.host_key_fingerprint,
                "install": request.install,
            },
        )
        outcome = "failure"
        details: dict[str, Any] = {"host": request.host}
        try:
            node = self._run(request, actor, log)
            outcome = "ok"
            details["version"] = node.version
            return node
        except NoustError as exc:
            details["error"] = exc.message
            raise
        finally:
            audit.record(
                "fleet.enroll.finish", target=f"node:{name}", outcome=outcome, details=details
            )

    def validate(self, request: EnrollRequest) -> None:
        """
        Check everything about a request that can be checked without connecting.

        Args:
            request: The request.

        Raises:
            NodeError: When any part is wrong, this central may not add servers
                yet, or the name is taken.
        """
        validate_node_name(request.name)
        validate_ssh_user(request.ssh_user)
        validate_port(request.port, "SSH port")
        validate_access_level(request.access)
        target = parse_ssh_target(request.host)
        if target.user is not None or target.port is not None:
            raise NodeError(
                f"Give the address alone, not {request.host!r}",
                details="The account and the port are separate fields.",
                field="host",
            )
        if not _FINGERPRINT.fullmatch(request.host_key_fingerprint):
            raise NodeError(
                "The host key fingerprint is missing or malformed",
                details="Scan the server first and confirm what it shows: SHA256:... "
                "(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub on the server).",
                field="host_key_fingerprint",
            )
        request.credential.validate()
        blockers = self.manager.registration_blockers()
        if blockers:
            raise NodeError(
                "This central may not register servers yet", details="\n".join(blockers)
            )
        if self.manager.store.get_node(request.name) is not None:
            raise NodeError(
                f"A node named {request.name} is already registered",
                details=f"Pick another name, or remove it first: noust node remove {request.name}",
                field="name",
            )

    def _run(self, request: EnrollRequest, actor: str | None, log: Log) -> NodeRecord:
        """
        Do the enrollment inside a private directory that goes away afterwards.

        Args:
            request: The validated request.
            actor: Who asked.
            log: Where to say what happens.

        Returns:
            The registered node.
        """
        name = request.name
        log(f"Checking the host key of {request.host}:{request.port}", "info")
        scan = self.scan_host_key(request.host, request.port)
        if scan.fingerprint != request.host_key_fingerprint:
            raise NodeError(
                "The host key is not the one you confirmed",
                details=(
                    f"{request.host}:{request.port} presents {scan.fingerprint}; you confirmed "
                    f"{request.host_key_fingerprint}. Either the address is not the server you "
                    "meant or something stands between this central and it. Nothing was sent."
                ),
                field="host_key_fingerprint",
            )
        log(f"The host key is {scan.fingerprint}, as confirmed", "success")

        log(f"Preparing this central's key for {name}", "info")
        central_key = self.manager.central_public_key(name)
        central = central_name()

        with self._session(request, scan) as session:
            version = self._probe(session, log)
            if version is None:
                version = self._install(session, log)
            log(f"Noust {version} is on the server", "info")

            join_code = self._authorize(session, central, central_key, log)
            try:
                code = JoinCode.decode(join_code)
                if code.ssh_host_key != scan.key.bare:
                    raise NodeError(
                        "The server that authorized this central is not the one that was scanned",
                        details=(
                            "Its join code carries another host key than the one you confirmed. "
                            "Nothing was registered; the authorization is being withdrawn."
                        ),
                    )
                log("Joining: pinning the host key and opening the tunnel", "info")
                target = (
                    f"[{request.host}]:{request.port}"
                    if ":" in request.host
                    else f"{request.host}:{request.port}"
                )
                node = self.manager.add(name, ssh_target=target, join_code=join_code, actor=actor)
            except NoustError as exc:
                self._withdraw(session, central, log)
                raise NodeError(
                    f"{name} could not be added: {exc.message}",
                    details=(
                        f"{exc.details}\nThe server's authorization of this central was "
                        "withdrawn, so nothing is left half-done. Run the enrollment again."
                    ).strip(),
                ) from exc
            if request.retire_key is not None:
                self._retire_key(session, request.retire_key, log)
        log(f"{name} is enrolled: {node.status}", "success")
        return node

    # -- The connection ----------------------------------------------------

    @contextmanager
    def _session(self, request: EnrollRequest, scan: HostKeyScan) -> Iterator[_Session]:
        """
        Prepare what ssh needs and remove it afterwards, whatever happens.

        The private directory is memory-backed where the system has such a place;
        it holds the pinned ``known_hosts`` and, for a key credential, the key.
        The password helper is a script with no secret in it, kept beside the
        store so that it can execute (``/run`` and ``/dev/shm`` may be ``noexec``).

        Args:
            request: The request.
            scan: The host key that was confirmed.

        Yields:
            The session.
        """
        root = secrets_dir()
        base = sealing.plaintext_copy_dir(root) / "enroll"
        sealing.ensure_private_dir(base, self.fs)
        directory = base / token_source.token_hex(8)
        self.fs.make_dir(directory, mode=0o700, parents=True)
        known_hosts = directory / "known_hosts"
        identity: Path | None = None
        askpass: Path | None = None
        try:
            self.fs.write_text(
                known_hosts,
                f"{enroll_alias(request.name)} {scan.key.bare}\n",
                mode=SECRET_MODE,
            )
            credential = request.credential
            if credential.private_key:
                identity = directory / "identity"
                text = credential.private_key.replace("\r\n", "\n").rstrip("\n") + "\n"
                self.fs.write_text(identity, text, mode=SECRET_MODE)
            if credential.secret:
                askpass = self._askpass()
            yield _Session(
                request=request,
                directory=directory,
                known_hosts=known_hosts,
                identity=identity,
                askpass=askpass,
                algorithms=ssh_algorithms(self.runner),
                secret_values=credential.secrets(),
            )
        finally:
            self.fs.remove_tree(directory)

    def _askpass(self) -> Path:
        """
        Write the helper ssh calls for a password, and return where it is.

        The script holds no secret: it prints the variable the call sets in the
        environment of ssh alone. It sits beside the store, which is where a
        script may run from even where ``/run`` and ``/dev/shm`` are ``noexec``.

        Returns:
            The script.
        """
        directory = secrets_dir().parent / "enroll"
        self.fs.make_dir(directory, mode=0o700, parents=True)
        path = directory / "askpass.sh"
        self.fs.write_text(
            path,
            "#!/bin/sh\n# Answers ssh's prompt from the environment of this one call.\n"
            "printf '%s\\n' \"$NOUST_ENROLL_SECRET\"\n",
            mode=0o700,
        )
        return path

    def _ssh(self, session: _Session, remote: Sequence[str]) -> tuple[list[str], dict[str, str]]:
        """
        Build one ssh call: its argv, and the environment that carries the secret.

        Every option is spelled here, none in a configuration file, and the host
        key is the one confirmed and no other.

        Args:
            session: The session.
            remote: The command to run on the server, as an argv.

        Returns:
            The ssh argv, and the environment to give the runner.
        """
        request = session.request
        credential = request.credential
        options = {
            "StrictHostKeyChecking": "yes",
            "UserKnownHostsFile": str(session.known_hosts),
            "GlobalKnownHostsFile": "/dev/null",
            "HostKeyAlias": enroll_alias(request.name),
            "CheckHostIP": "no",
            "UpdateHostKeys": "no",
            "IdentitiesOnly": "yes",
            "IdentityAgent": "none",
            "ForwardAgent": "no",
            "ForwardX11": "no",
            "PermitLocalCommand": "no",
            "NumberOfPasswordPrompts": "1",
            "ServerAliveInterval": str(SERVER_ALIVE_INTERVAL),
            "ServerAliveCountMax": str(SERVER_ALIVE_COUNT),
            "ConnectTimeout": str(CONNECT_TIMEOUT),
            "LogLevel": "ERROR",
            **session.algorithms,
        }
        env: dict[str, str] = {}
        if credential.private_key:
            options |= {
                "PreferredAuthentications": "publickey",
                "PasswordAuthentication": "no",
                "KbdInteractiveAuthentication": "no",
                "BatchMode": "no" if credential.secret else "yes",
            }
        else:
            options |= {
                "PreferredAuthentications": "password,keyboard-interactive",
                "PubkeyAuthentication": "no",
                "PasswordAuthentication": "yes",
                "KbdInteractiveAuthentication": "yes",
                "BatchMode": "no",
            }
        if session.askpass is not None and credential.secret:
            env = {
                "SSH_ASKPASS": str(session.askpass),
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": "noust-enroll",
                "NOUST_ENROLL_SECRET": credential.secret,
            }
        argv = ["ssh", "-T", "-F", "/dev/null", "-p", str(request.port), "-l", request.ssh_user]
        if session.identity is not None:
            argv += ["-i", str(session.identity)]
        for key, value in options.items():
            argv += ["-o", f"{key}={value}"]
        command = [*(["sudo", "-n"] if session.sudo else []), *remote]
        return [*argv, "--", request.host, shlex.join(command)], env

    def _remote(
        self,
        session: _Session,
        remote: Sequence[str],
        *,
        timeout: int = REMOTE_TIMEOUT,
        input_text: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """
        Run one command on the server.

        Args:
            session: The session.
            remote: The command, as an argv for the server's shell.
            timeout: Seconds it may take.
            input_text: Text for its standard input (not for a streamed command).
            on_line: Called with each line of its output while it runs.

        Returns:
            How it ended.

        Raises:
            NodeError: When ssh is not installed here.
        """
        argv, env = self._ssh(session, remote)
        if on_line is not None:
            result = self.runner.stream(
                argv,
                on_line=on_line,
                env=env,
                timeout=timeout,
                secrets=session.secret_values,
                clean_env=True,
            )
        else:
            result = self.runner.run(
                argv,
                env=env,
                timeout=timeout,
                input=input_text,
                secrets=session.secret_values,
                clean_env=True,
            )
        if result.exit_code == EXIT_NOT_FOUND and "ssh" in result.stderr.lower():
            raise NodeError(
                "ssh is not installed on this central",
                details="Install the OpenSSH client (openssh-client), then try again.",
            )
        return result

    # -- The steps ---------------------------------------------------------

    def _probe(self, session: _Session, log: Log) -> str | None:
        """
        Connect, learn who the credential is, and whether Noust is there.

        Args:
            session: The session.
            log: Where to say what happens.

        Returns:
            The server's Noust version, or None when it is not installed.

        Raises:
            NodeError: When the connection or the credential fails, the account
                cannot act as root, or the Noust there is too old.
        """
        request = session.request
        log(f"Connecting to {request.ssh_user}@{request.host}:{request.port}", "info")
        script = (
            "id -u; if command -v noust >/dev/null 2>&1; then noust --version; "
            f"else echo {MISSING}; fi"
        )
        result = self._remote(session, ["sh", "-c", script])
        if not result.success:
            raise NodeError(
                f"Could not connect to {request.host}:{request.port} as {request.ssh_user}",
                details=(
                    f"ssh did not finish within {REMOTE_TIMEOUT} seconds. Check the address, the "
                    "port and the server's firewall."
                    if result.timed_out
                    else _explain(result.stderr or result.stdout)
                ),
            )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) < 2 or not lines[0].isdigit():
            raise NodeError(
                f"{request.host} answered, but not as a Linux shell",
                details=f"It printed: {_tail(result.stdout)}",
            )
        if lines[0] != "0":
            self._require_sudo(session)
        log("Connected", "success")
        if lines[1] == MISSING:
            return None
        match = _VERSION.search(lines[1])
        if match is None:
            raise NodeError(
                "The noust on the server did not say its version",
                details=f"It printed: {_tail(lines[1])}",
            )
        found = tuple(int(part) for part in match.groups())
        if found < MINIMUM_NODE_VERSION:
            raise NodeError(
                f"Noust {'.'.join(map(str, found))} on the server is too old to be a node",
                details=(
                    "A central reaches a server through the restricted tunnel account that "
                    f"Noust {'.'.join(map(str, MINIMUM_NODE_VERSION))} and later create. "
                    "Update Noust there first (noust update, or the installer), then enroll."
                ),
            )
        return ".".join(map(str, found))

    def _require_sudo(self, session: _Session) -> None:
        """
        Accept an account that is not root only if it has passwordless sudo.

        Args:
            session: The session; ``sudo`` is set on success.

        Raises:
            NodeError: When it does not.
        """
        session.sudo = True
        result = self._remote(session, ["true"])
        if not result.success:
            session.sudo = False
            raise NodeError(
                f"{session.request.ssh_user} is not root and has no passwordless sudo",
                details=(
                    f"{_tail(result.stderr)}\nInstalling Noust and authorizing a central needs "
                    "root. Use root (PermitRootLogin prohibit-password with a key works), or give "
                    f"{session.request.ssh_user} NOPASSWD sudo, or enroll the server the manual way "
                    "(docs/CENTRAL.md)."
                ).strip(),
            )

    def _installer_text(self) -> str:
        """
        Read this central's copy of the installer.

        Returns:
            Its text.

        Raises:
            NodeError: When this central has none.
        """
        path = self._installer or find_installer()
        if path is None:
            raise NodeError(
                "Noust is not on the server, and this central has no installer to put it there",
                details=(
                    "A central installed from the deb or rpm package carries one "
                    "(/usr/share/noust/install.sh). Install Noust on the server yourself "
                    "(see the install section of the README: download install.sh, check its "
                    "SHA256SUMS, run it), then enroll the server again."
                ),
            )
        return path.read_text(encoding="utf-8")

    def _install(self, session: _Session, log: Log) -> str:
        """
        Put Noust on the server with this central's own copy of the installer.

        The script is sent over the connection, written where only root can read
        it, run and removed; the server downloads no script. The console is left to
        ``noust fleet authorize``, which makes it a service on loopback.

        Args:
            session: The session.
            log: Where to say what happens, and the installer's output.

        Returns:
            The version installed.

        Raises:
            NodeError: When installing is not allowed, there is no installer, or
                the installer fails (its own output is in the details).
        """
        if not session.request.install:
            raise NodeError(
                "Noust is not installed on the server",
                details="Install it (install.sh from the release, checked against its "
                "SHA256SUMS) or enroll again without --no-install.",
            )
        text = self._installer_text()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        remote_path = f"/run/noust-install-{token_source.token_hex(6)}.sh"
        log(
            f"Installing Noust on the server with this central's installer (sha256 {digest[:16]})",
            "info",
        )
        uploaded = self._remote(
            session, ["sh", "-c", f"umask 077 && cat > {remote_path}"], input_text=text
        )
        if not uploaded.success:
            raise NodeError(
                "The installer could not be put on the server",
                details=_explain(uploaded.stderr or uploaded.stdout),
            )
        lines: list[str] = []

        def relay(line: str) -> None:
            lines.append(line)
            log(line, "info")

        try:
            result = self._remote(
                session,
                ["sh", remote_path, "--yes", "--no-console"],
                timeout=INSTALL_TIMEOUT,
                on_line=relay,
            )
        finally:
            self._remote(session, ["rm", "-f", remote_path])
        if not result.success:
            raise NodeError(
                f"The installer failed on the server (exit {result.exit_code})",
                details="\n".join(lines[-30:]) or _tail(result.stderr),
            )
        version = self._probe(session, log)
        if version is None:
            raise NodeError(
                "The installer finished and noust is still not on the server's PATH",
                details="\n".join(lines[-15:]),
            )
        return version

    def _authorize(self, session: _Session, central: str, central_key: str, log: Log) -> str:
        """
        Run ``noust fleet authorize`` on the server and take the join code.

        The output holds the join code (a token) and, on a server that never had
        a console token, that token: it is parsed here and not logged, and the
        console token is dropped. A server is managed through its central; its own
        token can be issued again with ``noust web token --new``.

        Args:
            session: The session.
            central: This central's name.
            central_key: Its public key for this server.
            log: Where to say what happens.

        Returns:
            The join code.

        Raises:
            NodeError: When authorize fails or prints nothing usable.
        """
        request = session.request
        log(f"Authorizing this central on {request.name} (access {request.access})", "info")
        command = [
            "noust",
            "fleet",
            "authorize",
            "--central-key",
            central_key,
            "--name",
            central,
            "--access",
            request.access,
            "--allow-host-access" if request.host_access else "--no-host-access",
            "--json",
        ]
        if request.replace_token:
            command.append("--yes")
        result = self._remote(session, command, timeout=AUTHORIZE_TIMEOUT)
        if not result.success:
            combined = "\n".join(
                part.strip() for part in (result.stdout, result.stderr) if part.strip()
            )
            if "Revoke it and issue a new token" in combined:
                raise NodeError(
                    f"The server already holds a token for the central {central}",
                    details="Enroll again allowing it to be replaced (--replace-token): it is "
                    "revoked and a new one issued.",
                )
            if (
                "console runs in the background" in combined
                or "Stop it and run it as a service" in combined
            ):
                raise NodeError(
                    "The server runs its console in the background",
                    details="Stop it there (noust web stop) or enroll again with --replace-token, "
                    "which lets authorize move it to a service on the same port.",
                )
            raise NodeError(
                f"noust fleet authorize failed on the server (exit {result.exit_code})",
                details=_tail(combined),
            )
        payload = _json_of(result.stdout)
        join_code = payload.get("join_code") if payload else None
        if not isinstance(join_code, str) or not join_code.startswith("noust-join:"):
            raise NodeError(
                "noust fleet authorize did not print a join code",
                details="Its output was not what this version of Noust prints with --json. "
                "Is the server's Noust older or newer than this central's?",
            )
        for warning in payload.get("warnings", []) if payload else []:
            log(str(warning), "warning")
        log("The server authorized this central", "success")
        return join_code

    def _retire_key(self, session: _Session, public_key: str, log: Log) -> None:
        """
        Take a one-time login key out of the account's ``authorized_keys``.

        The key's private half is gone with the call, so what stays behind is a
        line nobody can use; it is removed anyway, so that a server the central
        created has in its ``authorized_keys`` only what its operator put there.
        Failing to remove it is a warning, never a failure of the enrollment.

        Args:
            session: The session, still open.
            public_key: ``ssh-ed25519 AAAA...``; lines holding its blob go.
            log: Where to say what happens.
        """
        blob = parse_public_key(public_key, what="one-time login key").blob
        log("Removing the one-time login key from the server", "info")
        script = (
            'f="$(getent passwd "$1" | cut -d: -f6)/.ssh/authorized_keys"; '
            '[ -f "$f" ] || exit 0; '
            'tmp="$(mktemp)" && { grep -vF -- "$2" "$f" > "$tmp"; [ $? -le 1 ] && cat "$tmp" > "$f"; '
            'status=$?; rm -f "$tmp"; exit $status; }'
        )
        try:
            result = self._remote(
                session, ["sh", "-c", script, "sh", session.request.ssh_user, blob]
            )
        except NodeError as exc:
            log(f"The one-time login key could not be removed: {exc.message}", "warning")
            return
        if not result.success:
            log(
                "The one-time login key is still in the server's authorized_keys (its private "
                f"half is gone, so it opens nothing); remove the line holding {blob[:24]}... "
                "by hand.",
                "warning",
            )

    def _withdraw(self, session: _Session, central: str, log: Log) -> None:
        """
        Take back the authorization this call made, after a failure past it.

        Args:
            session: The session.
            central: This central's name.
            log: Where to say what happens.
        """
        log(f"Withdrawing the authorization of {central} on the server", "warning")
        try:
            result = self._remote(session, ["noust", "fleet", "deauthorize", "--name", central])
        except NodeError as exc:
            logger.warning("Could not withdraw the authorization: %s", exc.message)
            log(f"It could not be withdrawn: {exc.message}", "warning")
            return
        if not result.success:
            log(
                "It could not be withdrawn; run on the server: "
                f"noust fleet deauthorize --name {central}",
                "warning",
            )


def _json_of(output: str) -> dict[str, Any] | None:
    """
    Read the JSON object a command printed last.

    Args:
        output: Its standard output; prompts or notes may come before the object.

    Returns:
        The object, or None when there is none.
    """
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None
