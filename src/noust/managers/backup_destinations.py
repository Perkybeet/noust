# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Remote backup destinations, uploaded to with rclone (2.2).

A destination is a name (``noust backup destination add NAME --type ...``) for
an rclone remote Noust builds on the fly. rclone itself never sees a
credentials file: every option a remote needs travels as an
``RCLONE_CONFIG_<REMOTE>_<KEY>`` environment variable, built fresh for each
call from the non-secret :attr:`~noust.core.store.BackupDestinationRecord.settings`
column and the secret fields kept in :class:`~noust.core.secrets.SecretStore`
under ``backup-destinations/<name>``. Nothing is ever written to a
``rclone.conf`` on disk, and a password rclone expects obscured (an SFTP,
SMB or WebDAV password, a crypt passphrase) is obscured through ``rclone
obscure -`` on its stdin, never as an argument - the same rule
:mod:`noust.core.runner` enforces for every other secret.

Optional per-destination encryption wraps the remote in an rclone ``crypt``
backend, keyed by two passphrases generated with :func:`secrets.token_urlsafe`
and stored the same way. Losing them makes every backup on that destination
unrecoverable, which is why :meth:`BackupDestinationManager.show_key` exists:
an operator who wants a copy for safekeeping can print them once, deliberately,
through a sudo-mode API call or the CLI. The same key is accepted back by
:meth:`BackupDestinationManager.add` (``crypt_key``), which is how a
replacement server reads what the lost one encrypted; and removing an encrypted
destination is refused until the caller confirms the key was saved, because
the removal deletes the only copy Noust has.

**One folder per server.** Every backup's sidecar records which server took it
(:func:`~noust.managers.backup_manager.server_id`), and remote retention only
ever deletes this server's own backups. Two servers sharing a folder no longer
prune each other, but they still share it: give each server its own folder.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets as _secrets_module
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.core.dependencies import RCLONE_DEPENDENCY, dependency_install_hint
from noust.core.exceptions import BackupError, DependencyError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.redact import Scrubber
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import BackupDestinationRecord, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.managers.backup_manager import (
    ARCHIVE_SUFFIX,
    BACKUP_ID_PATTERN,
    BackupManager,
    BackupMetadata,
    backup_id_of_archive,
    server_id,
)
from noust.validators.names import validate_app_name

__all__ = [
    "BACKEND_FIELDS",
    "DEFAULT_REMOTE_PATH",
    "BackendField",
    "BackupDestinationManager",
    "backend_fields",
    "decode_sidecars",
    "parse_crypt_key",
    "validate_destination_name",
]

#: Folder under a remote that backups are written into when the operator did
#: not name one of their own.
DEFAULT_REMOTE_PATH = "wasm-backups"

#: Deadline for a quick rclone call: obscuring a password, deleting one file.
_RCLONE_TIMEOUT = 30

#: Deadline for listing a remote directory.
_RCLONE_LIST_TIMEOUT = 60

#: Deadline for copying an archive. Generous: a multi-gigabyte backup over a
#: slow link must not be judged by the same clock as a directory listing.
_TRANSFER_TIMEOUT = 3600

#: A destination's name is also the rclone remote name Noust builds for it, so
#: it is restricted to what safely survives being upper-cased into an
#: environment variable prefix (see :func:`_env_prefix`).
#: Always used with fullmatch: ``$`` also matches before a trailing newline.
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")

#: Directory under the backup directory a remote restore downloads into: the
#: backups' own filesystem, sized for archives, rather than a ``/tmp`` that is
#: often a small tmpfs. Hidden, and one level above the files, so no listing
#: of local backups ever mistakes a download in progress for a backup.
STAGING_DIR_NAME = ".remote-staging"

#: Space left free on top of the archive when downloading one.
_STAGING_HEADROOM = 64 * 1024 * 1024

#: One line of what ``noust backup destination show-key`` prints:
#: ``password:  <value>`` or ``password2: <value>``, after the logger's icon.
_KEY_LINE_RE = re.compile(r"(?<![A-Za-z0-9_])(password2?)\s*:\s*(\S+)\s*$")

#: Terminal colour codes a copied CLI output may still carry.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

#: rclone option names, per backend, that must be obscured (``rclone obscure
#: -``) before they reach an ``RCLONE_CONFIG_*_*`` variable. Only the option
#: types rclone itself declares as "password" need this; an S3 secret key or a
#: B2 application key are read as plain text by rclone's own config loader.
_OBSCURED_OPTIONS = frozenset({"pass", "crypt_password", "crypt_password2"})


@dataclass(frozen=True)
class BackendField:
    """
    One field a backend's destination form asks for.

    Attributes:
        key: The rclone option name (``pass``, ``access_key_id``...), or
            ``path`` for the one field every backend shares.
        label: Human label for the console's form and the CLI's ``--help``.
        secret: Stored in :class:`~noust.core.secrets.SecretStore`, never in
            :attr:`~noust.core.store.BackupDestinationRecord.settings`.
        required: Must be given when the destination is created.
        obscure: Passed through ``rclone obscure -`` before it reaches an
            environment variable, because rclone's own config loader expects
            it obscured (see :data:`_OBSCURED_OPTIONS`).
        placeholder: Example value shown in the console's form.
        help: One line of guidance, shown under the field.
        choices: Fixed set of values, when the field is a choice rather than
            free text; empty for free text.
    """

    key: str
    label: str
    secret: bool = False
    required: bool = True
    obscure: bool = False
    placeholder: str = ""
    help: str = ""
    choices: tuple[str, ...] = ()


#: One field every backend shares: where under the remote backups are written.
_PATH_FIELD = BackendField(
    key="path",
    label="Remote folder",
    required=False,
    placeholder=DEFAULT_REMOTE_PATH,
    help="Folder under the remote where backups are written; created if it does not exist. "
    "Give each server its own folder.",
)

#: Fields per backend, in the order a form should ask for them. This is the
#: one definition rclone's environment variables, the console's destination
#: form and ``noust backup destination add --help`` are all built from.
BACKEND_FIELDS: dict[str, list[BackendField]] = {
    "sftp": [
        BackendField("host", "Host", required=True, help="SSH server address."),
        BackendField("user", "User", required=True),
        BackendField("port", "Port", required=False, placeholder="22"),
        BackendField(
            "pass",
            "Password",
            secret=True,
            obscure=True,
            required=False,
            help="Leave blank when using a private key instead.",
        ),
        BackendField(
            "key_file",
            "Private key path",
            required=False,
            help="Path to an SSH private key readable by Noust, on this machine.",
        ),
    ],
    "smb": [
        BackendField("host", "Host", required=True),
        BackendField("user", "User", required=True),
        BackendField("pass", "Password", secret=True, obscure=True, required=True),
        BackendField("domain", "Domain", required=False, placeholder="WORKGROUP"),
    ],
    "webdav": [
        BackendField(
            "url", "URL", required=True, help="e.g. https://cloud.example.com/remote.php/webdav/"
        ),
        BackendField(
            "vendor",
            "Vendor",
            required=False,
            choices=("nextcloud", "owncloud", "sharepoint", "other"),
            placeholder="other",
        ),
        BackendField("user", "User", required=True),
        BackendField("pass", "Password", secret=True, obscure=True, required=True),
    ],
    "s3": [
        BackendField(
            "provider",
            "Provider",
            required=True,
            choices=(
                "AWS",
                "Cloudflare",
                "Backblaze",
                "Wasabi",
                "Minio",
                "Hetzner",
                "Scaleway",
                "Other",
            ),
        ),
        BackendField("access_key_id", "Access key ID", required=True),
        BackendField("secret_access_key", "Secret access key", secret=True, required=True),
        BackendField("region", "Region", required=False),
        BackendField(
            "endpoint", "Endpoint", required=False, help="Required for every provider except AWS."
        ),
    ],
    "b2": [
        BackendField("account", "Account ID", required=True),
        BackendField("key", "Application key", secret=True, required=True),
    ],
    "drive": [
        BackendField(
            "token",
            "Token",
            secret=True,
            required=True,
            help='Run "rclone authorize drive" on your own machine and paste the JSON it prints.',
        ),
    ],
    "onedrive": [
        BackendField(
            "token",
            "Token",
            secret=True,
            required=True,
            help='Run "rclone authorize onedrive" on your own machine and paste the JSON it prints.',
        ),
    ],
    "dropbox": [
        BackendField(
            "token",
            "Token",
            secret=True,
            required=True,
            help='Run "rclone authorize dropbox" on your own machine and paste the JSON it prints.',
        ),
    ],
    "pcloud": [
        BackendField(
            "token",
            "Token",
            secret=True,
            required=True,
            help='Run "rclone authorize pcloud" on your own machine and paste the JSON it prints.',
        ),
    ],
    "local": [],
}


def backend_fields(backend: str) -> list[BackendField]:
    """
    Return every field a backend's form asks for, including the shared ones.

    Args:
        backend: Backend name, a key of :data:`BACKEND_FIELDS`.

    Returns:
        The backend's own fields, then :data:`_PATH_FIELD`.

    Raises:
        BackupError: When ``backend`` is not one Noust knows.
    """
    try:
        own = BACKEND_FIELDS[backend]
    except KeyError as exc:
        raise BackupError(
            f"Unknown backup destination backend: {backend!r}",
            details=f"Use one of: {', '.join(sorted(BACKEND_FIELDS))}.",
        ) from exc
    return [*own, _PATH_FIELD]


def validate_destination_name(name: str) -> str:
    """
    Check a destination name is safe to become an rclone remote name.

    Args:
        name: Name as given by the operator.

    Returns:
        The name, unchanged.

    Raises:
        BackupError: When the name does not match the pattern.
    """
    if not _NAME_RE.fullmatch(name):
        raise BackupError(
            f"Invalid backup destination name: {name!r}",
            details="Use 1-32 characters: lowercase letters, digits and '-', starting with a "
            "letter or digit. The name also becomes an rclone remote name.",
        )
    return name


def _checked_crypt_key(password: object, password2: object) -> dict[str, str]:
    """
    Check both passphrases of a crypt key are present.

    Args:
        password: The first passphrase, as read.
        password2: The second passphrase (the salt), as read.

    Returns:
        ``{"password": ..., "password2": ...}``.

    Raises:
        BackupError: When either is missing or not text.
    """
    if not isinstance(password, str) or not password.strip():
        raise BackupError(
            "The encryption key has no password",
            details="Both passphrases 'noust backup destination show-key' printed are needed.",
        )
    if not isinstance(password2, str) or not password2.strip():
        raise BackupError(
            "The encryption key has no password2",
            details="Both passphrases 'noust backup destination show-key' printed are needed: "
            "password2 is the salt, and without it nothing decrypts.",
        )
    return {"password": password.strip(), "password2": password2.strip()}


def parse_crypt_key(text: str) -> dict[str, str]:
    """
    Read an encryption key back from what ``show-key`` printed.

    Accepted: the ``password:`` / ``password2:`` lines the CLI prints (with
    the logger's icon, colours included), its ``--json`` output, and the two
    passphrases on two lines, which is what the console's "Copy both" copies.

    Args:
        text: The key as pasted or piped in.

    Returns:
        ``{"password": ..., "password2": ...}``.

    Raises:
        BackupError: When no key can be read from the text.
    """
    stripped = _ANSI_RE.sub("", text).strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise BackupError("The encryption key is not valid JSON", details=str(exc)) from exc
        if not isinstance(data, dict):
            raise BackupError("The encryption key must be a JSON object")
        return _checked_crypt_key(data.get("password"), data.get("password2"))

    labelled: dict[str, str] = {}
    for line in stripped.splitlines():
        match = _KEY_LINE_RE.search(line)
        if match:
            labelled[match.group(1)] = match.group(2)
    if labelled:
        return _checked_crypt_key(labelled.get("password"), labelled.get("password2"))

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) == 2:
        return _checked_crypt_key(lines[0], lines[1])
    raise BackupError(
        "Could not read an encryption key",
        details="Give what 'noust backup destination show-key NAME' printed: its "
        "'password:' and 'password2:' lines, its --json output, or the two passphrases "
        "on two lines.",
    )


def _env_prefix(name: str) -> str:
    """
    Build the ``RCLONE_CONFIG_<...>_`` prefix for a destination's remote.

    Args:
        name: Destination name.

    Returns:
        The name, upper-cased. A dash stays a dash: rclone looks the remote
        ``it-local`` up as ``RCLONE_CONFIG_IT-LOCAL_*`` (checked against
        rclone 1.60 and 1.75), and turning it into an underscore made every
        destination with a dash in its name unreachable.
    """
    return name.upper()


def _remote_path(value: str | None) -> str:
    """
    Normalise a destination's folder without changing what it means.

    Only trailing slashes go. A leading one is kept: ``/srv/backups`` on a
    local or SFTP destination is absolute, and without the slash rclone
    reads it relative to the directory the command ran from (or the SFTP
    user's home).

    Args:
        value: The folder as given, or None.

    Returns:
        The folder, or :data:`DEFAULT_REMOTE_PATH` when none was given.
    """
    text = (value or "").strip()
    if not text:
        return DEFAULT_REMOTE_PATH
    return text.rstrip("/") or "/"


def _secret_namespace(name: str) -> str:
    """
    Return the :class:`~noust.core.secrets.SecretStore` name for a destination.

    Args:
        name: Destination name.

    Returns:
        The namespaced secret name.
    """
    return f"backup-destinations/{name}"


def _scrub(text: str, secret_values: list[str]) -> str:
    """
    Remove known secret values from rclone's own output before it is kept.

    Args:
        text: Text rclone printed - stdout or stderr.
        secret_values: Every raw secret value known for the destination.

    Returns:
        The text with each secret value replaced.
    """
    return Scrubber(values=secret_values).scrub(text)


def _hash_file(path: Path, hash_name: str) -> str | None:
    """
    Hash a local file the way rclone names a hash algorithm.

    Args:
        path: File to hash.
        hash_name: rclone's name for the algorithm (``md5``, ``sha1``...).

    Returns:
        The hex digest, or None when Python's own hashlib does not know an
        algorithm by that name - the hash check is skipped rather than
        treated as a failure, since the size check already ran.
    """
    try:
        digest = hashlib.new(hash_name)
    except (ValueError, TypeError):
        return None
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_RCLONE_TIME_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


def _parse_rclone_time(value: str) -> datetime:
    """
    Parse an rclone ``ModTime`` (RFC3339, often with nanosecond precision).

    Python 3.10's :meth:`datetime.fromisoformat` accepts neither a trailing
    ``Z`` nor more than six fractional digits, both of which rclone prints.

    Args:
        value: The timestamp as rclone's JSON gives it.

    Returns:
        The parsed, timezone-aware moment.

    Raises:
        ValueError: When the value does not match rclone's timestamp shape.
    """
    match = _RCLONE_TIME_RE.match((value or "").strip())
    if not match:
        raise ValueError(f"Unrecognised timestamp from rclone: {value!r}")
    frac = (match.group("frac") or "")[:6].ljust(6, "0")
    tz = match.group("tz") or "Z"
    if tz == "Z":
        tz = "+00:00"
    elif ":" not in tz:
        tz = f"{tz[:3]}:{tz[3:]}"
    return datetime.fromisoformat(f"{match.group('base')}.{frac}{tz}")


def decode_sidecars(text: str) -> list[dict[str, Any]]:
    """
    Split what ``rclone cat`` printed for a folder of ``.json`` sidecars.

    They are concatenated, one object after another, so the stream is decoded
    object by object rather than fetched one call per file.

    Args:
        text: The concatenated JSON objects.

    Returns:
        The objects read, in order. Everything after one that cannot be read
        is dropped: what it described is unknown, and callers treat an
        unknown origin as one to keep - the safe way to be wrong.
    """
    objects: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    position = 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            break
        try:
            data, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            break
        if isinstance(data, dict):
            objects.append(data)
    return objects


def _mod_time_or_min(entry: dict[str, Any]) -> datetime:
    """
    Read an entry's ``ModTime``, tolerating one rclone did not set.

    Args:
        entry: One ``rclone lsjson`` entry.

    Returns:
        The parsed moment, or the earliest possible one when it cannot be
        read - such an entry sorts last and is never kept over a real one.
    """
    try:
        return _parse_rclone_time(entry.get("ModTime", ""))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _require_rclone(runner: CommandRunner) -> None:
    """
    Refuse to go further when rclone is not installed.

    Args:
        runner: Runner used to probe for the executable.

    Raises:
        DependencyError: When ``rclone`` is not on PATH, naming how to
            install it on every distribution Noust supports.
    """
    if runner.exists("rclone"):
        return
    raise DependencyError(
        "rclone is required for backup destinations",
        details=dependency_install_hint(RCLONE_DEPENDENCY),
    )


class BackupDestinationManager:
    """
    Create, use and remove remote backup destinations.

    Args:
        runner: Runner every rclone and ``rclone obscure`` call goes through.
        secrets: Where a destination's secret fields are kept.
        store: The application store a destination's record lives in.
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        secrets: SecretStore | None = None,
        store: NoustStore | None = None,
    ) -> None:
        self._runner = runner
        self._secrets = secrets
        self._store = store

    @property
    def runner(self) -> CommandRunner:
        """The command runner every rclone call goes through."""
        return self._runner or get_runner()

    @property
    def secrets(self) -> SecretStore:
        """Where a destination's secret fields are kept."""
        return self._secrets or SecretStore()

    @property
    def store(self) -> NoustStore:
        """The application store a destination's record lives in."""
        return self._store or get_store()

    # -- catalogue ----------------------------------------------------------

    def backends(self) -> dict[str, list[BackendField]]:
        """
        Describe every backend's form, for the console and ``--help``.

        Returns:
            Every backend's fields, including the shared ``path`` field.
        """
        return {name: backend_fields(name) for name in BACKEND_FIELDS}

    def list_destinations(self) -> list[BackupDestinationRecord]:
        """
        List every configured destination.

        Returns:
            The destinations, by name.
        """
        return self.store.list_backup_destinations()

    def get(self, name: str) -> BackupDestinationRecord | None:
        """
        Look a destination up by name.

        Args:
            name: Destination name.

        Returns:
            The record, or None when there is no such destination.
        """
        return self.store.get_backup_destination(name)

    def configured_secret_fields(self, name: str) -> list[str]:
        """
        Name which of a backend's secret fields are set, without their values.

        What an API listing shows instead of a secret: never the value, only
        whether one is stored, so the console can render "configured" rather
        than a blank password field that looks unset.

        Args:
            name: Destination name.

        Returns:
            The secret field keys (from :func:`backend_fields`) that have a
            stored value, sorted.
        """
        destination = self._require(name)
        secret_values = self.secrets.read_json(_secret_namespace(name))
        secret_keys = {spec.key for spec in backend_fields(destination.backend) if spec.secret}
        return sorted(key for key in secret_keys if secret_values.get(key))

    def has_encryption_key(self, name: str) -> bool:
        """
        Report whether an encrypted destination's crypt passphrases exist.

        Args:
            name: Destination name.

        Returns:
            True when both passphrases are stored; also True for a
            destination that is not encrypted, since it needs none.
        """
        destination = self._require(name)
        if not destination.encrypted:
            return True
        secret_values = self.secrets.read_json(_secret_namespace(name))
        return bool(secret_values.get("crypt_password") and secret_values.get("crypt_password2"))

    def _require(self, name: str) -> BackupDestinationRecord:
        """
        Look a destination up, or refuse to continue.

        Args:
            name: Destination name.

        Returns:
            The record.

        Raises:
            BackupError: When there is no such destination.
        """
        destination = self.store.get_backup_destination(name)
        if destination is None:
            raise BackupError(
                f"No such backup destination: {name}",
                details="Run 'noust backup destination list' to see the destinations "
                "Noust knows about.",
            )
        return destination

    def require_encrypted_upload(self, name: str) -> BackupDestinationRecord:
        """
        Refuse an upload to an unencrypted destination when backups must leave encrypted.

        Every upload (an application's backup, a database dump) passes here
        before rclone runs (rule 4). The requirement is the ``ens-medium``
        profile's, or ``backup.encryption: required`` outside it
        (:func:`noust.core.ens.profile.backup_encryption_required`, ENS mp.si.2).

        Args:
            name: Destination name.

        Returns:
            The destination's record.

        Raises:
            BackupError: No such destination, or it is not encrypted and
                encryption is required.
        """
        from noust.core.ens.profile import backup_encryption_required

        destination = self._require(name)
        if destination.encrypted or not backup_encryption_required():
            return destination
        raise BackupError(
            f"{name} is not encrypted, and backups leave this server only encrypted",
            details=(
                "The ens-medium security profile (or backup.encryption: required) refuses "
                f"unencrypted uploads. Encrypt it: noust backup destination update {name} "
                f"--encrypt, then keep its key safe: noust backup destination show-key {name}."
            ),
        )

    # -- fields and secrets ---------------------------------------------

    def _split_fields(
        self, backend: str, fields: dict[str, str], *, partial: bool
    ) -> tuple[dict[str, str], dict[str, str]]:
        """
        Sort submitted fields into non-secret settings and secret values.

        Args:
            backend: The destination's backend.
            fields: Field values keyed by :attr:`BackendField.key`. An empty
                string is treated the same as an absent field.
            partial: True for an update, where an absent or blank field keeps
                whatever is already stored rather than being required.

        Returns:
            The non-secret settings and the secret values, each keyed by
            field name.

        Raises:
            BackupError: When a field name is not one the backend declares,
                or a required field is missing on a non-partial call.
        """
        specs = {spec.key: spec for spec in backend_fields(backend)}
        unknown = set(fields) - set(specs)
        if unknown:
            raise BackupError(
                f"Unknown field(s) for backend {backend!r}: {', '.join(sorted(unknown))}",
                details=f"Valid fields: {', '.join(sorted(specs))}.",
            )

        settings: dict[str, str] = {}
        secret_values: dict[str, str] = {}
        for key, spec in specs.items():
            raw = fields.get(key)
            if raw is None or raw == "":
                if spec.required and not partial:
                    raise BackupError(
                        f"Missing required field {key!r} for backend {backend!r}",
                        details=spec.help or f"{spec.label} is required.",
                    )
                continue
            if spec.secret:
                secret_values[key] = raw
            else:
                settings[key] = raw
        return settings, secret_values

    def _secret_literals(self, name: str) -> list[str]:
        """
        Return every raw secret value known for a destination.

        Used to redact rclone's recorded argv and to scrub its stderr before
        either is kept or shown.

        Args:
            name: Destination name.

        Returns:
            The values, in no particular order.
        """
        return [
            value for value in self.secrets.read_json(_secret_namespace(name)).values() if value
        ]

    def _obscure(self, raw: str) -> str:
        """
        Obscure a password the way rclone's own config loader expects it.

        Args:
            raw: The plain-text value.

        Returns:
            The obscured value, from ``rclone obscure -`` on its stdin - never
            as an argument, which is how a password used to end up in every
            local user's ``ps`` output.

        Raises:
            BackupError: When rclone refuses to obscure the value.
        """
        result = self.runner.run(["rclone", "obscure", "-"], input=raw, timeout=_RCLONE_TIMEOUT)
        if not result.success:
            raise BackupError(
                "rclone could not obscure a password",
                details=(result.stderr or result.stdout).strip(),
            )
        return result.stdout.strip()

    def _ensure_crypt_secrets(self, name: str, crypt_key: dict[str, str] | None = None) -> None:
        """
        Store the crypt passphrases for a destination: given, or generated once.

        Args:
            name: Destination name.
            crypt_key: An existing key (``{"password", "password2"}``) to use
                instead of generating one - the key of backups already on the
                destination. None generates whichever passphrase is missing.
        """
        namespace = _secret_namespace(name)
        secret_values = self.secrets.read_json(namespace)
        if crypt_key is not None:
            checked = _checked_crypt_key(crypt_key.get("password"), crypt_key.get("password2"))
            secret_values["crypt_password"] = checked["password"]
            secret_values["crypt_password2"] = checked["password2"]
            self.secrets.write_json(namespace, secret_values)
            return
        changed = False
        for key in ("crypt_password", "crypt_password2"):
            if not secret_values.get(key):
                secret_values[key] = _secrets_module.token_urlsafe(32)
                changed = True
        if changed:
            self.secrets.write_json(namespace, secret_values)

    # -- CRUD -------------------------------------------------------------

    def add(
        self,
        name: str,
        backend: str,
        fields: dict[str, str],
        *,
        encrypted: bool = False,
        crypt_key: dict[str, str] | None = None,
    ) -> BackupDestinationRecord:
        """
        Create a backup destination.

        Args:
            name: Destination name; also becomes its rclone remote name.
            backend: One of :data:`BACKEND_FIELDS`.
            fields: Field values keyed by rclone option name.
            encrypted: Wrap the remote in an rclone ``crypt`` backend, with
                generated passphrases only :meth:`show_key` ever prints.
            crypt_key: The key of backups already on the destination, as
                :meth:`show_key` returned it (see :func:`parse_crypt_key`),
                used instead of generating one: how a replacement server reads
                what the lost one encrypted. Requires ``encrypted``.

        Returns:
            The destination as stored.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the name or a field is invalid, the destination
                already exists, or a key is given for an unencrypted one.
        """
        _require_rclone(self.runner)
        validated_name = validate_destination_name(name)
        if self.store.get_backup_destination(validated_name) is not None:
            raise BackupError(f"Backup destination already exists: {validated_name}")
        if crypt_key is not None and not encrypted:
            raise BackupError(
                "An encryption key was given for a destination that is not encrypted",
                details="Add it with encryption on (--encrypt) to use an existing key.",
            )
        if crypt_key is not None:
            # Checked before anything is stored, so a bad key leaves nothing behind.
            _checked_crypt_key(crypt_key.get("password"), crypt_key.get("password2"))

        settings, secret_values = self._split_fields(backend, fields, partial=False)
        settings["path"] = _remote_path(settings.get("path"))

        self.secrets.write_json(_secret_namespace(validated_name), secret_values)
        if encrypted:
            self._ensure_crypt_secrets(validated_name, crypt_key)

        record = BackupDestinationRecord(
            name=validated_name, backend=backend, settings=settings, encrypted=encrypted
        )
        return self.store.save_backup_destination(record)

    def update(
        self, name: str, fields: dict[str, str], *, encrypted: bool | None = None
    ) -> BackupDestinationRecord:
        """
        Change a destination's fields, keeping whatever was left blank.

        Args:
            name: Destination name.
            fields: Field values to change; a blank or absent secret field
                keeps the value already stored.
            encrypted: Turn encryption on or off; None leaves it as it was.
                Turning it on for the first time generates its passphrases.

        Returns:
            The destination as stored.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When there is no such destination, or a field is
                invalid.
        """
        existing = self._require(name)
        _require_rclone(self.runner)

        settings, secret_values = self._split_fields(existing.backend, fields, partial=True)
        if "path" in settings:
            settings["path"] = _remote_path(settings["path"])

        if secret_values:
            namespace = _secret_namespace(name)
            current_secrets = self.secrets.read_json(namespace)
            current_secrets.update(secret_values)
            self.secrets.write_json(namespace, current_secrets)

        new_encrypted = existing.encrypted if encrypted is None else encrypted
        if new_encrypted and not existing.encrypted:
            self._ensure_crypt_secrets(name)

        record = BackupDestinationRecord(
            name=name,
            backend=existing.backend,
            settings={**existing.settings, **settings},
            encrypted=new_encrypted,
        )
        return self.store.save_backup_destination(record)

    def remove(self, name: str, *, force: bool = False, key_saved: bool = False) -> None:
        """
        Delete a destination and its secrets.

        Backups already sent there are not deleted. For an encrypted
        destination that means they stay behind readable only with its key,
        and this deletes the only copy Noust has of it - so the removal is
        refused until the caller says the key was saved (the CLI prints it and
        asks, the console shows it and asks).

        Args:
            name: Destination name.
            force: Remove it even when a schedule still references it,
                dropping the reference from every schedule that has it.
            key_saved: The operator has a copy of the encryption key. Ignored
                for a destination that is not encrypted or has no key left.

        Raises:
            BackupError: When there is no such destination, a schedule
                references it and ``force`` was not given, or it is encrypted
                and ``key_saved`` was not given.
        """
        destination = self._require(name)
        if destination.encrypted and not key_saved and self.has_encryption_key(name):
            raise BackupError(
                f"Backup destination {name!r} is encrypted: the backups already sent there "
                "can only be read with its key",
                details=f"Removing it deletes the only copy Noust has of that key. Run 'noust "
                f"backup destination show-key {name}' and keep what it prints, then remove it "
                "confirming the key was saved (key_saved=true in the API).",
            )

        referencing = [
            schedule.app_domain
            for schedule in self.store.list_backup_schedules()
            if any(destination.get("name") == name for destination in schedule.destinations)
        ]
        # A database's backup policy sends to destinations by name too: removing one it
        # names would leave the policy failing its upload every night.
        # Imported here: the databases package is not something every backup command loads.
        from noust.managers.database.backup_records import BackupRecords

        records = BackupRecords(self.store)
        policies = [
            policy
            for policy in records.policies()
            if any(destination.get("name") == name for destination in policy.destinations)
        ]
        used_by = sorted([*referencing, *(f"{p.engine}/{p.db_name}" for p in policies)])
        if used_by and not force:
            raise BackupError(
                f"Backup destination {name!r} is used by {len(used_by)} "
                f"schedule(s): {', '.join(used_by)}",
                details="Pass --force to remove it and drop the reference from those schedules.",
            )

        for domain in referencing:
            schedule = self.store.get_backup_schedule(domain)
            if schedule is None:
                continue
            schedule.destinations = [
                destination
                for destination in schedule.destinations
                if destination.get("name") != name
            ]
            self.store.save_backup_schedule(schedule)
        for policy in policies:
            policy.destinations = [
                destination
                for destination in policy.destinations
                if destination.get("name") != name
            ]
            records.save_policy(policy)

        self.store.delete_backup_destination(name)
        self.secrets.delete(_secret_namespace(name))

    def show_key(self, name: str) -> dict[str, str]:
        """
        Reveal a destination's encryption passphrases, for safekeeping.

        Args:
            name: Destination name.

        Returns:
            ``{"password": ..., "password2": ...}``.

        Raises:
            BackupError: When there is no such destination, it is not
                encrypted, or its keys are missing (which means every backup
                encrypted with them is already unrecoverable).
        """
        destination = self._require(name)
        if not destination.encrypted:
            raise BackupError(
                f"Backup destination {name!r} is not encrypted",
                details="There is no encryption key to show.",
            )
        secret_values = self.secrets.read_json(_secret_namespace(name))
        password = secret_values.get("crypt_password")
        password2 = secret_values.get("crypt_password2")
        if not password or not password2:
            raise BackupError(
                f"Encryption keys for {name!r} are missing",
                details="The destination is marked encrypted but its keys were not found; "
                "backups written to it cannot be decrypted without them.",
            )
        return {"password": password, "password2": password2}

    # -- rclone plumbing ----------------------------------------------------

    def remote_env(self, name: str) -> dict[str, str]:
        """
        Build the environment rclone needs to reach a destination.

        Args:
            name: Destination name.

        Returns:
            ``RCLONE_CONFIG_*`` variables for the destination's remote, and
            for its crypt wrapper when it is encrypted.

        Raises:
            BackupError: When the destination is encrypted but its
                passphrases are missing.
        """
        destination = self._require(name)
        prefix = _env_prefix(name)
        secret_values = self.secrets.read_json(_secret_namespace(name))

        env: dict[str, str] = {
            f"RCLONE_CONFIG_{prefix}_TYPE": destination.backend,
            # rclone's defaults retry an unreachable server for about a minute
            # and a half; a destination that is down should say so in seconds.
            # Flags as environment, so every command gets them.
            "RCLONE_RETRIES": "1",
            "RCLONE_CONTIMEOUT": "15s",
        }
        for spec in backend_fields(destination.backend):
            if spec.key == "path":
                continue
            if spec.secret:
                raw = secret_values.get(spec.key)
                if not raw:
                    continue
                value = self._obscure(raw) if spec.obscure else raw
            else:
                value = destination.settings.get(spec.key, "")
                if not value:
                    continue
            env[f"RCLONE_CONFIG_{prefix}_{spec.key.upper()}"] = value

        if destination.encrypted:
            crypt_prefix = f"{prefix}CRYPT"
            wrapped = f"{name}:{_remote_path(destination.settings.get('path'))}"
            password = secret_values.get("crypt_password")
            password2 = secret_values.get("crypt_password2")
            if not password or not password2:
                raise BackupError(
                    f"Backup destination {name!r} is marked encrypted but has no encryption key",
                    details="Recreate it, or turn encryption off: a crypt remote with no "
                    "password cannot be used.",
                )
            env[f"RCLONE_CONFIG_{crypt_prefix}_TYPE"] = "crypt"
            env[f"RCLONE_CONFIG_{crypt_prefix}_REMOTE"] = wrapped
            env[f"RCLONE_CONFIG_{crypt_prefix}_PASSWORD"] = self._obscure(password)
            env[f"RCLONE_CONFIG_{crypt_prefix}_PASSWORD2"] = self._obscure(password2)

        return env

    def target(self, name: str) -> str:
        """
        Return the rclone remote reference to use on the command line.

        Args:
            name: Destination name.

        Returns:
            ``<name>:<path>`` for a plain destination, or ``<name>crypt:`` for
            an encrypted one - the crypt wrapper already carries the path.
        """
        destination = self._require(name)
        if destination.encrypted:
            return f"{name}crypt:"
        return f"{name}:{_remote_path(destination.settings.get('path'))}"

    def _app_target(self, name: str, app_name: str) -> str:
        """
        Return where one application's backups live on a destination.

        The application name is validated here, where it becomes part of a
        remote path, rather than by each caller: ``../..`` from a query string
        must not list or download from outside the destination's folder.

        Args:
            name: Destination name.
            app_name: Application name.

        Returns:
            The remote reference of the application's own subdirectory.

        Raises:
            BackupError: When ``app_name`` is not a valid application name.
        """
        try:
            validated = validate_app_name(app_name)
        except ValidationError as exc:
            raise BackupError(
                f"Invalid application name: {app_name!r}",
                details="An application name is one path segment: letters, digits, '.', "
                "'_' and '-'.",
            ) from exc
        return f"{self.target(name).rstrip('/')}/{validated}"

    def _list_remote(
        self,
        env: dict[str, str],
        target_dir: str,
        secrets_literal: list[str],
        *,
        hashes: bool = True,
    ) -> list[dict[str, Any]]:
        """
        List a remote directory, tolerating one that does not exist yet.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: Remote reference to list.
            secrets_literal: Values to scrub from any error text.
            hashes: Ask for each file's hashes, which some backends compute
                by reading the file (SFTP, local): only upload verification
                needs them.

        Returns:
            The directory's entries; empty when it does not exist.

        Raises:
            BackupError: When rclone fails for any other reason, or answers
                with something that is not the JSON it promises.
        """
        argv = (
            ["rclone", "lsjson", "--hash", target_dir]
            if hashes
            else ["rclone", "lsjson", target_dir]
        )
        result = self.runner.run(
            argv,
            env=env,
            timeout=_RCLONE_LIST_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            combined = f"{result.stderr}\n{result.stdout}".lower()
            if "not found" in combined or "directory not found" in combined:
                return []
            raise BackupError(
                f"Could not list {target_dir}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise BackupError(
                f"rclone returned invalid JSON listing {target_dir}", details=str(exc)
            ) from exc
        return [entry for entry in data if isinstance(entry, dict)]

    # -- test ---------------------------------------------------------------

    def test(self, name: str) -> dict[str, Any]:
        """
        Check that a destination can be reached.

        Args:
            name: Destination name.

        Returns:
            ``{"ok": True, "entries": [...]}`` with the top-level entries
            found at the destination's own path.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the destination cannot be reached.
        """
        _require_rclone(self.runner)
        env = self.remote_env(name)
        secrets_literal = self._secret_literals(name)
        target = self.target(name)

        # The folder is created on first use, as its help promises; a test
        # of a destination nobody has pushed to yet must not fail on "not
        # found". mkdir is idempotent and is also the first real write, so a
        # read-only credential is caught here rather than at the first backup.
        created = self.runner.run(
            ["rclone", "mkdir", target],
            env=env,
            timeout=_RCLONE_LIST_TIMEOUT,
            secrets=secrets_literal,
        )
        if not created.success:
            raise BackupError(
                f"Could not reach backup destination {name!r}",
                details=_scrub((created.stderr or created.stdout).strip(), secrets_literal),
            )

        result = self.runner.run(
            ["rclone", "lsf", target, "--max-depth", "1"],
            env=env,
            timeout=_RCLONE_LIST_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            raise BackupError(
                f"Could not reach backup destination {name!r}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )
        return {"ok": True, "entries": [line for line in result.stdout.splitlines() if line]}

    # -- upload, verify, retention -------------------------------------

    def _verify_uploaded(self, local_file: Path, entries: list[dict[str, Any]]) -> str:
        """
        Confirm an uploaded file matches its local copy.

        Args:
            local_file: The file as it exists on this machine.
            entries: The destination directory's entries, from
                :meth:`_list_remote`.

        Returns:
            What the file was verified by: the name of the hash that matched,
            or ``"size"`` when the destination reports no hash Python can
            compute (every crypt remote: it cannot hash what it encrypts).

        Raises:
            BackupError: When the file was not found remotely, its size
                differs, a hash the backend reports differs, or the local
                file can no longer be read.
        """
        entry = next((e for e in entries if e.get("Name") == local_file.name), None)
        if entry is None:
            raise BackupError(
                f"Upload of {local_file.name} did not reach the destination",
                details="It was not listed there right after being copied.",
            )

        try:
            local_size = local_file.stat().st_size
            remote_size = entry.get("Size")
            if isinstance(remote_size, int) and remote_size != local_size:
                raise BackupError(
                    f"Size mismatch after uploading {local_file.name}",
                    details=f"Local file is {local_size} bytes; the destination reports "
                    f"{remote_size}.",
                )

            for hash_name, remote_hash in (entry.get("Hashes") or {}).items():
                if not remote_hash:
                    continue
                local_hash = _hash_file(local_file, hash_name)
                if local_hash is None:
                    continue
                if local_hash != remote_hash:
                    raise BackupError(
                        f"Hash mismatch after uploading {local_file.name}",
                        details=f"{hash_name} differs between the local file and the destination.",
                    )
                return str(hash_name)
        except OSError as exc:
            raise BackupError(
                f"Could not read {local_file} to verify its upload",
                details=f"{exc}. The local backup may have been deleted while it was sent.",
            ) from exc
        return "size"

    def _discard_upload(
        self,
        env: dict[str, str],
        target_dir: str,
        names: list[str],
        secrets_literal: list[str],
        failure: BackupError,
    ) -> BackupError:
        """
        Remove an upload that failed verification, so it never counts as a backup.

        A copy that does not match would otherwise stay on the destination,
        be listed and restorable like any other, and count toward retention -
        pushing a good backup out to make room for a bad one.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The application's own subdirectory on the destination.
            names: File names that were uploaded.
            secrets_literal: Values to scrub from any error text.
            failure: Why verification failed.

        Returns:
            The error to raise: the verification failure, saying whether the
            bad copy was removed, and rclone's own words when it was not.
        """
        leftovers: list[str] = []
        for name in names:
            result = self.runner.run(
                ["rclone", "deletefile", f"{target_dir}/{name}"],
                env=env,
                timeout=_RCLONE_TIMEOUT,
                secrets=secrets_literal,
            )
            if not result.success and "not found" not in (result.stderr or "").lower():
                leftovers.append(
                    f"{name}: " + _scrub((result.stderr or result.stdout).strip(), secrets_literal)
                )
        details = failure.details or ""
        if leftovers:
            details += (
                "\n\nThe copy that failed verification could not be removed from the "
                "destination either; delete it by hand before restoring anything from there:\n"
                + "\n".join(leftovers)
            )
        else:
            details += "\n\nThe copy that failed verification was removed from the destination."
        return BackupError(failure.message, details=details.strip())

    def _remote_origins(
        self, env: dict[str, str], target_dir: str, secrets_literal: list[str]
    ) -> dict[str, str | None]:
        """
        Read which server took each backup in an application's remote folder.

        One ``rclone cat`` over every sidecar in the folder: they are small,
        and each carries its own ``id``, so the concatenated output is split
        back into objects rather than fetched one call per file.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The application's own subdirectory on the destination.
            secrets_literal: Values to scrub from any error text.

        Returns:
            Backup id to the server id its sidecar records (None when it
            records none). A sidecar that cannot be read is absent, and a
            backup whose origin is unknown is never deleted by retention.

        Raises:
            BackupError: When rclone cannot read the folder.
        """
        result = self.runner.run(
            ["rclone", "cat", target_dir, "--include", "*.json", "--max-depth", "1"],
            env=env,
            timeout=_RCLONE_LIST_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            raise BackupError(
                f"Could not read the backup metadata in {target_dir}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )

        origins: dict[str, str | None] = {}
        for data in decode_sidecars(result.stdout or ""):
            if isinstance(data.get("id"), str):
                origin = data.get("origin")
                origins[data["id"]] = origin if isinstance(origin, str) else None
        return origins

    def _apply_remote_retention(
        self,
        env: dict[str, str],
        target_dir: str,
        entries: list[dict[str, Any]],
        retention_count: int | None,
        retention_days: int | None,
        secrets_literal: list[str],
    ) -> list[str]:
        """
        Delete this server's own old backups of one application on a destination.

        Only backups whose sidecar records this server's
        :func:`~noust.managers.backup_manager.server_id` are counted or
        deleted. Another server writing to the same folder, a backup taken
        before 2.2 and a backup whose sidecar is missing are all left alone:
        retention exists to bound this server's own usage, not to decide what
        someone else keeps.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The application's own subdirectory on the destination.
            entries: The folder's entries, listed after the upload.
            retention_count: Backups to keep, newest first; None for no limit.
            retention_days: Maximum age in days; None for no limit.
            secrets_literal: Values to scrub from any error text.

        Returns:
            The backup identifiers removed.

        Raises:
            BackupError: When rclone cannot read the sidecars or delete a
                file for a reason other than it already being gone.
        """
        if not retention_count and not retention_days:
            return []

        origins = self._remote_origins(env, target_dir, secrets_literal)
        own = server_id()
        by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            backup_id = backup_id_of_archive(Path(entry.get("Name", "")))
            if backup_id and origins.get(backup_id) == own:
                by_id[backup_id] = entry

        ordered = sorted(by_id.items(), key=lambda item: _mod_time_or_min(item[1]), reverse=True)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days) if retention_days else None
        )

        to_delete: list[str] = []
        for index, (backup_id, entry) in enumerate(ordered):
            beyond_count = retention_count is not None and index >= retention_count
            beyond_age = cutoff is not None and _mod_time_or_min(entry) < cutoff
            if beyond_count or beyond_age:
                to_delete.append(backup_id)

        for backup_id in to_delete:
            for suffix in (ARCHIVE_SUFFIX, ".json"):
                result = self.runner.run(
                    ["rclone", "deletefile", f"{target_dir}/{backup_id}{suffix}"],
                    env=env,
                    timeout=_RCLONE_TIMEOUT,
                    secrets=secrets_literal,
                )
                if not result.success and "not found" not in (result.stderr or "").lower():
                    raise BackupError(
                        f"Failed to remove {backup_id}{suffix} from the destination "
                        "during retention",
                        details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
                    )
        return to_delete

    def push(
        self,
        backup: BackupMetadata,
        destination_name: str,
        *,
        retention_count: int | None = None,
        retention_days: int | None = None,
        backup_manager: BackupManager | None = None,
    ) -> dict[str, Any]:
        """
        Copy a local backup to a destination, verify it, and apply retention.

        Args:
            backup: Metadata of the local backup to upload.
            destination_name: Destination to upload to.
            retention_count: This server's remote backups of this application
                to keep, newest first; None applies none.
            retention_days: Maximum age, in days, of this server's remote
                backups of this application; None applies none.
            backup_manager: Manager the local archive is read through;
                defaults to a fresh one.

        Returns:
            A summary: the files uploaded, what the archive was verified by
            (``verified_by``: a hash name, or ``"size"`` when the destination
            offers no hash), and the backup identifiers removed by retention.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the local archive is missing, the upload fails,
                verification fails (the bad copy is removed first), or
                retention cannot be applied.
        """
        _require_rclone(self.runner)
        self.require_encrypted_upload(destination_name)
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)

        manager = backup_manager or BackupManager(verbose=False)
        archive_path, metadata_path = manager.local_files(backup)
        if not archive_path.is_file():
            raise BackupError(f"Backup archive not found: {archive_path}")
        if not metadata_path.is_file():
            raise BackupError(f"Backup metadata not found: {metadata_path}")

        app_name = domain_to_app_name(backup.domain)
        target_dir = self._app_target(destination_name, app_name)
        uploaded = [archive_path.name, metadata_path.name]

        for position, local_file in enumerate((archive_path, metadata_path)):
            result = self.runner.run(
                ["rclone", "copyto", str(local_file), f"{target_dir}/{local_file.name}"],
                env=env,
                timeout=_TRANSFER_TIMEOUT,
                secrets=secrets_literal,
            )
            if not result.success:
                failure = BackupError(
                    f"Failed to upload {local_file.name} to {destination_name}",
                    details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
                )
                if position == 0:
                    raise failure
                # An archive with no sidecar is a backup nothing can restore
                # or attribute to this server: it is removed, not left behind.
                raise self._discard_upload(env, target_dir, uploaded, secrets_literal, failure)

        entries = self._list_remote(env, target_dir, secrets_literal)
        try:
            verified_by = self._verify_uploaded(archive_path, entries)
            self._verify_uploaded(metadata_path, entries)
        except BackupError as exc:
            raise self._discard_upload(env, target_dir, uploaded, secrets_literal, exc) from exc

        if verified_by == "size":
            Logger(verbose=False).warning(
                f"{destination_name} reports no hash Noust can compare for {archive_path.name}"
                " (an encrypted destination never does): the upload was verified by its size "
                "only."
            )

        try:
            deleted = self._apply_remote_retention(
                env, target_dir, entries, retention_count, retention_days, secrets_literal
            )
        except BackupError as exc:
            raise BackupError(
                f"{backup.id} was uploaded to {destination_name} and verified, but retention "
                "could not be applied there",
                details=exc.details or exc.message,
            ) from exc

        return {
            "destination": destination_name,
            "backup_id": backup.id,
            "uploaded": uploaded,
            "verified_by": verified_by,
            "retention_deleted": deleted,
        }

    # -- browsing and restoring ------------------------------------------

    def remote_list(self, destination_name: str, app_name: str | None = None) -> dict[str, Any]:
        """
        List what a destination holds.

        Args:
            destination_name: Destination to list.
            app_name: List that application's backups; without it, list the
                application directories found at the destination's own path.

        Returns:
            ``{"apps": [...]}`` when ``app_name`` is None, or
            ``{"backups": [...]}`` otherwise, newest first.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the destination cannot be listed.
        """
        _require_rclone(self.runner)
        self._require(destination_name)
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)

        if app_name is None:
            entries = self._list_remote(env, self.target(destination_name), secrets_literal)
            apps = sorted(entry.get("Name", "") for entry in entries if entry.get("IsDir"))
            return {"apps": apps, "backups": []}

        target_dir = self._app_target(destination_name, app_name)
        entries = self._list_remote(env, target_dir, secrets_literal)

        by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            name = entry.get("Name", "")
            backup_id = backup_id_of_archive(Path(name))
            if backup_id:
                by_id.setdefault(backup_id, {})["archive"] = entry
            elif name.endswith(".json"):
                stem = name[: -len(".json")]
                if BACKUP_ID_PATTERN.match(stem):
                    by_id.setdefault(stem, {})["metadata"] = entry

        backups = []
        for backup_id, parts in by_id.items():
            archive = parts.get("archive")
            backups.append(
                {
                    "backup_id": backup_id,
                    "app_name": app_name,
                    "size": archive.get("Size") if archive else None,
                    "modified": archive.get("ModTime") if archive else None,
                    "has_metadata": "metadata" in parts,
                }
            )
        backups.sort(key=lambda entry: entry.get("modified") or "", reverse=True)
        return {"apps": [], "backups": backups}

    def download(
        self,
        destination_name: str,
        backup_id: str,
        app_name: str,
        staging_dir: Path,
        *,
        fs: FileSystem | None = None,
    ) -> tuple[Path, Path]:
        """
        Download a backup's archive and sidecar, verifying both.

        Before anything is transferred the archive's size is read from the
        destination and compared with the space free where it is going: a
        restore that fills the disk half-way fails the machine along with it.
        After, the sidecar must describe this backup of this application -
        the domain it records is where a restore without a target goes, so a
        sidecar in the wrong folder must not decide that - and the archive
        must match the checksum it records.

        Args:
            destination_name: Destination to download from.
            backup_id: Backup identifier.
            app_name: Application the backup belongs to, which is also the
                folder it is read from.
            staging_dir: Directory to download into; created if missing.
                :meth:`restore_remote` uses one under the backup directory.
            fs: Filesystem the staging directory is created through.

        Returns:
            The downloaded archive path and metadata sidecar path.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the archive is not on the destination, there is
                not enough space for it, the download fails, the sidecar
                belongs to another backup or application, or the archive does
                not match the checksum recorded in its sidecar.
        """
        _require_rclone(self.runner)
        self._require(destination_name)
        target_dir = self._app_target(destination_name, app_name)
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)

        filesystem = fs or get_fs()
        filesystem.make_dir(staging_dir, mode=SECRET_DIR_MODE, parents=True)

        archive_name = f"{backup_id}{ARCHIVE_SUFFIX}"
        metadata_name = f"{backup_id}.json"
        archive_path = staging_dir / archive_name
        metadata_path = staging_dir / metadata_name

        self._check_staging_space(env, target_dir, archive_name, staging_dir, secrets_literal)

        for name, destination_path in (
            (archive_name, archive_path),
            (metadata_name, metadata_path),
        ):
            result = self.runner.run(
                ["rclone", "copyto", f"{target_dir}/{name}", str(destination_path)],
                env=env,
                timeout=_TRANSFER_TIMEOUT,
                secrets=secrets_literal,
            )
            if not result.success:
                raise BackupError(
                    f"Failed to download {name} from {destination_name}",
                    details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
                )
            if not destination_path.is_file():
                raise BackupError(
                    f"Download of {name} did not produce a file at {destination_path}"
                )

        try:
            sidecar = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError(
                f"Downloaded metadata for {backup_id} is unreadable", details=str(exc)
            ) from exc
        if not isinstance(sidecar, dict):
            raise BackupError(f"Downloaded metadata for {backup_id} is not a JSON object")

        if sidecar.get("id") != backup_id:
            raise BackupError(
                f"The metadata downloaded as {metadata_name} describes another backup",
                details=f"It names {sidecar.get('id')!r}. Nothing was restored.",
            )
        recorded_domain = sidecar.get("domain")
        if not isinstance(recorded_domain, str) or domain_to_app_name(recorded_domain) != app_name:
            raise BackupError(
                f"Backup {backup_id} on {destination_name} says it belongs to "
                f"{recorded_domain!r}, not to {app_name}",
                details="A backup is restored from the folder of the application it was taken "
                "from, and its metadata decides where a restore without a target goes, so the "
                "two must agree. Nothing was restored.",
            )

        expected_checksum = sidecar.get("checksum")
        if expected_checksum:
            actual = BackupManager(verbose=False).checksum_of(archive_path)
            if actual != expected_checksum:
                raise BackupError(
                    f"Checksum mismatch after downloading {backup_id} from {destination_name}",
                    details="The downloaded archive does not match the checksum recorded when "
                    "it was created. Restoring it would restore corrupted data.",
                )

        return archive_path, metadata_path

    def _check_staging_space(
        self,
        env: dict[str, str],
        target_dir: str,
        archive_name: str,
        staging_dir: Path,
        secrets_literal: list[str],
    ) -> None:
        """
        Refuse a download the staging directory has no room for.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The application's own subdirectory on the destination.
            archive_name: The archive about to be downloaded.
            staging_dir: Where it is going.
            secrets_literal: Values to scrub from any error text.

        Raises:
            BackupError: When the archive is not on the destination, or it
                would not fit.
        """
        entries = self._list_remote(env, target_dir, secrets_literal, hashes=False)
        entry = next((e for e in entries if e.get("Name") == archive_name), None)
        if entry is None:
            raise BackupError(
                f"{archive_name} was not found on the destination",
                details=f"Looked in {target_dir}. List what is there with "
                "'noust backup remote-list'.",
            )
        size = entry.get("Size")
        if not isinstance(size, int) or size < 0:
            return
        free = shutil.disk_usage(staging_dir).free
        if free < size + _STAGING_HEADROOM:
            raise BackupError(
                f"Not enough space to download {archive_name}",
                details=f"The archive is {size} bytes and {staging_dir} has {free} bytes free "
                f"(plus {_STAGING_HEADROOM} bytes kept spare). Free some space there first; "
                "nothing was downloaded.",
            )

    def restore_remote(
        self,
        destination_name: str,
        backup_id: str,
        app_name: str,
        *,
        target_domain: str | None = None,
        restore_env: bool = True,
        backup_manager: BackupManager | None = None,
    ) -> str:
        """
        Download a backup from a destination and restore it: the one remote restore.

        The download is staged under the backup directory
        (:data:`STAGING_DIR_NAME`), never in ``/tmp``, and removed afterwards
        whatever happens.

        Args:
            destination_name: Destination to download from.
            backup_id: Backup identifier.
            app_name: Application folder the backup is read from.
            target_domain: Domain to restore into. None restores into the
                domain the backup's metadata records, which :meth:`download`
                has checked belongs to ``app_name``.
            restore_env: Restore the ``.env`` files from the archive.
            backup_manager: Manager the restore runs through.

        Returns:
            The domain that was restored.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the download, its checks or the restore fail.
            AppBusyError: Another operation holds the target's lock.
        """
        manager = backup_manager or BackupManager(verbose=False)
        filesystem = manager.fs
        staging_root = manager.backup_dir / STAGING_DIR_NAME
        staging = staging_root / f"{backup_id}-{_secrets_module.token_hex(4)}"
        try:
            filesystem.make_dir(staging_root, mode=SECRET_DIR_MODE, parents=True)
            archive_path, metadata_path = self.download(
                destination_name, backup_id, app_name, staging, fs=filesystem
            )
            try:
                fallback = BackupMetadata.from_dict(json.loads(metadata_path.read_text()))
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise BackupError(
                    f"Downloaded metadata for {backup_id} is incomplete", details=str(exc)
                ) from exc
            domain = target_domain or fallback.domain
            manager.restore_archive(
                archive_path,
                target_domain=domain,
                restore_env=restore_env,
                expected_checksum=fallback.checksum,
                fallback=fallback,
            )
            return domain
        finally:
            try:
                filesystem.remove_tree(staging)
            except OSError as exc:
                Logger(verbose=False).warning(f"Could not remove the download at {staging}: {exc}")
