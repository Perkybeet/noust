# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sealed secrets: Noust's secret files, encrypted at rest under a passphrase.

A central keeps the keys to every server it manages. On a NAS, whose disks
leave the house in a backup, a replacement or a theft, those keys should not
be readable from the disk alone. Sealing encrypts every file of the
:class:`~noust.core.secrets.SecretStore` with a key derived from a passphrase
the operator types when the central starts; the passphrase and the key are
never written anywhere, so a sealed store is only as readable as the
passphrase is guessable.

The construction uses only the standard library and ``openssl``, both
already present everywhere Noust runs:

- **Key derivation**: scrypt (``n=2**15, r=8, p=1``) over the passphrase and a
  32-byte random salt, 64 bytes out: 32 for encryption, 32 for
  authentication. The salt and parameters live in the seal header,
  ``secrets/.seal``, a name no secret can have.
- **Encryption**: AES-256-CBC by ``openssl enc``, with a fresh 8-byte salt per
  file from which openssl derives that file's key and IV (PBKDF2, one
  iteration: the input is already a uniformly random 256-bit key). The key
  reaches openssl in its environment (``-pass env:``), never in its argv,
  which every local user can read in ``ps``.
- **Authentication**: HMAC-SHA256 over the secret's name, the file salt and
  the ciphertext (encrypt-then-MAC), checked in constant time before openssl
  sees a byte. Binding the name means one sealed file moved over another is
  refused, not silently served as the other secret.
- **The header** carries its own HMAC, which is how a wrong passphrase is
  told apart from a damaged file: the passphrase is checked against it
  before anything is decrypted.

The derived keys are kept in this process's memory only, per secrets
directory, from :func:`unlock` until :func:`lock` or the end of the process.
A central started with a sealed store is *locked*: every read of a sealed
secret raises :class:`SecretsLockedError`, so nothing that needs a key (a
tunnel to a node) can start until the operator unlocks it.

Sealing and unsealing rewrite the files one by one and record where they are
in the header (``sealing``/``unsealing``), so an interrupted run is finished
by running the same command again. Outside those states a file stored in
clear in a sealed store is refused: it is either left over from an
interrupted run or put there by someone else.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets as random
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.exceptions import NoustError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner

logger = logging.getLogger(__name__)

#: The seal header's name inside the secrets directory. It starts with a dot,
#: which no secret's name may, so it can never collide with one.
SEAL_FILE = ".seal"

#: Prefix of a sealed file's content.
SEALED_PREFIX = "noust-sealed:v1:"

#: scrypt parameters: 32 MiB of memory and a fraction of a second per try.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
#: Enough for SCRYPT_N and SCRYPT_R (128 * r * n bytes) with headroom;
#: OpenSSL's own default ceiling is exactly 32 MiB and refuses these.
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 32
#: openssl enc's salt is eight bytes.
FILE_SALT_BYTES = 8

#: The shortest passphrase accepted. Everything a sealed store protects is as
#: strong as this, and scrypt only slows guessing down.
MIN_PASSPHRASE_LENGTH = 12

#: The environment variable openssl reads the key from.
KEY_ENV = "NOUST_SEAL_KEY"

#: How long one openssl run may take. A secret is a few kilobytes.
OPENSSL_TIMEOUT = 30

#: Header states. ``sealed`` is the only one in which a file in clear is an
#: error; the other two are an operation in progress.
STATE_SEALED = "sealed"
STATE_SEALING = "sealing"
STATE_UNSEALING = "unsealing"
_STATES = (STATE_SEALED, STATE_SEALING, STATE_UNSEALING)

_CIPHER = ("-aes-256-cbc", "-pbkdf2", "-iter", "1", "-md", "sha256")


class SealError(NoustError):
    """A sealed store cannot be read, written, sealed or unsealed as asked."""


class SecretsLockedError(SealError):
    """The secrets are sealed and this process has not been given the passphrase."""


class WrongPassphraseError(SealError):
    """The passphrase does not open this sealed store."""


@dataclass(frozen=True)
class SealKeys:
    """
    The two keys a passphrase derives. Never printed: ``repr`` hides them.

    Attributes:
        enc: 32 bytes handed to openssl to encrypt and decrypt.
        mac: 32 bytes for the HMAC over every sealed file and the header.
    """

    enc: bytes = field(repr=False)
    mac: bytes = field(repr=False)


@dataclass(frozen=True)
class SealHeader:
    """
    What ``secrets/.seal`` records.

    Attributes:
        salt: The scrypt salt.
        n: scrypt's cost parameter.
        r: scrypt's block size.
        p: scrypt's parallelism.
        state: ``sealed``, or an operation in progress.
        mac: HMAC of the fields above under the MAC key.
    """

    salt: bytes
    n: int
    r: int
    p: int
    state: str
    mac: str = ""

    def body(self) -> dict[str, Any]:
        """
        The authenticated fields, as they are written.

        Returns:
            A JSON-ready mapping without the MAC.
        """
        return {
            "format": "noust-seal",
            "version": 1,
            "state": self.state,
            "kdf": {"name": "scrypt", "n": self.n, "r": self.r, "p": self.p},
            "salt": self.salt.hex(),
        }

    def signed(self, keys: SealKeys) -> SealHeader:
        """
        Return this header with its MAC computed under a key.

        Args:
            keys: The keys the passphrase derived.

        Returns:
            A copy carrying the MAC.
        """
        return SealHeader(self.salt, self.n, self.r, self.p, self.state, _header_mac(self, keys))

    def with_state(self, state: str) -> SealHeader:
        """
        Return this header in another state, unsigned.

        Args:
            state: The new state.

        Returns:
            A copy; sign it before writing.
        """
        return SealHeader(self.salt, self.n, self.r, self.p, state)


@dataclass
class SealReport:
    """
    What a seal or unseal run did.

    Attributes:
        rewritten: Names of the files it encrypted or decrypted.
        already: How many were already in the target form.
    """

    rewritten: list[str] = field(default_factory=list)
    already: int = 0


# -- the keys in memory --------------------------------------------------------

_keyring: dict[str, SealKeys] = {}
_keyring_lock = threading.Lock()
_unlock_listeners: list[Callable[[], None]] = []


def _ring_key(root: Path) -> str:
    """
    Name a secrets directory in the keyring.

    Args:
        root: The secrets directory.

    Returns:
        Its absolute path, so two spellings of it share one entry.
    """
    return os.path.abspath(root)


def _keys_for(root: Path) -> SealKeys | None:
    """
    Return the keys this process holds for a secrets directory.

    Args:
        root: The secrets directory.

    Returns:
        The keys, or None when it is locked.
    """
    with _keyring_lock:
        return _keyring.get(_ring_key(root))


def _remember(root: Path, keys: SealKeys) -> None:
    """
    Keep the keys for a secrets directory in memory.

    Args:
        root: The secrets directory.
        keys: The derived keys.
    """
    with _keyring_lock:
        _keyring[_ring_key(root)] = keys


def lock(root: Path) -> None:
    """
    Forget the keys for a secrets directory; its sealed files read as locked.

    The decrypted copies handed to other programs (:func:`plaintext_copy_dir`)
    are removed too: a locked central keeps nothing readable.

    Args:
        root: The secrets directory.
    """
    with _keyring_lock:
        _keyring.pop(_ring_key(root), None)
    remove_plaintext_copies(root)


def remove_plaintext_copies(root: Path) -> None:
    """
    Remove every decrypted copy of a secrets directory's files.

    Called when the store is locked and when the server that made them shuts
    down: a copy outlives neither the keys nor the process that needed it.

    Args:
        root: The secrets directory.
    """
    copies = plaintext_copy_dir(root)
    if copies.is_dir() and not copies.is_symlink():
        get_fs().remove_tree(copies)


def plaintext_copy_dir(root: Path) -> Path:
    """
    Return where decrypted copies of a sealed store's files are put.

    ``ssh -i`` and ``UserKnownHostsFile`` want a path, and on a sealed store
    the file at the secret's path holds ciphertext. The copy goes to the
    user's runtime directory (``$XDG_RUNTIME_DIR``, else the temporary
    directory, a tmpfs in the container), which is memory-backed and private,
    under a name derived from the secrets directory so two stores never
    share copies.

    Args:
        root: The secrets directory.

    Returns:
        The directory (not created here).
    """
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    digest = hashlib.sha256(_ring_key(root).encode("utf-8")).hexdigest()[:16]
    return Path(base) / f"noust-{os.getuid()}" / digest


def ensure_private_dir(path: Path, fs: FileSystem) -> None:
    """
    Create a directory only this user can enter, refusing one someone planted.

    In a shared temporary directory another user can create the name first,
    as a directory of their own or a symlink to one; either would receive a
    private key.

    Args:
        path: The directory.
        fs: The filesystem seam.

    Raises:
        SealError: When it is a symlink, not owned by this user, or open to
            others.
    """
    fs.make_dir(path, mode=SECRET_DIR_MODE, parents=True)
    for directory in (path.parent, path):
        try:
            info = os.lstat(directory)
        except FileNotFoundError:
            # Rehearsal: the seam created nothing, and nothing will be written.
            return
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SealError(
                f"Refusing to put a decrypted secret in {directory}",
                details=(
                    "It is a symlink, belongs to another user, or others can enter it. "
                    "Remove it, or set XDG_RUNTIME_DIR to a private directory."
                ),
            )


def add_unlock_listener(listener: Callable[[], None]) -> None:
    """
    Be told when a sealed store is unlocked in this process.

    The fleet opens its tunnels from here: while the store is locked the node
    keys cannot be read, so nothing can be dialled until this fires.

    Args:
        listener: Called with no arguments after every successful unlock.
    """
    with _keyring_lock:
        if listener not in _unlock_listeners:
            _unlock_listeners.append(listener)


def remove_unlock_listener(listener: Callable[[], None]) -> None:
    """
    Stop telling a listener about unlocks.

    Args:
        listener: One added with :func:`add_unlock_listener`.
    """
    with _keyring_lock:
        if listener in _unlock_listeners:
            _unlock_listeners.remove(listener)


def _notify_unlocked() -> None:
    """Call every unlock listener; one that fails does not undo the unlock."""
    with _keyring_lock:
        listeners = list(_unlock_listeners)
    for listener in listeners:
        try:
            listener()
        except (NoustError, OSError) as exc:
            logger.error("A listener failed after the secrets were unlocked: %s", exc)


# -- derivation and the header ------------------------------------------------


def derive_keys(
    passphrase: str, salt: bytes, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P
) -> SealKeys:
    """
    Derive the encryption and MAC keys from a passphrase.

    Args:
        passphrase: What the operator typed.
        salt: The store's scrypt salt.
        n: scrypt's cost parameter.
        r: scrypt's block size.
        p: scrypt's parallelism.

    Returns:
        The two keys.
    """
    material = hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=64
    )
    return SealKeys(enc=material[:32], mac=material[32:])


def _canonical(value: dict[str, Any]) -> bytes:
    """
    Serialise a mapping the same way every time, for a MAC.

    Args:
        value: The mapping.

    Returns:
        Its canonical JSON bytes.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _header_mac(header: SealHeader, keys: SealKeys) -> str:
    """
    Authenticate a header's fields.

    Args:
        header: The header.
        keys: The derived keys.

    Returns:
        The hex HMAC-SHA256.
    """
    return hmac.new(
        keys.mac, b"noust-seal-header\0" + _canonical(header.body()), "sha256"
    ).hexdigest()


def header_path(root: Path) -> Path:
    """
    Return where a secrets directory's seal header is.

    Args:
        root: The secrets directory.

    Returns:
        The header's path.
    """
    return root / SEAL_FILE


def _read_no_follow(path: Path) -> str | None:
    """
    Read a file without following a symlink.

    Args:
        path: The file.

    Returns:
        Its text, or None when it does not exist.

    Raises:
        SealError: When it is a symlink or cannot be read.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SealError(
            f"Cannot read {path}",
            details=f"It is not a regular file Noust wrote ({exc.strerror}).",
        ) from exc
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        return handle.read()


def read_header(root: Path) -> SealHeader | None:
    """
    Read a secrets directory's seal header.

    Args:
        root: The secrets directory.

    Returns:
        The header, or None when the store is not sealed.

    Raises:
        SealError: When the header is there but damaged.
    """
    path = header_path(root)
    text = _read_no_follow(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
        kdf = data["kdf"]
        header = SealHeader(
            salt=bytes.fromhex(data["salt"]),
            n=int(kdf["n"]),
            r=int(kdf["r"]),
            p=int(kdf["p"]),
            state=str(data["state"]),
            mac=str(data["mac"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise SealError(
            "The seal header is damaged",
            details=(
                f"{path} is not a seal header Noust wrote ({exc}). Restore it from the "
                "backup of the data directory: without it the sealed secrets cannot be read."
            ),
        ) from exc
    if data.get("format") != "noust-seal" or kdf.get("name") != "scrypt":
        raise SealError(
            "The seal header is not one this Noust understands",
            details=f"{path} names format {data.get('format')!r}, kdf {kdf.get('name')!r}.",
        )
    if header.state not in _STATES:
        raise SealError(
            "The seal header is damaged", details=f"Unknown state {header.state!r} in {path}."
        )
    return header


def _write_header(root: Path, header: SealHeader, fs: FileSystem) -> None:
    """
    Write a signed seal header.

    Args:
        root: The secrets directory.
        header: The header, with its MAC.
        fs: The filesystem seam.
    """
    fs.write_text(
        header_path(root),
        json.dumps({**header.body(), "mac": header.mac}, indent=2, sort_keys=True) + "\n",
        mode=SECRET_MODE,
    )


def is_sealed(root: Path) -> bool:
    """
    Report whether a secrets directory is sealed (or being sealed or unsealed).

    Args:
        root: The secrets directory.

    Returns:
        True when it has a seal header.
    """
    return os.path.lexists(header_path(root))


def is_unlocked(root: Path) -> bool:
    """
    Report whether this process holds the keys for a secrets directory.

    Args:
        root: The secrets directory.

    Returns:
        True when :func:`unlock` succeeded here and :func:`lock` has not run.
    """
    return _keys_for(root) is not None


def _open(root: Path, passphrase: str) -> tuple[SealHeader, SealKeys]:
    """
    Check a passphrase against a sealed store's header.

    Args:
        root: The secrets directory.
        passphrase: The passphrase.

    Returns:
        The header and the keys it derives.

    Raises:
        SealError: When the store is not sealed or the header is damaged.
        WrongPassphraseError: When the passphrase does not match.
    """
    header = read_header(root)
    if header is None:
        raise SealError(
            "The secrets are not sealed",
            details="There is nothing to unlock. 'noust central seal' seals them.",
        )
    keys = derive_keys(passphrase, header.salt, n=header.n, r=header.r, p=header.p)
    if not hmac.compare_digest(_header_mac(header, keys), header.mac):
        raise WrongPassphraseError(
            "Wrong passphrase",
            details=(
                "It does not open these secrets. There is no way to recover a lost "
                "passphrase: the sealed secrets can only be replaced (re-enrol the nodes)."
            ),
        )
    return header, keys


def unlock(root: Path, passphrase: str) -> None:
    """
    Give this process the keys to a sealed store.

    Args:
        root: The secrets directory.
        passphrase: The passphrase it was sealed with.

    Raises:
        SealError: When the store is not sealed or the header is damaged.
        WrongPassphraseError: When the passphrase does not match.
    """
    _, keys = _open(root, passphrase)
    _remember(root, keys)
    _notify_unlocked()


# -- one value -----------------------------------------------------------------


def _mac(keys: SealKeys, name: str, file_salt: bytes, ciphertext: bytes) -> str:
    """
    Authenticate one sealed value.

    Args:
        keys: The derived keys.
        name: The secret's name, bound so files cannot be swapped.
        file_salt: The salt openssl derived this file's key and IV from.
        ciphertext: What openssl produced.

    Returns:
        The hex HMAC-SHA256.
    """
    message = b"noust-sealed:v1\0" + name.encode("utf-8") + b"\0" + file_salt + ciphertext
    return hmac.new(keys.mac, message, "sha256").hexdigest()


def _openssl(
    runner: CommandRunner, keys: SealKeys, argv_tail: list[str], data: str, *, what: str
) -> str:
    """
    Run ``openssl enc`` with the key in its environment.

    Args:
        runner: The command runner.
        keys: The derived keys; only ``enc`` reaches openssl.
        argv_tail: Everything after ``openssl enc``: direction, salt, codec.
        data: Standard input, always ASCII (base64).
        what: What is being done, for the error.

    Returns:
        Standard output, stripped.

    Raises:
        SealError: When openssl fails or prints nothing.
    """
    result = runner.run(
        ["openssl", "enc", *argv_tail],
        env={KEY_ENV: keys.enc.hex()},
        input=data,
        timeout=OPENSSL_TIMEOUT,
    )
    output = (result.stdout or "").strip()
    if not result.success or not output:
        raise SealError(
            f"openssl could not {what}",
            details="Check that openssl is installed: 'openssl version'.",
            output=(result.stderr or "").strip() or None,
        )
    return output


def seal_value(name: str, value: str, keys: SealKeys, runner: CommandRunner) -> str:
    """
    Encrypt and authenticate one secret.

    The plaintext is base64-encoded before openssl sees it, so the round trip
    through a text pipe is exact whatever bytes the secret holds.

    Args:
        name: The secret's name.
        value: Its plaintext.
        keys: The derived keys.
        runner: The command runner.

    Returns:
        The sealed text, to be written in place of the plaintext.

    Raises:
        SealError: When openssl fails.
    """
    file_salt = random.token_bytes(FILE_SALT_BYTES)
    plaintext = base64.b64encode(value.encode("utf-8")).decode("ascii")
    encoded = _openssl(
        runner,
        keys,
        ["-e", *_CIPHER, "-S", file_salt.hex(), "-pass", f"env:{KEY_ENV}", "-a", "-A"],
        plaintext + "\n",
        what=f"seal the secret {name}",
    )
    ciphertext = _b64decode(encoded, name)
    tag = _mac(keys, name, file_salt, ciphertext)
    return f"{SEALED_PREFIX}{file_salt.hex()}:{encoded}:{tag}"


def _b64decode(text: str, name: str) -> bytes:
    """
    Decode base64 that is part of a sealed value.

    Args:
        text: The base64 text.
        name: The secret's name, for the error.

    Returns:
        The bytes.

    Raises:
        SealError: When it is not base64.
    """
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SealError(
            f"The sealed secret {name} is damaged", details=f"It is not valid base64: {exc}"
        ) from exc


def is_sealed_value(text: str) -> bool:
    """
    Report whether a secret file's content is sealed.

    Args:
        text: The file's content.

    Returns:
        True when it carries the sealed prefix.
    """
    return text.startswith(SEALED_PREFIX)


def unseal_value(name: str, text: str, keys: SealKeys, runner: CommandRunner) -> str:
    """
    Authenticate and decrypt one sealed secret.

    Args:
        name: The secret's name, which the MAC binds.
        text: The sealed text.
        keys: The derived keys.
        runner: The command runner.

    Returns:
        The plaintext.

    Raises:
        SealError: When the file was altered, moved from another name, or
            openssl fails.
    """
    try:
        salt_hex, encoded, tag = text[len(SEALED_PREFIX) :].strip().split(":")
        file_salt = bytes.fromhex(salt_hex)
    except ValueError as exc:
        raise SealError(
            f"The sealed secret {name} is damaged",
            details="It does not have the salt, ciphertext and MAC a sealed secret has.",
        ) from exc
    ciphertext = _b64decode(encoded, name)
    if not hmac.compare_digest(_mac(keys, name, file_salt, ciphertext), tag):
        raise SealError(
            f"The sealed secret {name} failed its integrity check",
            details=(
                "The file was altered, damaged, or copied over from another secret's name. "
                "Restore it from the backup of the data directory, or replace the secret."
            ),
        )
    # openssl 3 omits its "Salted__" header when the salt is given; older ones
    # wrote it. Either form is accepted: with the header, openssl reads the
    # salt from it; without, it is passed.
    salt_args = [] if ciphertext.startswith(b"Salted__") else ["-S", salt_hex]
    output = _openssl(
        runner,
        keys,
        ["-d", *_CIPHER, *salt_args, "-pass", f"env:{KEY_ENV}", "-a", "-A"],
        encoded + "\n",
        what=f"open the sealed secret {name}",
    )
    try:
        return base64.b64decode(output, validate=True).decode("utf-8")
    except (binascii.Error, ValueError) as exc:
        raise SealError(
            f"openssl did not return the secret {name}",
            details="Its output was not what was sealed; check 'openssl version'.",
        ) from exc


# -- the store's side ------------------------------------------------------------


def _locked_error(name: str) -> SecretsLockedError:
    """
    Build the error a read or write of a locked store raises.

    Args:
        name: The secret's name.

    Returns:
        The error.
    """
    return SecretsLockedError(
        f"The secrets are sealed and locked; {name} cannot be used",
        details=(
            "Unlock them with the passphrase: in the console (the Unlock form), or "
            "'noust central unlock' on the central (docker exec -it noust noust central "
            "unlock in a container)."
        ),
    )


def encode_for_store(root: Path, name: str, value: str, runner: CommandRunner | None = None) -> str:
    """
    Turn a secret into what its file holds: itself, or sealed.

    Args:
        root: The secrets directory.
        name: The secret's name.
        value: Its plaintext.
        runner: The command runner; the process-wide one by default.

    Returns:
        The file content.

    Raises:
        SecretsLockedError: When the store is sealed and this process is
            locked: writing the value in clear would unseal it quietly.
        SealError: When sealing fails.
    """
    if not is_sealed(root):
        return value
    keys = _keys_for(root)
    if keys is None:
        raise _locked_error(name)
    return seal_value(name, value, keys, runner or get_runner())


def decode_from_store(root: Path, name: str, text: str, runner: CommandRunner | None = None) -> str:
    """
    Turn a secret file's content back into the secret.

    Args:
        root: The secrets directory.
        name: The secret's name.
        text: The file's content.
        runner: The command runner; the process-wide one by default.

    Returns:
        The plaintext.

    Raises:
        SecretsLockedError: When the value is sealed and this process is locked.
        SealError: When the value fails its check, when a sealed value is
            found with no seal header, or when a value is in clear in a
            store that is sealed.
    """
    header = read_header(root)
    if header is None:
        if is_sealed_value(text):
            raise SealError(
                f"The secret {name} is sealed but the seal header is missing",
                details=(
                    f"{header_path(root)} is gone. Restore it from the backup of the "
                    "data directory; the sealed secrets cannot be read without it."
                ),
            )
        return text
    if not is_sealed_value(text):
        if header.state == STATE_SEALED:
            raise SealError(
                f"The secret {name} is stored in clear in a sealed store",
                details=(
                    "An interrupted seal leaves this; 'noust central seal' finishes it. "
                    "If no seal was interrupted, someone else wrote the file: check it."
                ),
            )
        return text
    keys = _keys_for(root)
    if keys is None:
        raise _locked_error(name)
    return unseal_value(name, text, keys, runner or get_runner())


def _secret_files(root: Path) -> Iterator[tuple[str, Path]]:
    """
    Walk every secret file under a secrets directory.

    Args:
        root: The secrets directory.

    Yields:
        ``(name, path)`` for each secret, the header excluded.

    Raises:
        SealError: When a symlink is found: sealing never follows one.
    """
    if not root.is_dir():
        return
    for directory, subdirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for entry in [*subdirs, *files]:
            if (base / entry).is_symlink():
                raise SealError(
                    f"Refusing to seal through a symlink: {base / entry}",
                    details="Nothing in the secrets directory should be a link. Remove it.",
                )
        for entry in sorted(files):
            # No secret's name starts with a dot: this is the header, or a
            # temporary file an interrupted atomic write left behind.
            if entry.startswith("."):
                continue
            path = base / entry
            yield path.relative_to(root).as_posix(), path


def check_passphrase(passphrase: str) -> None:
    """
    Refuse a passphrase too short to protect anything.

    Args:
        passphrase: The candidate.

    Raises:
        SealError: When it is shorter than :data:`MIN_PASSPHRASE_LENGTH`.
    """
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise SealError(
            f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters",
            details="Several unrelated words make a long passphrase that is easy to type.",
        )


def seal_store(
    root: Path,
    passphrase: str,
    *,
    fs: FileSystem | None = None,
    runner: CommandRunner | None = None,
) -> SealReport:
    """
    Encrypt every secret file under a passphrase, or finish doing so.

    The header is written first, in the ``sealing`` state, so from then on no
    process without the key can write a secret in clear; each file is then
    replaced by its sealed form, and the header moves to ``sealed`` last. A
    run that stops half-way is finished by running it again with the same
    passphrase. On success this process holds the keys (it is unlocked).

    Args:
        root: The secrets directory.
        passphrase: The passphrase; at least :data:`MIN_PASSPHRASE_LENGTH`.
        fs: The filesystem seam; the process-wide one by default.
        runner: The command runner; the process-wide one by default.

    Returns:
        What was sealed.

    Raises:
        SealError: When it is already sealed, the passphrase is too short, a
            symlink is found or openssl fails.
        WrongPassphraseError: When finishing an interrupted seal with a
            different passphrase.
    """
    fs = fs or get_fs()
    runner = runner or get_runner()
    check_passphrase(passphrase)
    existing = read_header(root)
    if existing is not None:
        header, keys = _open(root, passphrase)
        if header.state == STATE_SEALED:
            raise SealError(
                "The secrets are already sealed",
                details="'noust central unseal' reverts it; unseal and seal again to "
                "change the passphrase.",
            )
    else:
        salt = random.token_bytes(SALT_BYTES)
        keys = derive_keys(passphrase, salt)
        header = SealHeader(salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, STATE_SEALING)
        fs.make_dir(root, mode=SECRET_DIR_MODE, parents=True)
    _write_header(root, header.with_state(STATE_SEALING).signed(keys), fs)

    report = SealReport()
    for name, path in list(_secret_files(root)):
        text = _read_no_follow(path) or ""
        if is_sealed_value(text):
            # Checked, not trusted: a file sealed under another passphrase or
            # altered since would otherwise surface later, far from here.
            unseal_value(name, text, keys, runner)
            report.already += 1
            continue
        fs.write_text(path, seal_value(name, text, keys, runner), mode=SECRET_MODE)
        report.rewritten.append(name)

    _write_header(root, header.with_state(STATE_SEALED).signed(keys), fs)
    _remember(root, keys)
    return report


def unseal_store(
    root: Path,
    passphrase: str,
    *,
    fs: FileSystem | None = None,
    runner: CommandRunner | None = None,
) -> SealReport:
    """
    Decrypt every secret file and remove the seal.

    The header moves to ``unsealing`` first, each file is written back in
    clear, and the header is removed last; an interrupted run is finished by
    running it again.

    Args:
        root: The secrets directory.
        passphrase: The passphrase it was sealed with.
        fs: The filesystem seam; the process-wide one by default.
        runner: The command runner; the process-wide one by default.

    Returns:
        What was unsealed.

    Raises:
        SealError: When it is not sealed, a file fails its check or openssl
            fails.
        WrongPassphraseError: When the passphrase does not match.
    """
    fs = fs or get_fs()
    runner = runner or get_runner()
    header, keys = _open(root, passphrase)
    _write_header(root, header.with_state(STATE_UNSEALING).signed(keys), fs)

    # Every file is opened before any is rewritten, so one that fails its
    # check stops the run with the store still sealed and intact.
    plaintexts: list[tuple[str, Path, str]] = []
    report = SealReport()
    for name, path in list(_secret_files(root)):
        text = _read_no_follow(path) or ""
        if not is_sealed_value(text):
            report.already += 1
            continue
        plaintexts.append((name, path, unseal_value(name, text, keys, runner)))
    for name, path, value in plaintexts:
        fs.write_text(path, value, mode=SECRET_MODE)
        report.rewritten.append(name)

    fs.remove(header_path(root), missing_ok=True)
    lock(root)
    return report
