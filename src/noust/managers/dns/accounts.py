# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS provider accounts Noust holds, and the credentials behind them.

An account is a name, a provider and a credential. The credential is a file in
the secret store (``dns/<name>``, 0600, sealed with the rest on a sealed central):
for Cloudflare the API token on its own, for a certbot plugin the credentials file
the plugin reads, written as it reads it. It never reaches a column, a log line,
an audit event, an argument or a response; the console shows an account's name,
kind, zones and when its token last worked, nothing more.

Adding an account proves it: a Cloudflare token must list zones before it is kept,
and a plugin account must have its plugin installed, so the failure is at the
moment the operator can fix it and not at the first renewal, weeks later.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from noust.central import require_server_role
from noust.core import audit
from noust.core.certificate_store import CertificateStore
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.exceptions import NoustError, ValidationError
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.core.teams.model import ResourceNotFoundError
from noust.core.tunnel_store import TunnelStore
from noust.managers.dns.catalog import KINDS, ProviderKind, kind_of
from noust.managers.dns.cloudflare import CloudflareClient, CloudflareProvider, Opener
from noust.managers.dns.provider import DnsProvider, DnsProviderError, Zone

#: The secret namespace the credentials live in.
SECRET_NAMESPACE = "dns"  # noqa: S105 - a directory name, not a password

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

#: What a credential value may not hold: a line break would add a line to the file
#: certbot reads, and anything else is not a credential.
_BAD_VALUE = re.compile(r"[\x00-\x1f\x7f]")


def secret_name(account: str) -> str:
    """
    Args:
        account: The account's name.

    Returns:
        The name its credential is kept under in the secret store.
    """
    return f"{SECRET_NAMESPACE}/{account}"


class DnsAccounts:
    """
    Add, check, list and remove DNS accounts.

    Args:
        store: The store; the process-wide one by default.
        secrets: Where credentials live; the secret store beside the store by default.
        runner: Asks certbot which plugins it has; the process-wide runner by default.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        runner: CommandRunner | None = None,
        opener: Opener | None = None,
    ) -> None:
        self._store = store
        self.records = DnsStore(store)
        self.secrets = secrets or SecretStore()
        self._runner = runner
        self._opener = opener

    @property
    def runner(self) -> CommandRunner:
        """The runner that asks certbot about its plugins."""
        return self._runner or get_runner()

    def accounts(self) -> list[DnsAccount]:
        """
        Returns:
            Every account, by name.
        """
        return self.records.list_accounts()

    def get(self, name: str) -> DnsAccount:
        """
        Args:
            name: The account's name.

        Returns:
            The account.

        Raises:
            NoustError: There is none of that name.
        """
        account = self.records.get_account(name)
        if account is None:
            raise ResourceNotFoundError(
                f"There is no DNS account called {name}",
                details="List them with: noust dns provider list",
            )
        return account

    def kind(self, account: DnsAccount) -> ProviderKind:
        """
        Args:
            account: An account.

        Returns:
            Its kind.

        Raises:
            NoustError: The kind is one this version does not know.
        """
        found = kind_of(account.kind)
        if found is None:
            raise NoustError(
                f"{account.name} is a {account.kind} account, which this version does not know",
                details="Known kinds: " + ", ".join(sorted(KINDS)),
            )
        return found

    def add(
        self,
        name: str,
        kind: str,
        credentials: Mapping[str, str],
        *,
        automatic: bool = False,
        created_by: str | None = None,
    ) -> DnsAccount:
        """
        Add an account, proving its credential first.

        Args:
            name: What to call it: lower case letters, digits and dashes.
            kind: The provider (see :mod:`noust.managers.dns.catalog`).
            credentials: The fields the kind asks for. Never stored in a column.
            automatic: Create the records of a domain when it is added, without asking.
            created_by: Who is adding it.

        Returns:
            The account.

        Raises:
            ValidationError: The name, the kind or a credential is not acceptable.
            DnsProviderError: The provider refused the token.
            NoustError: The plugin a kind needs is not installed.
        """
        require_server_role("DNS accounts")
        if not _NAME.fullmatch(name):
            raise ValidationError(
                f"{name!r} cannot be an account name",
                details="Use lower case letters, digits and dashes, starting with a letter or digit.",
                field="name",
            )
        if self.records.get_account(name) is not None:
            raise ValidationError(
                f"There is already a DNS account called {name}",
                details=f"Remove it first: noust dns provider remove {name}",
                field="name",
            )
        spec = kind_of(kind)
        if spec is None:
            raise ValidationError(
                f"{kind!r} is not a DNS provider Noust knows",
                details="Known: " + ", ".join(sorted(KINDS)) + ".",
                field="kind",
            )
        values = self._checked_fields(spec, credentials)
        settings: dict[str, object] = {}
        error: str | None = None
        if spec.native:
            zones = CloudflareProvider(self._client(values["token"])).zones()
            if not zones:
                raise DnsProviderError(
                    "The token works but sees no zones",
                    details="Give it Zone:Read and DNS:Edit on the zones to manage.",
                )
            if values.get("account_id"):
                settings["account_id"] = values["account_id"]
            self.secrets.write(secret_name(name), values["token"])
        else:
            self._require_plugin(spec)
            self.secrets.write(secret_name(name), spec.template.format(**values))
            settings["plugin"] = spec.plugin
        account = DnsAccount(
            name=name,
            kind=kind,
            automatic=automatic and spec.native,
            settings=settings,
            verified_at=None if not spec.native else _now(),
            last_error=error,
            created_by=created_by,
        )
        self.records.save_account(account)
        audit.record(
            "dns.account",
            target=f"dns:{name}",
            details={"action": "add", "kind": kind, "automatic": account.automatic},
        )
        return self.get(name)

    def provider(self, name: str) -> DnsProvider:
        """
        Build the client of a native account.

        Args:
            name: The account.

        Returns:
            Its provider.

        Raises:
            NoustError: The account is not native, or its credential is missing.
        """
        account = self.get(name)
        if not self.kind(account).native:
            raise NoustError(
                f"{name} is a {account.kind} account: Noust only answers challenges through it",
                details="Records and tunnels need a Cloudflare account.",
            )
        token = self.secrets.read(secret_name(name))
        if not token:
            raise NoustError(
                f"The token of {name} is missing",
                details=f"Add the account again: noust dns provider remove {name}",
            )
        return CloudflareProvider(self._client(token.strip()))

    def verify(self, name: str) -> list[Zone]:
        """
        Try the account's credential again and record how it went.

        Args:
            name: The account.

        Returns:
            Its zones (empty for a plugin account, which Noust cannot list).

        Raises:
            DnsProviderError: The provider refused the token; the account keeps the words.
        """
        account = self.get(name)
        spec = self.kind(account)
        if not spec.native:
            self._require_plugin(spec)
            self.records.mark_verified(name, None)
            return []
        try:
            zones = self.provider(name).zones()
        except DnsProviderError as exc:
            self.records.mark_verified(name, exc.output or exc.message)
            raise
        self.records.mark_verified(name, None)
        return zones

    def set_automatic(self, name: str, automatic: bool) -> DnsAccount:
        """
        Choose whether adding a domain creates its records without asking.

        Args:
            name: The account.
            automatic: The choice.

        Returns:
            The account.

        Raises:
            ValidationError: The account's kind keeps no records.
        """
        account = self.get(name)
        if automatic and not self.kind(account).native:
            raise ValidationError(
                f"{name} is a {account.kind} account: Noust keeps no records there",
                field="automatic",
            )
        self.records.save_account(
            DnsAccount(
                name=account.name,
                kind=account.kind,
                automatic=automatic,
                settings=account.settings,
                verified_at=account.verified_at,
                last_error=account.last_error,
                created_at=account.created_at,
                created_by=account.created_by,
            )
        )
        audit.record(
            "dns.account",
            target=f"dns:{name}",
            details={"action": "automatic", "automatic": automatic},
        )
        return self.get(name)

    def set_propagation(self, name: str, seconds: int) -> DnsAccount:
        """
        Choose how long a challenge record is waited for before the order goes on.

        Args:
            name: The account.
            seconds: 0 to 600.

        Returns:
            The account.

        Raises:
            ValidationError: The wait is out of range, or the account is not Noust's to wait for
                (a plugin has its own setting).
        """
        account = self.get(name)
        if not self.kind(account).native:
            raise ValidationError(
                f"{name} is answered by certbot's plugin, which has its own wait",
                field="propagation_seconds",
            )
        if isinstance(seconds, bool) or not 0 <= seconds <= 600:
            raise ValidationError("The wait is 0 to 600 seconds", field="propagation_seconds")
        self.records.save_account(
            replace(account, settings={**account.settings, "propagation_seconds": seconds})
        )
        return self.get(name)

    def remove(self, name: str) -> None:
        """
        Remove an account, its credential and its records' bookkeeping.

        The records themselves stay at the provider: removing an account is not a
        reason to touch DNS. To release them first, remove the domains or run
        ``noust dns sweep``.

        Args:
            name: The account.

        Raises:
            ValidationError: A certificate is ordered through it, so removing it
                would break the next renewal, or a tunnel is managed through it.
        """
        account = self.get(name)
        users = CertificateStore(self._store).domains_with_provider(name)
        if users:
            raise ValidationError(
                f"{name} answers the certificate challenge of {', '.join(users)}",
                details="Give them another way first: noust cert use APP --acme, or another "
                "account.",
                field="name",
            )
        tunnels = [
            tunnel.name for tunnel in TunnelStore(self._store).all() if tunnel.provider == name
        ]
        if tunnels:
            # The tunnel's row goes with its account, and its connector would run on with
            # nothing that knows how to stop it or to delete it at Cloudflare.
            raise ValidationError(
                f"{name} manages the tunnel {', '.join(tunnels)}",
                details="Remove the tunnel first: noust tunnel remove " + tunnels[0],
                field="name",
            )
        self.records.delete_account(name)
        self.secrets.delete(secret_name(name))
        audit.record(
            "dns.account",
            target=f"dns:{name}",
            details={"action": "remove", "kind": account.kind},
        )

    def credentials_file(self, name: str) -> Path:
        """
        Name the file a certbot plugin reads an account's credentials from.

        Args:
            name: A plugin account.

        Returns:
            Its path. certbot keeps it in the lineage's renewal configuration and
            reads it again at every renewal.

        Raises:
            NoustError: The account is native, or its file is missing.
        """
        account = self.get(name)
        if self.kind(account).native:
            raise NoustError(f"{name} is answered by Noust itself and has no credentials file")
        path = self.secrets.path(secret_name(name))
        if not path.exists():
            raise NoustError(
                f"The credentials of {name} are missing",
                details="Remove the account and add it again.",
            )
        return self.secrets.usable_path(secret_name(name))

    # -- internals -----------------------------------------------------------

    def _client(self, token: str) -> CloudflareClient:
        """
        Args:
            token: A Cloudflare API token.

        Returns:
            A client holding it.
        """
        return CloudflareClient(token, opener=self._opener)

    @staticmethod
    def _checked_fields(spec: ProviderKind, given: Mapping[str, str]) -> dict[str, str]:
        """
        Check the fields an account needs.

        Args:
            spec: The kind.
            given: What the operator gave.

        Returns:
            Every field of the kind, defaults filled in.

        Raises:
            ValidationError: One is missing, unknown or cannot be a credential.
        """
        known = {field.name for field in spec.fields}
        for key in given:
            if key not in known:
                raise ValidationError(
                    f"{key} is not something a {spec.label} account takes",
                    details="It takes: " + ", ".join(sorted(known)) + ".",
                    field=key,
                )
        values: dict[str, str] = {}
        for field in spec.fields:
            value = str(given.get(field.name) or field.default).strip()
            if not value and field.required:
                raise ValidationError(f"{field.label} is required", field=field.name)
            if _BAD_VALUE.search(value) or len(value) > 512:
                raise ValidationError(f"{field.label} cannot be used", field=field.name)
            values[field.name] = value
        if spec.native and not 20 <= len(values["token"]) <= 256:
            raise ValidationError("That is not a Cloudflare API token", field="token")
        return values

    def _require_plugin(self, spec: ProviderKind) -> None:
        """
        Refuse a plugin account whose certbot plugin is not installed.

        Skipped when certbot itself is not installed yet: the account is useful
        the moment it is.

        Args:
            spec: The kind.

        Raises:
            NoustError: certbot is here and the plugin is not.
        """
        runner = self.runner
        if not runner.exists("certbot"):
            return
        result = runner.run(["certbot", "plugins"], timeout=60)
        if result.success and f"* {spec.plugin}" not in result.stdout:
            raise NoustError(
                f"certbot has no {spec.plugin} plugin",
                details=f"Install it ({spec.package}) and add the account again.",
            )


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
