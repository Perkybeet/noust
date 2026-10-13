# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
DNS accounts and the records Noust manages in them, over the API.

A thin translation of :mod:`noust.managers.dns`, which ``noust dns`` reads too. Two
routers, because two things belong to two owners:

- :data:`router` (``/dns``) is the server's: the accounts and the credentials behind
  them, the managed records and their sweep. A team member does not see them.
- :data:`apps_router` (``/apps/{domain}/dns``) is an application's: what its names
  would point at, and the records it uses.

No credential is ever in a response: an account is its name, kind, whether it creates
records on its own and when its token last worked. Adding and removing one asks for
sudo mode, because a credential that edits DNS goes in or out.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.dns_store import DnsAccount, ManagedRecord
from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers.domains import list_domains
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.catalog import KINDS
from noust.managers.dns.records import ManagedDns, server_addresses
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)
apps_router = APIRouter(route_class=NoustErrorRoute)


class CredentialFieldModel(BaseModel):
    """
    One thing a kind of account needs.

    Attributes:
        name: The key in ``credentials``.
        label: What to ask it as.
        secret: It is a credential.
        required: The account cannot work without it.
        default: What it is when not given.
    """

    name: str
    label: str
    secret: bool
    required: bool
    default: str = ""


class ProviderKindModel(BaseModel):
    """
    A kind of DNS provider.

    Attributes:
        kind: Its name.
        label: How to call it.
        native: Noust speaks its API: managed records, the challenge hook, tunnels.
        fields: What an account needs.
        help: Where the credentials come from.
        package: The package a plugin kind needs installed.
    """

    kind: str
    label: str
    native: bool
    fields: list[CredentialFieldModel]
    help: str = ""
    package: str = ""


class DnsAccountModel(BaseModel):
    """
    A DNS account. Never its credential.

    Attributes:
        name: What it is called.
        kind: The provider.
        label: How the provider is called.
        native: Noust manages records and tunnels in it.
        automatic: Adding a domain creates its records without asking.
        verified_at: When its credential last worked, ISO 8601 UTC.
        last_error: Why it last did not, in the provider's own words.
        propagation_seconds: How long a challenge record is waited for.
        created_at: When it was added.
    """

    name: str
    kind: str
    label: str
    native: bool
    automatic: bool
    verified_at: str | None = None
    last_error: str | None = None
    propagation_seconds: int | None = None
    created_at: str = ""


class DnsAccountList(BaseModel):
    """
    Every account, and the kinds that can be added.

    Attributes:
        accounts: The accounts.
        kinds: The providers Noust knows.
    """

    accounts: list[DnsAccountModel]
    kinds: list[ProviderKindModel]


class AddDnsAccountRequest(BaseModel):
    """
    A DNS account to add.

    Attributes:
        name: What to call it: lower case letters, digits and dashes.
        kind: The provider.
        credentials: The fields the kind asks for; proven before they are kept.
        automatic: Create the records of a domain when it is added, without asking.
    """

    name: str
    kind: str
    credentials: dict[str, str] = Field(default_factory=dict)
    automatic: bool = False


class UpdateDnsAccountRequest(BaseModel):
    """
    Changes to a DNS account. A field left out is not changed.

    Attributes:
        automatic: Create records without asking.
        propagation_seconds: How long to wait for a challenge record (0 to 600).
    """

    automatic: bool | None = None
    propagation_seconds: int | None = Field(default=None, ge=0, le=600)


class ZoneModel(BaseModel):
    """
    A zone an account manages.

    Attributes:
        id: The provider's id.
        name: The zone.
    """

    id: str
    name: str


class ManagedRecordModel(BaseModel):
    """
    A DNS record Noust manages.

    Attributes:
        id: Its row.
        provider: The account.
        zone: The zone.
        hostname: Its name.
        type: ``A``, ``AAAA`` or ``CNAME``.
        content: What it holds.
        owned: Noust created it (and so deletes it); false for one adopted as it was.
        proxied: The provider proxies it.
        applications: The applications that use it.
        unreferenced_since: When the last application let go of it.
    """

    id: int
    provider: str
    zone: str
    hostname: str
    type: str
    content: str
    owned: bool
    proxied: bool
    applications: list[str] = Field(default_factory=list)
    unreferenced_since: str | None = None


class ManagedRecordList(BaseModel):
    """
    The managed records.

    Attributes:
        records: Every one.
    """

    records: list[ManagedRecordModel]


class SweepResult(BaseModel):
    """
    What the sweep released.

    Attributes:
        released: One entry per record the sweep handled.
    """

    released: list[dict[str, str]]


class DnsPlanModel(BaseModel):
    """
    What making sure a record exists would do.

    Attributes:
        hostname: The name.
        type: The record type.
        content: What it would hold.
        account: The account that holds the zone.
        zone: The zone.
        action: ``create``, ``exists``, ``adopt`` or ``conflict``.
        current: What the record in the way holds, for a conflict.
        record_id: Its id at the provider.
        automatic: The account creates records without asking.
    """

    hostname: str
    type: str
    content: str
    account: str
    zone: str
    action: str
    current: str | None = None
    record_id: str | None = None
    automatic: bool = False


class AppDnsModel(BaseModel):
    """
    The DNS of an application's names.

    Attributes:
        app: The application's primary domain.
        addresses: The addresses of this server a record would carry.
        plans: One entry per name that a DNS account holds the zone of and per address.
        unmanaged: Names no account holds the zone of.
        records: The managed records the application uses.
    """

    app: str
    addresses: list[str]
    plans: list[DnsPlanModel]
    unmanaged: list[str]
    records: list[ManagedRecordModel]


class EnsureRecordRequest(BaseModel):
    """
    Make a name point at this server (or at another target).

    Attributes:
        hostname: The name.
        content: What it should hold; this server's public address by default.
        type: ``A``, ``AAAA`` or ``CNAME``; found from the content by default.
        account: The account, when more than one holds the zone.
        replace_record: The id of the record in the way the operator confirmed replacing.
        replace_current: What it held when they confirmed.
    """

    hostname: str
    content: str | None = None
    type: str | None = None
    account: str | None = None
    replace_record: str | None = None
    replace_current: str | None = None


class EnsureRecordResponse(BaseModel):
    """
    What making sure a record exists did.

    Attributes:
        hostname: The name.
        type: The record type.
        content: What it holds.
        outcome: ``created``, ``adopted``, ``replaced`` or ``exists``.
        account: The account.
    """

    hostname: str
    type: str
    content: str
    outcome: str
    account: str


def _account(account: DnsAccount, accounts: DnsAccounts) -> DnsAccountModel:
    """
    Translate an account to its response model.

    Args:
        account: The stored account.
        accounts: The manager, for the kind.

    Returns:
        The response model.
    """
    spec = accounts.kind(account)
    seconds = account.settings.get("propagation_seconds")
    return DnsAccountModel(
        name=account.name,
        kind=account.kind,
        label=spec.label,
        native=spec.native,
        automatic=account.automatic,
        verified_at=account.verified_at,
        last_error=account.last_error,
        propagation_seconds=int(seconds) if isinstance(seconds, int) else None,
        created_at=account.created_at,
    )


def _record(record: ManagedRecord, apps: list[str]) -> ManagedRecordModel:
    """
    Translate a managed record to its response model.

    Args:
        record: The stored record.
        apps: The applications that use it.

    Returns:
        The response model.
    """
    return ManagedRecordModel(
        id=record.id or 0,
        provider=record.provider,
        zone=record.zone_name,
        hostname=record.hostname,
        type=record.record_type,
        content=record.content,
        owned=record.owned,
        proxied=record.proxied,
        applications=apps,
        unreferenced_since=record.unreferenced_since,
    )


def _require_app(domain: str) -> str:
    """
    Validate an application's domain and require that it is deployed.

    Args:
        domain: The domain from the path.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/providers", response_model=DnsAccountList)
def list_providers(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> DnsAccountList:
    """
    List the DNS accounts, and the kinds that can be added.

    Args:
        session: Authenticated session, injected.

    Returns:
        The accounts (never a credential) and the catalog.
    """
    accounts = DnsAccounts()
    return DnsAccountList(
        accounts=[_account(account, accounts) for account in accounts.accounts()],
        kinds=[
            ProviderKindModel(
                kind=spec.kind,
                label=spec.label,
                native=spec.native,
                fields=[
                    CredentialFieldModel(
                        name=f.name,
                        label=f.label,
                        secret=f.secret,
                        required=f.required,
                        default=f.default,
                    )
                    for f in spec.fields
                ],
                help=spec.help,
                package=spec.package,
            )
            for spec in KINDS.values()
        ],
    )


@router.post("/providers", response_model=DnsAccountModel, status_code=201)
def add_provider(
    data: AddDnsAccountRequest,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> DnsAccountModel:
    """
    Add a DNS account, proving its credential first.

    Needs sudo mode: a credential that edits DNS goes in.

    Args:
        data: The account.
        session: Authenticated and elevated session, injected.

    Returns:
        The account.

    Raises:
        DnsProviderError: The provider refused the token (502, with its words).
    """
    accounts = DnsAccounts()
    account = accounts.add(
        data.name,
        data.kind,
        data.credentials,
        automatic=data.automatic,
        created_by=actor_label(session),
    )
    return _account(account, accounts)


@router.patch("/providers/{name}", response_model=DnsAccountModel)
def update_provider(
    name: str,
    data: UpdateDnsAccountRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> DnsAccountModel:
    """
    Change whether an account creates records on its own, or how long a challenge waits.

    Args:
        name: The account.
        data: The changes.
        session: Authenticated session, injected.

    Returns:
        The account.
    """
    accounts = DnsAccounts()
    if data.automatic is not None:
        accounts.set_automatic(name, data.automatic)
    if data.propagation_seconds is not None:
        accounts.set_propagation(name, data.propagation_seconds)
    return _account(accounts.get(name), accounts)


@router.post("/providers/{name}/verify", response_model=list[ZoneModel])
def verify_provider(
    name: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> list[ZoneModel]:
    """
    Try an account's credential again and list the zones it manages.

    Args:
        name: The account.
        session: Authenticated session, injected.

    Returns:
        The zones (empty for an account that Noust cannot list, a plugin's).

    Raises:
        DnsProviderError: The provider refused the token; the account keeps its words.
    """
    return [ZoneModel(id=zone.id, name=zone.name) for zone in DnsAccounts().verify(name)]


@router.delete("/providers/{name}", status_code=204)
def remove_provider(
    name: str, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> None:
    """
    Remove an account and its credential. Records stay at the provider.

    Args:
        name: The account.
        session: Authenticated and elevated session, injected.

    Raises:
        ValidationError: A certificate is ordered through it.
    """
    DnsAccounts().remove(name)


@router.get("/records", response_model=ManagedRecordList)
def list_records(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> ManagedRecordList:
    """
    List the DNS records Noust manages, and who uses them.

    Args:
        session: Authenticated session, injected.

    Returns:
        The records.
    """
    dns = ManagedDns()
    return ManagedRecordList(
        records=[
            _record(record, dns.records.refs(record.id or 0))
            for record in dns.records.list_records()
        ]
    )


@router.post("/sweep", response_model=SweepResult)
def sweep_records(session: Annotated[dict[str, Any], Depends(get_current_session)]) -> SweepResult:
    """
    Release the records nobody has used for an hour, as the monitor does.

    Args:
        session: Authenticated session, injected.

    Returns:
        What the sweep handled.
    """
    results = ManagedDns().sweep()
    return SweepResult(
        released=[
            {
                "hostname": r.hostname,
                "type": r.record_type,
                "outcome": r.outcome.value,
                "account": r.provider,
            }
            for r in results
        ]
    )


@apps_router.get("/{domain}/dns", response_model=AppDnsModel)
def get_app_dns(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> AppDnsModel:
    """
    Show what each of an application's names would point at, and the records it uses.

    Reads the providers (zones and records) and changes nothing.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        The plans, the names no account holds, and the managed records.
    """
    validated = _require_app(domain)
    dns = ManagedDns()
    addresses = server_addresses()
    plans: list[DnsPlanModel] = []
    unmanaged: list[str] = []
    for record in list_domains(validated):
        if not dns.targets(record.domain):
            unmanaged.append(record.domain)
            continue
        for address in addresses:
            plan = dns.plan(record.domain, address)
            plans.append(DnsPlanModel(**plan.to_dict()))
    return AppDnsModel(
        app=validated,
        addresses=addresses,
        plans=plans,
        unmanaged=unmanaged,
        records=[
            _record(r, dns.records.refs(r.id or 0)) for r in dns.records.records_of(validated)
        ],
    )


@apps_router.post("/{domain}/dns", response_model=EnsureRecordResponse)
def ensure_app_dns(
    domain: str,
    data: EnsureRecordRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> EnsureRecordResponse:
    """
    Make a name of the application point at this server, creating its record.

    A record in the way is a conflict (409) until the operator confirms replacing it,
    naming the record and what it held.

    Args:
        domain: The application's primary domain.
        data: The name, and optionally the target and the confirmation.
        session: Authenticated session, injected.

    Returns:
        What was done.

    Raises:
        ValidationError: The target cannot be published, or this server has no public address.
    """
    validated = _require_app(domain)
    content = data.content
    if not content:
        addresses = server_addresses()
        if not addresses:
            raise ValidationError(
                "This server has no public address to point the name at",
                details="Declare it: noust config set dns.public_addresses ADDRESS",
                field="content",
            )
        content = addresses[0]
    result = ManagedDns().ensure(
        data.hostname,
        content,
        app_domain=validated,
        record_type=data.type,
        account=data.account,
        replace_record=data.replace_record,
        replace_current=data.replace_current,
        actor=actor_label(session),
    )
    return EnsureRecordResponse(
        hostname=result.hostname,
        type=result.record_type,
        content=result.content,
        outcome=result.outcome.value,
        account=result.account,
    )
