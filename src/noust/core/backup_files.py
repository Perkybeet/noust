# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backups encrypted on this server's disk, and the one place they are opened (checklist H-08).

A backup on the disk that holds it is readable by whoever gets the disk, a snapshot of it, a
copy of ``/var/backups`` or a retired drive. Here a backup can be *sealed* the moment it is made:
the file keeps its name and becomes an envelope (:mod:`noust.core.ens.envelope`: scrypt,
AES-256-CBC by openssl, HMAC-SHA256 over the whole file, checked before openssl reads a byte), and
**nothing else in Noust opens a backup file but** :func:`open_backup` (rule 3: one implementation,
and the guard at the chokepoint, not in every reader). A file that is not sealed is returned as
it is, so a backup made before the option was turned on, one imported from elsewhere and one
downloaded from a destination that holds it in clear all go through the same door.

**The keys.** Each sealing key is a random passphrase in the secret store
(``backups/local-encryption/<id>``), and its id is written, in clear, in the envelope's label, so
rotating the key never strands an older backup: new backups are sealed with the current key, old
ones open with theirs. ``noust backup encryption show-key`` prints one so it can be kept
somewhere that is not this server; without it a sealed backup is gone with the secret store.

**What this protects, and what it does not.** The key lives in the secret store, on the same server
(root-only, and under :mod:`noust.core.sealing` when the store is sealed). It defends the backups
against being copied or read without the secret store: an exposed directory, a share, a snapshot or
a drive taken out of the machine *when the store is not on it or is sealed*. It does not defend
them against root on a running server, and it is not a substitute for encrypting the disk.

**What it costs.** Sealing writes the envelope beside the file and renames it into place (about
two extra copies of the file for a moment); opening decrypts into a private scratch directory under
the backups directory (0700, removed when the block ends). A backup is as large as it was, plus a
header of a few hundred bytes.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets as random
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core import audit, paths
from noust.core.ens.envelope import MAGIC, EnvelopeError, open_envelope, read_header, seal_file
from noust.core.exceptions import BackupError
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner
from noust.core.sealing import MIN_PASSPHRASE_LENGTH
from noust.core.secrets import SecretStore

logger = logging.getLogger(__name__)

#: What this thread has open: a sealed backup's path to its decrypted copy and how many nested
#: readers share it, so an operation that looks into the same archive five times decrypts it
#: once. And whether sealing is suspended for what this thread writes.
_local = threading.local()

__all__ = [
    "CURRENT_KEY",
    "KEY_PREFIX",
    "BackupKeys",
    "ResealOutcome",
    "is_sealed",
    "open_backup",
    "reseal",
    "seal_in_place",
    "sealing_suspended",
]

#: Where a sealing key is kept: ``backups/local-encryption/<id>``.
KEY_PREFIX = "backups/local-encryption"

#: The secret that names the key new backups are sealed with. Without it nothing is sealed.
CURRENT_KEY = f"{KEY_PREFIX}/current"

#: What an envelope's label starts with; the key's id follows.
LABEL_PREFIX = "noust-local:"

#: Where the decrypted copies of backups that are being used live, under the backups directory.
SCRATCH_DIR = ".open"

#: What a key's id looks like. Anything else in a label is not a key of ours, and in particular
#: not the name of another secret (``current`` lives beside the keys).
_KEY_ID = re.compile(r"[0-9a-f]{8}")

#: Random characters of a passphrase: 48 of 62 is about 285 bits.
PASSPHRASE_LENGTH = 48
_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass(frozen=True)
class KeyView:
    """
    A sealing key, without its value.

    Attributes:
        id: Its identifier, written in the label of every backup it sealed.
        current: Whether new backups are sealed with it.
    """

    id: str
    current: bool


class BackupKeys:
    """
    The keys backups on this server are sealed with.
    """

    def __init__(self, store: SecretStore | None = None) -> None:
        """
        Args:
            store: The secret store. The process-wide one when None.
        """
        self._store = store

    @property
    def store(self) -> SecretStore:
        """The secret store the keys are in."""
        if self._store is None:
            self._store = SecretStore()
        return self._store

    def current_id(self) -> str | None:
        """
        Returns:
            The id of the key new backups are sealed with, or None when nothing is sealed.
        """
        value = self.store.read(CURRENT_KEY)
        return value.strip() if value and value.strip() else None

    def enabled(self) -> bool:
        """
        Returns:
            Whether new backups are sealed.
        """
        return self.current_id() is not None

    def passphrase(self, key_id: str) -> str | None:
        """
        Args:
            key_id: A key's id.

        Returns:
            Its passphrase, or None when this server does not have it.
        """
        if _KEY_ID.fullmatch(key_id) is None:
            return None
        return self.store.read(f"{KEY_PREFIX}/{key_id}")

    def make_key(self) -> str:
        """
        Make a key and make it the one new backups are sealed with.

        Returns:
            Its id.
        """
        key_id = random.token_hex(4)
        passphrase = "".join(random.choice(_ALPHABET) for _ in range(PASSPHRASE_LENGTH))
        self.store.write(f"{KEY_PREFIX}/{key_id}", passphrase)
        self.store.write(CURRENT_KEY, key_id)
        audit.record("backup.encryption.key", target="backups:local", details={"key": key_id})
        return key_id

    def enable(self) -> tuple[str, bool]:
        """
        Turn sealing on for the backups made from now on.

        Returns:
            The current key's id and whether it was made now (False when sealing was already on:
            nothing is replaced, so turning it on twice never strands a backup).
        """
        current = self.current_id()
        if current is not None:
            return current, False
        return self.make_key(), True

    def rotate(self) -> str:
        """
        Make a new key for the backups made from now on; the old one stays and opens what it
        sealed.

        Returns:
            The new key's id.

        Raises:
            BackupError: When sealing is off, because there is nothing to rotate.
        """
        if not self.enabled():
            raise BackupError(
                "Backups are not sealed: there is no key to rotate",
                details="Turn sealing on first: noust backup encryption enable.",
            )
        return self.make_key()

    def reveal(self, key_id: str | None = None) -> tuple[str, str]:
        """
        Show a key so it can be kept somewhere that is not this server, and record that it was.

        The record names the key and never its value.

        Args:
            key_id: The key; the current one when None.

        Returns:
            The key's id and its value.

        Raises:
            BackupError: When no key is current and none was named, or this server holds no such
                key.
        """
        chosen = key_id or self.current_id()
        if chosen is None:
            raise BackupError(
                "No key is current",
                details="Seal backups first: noust backup encryption enable.",
            )
        passphrase = self.passphrase(chosen)
        if passphrase is None:
            raise BackupError(
                "This server holds no such key",
                details="List the keys it holds with: noust backup encryption status.",
            )
        audit.record("backup.encryption.reveal", target="backups:local", details={"key": chosen})
        return chosen, passphrase

    def import_key(self, key_id: str, passphrase: str) -> None:
        """
        Put a key back that this server lost: from the copy the operator kept elsewhere.

        Args:
            key_id: Its id, as the sealed backup's error names it.
            passphrase: Its value, as ``show-key`` printed it.

        Raises:
            BackupError: When the id is not a key's, the passphrase is too short to be one, or
                the key is already here with another value (a wrong id would otherwise replace a
                good key).
        """
        if _KEY_ID.fullmatch(key_id) is None:
            raise BackupError(
                f"{key_id!r} is not the id of a key",
                details="A key's id is eight lower-case hex digits, as the error of a backup "
                "that does not open names it.",
            )
        if len(passphrase) < MIN_PASSPHRASE_LENGTH:
            raise BackupError(
                "That is too short to be a key",
                details=f"Keys are {PASSPHRASE_LENGTH} characters; this has {len(passphrase)}.",
            )
        held = self.passphrase(key_id)
        if held is not None and held != passphrase:
            raise BackupError(
                f"This server already holds a key {key_id} with another value",
                details="Nothing was replaced. Check the id and the value you kept.",
            )
        self.store.write(f"{KEY_PREFIX}/{key_id}", passphrase)
        audit.record("backup.encryption.key", target="backups:local", details={"key": key_id})

    def stop_sealing(self) -> bool:
        """
        Stop sealing new backups. Every key is kept: what was sealed still opens.

        Returns:
            Whether anything was being sealed.
        """
        was = self.enabled()
        self.store.delete(CURRENT_KEY)
        if was:
            audit.record("backup.encryption.stop", target="backups:local")
        return was

    def keys(self) -> list[KeyView]:
        """
        Returns:
            Every key this server holds, the current one marked.
        """
        current = self.current_id()
        root = self.store.path(KEY_PREFIX)
        found: list[str] = []
        try:
            found = sorted(
                path.name for path in root.iterdir() if path.is_file() and path.name != "current"
            )
        except OSError:
            found = []
        return [KeyView(key_id, key_id == current) for key_id in found]


@contextmanager
def sealing_suspended() -> Iterator[None]:
    """
    Leave what this thread writes in clear, because something that is sealed whole contains it.

    A backup of an application carries the dumps of its databases inside an archive that is itself
    sealed; sealing each dump first would encrypt the same bytes twice and make the archive
    incompressible. The dumps are staged in a private directory and gone when the archive is made.

    Yields:
        Nothing; backups written in this block are not sealed.
    """
    before = getattr(_local, "suspended", 0)
    _local.suspended = before + 1
    try:
        yield
    finally:
        _local.suspended = before


def is_sealed(path: Path) -> bool:
    """
    Tell whether a file is an envelope.

    Args:
        path: The file.

    Returns:
        True when it starts with the envelope's magic line. A missing or unreadable file is not.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def sealed_with(path: Path) -> str | None:
    """
    Name the key a sealed backup was sealed with.

    Args:
        path: The file.

    Returns:
        The key's id, or None when it is not a backup sealed by this module.
    """
    if not is_sealed(path):
        return None
    try:
        label = str(read_header(path).get("label", ""))
    except EnvelopeError:
        return None
    return label.removeprefix(LABEL_PREFIX) if label.startswith(LABEL_PREFIX) else None


def seal_in_place(
    path: Path,
    *,
    keys: BackupKeys | None = None,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
) -> bool:
    """
    Seal a backup that was just made, keeping its name.

    The envelope is written beside the file and renamed over it, so the backup is never missing
    and never half sealed; and it is opened once with the key before the original goes, so a
    backup that cannot be read back is never the only copy.

    Args:
        path: The backup.
        keys: The keys. A default set when None.
        runner: The command runner for openssl. The process-wide one when None.
        fs: The filesystem seam. The process-wide one when None.

    Returns:
        True when the file is now sealed; False when sealing is off, the file is already
        sealed, or this is a rehearsal.

    Raises:
        BackupError: When the envelope cannot be written or does not open again; the file is
            left as it was.
    """
    keyring = keys or BackupKeys()
    if getattr(_local, "suspended", 0):
        return False
    key_id = keyring.current_id()
    if key_id is None or is_rehearsal() or is_sealed(path) or not path.is_file():
        return False
    passphrase = keyring.passphrase(key_id)
    if passphrase is None:
        raise BackupError(
            "Backups are to be sealed but the key is missing",
            details=f"The secret {KEY_PREFIX}/{key_id} is gone. Stop sealing "
            "(noust backup encryption disable) or make a new key (noust backup encryption "
            "rotate); the backup was left as it was.",
        )
    files = fs or get_fs()
    partial = path.with_name(f".{path.name}.sealing")
    try:
        seal_file(
            path, partial, passphrase, runner=runner, fs=files, label=f"{LABEL_PREFIX}{key_id}"
        )
        files.rename(partial, path)
    except (EnvelopeError, OSError) as exc:
        files.remove(partial)
        raise BackupError(
            f"Could not seal {path.name}",
            details=f"{str(getattr(exc, 'details', None) or exc).rstrip('.')}. "
            "The backup was left as it was.",
        ) from exc
    return True


@dataclass(frozen=True)
class ResealOutcome:
    """
    What became of one backup file when it was sealed after the fact.

    Attributes:
        state: ``sealed`` (it was in clear and is sealed now), ``repaired`` (it was sealed but
            its record still described the clear file, and now opens to exactly that),
            ``already`` (sealed and its record is current), ``would_seal`` and ``would_repair``
            (a rehearsal), or one that left it as it was: ``off`` (no key is current),
            ``missing``, ``damaged`` (it does not match its record), ``mismatch`` (sealed, and
            opens to something else), ``unreadable`` (its key is not here, or it does not
            open) and ``failed`` (sealing it did not work).
        size: The stored file's size, when it is described.
        sha256: Its SHA-256, when it is described.
        detail: What to tell the operator, for a state that left the file alone.
    """

    state: str
    size: int | None = None
    sha256: str | None = None
    detail: str = ""

    @property
    def changed(self) -> bool:
        """Whether the record of the file now has to be written."""
        return self.state in ("sealed", "repaired")


def _sha256(path: Path) -> str:
    """
    Args:
        path: A file.

    Returns:
        Its SHA-256, read in blocks (a backup is as large as the disk it is on).

    Raises:
        OSError: When it cannot be read.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sentence(error: BackupError) -> str:
    """
    Args:
        error: A backup error.

    Returns:
        Its message and details as one line a report can carry.
    """
    message = error.message.rstrip(".")
    return f"{message}. {error.details}".strip() if error.details else message


def reseal(
    path: Path,
    recorded_sha256: str | None,
    *,
    keys: BackupKeys | None = None,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
) -> ResealOutcome:
    """
    Seal a backup that was made in clear, and say what the stored file now is.

    Sealing changes the bytes that a backup's sidecar or record describes (size and checksum), so
    this is one step with the record: the clear file is checked against what was recorded first
    (a damaged backup is never sealed into something that looks sound), then sealed; and a file that
    is sealed but whose record still describes the clear one, because a run stopped between the two,
    is repaired once it opens to exactly the recorded checksum. The caller writes the record.

    Args:
        path: The backup.
        recorded_sha256: The checksum its record holds; None when it holds none.
        keys: The keys. A default set when None.
        runner: The command runner for openssl. The process-wide one when None.
        fs: The filesystem seam. The process-wide one when None.

    Returns:
        What became of it; see :class:`ResealOutcome`. Never raises for a file that cannot be
        sealed or opened: the state says why, so one such file does not stop a run over the rest.
    """
    if not path.is_file():
        return ResealOutcome("missing", detail=f"{path.name} is not there")
    keyring = keys or BackupKeys()
    try:
        digest = _sha256(path)
    except OSError as exc:
        return ResealOutcome("unreadable", detail=f"{path.name} cannot be read: {exc}")
    if not is_sealed(path):
        if not keyring.enabled():
            return ResealOutcome("off", detail="Backups are not sealed on this server")
        if recorded_sha256 and digest != recorded_sha256:
            return ResealOutcome(
                "damaged",
                detail=(
                    f"{path.name} does not match the checksum recorded when it was made "
                    f"({digest} now, {recorded_sha256} then); it was left in clear as it is"
                ),
            )
        if is_rehearsal():
            return ResealOutcome("would_seal")
        try:
            sealed = seal_in_place(path, keys=keyring, runner=runner, fs=fs)
        except BackupError as exc:
            return ResealOutcome("failed", detail=_sentence(exc))
        if not sealed:
            return ResealOutcome("off", detail="Backups are not sealed on this server")
        return ResealOutcome("sealed", path.stat().st_size, _sha256(path))
    if recorded_sha256 is None or digest == recorded_sha256:
        return ResealOutcome("already", path.stat().st_size, digest)
    if is_rehearsal():
        return ResealOutcome("would_repair")
    try:
        with open_backup(path, keys=keyring, runner=runner, fs=fs) as plain:
            opened = _sha256(plain)
    except (BackupError, OSError) as exc:
        return ResealOutcome(
            "unreadable", detail=_sentence(exc) if isinstance(exc, BackupError) else str(exc)
        )
    if opened != recorded_sha256:
        return ResealOutcome(
            "mismatch",
            detail=(
                f"{path.name} is sealed and does not open to what was recorded for it "
                f"({opened} opened, {recorded_sha256} recorded); nothing was changed"
            ),
        )
    return ResealOutcome("repaired", path.stat().st_size, digest)


def _scratch_root() -> Path:
    """
    Returns:
        The private directory decrypted copies are made in, beside the backups they came from.
    """
    return paths.backup_dir() / SCRATCH_DIR


@contextmanager
def open_backup(
    path: Path,
    *,
    keys: BackupKeys | None = None,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
) -> Iterator[Path]:
    """
    Open a backup file for reading: the one door every reader of a backup goes through.

    A file that is not sealed is yielded as it is. A sealed one is authenticated whole, then
    decrypted into a private scratch directory under the same name (so what is read from a name,
    ``.gz`` or ``.dump``, still works), and that copy is removed when the block ends, however it
    ends.

    Args:
        path: The backup.
        keys: The keys. A default set when None.
        runner: The command runner for openssl. The process-wide one when None.
        fs: The filesystem seam. The process-wide one when None.

    Yields:
        The path to read: ``path`` itself, or its decrypted copy.

    Raises:
        BackupError: When it is sealed with a key this server does not have, or does not open
            (altered, damaged), with the fix.
    """
    if not is_sealed(path) or is_rehearsal():
        yield path
        return
    open_now: dict[str, list[Any]] = _local.__dict__.setdefault("open", {})
    shared = open_now.get(str(path))
    if shared is not None:
        # Nested in a reader of the same backup: the copy that is already decrypted.
        shared[1] += 1
        try:
            yield shared[0]
        finally:
            shared[1] -= 1
        return
    keyring = keys or BackupKeys()
    key_id = sealed_with(path)
    passphrase = keyring.passphrase(key_id) if key_id else None
    if passphrase is None:
        raise BackupError(
            f"{path.name} is sealed and this server does not hold its key",
            details=(
                f"It was sealed with key {key_id or 'of another program'}. Put the key back with "
                f"'noust backup encryption import-key {key_id or '<id>'}' (the value "
                "'noust backup encryption show-key' printed when it was made, kept elsewhere), or "
                "restore from a copy that is not sealed."
            ),
        )
    files = fs or get_fs()
    root = _scratch_root()
    files.make_dir(root, mode=SECRET_DIR_MODE)
    files.set_owner(root, user="root", group="root", mode=SECRET_DIR_MODE)
    scratch = root / random.token_hex(8)
    files.make_dir(scratch, mode=SECRET_DIR_MODE)
    opened = scratch / path.name
    try:
        try:
            open_envelope(path, opened, passphrase, runner=runner, fs=files)
        except EnvelopeError as exc:
            raise BackupError(
                f"{path.name} does not open", details=exc.details or exc.message
            ) from exc
        open_now[str(path)] = [opened, 1]
        try:
            yield opened
        finally:
            del open_now[str(path)]
    finally:
        try:
            files.remove_tree(scratch)
        except OSError as exc:
            logger.warning("Could not remove the decrypted copy %s: %s", scratch, exc)


#: Directories of the backups directory whose files are not backups to count: scratch space, the
#: staging area, dumps waiting to be imported, and Noust's own state (its own envelopes).
_NOT_COUNTED = frozenset({SCRATCH_DIR, ".staging", "imports", "self", "tmp"})

#: The files a backup is stored as.
_BACKUP_SUFFIXES = (
    ".tar.gz",
    ".dump",
    ".sql",
    ".sql.gz",
    ".dump.gz",
    ".rdb",
    ".rdb.gz",
    ".archive",
)


def inventory(root: Path | None = None) -> dict[str, int]:
    """
    Count the backups on this server's disk that are sealed and the ones that are in clear.

    Args:
        root: The backups directory. The configured one when None.

    Returns:
        ``sealed`` and ``clear``: files with the names backups are stored under, two levels
        deep at most, not counting sidecars, scratch space and Noust's own state.
    """
    base = root or paths.backup_dir()
    sealed = clear = 0
    stack: list[tuple[Path, int]] = [(base, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name in _NOT_COUNTED or entry.is_symlink():
                continue
            if entry.is_dir():
                if depth < 3:
                    stack.append((entry, depth + 1))
            elif entry.name.endswith(_BACKUP_SUFFIXES):
                if is_sealed(entry):
                    sealed += 1
                else:
                    clear += 1
    return {"sealed": sealed, "clear": clear}
