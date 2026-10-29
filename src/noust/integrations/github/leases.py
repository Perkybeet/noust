# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The access to GitHub that a central leases to a server it manages (item 90, D6).

The GitHub App lives on the central: its private key never leaves it. A server that deploys from a
repository of that App needs a credential to clone with, and cannot ask for one - it has nothing
that would let it call its central - so the central gives it one: an installation token scoped to
the repositories the server serves and to reading their contents, which lives an hour, renewed
before it ends (:mod:`noust.fleet.sources`). This module is what the server does with it.

- **It is a secret.** The token is a secret file sealed under the server's own key
  (:meth:`~noust.core.secrets.SecretStore.write_protected_json`); the table records which central
  leased it, for which repositories and until when, and never the token.
- **It ends on this server's clock.** The central says for how many seconds the token lives and the
  server counts them from when it arrived, so a clock that is off does not make the server trust a
  token that GitHub no longer does.
- **It covers what it was given.** The repositories and the host of the lease, nothing else: a
  clone of another repository is not helped by it.
- **A lease that ended is not used, and is remembered a while.** The error of a clone that fails
  for want of one says whose it was and when it ended (:func:`expired_note`), which is what a
  central that went quiet looks like from here.

Git receives it the way it receives every credential of Noust: in its environment, as the
``Authorization`` header of the host, never in argv, a URL, ``.git/config`` or a log
(:func:`noust.integrations.github.app.git_auth_environment`).
"""

from __future__ import annotations

import builtins
import hashlib
import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from noust.core.exceptions import (
    ConfigError,
    IntegrationError,
    NoustError,
    SourceError,
    ValidationError,
)
from noust.core.github_host import validate_host
from noust.core.runner import CommandResult
from noust.core.secrets import SecretStore
from noust.core.server_text import ServerText, say
from noust.core.store import NoustStore, get_store
from noust.validators.source import github_repository, github_repository_parts

logger = logging.getLogger(__name__)

__all__ = [
    "KEEP_AFTER_END",
    "LEASE_PERMISSIONS",
    "MAX_LIFETIME",
    "MAX_REPOSITORIES",
    "Lease",
    "SourceLeases",
    "expired_error",
    "expired_sentence",
    "target_of",
]

#: The longest a lease may live, in seconds. GitHub's installation tokens live an hour; a central
#: that says more is not telling the truth about one.
MAX_LIFETIME = 7200

#: Most repositories one lease covers. GitHub's own limit on a token's repositories is 500.
MAX_REPOSITORIES = 1000

#: How long an ended lease is kept so the failure of a clone can name it, in seconds.
KEEP_AFTER_END = 86400

#: The only permission a lease holds: reading what a clone needs.
LEASE_PERMISSIONS: Mapping[str, str] = {"contents": "read"}

_CENTRAL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,79}")
#: What GitHub's tokens are made of (``ghs_...``, or ``v1.`` and hex on an older Enterprise Server).
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,512}")

Clock = Callable[[], float]


def _now() -> float:
    """
    Returns:
        Epoch seconds. A function of the module, so that a test moves the time of every lease
        without handing a clock to each place that makes one.
    """
    return time.time()


@dataclass(frozen=True)
class Lease:
    """
    One token a central leased, as this server keeps it.

    Attributes:
        central: The central that leased it, as its fleet token names it.
        key: The central's name for this lease (host, installation and part), so a renewal
            replaces it.
        host: The GitHub host the token is for.
        installation_id: The installation it was issued from.
        repositories: ``owner/repo`` in lower case, sorted: all it reads.
        permissions: What the token may do on them.
        token_name: The secret file the token is in.
        received_at: When it arrived, on this server's clock.
        expires_at: When it stops being used, on this server's clock.
    """

    central: str
    key: str
    host: str
    installation_id: int
    repositories: tuple[str, ...]
    permissions: Mapping[str, str]
    token_name: str
    received_at: float
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The lease as the API shows it: everything but the token.
        """
        return {
            "central": self.central,
            "key": self.key,
            "host": self.host,
            "installation_id": self.installation_id,
            "repositories": list(self.repositories),
            "permissions": dict(self.permissions),
            "received_at": self.received_at,
            "expires_at": self.expires_at,
        }


def _lease(row: sqlite3.Row) -> Lease:
    """
    Args:
        row: A ``source_leases`` row.

    Returns:
        The lease.
    """
    return Lease(
        central=str(row["central"]),
        key=str(row["lease_key"]),
        host=str(row["host"]),
        installation_id=int(row["installation_id"]),
        repositories=tuple(json.loads(row["repos"])),
        permissions=dict(json.loads(row["permissions"])),
        token_name=str(row["token_name"]),
        received_at=float(row["received_at"]),
        expires_at=float(row["expires_at"]),
    )


def _checked_lifetime(value: object) -> int:
    """
    Args:
        value: Seconds the token lives, as the central said.

    Returns:
        Them.

    Raises:
        ValidationError: When they are not a whole number from 1 to :data:`MAX_LIFETIME`.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIFETIME:
        raise ValidationError(
            f"A lease lives a whole number of seconds from 1 to {MAX_LIFETIME}, not {value!r}",
            field="expires_in",
            details="GitHub's installation tokens live an hour; the central sends what is left.",
        )
    return value


def _checked_repositories(value: object) -> tuple[str, ...]:
    """
    Args:
        value: The repositories the lease covers.

    Returns:
        Them in lower case, sorted and without repeats.

    Raises:
        ValidationError: When it is not a list of 1 to :data:`MAX_REPOSITORIES` repositories.
    """
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
        raise ValidationError(
            "A lease covers a list of repositories",
            field="repositories",
            details="Name them owner/repo.",
        )
    if len(value) > MAX_REPOSITORIES:
        raise ValidationError(
            f"A lease covers at most {MAX_REPOSITORIES} repositories",
            field="repositories",
            details="The central sends several leases when a server serves more.",
        )
    found: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValidationError(
                "A repository is written owner/repo", field="repositories", details=f"Not: {item!r}"
            )
        owner, name = github_repository_parts(item)
        found.add(f"{owner}/{name}".lower())
    return tuple(sorted(found))


def _checked_host(value: object) -> str:
    """
    Args:
        value: The GitHub host the token is for.

    Returns:
        The host, lowercased.

    Raises:
        ValidationError: When it is not a host name.
    """
    try:
        host = validate_host(value)
    except ConfigError as exc:
        raise ValidationError(exc.message, field="host", details=exc.details) from exc
    if not host:
        raise ValidationError("A lease names the GitHub host its token is for", field="host")
    return host


def _checked_permissions(value: object) -> dict[str, str]:
    """
    Args:
        value: What the token may do; None for what a lease holds.

    Returns:
        :data:`LEASE_PERMISSIONS`.

    Raises:
        ValidationError: When it asks for anything else.
    """
    if value is None or value == dict(LEASE_PERMISSIONS):
        return dict(LEASE_PERMISSIONS)
    raise ValidationError(
        "A lease only reads the contents of repositories",
        field="permissions",
        details="Writing statuses and comments is the central's, with its own credentials.",
    )


class SourceLeases:
    """
    The leases this server holds, and the tokens in them.

    Args:
        store: This server's store; the process-wide one by default.
        secrets: Its secret store; the process-wide one by default.
        clock: Epoch seconds (tests move it).
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._given_clock = clock

    def _clock(self) -> float:
        """
        Returns:
            Epoch seconds now.
        """
        return (self._given_clock or _now)()

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def secrets(self) -> SecretStore:
        """The secret store."""
        return self._secrets or SecretStore()

    @staticmethod
    def _secret_name(central: str, key: str) -> str:
        """
        Args:
            central: The central.
            key: The lease's key.

        Returns:
            The name of the secret file, one per lease and never a path.
        """
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return f"leases/{central}.{digest}"

    def hold(
        self,
        *,
        central: str,
        key: str,
        host: str,
        installation_id: int,
        repositories: Sequence[str],
        token: str,
        expires_in: int,
        permissions: Mapping[str, str] | None = None,
    ) -> Lease:
        """
        Keep a token a central leased, replacing the one of the same key.

        Args:
            central: The central, as its fleet token names it.
            key: The central's name for this lease.
            host: The GitHub host the token is for.
            installation_id: The installation it was issued from.
            repositories: ``owner/repo`` it reads.
            token: The installation token.
            expires_in: Seconds it lives from now.
            permissions: What it may do; only reading contents is accepted.

        Returns:
            The lease, without its token.

        Raises:
            ValidationError: When any of it is not what a lease is; nothing is kept.
        """
        if not isinstance(central, str) or not _CENTRAL.fullmatch(central):
            raise ValidationError(
                f"Not a central's name: {central!r}",
                field="central",
                details="Lower-case letters, digits and '-'.",
            )
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValidationError(
                f"Not a lease's key: {key!r}",
                field="key",
                details="1 to 80 letters, digits or :._-, starting with a letter or digit.",
            )
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValidationError(
                "That is not an installation token",
                field="token",
                details="A token is up to 512 letters, digits and _.-; GitHub's start with ghs_.",
            )
        if (
            isinstance(installation_id, bool)
            or not isinstance(installation_id, int)
            or installation_id < 1
        ):
            raise ValidationError(
                f"Not an installation: {installation_id!r}", field="installation_id"
            )
        wanted_host = _checked_host(host)
        covered = _checked_repositories(repositories)
        allowed = _checked_permissions(permissions)
        lifetime = _checked_lifetime(expires_in)
        now = self._clock()
        name = self._secret_name(central, key)
        # The secret first: a lease whose token could not be kept leaves no row that points at it.
        self.secrets.write_protected_json(name, {"token": token})
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO source_leases (central, lease_key, host, installation_id, repos, "
                "permissions, token_name, received_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(central, lease_key) DO UPDATE SET host = excluded.host, "
                "installation_id = excluded.installation_id, repos = excluded.repos, "
                "permissions = excluded.permissions, token_name = excluded.token_name, "
                "received_at = excluded.received_at, expires_at = excluded.expires_at",
                (
                    central,
                    key,
                    wanted_host,
                    installation_id,
                    json.dumps(list(covered)),
                    json.dumps(allowed, sort_keys=True),
                    name,
                    now,
                    now + lifetime,
                ),
            )
        return Lease(
            central=central,
            key=key,
            host=wanted_host,
            installation_id=installation_id,
            repositories=covered,
            permissions=allowed,
            token_name=name,
            received_at=now,
            expires_at=now + lifetime,
        )

    def list(self) -> list[Lease]:
        """
        Returns:
            Every lease held, ended ones included, by central and key.
        """
        rows = self.store._get_connection().execute(
            "SELECT * FROM source_leases ORDER BY central, lease_key"
        )
        return [_lease(row) for row in rows.fetchall()]

    def token_of(self, lease: Lease) -> str:
        """
        Args:
            lease: A lease held.

        Returns:
            Its token.

        Raises:
            IntegrationError: When the secret file is gone or cannot be opened.
        """
        try:
            token = self.secrets.read_protected_json(lease.token_name).get("token")
        except NoustError as exc:
            raise IntegrationError(
                f"The token leased by {lease.central} cannot be opened",
                details=exc.message,
            ) from exc
        if not isinstance(token, str) or not token:
            raise IntegrationError(
                f"The token leased by {lease.central} is gone",
                details="The central leases another within minutes; until then nothing is cloned with it.",
            )
        return token

    def _covering(self, host: str, repository: str) -> builtins.list[Lease]:
        """
        Args:
            host: The GitHub host.
            repository: ``owner/repo``.

        Returns:
            The leases that cover it, ended or not.
        """
        wanted = repository.strip().lower()
        return [
            lease
            for lease in self.list()
            if lease.host == host.strip().lower() and wanted in lease.repositories
        ]

    def usable(self, host: str, repository: str) -> Lease | None:
        """
        Find the lease to clone a repository with.

        Args:
            host: The GitHub host of the repository.
            repository: ``owner/repo``.

        Returns:
            The lease that covers it and ends last; None when none has not ended.
        """
        now = self._clock()
        live = [lease for lease in self._covering(host, repository) if lease.expires_at > now]
        return max(live, key=lambda lease: lease.expires_at, default=None)

    def expired(self, host: str, repository: str) -> Lease | None:
        """
        Find the lease that would have cloned a repository if it had not ended.

        Args:
            host: The GitHub host of the repository.
            repository: ``owner/repo``.

        Returns:
            The ended lease that covered it and ended last; None when there is none or one
            has not ended.
        """
        now = self._clock()
        covering = self._covering(host, repository)
        if any(lease.expires_at > now for lease in covering):
            return None
        return max(covering, key=lambda lease: lease.expires_at, default=None)

    def drop(self, central: str, key: str | None = None) -> int:
        """
        Forget leases and their secrets.

        Args:
            central: The central whose leases go.
            key: Only this lease; every one of the central when None.

        Returns:
            How many were dropped.
        """
        held = [
            lease for lease in self.list() if lease.central == central and key in (None, lease.key)
        ]
        return self._forget(held)

    def purge(self) -> int:
        """
        Forget the leases that ended more than :data:`KEEP_AFTER_END` seconds ago.

        Returns:
            How many were forgotten.
        """
        limit = self._clock() - KEEP_AFTER_END
        return self._forget([lease for lease in self.list() if lease.expires_at < limit])

    def _forget(self, leases: Sequence[Lease]) -> int:
        """
        Args:
            leases: Leases held.

        Returns:
            How many were removed, with their secret files.
        """
        with self.store._transaction() as cursor:
            for lease in leases:
                cursor.execute(
                    "DELETE FROM source_leases WHERE central = ? AND lease_key = ?",
                    (lease.central, lease.key),
                )
        for lease in leases:
            self.secrets.delete(lease.token_name)
        return len(leases)

    def hosts(self) -> set[str]:
        """
        Returns:
            The hosts a lease is held for.
        """
        return {lease.host for lease in self.list()}


# -- What a clone makes of a lease -----------------------------------------------------------------


def target_of(url: str, hosts: Collection[str]) -> tuple[str, str] | None:
    """
    Name the lease host and the repository an https URL is.

    Args:
        url: What the program that clones is given.
        hosts: The hosts leases are held for.

    Returns:
        ``(host, owner/repo)``; None when the URL is not an https repository of one of the hosts.
    """
    parts = urlsplit(url.split("#", 1)[0].strip())
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    name = parts.hostname.lower()
    authority = f"{name}:{parts.port}" if parts.port else name
    for host in (authority, name):
        if host in hosts:
            repository = github_repository(url, hosts=(host,))
            if repository:
                return host, repository
    return None


def _stamp(moment: float) -> str:
    """
    Args:
        moment: Epoch seconds.

    Returns:
        The minute, the way a sentence says it.
    """
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def expired_sentence(url: str, leases: SourceLeases | None = None) -> ServerText | None:
    """
    Say why a repository cannot be cloned when the lease that covered it has ended.

    The failure of a clone is the words of the program that clones, "the repository is private",
    which is true of a repository nobody gave this server access to and not of one a central did
    and then went quiet.

    Args:
        url: The repository URL the clone was given.
        leases: The leases of this server; the process-wide ones by default.

    Returns:
        The sentence, or None when no lease that covered the repository has ended (or the leases
        cannot be read, which is logged: the clone's own error is still raised without it).
    """
    # Imported here: the note of a central's calls is the fleet's, which imports the store.
    from noust.fleet.central_calls import CentralCalls

    try:
        held = leases or SourceLeases()
        target = target_of(url, held.hosts())
        if target is None:
            return None
        lease = held.expired(*target)
        if lease is None:
            return None
        called = next(
            (call.last_call_at for call in CentralCalls().list() if call.central == lease.central),
            None,
        )
    except (NoustError, sqlite3.Error) as exc:
        logger.debug("Could not read the leases: %s", exc)
        return None
    values: dict[str, str] = {
        "central": lease.central,
        "repository": target[1],
        "ended_at": _stamp(lease.expires_at),
        "last_call": "never" if called is None else _stamp(called),
        "command": "noust app deploy-key --help",
    }
    if called is None or called < lease.expires_at:
        return say("sources.lease_expired_quiet", **values)
    return say("sources.lease_expired", **values)


def expired_error(url: str | None, result: CommandResult) -> SourceError | None:
    """
    Build the error of a clone that a lease which ended would have let through.

    Args:
        url: The repository URL, without credentials; None when it is not known.
        result: The failed command; its own words are kept beside the sentence.

    Returns:
        The error, or None when no ended lease covered the repository and the clone's usual
        error stands.
    """
    sentence = expired_sentence(url) if url else None
    if sentence is None:
        return None
    # Imported here: the manager that clones imports this package's credentials.
    from noust.managers.source_manager import redact_git_text

    return SourceError.from_text(sentence, output=redact_git_text(result.stderr or result.stdout))
