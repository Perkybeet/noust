# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The contract every cloud provider adapter keeps.

An adapter is the only code that knows a provider's endpoints, field names and
states. It turns them into :mod:`noust.fleet.cloud.models` and back, and does
nothing else: choosing, waiting, enrolling and cleaning up are the provisioner's
(:mod:`noust.fleet.cloud.provision`), so a fourth provider is one more subclass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from noust.core.exceptions import CloudProviderError, ValidationError
from noust.fleet.cloud.http import CloudHttp, MessageReader, Opener, generic_message
from noust.fleet.cloud.models import (
    Capabilities,
    Catalog,
    Choice,
    CreateSpec,
    RemoteServer,
)

#: Items asked per page when listing. Every provider caps this at 100 or more.
PAGE_SIZE = 50

#: Pages read at most when listing. A provider with five thousand images is not
#: one a form could show; a loop without a bound is not safe either.
MAX_PAGES = 40

#: A label Noust puts on what it creates, so the operator can find it in the
#: provider's console and the provider's tools can filter on it.
MANAGED_BY = "noust"


def text(value: Any) -> str:
    """
    Read a field as text.

    Args:
        value: Whatever the provider sent.

    Returns:
        ``str(value)``, empty for ``None``.
    """
    return "" if value is None else str(value)


def number(value: Any) -> float | None:
    """
    Read a field as a number.

    Args:
        value: Whatever the provider sent.

    Returns:
        The number, or None when it is not one (a boolean is not).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def gigabytes(value: float | None, unit: str = "GB") -> str:
    """
    Spell an amount of memory or disk.

    Args:
        value: The amount, in ``unit``.
        unit: ``GB`` or ``MB``.

    Returns:
        ``4 GB``; megabytes are converted. Empty when unknown.
    """
    if value is None:
        return ""
    amount = value / 1024 if unit == "MB" else value
    return f"{amount:g} GB"


def size_detail(vcpus: float | None, memory: str, disk: float | None) -> str:
    """
    Spell a plan's size on one line.

    Args:
        vcpus: Virtual CPUs.
        memory: Memory, already spelled.
        disk: Disk in GB.

    Returns:
        ``2 vCPU, 4 GB RAM, 40 GB disk``, with what is known.
    """
    parts = []
    if vcpus is not None:
        parts.append(f"{vcpus:g} vCPU")
    if memory:
        parts.append(f"{memory} RAM")
    if disk is not None:
        parts.append(f"{disk:g} GB disk")
    return ", ".join(parts)


class CloudProvider(ABC):
    """
    One provider's API, behind one account's token.

    Subclasses set the class attributes and implement the abstract methods.

    Attributes:
        kind: The provider's key (``hetzner``).
        display_name: What the operator reads (``Hetzner Cloud``).
        api_url: The API root, https.
        credentials_url: Where the operator creates the token.
        credentials_help: What the token must be allowed to do.
        capabilities: What :meth:`create_server` can do.
        user_data_limit: Largest cloud-init document the provider accepts, in bytes.
    """

    kind: ClassVar[str]
    display_name: ClassVar[str]
    api_url: ClassVar[str]
    credentials_url: ClassVar[str]
    credentials_help: ClassVar[str]
    capabilities: ClassVar[Capabilities]
    user_data_limit: ClassVar[int] = 32 * 1024

    def __init__(
        self,
        token: str,
        *,
        http: CloudHttp | None = None,
        opener: Opener | None = None,
        sleep: Any = None,
    ) -> None:
        """
        Args:
            token: The account's API token.
            http: A ready client (tests); one is built from ``token`` otherwise.
            opener: Replacement for :func:`urllib.request.urlopen`.
            sleep: Replacement for :func:`time.sleep` between attempts.
        """
        if http is None:
            extra: dict[str, Any] = {}
            if sleep is not None:
                extra["sleep"] = sleep
            http = CloudHttp(
                self.display_name,
                self.api_url,
                token,
                message=self.error_message(),
                opener=opener,
                **extra,
            )
        self.http = http

    @classmethod
    def error_message(cls) -> MessageReader:
        """
        Returns:
            How to read this provider's sentence out of an error body.
        """
        return generic_message

    # -- Reading --------------------------------------------------------

    @abstractmethod
    def verify(self) -> str:
        """
        Check the token with the cheapest authenticated call there is.

        Returns:
            A label for the account (an e-mail address or the provider's name).

        Raises:
            CloudProviderError: When the provider refuses the token.
        """

    @abstractmethod
    def locations(self) -> list[Choice]:
        """Returns: Where a machine may be created."""

    @abstractmethod
    def plans(self) -> list[Choice]:
        """Returns: The sizes on sale, each with the ``locations`` it is sold in."""

    @abstractmethod
    def images(self) -> list[Choice]:
        """Returns: The operating systems a machine may start from."""

    @abstractmethod
    def ssh_keys(self) -> list[Choice]:
        """Returns: The SSH keys the account already holds."""

    def firewalls(self) -> list[Choice]:
        """Returns: The account's firewalls; none for a provider that cannot attach them."""
        return []

    def networks(self) -> list[Choice]:
        """Returns: The account's private networks; none for a provider that cannot attach them."""
        return []

    def catalog(self) -> Catalog:
        """
        Read everything a form needs.

        Returns:
            The locations, plans, images, keys, firewalls and networks.

        Raises:
            CloudProviderError: When any list cannot be read.
        """
        caps = self.capabilities
        return Catalog(
            locations=self.locations(),
            plans=self.plans(),
            images=self.images(),
            ssh_keys=self.ssh_keys(),
            firewalls=self.firewalls() if caps.firewalls else [],
            networks=self.networks() if caps.networks else [],
        )

    # -- Changing -------------------------------------------------------

    @abstractmethod
    def upload_ssh_key(self, name: str, public_key: str) -> str:
        """
        Put a public key in the account.

        Args:
            name: What to call it there.
            public_key: One OpenSSH public key line.

        Returns:
            The provider's id for it, as text.
        """

    @abstractmethod
    def delete_ssh_key(self, key_id: str) -> None:
        """
        Remove a key from the account.

        Args:
            key_id: What :meth:`upload_ssh_key` returned.
        """

    @abstractmethod
    def create_server(self, spec: CreateSpec) -> RemoteServer:
        """
        Ask for a machine.

        Args:
            spec: What to create.

        Returns:
            The machine as the provider first reports it; it may not be running or
            have an address yet.

        Raises:
            CloudProviderError: When the provider refuses. Nothing was created.
        """

    def after_create(self, server: RemoteServer, spec: CreateSpec) -> None:
        """
        Do what the provider cannot take in the create call itself.

        Called once the machine exists and its id is recorded, so a failure here
        leaves a machine the caller knows about. Most providers need nothing.

        Args:
            server: What :meth:`create_server` returned.
            spec: What was asked for.
        """
        return None

    @abstractmethod
    def get_server(self, server_id: str) -> RemoteServer:
        """
        Read a machine.

        Args:
            server_id: The provider's id.

        Returns:
            Its current state and addresses.

        Raises:
            CloudProviderError: When the provider does not know it (status 404).
        """

    @abstractmethod
    def delete_server(self, server_id: str) -> None:
        """
        Delete a machine, and stop the charge for it.

        Args:
            server_id: The provider's id.
        """

    @abstractmethod
    def power_on(self, server_id: str) -> None:
        """
        Start a machine that is off.

        Args:
            server_id: The provider's id.
        """

    # -- Shared checks --------------------------------------------------

    def check_spec(self, spec: CreateSpec) -> None:
        """
        Refuse what this provider's create call cannot do, before anything is sent.

        Args:
            spec: What would be created.

        Raises:
            ValidationError: When it asks for more than the provider offers or
                for a machine nobody can reach.
        """
        caps = self.capabilities
        if not spec.ipv4 and not spec.ipv6:
            raise ValidationError(
                "The server needs a public address",
                details="Turn on IPv4 or IPv6: the central reaches it over SSH.",
                field="ipv4",
            )
        if not spec.ipv4 and not caps.ipv4_optional:
            raise ValidationError(
                f"{self.display_name} always gives a machine a public IPv4 address",
                details="Leave IPv4 on.",
                field="ipv4",
            )
        if spec.ipv6 and not caps.ipv6:
            raise ValidationError(
                f"{self.display_name} cannot give a machine a public IPv6 address",
                field="ipv6",
            )
        if spec.firewalls and not caps.firewalls:
            raise ValidationError(
                f"Firewalls cannot be attached at creation on {self.display_name}",
                field="firewalls",
            )
        if len(spec.firewalls) > 1 and not caps.multiple_firewalls:
            raise ValidationError(
                f"{self.display_name} attaches one firewall to a new machine", field="firewalls"
            )
        if spec.networks and not caps.networks:
            raise ValidationError(
                f"Private networks cannot be attached at creation on {self.display_name}",
                field="networks",
            )
        if len(spec.networks) > 1 and not caps.multiple_networks:
            raise ValidationError(
                f"{self.display_name} attaches one private network to a new machine",
                field="networks",
            )
        if spec.backups and not caps.backups:
            raise ValidationError(f"{self.display_name} offers no backups here", field="backups")
        size = len(spec.user_data.encode("utf-8"))
        if size > self.user_data_limit:
            raise ValidationError(
                f"The cloud-init document is {size} bytes; {self.display_name} accepts "
                f"{self.user_data_limit}",
                details="Shorten the scripts you added to it.",
                field="scripts",
            )

    def unexpected(self, what: str, body: Any) -> CloudProviderError:
        """
        Build the error for an answer that lacks what it should hold.

        Args:
            what: What was expected (``a server``).
            body: What came back.

        Returns:
            The error to raise.
        """
        return CloudProviderError(
            f"{self.display_name} answered without {what}",
            details="Its API may have changed; try again, and report it if it persists.",
            output=self.http.scrub(str(body)[:500]),
        )
