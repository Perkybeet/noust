# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The runner and the filesystem of a rehearsal that is meant to be read afterwards.

``--dry-run`` (:class:`~noust.core.runner.DryRunRunner`, :class:`~noust.core.fs.DryRunFileSystem`)
refuses every command and every change that is not a declared probe and keeps a line for each. A
plan needs more than a line: the commands in their order with the probes among them (what the plan
looked at is part of what it says), what each would run as, and the content of each file it would
write, to compare with what the file holds now. These two classes are that engine, with that
record, and nothing else; they add no way to execute anything.

What they promise, and what the tests of this package hold them to:

- A command that is not a declared probe never reaches the runner underneath, whatever the code
  that issues it believes.
- A probe is asked for real but briefly: its deadline is capped, and a plan that has spent its time
  looking stops looking and says so.
- Nothing is written, moved, linked or removed, and no file is read through a link the tree of an
  application holds (a repository is untrusted: a link it plants must not turn a plan into a way
  to read a file).
- What reaches a plan is scrubbed: the argv by the runner's own redaction and the caller's
  scrubber, the files by :mod:`noust.core.plans.diffs`.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from noust.core.fs import DryRunFileSystem
from noust.core.plans.diffs import MAX_TEXT_BYTES, FileView, view_of_change
from noust.core.plans.shape import MAX_COMMANDS, MAX_FILES, Change, PlanCommand, PlanFile
from noust.core.runner import (
    DEFAULT_TIMEOUT,
    EXIT_TIMEOUT,
    PTY_DEFAULT_COLS,
    PTY_DEFAULT_ROWS,
    CommandResult,
    CommandRunner,
    DryRunRunner,
    ProcessHandle,
    PtyHandle,
    SandboxSpec,
    _redact,
    _validate,
    is_read_only,
)

#: The longest a probe may take while a plan is made, whatever its caller allows: the person is
#: waiting, and a repository that does not answer in this long is told as "not known".
PROBE_TIMEOUT_CAP = 20

#: How long, from the start of a plan, probes are still asked. After it the plan goes on without
#: looking and says how many it did not ask.
PROBE_BUDGET_SECONDS = 60.0

#: An ancestor of a file may be a link only if root made it and it is this near the top of the
#: tree (``/var/www`` pointing at ``/srv/www``): below it the tree is an application's, and a link
#: there is what a repository can plant.
TRUSTED_LINK_DEPTH = 3


def asks_over_the_network(argv: Sequence[str]) -> bool:
    """
    Args:
        argv: A command.

    Returns:
        Whether it asks a repository or a registry (``git ls-remote``, ``docker buildx imagetools``):
        the only probes that leave the machine.
    """
    program = Path(argv[0]).name if argv else ""
    rest = list(argv[1:])
    return (program == "git" and "ls-remote" in rest) or (
        program == "docker" and "imagetools" in rest
    )


class PlanRunner(DryRunRunner):
    """
    A :class:`~noust.core.runner.DryRunRunner` that notes every command it is given.

    Attributes:
        commands: Every command, in order, redacted, probes among them; at most
            :data:`~noust.core.plans.shape.MAX_COMMANDS`.
        omitted: How many were not kept for that limit.
        skipped_probes: How many probes were not asked because the plan had spent its time.
        asked_remote: A probe asked a repository or a registry.
    """

    def __init__(
        self,
        inner: CommandRunner,
        *,
        scrub: Callable[[str], str],
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            inner: The runner the probes are asked of.
            scrub: What turns a text into one with no secret in it, applied to every argument.
            now: The clock the time for looking is measured with.
        """
        super().__init__(inner)
        self._scrub = scrub
        self._now = now
        self._started = now()
        self._lock = threading.Lock()
        self.commands: list[PlanCommand] = []
        self.omitted = 0
        self.skipped_probes = 0
        self.asked_remote = False

    def _note(
        self,
        argv: Sequence[str],
        secrets: Sequence[str],
        *,
        probe: bool,
        cwd: Path | None = None,
        user: str | None = None,
        sandbox: SandboxSpec | None = None,
        writes_to: Path | None = None,
    ) -> tuple[str, ...]:
        """
        Keep one command.

        Args:
            argv: The command.
            secrets: Values the caller says are secret.
            probe: It only looks.
            cwd: Where it runs.
            user: The account it runs as.
            sandbox: The confinement of a build.
            writes_to: The file whose content it captures.

        Returns:
            The command as it was kept: redacted.
        """
        redacted = tuple(self._scrub(arg) for arg in _redact(_validate(argv), secrets))
        runs_as = f"sandbox:{sandbox.user}" if sandbox is not None else user
        entry = PlanCommand(
            argv=redacted,
            probe=probe,
            cwd=str(cwd) if cwd is not None else None,
            runs_as=runs_as,
            writes_to=str(writes_to) if writes_to is not None else None,
        )
        with self._lock:
            if probe and asks_over_the_network(redacted):
                self.asked_remote = True
            if len(self.commands) < MAX_COMMANDS:
                self.commands.append(entry)
            else:
                self.omitted += 1
        return redacted

    def note_check(self, argv: Sequence[str], seconds: float | None) -> None:
        """
        Keep a check of the application the code would ask, in its place among the commands.

        Args:
            argv: What would be asked.
            seconds: The longest it would wait.
        """
        entry = PlanCommand(
            argv=tuple(self._scrub(str(arg)) for arg in argv),
            probe=True,
            check=True,
            seconds=seconds,
        )
        with self._lock:
            if len(self.commands) < MAX_COMMANDS:
                self.commands.append(entry)
            else:
                self.omitted += 1

    def _out_of_time(self) -> bool:
        """
        Returns:
            Whether the plan has spent the time it has for looking.
        """
        return self._now() - self._started > PROBE_BUDGET_SECONDS

    def _not_asked(self, redacted: tuple[str, ...]) -> CommandResult:
        """
        Args:
            redacted: The probe that was not asked.

        Returns:
            What a probe that timed out says, so that the code that asked takes it as "unknown".
        """
        with self._lock:
            self.skipped_probes += 1
        return CommandResult(
            argv=redacted,
            exit_code=EXIT_TIMEOUT,
            stderr="Not asked: the plan's time for looking ran out",
        )

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        probe = sandbox is None and is_read_only(argv)
        redacted = self._note(argv, secrets, probe=probe, cwd=cwd, user=user, sandbox=sandbox)
        if probe and self._out_of_time():
            return self._not_asked(redacted)
        return super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout=min(timeout, PROBE_TIMEOUT_CAP) if probe else timeout,
            input=input,
            stdin_path=stdin_path,
            user=user,
            check=check,
            secrets=secrets,
            sandbox=sandbox,
            clean_env=clean_env,
        )

    def stream(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        probe = sandbox is None and is_read_only(argv)
        redacted = self._note(argv, secrets, probe=probe, cwd=cwd, user=user, sandbox=sandbox)
        if probe and self._out_of_time():
            return self._not_asked(redacted)
        return super().stream(
            argv,
            on_line=on_line,
            cwd=cwd,
            env=env,
            timeout=min(timeout, PROBE_TIMEOUT_CAP) if probe else timeout,
            user=user,
            secrets=secrets,
            sandbox=sandbox,
            clean_env=clean_env,
        )

    def capture_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        compress: bool = False,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        self._note(argv, secrets, probe=False, cwd=cwd, user=user, writes_to=destination)
        return super().capture_to_file(
            argv,
            destination,
            compress=compress,
            cwd=cwd,
            env=env,
            timeout=timeout,
            user=user,
            secrets=secrets,
        )

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> ProcessHandle:
        probe = is_read_only(argv)
        self._note(argv, secrets, probe=probe, cwd=cwd, user=user)
        return super().start(argv, cwd=cwd, env=env, user=user, secrets=secrets)

    def pty(
        self,
        argv: Sequence[str],
        *,
        max_seconds: int,
        idle_seconds: int,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        rows: int = PTY_DEFAULT_ROWS,
        cols: int = PTY_DEFAULT_COLS,
    ) -> PtyHandle:
        self._note(argv, secrets, probe=False, cwd=cwd, user=user, sandbox=sandbox)
        return super().pty(
            argv,
            max_seconds=max_seconds,
            idle_seconds=idle_seconds,
            cwd=cwd,
            env=env,
            user=user,
            secrets=secrets,
            sandbox=sandbox,
            rows=rows,
            cols=cols,
        )


@dataclass
class _Draft:
    """
    What the rehearsal has done to one path so far.

    Attributes:
        path: The path.
        change: What would have happened to it.
        content: What a write would have put in it.
        mode: The permissions a write would have given it.
        destination: The other end of a move, a copy or a link.
    """

    path: str
    change: Change
    content: str | bytes | None = None
    mode: int | None = None
    destination: str | None = None


class PlanFileSystem(DryRunFileSystem):
    """
    A :class:`~noust.core.fs.DryRunFileSystem` that keeps what it was asked to do, to diff it.

    Reads are not routed through the filesystem seam, so the code under rehearsal reads the real
    files and its own writes are not there: a plan says so (see the ``unknown`` of an operation
    that reads back what it wrote). It does not keep the sentences of its parent's ``skipped``:
    the changes are here, with their content.
    """

    def __init__(self, *, scrub: Callable[[str], str]) -> None:
        """
        Args:
            scrub: What turns a line of a file into one with no secret in it.
        """
        super().__init__()
        self._scrub = scrub
        self._lock = threading.Lock()
        self._drafts: OrderedDict[str, _Draft] = OrderedDict()

    def _put(self, path: Path, draft: _Draft) -> None:
        """
        Args:
            path: What was changed.
            draft: How.
        """
        # A path that is moved and also copied has two entries: what happens to it is two things.
        key = (
            f"{draft.change}:{path}:{draft.destination}"
            if draft.change in ("move", "copy")
            else str(path)
        )
        with self._lock:
            self._drafts[key] = draft

    def write_text(
        self,
        path: Path,
        content: str,
        *,
        mode: int = 0o644,
        owner: tuple[int, int] | None = None,
    ) -> None:
        self._put(path, _Draft(str(path), "create", content, mode))

    def write_bytes(self, path: Path, content: bytes, *, mode: int = 0o644) -> None:
        self._put(path, _Draft(str(path), "create", content, mode))

    def make_dir(
        self, path: Path, *, mode: int = 0o755, parents: bool = True, exist_ok: bool = True
    ) -> None:
        if not os.path.isdir(path):
            self._put(path, _Draft(str(path), "mkdir", mode=mode))

    def _gone(self, path: Path, *, tree: bool) -> None:
        """
        Args:
            path: What was deleted.
            tree: It was a directory with everything under it.
        """
        key = str(path)
        with self._lock:
            made_here = key in self._drafts and not os.path.lexists(path)
            if tree:
                prefix = key.rstrip("/") + "/"
                for under in [name for name in self._drafts if name.startswith(prefix)]:
                    del self._drafts[under]
            if made_here:
                # Made and deleted inside the operation: nothing changes.
                del self._drafts[key]
            else:
                self._drafts[key] = _Draft(key, "delete")

    def remove(self, path: Path, *, missing_ok: bool = True) -> None:
        self._gone(path, tree=False)

    def remove_tree(self, path: Path) -> None:
        self._gone(path, tree=True)

    def move(self, source: Path, destination: Path) -> None:
        self._put(source, _Draft(str(source), "move", destination=str(destination)))

    def rename(self, source: Path, destination: Path) -> None:
        self._put(source, _Draft(str(source), "move", destination=str(destination)))

    def copy_tree(self, source: Path, destination: Path) -> None:
        self._put(source, _Draft(str(source), "copy", destination=str(destination)))

    def chmod(self, path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        with self._lock:
            if str(path) not in self._drafts:
                self._drafts[str(path)] = _Draft(str(path), "meta", mode=mode)

    def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
        with self._lock:
            if str(path) not in self._drafts:
                self._drafts[str(path)] = _Draft(str(path), "meta", mode=mode)

    def symlink(self, target: Path, link: Path) -> None:
        self._put(link, _Draft(str(link), "link", destination=str(target)))

    def files(self) -> tuple[tuple[PlanFile, ...], int]:
        """
        Turn what was done into what a plan says of each path.

        Returns:
            The paths, in the order they were first touched and at most
            :data:`~noust.core.plans.shape.MAX_FILES`, and how many were left out.
        """
        with self._lock:
            drafts = list(self._drafts.items())
        found: list[PlanFile] = []
        for _, draft in drafts:
            item = self._file(Path(draft.path), draft)
            if item is not None:
                found.append(item)
        return tuple(found[:MAX_FILES]), max(0, len(found) - MAX_FILES)

    def _file(self, path: Path, draft: _Draft) -> PlanFile | None:
        """
        Args:
            path: The path.
            draft: What was done to it.

        Returns:
            What the plan says of it, or None when the write would change nothing.
        """
        if draft.change != "create":
            return PlanFile(
                str(path),
                draft.change,
                destination=draft.destination,
            )
        existed = os.path.lexists(path)
        before = _read_before(path) if existed else None
        if isinstance(before, _Unreadable):
            return PlanFile(str(path), "modify", reason=before.reason)
        if existed and before == _as_bytes(draft.content):
            return None
        found: FileView = view_of_change(
            path, before=before, after=draft.content, scrub=self._scrub, mode=draft.mode
        )
        return PlanFile(
            str(path),
            "modify" if existed else "create",
            shown=found.shown,
            lines=found.lines,
            truncated=found.truncated,
            reason=found.reason,
        )


@dataclass(frozen=True)
class _Unreadable:
    """
    A file that is not read: what is said instead of its content.

    Attributes:
        reason: ``unreadable`` (a link or an error) or ``large``.
    """

    reason: Literal["unreadable", "large"]


def _as_bytes(content: str | bytes | None) -> bytes | None:
    """
    Args:
        content: What a write would put in a file.

    Returns:
        The same as bytes, to compare it with what the file holds.
    """
    return content.encode("utf-8") if isinstance(content, str) else content


def _under_a_planted_link(path: Path) -> bool:
    """
    Args:
        path: A file a plan would write.

    Returns:
        Whether it, or a directory above it, is a link that is not root's at the top of the tree.
    """
    parts = Path(os.path.abspath(path)).parts
    for depth in range(1, len(parts) + 1):
        candidate = Path(*parts[:depth])
        try:
            info = os.lstat(candidate)
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) and (info.st_uid != 0 or depth > TRUSTED_LINK_DEPTH):
            return True
    return False


def _read_before(path: Path) -> bytes | _Unreadable | None:
    """
    Read what a file holds now, for a plan to compare with what it would hold.

    Args:
        path: An existing path.

    Returns:
        Its content, or :class:`_Unreadable` when it is not read: under a link a tree could have
        planted, not a regular file, larger than a plan diffs, or not readable.
    """
    if _under_a_planted_link(path):
        return _Unreadable("unreadable")
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return _Unreadable("unreadable")
        if info.st_size > MAX_TEXT_BYTES:
            return _Unreadable("large")
        return Path(path).read_bytes()
    except OSError:
        return _Unreadable("unreadable")
