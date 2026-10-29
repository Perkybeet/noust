# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The zone editor of Noust DNS, and DNSSEC.

The store is the truth about a zone (``authdns_zones`` and ``authdns_records``) and the name
server is told what it says: every change is made in the store, the zone file is rendered from it
(:mod:`noust.managers.authdns.zonefile`), and Knot is asked to read it. When Knot does not take the
change (it checks the zone before it serves it, and keeps serving the previous one) the store is put
back as it was and the error carries Knot's own words, so the store and the name server never
disagree about what a zone holds.

What a zone may hold together is decided here, at the one door every caller uses (the API, the
command line and the central): a CNAME stands alone at its name and not at the apex, the apex
name servers are the zone's own, and a zone whose name servers are inside it has their addresses.

**DNSSEC** is Knot's: turning it on adds the zone to the signing policy and Knot makes the key,
signs and keeps signing. What the operator is given is the DS record, which the registrar needs.
Turning it off while the registrar still has the DS makes the domain unreachable for every
validating resolver, so it is refused unless the caller says the DS is gone.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from noust.core import audit, paths
from noust.core.authdns_store import (
    PRIMARY,
    AuthDnsRecord,
    AuthDnsServer,
    AuthDnsZone,
    ZoneSnapshot,
    check_address,
)
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore
from noust.managers.authdns.engine import DsRecord, ZoneStatus
from noust.managers.authdns.lock import editing
from noust.managers.authdns.server import AuthDnsServers
from noust.managers.authdns.zonefile import render_zone
from noust.validators.dns_zone import (
    check_contact,
    check_hostname,
    check_nameservers,
    check_record,
    check_record_name,
    check_record_type,
    check_ttl,
    check_zone_name,
)

log = logging.getLogger(__name__)

__all__ = ["DnssecInfo", "ZoneDetail", "ZoneEditor", "ZoneSummary"]

#: How many times, a second apart, a key that was just asked for is looked for.
KEY_ATTEMPTS = 20


@dataclass(frozen=True)
class ZoneSummary:
    """
    A zone in a list.

    Attributes:
        name: The zone.
        records: How many records it holds (a primary's).
        serial: The SOA serial the server serves now; None when it does not say.
        dnssec: The zone is signed.
        role: ``master`` where the server holds the zone, ``slave`` where it copies it.
    """

    name: str
    records: int
    serial: int | None
    dnssec: bool
    role: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The summary for the API.
        """
        return {
            "name": self.name,
            "records": self.records,
            "serial": self.serial,
            "dnssec": self.dnssec,
            "role": self.role,
        }


@dataclass(frozen=True)
class ZoneDetail:
    """
    A zone with its records.

    Attributes:
        zone: The zone.
        records: Its records.
        serial: The SOA serial served now.
    """

    zone: AuthDnsZone
    records: tuple[AuthDnsRecord, ...]
    serial: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The zone for the API.
        """
        return {
            **self.zone.to_dict(),
            "serial": self.serial,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True)
class DnssecInfo:
    """
    The state of a zone's signing.

    Attributes:
        zone: The zone.
        enabled: It is signed.
        algorithm: How (``ECDSAP256SHA256``).
        ds: The DS records to give the registrar; empty until the key exists.
    """

    zone: str
    enabled: bool
    algorithm: str = "ECDSAP256SHA256"
    ds: tuple[DsRecord, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The state for the API.
        """
        return {
            "zone": self.zone,
            "enabled": self.enabled,
            "algorithm": self.algorithm,
            "ds": [record.to_dict() for record in self.ds],
        }


def _inside(name: str, zone: str) -> bool:
    return name == zone or name.endswith(f".{zone}")


class ZoneEditor:
    """
    Make, change and remove the zones a primary serves.

    Args:
        store: The store; the process-wide one by default.
        servers: This server's part, whose engine is told of every change.
        clock: The time, for the serial a zone starts at.
        sleep: Waits (tests pass one that does not).
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        servers: AuthDnsServers | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.servers = servers or AuthDnsServers(store)
        self.records = self.servers.records
        self.engine = self.servers.engine
        self._clock = clock
        self._sleep = sleep

    # -- reading -------------------------------------------------------------------------

    def _server(self) -> AuthDnsServer:
        server = self.records.server()
        if server is None:
            raise ValidationError(
                "Noust DNS is not set up on this server",
                details="Set it up first: noust dns server setup",
                field="server",
            )
        return server

    def _primary(self) -> AuthDnsServer:
        server = self._server()
        if server.role != PRIMARY:
            raise ValidationError(
                "This server copies its zones from a primary",
                details="Edit zones on the server that holds them.",
                field="role",
            )
        return server

    def zones(self) -> list[ZoneSummary]:
        """
        Returns:
            The zones this server serves: a primary's own, or the ones a secondary copied.

        Raises:
            ValidationError: Noust DNS is not set up here.
        """
        server = self._server()
        status = self.engine.zone_status()
        if server.role == PRIMARY:
            return [
                ZoneSummary(
                    zone.name,
                    len(self.records.records(zone.name)),
                    status[zone.name].serial if zone.name in status else None,
                    zone.dnssec,
                    "master",
                )
                for zone in self.records.zones()
            ]
        return [
            ZoneSummary(name, 0, item.serial, False, item.role)
            for name, item in sorted(status.items())
            if name != paths.AUTHDNS_CATALOG
        ]

    def get(self, name: str) -> ZoneDetail:
        """
        Args:
            name: The zone.

        Returns:
            It, with its records.

        Raises:
            ValidationError: There is no such zone here.
        """
        self._primary()
        zone = self._zone(check_zone_name(name))
        status = self.engine.zone_status().get(zone.name)
        return ZoneDetail(
            zone, tuple(self.records.records(zone.name)), status.serial if status else None
        )

    def _zone(self, name: str) -> AuthDnsZone:
        zone = self.records.zone(name)
        if zone is None:
            raise ValidationError(
                f"{name} is not a zone of this server",
                details="Make it: noust dns zone create " + name,
                field="zone",
            )
        return zone

    # -- the mechanism every change goes through -----------------------------------------

    @contextmanager
    def _changing(self, name: str, *, config: bool = False) -> Iterator[None]:
        """
        Make a change to a zone and publish it, or put the zone back.

        Args:
            name: The zone.
            config: The change is to what Knot is configured with (a zone made or removed, a
                zone signed), not only to a zone file.

        Yields:
            Nothing: the caller changes the store; leaving the block publishes.

        Raises:
            EngineError: Knot did not take the change; the store is as it was.
        """
        with editing():
            snapshot = self.records.snapshot(name)
            try:
                yield
                self._publish(name, config=config)
            except (NoustError, OSError):
                self._put_back(name, snapshot, config=config)
                raise

    def _publish(self, name: str, *, config: bool) -> None:
        """
        Write a zone's file from the store and make Knot read it.

        Args:
            name: The zone.
            config: The configuration changes with it.

        Raises:
            EngineError: Knot did not take it.
        """
        zone = self.records.zone(name)
        if zone is None:
            self.engine.remove_zone_file(name)
            self.servers.reconfigure()
            return
        text = render_zone(zone, self.records.records(name), serial=int(self._clock()))
        self.engine.write_zone(name, text)
        if config:
            self.servers.reconfigure(zones=(name,))
        else:
            self.engine.reload((name,))

    def _put_back(self, name: str, snapshot: ZoneSnapshot | None, *, config: bool) -> None:
        """
        Return the store, the file and Knot to a zone as it was before a change that failed.

        Args:
            name: The zone.
            snapshot: The zone as it was; None when it did not exist.
            config: The change was to the configuration.
        """
        if snapshot is None:
            self.records.delete_zone(name)
        else:
            self.records.restore(snapshot)
        try:
            self._publish(name, config=config)
        except (NoustError, OSError) as exc:
            log.warning(
                "Noust DNS could not put %s back after a change Knot refused: %s", name, exc
            )

    # -- zones ---------------------------------------------------------------------------

    def create_zone(
        self,
        name: str,
        *,
        nameservers: Sequence[str] | None = None,
        contact: str | None = None,
        default_ttl: int = 3600,
        glue: Mapping[str, Sequence[str]] | None = None,
        dnssec: bool = False,
        actor: str | None = None,
    ) -> ZoneDetail:
        """
        Make a zone and serve it.

        Args:
            name: The zone, ``example.com``.
            nameservers: Its name servers; this server and its secondaries by default.
            contact: The address the SOA names; ``hostmaster@<zone>`` by default.
            default_ttl: The TTL of a record that has none.
            glue: Addresses for name servers that are inside the zone, ``{"ns1.example.com":
                ["203.0.113.10"]}``; those of this server and of its secondaries are known.
            dnssec: Sign it.
            actor: Who makes it.

        Returns:
            The zone.

        Raises:
            ValidationError: A value is not usable, the zone exists, or a name server inside the
                zone has no address.
            EngineError: Knot did not take it; nothing was made.
        """
        server = self._primary()
        zone_name = check_zone_name(name)
        if self.records.zone(zone_name) is not None:
            raise ValidationError(
                f"{zone_name} is already a zone of this server",
                details=f"See it: noust dns zone show {zone_name}",
                field="zone",
            )
        known = self._known_addresses(server)
        servers = (
            check_nameservers(list(nameservers))
            if nameservers
            else [n for n in known if n is not None]
        )
        if not servers:
            raise ValidationError(
                "The zone needs name servers",
                details="Name them (--nameserver ns1.example.net), or give this server a host "
                "name: noust dns server setup --hostname ns1.example.net",
                field="nameservers",
            )
        addresses = self._glue(zone_name, servers, known, glue or {})
        zone = AuthDnsZone(
            name=zone_name,
            nameservers=tuple(servers),
            contact=check_contact(contact) if contact else f"hostmaster@{zone_name}",
            default_ttl=check_ttl(default_ttl, field="default_ttl"),
            dnssec=bool(dnssec),
        )
        with self._changing(zone_name, config=True):
            self.records.create_zone(zone, actor=actor)
            for host, found in addresses.items():
                label = "@" if host == zone_name else host[: -len(zone_name) - 1]
                for address in found:
                    kind = "AAAA" if ":" in address else "A"
                    self.records.add_record(zone_name, label, kind, address, actor=actor)
        audit.record(
            "dns.zone",
            target=f"dns-zone:{zone_name}",
            details={"action": "create", "dnssec": zone.dnssec, "actor": actor},
        )
        return self.get(zone_name)

    def _known_addresses(self, server: AuthDnsServer) -> dict[str | None, list[str]]:
        """
        Args:
            server: This server's part.

        Returns:
            The name servers Noust knows (this server's name, its secondaries') with the addresses
            each answers on.
        """
        known: dict[str | None, list[str]] = {}
        if server.hostname:
            known[server.hostname] = list(server.listen)
        for secondary in self.records.secondaries():
            if secondary.hostname:
                # A secondary that answers on several addresses is several rows of one name.
                known.setdefault(secondary.hostname, []).append(secondary.address)
        return known

    @staticmethod
    def _glue(
        zone: str,
        servers: Sequence[str],
        known: Mapping[str | None, Sequence[str]],
        given: Mapping[str, Sequence[str]],
        has_address: Sequence[str] = (),
    ) -> dict[str, list[str]]:
        """
        Work out the addresses of the name servers that are inside the zone.

        Args:
            zone: The zone.
            servers: Its name servers.
            known: The name servers Noust knows the addresses of.
            given: What the caller gave.
            has_address: Name servers the zone already has an address record for.

        Returns:
            Host to addresses for each name server inside the zone.

        Raises:
            ValidationError: One has no address, or an address is not one.
        """
        wanted: dict[str, list[str]] = {}
        wrong = {check_hostname(host, field="glue") for host in given} - set(servers)
        if wrong:
            raise ValidationError(
                f"{', '.join(sorted(wrong))} is not a name server of the zone", field="glue"
            )
        for host in servers:
            if not _inside(host, zone) or host in has_address:
                continue
            found = [
                check_address(a, field_name="glue")
                for a in (given.get(host) or known.get(host) or [])
            ]
            if not found:
                raise ValidationError(
                    f"{host} is inside {zone}, so the zone must say where it is",
                    details=f"Give its address: --glue {host}=ADDRESS. A name server inside the "
                    "zone it serves can only be found through an address the registrar holds "
                    "(glue) and the zone repeats.",
                    field="glue",
                )
            wanted[host] = found
        return wanted

    def update_zone(
        self,
        name: str,
        *,
        nameservers: Sequence[str] | None = None,
        contact: str | None = None,
        default_ttl: int | None = None,
        glue: Mapping[str, Sequence[str]] | None = None,
        actor: str | None = None,
    ) -> ZoneDetail:
        """
        Change a zone's name servers, contact or default TTL.

        Args:
            name: The zone.
            nameservers: Its name servers.
            contact: The address the SOA names.
            default_ttl: The TTL of a record that has none.
            glue: Addresses for name servers that are inside the zone and not yet in it.
            actor: Who changes it.

        Returns:
            The zone.

        Raises:
            ValidationError: A value is not usable.
            EngineError: Knot did not take it; the zone is as it was.
        """
        self._primary()
        zone = self._zone(check_zone_name(name))
        servers = check_nameservers(list(nameservers)) if nameservers else list(zone.nameservers)
        present = {r.name for r in self.records.records(zone.name) if r.type in ("A", "AAAA")}
        has_address = [
            host
            for host in servers
            if _inside(host, zone.name)
            and ("@" if host == zone.name else host[: -len(zone.name) - 1]) in present
        ]
        additions = self._glue(
            zone.name, servers, self._known_addresses(self._server()), glue or {}, has_address
        )
        with self._changing(zone.name):
            self.records.update_zone(
                zone.name,
                nameservers=servers,
                contact=contact,
                default_ttl=default_ttl,
                actor=actor,
            )
            for host, found in additions.items():
                label = "@" if host == zone.name else host[: -len(zone.name) - 1]
                for address in found:
                    kind = "AAAA" if ":" in address else "A"
                    self.records.add_record(zone.name, label, kind, address, actor=actor)
        audit.record(
            "dns.zone",
            target=f"dns-zone:{zone.name}",
            details={"action": "update", "actor": actor},
        )
        return self.get(zone.name)

    def delete_zone(self, name: str, *, actor: str | None = None) -> bool:
        """
        Stop serving a zone and delete it with its records.

        Args:
            name: The zone.
            actor: Who deletes it.

        Returns:
            True when there was one.

        Raises:
            EngineError: Knot did not take it; the zone is as it was.
        """
        self._primary()
        zone_name = check_zone_name(name)
        if self.records.zone(zone_name) is None:
            return False
        with self._changing(zone_name, config=True):
            self.records.delete_zone(zone_name)
        audit.record(
            "dns.zone",
            target=f"dns-zone:{zone_name}",
            details={"action": "delete", "actor": actor},
        )
        return True

    # -- records -------------------------------------------------------------------------

    def _check_together(
        self, zone: str, name: str, kind: str, content: str, *, ignore: int | None = None
    ) -> None:
        """
        Refuse a record that cannot live with the ones already at its name.

        Args:
            zone: The zone.
            name: The record's name.
            kind: Its type.
            content: Its canonical content.
            ignore: A record being changed, which does not count against itself.

        Raises:
            ValidationError: The combination is not allowed in a zone.
        """
        others = [r for r in self.records.records(zone) if r.name == name and r.id != ignore]
        if kind == "CNAME":
            if name == "@":
                raise ValidationError(
                    "The name of the zone itself cannot be a CNAME",
                    details="Use A and AAAA records at the zone's name, or an alias at the "
                    "provider of the zone above.",
                    field="type",
                )
            if others:
                raise ValidationError(
                    f"{name} already has {others[0].type} records, and a CNAME stands alone",
                    details="Remove them, or choose another name.",
                    field="type",
                )
        elif any(r.type == "CNAME" for r in others):
            raise ValidationError(
                f"{name} is a CNAME, and a CNAME stands alone",
                details="Remove the CNAME first, or choose another name.",
                field="type",
            )
        if name == "@" and kind in ("NS", "DS"):
            raise ValidationError(
                "The name servers of the zone itself are the zone's own"
                if kind == "NS"
                else "The DS of a zone is given to its registrar, not kept in the zone",
                details="Change them with: noust dns zone set ZONE --nameserver NAME"
                if kind == "NS"
                else "Use noust dns zone dnssec ZONE to see it.",
                field="type",
            )

    def add_record(
        self,
        zone: str,
        name: str,
        record_type: str,
        content: str,
        *,
        ttl: int | None = None,
        actor: str | None = None,
    ) -> AuthDnsRecord:
        """
        Add a record to a zone and serve it.

        Args:
            zone: The zone.
            name: The record's name relative to the zone.
            record_type: Its type.
            content: What it holds.
            ttl: Seconds to live; the zone's by default.
            actor: Who adds it.

        Returns:
            The record.

        Raises:
            ValidationError: A value is not usable, the record is there, or it cannot be at that
                name with what the name has.
            EngineError: Knot did not take it; the zone is as it was.
        """
        self._primary()
        zone_name = check_zone_name(zone)
        self._zone(zone_name)
        kind = check_record_type(record_type)
        relative = check_record_name(name, zone_name)
        canonical = check_record(kind, content)
        self._check_together(zone_name, relative, kind, canonical)
        created: list[AuthDnsRecord] = []
        with self._changing(zone_name):
            created.append(
                self.records.add_record(zone_name, relative, kind, canonical, ttl=ttl, actor=actor)
            )
        audit.record(
            "dns.zone_record",
            target=f"dns-zone:{zone_name}",
            details={
                "action": "add",
                "name": relative,
                "type": kind,
                "id": created[0].id,
                "actor": actor,
            },
        )
        return created[0]

    def update_record(
        self,
        zone: str,
        record_id: int,
        *,
        content: str | None = None,
        ttl: int | None = None,
        clear_ttl: bool = False,
        actor: str | None = None,
    ) -> AuthDnsRecord:
        """
        Change what a record holds, or its TTL.

        Args:
            zone: The zone it is in.
            record_id: The record's number.
            content: The new content.
            ttl: The new TTL.
            clear_ttl: Go back to the zone's TTL.
            actor: Who changes it.

        Returns:
            The record.

        Raises:
            ValidationError: The record is not in that zone, or a value is not usable.
            EngineError: Knot did not take it; the zone is as it was.
        """
        self._primary()
        zone_name = check_zone_name(zone)
        current = self._record_of(zone_name, record_id)
        with self._changing(zone_name):
            changed = self.records.update_record(
                record_id, content=content, ttl=ttl, clear_ttl=clear_ttl, actor=actor
            )
        audit.record(
            "dns.zone_record",
            target=f"dns-zone:{zone_name}",
            details={
                "action": "update",
                "name": current.name,
                "type": current.type,
                "id": record_id,
                "actor": actor,
            },
        )
        return changed

    def delete_record(self, zone: str, record_id: int, *, actor: str | None = None) -> bool:
        """
        Remove a record.

        Args:
            zone: The zone it is in.
            record_id: The record's number.
            actor: Who removes it.

        Returns:
            True when there was one.

        Raises:
            ValidationError: The record is in another zone.
            EngineError: Knot did not take it; the zone is as it was.
        """
        self._primary()
        zone_name = check_zone_name(zone)
        self._zone(zone_name)
        found = self.records.record(record_id)
        if found is None:
            return False
        current = self._record_of(zone_name, record_id)
        with self._changing(zone_name):
            self.records.delete_record(record_id)
        audit.record(
            "dns.zone_record",
            target=f"dns-zone:{zone_name}",
            details={
                "action": "delete",
                "name": current.name,
                "type": current.type,
                "id": record_id,
                "actor": actor,
            },
        )
        return True

    def _record_of(self, zone: str, record_id: int) -> AuthDnsRecord:
        """
        Args:
            zone: A zone.
            record_id: A record's number.

        Returns:
            The record.

        Raises:
            ValidationError: There is none, or it is in another zone (a number is not a way into
                a zone the caller did not name).
        """
        self._zone(zone)
        found = self.records.record(record_id)
        if found is None or found.zone != zone:
            raise ValidationError(f"There is no record {record_id} in {zone}", field="id")
        return found

    # -- DNSSEC --------------------------------------------------------------------------

    def dnssec(self, name: str) -> DnssecInfo:
        """
        Args:
            name: The zone.

        Returns:
            Whether it is signed and, when it is, the DS records the registrar is given.

        Raises:
            ValidationError: There is no such zone.
        """
        self._primary()
        zone = self._zone(check_zone_name(name))
        if not zone.dnssec:
            return DnssecInfo(zone.name, False)
        return DnssecInfo(zone.name, True, ds=tuple(self.engine.ds_records(zone.name)))

    def set_dnssec(
        self, name: str, enabled: bool, *, ds_removed: bool = False, actor: str | None = None
    ) -> DnssecInfo:
        """
        Sign a zone, or stop signing it.

        Args:
            name: The zone.
            enabled: Whether it is signed.
            ds_removed: The DS record is no longer at the registrar. Without it a signed zone is
                not unsigned: validating resolvers would refuse the whole domain.
            actor: Who changes it.

        Returns:
            The state of the signing; when it was just turned on, with the DS records.

        Raises:
            ValidationError: Turning it off without saying the DS is gone.
            EngineError: Knot did not take it; the zone is as it was.
        """
        self._primary()
        zone = self._zone(check_zone_name(name))
        if enabled == zone.dnssec:
            return self.dnssec(zone.name)
        if not enabled and not ds_removed:
            raise ValidationError(
                f"{zone.name} is signed, and the registrar may still hold its DS record",
                details="While the parent zone has a DS for a zone that is not signed, every "
                "validating resolver refuses the domain. Remove the DS at the registrar, wait "
                "for its TTL, then run this again with --ds-removed.",
                field="ds_removed",
            )
        with self._changing(zone.name, config=True):
            self.records.update_zone(zone.name, dnssec=enabled, actor=actor)
        audit.record(
            "dns.zone",
            target=f"dns-zone:{zone.name}",
            details={"action": "dnssec_on" if enabled else "dnssec_off", "actor": actor},
        )
        if not enabled:
            return DnssecInfo(zone.name, False)
        for _ in range(KEY_ATTEMPTS):
            ds = self.engine.ds_records(zone.name)
            if ds:
                return DnssecInfo(zone.name, True, ds=tuple(ds))
            self._sleep(1.0)
        return DnssecInfo(zone.name, True)

    def confirm_ds(self, name: str, *, actor: str | None = None) -> DnssecInfo:
        """
        Tell the server the registrar now has the DS.

        Args:
            name: The zone.
            actor: Who says so.

        Returns:
            The state of the signing.

        Raises:
            ValidationError: The zone is not signed.
            EngineError: Knot has no key waiting for it.
        """
        self._primary()
        zone = self._zone(check_zone_name(name))
        if not zone.dnssec:
            raise ValidationError(f"{zone.name} is not signed", field="dnssec")
        self.engine.confirm_ds_published(zone.name)
        audit.record(
            "dns.zone",
            target=f"dns-zone:{zone.name}",
            details={"action": "ds_confirmed", "actor": actor},
        )
        return self.dnssec(zone.name)

    def served(self, name: str) -> ZoneStatus | None:
        """
        Args:
            name: The zone.

        Returns:
            What the server says of it now, or None when it does not serve it.
        """
        return self.engine.zone_status().get(name)
