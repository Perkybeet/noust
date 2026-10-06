# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server that a database can be made public on, without a process or a socket.

:class:`PublicHostRunner` stands in for the machine: ``ufw`` keeps its rules,
``systemctl enable --now`` opens the socket's port, and ``ss`` lists what is
open, so the order the real thing is done in shows (the port is not taken
before it is opened, a rule is there after it was added, both are gone after
they are removed). :func:`public_engine` builds an engine whose accounts, TLS
requirement and databases are recorded in a dict.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from noust.core.exceptions import DatabaseUserError
from noust.core.runner import CommandResult, FakeRunner
from noust.managers.database.base import BaseDatabaseManager
from tests.test_web_databases_api import make_engine

#: What ``ufw status verbose`` prints.
UFW_ACTIVE = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip
"""

UFW_INACTIVE = "Status: inactive\n"

#: The address ``ip`` reports.
PUBLIC_IP = "203.0.113.9"


class PublicHostRunner(FakeRunner):
    """
    A runner that keeps a firewall and a set of open sockets.

    Attributes:
        ufw: ``active``, ``inactive`` or ``absent``.
        rules: The ``ufw`` rules added so far, as ``show added`` prints them.
        sockets: Ports opened by ``systemctl enable --now <unit>.socket``.
        fail: argv prefixes that fail with the given stderr.
        base_ports: What listens before anything is opened: sshd and the engine.
    """

    def __init__(self, *, ufw: str = "active") -> None:
        super().__init__()
        self.ufw = ufw
        self.rules: list[str] = ["ufw allow 22/tcp"]
        self.sockets: dict[str, int] = {}
        self.fail: dict[tuple[str, ...], str] = {}
        #: False: systemd says yes and nothing holds the port.
        self.open_sockets = True
        self.base_ports = {22: "sshd", 5432: "postgres"}
        self.only_knows("systemctl", "ss", "ip", *(["ufw"] if ufw != "absent" else []))

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        command = tuple(argv)
        for prefix, error in self.fail.items():
            if command[: len(prefix)] == prefix:
                return CommandResult(argv=result.argv, exit_code=1, stderr=error)
        if command[:1] == ("ss",):
            return CommandResult(argv=result.argv, exit_code=0, stdout=self._ss())
        if command[:2] == ("ip", "-4"):
            return CommandResult(
                argv=result.argv,
                exit_code=0,
                stdout=f"2: eth0    inet {PUBLIC_IP}/24 brd 203.0.113.255 scope global eth0\n",
            )
        if command[:1] == ("ufw",):
            return self._ufw(command, result)
        if command[:1] == ("systemctl",):
            self._systemctl(command)
        return result

    def _ss(self) -> str:
        """
        Returns:
            What ``ss -ltnpH`` prints.
        """
        lines = [
            f'LISTEN 0 128 0.0.0.0:{port} 0.0.0.0:* users:(("{process}",pid=900,fd=3))'
            for port, process in self.base_ports.items()
            if port != 5432
        ]
        lines.append('LISTEN 0 244 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=812,fd=6))')
        lines += [
            f'LISTEN 0 128 0.0.0.0:{port} 0.0.0.0:* users:(("systemd",pid=1,fd=40))'
            for port in sorted(self.sockets.values())
        ]
        return "\n".join(lines) + "\n"

    def _systemctl(self, command: tuple[str, ...]) -> None:
        """
        Open or close a socket's port.

        Args:
            command: The systemctl command.
        """
        unit = next((part for part in command if part.endswith(".socket")), None)
        if unit is None:
            return
        port = re.search(r"-(\d+)\.socket$", unit)
        if port is None:
            return
        if command[1] == "enable" and "--now" in command:
            if self.open_sockets:
                self.sockets[unit] = int(port.group(1))
        elif command[1] == "disable":
            self.sockets.pop(unit, None)

    def _ufw(self, command: tuple[str, ...], result: CommandResult) -> CommandResult:
        """
        Answer a ufw command from the rules kept.

        Args:
            command: The ufw command.
            result: What the base class scripted.

        Returns:
            The answer.
        """
        if command[1:3] == ("status", "verbose"):
            text = UFW_ACTIVE if self.ufw == "active" else UFW_INACTIVE
            return CommandResult(argv=result.argv, exit_code=0, stdout=text)
        if command[1:3] == ("show", "added"):
            text = "Added user rules (see 'ufw status' for running firewall):\n" + "\n".join(
                self.rules
            )
            return CommandResult(argv=result.argv, exit_code=0, stdout=text + "\n")
        if command[1] == "allow":
            self.rules.append(shlex.join(command))
        elif command[1] == "delete":
            wanted = shlex.join(("ufw", "allow", *command[3:]))
            self.rules = [rule for rule in self.rules if not _same_without_comment(rule, wanted)]
        return result


def _same_without_comment(rule: str, wanted: str) -> bool:
    """
    Compare a stored ufw rule with a delete command's, ignoring the comment.

    Args:
        rule: A line of ``ufw show added``.
        wanted: ``ufw allow ...`` as the delete spells it.

    Returns:
        True when they are the same rule.
    """
    stored = rule.split(" comment ")[0]
    return stored == wanted


def public_engine(
    tmp_path: Path, state: dict[str, Any], *, tls_blocker: str | None = None
) -> type[BaseDatabaseManager]:
    """
    Build a PostgreSQL-like engine whose accounts and TLS requirements are recorded.

    Args:
        tmp_path: Where its dumps would go.
        state: The dict the engine records into: ``accounts``, ``tls``,
            ``profiles`` and ``calls``.
        tls_blocker: Why it cannot require TLS; None when it can.

    Returns:
        The manager class.
    """
    base = make_engine(tmp_path / "dumps")

    class PublicEngine(base):  # type: ignore[valid-type, misc]
        def server_port(self) -> int:
            return 5432

        def service_unit(self) -> str:
            return "postgresql.service"

        def validate_user_name(self, username: str) -> str:
            return username

        def remote_tls_blocker(self) -> str | None:
            return tls_blocker

        def remote_access_blockers(self) -> list[str]:
            return list(state.get("blockers", []))

        def set_remote_tls(
            self, username: str, database: str, required: bool, host: str = "localhost"
        ) -> None:
            state["calls"].append(("set_remote_tls", username, database, required))
            if state.get("tls_fails") and required:
                raise DatabaseUserError("PostgreSQL refused the line", details="pg_hba error")
            state["tls"][username] = required

        def apply_profile(
            self, username: str, database: str, profile: str, host: str = "localhost"
        ) -> None:
            state["profiles"][username] = (database, profile)

        def create_user(self, username, password=None, host="localhost", **kwargs):  # type: ignore[no-untyped-def]
            state["calls"].append(("create_user", username))
            state["accounts"].add(username)
            return super().create_user(username, password, host, **kwargs)

        def drop_user(self, username, host="localhost"):  # type: ignore[no-untyped-def]
            state["calls"].append(("drop_user", username))
            state["accounts"].discard(username)
            state["tls"].pop(username, None)
            return super().drop_user(username, host)

        def user_exists(self, username, host="localhost"):  # type: ignore[no-untyped-def]
            return username in state["accounts"]

    PublicEngine.state = {"installed": True, "running": True}
    return PublicEngine


def fresh_state() -> dict[str, Any]:
    """
    Returns:
        An empty record for :func:`public_engine`.
    """
    return {"accounts": set(), "tls": {}, "profiles": {}, "calls": [], "blockers": []}
