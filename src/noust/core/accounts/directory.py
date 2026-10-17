# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Signing in against a directory (LDAP, Active Directory): who may come in, and as whom.

An organisation under the ENS often keeps its people in a directory it already runs, and wants one
password for everything. :class:`DirectoryManager` is the one implementation of that, called by the
console's ``/api/auth/ldap`` routes and by ``noust auth ldap`` alike (rule 3). What it does when a
person signs in with the name and password they have in the directory:

1. **Finds the person** with a search the operator wrote as a template. What was typed is escaped
   (RFC 4515) before it goes into it, so it can only ever be a value, and a search that matches two
   entries refuses the sign-in instead of choosing.
2. **Proves the password to the directory itself** by binding as the entry that was found. Noust
   never compares a password, never stores one, and never sends an empty one (an empty password
   with a name is an anonymous bind, which most directories accept).
3. **Hands the person to the one admission every identity source shares**
   (:class:`~noust.core.accounts.federation.Admission`): the directory's own identifier
   (``entryUUID``, ``objectGUID``) is who they are, never their name or their e-mail, and the groups
   they are in give roles, memberships and scoped roles on every sign-in.

The directory is a first factor and nothing else. The account's own second factor still follows,
through the same ``account_signed_in`` every sign-in ends with.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.accounts.directory_store import DirectoryStore, LdapDirectory
from noust.core.accounts.federation import (
    ADMISSION_SETTINGS,
    Admission,
    apply_admission_setting,
    check_references,
    name_in_use,
    settle,
)
from noust.core.accounts.ldap_client import (
    SCOPE_BASE,
    TLS_LDAPS,
    TLS_MODES,
    TLS_NONE,
    LdapConnection,
    LdapEntry,
    LdapError,
    encode_filter,
    escape_filter_value,
    rdn_value,
)
from noust.core.accounts.manager import AccountManager
from noust.core.accounts.model import Account
from noust.core.accounts.oidc import Check
from noust.core.accounts.oidc_model import (
    IdentityClaims,
    OidcError,
    OidcRefused,
    validate_name,
)
from noust.core.accounts.oidc_store import Identity, OidcStore
from noust.core.exceptions import ValidationError
from noust.core.sealing import SecretsLockedError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

logger = logging.getLogger(__name__)

#: The settings :meth:`DirectoryManager.configure` accepts, beyond the admission ones.
SETTINGS: tuple[str, ...] = (
    "host",
    "port",
    "tls",
    "ca_file",
    "bind_dn",
    "user_base",
    "user_filter",
    "subject_attribute",
    "email_attribute",
    "name_attribute",
    "username_attribute",
    "groups_attribute",
    "group_base",
    "group_filter",
    "timeout_seconds",
    *ADMISSION_SETTINGS,
)

#: What a sign-in against a directory proves, the way an authentication method is named (RFC 8176).
AMR_PASSWORD = "pwd"  # noqa: S105 - an RFC 8176 method name, not a credential
MAX_TYPED = 256
MAX_PASSWORD = 1024
MAX_GROUPS = 500
DEFAULT_USER_FILTER = "(&(objectClass=person)(uid={username}))"

_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_ATTRIBUTE = re.compile(r"[A-Za-z][A-Za-z0-9.;-]{0,63}")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def validate_host(value: Any) -> str:
    """
    Args:
        value: The directory's name or address as typed.

    Returns:
        It, trimmed.

    Raises:
        ValidationError: When it is not a host name or an address (no scheme, no port, no path).
    """
    text = str(value or "").strip()
    try:
        ipaddress.ip_address(text.strip("[]"))
        return text.strip("[]")
    except ValueError:
        pass
    if not _HOST.fullmatch(text):
        raise ValidationError(
            f"Not a host name or an address: {text[:80]!r}",
            details="Write the name only, such as ldap.acme.test; the port is its own setting.",
            field="host",
        )
    return text.lower()


def validate_port(value: Any) -> int:
    """
    Args:
        value: A port as typed.

    Returns:
        The port.

    Raises:
        ValidationError: When it is not between 1 and 65535.
    """
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("The port is a number", field="port") from exc
    if not 1 <= port <= 65535:
        raise ValidationError("The port is between 1 and 65535", field="port")
    return port


def validate_tls(value: Any, host: str) -> str:
    """
    Args:
        value: ``ldaps``, ``starttls`` or ``none``.
        host: The directory's host.

    Returns:
        The mode.

    Raises:
        ValidationError: For another mode, or ``none`` for a directory over the network.
    """
    mode = str(value or "").strip().lower()
    if mode not in TLS_MODES:
        raise ValidationError(
            f"Unknown TLS mode: {mode[:20]!r}",
            details=f"Use one of: {', '.join(TLS_MODES)}.",
            field="tls",
        )
    if mode == TLS_NONE:
        from noust.core.accounts.oidc_model import is_loopback_host

        if not is_loopback_host(host):
            raise ValidationError(
                "A directory over the network must use TLS",
                details=(
                    "Use ldaps or starttls: a password never travels in clear text. Plain text "
                    "is accepted only for a directory on this machine."
                ),
                field="tls",
            )
    return mode


def validate_ca_file(value: Any) -> str | None:
    """
    Args:
        value: A certificate authority bundle's path, or empty for the system's.

    Returns:
        The path, or None.

    Raises:
        ValidationError: When it is not the absolute path of a file that exists.
    """
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute() or len(text) > 255 or "\x00" in text or not path.is_file():
        raise ValidationError(
            f"Not the path of a certificate bundle that exists: {text[:80]!r}",
            details="Give an absolute path to a PEM file with the authority's certificate.",
            field="ca_file",
        )
    return text


def validate_dn(value: Any, what: str, *, required: bool) -> str:
    """
    Args:
        value: A distinguished name as typed.
        what: The setting, for the message.
        required: Refuse an empty one.

    Returns:
        It, trimmed.

    Raises:
        ValidationError: When it is empty and required, too long or has control characters.
    """
    text = str(value or "").strip()
    if (
        (required and not text)
        or len(text) > 512
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
    ):
        raise ValidationError(
            f"Invalid {what}",
            details="A distinguished name such as ou=people,dc=acme,dc=test.",
            field=what,
        )
    return text


def validate_attribute(value: Any, what: str) -> str:
    """
    Args:
        value: An attribute name as typed.
        what: The setting, for the message.

    Returns:
        It.

    Raises:
        ValidationError: When it is not an attribute name.
    """
    text = str(value or "").strip()
    if not _ATTRIBUTE.fullmatch(text):
        raise ValidationError(
            f"Not an attribute name: {text[:60]!r}",
            details="Such as mail, uid, entryUUID or objectGUID.",
            field=what,
        )
    return text


def validate_template(
    value: Any, what: str, allowed: tuple[str, ...], *, required: tuple[str, ...]
) -> str:
    """
    Check a search written as a template, by building it with sample values.

    Args:
        value: A filter with ``{placeholders}``.
        what: The setting, for the message.
        allowed: The placeholders it may carry.
        required: The ones it must.

    Returns:
        It, trimmed.

    Raises:
        ValidationError: When it has another placeholder, lacks a required one, or is not a
            filter once the placeholders are filled.
    """
    text = str(value or "").strip()
    found = _PLACEHOLDER.findall(text)
    if any(name not in allowed for name in found) or any(name not in found for name in required):
        raise ValidationError(
            f"The {what} may use {', '.join('{' + a + '}' for a in allowed)}"
            + (f" and must use {', '.join('{' + r + '}' for r in required)}" if required else ""),
            details="Such as (&(objectClass=person)(uid={username})).",
            field=what,
        )
    sample = text
    for name in allowed:
        sample = sample.replace("{" + name + "}", escape_filter_value("sample"))
    try:
        encode_filter(sample)
    except LdapError as exc:
        raise ValidationError(
            f"The {what} is not a search filter: {exc.message}",
            details="Such as (&(objectClass=person)(uid={username})).",
            field=what,
        ) from exc
    return text


def validate_timeout(value: Any) -> int:
    """
    Args:
        value: Seconds one sign-in may take.

    Returns:
        The seconds.

    Raises:
        ValidationError: When it is not between 1 and 30.
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "The timeout is a number of seconds", field="timeout_seconds"
        ) from exc
    if not 1 <= seconds <= 30:
        raise ValidationError("The timeout is between 1 and 30 seconds", field="timeout_seconds")
    return seconds


@dataclass(frozen=True)
class SignIn:
    """
    A person the directory vouched for and the policy admitted.

    Attributes:
        account: The account they sign in as.
        identity: Their identity at the directory.
        notes: What happened on the way (linked, created, a role given).
        detail: One sentence for the sign-in's record.
    """

    account: Account
    identity: Identity
    notes: tuple[str, ...]
    detail: str


class DirectoryManager:
    """
    Configure directories, and sign people in against them.

    Args:
        store: The store; the process-wide one by default.
        accounts: The account manager; one over the same store by default.
        directory_store: The persistence; one over the same store by default.
        clock: The time source, replaceable in tests.
        guard: How a host is judged before it is reached (tests replace it for a directory on
            this machine); :mod:`noust.core.outbound` by default.
        resolver: How a name becomes addresses (tests replace it).
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        *,
        accounts: AccountManager | None = None,
        directory_store: DirectoryStore | None = None,
        clock: Callable[[], float] | None = None,
        guard: Callable[[str, tuple[str, ...]], None] | None = None,
        resolver: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        self._clock = clock or time.time
        self._guard = guard
        self._resolver = resolver
        self.accounts = accounts or AccountManager(store, clock=clock)
        self.data = directory_store or DirectoryStore(store, clock=clock)
        self.admission = Admission(self.accounts, OidcStore(store, clock=clock), self._clock)

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    # -------------------------------------------------------------- configuration

    def directories(self) -> list[LdapDirectory]:
        """
        Returns:
            Every configured directory.
        """
        return self.data.list_directories()

    def directory(self, name: str) -> LdapDirectory | None:
        """
        Args:
            name: A directory's name.

        Returns:
            The directory, or None.
        """
        return self.data.get_directory(name)

    def require_directory(self, name: str) -> LdapDirectory:
        """
        Args:
            name: A directory's name.

        Returns:
            The directory.

        Raises:
            ValidationError: When there is none.
        """
        found = self.data.get_directory(name)
        if found is None:
            raise ValidationError(
                f"No directory named {name!r}",
                details="List them with 'noust auth ldap show'.",
                field="name",
            )
        return found

    def public_directories(self) -> list[dict[str, str]]:
        """
        Returns:
            The name and label of each enabled directory: what the sign-in page offers.
        """
        return [
            {"name": found.name, "label": found.label}
            for found in self.data.list_directories()
            if found.enabled
        ]

    def configure(
        self,
        name: str,
        changes: Mapping[str, Any],
        *,
        bind_password: str | None = None,
        clear_bind_password: bool = False,
        actor: str | None = None,
    ) -> LdapDirectory:
        """
        Add a directory, or change one: only the settings named are touched.

        Args:
            name: The directory's name.
            changes: Setting to value, from :data:`SETTINGS`. A new directory needs ``host`` and
                ``user_base``.
            bind_password: The service account's password to store, when there is one to set.
            clear_bind_password: Forget it (the directory is searched anonymously).
            actor: Who is making the change, for the record.

        Returns:
            The directory as it is now.

        Raises:
            ValidationError: When a setting is refused, or a team or role it names does not
                exist; nothing is written then.
        """
        key = validate_name(name)
        unknown = sorted(set(changes) - set(SETTINGS))
        if unknown:
            raise ValidationError(
                f"Unknown setting: {', '.join(unknown)}",
                details=f"The settings are: {', '.join(SETTINGS)}.",
            )
        existing = self.data.get_directory(key)
        if existing is None:
            if not (changes.get("host") and changes.get("user_base")):
                raise ValidationError(
                    "A new directory needs a host and a user base",
                    details="Pass --host and --user-base, such as ou=people,dc=acme,dc=test.",
                )
            if name_in_use(self.accounts.store, key, except_protocol="ldap"):
                raise ValidationError(
                    f"The name {key!r} is already a sign-in provider's",
                    details="Names are shared by every way of signing in; pick another.",
                    field="name",
                )
        now = self.now()
        base = existing or LdapDirectory(
            name=key,
            label=key,
            host="",
            user_base="",
            user_filter=DEFAULT_USER_FILTER,
            created_at=now,
            created_by=actor,
        )
        directory = self._apply(base, changes)
        directory = replace(directory, updated_at=now, updated_by=actor)
        if directory.group_filter and not directory.group_base:
            raise ValidationError(
                "A group filter needs a group base",
                details="Set --group-base too.",
                field="group_base",
            )
        if directory.group_base and not directory.group_filter:
            raise ValidationError(
                "A group base needs a group filter",
                details="Such as (&(objectClass=groupOfNames)(member={dn})).",
                field="group_filter",
            )
        if directory.bind_dn and not (
            bind_password or (self.data.has_secret(key) and not clear_bind_password)
        ):
            raise ValidationError(
                "A service account needs its password",
                details="Pass --prompt-password or --password-stdin, or leave --bind-dn out to "
                "search anonymously.",
                field="bind_dn",
            )
        check_references(directory, self.accounts)
        if existing is not None and existing.enabled and not directory.enabled:
            from noust.core.accounts.local_login import refuse_to_lose_a_way_in

            refuse_to_lose_a_way_in(
                self.accounts.store,
                without_provider=existing.name,
                action=f"Disabling the directory {existing.name!r}",
            )
        self.data.save_directory(directory)
        if bind_password:
            self.data.write_secret(key, bind_password)
        elif clear_bind_password or not directory.bind_dn:
            self.data.delete_secret(key)
        return directory

    def _apply(self, directory: LdapDirectory, changes: Mapping[str, Any]) -> LdapDirectory:
        """
        Args:
            directory: The directory before.
            changes: The settings to change.

        Returns:
            The directory after, every changed setting validated.

        Raises:
            ValidationError: When a setting, or a combination, is refused.
        """
        updated = directory
        for setting, value in changes.items():
            if setting in ADMISSION_SETTINGS:
                updated = apply_admission_setting(updated, setting, value)
            elif setting == "host":
                updated = replace(updated, host=validate_host(value))
            elif setting == "port":
                updated = replace(updated, port=validate_port(value))
            elif setting == "tls":
                updated = replace(updated, tls=str(value or "").strip().lower())
            elif setting == "ca_file":
                updated = replace(updated, ca_file=validate_ca_file(value))
            elif setting == "bind_dn":
                updated = replace(updated, bind_dn=validate_dn(value, "bind_dn", required=False))
            elif setting == "user_base":
                updated = replace(updated, user_base=validate_dn(value, "user_base", required=True))
            elif setting == "user_filter":
                updated = replace(
                    updated,
                    user_filter=validate_template(
                        value, "user_filter", ("username",), required=("username",)
                    ),
                )
            elif setting == "group_base":
                updated = replace(
                    updated, group_base=validate_dn(value, "group_base", required=False)
                )
            elif setting == "group_filter":
                text = str(value or "").strip()
                updated = replace(
                    updated,
                    group_filter=validate_template(
                        text, "group_filter", ("dn", "username"), required=()
                    )
                    if text
                    else "",
                )
            elif setting == "timeout_seconds":
                updated = replace(updated, timeout_seconds=validate_timeout(value))
            elif setting == "subject_attribute":
                updated = replace(updated, subject_attribute=validate_attribute(value, setting))
            elif setting == "email_attribute":
                updated = replace(updated, email_attribute=validate_attribute(value, setting))
            elif setting == "name_attribute":
                updated = replace(updated, name_attribute=validate_attribute(value, setting))
            elif setting == "username_attribute":
                updated = replace(updated, username_attribute=validate_attribute(value, setting))
            elif setting == "groups_attribute":
                updated = replace(updated, groups_attribute=validate_attribute(value, setting))
        # A mode and a host are judged together, whichever was given.
        validate_tls(updated.tls, updated.host)
        if "port" not in changes and "tls" in changes and directory.port in (389, 636):
            updated = replace(updated, port=389 if updated.tls != TLS_LDAPS else 636)
        return settle(updated, self.accounts)

    def remove(self, name: str) -> LdapDirectory:
        """
        Remove a directory and its password. People linked to it keep their links but can no
        longer sign in through it.

        Args:
            name: The directory's name.

        Returns:
            The directory as it was.

        Raises:
            ValidationError: When there is none.
            LocalLoginRefused: While local sign-in is off and this is the last way an
                administrator signs in.
        """
        from noust.core.accounts.local_login import refuse_to_lose_a_way_in

        found = self.require_directory(name)
        refuse_to_lose_a_way_in(
            self.accounts.store,
            without_provider=found.name,
            action=f"Removing the directory {found.name!r}",
        )
        self.data.delete_directory(found.name)
        return found

    # ------------------------------------------------------------------- testing

    def _connection(self, directory: LdapDirectory) -> LdapConnection:
        """
        Args:
            directory: A directory.

        Returns:
            A connection to it, not yet opened.
        """
        return LdapConnection(
            directory.host,
            directory.port,
            directory.tls,
            ca_file=directory.ca_file,
            timeout=float(directory.timeout_seconds),
            guard=self._guard,
            resolver=self._resolver,
        )

    def test(self, name: str) -> list[Check]:
        """
        Look at a directory the way a sign-in would, without signing anybody in.

        Args:
            name: The directory's name.

        Returns:
            Each thing looked at, until one fails: the connection and its certificate, the
            service account, and that the people (and the groups) can be searched.

        Raises:
            ValidationError: When there is no such directory.
        """
        directory = self.require_directory(name)
        checks: list[Check] = []
        try:
            with self._connection(directory) as client:
                where = f"{directory.host}:{directory.port} ({directory.tls})"
                checks.append(Check("connection", True, f"Reached {where}"))
                password = self._service_password(directory)
                client.bind(directory.bind_dn, password)
                checks.append(
                    Check(
                        "bind",
                        True,
                        f"Bound as {directory.bind_dn}"
                        if directory.bind_dn
                        else "Searches anonymously",
                    )
                )
                found = client.search(
                    directory.user_base, "(objectClass=*)", [], scope=SCOPE_BASE, size_limit=1
                )
                checks.append(
                    Check(
                        "users",
                        bool(found),
                        f"Read {directory.user_base}"
                        if found
                        else f"{directory.user_base} is empty or not readable",
                    )
                )
                if directory.group_base:
                    seen = client.search(
                        directory.group_base, "(objectClass=*)", [], scope=SCOPE_BASE, size_limit=1
                    )
                    checks.append(
                        Check(
                            "groups",
                            bool(seen),
                            f"Read {directory.group_base}"
                            if seen
                            else f"{directory.group_base} is empty or not readable",
                        )
                    )
        except LdapError as exc:
            name_ = "bind" if checks else "connection"
            checks.append(Check(name_, False, _words(exc)))
        except SecretsLockedError as exc:
            checks.append(Check("bind", False, str(exc)))
        return checks

    # ------------------------------------------------------------------- sign-in

    def _service_password(self, directory: LdapDirectory) -> str:
        """
        Args:
            directory: A directory.

        Returns:
            The service account's password; empty when it searches anonymously.

        Raises:
            LdapError: When it should have one and has none.
        """
        if not directory.bind_dn:
            return ""
        password = self.data.read_secret(directory.name)
        if not password:
            raise LdapError("The service account has no password stored")
        return password

    def authenticate(self, name: str, username: str, password: str, client_ip: str) -> SignIn:
        """
        Sign a person in with the name and password they have in the directory.

        Args:
            name: The directory's name.
            username: What they typed.
            password: Their password.
            client_ip: Where the request came from.

        Returns:
            The account, its identity and what happened on the way.

        Raises:
            OidcError: ``unknown_directory`` or ``directory_unavailable`` (shown as unavailable,
                not counted against the address), ``bad_credentials`` (shown as failed, counted).
            OidcRefused: With a reason, when the policy says no.
        """
        directory = self.data.get_directory(name)
        if directory is None or not directory.enabled:
            raise OidcError(
                "unknown_directory", "That sign-in is not available", public="unavailable"
            )
        typed = (username or "").strip()
        if (
            not typed
            or len(typed) > MAX_TYPED
            or any(ord(c) < 32 or ord(c) == 127 for c in typed)
            or not password
            or len(password) > MAX_PASSWORD
            or "\x00" in password
        ):
            raise OidcError("bad_credentials", "Wrong name or password", public="failed")
        if not self.accounts.any_exist():
            raise Admission.no_accounts()
        person = self._look_up(directory, typed, password)
        who = f"{directory.name}:{person.subject}"
        try:
            notes, account, identity = self.admission.decide(directory, person, client_ip)
        except OidcRefused as exc:
            exc.who = who
            raise
        detail = f"through {directory.label}" + (f" ({'; '.join(notes)})" if notes else "")
        logger.info("Account %s signed in through %s", account.username, who)
        return SignIn(account, identity, tuple(notes), detail)

    def _look_up(self, directory: LdapDirectory, username: str, password: str) -> IdentityClaims:
        """
        Find the person in the directory and prove their password to it.

        Args:
            directory: The directory.
            username: What they typed, not yet escaped.
            password: Their password.

        Returns:
            Who the directory says they are.

        Raises:
            OidcError: As :meth:`authenticate`.
            OidcRefused: ``ambiguous_user`` or ``no_subject``.
        """
        try:
            with self._connection(directory) as client:
                client.bind(directory.bind_dn, self._service_password(directory))
                filter_text = directory.user_filter.replace(
                    "{username}", escape_filter_value(username)
                )
                wanted = [
                    directory.subject_attribute,
                    directory.email_attribute,
                    directory.name_attribute,
                    directory.username_attribute,
                    directory.groups_attribute,
                ]
                found = client.search(directory.user_base, filter_text, wanted, size_limit=2)
                if not found:
                    raise OidcError("bad_credentials", "Wrong name or password", public="failed")
                if len(found) > 1:
                    raise OidcRefused(
                        "ambiguous_user",
                        "More than one entry in the directory matches this name",
                        details="Make the user filter match one person.",
                    )
                entry = found[0]
                groups = self._groups(directory, client, entry, username)
                try:
                    client.bind(entry.dn, password)
                except LdapError as exc:
                    if exc.code is not None:
                        raise OidcError(
                            "bad_credentials",
                            "Wrong name or password",
                            details=exc.diagnostic,
                            public="failed",
                        ) from exc
                    raise
        except LdapError as exc:
            raise OidcError(
                "directory_unavailable",
                "The directory could not be asked",
                details=_words(exc),
                public="unavailable",
            ) from exc
        except SecretsLockedError as exc:
            raise OidcError(
                "directory_unavailable",
                "The directory could not be asked",
                details=str(exc),
                public="unavailable",
            ) from exc
        subject = entry.identifier(directory.subject_attribute)
        if subject is None:
            raise OidcRefused(
                "no_subject",
                "The directory has no stable identifier for this person",
                details=(
                    f"The entry has no {directory.subject_attribute}: set --subject-attribute to "
                    "entryUUID (OpenLDAP, 389, FreeIPA) or objectGUID (Active Directory)."
                ),
            )
        email = entry.first(directory.email_attribute)
        return IdentityClaims(
            issuer=directory.issuer,
            subject=subject,
            email=email.lower() if email else None,
            email_verified=None,
            name=entry.first(directory.name_attribute),
            username=entry.first(directory.username_attribute) or username,
            groups=tuple(groups),
            amr=(AMR_PASSWORD,),
        )

    def _groups(
        self, directory: LdapDirectory, client: LdapConnection, entry: LdapEntry, username: str
    ) -> list[str]:
        """
        The groups a person belongs to, by name and by distinguished name.

        Args:
            directory: The directory.
            client: A connection, bound as the service account.
            entry: The person's entry.
            username: What they typed.

        Returns:
            Each group's distinguished name and its first component, so a mapping may name either.
        """
        names: list[str] = []
        for dn in entry.values(directory.groups_attribute):
            names.extend([dn, rdn_value(dn)])
        if directory.group_base and directory.group_filter:
            filter_text = directory.group_filter.replace("{dn}", escape_filter_value(entry.dn))
            filter_text = filter_text.replace("{username}", escape_filter_value(username))
            for group in client.search(
                directory.group_base, filter_text, [], size_limit=MAX_GROUPS
            ):
                names.extend([group.dn, rdn_value(group.dn)])
        return [name for name in dict.fromkeys(names) if name][: MAX_GROUPS * 2]

    # -------------------------------------------------------------------- links

    def link(self, name: str, username: str, subject: str, *, actor: str | None = None) -> Identity:
        """
        Link an account to a person in a directory by hand.

        The way in for a directory whose provisioning is ``off``.

        Args:
            name: The directory.
            username: The account.
            subject: The directory's stable id for the person (``entryUUID``, ``objectGUID``).
            actor: Who links it, for the record.

        Returns:
            The identity.

        Raises:
            ValidationError: When the subject is empty or too long.
            OidcError: ``already_linked`` when that person is linked already.
        """
        directory = self.require_directory(name)
        account = self.accounts.require(username)
        text = (subject or "").strip()
        if not text or len(text) > 255 or any(ord(char) < 33 for char in text):
            raise ValidationError(
                "Invalid subject",
                details="Copy the directory's own identifier for the person (up to 255 characters).",
                field="subject",
            )
        return self.admission.data.link_identity(
            account.id,
            provider=directory.name,
            issuer=directory.issuer,
            subject=text,
            email=None,
            created_by=actor,
            protocol=directory.protocol,
        )


def _words(exc: LdapError) -> str:
    """
    Args:
        exc: What the directory client raised.

    Returns:
        What an operator reads: the client's sentence, then how to fix it and the directory's own
        words when it said any.
    """
    parts = [exc.message]
    if exc.details:
        parts.append(exc.details)
    if exc.diagnostic:
        parts.append(f"The directory said: {exc.diagnostic}")
    return " ".join(parts)
