# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Hetzner Cloud (https://docs.hetzner.cloud): servers, locations, server types, images.

A token is created per project in the Cloud Console (Security > API tokens) and
needs Read & Write. Lists are paginated with ``page`` and ``per_page``; errors
carry ``error.message``; a server's IPv4 address is known as soon as it is
created and its IPv6 one is the first address of the ``/64`` it is given.
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

#: How Hetzner's server states read in Noust's.
_STATES = {
    "initializing": "creating",
    "starting": "creating",
    "migrating": "creating",
    "rebuilding": "creating",
    "running": "running",
    "stopping": "stopped",
    "off": "stopped",
    "deleting": "deleting",
}


def _identifier(value: str) -> int | str:
    """
    Spell an id the way Hetzner takes it: a number when it is one.

    Args:
        value: An id or a name, as text.

    Returns:
        The integer, or the text unchanged.
    """
    return int(value) if value.isdigit() else value


def first_address(prefix: str | None) -> str | None:
    """
    Turn the ``/64`` Hetzner reports for IPv6 into the address the server answers on.

    Args:
        prefix: ``2001:db8:1::/64``.

    Returns:
        ``2001:db8:1::1``; None when there is none.
    """
    if not prefix:
        return None
    network = prefix.split("/", 1)[0]
    return f"{network}1" if network.endswith("::") else network


class HetznerCloud(CloudProvider):
    """Hetzner Cloud."""

    kind: ClassVar[str] = "hetzner"
    display_name: ClassVar[str] = "Hetzner Cloud"
    api_url: ClassVar[str] = "https://api.hetzner.cloud/v1"
    credentials_url: ClassVar[str] = "https://console.hetzner.cloud/projects"
    credentials_help: ClassVar[str] = (
        "In the project's Security > API tokens, create a token with Read & Write permission."
    )
    capabilities: ClassVar[Capabilities] = Capabilities(
        firewalls=True,
        networks=True,
        backups=True,
        ipv4_optional=True,
        ipv6=True,
        multiple_firewalls=True,
        multiple_networks=True,
    )
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
        page: int | None = 1
        for _ in range(MAX_PAGES):
            if page is None:
                break
            body = self.http.request(
                "GET", path, query={**(query or {}), "page": page, "per_page": PAGE_SIZE}
            )
            items = body.get(key) if isinstance(body, dict) else None
            found.extend(item for item in items or [] if isinstance(item, dict))
            pagination = (
                (body.get("meta") or {}).get("pagination") if isinstance(body, dict) else {}
            )
            next_page = pagination.get("next_page") if isinstance(pagination, dict) else None
            page = int(next_page) if isinstance(next_page, int) else None
        return found

    def verify(self) -> str:
        """Returns: The provider's name; Hetzner has no account endpoint."""
        self.http.request("GET", "/locations", query={"per_page": 1})
        return self.display_name

    def locations(self) -> list[Choice]:
        """Returns: Hetzner's locations."""
        return [
            Choice(
                id=text(item.get("name")),
                name=f"{text(item.get('city'))}, {text(item.get('country'))} ({text(item.get('name'))})",
                detail=text(item.get("description")),
                extra={"zone": text(item.get("network_zone"))},
            )
            for item in self._pages("/locations", "locations")
            if item.get("name")
        ]

    def plans(self) -> list[Choice]:
        """Returns: Hetzner's server types that are not deprecated."""
        plans = []
        for item in self._pages("/server_types", "server_types"):
            if not item.get("name") or item.get("deprecated") is True:
                continue
            prices = [p for p in item.get("prices") or [] if isinstance(p, dict)]
            monthly = None
            if prices:
                gross = (prices[0].get("price_monthly") or {}).get("gross")
                monthly = float(gross) if isinstance(gross, (str, int, float)) else None
            plans.append(
                Choice(
                    id=text(item["name"]),
                    name=text(item["name"]).upper(),
                    detail=size_detail(
                        number(item.get("cores")),
                        gigabytes(number(item.get("memory"))),
                        number(item.get("disk")),
                    ),
                    extra={
                        "arch": text(item.get("architecture")),
                        "locations": [text(p.get("location")) for p in prices if p.get("location")],
                        "monthly": monthly,
                    },
                )
            )
        return plans

    def images(self) -> list[Choice]:
        """Returns: Hetzner's system images, one entry per name with the architectures it has."""
        by_name: dict[str, Choice] = {}
        archs: dict[str, list[str]] = {}
        for item in self._pages("/images", "images", {"type": "system", "status": "available"}):
            name = text(item.get("name"))
            if not name or item.get("deprecated"):
                continue
            arch = text(item.get("architecture"))
            archs.setdefault(name, [])
            if arch and arch not in archs[name]:
                archs[name].append(arch)
            if name not in by_name:
                by_name[name] = Choice(
                    id=name,
                    name=text(item.get("description")) or name,
                    detail=f"{text(item.get('os_flavor'))} {text(item.get('os_version'))}".strip(),
                    extra={
                        "distribution": text(item.get("os_flavor")),
                        "version": text(item.get("os_version")),
                    },
                )
        return [
            Choice(c.id, c.name, c.detail, {**c.extra, "archs": archs[c.id]})
            for c in by_name.values()
        ]

    def ssh_keys(self) -> list[Choice]:
        """Returns: The project's SSH keys."""
        return [
            Choice(
                id=text(item.get("id")),
                name=text(item.get("name")),
                detail=text(item.get("fingerprint")),
            )
            for item in self._pages("/ssh_keys", "ssh_keys")
            if item.get("id") is not None
        ]

    def firewalls(self) -> list[Choice]:
        """Returns: The project's firewalls."""
        return [
            Choice(id=text(item.get("id")), name=text(item.get("name")))
            for item in self._pages("/firewalls", "firewalls")
            if item.get("id") is not None
        ]

    def networks(self) -> list[Choice]:
        """Returns: The project's private networks."""
        return [
            Choice(
                id=text(item.get("id")),
                name=text(item.get("name")),
                detail=text(item.get("ip_range")),
            )
            for item in self._pages("/networks", "networks")
            if item.get("id") is not None
        ]

    def upload_ssh_key(self, name: str, public_key: str) -> str:
        """
        Put a public key in the project.

        Args:
            name: What to call it.
            public_key: One OpenSSH public key line.

        Returns:
            Hetzner's id for it.
        """
        body = self.http.request("POST", "/ssh_keys", body={"name": name, "public_key": public_key})
        key = body.get("ssh_key") if isinstance(body, dict) else None
        if not isinstance(key, dict) or key.get("id") is None:
            raise self.unexpected("the key", body)
        return text(key["id"])

    def delete_ssh_key(self, key_id: str) -> None:
        """
        Remove a key from the project.

        Args:
            key_id: What :meth:`upload_ssh_key` returned.
        """
        self.http.request("DELETE", f"/ssh_keys/{_identifier(key_id)}")

    def create_server(self, spec: CreateSpec) -> RemoteServer:
        """
        Create a server.

        Args:
            spec: What to create.

        Returns:
            The server as Hetzner first reports it; its IPv4 address is already known.

        Raises:
            CloudProviderError: When Hetzner refuses; nothing was created.
        """
        self.check_spec(spec)
        body: dict[str, Any] = {
            "name": spec.name,
            "server_type": spec.plan,
            "image": spec.image,
            "location": spec.location,
            "start_after_create": True,
            "public_net": {"enable_ipv4": spec.ipv4, "enable_ipv6": spec.ipv6},
            "labels": {"managed-by": MANAGED_BY},
        }
        if spec.user_data:
            body["user_data"] = spec.user_data
        if spec.ssh_keys:
            body["ssh_keys"] = [_identifier(key) for key in spec.ssh_keys]
        if spec.firewalls:
            body["firewalls"] = [{"firewall": _identifier(fw)} for fw in spec.firewalls]
        if spec.networks:
            body["networks"] = [_identifier(net) for net in spec.networks]
        answer = self.http.request("POST", "/servers", body=body)
        # The root password Hetzner prints here is never read: nothing logs in with it.
        server = answer.get("server") if isinstance(answer, dict) else None
        if not isinstance(server, dict) or server.get("id") is None:
            raise self.unexpected("a server", answer)
        return self._server(server)

    def after_create(self, server: RemoteServer, spec: CreateSpec) -> None:
        """
        Turn the backups on, which Hetzner takes as an action of the server.

        Args:
            server: The created server.
            spec: What was asked for.
        """
        if spec.backups:
            self.http.request("POST", f"/servers/{_identifier(server.id)}/actions/enable_backup")

    def get_server(self, server_id: str) -> RemoteServer:
        """
        Read a server.

        Args:
            server_id: Hetzner's id.

        Returns:
            Its state and addresses.
        """
        answer = self.http.request("GET", f"/servers/{_identifier(server_id)}")
        server = answer.get("server") if isinstance(answer, dict) else None
        if not isinstance(server, dict):
            raise self.unexpected("a server", answer)
        return self._server(server)

    def delete_server(self, server_id: str) -> None:
        """
        Delete a server.

        Args:
            server_id: Hetzner's id.
        """
        self.http.request("DELETE", f"/servers/{_identifier(server_id)}")

    def power_on(self, server_id: str) -> None:
        """
        Start a server that is off.

        Args:
            server_id: Hetzner's id.
        """
        self.http.request("POST", f"/servers/{_identifier(server_id)}/actions/poweron")

    def _server(self, item: dict[str, Any]) -> RemoteServer:
        """
        Read a server out of Hetzner's JSON.

        Args:
            item: The ``server`` object.

        Returns:
            The server.

        Raises:
            CloudProviderError: When it has no id.
        """
        if item.get("id") is None:
            raise CloudProviderError("Hetzner Cloud described a server without an id")
        net: dict[str, Any] = item["public_net"] if isinstance(item.get("public_net"), dict) else {}
        v4: dict[str, Any] = net["ipv4"] if isinstance(net.get("ipv4"), dict) else {}
        v6: dict[str, Any] = net["ipv6"] if isinstance(net.get("ipv6"), dict) else {}
        ipv4, ipv6 = v4.get("ip"), v6.get("ip")
        raw = text(item.get("status"))
        return RemoteServer(
            id=text(item["id"]),
            name=text(item.get("name")),
            state=_STATES.get(raw, "unknown"),
            raw_state=raw,
            ipv4=ipv4 or None,
            ipv6=first_address(ipv6),
        )
