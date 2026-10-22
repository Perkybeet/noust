# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the deploy key tests share: a runner that stands in for ssh-keygen and ssh.

A FakeRunner only records and replays; ``ssh-keygen`` has to write the files
the code under test reads next. :class:`DeployKeyRunner` does what the real
program does for the three invocations Noust makes (generate, derive the
public half, remove a pinned host) and nothing else, so a test asserts on the
argv and on the files.
"""

from __future__ import annotations

import base64
import re
import struct
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from noust.core.runner import CommandResult, FakeRunner
from tests.fleet_support import ed25519_line, fingerprint

__all__ = [
    "DeployKeyRunner",
    "GitScript",
    "ed25519_line",
    "fingerprint",
    "private_key_text",
    "rsa_line",
]

#: What a private key file of the tests looks like. The seed is what the
#: fake ``ssh-keygen -y`` derives the public half from.
HEADER = "-----BEGIN OPENSSH PRIVATE KEY-----"
FOOTER = "-----END OPENSSH PRIVATE KEY-----"


def private_key_text(seed: int, *, marker: str = "") -> str:
    """
    Build a private key file the fake ssh-keygen can read.

    Args:
        seed: Which key it is.
        marker: ``PASSPHRASE`` makes it a key ssh-keygen cannot open without one.

    Returns:
        The file's text, newline-terminated.
    """
    return f"{HEADER}\nseed={seed} {marker}\n{FOOTER}\n"


def rsa_line(seed: int, comment: str = "") -> str:
    """
    Build a structurally valid ssh-rsa public key line.

    Args:
        seed: Varies the key bytes.
        comment: The comment, if any.

    Returns:
        ``ssh-rsa AAAA... [comment]``.
    """

    def part(data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + data

    blob = part(b"ssh-rsa") + part(b"\x01\x00\x01") + part(bytes([seed % 255 + 1]) * 256)
    return f"ssh-rsa {base64.b64encode(blob).decode()} {comment}".rstrip()


class DeployKeyRunner(FakeRunner):
    """
    A FakeRunner whose ssh-keygen generates, derives and forgets like the real one.

    Attributes:
        generated: How many pairs were generated; each is a different key.
        derive_as: What ``ssh-keygen -y`` prints for a key whose seed is ``n``;
            an ed25519 line by default.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.generated = 0
        self.derive_as: Callable[[int], str] = lambda seed: ed25519_line(1000 + seed)

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        if not argv or argv[0] != "ssh-keygen":
            return result
        if "-y" in argv:
            return self._derive(argv, result)
        if "-R" in argv:
            return self._forget(argv, result)
        if "-C" in argv and result.success:
            path = Path(argv[argv.index("-f") + 1])
            comment = argv[argv.index("-C") + 1]
            path.write_text(private_key_text(5000 + self.generated))
            Path(f"{path}.pub").write_text(ed25519_line(5000 + self.generated, comment) + "\n")
            self.generated += 1
        return result

    def _derive(self, argv: Sequence[str], result: CommandResult) -> CommandResult:
        """
        Answer ``ssh-keygen -y -P '' -f key`` from the key file.

        Args:
            argv: The command.
            result: What the base class scripted for it.

        Returns:
            The public half, or the failure the real program prints.
        """
        if not result.success:
            return result
        text = Path(argv[argv.index("-f") + 1]).read_text()
        if "PASSPHRASE" in text:
            return CommandResult(
                argv=result.argv,
                exit_code=1,
                stderr="Load key: incorrect passphrase supplied to decrypt private key\n",
            )
        found = re.search(r"seed=(\d+)", text)
        if not found:
            return CommandResult(argv=result.argv, exit_code=1, stderr="Load key: invalid format\n")
        return CommandResult(
            argv=result.argv, exit_code=0, stdout=self.derive_as(int(found.group(1))) + "\n"
        )

    def _forget(self, argv: Sequence[str], result: CommandResult) -> CommandResult:
        """
        Remove a host from a known_hosts file, as ``ssh-keygen -R`` does.

        Args:
            argv: The command.
            result: What the base class scripted for it.

        Returns:
            The same result.
        """
        path = Path(argv[argv.index("-f") + 1])
        host = argv[argv.index("-R") + 1]
        if result.success and path.is_file():
            kept = [
                line for line in path.read_text().splitlines() if not line.split(" ", 1)[0] == host
            ]
            path.write_text("\n".join(kept) + ("\n" if kept else ""))
        return result


class GitScript(FakeRunner):
    """
    A FakeRunner that answers a git command by the identity it was run with.

    ``responder(argv, env)`` returns ``(exit_code, stdout, stderr)``; the
    ``GIT_SSH_COMMAND`` in ``env`` says which key was offered.
    """

    def __init__(
        self, responder: Callable[[Sequence[str], Mapping[str, str]], tuple[int, str, str]]
    ) -> None:
        super().__init__()
        self.responder = responder

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        if argv and argv[0] == "git":
            code, out, err = self.responder(argv, kwargs.get("env") or {})
            return CommandResult(argv=result.argv, exit_code=code, stdout=out, stderr=err)
        return result
