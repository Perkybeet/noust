# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The picture an operator chose for the sign-in screen (item 129).

Where Noust draws a noust from the side, an operator can show a company's logo or any picture of
its own. It is the one place the picture is kept, read and judged (rule 3 and rule 4): the console's
API and ``noust console sign-in-image`` are clients of :class:`SignInImage`, so what a person can
set, and what is checked before it is set, is the same from either.

What is kept, in :func:`~noust.core.paths.sign_in_image_dir`:

- the picture, **as :func:`noust.validators.sign_in_image.check_image` rewrote it** (pixels and
  nothing else), in a file named by its own digest, so a replacement is a new name and a cache can
  hold the old address for ever without ever being wrong;
- ``appearance.json``, which says which one is current, its type and size, how it is fitted
  (``cover`` fills the space, ``contain`` shows it whole) and the description a screen reader says in
  its place (empty: the picture is decoration).

Both are written through :mod:`noust.core.fs`, so a dry run writes nothing, and the picture goes
before the record that names it: a stop in between leaves the old record pointing at the old
picture. Reading never trusts what it finds: a record that does not parse, names something that is
not a digest, or whose file is not there, is no picture at all (the screen draws what it always
did), and the picture is served only if its bytes still hash to the digest it is asked for.

Every change is audited here (``auth.sign_in_image.set`` and ``.remove``), with the digest and the
settings and never the description, which is text a person typed.

The directory is in the state directory, which the backup of Noust's own state carries whole, so a
restored server shows the same screen: no list of files names it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from noust.core import audit, paths
from noust.core.exceptions import ValidationError
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.server_text import say
from noust.core.timeutil import utc_now_iso
from noust.validators import sign_in_image as rules

__all__ = ["RECORD_NAME", "Appearance", "SignInImage"]

logger = logging.getLogger(__name__)

#: The record's file name inside the directory.
RECORD_NAME = "appearance.json"

#: What audit says the picture is the target of.
TARGET = "console:sign-in-image"

#: The extension each type is kept under.
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

#: A picture's file name: its digest and its extension, which is all the directory holds besides the
#: record. Judged with ``fullmatch``.
_PICTURE = re.compile(r"[0-9a-f]{64}\.(?:png|jpg|webp)")
_DIGEST = re.compile(r"[0-9a-f]{64}")

#: One change at a time: two at once could each clean up the other's file.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Appearance:
    """
    The picture that is on the sign-in screen and how it is drawn.

    Attributes:
        sha256: The digest of the file kept, which is its address.
        content_type: ``image/png``, ``image/jpeg`` or ``image/webp``.
        width: Pixels across.
        height: Pixels down.
        size: Bytes of the file kept.
        fit: ``cover`` or ``contain``.
        alt: What a screen reader says in its place; empty for decoration.
        updated_at: When it was set, ISO 8601 in UTC.
    """

    sha256: str
    content_type: str
    width: int
    height: int
    size: int
    fit: str
    alt: str
    updated_at: str


def _integer(value: object, low: int, high: int) -> int | None:
    """
    Args:
        value: A field of the record.
        low: The least it may be.
        high: The most it may be.

    Returns:
        The number, when it is an integer in range (a flag is not one).
    """
    if type(value) is int and low <= value <= high:
        return value
    return None


def _parse(document: object) -> Appearance | None:
    """
    Read the record, trusting nothing in it.

    Args:
        document: What the file held, decoded.

    Returns:
        The appearance, or None for anything that is not exactly what :meth:`SignInImage.set`
        writes.
    """
    if not isinstance(document, dict) or set(document) != set(Appearance.__dataclass_fields__):
        return None
    sha, kind, fit = document["sha256"], document["content_type"], document["fit"]
    width = _integer(document["width"], 1, rules.MAX_SIDE)
    height = _integer(document["height"], 1, rules.MAX_SIDE)
    size = _integer(document["size"], 1, rules.MAX_IMAGE_BYTES)
    when = document["updated_at"]
    if (
        not isinstance(sha, str)
        or not _DIGEST.fullmatch(sha)
        or kind not in _EXTENSIONS
        or fit not in rules.FITS
        or width is None
        or height is None
        or size is None
        or not isinstance(when, str)
        or len(when) > 64
    ):
        return None
    try:
        alt = rules.check_alt(document["alt"])
    except ValidationError:
        return None
    return Appearance(sha, kind, width, height, size, fit, alt, when)


class SignInImage:
    """The picture on the sign-in screen: one place that keeps it, reads it and judges it."""

    def __init__(self, directory: Path | None = None, *, fs: FileSystem | None = None) -> None:
        """
        Args:
            directory: Where it is kept; :func:`noust.core.paths.sign_in_image_dir` by default.
            fs: The filesystem every change goes through; the process-wide one by default, which
                is the dry-run one under ``--dry-run``.
        """
        self._directory = directory if directory is not None else paths.sign_in_image_dir()
        self._fs = fs

    @property
    def directory(self) -> Path:
        """The directory the picture and its record are kept in."""
        return self._directory

    @property
    def fs(self) -> FileSystem:
        """The filesystem changes go through, resolved when first needed."""
        if self._fs is None:
            self._fs = get_fs()
        return self._fs

    def _file(self, appearance: Appearance) -> Path:
        """
        Args:
            appearance: A valid record: its digest is 64 hexadecimal digits, so this is a name.

        Returns:
            Where its picture is.
        """
        return self._directory / f"{appearance.sha256}.{_EXTENSIONS[appearance.content_type]}"

    def _record(self) -> Appearance | None:
        """
        Returns:
            What the record says, when it is a record; None when there is none or it is not one.
        """
        path = self._directory / RECORD_NAME
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("The sign-in image's record cannot be read, so none is shown: %s", exc)
            return None
        try:
            found = _parse(json.loads(text))
        except ValueError:
            found = None
        if found is None:
            logger.warning(
                "The sign-in image's record is not one this version wrote; none is shown"
            )
        return found

    def get(self) -> Appearance | None:
        """
        Say what the sign-in screen shows in place of the drawing.

        Returns:
            The picture and how it is drawn; None when there is none, which is the drawing: also
            when the record is damaged or its file is gone, because a screen that cannot be drawn
            is worse than one that is not branded.
        """
        found = self._record()
        if found is None:
            return None
        try:
            on_disk = self._file(found).stat().st_size
        except OSError:
            logger.warning("The sign-in image's file is gone, so none is shown")
            return None
        if on_disk != found.size:
            logger.warning("The sign-in image's file is not the one its record names")
            return None
        return found

    def read(self, sha256: str) -> tuple[bytes, str] | None:
        """
        Read the picture, for the console to serve it.

        Args:
            sha256: The digest it is asked for: the address the page was given.

        Returns:
            The bytes and their type; None when that is not the current picture or when the file
            no longer hashes to its digest. Never a picture under an address that is not its own:
            the address is cached for a year.
        """
        found = self.get()
        if found is None or sha256 != found.sha256:
            return None
        try:
            data = self._file(found).read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != found.sha256:
            logger.warning(
                "The sign-in image's file does not match its digest, so it is not served"
            )
            return None
        return data, found.content_type

    def set(self, data: bytes | None, *, fit: object, alt: object) -> Appearance:
        """
        Put a picture on the sign-in screen, or change how the one there is drawn.

        Args:
            data: The file. None keeps the picture that is there and changes only its fit and
                description.
            fit: ``cover`` or ``contain``.
            alt: What a screen reader says in its place; empty for decoration.

        Returns:
            What the screen shows now.

        Raises:
            ValidationError: The file is not a picture of the three types within the limits, the
                fit or the description is not valid, or ``data`` is None and there is no picture
                to change. Nothing is written for any of them.
        """
        chosen_fit = rules.check_fit(fit)
        chosen_alt = rules.check_alt(alt)
        clean = rules.check_image(data) if data is not None else None
        with _LOCK:
            if clean is None:
                current = self.get()
                if current is None:
                    raise ValidationError.from_text(
                        say("sign_in_image.no_image_yet"), field="image"
                    )
                appearance = replace(
                    current, fit=chosen_fit, alt=chosen_alt, updated_at=utc_now_iso("seconds")
                )
            else:
                appearance = Appearance(
                    sha256=clean.sha256,
                    content_type=clean.content_type,
                    width=clean.width,
                    height=clean.height,
                    size=clean.size,
                    fit=chosen_fit,
                    alt=chosen_alt,
                    updated_at=utc_now_iso("seconds"),
                )
                self.fs.make_dir(self._directory, mode=0o755)
                self.fs.write_bytes(self._file(appearance), clean.data, mode=0o644)
            self.fs.write_text(
                self._directory / RECORD_NAME,
                json.dumps(asdict(appearance), indent=2, sort_keys=True) + "\n",
                mode=0o644,
            )
            self._forget_other_pictures(appearance)
        self._audit("auth.sign_in_image.set", self._details(appearance))
        return appearance

    def remove(self) -> Appearance | None:
        """
        Take the picture off the sign-in screen, so the drawing comes back.

        Returns:
            What was there; None when nothing was, which is not an error.
        """
        with _LOCK:
            previous = self.get()
            self.fs.remove(self._directory / RECORD_NAME)
            self._forget_other_pictures(None)
        if previous is not None:
            self._audit("auth.sign_in_image.remove", {"sha256": previous.sha256})
        return previous

    def _forget_other_pictures(self, keep: Appearance | None) -> None:
        """
        Delete the pictures the record no longer names, so a replacement leaves one.

        Args:
            keep: The current picture, whose file stays; None for none.
        """
        keeping = self._file(keep).name if keep is not None else None
        try:
            names = [entry.name for entry in self._directory.iterdir()]
        except FileNotFoundError:
            return
        for name in names:
            if _PICTURE.fullmatch(name) and name != keeping:
                self.fs.remove(self._directory / name)

    @staticmethod
    def _details(appearance: Appearance) -> dict[str, Any]:
        """
        Args:
            appearance: What was set.

        Returns:
            What the audit trail keeps: which picture and how it is drawn. Never the description,
            which is text a person typed; only whether there is one.
        """
        return {
            "sha256": appearance.sha256,
            "content_type": appearance.content_type,
            "bytes": appearance.size,
            "width": appearance.width,
            "height": appearance.height,
            "fit": appearance.fit,
            "described": bool(appearance.alt),
        }

    @staticmethod
    def _audit(event: str, details: dict[str, Any]) -> None:
        """
        Record a change, unless the run is a rehearsal and nothing changed.

        Args:
            event: ``auth.sign_in_image.set`` or ``auth.sign_in_image.remove``.
            details: What the trail keeps.
        """
        if not is_rehearsal():
            audit.record(event, target=TARGET, details=details)
