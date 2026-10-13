# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS-01 challenge: proving control of a name with a record, and taking it away.

certbot asks a hook to publish ``_acme-challenge.<name>`` with a value, validates, and
asks another to remove it. Those two hooks are ``noust dns challenge auth`` and
``cleanup``: certbot keeps the hook commands in the lineage's renewal configuration,
so every renewal runs them again, with the same code and without anyone being there.

Why Noust answers instead of certbot's own plugin: no plugin has to be installed, the
token never leaves the secret store (no file for certbot to keep a path to), and the
same Cloudflare client serves the managed records and the tunnels. Every other
provider is answered by its certbot plugin (:mod:`noust.managers.dns.catalog`).

The hook gets the name and the value from certbot's environment
(``CERTBOT_DOMAIN``, ``CERTBOT_VALIDATION``), never from an argument. A wildcard and
its base name share one record name with two values; the hook publishes each value
and removes only its own.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping

from noust.core import audit
from noust.core.exceptions import CertificateError, ValidationError
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.provider import AUTOMATIC_TTL, same_value, zone_for

#: The label a challenge record sits under.
CHALLENGE_LABEL = "_acme-challenge"

#: The note kept with a challenge record, so cleanup only ever deletes its own.
CHALLENGE_COMMENT = "managed-by: noust acme challenge"

#: Seconds waited after publishing, for the record to be served by the zone's name servers.
DEFAULT_PROPAGATION = 20

#: The longest wait an account may ask for.
MAX_PROPAGATION = 600

#: What certbot may hand a hook as the validation token: base64url.
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


def challenge_name(domain: str) -> str:
    """
    Name the record that answers the challenge for a domain.

    Args:
        domain: The name being validated, ``*.`` and all.

    Returns:
        ``_acme-challenge.example.com`` for ``example.com`` and for ``*.example.com``.
    """
    return f"{CHALLENGE_LABEL}.{domain.strip().lower().removeprefix('*.').rstrip('.')}"


def read_environment(environ: Mapping[str, str]) -> tuple[str, str]:
    """
    Read the name and the value certbot hands its hooks.

    Args:
        environ: The hook's environment.

    Returns:
        ``(domain, validation)``.

    Raises:
        CertificateError: The hook was not run by certbot, or was given something odd.
    """
    domain = environ.get("CERTBOT_DOMAIN", "").strip().lower()
    validation = environ.get("CERTBOT_VALIDATION", "").strip()
    if not domain or not validation:
        raise CertificateError(
            "This command is run by certbot, which sets CERTBOT_DOMAIN and CERTBOT_VALIDATION",
            details="It publishes and removes the DNS record a certificate order is proven with.",
        )
    if not _TOKEN.fullmatch(validation):
        raise CertificateError("certbot gave a validation value that is not one")
    return domain, validation


class DnsChallenge:
    """
    Publish and remove the DNS-01 records of one DNS account.

    Args:
        accounts: The accounts; built over the process-wide store by default.
        sleep: Waits for propagation (tests replace it).
    """

    def __init__(
        self,
        accounts: DnsAccounts | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.accounts = accounts or DnsAccounts()
        self._sleep = sleep

    def present(self, account_name: str, domain: str, validation: str) -> str:
        """
        Publish the challenge record and wait for it to be served.

        Args:
            account_name: The DNS account that holds the zone.
            domain: The name being validated.
            validation: The value to publish.

        Returns:
            The name of the record.

        Raises:
            CertificateError: The account holds no zone for the name.
            DnsProviderError: The provider refused the record.
        """
        name = challenge_name(domain)
        provider = self.accounts.provider(account_name)
        zone = zone_for(name, provider.zones())
        if zone is None:
            raise CertificateError(
                f"{account_name} holds no zone for {domain}",
                details="Use the account that manages the domain's DNS zone: "
                "noust cert use APP --acme --dns NAME",
            )
        existing = provider.find_records(zone, name, "TXT")
        if not any(same_value(record.content, validation) for record in existing):
            provider.create_record(
                zone, name, "TXT", validation, ttl=AUTOMATIC_TTL, comment=CHALLENGE_COMMENT
            )
            audit.record(
                "dns.challenge",
                target=f"dns:{account_name}",
                details={"action": "present", "name": name, "zone": zone.name},
            )
        self._sleep(self._propagation(account_name))
        return name

    def cleanup(self, account_name: str, domain: str, validation: str) -> int:
        """
        Remove the challenge record this order published, and only that.

        Args:
            account_name: The DNS account that holds the zone.
            domain: The name that was validated.
            validation: The value that was published.

        Returns:
            How many records were removed. Nothing removed is not an error:
            cleanup runs after a failure too, when nothing may have been created.

        Raises:
            DnsProviderError: The provider refused the deletion.
        """
        name = challenge_name(domain)
        provider = self.accounts.provider(account_name)
        zone = zone_for(name, provider.zones())
        if zone is None:
            return 0
        removed = 0
        for record in provider.find_records(zone, name, "TXT"):
            if same_value(record.content, validation) and record.comment == CHALLENGE_COMMENT:
                removed += int(provider.delete_record(zone, record.id))
        if removed:
            audit.record(
                "dns.challenge",
                target=f"dns:{account_name}",
                details={"action": "cleanup", "name": name, "zone": zone.name},
            )
        return removed

    def _propagation(self, account_name: str) -> float:
        """
        Args:
            account_name: A DNS account.

        Returns:
            Seconds to wait after publishing: the account's own setting, bounded.
        """
        raw = self.accounts.get(account_name).settings.get("propagation_seconds")
        try:
            seconds = int(raw) if raw is not None else DEFAULT_PROPAGATION
        except (TypeError, ValueError):
            seconds = DEFAULT_PROPAGATION
        if not 0 <= seconds <= MAX_PROPAGATION:
            raise ValidationError(
                f"The propagation wait of {account_name} must be 0 to {MAX_PROPAGATION} seconds",
                field="propagation_seconds",
            )
        return float(seconds)
