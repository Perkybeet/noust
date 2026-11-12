# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a certificate is doing, measured on its own life: the one answer (item 172).

Let's Encrypt issues for 90 days now, for 64 from 2027-02-10 and for 45 from 2028-02-16, and
offers a profile of 6 days. A threshold written in days is wrong for at least one of those: 25
days left is half the life of a 45-day certificate and a warning on a 90-day one. So the question
"how is this certificate" is asked of its fraction of life left, between ``notBefore`` and
``notAfter``:

- **healthy**: at least a third of its life is left. certbot renews at a third (at a half for a
  certificate of 10 days or less), so a certificate in this state needs nothing.
- **renewal pending**: under a third and not yet renewed. A warning: renewal is what ought to
  have happened by now.
- **critical**: under a sixth. Half the way from the renewal point to the end.
- **expired**.

``noust health``, the overview, the diagnosis, the monitor and the console all ask this module,
so a certificate is in one state whoever looks (rule 3). The operator's own ceiling,
``monitor.cert_warning_days``, still exists, and only says when the *monitor* speaks: under that
many days and past the renewal point, never before it (:meth:`CertLife.announces`).

An uploaded certificate has a life too, so it follows the same calculation.

Pure: it imports nothing of Noust and reads no clock but the one it is given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

#: The states, from the best to the worst.
HEALTHY = "healthy"
RENEWAL_PENDING = "renewal_pending"
CRITICAL = "critical"
EXPIRED = "expired"
UNKNOWN = "unknown"

#: Below this fraction of its life a certificate has gone past the point where it is renewed.
RENEWAL_FRACTION = 1 / 3

#: Below this fraction of its life a certificate is in danger of expiring unrenewed.
CRITICAL_FRACTION = 1 / 6

#: The life taken for a certificate whose start is not known: what every certificate Let's Encrypt
#: issued had until 2027, and so what the thresholds of before were written for.
ASSUMED_LIFE_DAYS = 90

_DAY = timedelta(days=1)

#: ``Mar  1 10:15:00 2027 GMT``: how openssl prints ``notBefore`` and ``notAfter``.
_OPENSSL_MOMENT = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})\s+(\d{4})\s+GMT")

#: ``2027-03-01 10:15:00+00:00``, the way certbot prints an expiry, with or without what it adds.
_ISO_MOMENT = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?)?"
)


@dataclass(frozen=True)
class CertLife:
    """
    How a certificate stands, with the figures that decided it.

    Attributes:
        state: One of :data:`HEALTHY`, :data:`RENEWAL_PENDING`, :data:`CRITICAL`,
            :data:`EXPIRED` and :data:`UNKNOWN`.
        days_left: Whole days until it expires (rounded down), negative once it has; None when
            its end is not known.
        life_days: How long the certificate lives, from ``notBefore`` to ``notAfter``; the
            assumed life when the start is not known; None when the end is not known.
        fraction_left: What is left of that life, 1.0 for a new certificate and 0.0 at the end;
            None when it cannot be told. Negative once expired.
        assumed_life: Whether ``life_days`` is the assumed one, because the start was not known.
    """

    state: str
    days_left: int | None = None
    life_days: float | None = None
    fraction_left: float | None = None
    assumed_life: bool = False

    @property
    def needs_renewal(self) -> bool:
        """Whether the certificate is past the point where it is renewed."""
        return self.state in (RENEWAL_PENDING, CRITICAL, EXPIRED)

    def announces(self, cap_days: int | None) -> bool:
        """
        Say whether the monitor speaks of this certificate.

        Args:
            cap_days: The operator's ``monitor.cert_warning_days``, a ceiling: the monitor speaks
                only under that many days. None for no ceiling.

        Returns:
            True for an expired certificate; True for one past its renewal point that has fewer
            days left than the ceiling. A healthy certificate is never spoken of, whatever the
            ceiling: the ceiling only narrows when a late renewal is said, it does not make a
            certificate with most of its life ahead late.
        """
        if self.state == EXPIRED:
            return True
        if self.state not in (RENEWAL_PENDING, CRITICAL) or self.days_left is None:
            return False
        return cap_days is None or self.days_left < cap_days


def assess(
    *,
    not_after: datetime | None,
    not_before: datetime | None = None,
    now: datetime | None = None,
) -> CertLife:
    """
    Judge a certificate on its own life.

    Args:
        not_after: When it expires; None when it could not be read.
        not_before: When it began to be valid; None when it could not be read, and the life is
            then taken to be :data:`ASSUMED_LIFE_DAYS`.
        now: The moment to judge at; the present when omitted.

    Returns:
        How it stands. A start at or after the end is not a life, and is treated as unknown.
    """
    if not_after is None:
        return CertLife(state=UNKNOWN)
    moment = now if now is not None else datetime.now(timezone.utc)
    end = _aware(not_after)
    remaining = end - _aware(moment)
    days_left = remaining // _DAY

    assumed = True
    life = timedelta(days=ASSUMED_LIFE_DAYS)
    if not_before is not None:
        start = _aware(not_before)
        if end > start:
            life = end - start
            assumed = False

    fraction = remaining / life
    if remaining < timedelta(0):
        state = EXPIRED
    elif fraction < CRITICAL_FRACTION:
        state = CRITICAL
    elif fraction < RENEWAL_FRACTION:
        state = RENEWAL_PENDING
    else:
        state = HEALTHY
    return CertLife(
        state=state,
        days_left=days_left,
        life_days=life / _DAY,
        fraction_left=fraction,
        assumed_life=assumed,
    )


def life_of(record: Any, now: datetime | None = None) -> CertLife:
    """
    Judge a certificate as the certificate manager lists it.

    Args:
        record: Anything with ``.get``: a ``CertificateInfo``, or the plain mapping a test or a
            fleet node hands over. It reads ``not_after`` (else certbot's ``expiry_full`` or the
            date in ``expiry``) and ``not_before``.
        now: The moment to judge at; the present when omitted.

    Returns:
        How it stands, as :func:`assess` says.
    """
    end = (
        parse_moment(record.get("not_after"))
        or parse_moment(record.get("expiry_full"))
        or parse_moment(record.get("expiry"), end_of_day=True)
    )
    return assess(not_after=end, not_before=parse_moment(record.get("not_before")), now=now)


def _aware(moment: datetime) -> datetime:
    """
    Args:
        moment: A moment, aware or naive (a naive one is UTC).

    Returns:
        The same moment, aware.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}


def parse_moment(text: str | None, *, end_of_day: bool = False) -> datetime | None:
    """
    Read a moment the way certbot, openssl and the store write it.

    Args:
        text: ``2027-03-01`` (midnight UTC), ``2027-03-01T10:15:00+00:00`` (with ``Z`` or a
            space instead of the ``T``, and a trailing note such as certbot's ``(VALID: 59
            days)``), or openssl's ``Mar  1 10:15:00 2027 GMT`` (with the ``notAfter=`` it
            prints before it).
        end_of_day: Read a date with no time as the end of that day. For an expiry written as a
            date alone: the certificate is good the whole of it, as it always was.

    Returns:
        The moment in UTC, or None when it cannot be read.
    """
    if not text:
        return None
    openssl = _OPENSSL_MOMENT.search(text)
    if openssl is not None:
        month, day, clock, year = openssl.groups()
        try:
            parsed = datetime.strptime(
                f"{year}-{_MONTHS[month]}-{day} {clock}", "%Y-%m-%d %H:%M:%S"
            )
        except (KeyError, ValueError):
            return None
        return parsed.replace(tzinfo=timezone.utc)
    iso = _ISO_MOMENT.search(text)
    if iso is None:
        return None
    date, clock, offset = iso.groups()
    try:
        moment = datetime.fromisoformat(f"{date}T{clock or '00:00:00'}")
    except ValueError:
        return None
    if clock is None and end_of_day:
        moment += _DAY
    if offset and offset != "Z":
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        moment -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return moment.replace(tzinfo=timezone.utc)
