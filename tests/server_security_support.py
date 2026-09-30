# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake server for the security tests: account files, key files, and an sshd.

:class:`FakeHost` builds a root tree (``/etc/passwd``, ``/etc/group``,
``/etc/shadow``, sudoers, sshd_config and ``authorized_keys`` files) under a
temporary directory, which :class:`~noust.managers.server.host.HostPaths`
points at. :class:`FakeSshd` is a :class:`~noust.core.runner.FakeRunner` that
answers ``sshd -T`` from what is on that disk - Noust's drop-in first, then
whatever the test says the rest of the configuration holds - so a test sees
the effective value change when the code under test writes the file, exactly
as on a real server, and stay put when a line earlier in the main file wins.

Key vectors were produced by ``ssh-keygen`` and their fingerprints checked with
``ssh-keygen -lf``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from noust.core.runner import CommandResult, FakeRunner
from noust.managers.server.host import HostPaths
from noust.managers.server.security_sshd import DROPIN, parse_dropin

#: A fixed "now" for every test: 2026-09-29 12:00:00 UTC.
NOW = 1790683200.0

ED_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDSBGPE4doRECSNVCitbxTexPRhLY2lUbsj1bJ/pa/Qm yago@laptop"
)
ED_FP = "SHA256:kfD2AdBlUHXDDbH0i9K8P/8/laEpPUY8FB2xVXqkJC8"

ALICE_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO8wIRSD/BaONDTwXh1fi1KOPEu6fjcK2id6NjqmeQFH alice@home"
)
ALICE_FP = "SHA256:5rxBUyN8yS4Tl7QcRWMEHtZbl+EqkNHMt0dJCLr/1qw"

CENTRAL_KEY_BARE = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPwKIFR3dT7vlHhRLtmueMukr//W1rW3muyJ8RmejKcC"
)
CENTRAL_FP = "SHA256:4yDNqv1O3P66iRait+oMWrVGjJ40Le05sg2tu2RmSj0"
CENTRAL_LINE = (
    'restrict,port-forwarding,permitopen="127.0.0.1:8080",permitlisten="127.0.0.1:1",'
    f'command="/usr/bin/false" {CENTRAL_KEY_BARE} noust-central:hub-1'
)

RSA_1024 = (
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQDY3TQORvzx4mWd/XLU/8KaKDUF76LAt6kkvlIwErGLeZGFx54NiEhC1oFa"
    "3Btr4pRsSZlFGx6nIDt/tQ8xSwPpw+jMNPCQSoZk5KZksfrKL+KqnQdpNKLmNQ26qgqDqvv62mWflO+ShuIaHqW9bOPQzGEZ"
    "KHYXY0f19oWbjSBpfQ== old@box"
)
RSA_1024_FP = "SHA256:IrL2G3WT61yfrJNJGcs9yeGJVI1oe33wpMbLe+Ush2o"

RSA_3072 = (
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDv7duA7M+UjO5+gu1xfoobKlvg3r3xT47UBhHhE54ZHuWwbYyNR2FV7I49"
    "MkGQhpJJgobQJ75dq7p8xt26X3WK+ZZD5GVD3oqtTQodJ7BnZceGARacymxDWCMFPMVS0y7xZNf5BhhprZ+A+0CgkYGIgBQV"
    "RjEl/z4uo1i8J4mmiegsB0t3AvOmYBEhKee61H71pZlNdRbzrsrUAwHf+J06Fai9CLrWRojermyccNJmNbWH2mrEMo4A6IZh"
    "1QrmRHkKrl6pCjdFnNG1C/ldHwLVpopCNP+VMdn27HPXF2zA3w1XgxLnmlQcPrvoHcYhVBGWuTNSI65fJrXnMRANxpDcG4D2"
    "1y1PE2Ik/kMMphSzOzBxxU6UqtzCh0N4jqV8HugkGRMcE+hMWZzVuSIpwaM2OXgzNxQjfUaSdmYzG7HUAyEff2FldyT/R4m8"
    "Q3K2AmEMe1GBZ4MJss4NDxpAqiaGD6KDQw7kV2oxN6KSe6QdyGMzX6S9OGRNw4y2llVnOfk= ops@work"
)
RSA_3072_FP = "SHA256:P/lEYbcfDnxuBf5K8/2ekALwy2NWbPYVRrr6KFFZm9o"

ECDSA_KEY = (
    "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBLM/hzu0OMLTVcNOe+Dd13vO"
    "VqkVMQub4jUrrQr7GPQAo2NXlEkci3p6g9/aZJhZWZqpKHg4AOVDrrF/rYj5Fqg= ec@x"
)
ECDSA_FP = "SHA256:HGeDy6wVstj149rHRYZ/vaMVlq1TiH1P7wsB7dKNmns"

#: sshd's defaults, as ``sshd -T`` prints them, for what the tests read.
SSHD_DEFAULTS: dict[str, str] = {
    "port": "22",
    "permitrootlogin": "prohibit-password",
    "passwordauthentication": "yes",
    "kbdinteractiveauthentication": "no",
    "usepam": "yes",
    "permitemptypasswords": "no",
    "pubkeyauthentication": "yes",
    "authorizedkeysfile": ".ssh/authorized_keys .ssh/authorized_keys2",
    # Off by default in the fake: the temporary tree belongs to whoever runs
    # the suite, not to root, and StrictModes has its own tests.
    "strictmodes": "no",
    "maxauthtries": "6",
    "logingracetime": "120",
    "x11forwarding": "yes",
    "clientaliveinterval": "0",
    "clientalivecountmax": "3",
    "loglevel": "INFO",
}


def journal_line(at: float, message: str, ident: str = "sshd") -> str:
    """
    One line of ``journalctl -o short-unix``.

    Args:
        at: When, epoch seconds.
        message: sshd's message.
        ident: The program that wrote it.

    Returns:
        The line.
    """
    return f"{at:.6f} vps-1 {ident}[4242]: {message}"


def accepted(
    user: str, fp: str, *, at: float, source: str = "198.51.100.7", port: int = 51234
) -> str:
    """
    A journal line of a key login.

    Args:
        user: The account.
        fp: The key's fingerprint.
        at: When.
        source: From where.
        port: The client's port.

    Returns:
        The line.
    """
    return journal_line(
        at, f"Accepted publickey for {user} from {source} port {port} ssh2: ED25519 {fp}"
    )


class FakeHost:
    """
    A server's account and SSH files, under a temporary root.

    Args:
        root: The temporary directory standing for ``/``.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths = HostPaths(root)
        self.write(
            "/etc/passwd",
            "\n".join(
                [
                    "root:x:0:0:root:/root:/bin/bash",
                    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin",
                    "alice:x:1000:1000:Alice:/home/alice:/bin/bash",
                    "bob:x:1001:1001:Bob:/home/bob:/bin/bash",
                    "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin",
                ]
            )
            + "\n",
        )
        self.write(
            "/etc/group",
            "root:x:0:\nsudo:x:27:alice\nalice:x:1000:\nbob:x:1001:\n",
        )
        self.write(
            "/etc/shadow",
            "root:$6$salt$hash:19000:0:99999:7:::\n"
            "daemon:*:19000:0:99999:7:::\n"
            "alice:!:19000:0:99999:7:::\n"
            "bob:$6$salt$hash:19000:0:99999:7:::\n"
            "www-data:*:19000:0:99999:7:::\n",
        )
        self.write("/etc/shells", "/bin/sh\n/bin/bash\n")
        self.write("/etc/login.defs", "UID_MIN\t\t\t 1000\nUID_MAX\t\t\t60000\n")
        self.write(
            "/etc/sudoers",
            "Defaults\tenv_reset\nroot\tALL=(ALL:ALL) ALL\n%sudo\tALL=(ALL:ALL) ALL\n"
            "@includedir /etc/sudoers.d\n",
        )
        self.write("/etc/sudoers.d/90-alice", "alice ALL=(ALL) NOPASSWD:ALL\n")
        self.write("/etc/sudoers.d/README", "# not a rule\n")
        self.write(
            "/etc/ssh/sshd_config",
            "Include /etc/ssh/sshd_config.d/*.conf\n\nKbdInteractiveAuthentication no\nUsePAM yes\n",
        )
        (root / "etc/ssh/sshd_config.d").mkdir(parents=True, exist_ok=True)
        self.write("/root/.ssh/authorized_keys", ED_KEY + "\n")
        self.write("/home/alice/.ssh/authorized_keys", ALICE_KEY + "\n")
        (root / "home/bob").mkdir(parents=True, exist_ok=True)

    def write(self, path: str, text: str) -> Path:
        """
        Write a file below the root.

        Args:
            path: Its path on the fake server.
            text: The content.

        Returns:
            The local path.
        """
        local = self.paths.at(path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(text)
        return local

    def read(self, path: str) -> str | None:
        """
        Read a file below the root.

        Args:
            path: Its path on the fake server.

        Returns:
            The content, or None when it does not exist.
        """
        local = self.paths.at(path)
        return local.read_text() if local.exists() else None


class FakeSshd(FakeRunner):
    """
    A runner whose ``sshd -T`` reads Noust's drop-in from the fake host.

    Attributes:
        host: The fake server.
        before_include: Directives the main file sets before its Include,
            which beat the drop-in (the case the verification must catch).
        configured: Directives the rest of the configuration sets (another
            drop-in, the main file after its Include).
        per_user: Extra directives for a ``-C user=`` connection, as a Match
            block would give.
        test_exit: What ``sshd -t`` exits with.
        test_output: What it prints on failure.
    """

    def __init__(self, host: FakeHost) -> None:
        super().__init__()
        self.host = host
        self.before_include: dict[str, str] = {}
        self.configured: dict[str, str] = {}
        self.per_user: dict[str, dict[str, str]] = {}
        self.test_exit = 0
        self.test_output = ""
        self.script(
            ["systemctl", "show", "ssh.service"],
            stdout="Id=ssh.service\nLoadState=loaded\nActiveState=active\n\n"
            "Id=ssh.service\nLoadState=loaded\nActiveState=active\n\n"
            "Id=ssh.socket\nLoadState=loaded\nActiveState=inactive\n",
        )
        self.script(["systemctl", "is-active"], stdout="inactive\n", exit_code=3)
        self.script(["journalctl"], stdout="")
        self.script(["ss", "-Hltnup"], stdout="")
        self.script(["ss", "-Htnp"], stdout="")

    def effective(self, user: str) -> dict[str, str]:
        """
        What sshd would use for a connection as ``user``: first value wins.

        Args:
            user: The account.

        Returns:
            Keyword to value.
        """
        dropin = parse_dropin(self.host.read(DROPIN))
        values: dict[str, str] = {}
        for layer in (
            self.per_user.get(user, {}),
            self.before_include,
            dropin,
            self.configured,
            SSHD_DEFAULTS,
        ):
            for keyword, value in layer.items():
                values.setdefault(keyword, value)
        return values

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 60,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        args = [str(a) for a in argv]
        if args[:2] == ["sshd", "-T"]:
            self.calls.append(tuple(args))
            self.envs.append(env)
            spec = args[3] if len(args) > 3 else ""
            name = dict(part.split("=", 1) for part in spec.split(",") if "=" in part).get(
                "user", "root"
            )
            text = "".join(f"{k} {v}\n" for k, v in self.effective(name).items())
            return CommandResult(argv=tuple(args), exit_code=0, stdout=text)
        if args == ["sshd", "-t"]:
            self.calls.append(tuple(args))
            self.envs.append(env)
            return CommandResult(
                argv=tuple(args), exit_code=self.test_exit, stderr=self.test_output
            )
        return super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            input=input,
            stdin_path=stdin_path,
            user=user,
            check=check,
            secrets=secrets,
        )


def uid() -> int:
    """The uid that owns files the suite creates."""
    return os.getuid()
