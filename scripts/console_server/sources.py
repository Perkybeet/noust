# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sources and delivery (4.0): the Source subsection of an application's page and the queue."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .app_tabs import _TABS_NPM_BUILD, _TABS_NPM_INSTALL, NOTHING_NEW_APP
from .sandbox import Sandbox

# ---------------------------------------------------------------------------
# Console: sources and delivery (4.0). The Source subsection of an application's
# settings needs a repository cloned over SSH, which a code host answers for the
# key a deploy signs in with; Settings, Integrations lists the host keys Noust
# pinned; and the deploy queue needs a build that lasts long enough to see it
# wait, to cancel it and to see the next one start.
#
# - taller.example.com, a static site on the repository bodas.example.com also
#   uses, is cloned over SSH: a deploy key made for it is shared by both.
# - The modelled code host accepts a key it has been shown: the first time the
#   key a rotation waits to put in place is offered, it refuses (the operator has
#   not added it yet), and the next one answers. The current key always answers.
# - catalogo.example.org, on releases, installs and builds one line of npm output
#   at a time, so an update takes about eighteen seconds and can be cancelled while
#   it runs; its second request waits behind it, and a third joins the second.
# ---------------------------------------------------------------------------

#: A static site cloned over SSH, from the repository another seeded site uses over https.
SOURCES_KEY_APP = "taller.example.com"
SOURCES_KEY_SOURCE = "git@github.com:you/landing.git"
SOURCES_KEY_COMMIT = "5b1f9d0c3a7e24681f0d9c8b7a6e5d4c3b2a1908"

#: The code hosts Noust has pinned a key of, as ssh-keygen -l names them in known_hosts.
SOURCES_PINNED_HOSTS = ("github.com", "[git.example.org]:2222")

#: Seconds each line of the release app's npm output takes: nine lines to install, nine to build.
DELIVERY_BUILD_LINE_DELAY = 1.0


def known_hosts_listing(path: Path) -> str:
    """
    Print a ``known_hosts`` file the way ``ssh-keygen -l -f`` does.

    Args:
        path: The file.

    Returns:
        One ``bits SHA256:fingerprint host (TYPE)`` line per key; nothing for a file that is not there.
    """
    import base64

    if not path.is_file():
        return ""
    names = {"ssh-ed25519": "ED25519", "ssh-rsa": "RSA", "ecdsa-sha2-nistp256": "ECDSA"}
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0].startswith("#"):
            continue
        digest = hashlib.sha256(base64.b64decode(parts[2])).digest()
        fingerprint = base64.b64encode(digest).decode().rstrip("=")
        lines.append(f"256 SHA256:{fingerprint} {parts[0]} ({names.get(parts[1], parts[1])})")
    return "".join(f"{line}\n" for line in lines)


def forget_known_host(path: Path, target: str) -> None:
    """
    Remove the keys of a host from a ``known_hosts`` file, as ``ssh-keygen -R`` does.

    Args:
        path: The file.
        target: The host as the file names it: ``name``, or ``[name]:port``.
    """
    if not path.is_file():
        return
    kept = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.split()[:1] != [target]
    ]
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


def _modelled_code_host(
    args: tuple[str, ...], env: Mapping[str, str] | None, refused: dict[str, int]
) -> Any:
    """
    Answer ``git ls-remote`` for the SSH repository the way its code host would.

    Args:
        args: The argv the runner was asked to run.
        env: Its environment: ``GIT_SSH_COMMAND`` names the key it signs in with.
        refused: How many times each waiting key has been turned away.

    Returns:
        The command's outcome, or None for any other command.
    """
    import shlex

    from noust.core.runner import CommandResult

    if args[:1] != ("git",) or "ls-remote" not in args or SOURCES_KEY_SOURCE not in args:
        return None
    command = shlex.split((env or {}).get("GIT_SSH_COMMAND", ""))
    key = command[command.index("-i") + 1] if "-i" in command else ""
    # The key a rotation waits to put in place is not at the code host until the operator adds
    # it: the first look is refused, with the words a real host uses, and the old key answers.
    if key.endswith("/next_key"):
        if key in refused:
            # Added at the host since: it answers, and the next rotation starts from the first look again.
            del refused[key]
        else:
            refused[key] = 1
            return CommandResult(
                args,
                128,
                "",
                "git@github.com: Permission denied (publickey).\n"
                "fatal: Could not read from remote repository.\n\n"
                "Please make sure you have the correct access rights\nand the repository exists.\n",
            )
    ref = next((argument for argument in args if argument.startswith("refs/heads/")), None)
    if ref is not None:
        return CommandResult(args, 0, f"{SOURCES_KEY_COMMIT}\t{ref}\n", "")
    return CommandResult(args, 0, f"ref: refs/heads/main\tHEAD\n{SOURCES_KEY_COMMIT}\tHEAD\n", "")


def _slow_release_builds(root: Path) -> None:
    """
    Run npm under an application's directory a line at a time, as a real build does.

    Like :func:`_tabs_slow_live_builds`, and as the real runner does it, a build that was
    cancelled stops at the next line: the window a cancellation opens is watched here too.

    Args:
        root: The application's directory.
    """
    from noust.core import cancellation
    from noust.core.runner import CommandCancelled, CommandResult, get_runner

    runner = get_runner()
    original_stream = runner.stream
    original_run = runner.run

    def lines_of(args: tuple[str, ...], cwd: Path | None) -> tuple[str, ...] | None:
        inside = cwd is not None and (Path(cwd) == root or root in Path(cwd).parents)
        if not (inside and args[:1] in (("npm",), ("npx",))):
            return None
        return _TABS_NPM_BUILD if "build" in args else _TABS_NPM_INSTALL

    def play(args: tuple[str, ...], output: tuple[str, ...], on_line: Any) -> Any:
        runner.calls.append(args)
        for line in output:
            time.sleep(DELIVERY_BUILD_LINE_DELAY)
            token = cancellation.current()
            if token is not None and token.requested:
                raise CommandCancelled(
                    f"{' '.join(args[:2])} was stopped: its deployment was cancelled"
                )
            if on_line is not None:
                on_line(line)
        return CommandResult(argv=args, exit_code=0, stdout="\n".join(output) + "\n", stderr="")

    def stream(argv: Sequence[str], *, on_line: Any, cwd: Path | None = None, **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        output = lines_of(args, cwd)
        if output is None:
            return original_stream(argv, on_line=on_line, cwd=cwd, **kwargs)
        return play(args, output, on_line)

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        output = lines_of(args, kwargs.get("cwd"))
        if output is None:
            return original_run(argv, **kwargs)
        return play(args, output, None)

    runner.stream = stream  # type: ignore[method-assign]
    runner.run = run  # type: ignore[method-assign]


def seed_sources_and_delivery(sandbox: Sandbox, store: Any) -> None:
    """
    Seed what the Source settings, the server's deploy keys and the deploy queue run on.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
    """
    from noust.core.runner import get_runner
    from noust.core.utils import domain_to_app_name
    from noust.managers.source_keys import DeployKeyManager
    from tests.support.fleet_support import ed25519_line

    app = store.get_app(SOURCES_KEY_APP)
    if app is not None:
        app.source = SOURCES_KEY_SOURCE
        store.update_app(app)

    known_hosts = DeployKeyManager().known_hosts_path()
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.write_text(
        "".join(
            f"{host} {ed25519_line(seed, '')}\n"
            for seed, host in enumerate(SOURCES_PINNED_HOSTS, 41)
        ),
        encoding="utf-8",
    )

    runner = get_runner()
    original = runner.run
    refused: dict[str, int] = {}

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        answer = _modelled_code_host(args, kwargs.get("env"), refused)
        if answer is None:
            return original(argv, **kwargs)
        runner.calls.append(args)
        return answer

    runner.run = run  # type: ignore[method-assign]

    _slow_release_builds(sandbox.apps_dir / domain_to_app_name(NOTHING_NEW_APP))
