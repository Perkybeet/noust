# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
DigitalOcean (https://docs.digitalocean.com/reference/api): droplets, regions, sizes, images.

A token is created in the control panel (API > Tokens) with the scopes it will
use; a custom scope set that includes droplet, ssh_key and regions/sizes/images
read is enough. Lists are paginated with ``page`` and ``per_page`` and say so in
``links.pages.next``; errors carry ``message``. A droplet has no address until
it has been provisioned, a moment after it is created.
"""

from __future__ import annotations

from typing import Any, ClassVar

from noust.core.exceptions import CloudProviderError
from noust.fleet.cloud.models import Capabilities, Choice, CreateSpec, RemoteServer
from noust.fleet.cloud.providers.base import (
    MANAGED_BY,
    MAX_PAGES,
    PAGE_SIZE,
    CloudProvider,
    gigabytes,
    number,
    size_detail,
    text,
)

#: How a droplet's status reads in Noust's.
_STATES = {"new": "creating", "active": "running", "off": "stopped", "archive": "stopped"}


class DigitalOcean(CloudProvider):
    """DigitalOcean."""

    kind: ClassVar[str] = "digitalocean"
    display_name: ClassVar[str] = "DigitalOcean"
    api_url: ClassVar[str] = "https://api.digitalocean.com/v2"
    credentials_url: ClassVar[str] = "https://cloud.digitalocean.com/account/api/tokens"
    credentials_help: ClassVar[str] = (
        "In API > Tokens, generate a token with write access, or a custom one that can create "
        "and delete droplets, manage SSH keys and read regions, sizes and images."
    )
    capabilities: ClassVar[Capabilities] = Capabilities(
        firewalls=True,
        networks=True,
        backups=True,
        ipv4_optional=False,
        ipv6=True,
        multiple_firewalls=True,
        multiple_networks=False,
    )
    user_data_limit: ClassVar[int] = 64 * 1024

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
        for page in range(1, MAX_PAGES + 1):
            body = self.http.request(
                "GET", path, query={**(query or {}), "page": page, "per_page": PAGE_SIZE}
            )
            items = body.get(key) if isinstance(body, dict) else None
            found.extend(item for item in items or [] if isinstance(item, dict))
            links = body.get("links") if isinstance(body, dict) else None
            pages = links.get("pages") if isinstance(links, dict) else None
            if not (isinstance(pages, dict) and pages.get("next")):
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
        """Returns: The regions that are open to new droplets."""
        return [
            Choice(
                id=text(item.get("slug")),
                name=text(item.get("name")),
                detail=text(item.get("slug")),
            )
            for item in self._pages("/regions", "regions")
            if item.get("slug") and item.get("available", True) is True
        ]

    def plans(self) -> list[Choice]:
        """Returns: The sizes that are on sale."""
        plans = []
        for item in self._pages("/sizes", "sizes"):
            if not item.get("slug") or item.get("available", True) is not True:
                continue
            monthly = number(item.get("price_monthly"))
            plans.append(
                Choice(
                    id=text(item["slug"]),
                    name=text(item.get("description")) or text(item["slug"]),
                    detail=size_detail(
                        number(item.get("vcpus")),
                        gigabytes(number(item.get("memory")), "MB"),
                        number(item.get("disk")),
                    ),
                    extra={
                        "locations": [text(r) for r in item.get("regions") or []],
                        "monthly": monthly,
                        "arch": "x86",
                    },
                )
            )
        return plans

    def images(self) -> list[Choice]:
        """Returns: The public distribution images."""
        return [
            Choice(
                id=text(item.get("slug")) or text(item.get("id")),
                name=f"{text(item.get('distribution'))} {text(item.get('name'))}".strip(),
                detail=text(item.get("description")),
                extra={
                    "distribution": text(item.get("distribution")),
                    "version": text(item.get("name")),
                    "archs": ["x86"],
                },
            )
            for item in self._pages("/images", "images", {"type": "distribution"})
            if (item.get("slug") or item.get("id")) and item.get("public", True) is True
        ]

    def ssh_keys(self) -> list[Choice]:
        """Returns: The account's SSH keys."""
        return [
            Choice(
                id=text(item.get("id")),
                name=text(item.get("name")),
                detail=text(item.get("fingerprint")),
            )
            for item in self._pages("/account/keys", "ssh_keys")
            if item.get("id") is not None
        ]

    def firewalls(self) -> list[Choice]:
        """Returns: The account's firewalls."""
        return [
            Choice(id=text(item.get("id")), name=text(item.get("name")))
            for item in self._pages("/firewalls", "firewalls")
            if item.get("id")
        ]

    def networks(self) -> list[Choice]:
        """Returns: The account's VPC networks."""
        return [
            Choice(
                id=text(item.get("id")),
                name=text(item.get("name")),
                detail=text(item.get("ip_range")),
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
            DigitalOcean's id for it.
        """
        body = self.http.request(
            "POST", "/account/keys", body={"name": name, "public_key": public_key}
        )
        key = body.get("ssh_key") if isinstance(body, dict) else None
        if not isinstance(key, dict) or key.get("id") is None:
            raise self.unexpected("the key", body)
        return text(key["id"])

    def delete_ssh_key(self, key_id: str) -> None:
        """
        Remove a key from the account.

        Args:
            key_id: What :meth:`upload_ssh_key` returned.
        """
        self.http.request("DELETE", f"/account/keys/{key_id}")

    def create_server(self, spec: CreateSpec) -> RemoteServer:
        """
        Create a droplet.

        Args:
            spec: What to create.

        Returns:
            The droplet as DigitalOcean first reports it: no address yet.

        Raises:
            CloudProviderError: When DigitalOcean refuses; nothing was created.
        """
        self.check_spec(spec)
        body: dict[str, Any] = {
            "name": spec.name,
            "region": spec.location,
            "size": spec.plan,
            "image": int(spec.image) if spec.image.isdigit() else spec.image,
            "ipv6": spec.ipv6,
            "backups": spec.backups,
            "tags": [MANAGED_BY],
        }
        if spec.user_data:
            body["user_data"] = spec.user_data
        if spec.ssh_keys:
            body["ssh_keys"] = [int(key) if key.isdigit() else key for key in spec.ssh_keys]
        if spec.networks:
            body["vpc_uuid"] = spec.networks[0]
        answer = self.http.request("POST", "/droplets", body=body)
        droplet = answer.get("droplet") if isinstance(answer, dict) else None
        if not isinstance(droplet, dict) or droplet.get("id") is None:
            raise self.unexpected("a droplet", answer)
        return self._server(droplet)

    def after_create(self, server: RemoteServer, spec: CreateSpec) -> None:
        """
        Attach the firewalls, which DigitalOcean takes from the firewall's side.

        Args:
            server: The created droplet.
            spec: What was asked for.
        """
        for firewall in spec.firewalls:
            self.http.request(
                "POST", f"/firewalls/{firewall}/droplets", body={"droplet_ids": [int(server.id)]}
            )

    def get_server(self, server_id: str) -> RemoteServer:
        """
        Read a droplet.

        Args:
            server_id: DigitalOcean's id.

        Returns:
            Its state and addresses.
        """
        answer = self.http.request("GET", f"/droplets/{server_id}")
        droplet = answer.get("droplet") if isinstance(answer, dict) else None
        if not isinstance(droplet, dict):
            raise self.unexpected("a droplet", answer)
        return self._server(droplet)

    def delete_server(self, server_id: str) -> None:
        """
        Delete a droplet.

        Args:
            server_id: DigitalOcean's id.
        """
        self.http.request("DELETE", f"/droplets/{server_id}")

    def power_on(self, server_id: str) -> None:
        """
        Start a droplet that is off.

        Args:
            server_id: DigitalOcean's id.
        """
        self.http.request("POST", f"/droplets/{server_id}/actions", body={"type": "power_on"})

    def _server(self, item: dict[str, Any]) -> RemoteServer:
        """
        Read a droplet out of DigitalOcean's JSON.

        Args:
            item: The ``droplet`` object.

        Returns:
            The server.

        Raises:
            CloudProviderError: When it has no id.
        """
        if item.get("id") is None:
            raise CloudProviderError("DigitalOcean described a droplet without an id")
        networks: dict[str, Any] = (
            item["networks"] if isinstance(item.get("networks"), dict) else {}
        )

        def public(version: str) -> str | None:
            for network in networks.get(version) or []:
                if isinstance(network, dict) and network.get("type") == "public":
                    address = network.get("ip_address")
                    if address:
                        return str(address)
            return None

        raw = text(item.get("status"))
        return RemoteServer(
            id=text(item["id"]),
            name=text(item.get("name")),
            state=_STATES.get(raw, "unknown"),
            raw_state=raw,
            ipv4=public("v4"),
            ipv6=public("v6"),
        )
