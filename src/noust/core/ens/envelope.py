# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
File envelopes: a whole file encrypted and authenticated under a passphrase.

What a central's backup is wrapped in (``noust central backup``, ENS G12:
mp.si.2, mp.info.6): the copy of every key to the fleet must be unreadable and
tamper-evident once it leaves the machine, and restorable on another one with
nothing but the passphrase.

The construction is the one :mod:`noust.core.sealing` uses for single secrets,
applied to a file (rule 3), with no new dependency:

- **Key derivation**: scrypt over the passphrase and a random 32-byte salt,
  64 bytes out, 32 to encrypt and 32 to authenticate
  (:func:`noust.core.sealing.derive_keys`).
- **Encryption**: AES-256-CBC by ``openssl enc`` (:data:`noust.core.sealing.CIPHER_ARGS`),
  file to file, the key in openssl's environment and never in its argv.
- **Authentication**: HMAC-SHA256 over the magic line, the canonical header
  and the ciphertext (encrypt-then-MAC), checked in constant time before
  openssl reads a byte. A separate check value in the header tells a wrong
  passphrase apart from an altered file.

The file is ``NOUST-ENVELOPE 1``, a line of JSON (the header: KDF parameters,
salts, label, tag), then the ciphertext. Every file written is 0600.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core import sealing
from noust.core.exceptions import NoustError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner

#: The first line of every envelope.
MAGIC = b"NOUST-ENVELOPE 1\n"

#: scrypt parameters; the sealing's, so one passphrase costs the same to guess
#: wherever it is used. A module attribute so a test can make it cheap.
SCRYPT_N = sealing.SCRYPT_N
SCRYPT_R = sealing.SCRYPT_R
SCRYPT_P = sealing.SCRYPT_P

#: The environment variable openssl reads the key from.
KEY_ENV = sealing.KEY_ENV

#: How long encrypting or decrypting a large archive may take.
OPENSSL_TIMEOUT = 3600

_CHUNK = 1024 * 1024
_SALT_BYTES = 32
_FILE_SALT_BYTES = 8
_CHECK_MESSAGE = b"noust-envelope:v1:passphrase"


class EnvelopeError(NoustError):
    """An envelope cannot be written, read or opened as asked."""


@dataclass(frozen=True)
class EnvelopeInfo:
    """
    What was written.

    Attributes:
        path: The envelope.
        size: Its size in bytes.
        sha256: The SHA-256 of the whole file, for a manifest or an audit event.
        header: Its header.
    """

    path: Path
    size: int
    sha256: str
    header: dict[str, Any]


def _canonical(header: dict[str, Any]) -> bytes:
    return json.dumps(
        {key: value for key, value in header.items() if key != "tag"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _file_tag(keys: sealing.SealKeys, header: dict[str, Any], path: Path, offset: int) -> str:
    """
    HMAC-SHA256 over the magic line, the header and the ciphertext.

    Args:
        keys: The derived keys.
        header: The header, its tag left out.
        path: The file holding the ciphertext.
        offset: Where the ciphertext starts in it.

    Returns:
        The hex tag.
    """
    mac = hmac.new(keys.mac, MAGIC + _canonical(header), hashlib.sha256)
    with path.open("rb") as handle:
        handle.seek(offset)
        while chunk := handle.read(_CHUNK):
            mac.update(chunk)
    return mac.hexdigest()


def _check_value(keys: sealing.SealKeys) -> str:
    return hmac.new(keys.mac, _CHECK_MESSAGE, hashlib.sha256).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _scratch(directory: Path) -> tempfile.TemporaryDirectory[str]:
    """
    An owner-only scratch directory beside where the result will go.

    Beside it so the final rename stays on one filesystem; 0700, so the
    ciphertext and the plaintext in transit are nobody else's; removed with
    everything in it when the ``with`` block ends.

    Args:
        directory: The destination's directory.

    Returns:
        The temporary directory, to use as a context manager.
    """
    return tempfile.TemporaryDirectory(prefix=".noust-envelope-", dir=directory)


def _openssl(
    runner: CommandRunner, keys: sealing.SealKeys, argv_tail: list[str], *, what: str
) -> None:
    """
    Run ``openssl enc`` on files, the key in its environment.

    Args:
        runner: The command runner.
        keys: The derived keys; only ``enc`` reaches openssl.
        argv_tail: Everything after ``openssl enc``.
        what: What is being done, for the error.

    Raises:
        EnvelopeError: openssl failed; its output is attached verbatim.
    """
    result = runner.run(
        ["openssl", "enc", *argv_tail],
        env={KEY_ENV: keys.enc.hex()},
        timeout=OPENSSL_TIMEOUT,
    )
    if not result.success:
        raise EnvelopeError(
            f"openssl could not {what}",
            details="Check that openssl is installed: 'openssl version'.",
            output=(result.stderr or result.stdout or "").strip() or None,
        )


def seal_file(
    source: Path,
    destination: Path,
    passphrase: str,
    *,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    label: str = "",
) -> EnvelopeInfo:
    """
    Encrypt and authenticate a file under a passphrase.

    Args:
        source: The file to protect.
        destination: Where the envelope goes; replaced atomically, 0600.
        passphrase: At least :data:`noust.core.sealing.MIN_PASSPHRASE_LENGTH`
            characters; never written anywhere.
        runner: The command runner; the process's own by default.
        fs: The filesystem seam; the process's own by default.
        label: What the envelope holds, kept in clear in its header.

    Returns:
        What was written.

    Raises:
        EnvelopeError: The passphrase is too short, or openssl failed.
    """
    if len(passphrase) < sealing.MIN_PASSPHRASE_LENGTH:
        raise EnvelopeError(
            f"The passphrase must have at least {sealing.MIN_PASSPHRASE_LENGTH} characters",
            details="Everything the envelope protects is as strong as its passphrase.",
        )
    runner = runner or get_runner()
    fs = fs or get_fs()
    salt = secrets.token_bytes(_SALT_BYTES)
    file_salt = secrets.token_bytes(_FILE_SALT_BYTES)
    keys = sealing.derive_keys(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    fs.make_dir(destination.parent, mode=SECRET_DIR_MODE, parents=True)
    with _scratch(destination.parent) as scratch:
        ciphertext = Path(scratch) / "ciphertext"
        staged = Path(scratch) / "envelope"
        _openssl(
            runner,
            keys,
            [
                "-e",
                *sealing.CIPHER_ARGS,
                "-S",
                file_salt.hex(),
                "-pass",
                f"env:{KEY_ENV}",
                "-in",
                str(source),
                "-out",
                str(ciphertext),
            ],
            what=f"encrypt {source.name}",
        )
        if not ciphertext.is_file() or ciphertext.stat().st_size == 0:
            raise EnvelopeError(
                f"openssl produced no ciphertext for {source.name}",
                details="Check that openssl is installed and the disk is not full.",
            )
        header: dict[str, Any] = {
            "format": "noust-envelope",
            "version": 1,
            "label": label,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
            "salt": salt.hex(),
            "cipher": "aes-256-cbc",
            "file_salt": file_salt.hex(),
            "mac": "hmac-sha256",
            "check": _check_value(keys),
        }
        header["tag"] = _file_tag(keys, header, ciphertext, 0)
        with staged.open("wb") as out, ciphertext.open("rb") as body:
            out.write(MAGIC)
            out.write(json.dumps(header, sort_keys=True).encode("utf-8") + b"\n")
            while chunk := body.read(_CHUNK):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        fs.rename(staged, destination)
        fs.chmod(destination, SECRET_MODE)
    return EnvelopeInfo(
        path=destination,
        size=destination.stat().st_size,
        sha256=_sha256(destination),
        header=header,
    )


def _read(path: Path) -> tuple[dict[str, Any], int]:
    """
    Read an envelope's header.

    Args:
        path: The envelope.

    Returns:
        The header and where the ciphertext starts.

    Raises:
        EnvelopeError: It is not an envelope this version reads.
    """
    try:
        with path.open("rb") as handle:
            magic = handle.readline(len(MAGIC) + 1)
            line = handle.readline(64 * 1024) if magic == MAGIC else b""
            offset = handle.tell()
    except OSError as exc:
        raise EnvelopeError(f"Cannot read {path}: {exc}") from exc
    if magic != MAGIC:
        raise EnvelopeError(
            f"{path} is not a Noust envelope",
            details="It does not start with the envelope's first line.",
        )
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise EnvelopeError(f"The envelope {path} is damaged: its header is not JSON") from exc
    if not isinstance(header, dict) or header.get("format") != "noust-envelope":
        raise EnvelopeError(f"The envelope {path} is damaged: its header is not an envelope's")
    if header.get("version") != 1:
        raise EnvelopeError(
            f"{path} is an envelope of version {header.get('version')}",
            details="Open it with the Noust version that wrote it, or a newer one.",
        )
    return header, offset


def read_header(path: Path) -> dict[str, Any]:
    """
    Read what an envelope says about itself, without a passphrase.

    Args:
        path: The envelope.

    Returns:
        The header: label, creation time, parameters (no secret is in it).

    Raises:
        EnvelopeError: It is not an envelope this version reads.
    """
    return _read(path)[0]


def _authenticate(path: Path, passphrase: str) -> tuple[dict[str, Any], int, sealing.SealKeys]:
    """
    Check the passphrase, then every byte of an envelope.

    Args:
        path: The envelope.
        passphrase: Its passphrase.

    Returns:
        The header, the ciphertext's offset and the keys.

    Raises:
        EnvelopeError: Wrong passphrase, or the file was altered.
    """
    header, offset = _read(path)
    try:
        kdf = header["kdf"]
        salt = bytes.fromhex(str(header["salt"]))
        keys = sealing.derive_keys(
            passphrase, salt, n=int(kdf["n"]), r=int(kdf["r"]), p=int(kdf["p"])
        )
        check, tag = str(header["check"]), str(header["tag"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EnvelopeError(f"The envelope {path} is damaged: its header lacks {exc}") from exc
    if not hmac.compare_digest(_check_value(keys), check):
        raise EnvelopeError(
            f"The passphrase does not open {path.name}",
            details="Type the passphrase given when the backup was made; there is no other way in.",
        )
    if not hmac.compare_digest(_file_tag(keys, header, path, offset), tag):
        raise EnvelopeError(
            f"{path.name} failed its integrity check",
            details="The file was altered or damaged after it was written. Use another copy.",
        )
    return header, offset, keys


def verify_envelope(path: Path, passphrase: str) -> dict[str, Any]:
    """
    Prove an envelope opens and is intact, without decrypting it.

    Args:
        path: The envelope.
        passphrase: Its passphrase.

    Returns:
        Its header.

    Raises:
        EnvelopeError: Wrong passphrase, or the file was altered.
    """
    return _authenticate(path, passphrase)[0]


def open_envelope(
    source: Path,
    destination: Path,
    passphrase: str,
    *,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
) -> dict[str, Any]:
    """
    Authenticate an envelope, then decrypt it to a file.

    Nothing reaches openssl, and nothing is written, until every byte has
    passed the MAC.

    Args:
        source: The envelope.
        destination: Where the plaintext goes, 0600, replaced atomically.
        passphrase: Its passphrase.
        runner: The command runner; the process's own by default.
        fs: The filesystem seam; the process's own by default.

    Returns:
        The envelope's header.

    Raises:
        EnvelopeError: Wrong passphrase, altered file, or openssl failed.
    """
    header, offset, keys = _authenticate(source, passphrase)
    runner = runner or get_runner()
    fs = fs or get_fs()
    fs.make_dir(destination.parent, mode=SECRET_DIR_MODE, parents=True)
    with _scratch(destination.parent) as scratch:
        ciphertext = Path(scratch) / "ciphertext"
        staged = Path(scratch) / "plaintext"
        with source.open("rb") as handle, ciphertext.open("wb") as out:
            handle.seek(offset)
            first = handle.read(_CHUNK)
            salted = first.startswith(b"Salted__")
            out.write(first)
            while chunk := handle.read(_CHUNK):
                out.write(chunk)
        # openssl 3 writes no "Salted__" header when the salt is given; an
        # older one did, and then reads the salt from it.
        salt_args = [] if salted else ["-S", str(header.get("file_salt", ""))]
        _openssl(
            runner,
            keys,
            [
                "-d",
                *sealing.CIPHER_ARGS,
                *salt_args,
                "-pass",
                f"env:{KEY_ENV}",
                "-in",
                str(ciphertext),
                "-out",
                str(staged),
            ],
            what=f"decrypt {source.name}",
        )
        fs.rename(staged, destination)
        fs.chmod(destination, SECRET_MODE)
    return header
