# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shared pieces of the push-deploy tests: a server's sshd, accounts and git, inside the test's directory.

A server that takes ``git push`` has an sshd, an account (``noust-git``) with ``git-shell`` for a
shell, a directory of repositories, a key file sshd reads, and repositories git makes. The rig
stands for all of it with real files and a runner that answers ``sshd -T``, ``useradd`` and the
few git commands the code asks, so what is asserted is what ends up on disk and what was run.
"""

from __future__ import annotations

import base64
import pwd
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import App, NoustStore
from noust.fleet.authorize import AuthorizedKeys, tunnel_keys_file
from noust.pushdeploy.host import PUSH_DROPIN_NAME, PUSH_USER, PushHost
from noust.pushdeploy.keys import COMMENT_PREFIX, PushKeys
from noust.pushdeploy.repos import PushRepos

DOMAIN = "shop.example.com"
OTHER = "blog.example.com"
GIT_SHELL = "/usr/bin/git-shell"
PUSH_UID = 997

SSHD_T = """port 22
allowtcpforwarding yes
disableforwarding no
permitrootlogin without-password
authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2
"""


def _blob(*parts: bytes) -> str:
    """
    Args:
        *parts: The strings of an SSH public key blob.

    Returns:
        The base64 of them, each length-prefixed.
    """
    raw = b"".join(struct.pack(">I", len(part)) + part for part in parts)
    return base64.b64encode(raw).decode("ascii")


def ed25519(seed: int, comment: str = "") -> str:
    """
    Args:
        seed: A byte that makes the key different from another.
        comment: The line's comment.

    Returns:
        A well-formed ssh-ed25519 public key line.
    """
    return " ".join(
        part for part in ("ssh-ed25519", _blob(b"ssh-ed25519", bytes([seed]) * 32), comment) if part
    )


def rsa(bits: int, seed: int = 1, comment: str = "") -> str:
    """
    Args:
        bits: The modulus size.
        seed: A byte that makes the key different from another.
        comment: The line's comment.

    Returns:
        A well-formed ssh-rsa public key line of that many bits.
    """
    modulus = b"\x00" + bytes([0x80 | seed]) * (bits // 8)
    return " ".join(
        part for part in ("ssh-rsa", _blob(b"ssh-rsa", b"\x01\x00\x01", modulus), comment) if part
    )


def ecdsa(seed: int = 1) -> str:
    """
    Returns:
        A well-formed ecdsa-sha2-nistp256 public key line.
    """
    return "ecdsa-sha2-nistp256 " + _blob(
        b"ecdsa-sha2-nistp256", b"nistp256", b"\x04" + bytes([seed]) * 64
    )


class PushRunner(FakeRunner):
    """
    sshd, useradd, chown and git as a server that takes pushes has them.

    ``sshd -T`` prints :attr:`config`; evaluated for the push account, the settings of Noust's
    ``Match`` block come first, as sshd applies them unless :attr:`ignore_policy` says another
    block wins. ``useradd`` adds the account, ``git init --bare`` makes a repository (its
    ``HEAD`` and a sample hook), and the reads of a repository answer from :attr:`branches`.
    """

    def __init__(self, world: PushWorld) -> None:
        super().__init__()
        self.world = world
        self.config = SSHD_T
        self.ignore_policy = False
        #: Branch tips by repository path: what ``rev-parse`` of a branch answers.
        self.branches: dict[str, dict[str, str]] = {}
        #: Tags by repository path.
        self.tags: dict[str, list[str]] = {}
        #: The commit a cache is at after a clone or a reset.
        self.cache_head = "c" * 40
        self.script(["systemctl", "is-active", "ssh.service"], stdout="active\n")

    def _scripted_for(self, args: list[str]) -> bool:
        return any(tuple(args[: len(s.match)]) == s.match for s in self._scripted)

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        check = kwargs.pop("check", False)
        cwd = kwargs.get("cwd")
        result = super().run(argv, **kwargs)
        args = [str(part) for part in argv]
        if args[:2] == ["sshd", "-T"] and not self._scripted_for(args):
            output = self.config
            if "-C" in args and f"user={PUSH_USER}," in args[args.index("-C") + 1]:
                if not self.ignore_policy:
                    output = self.world.policy_lines() + output
            result = replace(result, stdout=output)
        elif args and args[0] == "useradd" and result.success:
            home = args[args.index("--home-dir") + 1]
            self.world.add_account(args[-1], login_shell=args[args.index("--shell") + 1], home=home)
        elif args and args[0] == "git" and result.success:
            result = self._git(args, cwd, result)
        return result.check() if check else result

    def _git(self, args: list[str], cwd: Path | None, result: CommandResult) -> CommandResult:
        """
        Answer the git commands of the code under test, with files where git would leave some.
        """
        if "init" in args and "--bare" in args:
            repository = Path(args[-1])
            (repository / "hooks").mkdir(parents=True)
            (repository / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\n")
            (repository / "HEAD").write_text("ref: refs/heads/master\n")
        elif "symbolic-ref" in args:
            repository = Path(args[args.index("--git-dir") + 1])
            (repository / "HEAD").write_text(f"ref: {args[-1]}\n")
        elif "rev-parse" in args and "--verify" in args:
            repository = args[args.index("--git-dir") + 1]
            wanted = args[-1].removeprefix("refs/heads/").removesuffix("^{commit}")
            commit = self.branches.get(repository, {}).get(wanted)
            result = replace(result, stdout=(commit or "") + "\n", exit_code=0 if commit else 1)
        elif "rev-parse" in args and args[-1] == "HEAD":
            result = replace(result, stdout=self.cache_head + "\n")
        elif "for-each-ref" in args:
            repository = args[args.index("--git-dir") + 1]
            result = replace(result, stdout="\n".join(self.tags.get(repository, [])))
        elif "clone" in args:
            cache = Path(args[-1])
            (cache / ".git").mkdir(parents=True)
            self.world.origins[str(cache)] = args[-2]
        elif "get-url" in args and cwd is not None:
            origin = self.world.origins.get(str(cwd))
            result = replace(result, stdout=(origin or "") + "\n", exit_code=0 if origin else 1)
        return result


class PushWorld:
    """A server's sshd, accounts and directories, inside the test's directory."""

    def __init__(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp_path = tmp_path
        self.runner = PushRunner(self)
        self.dropin_dir = tmp_path / "sshd_config.d"
        self.dropin_dir.mkdir()
        self.sshd_config = tmp_path / "sshd_config"
        self.sshd_config.write_text(f"Include {self.dropin_dir}/*.conf\nPort 22\n")
        self.keys_dir = tmp_path / "etc-ssh-noust"
        self.home = tmp_path / "git"
        self.apps = tmp_path / "apps"
        self.accounts: dict[str, tuple[int, str, str]] = {"root": (0, "/root", "/bin/bash")}
        self.origins: dict[str, str] = {}

    def add_account(self, user: str, *, login_shell: str, home: str) -> None:
        uid = PUSH_UID if user == PUSH_USER else 999
        self.accounts[user] = (uid, home, login_shell)

    def passwd(self, user: str) -> pwd.struct_passwd:
        if user not in self.accounts:
            raise KeyError(user)
        uid, home, shell = self.accounts[user]
        return pwd.struct_passwd((user, "x", uid, uid, "", home, shell))

    @property
    def dropin(self) -> Path:
        return self.dropin_dir / PUSH_DROPIN_NAME

    @property
    def keys_file(self) -> Path:
        return self.keys_dir / f"{PUSH_USER}.keys"

    def policy_lines(self) -> str:
        """What ``sshd -T -C`` prints for the push account from Noust's block, if it is there."""
        if self.dropin.exists():
            text = self.dropin.read_text()
        elif self.sshd_config.exists():
            text = self.sshd_config.read_text()
        else:
            return ""
        lines: list[str] = []
        inside = False
        for raw in text.splitlines():
            if raw.startswith(f"Match User {PUSH_USER}"):
                inside = True
                continue
            if inside and raw.startswith("    "):
                keyword, _, value = raw.strip().partition(" ")
                lines.append(f"{keyword.lower()} {value}")
            elif inside:
                break
        return "\n".join(lines) + "\n" if lines else ""

    def host(self) -> PushHost:
        return PushHost(
            runner=self.runner,
            passwd=self.passwd,
            sshd_config=self.sshd_config,
            dropin_dir=self.dropin_dir,
            keys_dir=self.keys_dir,
            home=self.home,
        )

    def keys(self) -> PushKeys:
        editor: AuthorizedKeys = tunnel_keys_file(
            PUSH_USER, keys_dir=self.keys_dir, runner=self.runner, comment_prefix=COMMENT_PREFIX
        )
        return PushKeys(PUSH_USER, self.keys_dir, editor=editor)


@dataclass
class PushRig:
    """Everything a test of the push deployments needs."""

    world: PushWorld
    store: NoustStore
    repos: PushRepos

    @property
    def runner(self) -> PushRunner:
        return self.world.runner

    def app(self, domain: str = DOMAIN, **fields: Any) -> App:
        """
        Make an application on the release layout.

        Args:
            domain: Its domain.
            **fields: Fields of the row to override.

        Returns:
            The stored application.
        """
        values: dict[str, Any] = {
            "domain": domain,
            "app_type": "nodejs",
            "source": "https://github.com/acme/shop.git",
            "app_path": str(self.world.apps / domain.replace(".", "-")),
            "layout": "releases",
            "branch": None,
        }
        values.update(fields)
        return self.store.create_app(App(**values))

    def set_up(self) -> None:
        """Run ``noust push setup`` on the rig's server."""
        self.world.host().setup()


def build_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, console: Callable[[], str] | None = None
) -> Iterator[PushRig]:
    """
    Build a rig: a store, a server with sshd and a console on a loopback port.

    Args:
        tmp_path: The test's directory.
        monkeypatch: Pytest's patcher, for the pieces that name system paths.
        console: Where the hook reaches the console.

    Yields:
        The rig.
    """
    world = PushWorld(tmp_path / "server")
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr("noust.pushdeploy.host.git_shell", lambda: GIT_SHELL)
    monkeypatch.setattr("noust.pushdeploy.host.GIT_SHELL_CANDIDATES", (GIT_SHELL,))
    monkeypatch.setattr("noust.core.paths.PUSH_DIR", world.home)
    host = world.host()
    repos = PushRepos(
        runner=world.runner,
        store=store,
        host=host,
        keys=world.keys(),
        console_url=console or (lambda: "http://127.0.0.1:8080"),
        python="/usr/bin/python3",
        actor="alice",
    )
    yield PushRig(world=world, store=store, repos=repos)
    NoustStore.reset_instance()
