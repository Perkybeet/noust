# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Credentials Noust keeps for itself, one 0600 file each.

A GitHub App's private key, a backup destination's password, a fleet node's
token: none of them belongs in ``config.yaml``, which is read, printed and
edited, nor in a database column every query can reach. Each lives in a file
of its own under ``secrets/`` beside the store (``/var/lib/noust/secrets`` on a
server), created 0600 in a 0700 directory through the filesystem seam, so a
rehearsal writes nothing and there is never a moment when one is readable by
another user.

Names are namespaced (``github/private-key.pem``, ``backup-destinations/nas``)
and checked: a name is a relative path of simple segments, so no caller can
reach outside the directory, whatever it was handed.

Reading one never follows a symlink: a secret file an attacker replaced with
a link to ``/etc/shadow`` would otherwise be read, and possibly sent to a
remote API, as root.

A central may seal its secrets (:mod:`noust.core.sealing`): every file is then
encrypted under a passphrase. Callers do not change: :meth:`SecretStore.read`
and :meth:`SecretStore.write` decrypt and encrypt transparently while the
process holds the key, and raise
:class:`~noust.core.sealing.SecretsLockedError` while it does not. A caller
that hands a secret's *path* to another program (``ssh -i``) must read it
instead: on a sealed store the file holds ciphertext.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from noust.core import sealing
from noust.core.exceptions import ConfigError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner

# One segment: letters, digits, dot, dash, underscore; not starting with a
# dot, so neither ``..`` nor a hidden file can be named. Always used with
# fullmatch: ``$`` also matches before a trailing newline.
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")


def secrets_dir() -> Path:
    """
    Return the directory the secrets live in.

    Beside the store's database, so it follows the store wherever it is -
    the system location on a server, a temporary directory in a test.

    Returns:
        The ``secrets`` directory path (not created here).
    """
    from noust.core.store import get_store

    return get_store().db_path.parent / "secrets"


def _checked_name(name: str) -> tuple[str, ...]:
    """
    Split a secret's name into segments, refusing anything path-like.

    Args:
        name: ``namespace/key`` style name.

    Returns:
        The segments.

    Raises:
        ConfigError: The name is empty or a segment is not a simple name.
    """
    segments = tuple(name.split("/"))
    if not name or not all(_SEGMENT.fullmatch(segment) for segment in segments):
        raise ConfigError(
            f"Invalid secret name: {name!r}",
            details="A secret is named by simple segments separated by '/'.",
        )
    return segments


class SecretStore:
    """
    Read, write and delete Noust's own secret files.

    Args:
        root: Directory to keep them in; defaults to :func:`secrets_dir`.
        fs: Filesystem seam; defaults to the process-wide one.
        runner: Runs openssl for a sealed store; defaults to the process-wide
            one.
    """

    def __init__(
        self,
        root: Path | None = None,
        fs: FileSystem | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._root = root
        self._fs = fs
        self._runner = runner

    @property
    def root(self) -> Path:
        """The directory the secrets live in."""
        return self._root if self._root is not None else secrets_dir()

    @property
    def fs(self) -> FileSystem:
        """The filesystem writes go through."""
        return self._fs or get_fs()

    def path(self, name: str) -> Path:
        """
        Return where a secret is kept.

        Args:
            name: The secret's name.

        Returns:
            Its file path.

        Raises:
            ConfigError: The name is not a valid secret name.
        """
        return self.root.joinpath(*_checked_name(name))

    def write(self, name: str, value: str) -> None:
        """
        Store a secret, replacing any previous value atomically.

        Args:
            name: The secret's name.
            value: Its value.

        Raises:
            ConfigError: The name is not a valid secret name.
            SecretsLockedError: The store is sealed and this process is locked.
            SealError: Sealing the value failed.
        """
        path = self.path(name)
        content = sealing.encode_for_store(self.root, name, value, self._runner)
        self.fs.make_dir(path.parent, mode=SECRET_DIR_MODE, parents=True)
        self.fs.write_text(path, content, mode=SECRET_MODE)

    def read(self, name: str) -> str | None:
        """
        Read a secret.

        Args:
            name: The secret's name.

        Returns:
            Its value, or None when it was never stored.

        Raises:
            ConfigError: The name is invalid, or the entry is not a regular
                file (a symlink, say), which is refused rather than followed.
            SecretsLockedError: The secret is sealed and this process is locked.
            SealError: The sealed secret failed its integrity check.
        """
        path = self.path(name)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConfigError(
                f"Cannot read the secret {name}",
                details=f"{path} is not a regular file Noust wrote ({exc.strerror}).",
            ) from exc
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            text = handle.read()
        return sealing.decode_from_store(self.root, name, text, self._runner)

    def usable_path(self, name: str) -> Path:
        """
        Return a path another program can read the secret from.

        ``ssh -i`` and ``UserKnownHostsFile`` take a path. On a store that is
        not sealed that is the secret's own file. On a sealed one that file
        holds ciphertext, so the secret is decrypted into a private,
        memory-backed copy (:func:`~noust.core.sealing.plaintext_copy_dir`),
        rewritten on every call and removed when the store is locked.

        Args:
            name: The secret's name.

        Returns:
            The path to hand the other program.

        Raises:
            ConfigError: The name is not a valid secret name.
            SecretsLockedError: The store is sealed and this process is locked.
            SealError: The copy's directory is not private, or the secret
                failed its integrity check.
        """
        path = self.path(name)
        if not sealing.is_sealed(self.root):
            return path
        value = self.read(name)
        if value is None:
            # Absent: the program is handed the real path and reports it.
            return path
        copies = sealing.plaintext_copy_dir(self.root)
        target = copies.joinpath(*_checked_name(name))
        sealing.ensure_private_dir(copies, self.fs)
        self.fs.make_dir(target.parent, mode=SECRET_DIR_MODE, parents=True)
        self.fs.write_text(target, value, mode=SECRET_MODE)
        return target

    def delete(self, name: str) -> None:
        """
        Remove a secret; one that is not there is not an error.

        Args:
            name: The secret's name.
        """
        self.fs.remove(self.path(name), missing_ok=True)

    def delete_namespace(self, namespace: str) -> None:
        """
        Remove every secret under a namespace.

        Args:
            namespace: The first segment(s) of the names, such as ``github``.
        """
        path = self.path(namespace)
        if path.is_dir() and not path.is_symlink():
            self.fs.remove_tree(path)

    def write_json(self, name: str, value: dict[str, Any]) -> None:
        """
        Store a mapping of secrets as one JSON file.

        Args:
            name: The secret's name.
            value: The mapping.
        """
        self.write(name, json.dumps(value, sort_keys=True))

    def read_json(self, name: str) -> dict[str, Any]:
        """
        Read a mapping stored with :meth:`write_json`.

        Args:
            name: The secret's name.

        Returns:
            The mapping; empty when it was never stored.

        Raises:
            ConfigError: The file is there but is not a JSON object.
        """
        text = self.read(name)
        if text is None:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"The secret {name} is damaged",
                details=f"{self.path(name)} is not valid JSON: {exc}",
            ) from exc
        if not isinstance(value, dict):
            raise ConfigError(
                f"The secret {name} is damaged", details="It does not hold a JSON object."
            )
        return value
