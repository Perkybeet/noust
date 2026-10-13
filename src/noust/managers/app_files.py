# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files of an application, from the console: list, read, download, upload, edit (checklist B-55).

A file manager is a root-equivalent feature in a console that is root-equivalent
already, so everything here is about not making it more than it is:

- **It acts as the application's account**, never as root. Every operation is an
  argument vector run with :meth:`~noust.core.runner.CommandRunner.run` as that
  account (``find``, ``head``, ``cat``, ``dd``, ``mv``, ``rm``), so the kernel's
  own permissions decide what can be read and written, exactly as for the
  application and for ``noust app exec``. Root only looks at the path.
- **A path is a path inside the application**, relative to its directory. It never
  holds ``..``, a NUL byte or a control character, and every directory on the way
  is a real directory: a link in the tree (``current`` is one, and a repository can
  plant others) is shown, can be removed or renamed, and is never walked through.
  The checks use ``lstat``, and read nothing: what they race with is the
  application's own code, which can already reach what its account can.
- **No secrets by the side door.** ``.env`` files are the environment's, which has
  its own validation, masking and audit: the browser neither reads nor writes
  them. A secret in any other file is what the application's account can read,
  which is what a shell as it can too, and the routes ask for the same permission.
- **Bounded.** A file edited in the console is at most :data:`EDIT_MAX_BYTES`, an
  upload at most :data:`UPLOAD_MAX_BYTES`; a listing is cut at :data:`LIST_MAX`
  entries and says so.
- **Written atomically.** An edit or an upload goes to a temporary name beside
  the file, keeps the mode of the file it replaces, and is renamed over it.
- **Audited**: who, what and how many bytes, never the content.
"""

from __future__ import annotations

import logging
import os
import re
import secrets as _secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core import audit, paths
from noust.core.exceptions import NoustError, SecurityError, ServiceError, ValidationError
from noust.core.fs import get_fs
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.core.store import App, NoustStore, get_store
from noust.deployers.helpers.layout import app_root
from noust.managers.app_identity import service_account

logger = logging.getLogger(__name__)

#: The largest file the console opens for editing, in bytes.
EDIT_MAX_BYTES = 5 * 1024 * 1024

#: The largest upload, in bytes.
UPLOAD_MAX_BYTES = 100 * 1024 * 1024

#: The most entries of one directory a listing returns.
LIST_MAX = 2000

#: The most characters of a path.
PATH_MAX = 1024

#: Deadlines of the commands, in seconds.
LIST_TIMEOUT = 30
READ_TIMEOUT = 60
WRITE_TIMEOUT = 300

#: What a name may not hold: a NUL byte and the control characters.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: The environment's own files, which the browser leaves to the environment.
_ENV_NAME = re.compile(r"^\.env(?:\..*)?$")

#: A kind of entry, as ``find -printf %y`` says it.
_KINDS = {"f": "file", "d": "dir", "l": "link"}


@dataclass(frozen=True)
class Entry:
    """
    One entry of a directory.

    Attributes:
        name: Its name.
        kind: ``file``, ``dir``, ``link`` or ``other``.
        size: Its size in bytes (a link's is its target's name's length).
        modified: When it last changed, as an ISO timestamp in UTC.
        mode: Its permissions, as octal text such as ``640``.
        owner: Its owner.
        target: Where a link points, as text; never followed.
        sensitive: It is an environment file, which the browser does not open.
    """

    name: str
    kind: str
    size: int
    modified: str
    mode: str
    owner: str = ""
    target: str | None = None
    sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The entry as JSON-ready data.
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "modified": self.modified,
            "mode": self.mode,
            "owner": self.owner,
            "target": self.target,
            "sensitive": self.sensitive,
        }


@dataclass(frozen=True)
class Listing:
    """
    A directory.

    Attributes:
        path: Its path relative to the application, empty for the application's own directory.
        entries: What is in it, directories first, then by name.
        truncated: There were more than :data:`LIST_MAX` entries; the rest are not shown.
    """

    path: str
    entries: tuple[Entry, ...]
    truncated: bool = False


@dataclass(frozen=True)
class TextFile:
    """
    A file opened for editing.

    Attributes:
        path: Its path relative to the application.
        size: Its size in bytes.
        content: Its text.
    """

    path: str
    size: int
    content: str


def clean_path(raw: str, *, allow_empty: bool = True) -> PurePosixPath:
    """
    Read a path the client sent: relative to the application, and nothing else.

    Args:
        raw: What the client sent; empty or ``/`` is the application's directory.
        allow_empty: Accept the application's own directory.

    Returns:
        The path, without a leading slash or empty parts.

    Raises:
        ValidationError: It climbs with ``..``, holds a control character or a
            NUL byte, is too long, or is empty where a file is needed.
    """
    how = "Give a path inside the application, such as shared/uploads/logo.png."
    if not isinstance(raw, str) or len(raw) > PATH_MAX or _CONTROL.search(raw):
        raise ValidationError(
            "That is not a path inside the application", details=how, field="path"
        )
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ValidationError(
            "A path cannot climb out of the application with ..", details=how, field="path"
        )
    if not parts and not allow_empty:
        raise ValidationError("Name a file inside the application", details=how, field="path")
    return PurePosixPath(*parts) if parts else PurePosixPath()


def is_env_file(path: PurePosixPath) -> bool:
    """
    Tell whether a path names an environment file.

    Args:
        path: A path inside the application.

    Returns:
        True for ``.env`` and ``.env.*`` wherever they are.
    """
    return bool(path.parts) and _ENV_NAME.fullmatch(path.parts[-1]) is not None


def _refuse_env(path: PurePosixPath, what: str) -> None:
    """
    Refuse to read or write an environment file.

    Args:
        path: The path.
        what: What was asked, for the message.

    Raises:
        ValidationError: The path names an environment file.
    """
    if is_env_file(path):
        raise ValidationError(
            f"The file browser does not {what} environment files",
            details="Use the application's Environment: it validates every name and value, "
            "masks the secrets and records who revealed them.",
            field="path",
        )


def discard(path: Path) -> None:
    """
    Remove a scratch file, whether or not it is still there.

    Args:
        path: A file made by :meth:`AppFiles.reserve`.
    """
    with suppress(OSError):
        os.remove(path)


class AppFiles:
    """
    Operations on the files of one application, as its own account.

    Attributes:
        app: The application.
        root: Its directory, which every path is relative to.
        account: The account every command runs as.
    """

    def __init__(
        self,
        app: App,
        *,
        runner: CommandRunner | None = None,
        actor: str = "system",
        staging: Path | None = None,
    ) -> None:
        """
        Initialize the operations for an application.

        Args:
            app: The application.
            runner: Runs the commands; the process-wide one by default.
            actor: Who asked, for the audit trail.
            staging: Where uploads wait before they are written, a directory only
                root can enter; Noust's own state directory by default.
        """
        self.app = app
        self.root = app_root(app)
        self.account = service_account(app)[0]
        self._runner = runner
        self.actor = actor
        self._staging = staging

    @property
    def runner(self) -> CommandRunner:
        """The command runner."""
        return self._runner or get_runner()

    # -- Paths ----------------------------------------------------------------

    def resolve(self, path: PurePosixPath, *, leaf_may_be_link: bool = False) -> Path:
        """
        Turn a path inside the application into one on disk, refusing links on the way.

        Args:
            path: A cleaned path.
            leaf_may_be_link: The last part may be a link (to remove or rename it).

        Returns:
            The path on disk.

        Raises:
            SecurityError: A directory on the way is a link, or the application's
                directory is not a plain directory.
        """
        if self.root.is_symlink() or not self.root.is_dir():
            raise SecurityError(
                f"{self.root} is not a plain directory",
                details="The application's directory is a link or is missing; Noust "
                "does not browse through it.",
            )
        current = self.root
        parts = path.parts
        for index, part in enumerate(parts):
            current = current / part
            last = index == len(parts) - 1
            if current.is_symlink():
                if last and leaf_may_be_link:
                    return current
                raise SecurityError(
                    f"{'/'.join(parts[: index + 1])} is a link",
                    details="Noust does not follow links in an application's tree: a "
                    "repository can plant one anywhere. Open the directory it points to "
                    "by its own path (releases/<id> for current).",
                )
            if not last and not current.is_dir() and os.path.lexists(current):
                raise ValidationError(
                    f"{'/'.join(parts[: index + 1])} is not a directory", field="path"
                )
        return current

    # -- Commands -----------------------------------------------------------------

    def _run(
        self,
        argv: list[str],
        *,
        timeout: int,
        stdin_path: Path | None = None,
        what: str,
    ) -> CommandResult:
        """
        Run a command as the application's account and require it to succeed.

        Args:
            argv: The command.
            timeout: Its deadline.
            stdin_path: A file given to it as stdin.
            what: What it does, for the error.

        Returns:
            The result.

        Raises:
            ServiceError: It failed; the message carries its own words.
        """
        try:
            result = self.runner.run(
                argv, timeout=timeout, user=self.account, stdin_path=stdin_path, clean_env=True
            )
        except UnicodeDecodeError as exc:
            raise ServiceError(
                f"Could not {what}: a name or content here is not valid UTF-8",
                details="Rename it from a terminal, or download it instead.",
            ) from exc
        if not result.success:
            said = (result.stderr or result.stdout).strip() or f"exit status {result.exit_code}"
            raise ServiceError(f"Could not {what}", details=said)
        return result

    def _audit(self, action: str, path: PurePosixPath, **details: Any) -> None:
        """
        Record what was done, never what the content was.

        Args:
            action: ``read``, ``write``, ``upload``, ``mkdir``, ``move`` or ``remove``.
            path: The path.
            **details: Sizes and the like.
        """
        audit.record(
            "apps.files",
            target=f"app:{self.app.domain}",
            details={"action": action, "path": str(path), "by": self.actor, **details},
        )

    # -- Reading --------------------------------------------------------------------

    def list_directory(self, raw: str = "") -> Listing:
        """
        List a directory.

        Args:
            raw: The directory, relative to the application.

        Returns:
            Its entries: directories first, then by name.

        Raises:
            ValidationError: The path is not a path inside the application, or is a file.
            SecurityError: A link is on the way.
            ServiceError: The listing failed.
        """
        path = clean_path(raw)
        directory = self.resolve(path)
        if not directory.is_dir():
            raise ValidationError(f"{path or '.'} is not a directory", field="path")
        result = self._run(
            [
                "find",
                "-P",
                str(directory),
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-printf",
                "%y\\0%s\\0%T@\\0%m\\0%u\\0%l\\0%f\\0",
            ],
            timeout=LIST_TIMEOUT,
            what=f"list {path or 'the application'}",
        )
        fields = result.stdout.split("\0")
        if fields and fields[-1] == "":
            fields.pop()
        entries: list[Entry] = []
        for index in range(0, len(fields) - 6, 7):
            kind, size, stamp, mode, owner, target, name = fields[index : index + 7]
            try:
                modified = datetime.fromtimestamp(float(stamp), tz=timezone.utc).isoformat()
                length = int(size)
            except ValueError:
                modified, length = "", 0
            entries.append(
                Entry(
                    name=name,
                    kind=_KINDS.get(kind, "other"),
                    size=length,
                    modified=modified,
                    mode=mode,
                    owner=owner,
                    target=target or None,
                    sensitive=_ENV_NAME.fullmatch(name) is not None,
                )
            )
        entries.sort(key=lambda entry: (entry.kind != "dir", entry.name.lower()))
        truncated = len(entries) > LIST_MAX
        return Listing(path=str(path), entries=tuple(entries[:LIST_MAX]), truncated=truncated)

    def read_text(self, raw: str) -> TextFile:
        """
        Open a file for editing.

        Args:
            raw: The file, relative to the application.

        Returns:
            Its text.

        Raises:
            ValidationError: It is an environment file, a directory, larger than
                :data:`EDIT_MAX_BYTES` or not text.
            SecurityError: A link is on the way, or the file is one.
            ServiceError: It could not be read.
        """
        path = clean_path(raw, allow_empty=False)
        _refuse_env(path, "open")
        target = self.resolve(path)
        if not target.is_file():
            raise ValidationError(f"{path} is not a file", field="path")
        size = self._size(target)
        if size > EDIT_MAX_BYTES:
            raise ValidationError(
                f"{path} is {size} bytes: the console edits files up to {EDIT_MAX_BYTES // 1024 // 1024} MiB",
                details="Download it, change it and upload it again.",
                field="path",
            )
        result = self._run(
            ["head", "-c", str(EDIT_MAX_BYTES + 1), "--", str(target)],
            timeout=READ_TIMEOUT,
            what=f"read {path}",
        )
        text = result.stdout
        if "\0" in text:
            raise ValidationError(
                f"{path} is not a text file",
                details="Download it instead.",
                field="path",
            )
        self._audit("read", path, bytes=size)
        return TextFile(path=str(path), size=size, content=text)

    def _size(self, target: Path) -> int:
        """
        Ask how large a file is, as the account.

        Args:
            target: The file.

        Returns:
            Its size in bytes.
        """
        result = self._run(
            ["stat", "-c", "%s", "--", str(target)],
            timeout=LIST_TIMEOUT,
            what=f"stat {target.name}",
        )
        try:
            return int(result.stdout.strip())
        except ValueError:
            return 0

    def download(self, raw: str, destination: Path) -> int:
        """
        Copy a file, as the account, to a file only root can read, for the response.

        Args:
            raw: The file, relative to the application.
            destination: Where the copy goes.

        Returns:
            The size copied.

        Raises:
            ValidationError: It is an environment file or not a file.
            SecurityError: A link is on the way.
            ServiceError: It could not be read.
        """
        path = clean_path(raw, allow_empty=False)
        _refuse_env(path, "download")
        target = self.resolve(path)
        if not target.is_file():
            raise ValidationError(f"{path} is not a file", field="path")
        result = self.runner.capture_to_file(
            ["cat", "--", str(target)], destination, user=self.account, timeout=WRITE_TIMEOUT
        )
        if not result.success:
            raise ServiceError(f"Could not read {path}", details=result.stderr.strip())
        size = destination.stat().st_size
        self._audit("read", path, bytes=size, download=True)
        return size

    # -- Writing --------------------------------------------------------------------

    def reserve(self) -> Path:
        """
        Create an empty file only root can enter, to hold bytes on their way.

        The caller removes it.

        Returns:
            The file.
        """
        directory = self._staging or (paths.state_dir() / "file-staging")
        get_fs().make_dir(directory, mode=0o700)
        os.chmod(directory, 0o700)
        handle, name = tempfile.mkstemp(dir=directory, prefix="up-")
        os.close(handle)
        return Path(name)

    @contextmanager
    def staged(self, data: bytes | None = None) -> Iterator[Path]:
        """
        Hold bytes in a file only root can enter, until they are written.

        Args:
            data: What to put in it; None for an empty file the caller fills.

        Yields:
            The file.
        """
        name = self.reserve()
        try:
            if data is not None:
                with open(name, "wb") as sink:
                    sink.write(data)
            yield name
        finally:
            discard(name)

    def save_bytes(self, raw: str, source: Path, *, create: bool, action: str = "write") -> int:
        """
        Put the bytes of a staged file at a path, atomically, as the account.

        The file is written to a temporary name beside the destination, takes the
        mode of the file it replaces, and is renamed over it, so a reader never
        sees half a file.

        Args:
            raw: The destination, relative to the application.
            source: The staged file.
            create: Whether the file may be new; False requires it to exist.
            action: What the audit trail calls it.

        Returns:
            The bytes written.

        Raises:
            ValidationError: It is an environment file, a directory, or does not
                exist and ``create`` is False; or too large.
            SecurityError: A link is on the way, or the destination is one.
            ServiceError: The write failed; the file is as it was.
        """
        path = clean_path(raw, allow_empty=False)
        _refuse_env(path, "write")
        size = source.stat().st_size
        if size > UPLOAD_MAX_BYTES:
            raise ValidationError(
                f"That is {size} bytes: the console uploads up to "
                f"{UPLOAD_MAX_BYTES // 1024 // 1024} MiB",
                field="path",
            )
        destination = self.resolve(path)
        if destination.is_dir():
            raise ValidationError(f"{path} is a directory", field="path")
        parent = destination.parent
        if not parent.is_dir():
            raise ValidationError(f"{path.parent or '.'} does not exist", field="path")
        existed = os.path.lexists(destination)
        if not existed and not create:
            raise ValidationError(f"{path} does not exist", field="path")
        temporary = parent / f".noust-{_secrets.token_hex(6)}.part"
        self._run(
            ["dd", f"of={temporary}", "bs=64k", "status=none"],
            timeout=WRITE_TIMEOUT,
            stdin_path=source,
            what=f"write {path}",
        )
        try:
            if existed:
                self._run(
                    ["chmod", f"--reference={destination}", "--", str(temporary)],
                    timeout=LIST_TIMEOUT,
                    what=f"keep the mode of {path}",
                )
            self._run(
                ["mv", "-T", "--", str(temporary), str(destination)],
                timeout=LIST_TIMEOUT,
                what=f"put {path} in place",
            )
        except NoustError:
            self.runner.run(
                ["rm", "-f", "--", str(temporary)], timeout=LIST_TIMEOUT, user=self.account
            )
            raise
        self._audit(action, path, bytes=size, created=not existed)
        return size

    def save_text(self, raw: str, content: str, *, create: bool = False) -> int:
        """
        Save the text of a file the console edited.

        Args:
            raw: The file, relative to the application.
            content: Its new text.
            create: Whether the file may be new.

        Returns:
            The bytes written.

        Raises:
            ValidationError: See :meth:`save_bytes`; or larger than :data:`EDIT_MAX_BYTES`.
        """
        data = content.encode("utf-8")
        if len(data) > EDIT_MAX_BYTES:
            raise ValidationError(
                f"That is {len(data)} bytes: the console edits files up to "
                f"{EDIT_MAX_BYTES // 1024 // 1024} MiB",
                field="content",
            )
        with self.staged(data) as source:
            return self.save_bytes(raw, source, create=create)

    def make_directory(self, raw: str) -> None:
        """
        Create a directory whose parent exists.

        Args:
            raw: The directory, relative to the application.

        Raises:
            ValidationError: It exists, or its parent does not.
            SecurityError: A link is on the way.
            ServiceError: It could not be created.
        """
        path = clean_path(raw, allow_empty=False)
        _refuse_env(path, "create")
        target = self.resolve(path)
        if os.path.lexists(target):
            raise ValidationError(f"{path} exists already", field="path")
        if not target.parent.is_dir():
            raise ValidationError(f"{path.parent or '.'} does not exist", field="path")
        self._run(["mkdir", "--", str(target)], timeout=LIST_TIMEOUT, what=f"create {path}")
        self._audit("mkdir", path)

    def move(self, source_raw: str, destination_raw: str, *, overwrite: bool = False) -> None:
        """
        Rename or move a file or directory inside the application.

        Args:
            source_raw: What to move.
            destination_raw: Where it goes.
            overwrite: Replace what is there. Off, an existing destination is refused.

        Raises:
            ValidationError: Either is an environment file, the source does not
                exist, or the destination does and ``overwrite`` is off.
            SecurityError: A link is on the way (a link itself may be moved).
            ServiceError: The move failed.
        """
        source = clean_path(source_raw, allow_empty=False)
        destination = clean_path(destination_raw, allow_empty=False)
        for path in (source, destination):
            _refuse_env(path, "move")
        origin = self.resolve(source, leaf_may_be_link=True)
        goal = self.resolve(destination, leaf_may_be_link=True)
        if not os.path.lexists(origin):
            raise ValidationError(f"{source} does not exist", field="path")
        if os.path.lexists(goal) and not overwrite:
            raise ValidationError(
                f"{destination} exists already", details="Ask to replace it.", field="to"
            )
        if not goal.parent.is_dir():
            raise ValidationError(f"{destination.parent or '.'} does not exist", field="to")
        if str(goal).startswith(str(origin) + "/"):
            raise ValidationError("A directory cannot move into itself", field="to")
        self._run(
            ["mv", "-T", "--", str(origin), str(goal)],
            timeout=WRITE_TIMEOUT,
            what=f"move {source} to {destination}",
        )
        self._audit("move", source, to=str(destination))

    def remove(self, raw: str, *, recursive: bool = False) -> None:
        """
        Delete a file, a link, an empty directory, or a directory and what is in it.

        A link is removed itself, never what it points at.

        Args:
            raw: What to delete.
            recursive: Delete a directory with its contents.

        Raises:
            ValidationError: It is the application's directory, an environment file,
                missing, or a directory that is not empty and ``recursive`` is off.
            SecurityError: A link is on the way (a link itself may be removed).
            ServiceError: The deletion failed.
        """
        path = clean_path(raw, allow_empty=False)
        _refuse_env(path, "delete")
        target = self.resolve(path, leaf_may_be_link=True)
        if not os.path.lexists(target):
            raise ValidationError(f"{path} does not exist", field="path")
        if target.is_dir() and not target.is_symlink():
            if recursive:
                argv = ["rm", "-rf", "--one-file-system", "--", str(target)]
            else:
                argv = ["rmdir", "--", str(target)]
        else:
            argv = ["rm", "-f", "--", str(target)]
        self._run(argv, timeout=WRITE_TIMEOUT, what=f"delete {path}")
        self._audit("remove", path, recursive=recursive)


def files_for(
    domain: str,
    *,
    actor: str = "system",
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
) -> AppFiles:
    """
    Build the file operations of an application, refusing what has no files of its own.

    Args:
        domain: The application.
        actor: Who asked, for the audit trail.
        runner: Runs the commands.
        store: The store.

    Returns:
        The operations.

    Raises:
        NoustError: Nothing is deployed there.
        ValidationError: It is a stack or a container, whose files are the images' and volumes'.
    """
    app = (store or get_store()).get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    if app.app_type in ("docker-compose", "container"):
        raise ValidationError(
            f"{domain} runs in containers: its files are the images' and volumes'",
            details="Open a terminal in the service to look at them. The application's "
            "directory holds only the compose file and what Noust wrote.",
            field="domain",
        )
    return AppFiles(app, runner=runner, actor=actor)
