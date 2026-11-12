# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking a registry when the registration of a domain expires (item 173).

An expired registration takes down every site under it, and the certificates with it, and nothing of
Noust's looked at it. This is the one request to the outside that nobody typed an address for, so it
is held to what ``docs/PRIVACY.md`` promises:

- **Off unless the operator turned it on** (``monitor.domain_expiry``, off by default and off under
  the ENS profile too): the monitor's watch (:mod:`noust.monitor.watch_domains`) does nothing
  otherwise, and ``noust domain expiry --refresh`` is a person asking.
- **One question a day for each registrable domain**, to the registry that table names
  (:mod:`noust.core.rdap`, bundled: IANA is not asked), over https, to nobody else. A registry with
  no answer to give is said, and not asked again every day.
- **What is sent** is the domain name and ``User-Agent: noust/<version>``: no host name, no
  identifier, no cookie. The destination sees this server's address, as any request shows it.
- **Where it goes** is checked by :func:`noust.core.outbound.require_public_host` at the moment of the
  request, a redirect is never followed, and at most 256 KiB of the answer is read.

The registrable domain of a served name is found by the registry's own answer, climbing label by
label (:func:`noust.core.rdap.candidates`), and the one found is kept
(:class:`~noust.core.domain_registrations.RegistrationStore`).
"""

from __future__ import annotations

import http.client
import logging
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from noust import __version__
from noust.core import outbound, rdap
from noust.core.domain_registrations import (
    ERROR,
    NO_EXPIRY,
    NO_RDAP,
    NOT_FOUND,
    OK,
    Registration,
    RegistrationStore,
)
from noust.core.exceptions import NoustError
from noust.core.store import NoustStore

log = logging.getLogger(__name__)

#: How long one question may take.
TIMEOUT_SECONDS = 10.0

#: The most of an answer that is read. A domain object is a few kilobytes.
MAX_BYTES = 256 * 1024

#: When to ask again, by how the last look ended.
ASK_AGAIN = {
    OK: timedelta(days=1),
    ERROR: timedelta(hours=6),
    NOT_FOUND: timedelta(days=7),
    NO_EXPIRY: timedelta(days=30),
    NO_RDAP: timedelta(days=30),
}

#: What an attempt can raise that is the registry's or the network's and not a defect: it is the
#: answer ``error`` and is asked again soon.
_REQUEST_ERRORS: tuple[type[Exception], ...] = (
    urllib.error.URLError,
    http.client.HTTPException,
    OSError,
    outbound.DestinationRefused,
    outbound.UnsafePath,
    ValueError,
)


def served_names(store: NoustStore | None = None) -> list[str]:
    """
    List the names Noust serves, in the order of the applications.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        The domains of every application that is not a preview, the ones switched off left out,
        each once. Names nobody could have registered are left to :meth:`DomainRegistrations.check`.
    """
    from noust.core.store import get_store

    reader = store or get_store()
    names: list[str] = []
    for app in reader.list_apps():
        if getattr(app, "preview_parent", None):
            continue
        for record in reader.list_domains(app.domain, include_disabled=False):
            if record.domain not in names:
                names.append(record.domain)
    return names


def known_registrations(
    names: Iterable[str], store: NoustStore | None = None
) -> dict[str, Registration]:
    """
    Read what is known of the registration of each name, asking nobody.

    The one reader the command line, the API and ``noust health`` use (rule 3).

    Args:
        names: Names Noust serves.
        store: The store; the process-wide one by default.

    Returns:
        Each name that was looked at, with what its registry said; a name never looked at is left
        out, and so is everything when the store cannot be read.
    """
    try:
        records = RegistrationStore(store)
        found: dict[str, Registration] = {}
        for name in names:
            registration = records.for_name(name)
            if registration is not None:
                found[name] = registration
    except (NoustError, sqlite3.Error) as exc:
        log.debug("The registrations were not read: %s", exc)
        return {}
    return found


class Response(Protocol):
    """What the opener returns: a status and a body that can be read in part."""

    status: int

    def read(self, size: int = -1) -> bytes:
        """Read at most ``size`` bytes."""


Opener = Callable[[urllib.request.Request, float], AbstractContextManager[Response]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A registry is asked at the address the table gives and nowhere it points to."""

    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        """
        Decline every redirect, so it surfaces as an HTTP error naming the status.

        Returns:
            None, which makes urllib raise instead of following.
        """
        return None


def _real_opener(
    request: urllib.request.Request, timeout: float
) -> AbstractContextManager[Response]:
    """
    Make the request, without following a redirect.

    Args:
        request: The question.
        timeout: Seconds.

    Returns:
        The response, as a context manager.
    """
    opened: AbstractContextManager[Response] = urllib.request.build_opener(_NoRedirect).open(
        request, timeout=timeout
    )
    return opened


@dataclass(frozen=True)
class _Asked:
    """
    What one question came to.

    Attributes:
        found: The registry recognises the name.
        document: Its answer, when it gave one.
        error: A sentence, when the question did not come to an answer.
    """

    found: bool
    document: Any = None
    error: str = ""


class DomainRegistrations:
    """
    Asks registries when registrations expire and keeps what they say.

    Args:
        store: The store; the process-wide one by default.
        opener: Makes a request and returns its response; tests inject one.
        clock: The present; tests move it.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        *,
        opener: Opener | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.records = RegistrationStore(store)
        self._opener = opener or _real_opener
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ one question

    def _ask(self, base: str, name: str) -> _Asked:
        """
        Ask one registry what it knows of one name.

        Args:
            base: The registry's service address, from the bundled table.
            name: The domain asked about.

        Returns:
            Whether the registry recognised it and what it said; or the sentence saying why there
            is no answer.
        """
        try:
            outbound.require_public_host(rdap.registry_name(base))
            request = urllib.request.Request(
                rdap.rdap_url(base, name),
                headers={"Accept": "application/rdap+json", "User-Agent": f"noust/{__version__}"},
            )
            with self._opener(request, TIMEOUT_SECONDS) as response:
                body = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return _Asked(found=False)
            kind = "redirected to another address" if 300 <= exc.code < 400 else f"HTTP {exc.code}"
            return _Asked(found=False, error=f"The registry answered {kind}.")
        except _REQUEST_ERRORS as exc:
            return _Asked(found=False, error=f"The registry could not be asked: {exc}")
        if len(body) > MAX_BYTES:
            return _Asked(found=False, error="The registry's answer is larger than it should be.")
        document = rdap.parse_answer(body)
        if document is None:
            return _Asked(found=False, error="The registry's answer is not RDAP.")
        return _Asked(found=True, document=document)

    # ------------------------------------------------------------------ one name

    def check(self, name: str, *, force: bool = False) -> Registration | None:
        """
        Find out when the registration of the domain a served name belongs to expires.

        Args:
            name: A name Noust serves.
            force: Ask although it was looked at recently.

        Returns:
            What the registry said, kept; None when the name is not one somebody could have
            registered (an address, ``localhost``). A registry that cannot be asked is a
            registration with the status ``error``, to look at again soon.
        """
        names = rdap.candidates(name)
        if not names:
            return None
        served = rdap.served_name(name)
        labels = served.split(".")
        base = rdap.service_for(labels[-1])
        now = self._clock()
        if base is None:
            guess = ".".join(labels[-2:])
            self.records.remember_registrable(served, guess, now)
            return self._keep(
                guess,
                NO_RDAP,
                None,
                None,
                now,
                f"This registry does not publish the expiry (the registry of .{labels[-1]} has no RDAP "
                "service).",
            )
        known = self.records.registrable_of(served) or self._already_known_above(served, names)
        if known is not None:
            current = self.records.get(known)
            if current is not None and not force and not current.due(now):
                return current
            return self._look(known, base, now, [known], served)
        return self._look(None, base, now, names, served)

    def _already_known_above(self, served: str, names: list[str]) -> str | None:
        """
        Find a registrable domain above a served name that the registry already recognised.

        If the registry knows ``example.com``, it does not know ``www.example.com`` as a
        registration of its own: asking would only be a question with a known answer.

        Args:
            served: The name Noust serves.
            names: What :func:`noust.core.rdap.candidates` made of it, the name first.

        Returns:
            That domain, now kept as the one the name belongs to; None when there is none.
        """
        for candidate in names[1:]:
            record = self.records.get(candidate)
            if record is not None and record.status in (OK, NO_EXPIRY):
                self.records.remember_registrable(served, candidate, self._clock())
                return candidate
        return None

    def _look(
        self,
        registrable: str | None,
        base: str,
        now: datetime,
        names: list[str],
        served: str,
    ) -> Registration:
        """
        Ask about each name in turn until the registry recognises one.

        Args:
            registrable: The registrable domain already known for the served name, if any.
            base: The registry's service address.
            now: The present.
            names: What to ask about, in order.
            served: The name Noust serves.

        Returns:
            What it came to, kept.
        """
        for candidate in names:
            asked = self._ask(base, candidate)
            if asked.error:
                key = registrable or served
                return self._keep(key, ERROR, None, base, now, asked.error)
            if not asked.found:
                continue
            self.records.remember_registrable(served, candidate, now)
            expires = rdap.expiration_from(asked.document)
            if expires is None:
                return self._keep(
                    candidate,
                    NO_EXPIRY,
                    None,
                    base,
                    now,
                    "The registry answered but does not publish the expiry of this domain.",
                )
            return self._keep(candidate, OK, expires.isoformat(timespec="seconds"), base, now, "")
        return self._keep(
            registrable or served,
            NOT_FOUND,
            None,
            base,
            now,
            "The registry does not know this name, nor the names above it.",
        )

    def _keep(
        self,
        domain: str,
        status: str,
        expires_at: str | None,
        base: str | None,
        now: datetime,
        detail: str,
    ) -> Registration:
        """
        Keep what a look came to, with when to look again.

        Args:
            domain: The registrable domain, or the served name when it is not known yet.
            status: How it ended.
            expires_at: When the registration expires, ISO 8601, when the registry said.
            base: The registry's service address, when one answered.
            now: The present.
            detail: A sentence about how it ended.

        Returns:
            The registration as kept.
        """
        registration = Registration(
            domain=domain,
            status=status,
            expires_at=expires_at,
            rdap_base=base,
            checked_at=now.isoformat(timespec="seconds"),
            next_check_at=(now + ASK_AGAIN[status]).isoformat(timespec="seconds"),
            detail=detail,
        )
        self.records.save(registration)
        return registration

    # ------------------------------------------------------------------ many names

    def check_names(self, names: Iterable[str], *, force: bool = False) -> list[Registration]:
        """
        Look at the registrable domain of each name, once each.

        Args:
            names: The names Noust serves.
            force: Ask although a domain was looked at recently.

        Returns:
            What each distinct domain came to, in the order its first name came. A name that could
            not be asked about is left out; one that fails does not stop the rest.
        """
        done: dict[str, Registration] = {}
        for name in names:
            known = self.records.registrable_of(name)
            if known is not None and known in done:
                continue
            try:
                found = self.check(name, force=force)
            except NoustError as exc:
                log.warning("The registration of %s could not be kept: %s", name, exc.message)
                continue
            if found is not None:
                done.setdefault(found.domain, found)
        return list(done.values())

    def forget_unserved(self, served: Iterable[str]) -> None:
        """
        Forget the registrations no name Noust serves leads to any more.

        Args:
            served: Every name Noust serves now.
        """
        self.records.keep_only(served)
