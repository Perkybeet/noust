# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The private networks this server is on: a Tailscale tailnet and WireGuard interfaces.

An application can be published to one of them only: its site answers the network's addresses and
refuses everybody else (:mod:`noust.deployers.private_access`). What is here is the half that
belongs to the machine and not to an application: which networks exist, which addresses are theirs,
and, for Tailscale, the name the tailnet gives this server and whether it can issue a certificate
for it.

Nothing here changes anything. Both programs are only asked what they know (``ip -j``,
``tailscale status --json``), through the runner like everything else, and a program that is not
installed is a network that is not there.

- **Tailscale.** The tailnet is the CGNAT range Tailscale assigns (``100.64.0.0/10``) and its IPv6
  unique-local prefix. Every device of the tailnet is inside it, so allowing it is "any device the
  tailnet's own access rules let reach this server"; narrower than that is the tailnet's job
  (its ACLs), which is the place that already knows who is who.
- **WireGuard.** An interface of type ``wireguard``; its network is the one its own address sits
  in (``10.8.0.1/24`` is ``10.8.0.0/24``), which is the peers' range by the usual convention.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.runner import CommandRunner, get_runner
from noust.core.teams.model import ResourceNotFoundError

#: The most ranges an operator may name for one site.
MAX_CIDRS = 20

#: Seconds a look at the network may take.
PROBE_TIMEOUT = 10

#: Tailscale's address ranges: the CGNAT block it hands out and its IPv6 prefix.
TAILSCALE_CIDRS = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")

#: The name Noust gives the tailnet, whatever it is called there.
TAILSCALE = "tailscale"

#: Read-only probes, declared by exact argv for ``--dry-run``.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    ("ip", "-j", "address", "show"),
    ("ip", "-j", "link", "show", "type", "wireguard"),
    ("tailscale", "status", "--json"),
)

#: The only blocks a private site may answer.
_PRIVATE_BLOCKS = tuple(
    ipaddress.ip_network(block)
    for block in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
)
_INTERFACE = re.compile(r"[A-Za-z0-9_.=+-]{1,15}")
_DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")


@dataclass(frozen=True)
class PrivateNetwork:
    """
    A network only some devices are on.

    Attributes:
        name: What it is called to Noust: ``tailscale``, or a WireGuard interface's name.
        kind: ``tailscale`` or ``wireguard``.
        interface: The network interface, empty for a tailnet in userspace mode.
        cidrs: The networks a device of it has an address in.
        addresses: This server's own addresses on it.
        up: The network is connected now.
        dns_name: What the tailnet calls this server (``web.tail1234.ts.net``).
        tailnet: The tailnet's name.
        cert_domains: Names Tailscale will issue a certificate for.
        problem: Why it is not usable, when it is not.
    """

    name: str
    kind: str
    interface: str = ""
    cidrs: tuple[str, ...] = ()
    addresses: tuple[str, ...] = ()
    up: bool = True
    dns_name: str = ""
    tailnet: str = ""
    cert_domains: tuple[str, ...] = field(default_factory=tuple)
    problem: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "interface": self.interface,
            "cidrs": list(self.cidrs),
            "addresses": list(self.addresses),
            "up": self.up,
            "dns_name": self.dns_name,
            "tailnet": self.tailnet,
            "cert_domains": list(self.cert_domains),
            "problem": self.problem,
        }


def is_private_range(cidr: str) -> bool:
    """
    Tell whether a network is one nobody on the public internet has an address in.

    Python's own notion of "private" also covers the documentation and benchmarking blocks, which
    are not networks anybody has devices in, so the ranges are spelled out here.

    Args:
        cidr: An address or a network.

    Returns:
        True when it lies inside RFC 1918, the CGNAT block Tailscale and carriers use, the unique
        local and link-local blocks, or loopback; False for anything else, and for text that is
        not a network.
    """
    try:
        network = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return False
    return any(
        network.version == block.version and network.subnet_of(block)  # type: ignore[arg-type]
        for block in _PRIVATE_BLOCKS
    )


def _json(text: str) -> Any:
    """
    Args:
        text: A program's JSON output.

    Returns:
        The decoded document, or None when it is not JSON.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


class PrivateNetworks:
    """
    Look at the private networks of this server.

    Args:
        runner: What the programs run through; the process-wide runner by default.
    """

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner

    @property
    def runner(self) -> CommandRunner:
        """What the programs run through."""
        return self._runner or get_runner()

    def discover(self) -> list[PrivateNetwork]:
        """
        Returns:
            The tailnet, when this server is on one, then each WireGuard interface.
        """
        found: list[PrivateNetwork] = []
        tailnet = self._tailscale()
        if tailnet is not None:
            found.append(tailnet)
        found.extend(self._wireguard())
        return found

    def get(self, name: str) -> PrivateNetwork:
        """
        Args:
            name: ``tailscale`` or a WireGuard interface.

        Returns:
            That network.

        Raises:
            ResourceNotFoundError: This server is not on a network of that name.
        """
        for network in self.discover():
            if network.name == name:
                return network
        raise ResourceNotFoundError(
            f"This server is not on a private network called {name}",
            details="List them with: noust app policy private networks",
        )

    # -- Tailscale ---------------------------------------------------------

    def _tailscale(self) -> PrivateNetwork | None:
        """
        Returns:
            The tailnet, or None when Tailscale is not installed or has nothing to say.
        """
        if not self.runner.exists("tailscale"):
            return None
        result = self.runner.run(["tailscale", "status", "--json"], timeout=PROBE_TIMEOUT)
        document = _json(result.stdout) if result.stdout else None
        if not isinstance(document, dict):
            return PrivateNetwork(
                name=TAILSCALE,
                kind="tailscale",
                cidrs=TAILSCALE_CIDRS,
                up=False,
                problem=(result.stderr or result.stdout or "tailscale did not answer").strip()[
                    :300
                ],
            )
        state = str(document.get("BackendState") or "")
        own = document.get("Self")
        me = own if isinstance(own, dict) else {}
        dns_name = str(me.get("DNSName") or "").rstrip(".").lower()
        tailnet_info = document.get("CurrentTailnet")
        tailnet = str(tailnet_info.get("Name") or "") if isinstance(tailnet_info, dict) else ""
        addresses = tuple(
            address
            for address in (str(item) for item in me.get("TailscaleIPs") or [])
            if _is_address(address)
        )
        domains = tuple(
            name
            for name in (
                str(item).rstrip(".").lower() for item in document.get("CertDomains") or []
            )
            if _DNS_NAME.fullmatch(name)
        )
        up = state == "Running"
        return PrivateNetwork(
            name=TAILSCALE,
            kind="tailscale",
            interface="tailscale0" if "tailscale0" in self._addresses() else "",
            cidrs=TAILSCALE_CIDRS,
            addresses=addresses,
            up=up,
            dns_name=dns_name if _DNS_NAME.fullmatch(dns_name) else "",
            tailnet=tailnet,
            cert_domains=domains,
            problem="" if up else f"Tailscale is {state or 'not running'}",
        )

    # -- WireGuard ---------------------------------------------------------

    def _wireguard(self) -> list[PrivateNetwork]:
        """
        Returns:
            One network per WireGuard interface, with the network its address is in.
        """
        links = self.runner.run(
            ["ip", "-j", "link", "show", "type", "wireguard"], timeout=PROBE_TIMEOUT
        )
        listed = _json(links.stdout) if links.success and links.stdout else None
        if not isinstance(listed, list):
            return []
        names = [
            str(item["ifname"])
            for item in listed
            if isinstance(item, dict) and _INTERFACE.fullmatch(str(item.get("ifname") or ""))
        ]
        if not names:
            return []
        addresses = self._addresses()
        networks = []
        for name in names:
            own = addresses.get(name, [])
            cidrs = tuple(dict.fromkeys(str(interface.network) for interface in own))
            networks.append(
                PrivateNetwork(
                    name=name,
                    kind="wireguard",
                    interface=name,
                    cidrs=cidrs,
                    addresses=tuple(str(interface.ip) for interface in own),
                    up=bool(own),
                    problem="" if own else f"{name} has no address",
                )
            )
        return networks

    def _addresses(self) -> dict[str, list[ipaddress.IPv4Interface | ipaddress.IPv6Interface]]:
        """
        Returns:
            Each interface's addresses with their prefix, global ones only.
        """
        result = self.runner.run(["ip", "-j", "address", "show"], timeout=PROBE_TIMEOUT)
        listed = _json(result.stdout) if result.success and result.stdout else None
        out: dict[str, list[ipaddress.IPv4Interface | ipaddress.IPv6Interface]] = {}
        if not isinstance(listed, list):
            return out
        for item in listed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("ifname") or "")
            for info in item.get("addr_info") or []:
                if not isinstance(info, dict) or info.get("scope") not in ("global", None):
                    continue
                try:
                    out.setdefault(name, []).append(
                        ipaddress.ip_interface(f"{info['local']}/{info['prefixlen']}")
                    )
                except (KeyError, ValueError):
                    continue
        return out


def _is_address(text: str) -> bool:
    """
    Args:
        text: Anything.

    Returns:
        Whether it is an IP address.
    """
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def checked_cidrs(values: list[str]) -> list[str]:
    """
    Validate networks an operator names for themselves.

    Args:
        values: Addresses or networks.

    Returns:
        Each as a network, in the order given, without repeats.

    Raises:
        ValidationError: There are too many, one is not a network, or is a public one (a "private" site that
            answers the public internet is the opposite of what was asked).
    """
    if len(values) > MAX_CIDRS:
        raise ValidationError(f"At most {MAX_CIDRS} ranges", field="cidrs")
    out: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError:
            raise ValidationError(
                f"{value!r} is not an address or a network", field="cidrs"
            ) from None
        if not is_private_range(str(network)):
            raise ValidationError(
                f"{network} is a public network",
                details="A private site answers networks only some devices are in: "
                "10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 or fc00::/7.",
                field="cidrs",
            )
        if str(network) not in out:
            out.append(str(network))
    return out
