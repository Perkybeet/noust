# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the registry last said of each domain Noust serves (item 173).

The registration of a domain expires, which nothing of Noust's saw: an expired registration takes
down every site under it and the certificates with it. With ``monitor.domain_expiry`` on (off by
default; ``docs/PRIVACY.md`` says what is asked of whom) the monitor asks the registry of each
registrable domain once a day, and the answers are kept here, in the dates lane's fragment of schema
v18: ``domain_registrations`` (one row per registrable domain) and ``domain_registrables`` (which
registrable domain each name Noust serves belongs to, found by the registry's own answer: see
:mod:`noust.core.rdap`). ``noust domain list``, the domain's page in the console and ``noust health``
read them; nothing is asked when they do.

Every write is validated here, at the one place the table is written: a status is one of those below,
a domain is a name somebody could have registered, a date reads as one, and the registry's own words
are kept short.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core import cert_life
from noust.core.exceptions import ValidationError
from noust.core.rdap import is_checkable, served_name
from noust.core.store import NoustStore, get_store

#: How the last look at a registrable domain ended.
OK = "ok"
#: The registry of its top-level domain does not publish through RDAP: said once, not retried daily.
NO_RDAP = "no_rdap"
#: The registry answered but the answer carries no expiry: it does not publish that either.
NO_EXPIRY = "no_expiry"
#: The registry does not know the name, nor any of the labels above it.
NOT_FOUND = "not_found"
#: The registry did not answer, or answered something that is not RDAP: looked at again soon.
ERROR = "error"

STATUSES = frozenset({OK, NO_RDAP, NO_EXPIRY, NOT_FOUND, ERROR})

#: The longest the registry's own words are kept.
DETAIL_LIMIT = 500


@dataclass(frozen=True)
class Registration:
    """
    What a registry said of one registrable domain.

    Attributes:
        domain: The registrable domain, as the registry recognised it.
        status: One of :data:`STATUSES`.
        expires_at: When the registration expires, ISO 8601 in UTC; None unless ``status`` is ``ok``.
        rdap_base: The registry service that answered, an https address; None when none did.
        checked_at: When it was last looked at.
        next_check_at: When to look again.
        detail: What came back when it did not work, or why there is no answer, in a sentence of
            the registry's own words or Noust's, at most :data:`DETAIL_LIMIT` characters.
    """

    domain: str
    status: str
    expires_at: str | None
    rdap_base: str | None
    checked_at: str
    next_check_at: str
    detail: str = ""

    def days_left(self, now: datetime | None = None) -> int | None:
        """
        Args:
            now: The moment to count from; the present when omitted.

        Returns:
            Whole days until the registration expires (rounded down, negative once it has);
            None when the registry gave no expiry.
        """
        end = cert_life.parse_moment(self.expires_at)
        if end is None:
            return None
        moment = now if now is not None else datetime.now(timezone.utc)
        return int((end - moment).total_seconds() // 86400)

    def due(self, now: datetime | None = None) -> bool:
        """
        Args:
            now: The moment to judge at; the present when omitted.

        Returns:
            Whether it is time to look at the registry again.
        """
        when = cert_life.parse_moment(self.next_check_at)
        moment = now if now is not None else datetime.now(timezone.utc)
        return when is None or when <= moment

    def describe(self, now: datetime | None = None) -> str:
        """
        Say what the registry said, in a short sentence for a table.

        Args:
            now: The moment to count from; the present when omitted.

        Returns:
            The date it expires and how long that is, or why there is no date: the registry does
            not publish it, does not know the name, or could not be asked.
        """
        if self.status == OK and self.expires_at:
            left = self.days_left(now)
            date = (self.expires_at or "")[:10]
            if left is None:
                return date
            if left < 0:
                return f"{date} (expired {-left} days ago)"
            return f"{date} (in {left} days)" if left != 1 else f"{date} (in 1 day)"
        return self.detail or {
            NO_RDAP: "This registry does not publish the expiry.",
            NO_EXPIRY: "This registry does not publish the expiry.",
            NOT_FOUND: "The registry does not know this name.",
            ERROR: "The registry could not be asked.",
        }.get(self.status, self.status)

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        """
        Args:
            now: The moment to count from; the present when omitted.

        Returns:
            What a client reads: the registrable domain, how the last look ended, when the
            registration expires and in how many whole days, which registry said so (its host),
            when it was looked at and what came back when it did not work.
        """
        from noust.core.rdap import registry_name

        return {
            "domain": self.domain,
            "status": self.status,
            "expires_at": self.expires_at,
            "days_left": self.days_left(now),
            "registry": registry_name(self.rdap_base) if self.rdap_base else None,
            "checked_at": self.checked_at,
            "detail": self.detail,
        }


def _checked(registration: Registration) -> Registration:
    """
    Validate a registration before it is written.

    Args:
        registration: What is about to be kept.

    Returns:
        The same registration with the registry's words cut to :data:`DETAIL_LIMIT`.

    Raises:
        ValidationError: A status that is not one, a name that is not a registrable domain, a date
            that does not read as one, or a registry address that is not https.
    """
    if registration.status not in STATUSES:
        raise ValidationError(
            f"{registration.status!r} is not a status of a registration",
            details=f"It is one of: {', '.join(sorted(STATUSES))}.",
            field="status",
        )
    if not is_checkable(registration.domain) or registration.domain != served_name(
        registration.domain
    ):
        raise ValidationError(
            f"{registration.domain!r} is not a domain name that can be registered",
            details="Use the name as the registry writes it: lower case, two labels or more.",
            field="domain",
        )
    for field_name, value in (
        ("expires_at", registration.expires_at),
        ("checked_at", registration.checked_at),
        ("next_check_at", registration.next_check_at),
    ):
        if value is not None and cert_life.parse_moment(value) is None:
            raise ValidationError(
                f"{value!r} is not a date", details="Write it as ISO 8601.", field=field_name
            )
    if registration.rdap_base is not None and not registration.rdap_base.startswith("https://"):
        raise ValidationError(
            f"{registration.rdap_base!r} is not an https address",
            details="Noust asks a registry over https only.",
            field="rdap_base",
        )
    if len(registration.detail) <= DETAIL_LIMIT:
        return registration
    return Registration(
        registration.domain,
        registration.status,
        registration.expires_at,
        registration.rdap_base,
        registration.checked_at,
        registration.next_check_at,
        registration.detail[: DETAIL_LIMIT - 1] + "…",
    )


class RegistrationStore:
    """
    The store's record of registrations.

    Args:
        store: The store holding ``domain_registrations`` and ``domain_registrables``.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store or get_store()

    def save(self, registration: Registration) -> None:
        """
        Keep what a registry said, replacing what it said before.

        Args:
            registration: The registration.

        Raises:
            ValidationError: When it is not one that can be kept (see :func:`_checked`).
        """
        item = _checked(registration)
        with self._store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO domain_registrations "
                "(domain, status, expires_at, rdap_base, checked_at, next_check_at, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(domain) DO UPDATE SET "
                "status = excluded.status, expires_at = excluded.expires_at, "
                "rdap_base = excluded.rdap_base, checked_at = excluded.checked_at, "
                "next_check_at = excluded.next_check_at, detail = excluded.detail",
                (
                    item.domain,
                    item.status,
                    item.expires_at,
                    item.rdap_base,
                    item.checked_at,
                    item.next_check_at,
                    item.detail,
                ),
            )

    def get(self, domain: str) -> Registration | None:
        """
        Args:
            domain: A registrable domain.

        Returns:
            What its registry last said, or None when it was never asked.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "SELECT domain, status, expires_at, rdap_base, checked_at, next_check_at, detail "
                "FROM domain_registrations WHERE domain = ?",
                (domain,),
            )
            row = cursor.fetchone()
        return Registration(*row) if row else None

    def all(self) -> list[Registration]:
        """
        Returns:
            Every registration, the soonest to expire first, those with no known expiry last.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "SELECT domain, status, expires_at, rdap_base, checked_at, next_check_at, detail "
                "FROM domain_registrations "
                "ORDER BY expires_at IS NULL, expires_at, domain"
            )
            return [Registration(*row) for row in cursor.fetchall()]

    def for_name(self, name: str) -> Registration | None:
        """
        Read what is known of the registration a served name belongs to, asking nobody.

        Args:
            name: A name Noust serves.

        Returns:
            The registration of the registrable domain found for it; or, when the registry could
            not be asked yet, what the last look at the name itself came to; None when it was
            never looked at.
        """
        registrable = self.registrable_of(name)
        return self.get(registrable) if registrable else self.get(served_name(name))

    def registrable_of(self, name: str) -> str | None:
        """
        Args:
            name: A name Noust serves.

        Returns:
            The registrable domain the registry recognised for it, or None when it was not found
            yet.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "SELECT registrable FROM domain_registrables WHERE name = ?", (served_name(name),)
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None

    def remember_registrable(self, name: str, registrable: str, found_at: datetime) -> None:
        """
        Keep which registrable domain a name was found to belong to.

        Args:
            name: A name Noust serves.
            registrable: The name the registry recognised: the name itself or one of the labels
                above it.
            found_at: When the registry said so.

        Raises:
            ValidationError: When either is not a domain name.
        """
        for value in (name, registrable):
            if not is_checkable(value):
                raise ValidationError(
                    f"{value!r} is not a domain name that can be registered", field="domain"
                )
        with self._store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO domain_registrables (name, registrable, found_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET registrable = excluded.registrable, "
                "found_at = excluded.found_at",
                (
                    served_name(name),
                    served_name(registrable),
                    found_at.isoformat(timespec="seconds"),
                ),
            )

    def forget_registrable(self, name: str) -> None:
        """
        Args:
            name: A name Noust serves, whose registrable domain is to be found again.
        """
        with self._store._transaction() as cursor:
            cursor.execute("DELETE FROM domain_registrables WHERE name = ?", (served_name(name),))

    def names_of(self, registrable: str) -> list[str]:
        """
        Args:
            registrable: A registrable domain.

        Returns:
            The names Noust serves that belong to it, in order.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "SELECT name FROM domain_registrables WHERE registrable = ? ORDER BY name",
                (registrable,),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def keep_only(self, served: Iterable[str]) -> None:
        """
        Forget what no name Noust serves leads to any more.

        Args:
            served: Every name Noust serves now.
        """
        kept = {served_name(name) for name in served}
        with self._store._transaction() as cursor:
            cursor.execute("SELECT name, registrable FROM domain_registrables")
            known = [(str(name), str(registrable)) for name, registrable in cursor.fetchall()]
            for name, _ in known:
                if name not in kept:
                    cursor.execute("DELETE FROM domain_registrables WHERE name = ?", (name,))
            live = {registrable for name, registrable in known if name in kept}
            cursor.execute("SELECT domain FROM domain_registrations")
            for (domain,) in cursor.fetchall():
                if domain not in live:
                    cursor.execute("DELETE FROM domain_registrations WHERE domain = ?", (domain,))
