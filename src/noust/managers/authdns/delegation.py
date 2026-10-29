# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Delegating a domain to Noust DNS: the steps at the registrar, and the check that they were done.

A zone Noust DNS serves answers nothing to the internet until the domain's registrar says so. That
is the part nobody can do for the operator, so this module says exactly what to do there, in
order, and then looks at the world to see whether it was done:

1. **Glue**, when a name server's name is inside the domain it serves (``ns1.example.com`` for
   ``example.com``): the registrar must hold its address, or nothing can find it.
2. **The name servers**: the domain's NS set at the registrar becomes the zone's.
3. **The DS record**, for a signed zone, and only after step 2 has reached the resolvers: a DS for
   a zone that the old name servers still serve unsigned makes validating resolvers refuse the
   domain.

The check asks each name server directly (does it answer, as the authority, with the same serial
as the others, and does it refuse to recurse) and asks the public resolvers what they find for
the domain now (the name servers, and the DS). What a registrar does is not visible from here, so
the check reports what resolvers see, which is what matters, and says which step is missing.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.authdns_store import AuthDnsZone
from noust.core.config import Config
from noust.core.server_text import say
from noust.managers.authdns.dnsquery import Answer, DnsError, Transport, ask
from noust.managers.authdns.dnsquery import _udp as default_transport
from noust.managers.authdns.engine import DsRecord
from noust.managers.authdns.zones import ZoneEditor
from noust.managers.dns.resolver import DEFAULT_RESOLVERS
from noust.managers.dns.resolver import resolve as resolve_addresses

__all__ = ["DelegationCheck", "DelegationGuide", "DelegationPlan", "NameServer", "ServerCheck"]

#: A name outside every zone Noust serves, asked of a name server to see whether it recurses.
_OTHER_DOMAIN = "recursion-check.example.org"


def _text(code: str, **params: Any) -> dict[str, Any]:
    """
    Args:
        code: A sentence of :mod:`noust.core.server_text`.
        **params: Its values.

    Returns:
        The sentence for the API: its code, its values, its English text and its fix.
    """
    sentence = say(code, **params)
    return {**sentence.to_dict(), "text": sentence.text, "hint": sentence.hint}


def same_network(first: str, second: str) -> bool:
    """
    Args:
        first: An address.
        second: Another.

    Returns:
        They are in one /24 (IPv4) or /48 (IPv6): an outage of that network takes both.
    """
    one, two = ipaddress.ip_address(first), ipaddress.ip_address(second)
    if one.version != two.version:
        return False
    bits = 24 if one.version == 4 else 48
    return ipaddress.ip_network(f"{one}/{bits}", strict=False) == ipaddress.ip_network(
        f"{two}/{bits}", strict=False
    )


@dataclass(frozen=True)
class NameServer:
    """
    A name server of a zone.

    Attributes:
        name: Its host name.
        addresses: Where it answers; empty when only resolving the name can say.
        glue: The registrar must hold its address (the name is inside the zone).
    """

    name: str
    addresses: tuple[str, ...]
    glue: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The name server for the API.
        """
        return {"name": self.name, "addresses": list(self.addresses), "glue": self.glue}


@dataclass(frozen=True)
class DelegationPlan:
    """
    What to do at the registrar.

    Attributes:
        zone: The zone.
        nameservers: Its name servers.
        dnssec: The zone is signed.
        ds: The DS records to give the registrar, when it is signed.
        steps: The steps, in order, as sentences.
        warnings: What would make the delegation fragile, as sentences.
    """

    zone: str
    nameservers: tuple[NameServer, ...]
    dnssec: bool
    ds: tuple[DsRecord, ...]
    steps: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan for the API.
        """
        return {
            "zone": self.zone,
            "nameservers": [server.to_dict() for server in self.nameservers],
            "dnssec": self.dnssec,
            "ds": [record.to_dict() for record in self.ds],
            "steps": list(self.steps),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ServerCheck:
    """
    What one name server said when asked directly.

    Attributes:
        name: Its host name.
        address: The address asked.
        answers: It answered.
        authoritative: It answered as the authority for the zone.
        serial: The SOA serial it serves.
        refuses_recursion: It refused a question about another domain; None when not asked.
    """

    name: str
    address: str
    answers: bool
    authoritative: bool = False
    serial: int | None = None
    refuses_recursion: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API.
        """
        return {
            "name": self.name,
            "address": self.address,
            "answers": self.answers,
            "authoritative": self.authoritative,
            "serial": self.serial,
            "refuses_recursion": self.refuses_recursion,
        }


@dataclass
class DelegationCheck:
    """
    Whether the delegation is in place.

    Attributes:
        zone: The zone.
        servers: What each name server answered.
        delegated: Public resolvers find exactly the zone's name servers; None when none answered.
        public_nameservers: The name servers they find.
        ds_published: The parent holds a DS; None when not asked or not answered.
        ds_matches: It is this zone's key.
        problems: What is not as it should be, as sentences.
        ready: Nothing is wrong.
    """

    zone: str
    servers: list[ServerCheck] = field(default_factory=list)
    delegated: bool | None = None
    public_nameservers: list[str] = field(default_factory=list)
    ds_published: bool | None = None
    ds_matches: bool | None = None
    problems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Nothing is wrong, and the world sees the delegation."""
        return not self.problems and self.delegated is True

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API.
        """
        return {
            "zone": self.zone,
            "servers": [server.to_dict() for server in self.servers],
            "delegated": self.delegated,
            "public_nameservers": self.public_nameservers,
            "ds_published": self.ds_published,
            "ds_matches": self.ds_matches,
            "problems": self.problems,
            "ready": self.ready,
        }


class DelegationGuide:
    """
    Say how to delegate a zone to Noust DNS, and check it.

    Args:
        editor: The zone editor of this server.
        resolvers: The public resolvers to ask; ``dns.resolvers`` by default.
        transport: Sends one question (tests replace it).
        resolve: Resolves a host name to addresses (tests replace it).
    """

    def __init__(
        self,
        editor: ZoneEditor | None = None,
        resolvers: Sequence[str] | None = None,
        transport: Transport = default_transport,
        resolve: Callable[[str], tuple[list[str], str | None]] | None = None,
    ) -> None:
        self.editor = editor or ZoneEditor()
        self._resolvers = resolvers
        self._transport = transport
        self._resolve = resolve or (lambda name: resolve_addresses(name, self.resolvers))

    @property
    def resolvers(self) -> tuple[str, ...]:
        """The public resolvers asked."""
        if self._resolvers is not None:
            return tuple(self._resolvers)
        return tuple(str(r) for r in Config().get("dns.resolvers") or DEFAULT_RESOLVERS)

    # -- the steps -----------------------------------------------------------------------

    def _zone(self, name: str) -> AuthDnsZone:
        return self.editor.get(name).zone

    def nameservers(self, zone: AuthDnsZone) -> list[NameServer]:
        """
        Args:
            zone: A zone.

        Returns:
            Its name servers with the addresses the zone itself holds for those inside it.
        """
        records = self.editor.records.records(zone.name)
        found = []
        for host in zone.nameservers:
            inside = host == zone.name or host.endswith(f".{zone.name}")
            label = "@" if host == zone.name else host[: -len(zone.name) - 1]
            addresses = tuple(
                r.content for r in records if inside and r.name == label and r.type in ("A", "AAAA")
            )
            found.append(NameServer(host, addresses, inside))
        return found

    def plan(self, name: str) -> DelegationPlan:
        """
        Say exactly what to do at the registrar.

        Args:
            name: The zone.

        Returns:
            The steps, the DS records when the zone is signed, and what would make it fragile.

        Raises:
            ValidationError: There is no such zone here.
        """
        zone = self._zone(name)
        servers = self.nameservers(zone)
        ds = tuple(self.editor.dnssec(zone.name).ds) if zone.dnssec else ()
        steps: list[dict[str, Any]] = []
        glued = [server for server in servers if server.glue]
        if glued:
            steps.append(
                _text(
                    "authdns.delegation.glue",
                    hosts=", ".join(server.name for server in glued),
                    addresses="; ".join(f"{s.name} {' '.join(s.addresses)}" for s in glued),
                )
            )
        steps.append(
            _text(
                "authdns.delegation.nameservers",
                zone=zone.name,
                nameservers=", ".join(server.name for server in servers),
            )
        )
        if zone.dnssec:
            steps.append(
                _text(
                    "authdns.delegation.ds",
                    zone=zone.name,
                    ds="; ".join(record.text for record in ds)
                    or "(the key is being made: ask again)",
                )
            )
        steps.append(_text("authdns.delegation.check", command=f"noust dns zone check {zone.name}"))
        warnings: list[dict[str, Any]] = []
        if len(servers) < 2:
            warnings.append(_text("authdns.delegation.one_server", zone=zone.name))
        known = self._addresses(servers)
        for index, first in enumerate(known):
            for second in known[index + 1 :]:
                if first[1] and second[1] and same_network(first[1][0], second[1][0]):
                    warnings.append(
                        _text("authdns.delegation.same_network", first=first[0], second=second[0])
                    )
        return DelegationPlan(
            zone=zone.name,
            nameservers=tuple(servers),
            dnssec=zone.dnssec,
            ds=ds,
            steps=tuple(steps),
            warnings=tuple(warnings),
        )

    def _addresses(self, servers: Sequence[NameServer]) -> list[tuple[str, tuple[str, ...]]]:
        """
        Args:
            servers: Name servers.

        Returns:
            Each with the addresses it answers on: the zone's glue, else this server's own and its
            secondaries', else what the name resolves to.
        """
        own = self.editor.records.server()
        secondaries = {
            s.hostname: s.address for s in self.editor.records.secondaries() if s.hostname
        }
        found: list[tuple[str, tuple[str, ...]]] = []
        for server in servers:
            if server.addresses:
                found.append((server.name, server.addresses))
            elif own is not None and own.hostname == server.name and own.listen:
                found.append((server.name, tuple(own.listen)))
            elif server.name in secondaries:
                found.append((server.name, (secondaries[server.name],)))
            else:
                resolved, _asked = self._resolve(server.name)
                found.append((server.name, tuple(resolved)))
        return found

    # -- the check -----------------------------------------------------------------------

    def check(self, name: str) -> DelegationCheck:
        """
        Look at the world and say whether the delegation is in place.

        Args:
            name: The zone.

        Returns:
            What each name server answers, what public resolvers find, and what is wrong.

        Raises:
            ValidationError: There is no such zone here.
        """
        zone = self._zone(name)
        result = DelegationCheck(zone=zone.name)
        servers = self.nameservers(zone)
        for host, addresses in self._addresses(servers):
            for address in addresses:
                result.servers.append(self._ask_server(zone.name, host, address))
        self._judge_servers(zone.name, result)
        self._ask_public(zone, [s.name for s in servers], result)
        return result

    def _ask_server(self, zone: str, host: str, address: str) -> ServerCheck:
        """
        Args:
            zone: The zone.
            host: The name server's name.
            address: The address to ask.

        Returns:
            What it answered for the zone, and whether it refuses to recurse.
        """
        try:
            answer = ask(address, zone, "SOA", transport=self._transport)
        except DnsError:
            return ServerCheck(host, address, answers=False)
        soa = answer.of_type("SOA")
        serial = int(soa[0].data.rsplit(" ", 1)[-1]) if soa else None
        refuses: bool | None
        try:
            other = ask(address, _OTHER_DOMAIN, "A", recursion=True, transport=self._transport)
            refuses = other.rcode == 5 and not other.recursion_available and not other.answers
        except DnsError:
            refuses = None
        return ServerCheck(
            host,
            address,
            answers=True,
            authoritative=answer.authoritative and answer.rcode == 0 and bool(soa),
            serial=serial,
            refuses_recursion=refuses,
        )

    def _judge_servers(self, zone: str, result: DelegationCheck) -> None:
        """
        Args:
            zone: The zone.
            result: The check so far; its problems are added to.
        """
        for server in result.servers:
            if not server.answers:
                result.problems.append(
                    _text(
                        "authdns.check.silent",
                        server=server.name,
                        address=server.address,
                        zone=zone,
                    )
                )
            elif not server.authoritative:
                result.problems.append(
                    _text(
                        "authdns.check.not_authoritative",
                        server=server.name,
                        address=server.address,
                        zone=zone,
                    )
                )
            if server.refuses_recursion is False:
                result.problems.append(
                    _text("authdns.check.recurses", server=server.name, address=server.address)
                )
        serials = {s.serial for s in result.servers if s.authoritative and s.serial is not None}
        if len(serials) > 1:
            ordered = sorted(
                (s for s in result.servers if s.serial is not None),
                key=lambda s: s.serial or 0,
            )
            behind, ahead = ordered[0], ordered[-1]
            result.problems.append(
                _text(
                    "authdns.check.out_of_sync",
                    server=behind.name,
                    serial=behind.serial or 0,
                    zone=zone,
                    primary=ahead.name,
                    primary_serial=ahead.serial or 0,
                )
            )

    def _ask_public(self, zone: AuthDnsZone, expected: list[str], result: DelegationCheck) -> None:
        """
        Args:
            zone: The zone.
            expected: Its name servers.
            result: The check so far; filled with what resolvers find.
        """
        answered: Answer | None = None
        ds_answer: Answer | None = None
        for resolver in self.resolvers:
            try:
                answered = ask(resolver, zone.name, "NS", recursion=True, transport=self._transport)
                ds_answer = ask(
                    resolver,
                    zone.name,
                    "DS",
                    recursion=True,
                    dnssec=True,
                    transport=self._transport,
                )
            except DnsError:
                continue
            break
        if answered is None:
            result.problems.append(_text("authdns.check.no_public_answer", zone=zone.name))
            return
        found = sorted({record.data for record in answered.of_type("NS")})
        result.public_nameservers = found
        result.delegated = bool(found) and set(found) == set(expected)
        if not result.delegated:
            result.problems.append(
                _text(
                    "authdns.check.not_delegated",
                    zone=zone.name,
                    found=", ".join(found) or "no name servers",
                )
            )
        if ds_answer is None:
            return
        published = {record.data for record in ds_answer.of_type("DS")}
        result.ds_published = bool(published)
        if zone.dnssec:
            wanted = {r.text for r in self.editor.dnssec(zone.name).ds}
            result.ds_matches = bool(published & wanted)
            if not published and result.delegated:
                result.problems.append(_text("authdns.check.ds_missing", zone=zone.name))
            elif published and not result.ds_matches:
                result.problems.append(_text("authdns.check.ds_mismatch", zone=zone.name))
        elif published:
            result.problems.append(_text("authdns.check.ds_dangling", zone=zone.name))
