# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_fleet_authorize.py`` that other tests build on."""

from __future__ import annotations

import pwd
from dataclasses import replace
from pathlib import Path
from typing import Any

from noust.core.fs import RecordingFileSystem
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import NoustStore
from noust.fleet.authorize import SSHD_DROPIN_NAME, TUNNEL_USER, authorize, deauthorize
from noust.web.auth import TokenManager
from tests.support.fleet_support import HOST_KEY, ed25519_line

CENTRAL_KEY = ed25519_line(7, "noust-central@nas")

SSHD_T = """port 22
allowtcpforwarding yes
disableforwarding no
permitrootlogin without-password
authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2
"""


class SshdRunner(FakeRunner):
    """
    sshd, systemctl and useradd as a node has them.

    ``sshd -T`` prints :attr:`config`; evaluated for the tunnel account, the
    settings of Noust's ``Match`` block come first, as sshd applies them -
    unless :attr:`ignore_policy` says another block wins. ``useradd`` adds
    the account to the node. Anything a test scripts wins over both.
    """

    def __init__(self, node: Node) -> None:
        super().__init__()
        self.node = node
        self.config = SSHD_T
        self.ignore_policy = False
        self.script(["systemctl", "is-active", "ssh.service"], stdout="active\n")

    def _scripted_for(self, argv: list[str]) -> bool:
        return any(tuple(argv[: len(s.match)]) == s.match for s in self._scripted)

    def run(self, argv, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        check = kwargs.pop("check", False)
        result = super().run(argv, **kwargs)
        args = [str(a) for a in argv]
        if args[:2] == ["sshd", "-T"] and not self._scripted_for(args):
            output = self.config
            if "-C" in args and f"user={TUNNEL_USER}," in args[args.index("-C") + 1]:
                if not self.ignore_policy:
                    output = self.node.policy_lines() + output
            result = replace(result, stdout=output)
        if args and args[0] == "useradd" and result.success:
            self.node.add_account(args[-1], login=args[args.index("--shell") + 1])
        return result.check() if check else result


class Node:
    """A node's accounts, sshd and host key, inside the test's directory."""

    def __init__(self, tmp_path: Path, tokens: TokenManager) -> None:
        self.tmp_path = tmp_path
        self.tokens = tokens
        self.runner = SshdRunner(self)
        self.host_key_file = tmp_path / "ssh_host_ed25519_key.pub"
        self.host_key_file.write_text(HOST_KEY + " root@node\n")
        self.dropin_dir = tmp_path / "sshd_config.d"
        self.dropin_dir.mkdir()
        self.sshd_config = tmp_path / "sshd_config"
        self.sshd_config.write_text(f"Include {self.dropin_dir}/*.conf\nPort 22\n")
        self.tunnel_keys_dir = tmp_path / "etc-ssh-noust"
        self.homes: dict[str, tuple[int, Path, str]] = {
            "root": (0, tmp_path / "root", "/bin/bash"),
            "deploy": (1001, tmp_path / "home" / "deploy", "/bin/bash"),
        }
        for _, home, _ in self.homes.values():
            home.mkdir(parents=True)
        self.console_calls = 0
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())

    def add_account(self, user: str, *, login: str = "/usr/sbin/nologin") -> None:
        self.homes[user] = (999, Path("/nonexistent"), login)

    def passwd(self, user: str) -> pwd.struct_passwd:
        if user not in self.homes:
            raise KeyError(user)
        uid, home, shell = self.homes[user]
        return pwd.struct_passwd((user, "x", uid, uid, "", str(home), shell))

    def keys_file(self, user: str = TUNNEL_USER) -> Path:
        if user == TUNNEL_USER:
            return self.tunnel_keys_dir / f"{TUNNEL_USER}.keys"
        return self.homes[user][1] / ".ssh" / "authorized_keys"

    @property
    def dropin(self) -> Path:
        return self.dropin_dir / SSHD_DROPIN_NAME

    def policy_lines(self) -> str:
        """What sshd -T -C prints for the tunnel account from Noust's block, if it is there."""
        if self.dropin.exists():
            text = self.dropin.read_text()
        elif self.sshd_config.exists():
            text = self.sshd_config.read_text()
        else:
            return ""
        lines: list[str] = []
        inside = False
        for raw in text.splitlines():
            if raw.startswith(f"Match User {TUNNEL_USER}"):
                inside = True
                continue
            if inside and raw.startswith("    "):
                keyword, _, value = raw.strip().partition(" ")
                lines.append(f"{keyword.lower()} {value}")
            elif inside:
                break
        return "\n".join(lines) + "\n" if lines else ""

    def console(self) -> int:
        self.console_calls += 1
        return 8080

    def arguments(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "central_key": CENTRAL_KEY,
            "central": "nas",
            "tokens": self.tokens,
            "ensure_console": self.console,
            "confirm_replace": lambda names: True,
            "runner": self.runner,
            "host_key_file": self.host_key_file,
            "sshd_config": self.sshd_config,
            "sshd_dropin_dir": self.dropin_dir,
            "tunnel_keys_dir": self.tunnel_keys_dir,
            "passwd": self.passwd,
            "store": self.store,
            "self_check": None,
        }
        values.update(overrides)
        return values

    def authorize(self, key: str = CENTRAL_KEY, *, user: str = TUNNEL_USER, confirm=None, **kw):
        return authorize(
            **self.arguments(
                central_key=key,
                ssh_user=user,
                allow_root=user == "root",
                confirm_replace=confirm or (lambda names: True),
                **kw,
            )
        )

    def deauthorize(self, *, user: str | None = None, dry_run: bool = False):
        return deauthorize(
            central="nas",
            tokens=self.tokens,
            ssh_user=user,
            runner=self.runner,
            sshd_config=self.sshd_config,
            tunnel_keys_dir=self.tunnel_keys_dir,
            passwd=self.passwd,
            dry_run=dry_run,
        )
