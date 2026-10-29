# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The configuration of Knot for Noust DNS, written whole from the server's part and its zones.

Noust writes the whole file and marks it (the first line), so what Knot runs is what the store
says and an edit made by hand is overwritten at the next change; a file Noust did not write is
never overwritten (:mod:`noust.managers.authdns.engine` refuses a server that already runs Knot
with zones of its own).

What the file says, and why:

- **Listen only on the addresses given.** Binding to every address would fight the stub resolver
  that systemd-resolved runs on 127.0.0.53:53, and would answer on addresses the operator never
  meant to publish.
- **A primary lists its zones in a catalog zone it generates** and each zone is a member of it
  (RFC 9432). A secondary interprets that catalog and copies every zone it lists from the
  primary, so a zone added or removed on the primary appears or goes on every secondary with no
  other instruction.
- **Transfers need the key.** ``automatic-acl`` derives the transfer and notify rules from the
  remotes, and each remote names the transfer key, so an address alone is never enough to copy a
  zone.
- **The engine owns the serial** (``difference-no-serial`` and ``unixtime``), keeps the zone in
  its journal across restarts and never writes the zone file back (``zonefile-sync: -1``): the
  file is Noust's output only.
- **One ECDSA P-256 key signs a signed zone** (``single-type-signing``: a single key, a single DS
  to give the registrar) and the CDS and CDNSKEY are always published, which is how a registrar
  that supports RFC 8078 can keep the DS in step by itself.
- **UDP answers are rate limited** (``mod-rrl``), with the servers it talks to exempt, so the
  server is a poor tool to flood a third party with. Knot never recurses: a query for a name it
  does not serve is REFUSED.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from noust.core import paths
from noust.core.authdns_store import PRIMARY, SECONDARY
from noust.core.exceptions import ValidationError
from noust.validators.dns_zone import check_zone_name

__all__ = ["CONFIG_MARKER", "KnotSettings", "ZoneEntry", "render_config", "storage_path"]

#: What the first line says; the engine overwrites a file only when it finds it.
CONFIG_MARKER = f"{paths.UNIT_MARKER} for Noust DNS: changes made here are overwritten"

#: UDP responses per second a single address may get, and how many of the ones over it are
#: answered truncated (``2`` is every other one) instead of dropped.
RATE_LIMIT = 100
SLIP = 2

_BASE64 = re.compile(r"[A-Za-z0-9+/]{16,512}={0,2}")


@dataclass(frozen=True)
class ZoneEntry:
    """
    A zone of a primary, as the configuration needs it.

    Attributes:
        name: The zone.
        signed: The engine signs it.
    """

    name: str
    signed: bool = False


@dataclass(frozen=True)
class KnotSettings:
    """
    Everything the configuration is written from.

    Attributes:
        role: ``primary`` or ``secondary``.
        listen: The addresses it answers on.
        secret: The transfer key, base64.
        zones: A primary's zones.
        secondaries: A primary's secondaries, by address.
        primary_address: A secondary's primary.
    """

    role: str
    listen: tuple[str, ...]
    secret: str
    zones: tuple[ZoneEntry, ...] = ()
    secondaries: tuple[str, ...] = ()
    primary_address: str | None = None


def storage_path() -> str:
    """
    Returns:
        Where the engine keeps the zone files, as the configuration spells it.
    """
    return str(paths.AUTHDNS_ZONES)


def _address(value: str) -> str:
    """
    Args:
        value: An address.

    Returns:
        It, canonical.

    Raises:
        ValueError: It is not an IP address.
    """
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ValueError(f"not an IP address: {value[:60]!r}") from None


def _at_53(address: str) -> str:
    return f"{_address(address)}@{paths.AUTHDNS_PORT}"


def _zone_name(name: str) -> str:
    try:
        return check_zone_name(name)
    except ValidationError as exc:
        raise ValueError(exc.message) from None


def render_config(settings: KnotSettings) -> str:
    """
    Write the configuration.

    Args:
        settings: What it says.

    Returns:
        The text of ``knot.conf``, marked.

    Raises:
        ValueError: A value is not what the file can carry (an address that is not one, a key
            that is not base64, a role that does not exist): the caller judged them before, and
            this refuses to write anything it was not given clean.
    """
    if settings.role not in (PRIMARY, SECONDARY):
        raise ValueError(f"not a role: {settings.role!r}")
    if not settings.listen:
        raise ValueError("a name server answers on at least one address")
    if not _BASE64.fullmatch(settings.secret):
        raise ValueError("the transfer key is base64")
    if settings.role == SECONDARY and not settings.primary_address:
        raise ValueError("a secondary copies from a primary")
    listen = ", ".join(_at_53(a) for a in settings.listen)
    peers = (
        [_address(a) for a in settings.secondaries]
        if settings.role == PRIMARY
        else [_address(settings.primary_address or "")]
    )
    out = [
        f"# {CONFIG_MARKER}",
        "server:",
        '    rundir: "/run/knot"',
        "    user: knot:knot",
        f"    listen: [ {listen} ]",
        '    identity: ""',
        '    version: ""',
        "    automatic-acl: on",
        "",
        "log:",
        "  - target: syslog",
        "    any: info",
        "",
        "database:",
        f'    storage: "{paths.AUTHDNS_STORAGE}"',
        "",
        "key:",
        f"  - id: {paths.AUTHDNS_TRANSFER_KEY}",
        "    algorithm: hmac-sha256",
        f'    secret: "{settings.secret}"',
        "",
    ]
    remotes: list[str] = []
    if settings.role == PRIMARY:
        remotes = [f"noust-secondary-{n}" for n in range(1, len(peers) + 1)]
        if remotes:
            out.append("remote:")
            for remote, address in zip(remotes, peers, strict=True):
                out += [
                    f"  - id: {remote}",
                    f"    address: {_at_53(address)}",
                    f"    key: {paths.AUTHDNS_TRANSFER_KEY}",
                ]
            out.append("")
    else:
        out += [
            "remote:",
            "  - id: noust-primary",
            f"    address: {_at_53(peers[0])}",
            f"    key: {paths.AUTHDNS_TRANSFER_KEY}",
            "",
        ]
    out += ["mod-rrl:", "  - id: noust-rrl", f"    rate-limit: {RATE_LIMIT}", f"    slip: {SLIP}"]
    if peers:
        out.append(f"    whitelist: [ {', '.join(peers)} ]")
    out.append("")
    if settings.role == PRIMARY:
        out += [
            "policy:",
            "  - id: noust-signing",
            "    algorithm: ecdsap256sha256",
            "    single-type-signing: on",
            "    zsk-lifetime: 0",
            "    cds-cdnskey-publish: always",
            "",
        ]
    storage = f'    storage: "{storage_path()}"'
    out += [
        "template:",
        "  - id: default",
        storage,
        '    file: "%s.zone"',
        "    semantic-checks: on",
    ]
    if settings.role == PRIMARY:
        out += [
            "    zonefile-load: difference-no-serial",
            "    zonefile-sync: -1",
            "    journal-content: all",
            "    serial-policy: unixtime",
            "    dnssec-policy: noust-signing",
        ]
        if remotes:
            out.append(f"    notify: [ {', '.join(remotes)} ]")
    out.append("    global-module: mod-rrl/noust-rrl")
    if settings.role == SECONDARY:
        out += [
            "  - id: noust-member",
            storage,
            '    file: "%s.zone"',
            "    master: noust-primary",
            "    semantic-checks: on",
        ]
    out += ["", "zone:"]
    catalog = f"{paths.AUTHDNS_CATALOG}."
    if settings.role == PRIMARY:
        out += [f"  - domain: {catalog}", "    catalog-role: generate", "    zonefile-load: none"]
        for zone in sorted(settings.zones, key=lambda z: z.name):
            out += [f"  - domain: {_zone_name(zone.name)}."]
            if zone.signed:
                out.append("    dnssec-signing: on")
            out += ["    catalog-role: member", f"    catalog-zone: {catalog}"]
    else:
        out += [
            f"  - domain: {catalog}",
            "    master: noust-primary",
            "    catalog-role: interpret",
            "    catalog-template: noust-member",
        ]
    return "\n".join(out) + "\n"
