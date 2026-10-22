# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS records Noust keeps for the names an application answers on, and who owns them.

The question this module answers is not "how do I create a record" but "when may I
delete one". A record Noust deletes that it did not make is an outage somebody else
caused, so the rules are strict and every one of them is a line of this file:

- **Ownership is a marker, kept where the provider keeps it.** A record Noust creates
  carries ``managed-by: noust <server>/<id>`` in its comment. Only a record that was
  created here *and* still carries its marker, with the type, name and content it was
  created with, is ever deleted. One that was already there is adopted as it was
  (``owned`` false) and left alone when its last user goes. One an operator edited
  since is reported as changed and left as it is.
- **Replacing is a decision.** A different record of the same name is a conflict, and
  stays one until the operator confirms the exact record they saw (its id and what it
  held): if either changed meanwhile, the confirmation no longer applies. Several
  records at one name are never touched.
- **A record is released when its last user lets go**, not when the application that
  made it does: two applications on one name share one record. A name another
  application still answers on is never released.
- **Only public addresses are published.** A private, loopback, shared or reserved
  address in an ``A`` record points the world at nothing; the operator gets the
  sentence that says so instead.
- **One change at a time per name**, so two requests for the same hostname do not
  create it twice or delete it while the other adopts it, and the provider is asked
  again under the lock, because what it said a moment ago may no longer be true.
- **Orphans are swept**: a record nobody references for an hour is released by
  :meth:`ManagedDns.sweep`, the hourly pass of the monitor.

Every outcome is an audit event.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import AppBusyError, app_lock
from noust.core.config import Config
from noust.core.dns_store import DnsAccount, DnsStore, ManagedRecord
from noust.core.exceptions import (
    NoustError,
    RecordConflictError,
    ValidationError,
)
from noust.core.store import NoustStore, get_store
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.provider import (
    DnsProvider,
    DnsProviderError,
    RemoteRecord,
    Zone,
    is_public_address,
    record_type_for,
    same_value,
    zone_for,
)

#: Seconds another change to the same name is waited for.
LOCK_WAIT = 5.0

#: How long a record nobody uses is kept before the sweep releases it.
SWEEP_GRACE_MINUTES = 60

_HOST = re.compile(r"^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class Release(str, Enum):
    """What releasing a record came to."""

    DELETED = "deleted"
    ALREADY_GONE = "already_gone"
    NOT_OWNED = "not_owned"
    CHANGED_EXTERNALLY = "changed_externally"
    STILL_USED = "still_used"
    FAILED = "failed"
    BUSY = "busy"


class Ensured(str, Enum):
    """What making sure a record exists came to."""

    CREATED = "created"
    ADOPTED = "adopted"
    REPLACED = "replaced"
    EXISTS = "exists"


class DnsRecordConflict(RecordConflictError):
    """A record with another value is already at that name (see :class:`RecordConflictError`)."""

    def __init__(self, hostname: str, record_id: str, current: str, wanted: str) -> None:
        """
        Args:
            hostname: The name.
            record_id: The provider's id of the record in the way.
            current: What it holds.
            wanted: What was asked for.
        """
        super().__init__(
            f"{hostname} already has a record, and it holds {current}",
            (
                f"Noust wants it to hold {wanted}. If that is what you want, confirm the "
                f"replacement of the record {record_id}; only its content changes."
            ),
            hostname=hostname,
            record_id=record_id,
            current=current,
            wanted=wanted,
        )


@dataclass(frozen=True)
class EnsureResult:
    """
    What making sure a record exists did.

    Attributes:
        hostname: The name.
        record_type: ``A``, ``AAAA`` or ``CNAME``.
        content: What it holds.
        outcome: What happened.
        record: The managed record.
        account: The account it is at.
    """

    hostname: str
    record_type: str
    content: str
    outcome: Ensured
    record: ManagedRecord
    account: str


@dataclass(frozen=True)
class ReleaseResult:
    """
    What releasing one record did.

    Attributes:
        hostname: The name.
        record_type: Its type.
        outcome: What happened.
        provider: The account it was at.
    """

    hostname: str
    record_type: str
    outcome: Release
    provider: str


@dataclass(frozen=True)
class DnsPlan:
    """
    What making sure a record exists would do, said before it is done.

    Attributes:
        hostname: The name.
        record_type: The type that would be written.
        content: What it would hold.
        account: The account that holds the zone.
        zone: The zone.
        action: ``create``, ``exists``, ``adopt`` or ``conflict``.
        current: What the record holding the name now says, when it is in the way.
        record_id: Its id at the provider.
        automatic: The account creates records without asking.
    """

    hostname: str
    record_type: str
    content: str
    account: str
    zone: str
    action: str
    current: str | None = None
    record_id: str | None = None
    automatic: bool = False

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The plan for the API and the CLI.
        """
        return {
            "hostname": self.hostname,
            "type": self.record_type,
            "content": self.content,
            "account": self.account,
            "zone": self.zone,
            "action": self.action,
            "current": self.current,
            "record_id": self.record_id,
            "automatic": self.automatic,
        }


@dataclass(frozen=True)
class DnsTarget:
    """
    The account and zone that hold a name.

    Attributes:
        account: The DNS account.
        zone: The zone the name is in.
    """

    account: DnsAccount
    zone: Zone


def checked_hostname(hostname: str) -> str:
    """
    Args:
        hostname: A name to publish.

    Returns:
        The name, lower case, without a trailing dot.

    Raises:
        ValidationError: It is not a host name (or ``*.`` and one).
    """
    name = hostname.strip().lower().rstrip(".")
    if not _HOST.fullmatch(name):
        raise ValidationError(f"{hostname!r} is not a host name", field="hostname")
    return name


def public_addresses() -> list[str]:
    """
    Returns:
        This server's public addresses, IPv4 first. Empty behind NAT or when none is routable.
    """
    from noust.deployers.domains import machine_addresses

    return [address for address in machine_addresses() if is_public_address(address)]


def server_addresses() -> list[str]:
    """
    Returns:
        The addresses a record for this server should carry: the ones the operator
        declared (``dns.public_addresses``, for a server behind NAT), else its public
        interface addresses, IPv4 first.
    """
    declared = Config().get("dns.public_addresses") or []
    chosen = [str(address) for address in declared]
    return chosen or public_addresses()


def server_label() -> str:
    """
    Returns:
        A short name for this server to put in the ownership marker.
    """
    host = re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().split(".")[0].lower()).strip("-")
    return (host or "server")[:24]


def ownership_comment(record: ManagedRecord, label: str | None = None) -> str:
    """
    Args:
        record: A managed record.
        label: This server's short name; read from the machine by default.

    Returns:
        The note the record carries at the provider, which says who made it.
    """
    return f"managed-by: noust {label or server_label()}/{record.uuid}"


class ManagedDns:
    """
    Create, adopt, replace and release the DNS records of the names applications answer on.

    Args:
        accounts: The accounts; built over the process-wide store by default.
        store: The store; the process-wide one by default.
        label: This server's short name for the marker (tests pin it).
        sleep: Waits between attempts at the lock (tests replace it).
        monotonic: The clock the lock's wait is counted on.
    """

    def __init__(
        self,
        accounts: DnsAccounts | None = None,
        store: NoustStore | None = None,
        *,
        label: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self.accounts = accounts or DnsAccounts(store=store)
        self.records = DnsStore(store)
        self._label = label
        self._sleep = sleep
        self._monotonic = monotonic

    # -- locating ------------------------------------------------------------

    def targets(self, hostname: str) -> list[DnsTarget]:
        """
        Find the accounts whose zones hold a name, longest zone first.

        Args:
            hostname: The name.

        Returns:
            One target per native account that has a zone the name is in; those
            with the most specific zone first.

        Raises:
            DnsProviderError: An account's token was refused.
        """
        name = checked_hostname(hostname).removeprefix("*.")
        found: list[DnsTarget] = []
        for account in self.accounts.accounts():
            if not self.accounts.kind(account).native:
                continue
            zone = zone_for(name, self.accounts.provider(account.name).zones())
            if zone is not None:
                found.append(DnsTarget(account, zone))
        return sorted(found, key=lambda target: -len(target.zone.name))

    def _target(self, hostname: str, account: str | None) -> DnsTarget:
        """
        Pick the account and zone that hold a name.

        Args:
            hostname: The name.
            account: A specific account, or None to find one.

        Returns:
            The target.

        Raises:
            ValidationError: No account holds the name, or two hold it equally well.
        """
        candidates = self.targets(hostname)
        if account is not None:
            candidates = [target for target in candidates if target.account.name == account]
        if not candidates:
            raise ValidationError(
                f"No DNS account holds the zone of {hostname}",
                details="Add the account that manages it: noust dns provider add",
                field="hostname",
            )
        if len(candidates) > 1 and len(candidates[0].zone.name) == len(candidates[1].zone.name):
            names = ", ".join(sorted(target.account.name for target in candidates))
            raise ValidationError(
                f"{hostname} is in a zone that more than one account holds ({names})",
                details="Say which: --account NAME",
                field="account",
            )
        return candidates[0]

    # -- making sure ---------------------------------------------------------

    def plan(
        self,
        hostname: str,
        content: str,
        *,
        record_type: str | None = None,
        account: str | None = None,
    ) -> DnsPlan:
        """
        Say what making sure a record exists would do, without doing it.

        Args:
            hostname: The name.
            content: What it should hold: an address, or a name for a CNAME.
            record_type: ``A``, ``AAAA`` or ``CNAME``; found from the content by default.
            account: A specific account.

        Returns:
            The plan.

        Raises:
            ValidationError: The name or the content cannot be published, or no account holds it.
            DnsProviderError: The provider failed.
        """
        name, kind, wanted = self._checked(hostname, content, record_type)
        target = self._target(name, account)
        remote = self.accounts.provider(target.account.name).find_records(target.zone, name, kind)
        action, current, record_id = self._decide(remote, wanted)
        return DnsPlan(
            hostname=name,
            record_type=kind,
            content=wanted,
            account=target.account.name,
            zone=target.zone.name,
            action=action,
            current=current,
            record_id=record_id,
            automatic=target.account.automatic,
        )

    def ensure(
        self,
        hostname: str,
        content: str,
        *,
        app_domain: str,
        record_type: str | None = None,
        proxied: bool = False,
        account: str | None = None,
        replace_record: str | None = None,
        replace_current: str | None = None,
        actor: str | None = None,
    ) -> EnsureResult:
        """
        Make sure a record holds what an application needs, and note that it uses it.

        Args:
            hostname: The name.
            content: What it should hold.
            app_domain: The application that uses it.
            record_type: ``A``, ``AAAA`` or ``CNAME``; found from the content by default.
            proxied: Let the provider proxy it.
            account: A specific account.
            replace_record: The id of the record the operator confirmed replacing.
            replace_current: What that record held when they confirmed.
            actor: Who asked.

        Returns:
            What was done.

        Raises:
            ValidationError: The name or the content cannot be published, or no account holds it.
            DnsRecordConflict: A record with another value is in the way and its replacement
                was not confirmed (or no longer matches what was confirmed).
            DnsProviderError: The provider failed, or several records are at the name.
        """
        require_server_role("DNS records")
        name, kind, wanted = self._checked(hostname, content, record_type)
        target = self._target(name, account)
        provider = self.accounts.provider(target.account.name)
        with self._locked(name):
            remote = provider.find_records(target.zone, name, kind)
            managed = self.records.find_record(target.account.name, name, kind)
            matching = next((item for item in remote if same_value(item.content, wanted)), None)
            if matching is not None:
                record, outcome = self._track(target, matching, managed, kind, name, wanted, actor)
            elif len(remote) > 1:
                raise DnsProviderError(
                    f"Several {kind} records already exist for {name}",
                    details="Noust will not choose between them. Leave one in your DNS provider.",
                )
            elif len(remote) == 1:
                only = remote[0]
                confirmed = (
                    replace_record is not None
                    and replace_record == only.id
                    and (replace_current is None or same_value(replace_current, only.content))
                )
                if not confirmed:
                    raise DnsRecordConflict(name, only.id, only.content, wanted)
                updated = provider.update_record(target.zone, only.id, wanted)
                # A provider that keeps records in sets names the record by its value, so the
                # record it answers with is the one to follow from here on.
                record, _ = self._track(
                    target,
                    RemoteRecord(updated.id or only.id, kind, name, wanted, comment=only.comment),
                    managed,
                    kind,
                    name,
                    wanted,
                    actor,
                )
                outcome = Ensured.REPLACED
            else:
                record = self._create(target, provider, managed, kind, name, wanted, proxied, actor)
                outcome = Ensured.CREATED
            self.records.add_ref(_row(record), app_domain)
        audit.record(
            "dns.record",
            target=f"dns:{target.account.name}",
            details={
                "action": outcome.value,
                "hostname": name,
                "type": kind,
                "zone": target.zone.name,
                "app": app_domain,
            },
        )
        return EnsureResult(name, kind, wanted, outcome, record, target.account.name)

    def _create(
        self,
        target: DnsTarget,
        provider: DnsProvider,
        managed: ManagedRecord | None,
        kind: str,
        name: str,
        content: str,
        proxied: bool,
        actor: str | None,
    ) -> ManagedRecord:
        """
        Create a record with its marker and remember it as Noust's.

        Args:
            target: The account and zone.
            provider: The account's client.
            managed: The row Noust had for this name and type, if any.
            kind: The type.
            name: The name.
            content: What it holds.
            proxied: Let the provider proxy it.
            actor: Who asked.

        Returns:
            The managed record.
        """
        record = ManagedRecord(
            uuid=managed.uuid if managed else "",
            provider=target.account.name,
            zone_id=target.zone.id,
            zone_name=target.zone.name,
            hostname=name,
            record_type=kind,
            content=content,
            owned=True,
            proxied=proxied,
            created_by=actor,
        )
        if not record.uuid:
            record.uuid = uuid.uuid4().hex[:12]
        created = provider.create_record(
            target.zone,
            name,
            kind,
            content,
            proxied=proxied,
            comment=ownership_comment(record, self._label),
        )
        record.remote_id = created.id
        return self.records.save_record(record)

    def _track(
        self,
        target: DnsTarget,
        remote: RemoteRecord,
        managed: ManagedRecord | None,
        kind: str,
        name: str,
        content: str,
        actor: str | None,
    ) -> tuple[ManagedRecord, Ensured]:
        """
        Remember a record that already exists at the provider, never as Noust's unless it says so.

        A row is owned only while the provider's comment still carries its marker:
        a record whose marker an operator removed is theirs again.

        Args:
            target: The account and zone.
            remote: The record at the provider.
            managed: The row Noust had, if any.
            kind: The type.
            name: The name.
            content: What it holds.
            actor: Who asked.

        Returns:
            The managed record and whether it was adopted or already managed.
        """
        record = managed or ManagedRecord(
            uuid="",
            provider=target.account.name,
            zone_id=target.zone.id,
            zone_name=target.zone.name,
            hostname=name,
            record_type=kind,
            content=content,
            owned=False,
            created_by=actor,
        )
        marker_ok = managed is not None and (
            not self._keeps_comments(target.account.name)
            or remote.comment == ownership_comment(managed, self._label)
        )
        record.owned = bool(managed and managed.owned and marker_ok)
        record.remote_id = remote.id
        record.content = content
        record.zone_id, record.zone_name = target.zone.id, target.zone.name
        saved = self.records.save_record(record)
        return saved, Ensured.EXISTS if managed is not None else Ensured.ADOPTED

    def _keeps_comments(self, account: str) -> bool:
        """
        Args:
            account: A DNS account's name.

        Returns:
            Whether its provider keeps a note with each record, which is where the ownership
            marker is read back from. Where it does not, Noust's own record of what it made
            (with the record still being exactly what it created) is what stands.
        """
        return self.accounts.kind(self.accounts.get(account)).comments

    @staticmethod
    def _decide(remote: Sequence[RemoteRecord], wanted: str) -> tuple[str, str | None, str | None]:
        """
        Decide what a record needs, from what the provider has.

        Args:
            remote: The records at the name and type.
            wanted: The content.

        Returns:
            ``(action, current, id)``: ``exists`` when one holds it, ``conflict`` when
            one holds something else (``current`` and its id), ``create`` otherwise.
        """
        for item in remote:
            if same_value(item.content, wanted):
                return "exists", item.content, item.id
        if len(remote) == 1:
            return "conflict", remote[0].content, remote[0].id
        if remote:
            return "conflict", None, None
        return "create", None, None

    @staticmethod
    def _checked(hostname: str, content: str, record_type: str | None) -> tuple[str, str, str]:
        """
        Check what is about to be published.

        Args:
            hostname: The name.
            content: What it should hold.
            record_type: The type, or None to find it from the content.

        Returns:
            The name, the type and the content, normalised.

        Raises:
            ValidationError: Any of them cannot be published.
        """
        name = checked_hostname(hostname)
        text = content.strip()
        kind = (record_type or "").upper() or record_type_for(text)
        if kind not in ("A", "AAAA", "CNAME"):
            raise ValidationError("A record is A, AAAA or CNAME", field="type")
        if kind in ("A", "AAAA"):
            try:
                address = ipaddress.ip_address(text)
            except ValueError:
                raise ValidationError(
                    f"{content!r} is not an IP address", field="content"
                ) from None
            if (kind == "A") != (address.version == 4):
                raise ValidationError(f"{text} is not a valid {kind} record", field="content")
            if not is_public_address(text):
                raise ValidationError(
                    f"{text} is not a public address, so a record for it would point nowhere",
                    details="Private, loopback, shared (100.64.0.0/10), link-local and reserved "
                    "addresses are not published. Give the server's public address, or add the "
                    "record by hand.",
                    field="content",
                )
        else:
            text = checked_hostname(text)
        return name, kind, text

    def ensure_for_domain(
        self, app_domain: str, hostname: str, *, actor: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Point a name at this server, in the DNS account that holds its zone.

        What adding a domain does about DNS. An account that creates records without
        asking gets them created; one that does not is only asked what it would do, so
        the operator can say yes. Nothing here fails the caller: a name that is added
        is added whatever DNS says, and the reasons come back as entries.

        Args:
            app_domain: The application the name belongs to.
            hostname: The name, a wildcard included.
            actor: Who asked.

        Returns:
            One entry per address of this server: ``hostname``, ``type``, ``content``,
            ``account`` and an ``outcome`` (``created``, ``adopted``, ``replaced``,
            ``exists``, ``pending``, ``conflict`` or ``failed``) with the words that
            explain it. Empty when no account holds the zone.
        """
        try:
            candidates = self.targets(hostname)
        except (DnsProviderError, ValidationError, NoustError) as exc:
            return [{"hostname": hostname, "outcome": "failed", "detail": _words(exc)}]
        if not candidates:
            return []
        target = candidates[0]
        addresses = server_addresses()
        if not addresses:
            return [
                {
                    "hostname": hostname,
                    "account": target.account.name,
                    "outcome": "failed",
                    "detail": "This server has no public address to point the name at. Declare "
                    "it: noust config set dns.public_addresses ADDRESS",
                }
            ]
        entries: list[dict[str, Any]] = []
        for address in addresses:
            entry: dict[str, Any] = {
                "hostname": hostname,
                "type": record_type_for(address),
                "content": address,
                "account": target.account.name,
            }
            try:
                if not target.account.automatic:
                    plan = self.plan(hostname, address, account=target.account.name)
                    entry.update(
                        outcome="pending",
                        action=plan.action,
                        current=plan.current,
                        record_id=plan.record_id,
                    )
                else:
                    result = self.ensure(
                        hostname,
                        address,
                        app_domain=app_domain,
                        account=target.account.name,
                        actor=actor,
                    )
                    entry["outcome"] = result.outcome.value
            except DnsRecordConflict as exc:
                entry.update(
                    outcome="conflict",
                    current=exc.current,
                    record_id=exc.record_id,
                    detail=exc.message,
                )
            except (DnsProviderError, ValidationError, NoustError) as exc:
                entry.update(outcome="failed", detail=_words(exc))
            entries.append(entry)
        return entries

    # -- releasing -----------------------------------------------------------

    def release(
        self, app_domain: str, *, hostname: str | None = None, delete: bool = True
    ) -> list[ReleaseResult]:
        """
        Say an application no longer uses its records, and delete those nobody else does.

        Args:
            app_domain: The application.
            hostname: Only the records of this name; every one of the application's by default.
            delete: Delete what nobody uses. False only forgets the application's use.

        Returns:
            What happened to each record the application used.
        """
        results: list[ReleaseResult] = []
        for record in self.records.records_of(app_domain):
            if hostname is not None and record.hostname != hostname.strip().lower():
                continue
            left = self.records.remove_ref(_row(record), app_domain)
            if left:
                outcome = Release.STILL_USED
            elif not delete:
                outcome = Release.STILL_USED
            else:
                outcome = self._delete(record)
            results.append(
                ReleaseResult(record.hostname, record.record_type, outcome, record.provider)
            )
        return results

    def sweep(self, *, grace_minutes: int = SWEEP_GRACE_MINUTES) -> list[ReleaseResult]:
        """
        Release the records nobody has used for a while.

        Args:
            grace_minutes: How long a record may stay unused before it goes.

        Returns:
            What happened to each record swept.
        """
        store = self._store or get_store()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
        results: list[ReleaseResult] = []
        for record in self.records.list_records():
            row = _row(record)
            for app in self.records.refs(row):
                if store.get_app(app) is None:
                    self.records.remove_ref(row, app)
            fresh = self.records.get_record(row)
            if fresh is None or self.records.refs(row):
                continue
            if not fresh.unreferenced_since or _parse(fresh.unreferenced_since) > cutoff:
                continue
            results.append(
                ReleaseResult(
                    fresh.hostname, fresh.record_type, self._delete(fresh), fresh.provider
                )
            )
        return results

    def _delete(self, record: ManagedRecord) -> Release:
        """
        Delete a record at the provider if, and only if, it is still exactly what Noust made.

        Args:
            record: The managed record nobody uses.

        Returns:
            What came of it. The row is forgotten in every case but a failure or a busy name.
        """
        row = _row(record)
        if not _points_at_tunnel(record) and self._used_by_an_application(record.hostname):
            return Release.STILL_USED
        if not record.owned:
            self.records.delete_record(row)
            return self._audited(record, Release.NOT_OWNED)
        try:
            provider = self.accounts.provider(record.provider)
            zone = Zone(record.zone_id, record.zone_name)
            with self._locked(record.hostname):
                remote = provider.get_record(zone, record.remote_id) if record.remote_id else None
                if remote is None:
                    self.records.delete_record(row)
                    return self._audited(record, Release.ALREADY_GONE)
                unchanged = (
                    remote.type == record.record_type
                    and remote.name == record.hostname
                    and same_value(remote.content, record.content)
                    and (
                        not self._keeps_comments(record.provider)
                        or remote.comment == ownership_comment(record, self._label)
                    )
                )
                if not unchanged:
                    self.records.delete_record(row)
                    return self._audited(record, Release.CHANGED_EXTERNALLY)
                gone = provider.delete_record(zone, remote.id)
                self.records.delete_record(row)
                return self._audited(record, Release.DELETED if gone else Release.ALREADY_GONE)
        except AppBusyError:
            return Release.BUSY
        except (DnsProviderError, NoustError):
            return Release.FAILED

    def _audited(self, record: ManagedRecord, outcome: Release) -> Release:
        """
        Record what became of a record in the audit trail.

        Args:
            record: The record.
            outcome: What came of it.

        Returns:
            The outcome, for the caller to return.
        """
        audit.record(
            "dns.record",
            target=f"dns:{record.provider}",
            details={
                "action": f"release:{outcome.value}",
                "hostname": record.hostname,
                "type": record.record_type,
                "zone": record.zone_name,
            },
        )
        return outcome

    def _used_by_an_application(self, hostname: str) -> bool:
        """
        Args:
            hostname: A name.

        Returns:
            Whether an application still answers on it.
        """
        store = self._store or get_store()
        return store.domain_owner(hostname.removeprefix("*.")) is not None

    @contextmanager
    def _locked(self, hostname: str) -> Iterator[None]:
        """
        Hold the lock of one name, waiting a few seconds for whoever has it.

        Args:
            hostname: The name.

        Yields:
            Nothing; the lock is held until the block exits.

        Raises:
            AppBusyError: Another change to the name did not finish in time.
        """
        deadline = self._monotonic() + LOCK_WAIT
        key = f"dns-record.{hostname.removeprefix('*.')}"
        stack = ExitStack()
        while True:
            try:
                stack.enter_context(app_lock(key, "DNS change"))
                break
            except AppBusyError:
                if self._monotonic() >= deadline:
                    raise
                self._sleep(0.1)
        with stack:
            yield


def _points_at_tunnel(record: ManagedRecord) -> bool:
    """
    Args:
        record: A managed record.

    Returns:
        Whether it is a CNAME to a Cloudflare Tunnel. That is not an address of this server,
        so an application that still answers on the name does not need it: once nothing
        routes the name through the tunnel it points at nothing, and is Noust's to delete.
    """
    return record.record_type == "CNAME" and record.content.lower().endswith(".cfargotunnel.com")


def _words(error: NoustError) -> str:
    """
    Args:
        error: Something a provider or a check refused.

    Returns:
        What to tell the operator: the message, and the provider's own words when it had some.
    """
    return " ".join(part for part in (error.message, error.output or error.details) if part)


def _row(record: ManagedRecord) -> int:
    """
    Args:
        record: A record read from or saved to the store.

    Returns:
        Its row id.

    Raises:
        NoustError: It was never saved, which is a bug in the caller.
    """
    if record.id is None:
        raise NoustError(f"The record of {record.hostname} has no row")
    return record.id


def _parse(moment: str) -> datetime:
    """
    Args:
        moment: An ISO 8601 time.

    Returns:
        It as an aware datetime (UTC when it carried no offset).
    """
    parsed = datetime.fromisoformat(moment)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
