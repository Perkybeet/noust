# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Vultr (https://www.vultr.com/api): instances, regions, plans, operating systems.

An API key is created in the account (Account > API); it also needs the calling
address in its access control list. Lists are paged with a cursor
(``meta.links.next``); errors carry ``error``. Vultr takes user-data as base64, and
an instance has the address ``0.0.0.0`` until it has been provisioned.
"""

from __future__ import annotations

import base64
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

from noust.core.exceptions import CloudProviderError
from noust.fleet.cloud.models import Capabilities, Choice, CreateSpec, RemoteServer
from noust.fleet.cloud.providers.base import (
    MANAGED_BY,
    MAX_PAGES,
    CloudProvider,
    gigabytes,
    number,
    size_detail,
    text,
)

#: ``/os`` also lists the ways to start from something other than a distribution.
_NOT_A_DISTRIBUTION = frozenset({"iso", "snapshot", "backup", "custom", "application"})

#: The address an instance has before it has one.
_NO_ADDRESS = frozenset({"", "0.0.0.0", "::"})  # noqa: S104 - a placeholder Vultr reports, not a bind

#: Vultr lists up to this many per page.
_PER_PAGE = 100


def _usable(address: Any) -> str | None:
    """
    Keep an address only when it is a real one.

    Args:
        address: What Vultr reported.

    Returns:
        The address, or None for the placeholder an instance has while it is created.
    """
    value = text(address)
    return None if value in _NO_ADDRESS else value


class Vultr(CloudProvider):
    """Vultr."""

    kind: ClassVar[str] = "vultr"
    display_name: ClassVar[str] = "Vultr"
    api_url: ClassVar[str] = "https://api.vultr.com/v2"
    credentials_url: ClassVar[str] = "https://my.vultr.com/settings/#settingsapi"
    credentials_help: ClassVar[str] = (
        "In Account > API, enable the API and copy the key; allow this central's address in "
        "its access control list, or Vultr refuses every call."
    )
    capabilities: ClassVar[Capabilities] = Capabilities(
        firewalls=True,
        networks=True,
        backups=True,
        ipv4_optional=True,
        ipv6=True,
        multiple_firewalls=False,
        multiple_networks=True,
    )
    # Vultr takes base64, which is a third larger than what it carries.
    user_data_limit: ClassVar[int] = 32 * 1024

    def _pages(
        self, path: str, key: str, query: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Read every page of a list.

        Args:
            path: The list's path.
            key: The key its items sit under.
            query: Extra query parameters.

        Returns:
            The items.
        """
        found: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {**(query or {}), "per_page": _PER_PAGE}
            if cursor:
                params["cursor"] = cursor
            body = self.http.request("GET", path, query=params)
            items = body.get(key) if isinstance(body, dict) else None
            found.extend(item for item in items or [] if isinstance(item, dict))
            meta = body.get("meta") if isinstance(body, dict) else None
            links = meta.get("links") if isinstance(meta, dict) else None
            following = links.get("next") if isinstance(links, dict) else None
            cursor = None
            if following:
                values = parse_qs(urlparse(str(following)).query).get("cursor")
                cursor = values[0] if values else None
            if not cursor:
                break
        return found

    def verify(self) -> str:
        """Returns: The account's e-mail address."""
        body = self.http.request("GET", "/account")
        account = body.get("account") if isinstance(body, dict) else None
        if not isinstance(account, dict):
            raise self.unexpected("the account", body)
        return text(account.get("email")) or self.display_name

    def locations(self) -> list[Choice]:
        """Returns: Vultr's regions."""
        return [
            Choice(
                id=text(item.get("id")),
                name=f"{text(item.get('city'))}, {text(item.get('country'))}",
                detail=text(item.get("id")),
            )
            for item in self._pages("/regions", "regions")
            if item.get("id")
        ]

    def plans(self) -> list[Choice]:
        """Returns: Vultr's plans."""
        return [
            Choice(
                id=text(item.get("id")),
                name=text(item.get("id")),
                detail=size_detail(
                    number(item.get("vcpu_count")),
                    gigabytes(number(item.get("ram")), "MB"),
                    number(item.get("disk")),
                ),
                extra={
                    "locations": [text(r) for r in item.get("locations") or []],
                    "monthly": number(item.get("monthly_cost")),
                    "arch": "x86",
                },
            )
            for item in self._pages("/plans", "plans", {"type": "all"})
            if item.get("id")
        ]

    def images(self) -> list[Choice]:
        """Returns: Vultr's distributions, leaving out ISOs, snapshots and applications."""
        return [
            Choice(
                id=text(item.get("id")),
                name=text(item.get("name")),
                detail=text(item.get("arch")),
                extra={
                    "distribution": text(item.get("family")),
                    "archs": ["arm" if "arm" in text(item.get("arch")) else "x86"],
                },
            )
            for item in self._pages("/os", "os")
            if item.get("id") is not None
            and text(item.get("family")).lower() not in _NOT_A_DISTRIBUTION
        ]

    def ssh_keys(self) -> list[Choice]:
        """Returns: The account's SSH keys."""
        return [
            Choice(id=text(item.get("id")), name=text(item.get("name")))
            for item in self._pages("/ssh-keys", "ssh_keys")
            if item.get("id")
        ]

    def firewalls(self) -> list[Choice]:
        """Returns: The account's firewall groups."""
        return [
            Choice(
                id=text(item.get("id")), name=text(item.get("description")) or text(item.get("id"))
            )
            for item in self._pages("/firewalls", "firewall_groups")
            if item.get("id")
        ]

    def networks(self) -> list[Choice]:
        """Returns: The account's VPCs."""
        return [
            Choice(
                id=text(item.get("id")),
                name=text(item.get("description")) or text(item.get("id")),
                detail=text(item.get("v4_subnet")),
                extra={"locations": [text(item.get("region"))] if item.get("region") else []},
            )
            for item in self._pages("/vpcs", "vpcs")
            if item.get("id")
        ]

    def upload_ssh_key(self, name: str, public_key: str) -> str:
        """
        Put a public key in the account.

        Args:
            name: What to call it.
            public_key: One OpenSSH public key line.

        Returns:
            Vultr's id for it.
        """
        body = self.http.request("POST", "/ssh-keys", body={"name": name, "ssh_key": public_key})
        key = body.get("ssh_key") if isinstance(body, dict) else None
        if not isinstance(key, dict) or not key.get("id"):
            raise self.unexpected("the key", body)
        return text(key["id"])

    def delete_ssh_key(self, key_id: str) -> None:
        """
        Remove a key from the account.

        Args:
            key_id: What :meth:`upload_ssh_key` returned.
        """
        self.http.request("DELETE", f"/ssh-keys/{key_id}")

    def create_server(self, spec: CreateSpec) -> RemoteServer:
        """
        Create an instance.

        Args:
            spec: What to create.

        Returns:
            The instance as Vultr first reports it: no address yet.

        Raises:
            CloudProviderError: When Vultr refuses; nothing was created.
        """
        self.check_spec(spec)
        body: dict[str, Any] = {
            "region": spec.location,
            "plan": spec.plan,
            "os_id": int(spec.image) if spec.image.isdigit() else spec.image,
            "label": spec.name,
            "hostname": spec.name,
            "enable_ipv6": spec.ipv6,
            "disable_public_ipv4": not spec.ipv4,
            "backups": "enabled" if spec.backups else "disabled",
            "tags": [MANAGED_BY],
        }
        if spec.user_data:
            body["user_data"] = base64.b64encode(spec.user_data.encode("utf-8")).decode("ascii")
        if spec.ssh_keys:
            body["sshkey_id"] = list(spec.ssh_keys)
        if spec.firewalls:
            body["firewall_group_id"] = spec.firewalls[0]
        if spec.networks:
            body["attach_vpc"] = list(spec.networks)
        answer = self.http.request("POST", "/instances", body=body)
        instance = answer.get("instance") if isinstance(answer, dict) else None
        if not isinstance(instance, dict) or not instance.get("id"):
            raise self.unexpected("an instance", answer)
        # Vultr echoes the generated root password here; it is never read.
        return self._server(instance)

    def get_server(self, server_id: str) -> RemoteServer:
        """
        Read an instance.

        Args:
            server_id: Vultr's id.

        Returns:
            Its state and addresses.
        """
        answer = self.http.request("GET", f"/instances/{server_id}")
        instance = answer.get("instance") if isinstance(answer, dict) else None
        if not isinstance(instance, dict):
            raise self.unexpected("an instance", answer)
        return self._server(instance)

    def delete_server(self, server_id: str) -> None:
        """
        Delete an instance.

        Args:
            server_id: Vultr's id.
        """
        self.http.request("DELETE", f"/instances/{server_id}")

    def power_on(self, server_id: str) -> None:
        """
        Start an instance that is off.

        Args:
            server_id: Vultr's id.
        """
        self.http.request("POST", f"/instances/{server_id}/start")

    def _server(self, item: dict[str, Any]) -> RemoteServer:
        """
        Read an instance out of Vultr's JSON.

        Args:
            item: The ``instance`` object.

        Returns:
            The server.

        Raises:
            CloudProviderError: When it has no id.
        """
        if not item.get("id"):
            raise CloudProviderError("Vultr described an instance without an id")
        status = text(item.get("status"))
        power = text(item.get("power_status"))
        server_status = text(item.get("server_status"))
        if status == "pending":
            state = "creating"
        elif power == "stopped" or status == "suspended":
            state = "stopped"
        elif status == "active" and power == "running" and server_status in ("ok", ""):
            state = "running"
        elif status == "active":
            state = "creating"
        else:
            state = "unknown"
        return RemoteServer(
            id=text(item["id"]),
            name=text(item.get("label")) or text(item.get("hostname")),
            state=state,
            raw_state=f"{status}/{power}/{server_status}",
            ipv4=_usable(item.get("main_ip")),
            ipv6=_usable(item.get("v6_main_ip")),
        )
