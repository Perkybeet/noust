# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a cloud provider offers and what it reports about a machine, in one shape.

Each provider names the same things differently (a Hetzner *server type* is a
DigitalOcean *size* is a Vultr *plan*; a *location* is a *region*). The adapters
translate to these, so the console, the CLI and the provisioning job read one
vocabulary and never a provider's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ValidationError

#: The states a machine is reported in, whatever the provider calls them.
SERVER_STATES = ("creating", "running", "stopped", "deleting", "unknown")

#: A cloud account's name: what a secret path and a console label allow.
_ACCOUNT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")

#: A cloud-init library entry's name.
_SCRIPT = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?")


def validate_account_name(name: object) -> str:
    """
    Check the name of a cloud account.

    Args:
        name: What the operator typed.

    Returns:
        The name.

    Raises:
        ValidationError: When it is not 1 to 32 lower-case letters, digits and dashes.
    """
    if not isinstance(name, str) or not _ACCOUNT.fullmatch(name):
        raise ValidationError(
            f"Invalid account name: {name!r}",
            details="Use 1 to 32 lower-case letters, digits and dashes, such as 'hetzner-prod'.",
            field="name",
        )
    return name


def validate_script_name(name: object) -> str:
    """
    Check the name of a cloud-init library entry.

    Args:
        name: What the operator typed.

    Returns:
        The name.

    Raises:
        ValidationError: When it is not 1 to 64 lower-case letters, digits, dots,
            dashes and underscores.
    """
    if not isinstance(name, str) or not _SCRIPT.fullmatch(name):
        raise ValidationError(
            f"Invalid script name: {name!r}",
            details="Use 1 to 64 lower-case letters, digits, dots, dashes and underscores, "
            "such as 'swap-2g'.",
            field="name",
        )
    return name


@dataclass(frozen=True)
class Choice:
    """
    One thing a provider offers to pick from.

    Attributes:
        id: What the create call takes: a slug, a name or an id, as text.
        name: What the operator reads.
        detail: A short second line (``2 vCPU, 4 GB RAM, 40 GB disk``).
        extra: What a form may use to narrow the choices: ``locations`` a plan is
            sold in, ``arch`` an image or plan is for, ``monthly`` price.
    """

    id: str
    name: str
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The choice, JSON-ready.
        """
        return {"id": self.id, "name": self.name, "detail": self.detail, **self.extra}


@dataclass(frozen=True)
class Capabilities:
    """
    What a provider's create call can do, so a form offers only that.

    Attributes:
        firewalls: Attach existing firewalls at creation.
        networks: Attach existing private networks at creation.
        backups: Turn on the provider's paid backups.
        ipv4_optional: A machine may be created without a public IPv4 address.
        ipv6: A machine may get a public IPv6 address.
        multiple_firewalls: More than one firewall may be attached.
        multiple_networks: More than one private network may be attached.
    """

    firewalls: bool = False
    networks: bool = False
    backups: bool = False
    ipv4_optional: bool = False
    ipv6: bool = True
    multiple_firewalls: bool = False
    multiple_networks: bool = False

    def to_dict(self) -> dict[str, bool]:
        """
        Returns:
            Every capability, by name.
        """
        return {
            "firewalls": self.firewalls,
            "networks": self.networks,
            "backups": self.backups,
            "ipv4_optional": self.ipv4_optional,
            "ipv6": self.ipv6,
            "multiple_firewalls": self.multiple_firewalls,
            "multiple_networks": self.multiple_networks,
        }


@dataclass
class Catalog:
    """
    Everything a form needs to ask for a machine at one provider.

    Attributes:
        locations: Where a machine may be created.
        plans: Sizes (CPU, memory, disk), each narrowed by ``extra["locations"]``.
        images: Operating systems, each narrowed by ``extra["arch"]``.
        ssh_keys: The account's own SSH keys, to add for the operator's own access.
        firewalls: Existing firewalls.
        networks: Existing private networks.
    """

    locations: list[Choice] = field(default_factory=list)
    plans: list[Choice] = field(default_factory=list)
    images: list[Choice] = field(default_factory=list)
    ssh_keys: list[Choice] = field(default_factory=list)
    firewalls: list[Choice] = field(default_factory=list)
    networks: list[Choice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every list, JSON-ready.
        """
        return {
            name: [choice.to_dict() for choice in getattr(self, name)]
            for name in ("locations", "plans", "images", "ssh_keys", "firewalls", "networks")
        }


@dataclass
class CreateSpec:
    """
    What to ask a provider for.

    Attributes:
        name: The machine's name and host name.
        location: A :class:`Choice` id from ``locations``.
        plan: A :class:`Choice` id from ``plans``.
        image: A :class:`Choice` id from ``images``.
        user_data: The cloud-init document the machine boots with.
        ssh_keys: Provider ids of SSH keys to authorize for root.
        firewalls: Provider ids of firewalls to attach.
        networks: Provider ids of private networks to attach.
        ipv4: Give it a public IPv4 address.
        ipv6: Give it a public IPv6 address.
        backups: Turn the provider's backups on.
    """

    name: str
    location: str
    plan: str
    image: str
    user_data: str = ""
    ssh_keys: list[str] = field(default_factory=list)
    firewalls: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    ipv4: bool = True
    ipv6: bool = True
    backups: bool = False


@dataclass(frozen=True)
class RemoteServer:
    """
    A machine as its provider reports it.

    Attributes:
        id: The provider's id, as text.
        name: Its name.
        state: One of :data:`SERVER_STATES`.
        raw_state: The provider's own word for it, verbatim.
        ipv4: Its public IPv4 address, once it has one.
        ipv6: Its public IPv6 address, once it has one.
    """

    id: str
    name: str
    state: str
    raw_state: str = ""
    ipv4: str | None = None
    ipv6: str | None = None

    @property
    def address(self) -> str | None:
        """The address to reach it on: IPv4 when it has one, else IPv6."""
        return self.ipv4 or self.ipv6

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The machine, JSON-ready.
        """
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "raw_state": self.raw_state,
            "ipv4": self.ipv4,
            "ipv6": self.ipv6,
            "address": self.address,
        }
