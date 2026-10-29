# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust DNS over the API: ``/api/dns/noust``.

A thin translation of :mod:`noust.managers.authdns`, which ``noust dns server`` and ``noust dns
zone`` call too (rule 3). The server's part is the machine's (it installs a program, opens port 53
and answers on addresses), so setting it up, removing it and reading its transfer key are host
access and ask for sudo mode; the zones and their records are managed with the server. Nothing
here edits a zone file or runs Knot.

Installing and setting up take minutes (a package), so they are jobs whose log streams the
package manager. The transfer key is in the body of the one call that gives a secondary its key,
and nowhere afterwards: the job registers it as a secret so no line of its log can quote it.

A central reaches all of this on each of its servers through the node proxy, and sets a whole
cluster up with the fleet action ``dns_cluster`` (:mod:`noust.web.fleet_dns_actions`).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

from noust.managers.authdns.delegation import DelegationGuide
from noust.managers.authdns.server import AuthDnsServers, TransferKey
from noust.managers.authdns.zones import ZoneEditor
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    CodedText,
    JobAcceptedResponse,
    require_elevated,
)
from noust.web.api.server.common import ServerRoute
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=ServerRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]


# -- models --------------------------------------------------------------------------------


class AuthDnsSecondaryModel(BaseModel):
    """
    A server a primary copies its zones to.

    Attributes:
        address: Where it is reached.
        hostname: The name it has as a name server.
        added_at: When it was added.
        added_by: Who added it.
    """

    address: str
    hostname: str | None = None
    added_at: str = ""
    added_by: str | None = None


class AuthDnsServerStatusModel(BaseModel):
    """
    Noust DNS on this server.

    Attributes:
        engine: The program that answers (``knot``).
        installed: It is on this machine.
        version: Its version.
        running: Its service runs.
        configured: Noust has set this server up.
        role: ``primary`` or ``secondary``.
        hostname: The name it has as a name server.
        listen: The addresses it answers on.
        primary_address: A secondary's primary.
        zones: How many zones it serves.
        secondaries: A primary's secondaries.
        candidates: Addresses it could answer on, while it is not set up.
        firewall: What Noust opened for it.
        foreign_zones: Zones of a Knot somebody else configured, which Noust does not take over.
        warnings: Codes of the sentences that say what running a name server takes on
            (``serverText.<code>``); the console shows them before it is set up.
    """

    engine: str
    installed: bool
    version: str | None = None
    running: bool
    configured: bool
    role: str | None = None
    hostname: str | None = None
    listen: list[str] = Field(default_factory=list)
    primary_address: str | None = None
    zones: int = 0
    secondaries: list[AuthDnsSecondaryModel] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    firewall: dict[str, Any] = Field(default_factory=dict)
    foreign_zones: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AuthDnsTransferKeyModel(BaseModel):
    """
    The key a primary and its secondaries sign transfers with.

    Attributes:
        name: Its name, the same on every server.
        algorithm: ``hmac-sha256``.
        secret: The key, base64. Only in the answer to the call that asks for it.
    """

    name: str
    algorithm: str
    secret: str


class AuthDnsTransferKeyIn(BaseModel):
    """
    The key a secondary is given.

    Attributes:
        secret: The key as the primary's ``GET .../server/key`` returned it.
    """

    secret: str = Field(min_length=16, max_length=512)


class AuthDnsInstallRequest(BaseModel):
    """
    Install the program that answers.

    Attributes:
        epel: Agree to enable EPEL, where Knot comes from on RHEL and its rebuilds.
    """

    epel: bool = False


class AuthDnsSetupRequest(BaseModel):
    """
    Make this server a primary or a secondary.

    Attributes:
        role: ``primary`` holds the zones; ``secondary`` copies them from a primary.
        hostname: The name it has as a name server.
        listen: The addresses to answer on; the server's public addresses by default.
        primary_address: For a secondary, the primary's address.
        transfer_key: For a secondary, the key the primary signs its transfers with.
        epel: Agree to enable EPEL, where Knot comes from on RHEL and its rebuilds.
    """

    role: Literal["primary", "secondary"]
    hostname: StrictStr | None = Field(default=None, max_length=253)
    listen: list[StrictStr] | None = None
    primary_address: StrictStr | None = Field(default=None, max_length=64)
    transfer_key: AuthDnsTransferKeyIn | None = None
    epel: bool = False


class AuthDnsAddSecondaryRequest(BaseModel):
    """
    Let a server copy this primary's zones.

    Attributes:
        address: Where it is reached.
        hostname: The name it has as a name server.
    """

    address: StrictStr = Field(max_length=64)
    hostname: StrictStr | None = Field(default=None, max_length=253)


class AuthDnsRemovedModel(BaseModel):
    """
    The answer to a removal.

    Attributes:
        removed: There was something to remove.
    """

    removed: bool


class AuthDnsRecordModel(BaseModel):
    """
    A record of a zone.

    Attributes:
        id: Its number.
        zone: The zone.
        name: Its name relative to the zone; ``@`` is the zone itself.
        type: ``A``, ``AAAA``, ``CNAME``, ``MX``, ``NS``, ``TXT``, ``SRV``, ``CAA``, ``PTR`` or ``DS``.
        content: What it holds, in canonical form.
        ttl: Seconds to live; null for the zone's.
        created_at: When it was made.
        created_by: Who made it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    id: int | None = None
    zone: str
    name: str
    type: str
    content: str
    ttl: int | None = None
    created_at: str = ""
    created_by: str | None = None
    updated_at: str = ""
    updated_by: str | None = None


class AuthDnsZoneSummaryModel(BaseModel):
    """
    A zone in a list.

    Attributes:
        name: The zone.
        records: How many records it holds (a primary's).
        serial: The SOA serial the server serves now.
        dnssec: The zone is signed; null on a secondary, which does not know whether the primary
            signs what it copied.
        role: ``master`` where the server holds the zone, ``slave`` where it copies it.
    """

    name: str
    records: int
    serial: int | None = None
    dnssec: bool | None = None
    role: str


class AuthDnsZoneListModel(BaseModel):
    """
    The zones this server serves.

    Attributes:
        zones: Each one.
    """

    zones: list[AuthDnsZoneSummaryModel]


class AuthDnsZoneModel(BaseModel):
    """
    A zone with its records.

    Attributes:
        name: The zone.
        nameservers: Its name servers, in order.
        contact: The address the SOA names.
        default_ttl: The TTL of a record that has none.
        dnssec: The zone is signed.
        serial: The SOA serial the server serves now.
        records: Its records.
        created_at: When it was made.
        created_by: Who made it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    nameservers: list[str]
    contact: str
    default_ttl: int
    dnssec: bool
    serial: int | None = None
    records: list[AuthDnsRecordModel]
    created_at: str = ""
    created_by: str | None = None
    updated_at: str = ""
    updated_by: str | None = None


class AuthDnsCreateZoneRequest(BaseModel):
    """
    Make a zone and serve it.

    Attributes:
        name: The zone, ``example.com``.
        nameservers: Its name servers; this server and its secondaries by default.
        contact: The address the SOA names; ``hostmaster@<zone>`` by default.
        default_ttl: The TTL of a record that has none.
        glue: Addresses of name servers that are inside the zone, by name.
        dnssec: Sign it.
    """

    name: StrictStr = Field(max_length=253)
    nameservers: list[StrictStr] | None = None
    contact: StrictStr | None = Field(default=None, max_length=320)
    default_ttl: StrictInt = 3600
    glue: dict[StrictStr, list[StrictStr]] | None = None
    dnssec: StrictBool = False


class AuthDnsUpdateZoneRequest(BaseModel):
    """
    Change a zone. A field left out is not changed.

    Attributes:
        nameservers: Its name servers.
        contact: The address the SOA names.
        default_ttl: The TTL of a record that has none.
        glue: Addresses of name servers that are inside the zone and not yet in it.
    """

    nameservers: list[StrictStr] | None = None
    contact: StrictStr | None = Field(default=None, max_length=320)
    default_ttl: StrictInt | None = None
    glue: dict[StrictStr, list[StrictStr]] | None = None


class AuthDnsAddRecordRequest(BaseModel):
    """
    Add a record.

    Attributes:
        name: Its name relative to the zone (``www``, ``@``, ``*.apps``).
        type: Its type.
        content: What it holds.
        ttl: Seconds to live; the zone's by default.
    """

    name: StrictStr = Field(max_length=253)
    type: StrictStr = Field(max_length=16)
    content: StrictStr = Field(max_length=4096)
    ttl: StrictInt | None = None


class AuthDnsUpdateRecordRequest(BaseModel):
    """
    Change a record. A field left out is not changed.

    Attributes:
        content: What it holds.
        ttl: Seconds to live.
        default_ttl: Go back to the zone's TTL.
    """

    content: StrictStr | None = Field(default=None, max_length=4096)
    ttl: StrictInt | None = None
    default_ttl: StrictBool = False


class AuthDnsDsModel(BaseModel):
    """
    A DS record, which the registrar needs.

    Attributes:
        key_tag: The key's tag.
        algorithm: The signing algorithm's number (13 is ECDSA P-256).
        digest_type: 2 is SHA-256, 4 is SHA-384.
        digest: The digest, hexadecimal.
        text: The record as a registrar's form takes it.
    """

    key_tag: int
    algorithm: int
    digest_type: int
    digest: str
    text: str


class AuthDnsDnssecModel(BaseModel):
    """
    The signing of a zone.

    Attributes:
        zone: The zone.
        enabled: It is signed.
        algorithm: How.
        ds: The DS records to give the registrar; empty until the key exists.
    """

    zone: str
    enabled: bool
    algorithm: str
    ds: list[AuthDnsDsModel]


class AuthDnsDnssecRequest(BaseModel):
    """
    Sign a zone or stop signing it.

    Attributes:
        enabled: Whether it is signed.
        ds_removed: The DS record is no longer at the registrar. Unsigning a signed zone is
            refused without it: validating resolvers would refuse the whole domain.
    """

    enabled: StrictBool
    ds_removed: StrictBool = False


class AuthDnsSentenceModel(CodedText):
    """
    A sentence for a person, with its English text.

    Attributes:
        text: The sentence in English, always sent.
        hint: How to fix it in English, when the sentence has a fix.
    """

    text: str
    hint: str | None = None


class AuthDnsNameServerModel(BaseModel):
    """
    A name server of a zone.

    Attributes:
        name: Its host name.
        addresses: Where it answers; empty when only resolving the name can say.
        glue: The registrar must hold its address (the name is inside the zone).
    """

    name: str
    addresses: list[str]
    glue: bool


class AuthDnsDelegationPlanModel(BaseModel):
    """
    What to do at the registrar.

    Attributes:
        zone: The zone.
        nameservers: Its name servers.
        dnssec: The zone is signed.
        ds: The DS records to give the registrar.
        steps: The steps, in order.
        warnings: What would make the delegation fragile.
    """

    zone: str
    nameservers: list[AuthDnsNameServerModel]
    dnssec: bool
    ds: list[AuthDnsDsModel]
    steps: list[AuthDnsSentenceModel]
    warnings: list[AuthDnsSentenceModel]


class AuthDnsServerCheckModel(BaseModel):
    """
    What one name server said when asked directly.

    Attributes:
        name: Its host name.
        address: The address asked.
        answers: It answered.
        authoritative: It answered as the authority for the zone.
        serial: The SOA serial it serves.
        refuses_recursion: It refused a question about another domain; null when not asked.
    """

    name: str
    address: str
    answers: bool
    authoritative: bool
    serial: int | None = None
    refuses_recursion: bool | None = None


class AuthDnsDelegationCheckModel(BaseModel):
    """
    Whether the delegation is in place.

    Attributes:
        zone: The zone.
        servers: What each name server answered.
        delegated: Public resolvers find exactly the zone's name servers; null when none answered.
        public_nameservers: The name servers they find.
        ds_published: The parent holds a DS; null when not asked or not answered.
        ds_matches: It is this zone's key.
        problems: What is not as it should be.
        ready: Nothing is wrong and the world sees the delegation.
    """

    zone: str
    servers: list[AuthDnsServerCheckModel]
    delegated: bool | None = None
    public_nameservers: list[str]
    ds_published: bool | None = None
    ds_matches: bool | None = None
    problems: list[AuthDnsSentenceModel]
    ready: bool


# -- the server's part ---------------------------------------------------------------------


@router.get("/server", response_model=AuthDnsServerStatusModel)
def server_status(session: Session) -> dict[str, Any]:
    """
    Noust DNS on this server: whether it is installed, its role, its zones and what it takes on.

    Args:
        session: The authenticated session.

    Returns:
        The state, whether or not the server has been set up.
    """
    return AuthDnsServers().status().to_dict()


def install_job(epel: bool, actor: str, job_context: JobContext) -> dict[str, Any]:
    """
    Install the program that answers, as a job whose log streams the package manager.

    Args:
        epel: The operator agreed to enable EPEL.
        actor: Who asked.
        job_context: The job's handle.

    Returns:
        Noust DNS on this server.
    """
    job_context.update("Installing Knot DNS", 10)
    return AuthDnsServers().install(epel=epel, on_output=job_context.log).to_dict()


def setup_job(
    request: dict[str, Any], transfer_secret: str | None, actor: str, job_context: JobContext
) -> dict[str, Any]:
    """
    Make this server a primary or a secondary, as a job whose log streams the installer.

    Args:
        request: The checked :class:`AuthDnsSetupRequest` without its key.
        transfer_secret: A secondary's key; registered as a secret so no line of the log quotes it.
        actor: Who asked.
        job_context: The job's handle.

    Returns:
        Noust DNS on this server.
    """
    if transfer_secret:
        job_context.add_secret_env({"DNS_TRANSFER_KEY": transfer_secret})
    key = TransferKey("noust-xfr", "hmac-sha256", transfer_secret) if transfer_secret else None
    job_context.update("Setting up Noust DNS", 10)
    status = AuthDnsServers().setup(
        request["role"],
        hostname=request.get("hostname"),
        listen=request.get("listen"),
        primary_address=request.get("primary_address"),
        transfer_key=key,
        epel=bool(request.get("epel")),
        actor=actor,
        on_output=job_context.log,
    )
    return status.to_dict()


@router.post("/server/install", response_model=JobAcceptedResponse, status_code=202)
def install_server(body: AuthDnsInstallRequest, session: Elevated) -> JobAcceptedResponse:
    """
    Install Knot DNS, the program that answers; configure nothing.

    Needs sudo mode: it installs a package.

    Args:
        body: Whether EPEL may be enabled.
        session: The elevated session.

    Returns:
        The queued job.
    """
    AuthDnsServers().check_install(epel=body.epel)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_ACTION,
        name="Install Knot DNS",
        description="Installing the name server program of Noust DNS",
        func=install_job,
        kwargs={"epel": body.epel, "actor": actor},
        metadata={"kind": "dns_server_install"},
        total_steps=100,
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Installing Knot DNS queued",
        job=job.to_dict(),
    )


@router.post("/server", response_model=JobAcceptedResponse, status_code=202)
def setup_server(body: AuthDnsSetupRequest, session: Elevated) -> JobAcceptedResponse:
    """
    Make this server a primary or a secondary of Noust DNS.

    Needs sudo mode: it installs a package, writes the name server's configuration, opens port 53
    in the firewall and answers on the addresses given. An error in any step puts the earlier
    ones back. It is an option and never a default: the status lists the two costs of running a
    name server (``warnings``) for the console to show first.

    Args:
        body: The role, the name, the addresses and, for a secondary, the primary and its key.
        session: The elevated session.

    Returns:
        The queued job; its log streams the installer.
    """
    actor = actor_label(session)
    AuthDnsServers().check_setup(
        body.role,
        hostname=body.hostname,
        listen=body.listen,
        primary_address=body.primary_address,
        has_key=body.transfer_key is not None,
    )
    AuthDnsServers().check_install(epel=body.epel)
    request = dump_model(body)
    secret = (request.pop("transfer_key", None) or {}).get("secret")
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_ACTION,
        name=f"Set up Noust DNS as a {body.role}",
        description="Installing Knot DNS if needed, writing its configuration and opening port 53",
        func=setup_job,
        kwargs={"request": request, "transfer_secret": secret, "actor": actor},
        metadata={"kind": "dns_server_setup", "role": body.role},
        total_steps=100,
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Setting up Noust DNS as a {body.role} queued",
        job=job.to_dict(),
    )


@router.delete("/server", response_model=AuthDnsRemovedModel)
def remove_server(session: Elevated, delete_zones: bool = False) -> AuthDnsRemovedModel:
    """
    Stop the name server and take Noust DNS off this server.

    The package and the signing keys stay: the keys are the only copy of what the DS record at the
    registrar points at.

    Args:
        session: The elevated session.
        delete_zones: Also delete the zones and records this server serves; without it a server
            that serves zones is refused.

    Returns:
        Whether Noust DNS was set up here.

    Raises:
        ValidationError: The server serves zones and ``delete_zones`` is false (400).
    """
    servers = AuthDnsServers()
    was = servers.records.server() is not None
    servers.remove(delete_zones=delete_zones, actor=actor_label(session))
    return AuthDnsRemovedModel(removed=was)


@router.get("/server/key", response_model=AuthDnsTransferKeyModel)
def transfer_key(session: Elevated) -> dict[str, str]:
    """
    The key this primary signs its transfers with, for the secondary that is being set up.

    Needs sudo mode: it is a secret. Whoever holds it can ask this primary for its zones, which
    are public data, and nothing else.

    Args:
        session: The elevated session.

    Returns:
        The key.

    Raises:
        ValidationError: Noust DNS is not set up here (400).
    """
    return AuthDnsServers().transfer_key().to_dict()


@router.post("/server/secondaries", response_model=AuthDnsSecondaryModel, status_code=201)
def add_secondary(body: AuthDnsAddSecondaryRequest, session: Session) -> dict[str, Any]:
    """
    Let a server copy this primary's zones, and tell it when they change.

    Args:
        body: Its address and name.
        session: The authenticated session.

    Returns:
        The secondary.

    Raises:
        ValidationError: This server is not a primary, or a value is not usable (400).
    """
    return (
        AuthDnsServers()
        .add_secondary(body.address, body.hostname, actor=actor_label(session))
        .to_dict()
    )


@router.delete("/server/secondaries/{address}", response_model=AuthDnsRemovedModel)
def remove_secondary(address: str, session: Session) -> AuthDnsRemovedModel:
    """
    Stop copying this primary's zones to a server.

    Args:
        address: The secondary's address.
        session: The authenticated session.

    Returns:
        Whether there was one.
    """
    return AuthDnsRemovedModel(
        removed=AuthDnsServers().remove_secondary(address, actor=actor_label(session))
    )


# -- zones ---------------------------------------------------------------------------------


@router.get("/zones", response_model=AuthDnsZoneListModel)
def list_zones(session: Session) -> dict[str, Any]:
    """
    The zones this server serves: a primary's own, or the ones a secondary copied.

    Args:
        session: The authenticated session.

    Returns:
        The zones.

    Raises:
        ValidationError: Noust DNS is not set up here (400).
    """
    return {"zones": [item.to_dict() for item in ZoneEditor().zones()]}


@router.post("/zones", response_model=AuthDnsZoneModel, status_code=201)
def create_zone(body: AuthDnsCreateZoneRequest, session: Session) -> dict[str, Any]:
    """
    Make a zone and serve it. The domain answers from here once its registrar delegates it.

    Args:
        body: The zone and its name servers.
        session: The authenticated session.

    Returns:
        The zone.

    Raises:
        ValidationError: A value is not usable, the zone exists, or a name server inside it has
            no address (400).
    """
    return (
        ZoneEditor()
        .create_zone(
            body.name,
            nameservers=body.nameservers,
            contact=body.contact,
            default_ttl=body.default_ttl,
            glue=body.glue,
            dnssec=body.dnssec,
            actor=actor_label(session),
        )
        .to_dict()
    )


@router.get("/zones/{zone}", response_model=AuthDnsZoneModel)
def get_zone(zone: str, session: Session) -> dict[str, Any]:
    """
    A zone with its records.

    Args:
        zone: The zone.
        session: The authenticated session.

    Returns:
        The zone.

    Raises:
        ValidationError: There is no such zone here (400).
    """
    return ZoneEditor().get(zone).to_dict()


@router.patch("/zones/{zone}", response_model=AuthDnsZoneModel)
def update_zone(zone: str, body: AuthDnsUpdateZoneRequest, session: Session) -> dict[str, Any]:
    """
    Change a zone's name servers, contact or default TTL.

    Args:
        zone: The zone.
        body: What to change.
        session: The authenticated session.

    Returns:
        The zone.
    """
    return (
        ZoneEditor()
        .update_zone(
            zone,
            nameservers=body.nameservers,
            contact=body.contact,
            default_ttl=body.default_ttl,
            glue=body.glue,
            actor=actor_label(session),
        )
        .to_dict()
    )


@router.delete("/zones/{zone}", response_model=AuthDnsRemovedModel)
def delete_zone(zone: str, session: Elevated) -> AuthDnsRemovedModel:
    """
    Stop serving a zone and delete it with its records.

    Needs sudo mode: resolvers that still point at this server stop finding the domain.

    Args:
        zone: The zone.
        session: The elevated session.

    Returns:
        Whether there was one.
    """
    return AuthDnsRemovedModel(removed=ZoneEditor().delete_zone(zone, actor=actor_label(session)))


@router.post("/zones/{zone}/records", response_model=AuthDnsRecordModel, status_code=201)
def add_record(zone: str, body: AuthDnsAddRecordRequest, session: Session) -> dict[str, Any]:
    """
    Add a record to a zone and serve it.

    Args:
        zone: The zone.
        body: The record.
        session: The authenticated session.

    Returns:
        The record.

    Raises:
        ValidationError: A value is not usable, the record is there, or it cannot be at that name
            with what the name has (400).
    """
    return (
        ZoneEditor()
        .add_record(
            zone, body.name, body.type, body.content, ttl=body.ttl, actor=actor_label(session)
        )
        .to_dict()
    )


@router.patch("/zones/{zone}/records/{record_id}", response_model=AuthDnsRecordModel)
def update_record(
    zone: str, record_id: int, body: AuthDnsUpdateRecordRequest, session: Session
) -> dict[str, Any]:
    """
    Change what a record holds, or its TTL.

    Args:
        zone: The zone.
        record_id: The record's number.
        body: What to change.
        session: The authenticated session.

    Returns:
        The record.
    """
    return (
        ZoneEditor()
        .update_record(
            zone,
            record_id,
            content=body.content,
            ttl=body.ttl,
            clear_ttl=body.default_ttl,
            actor=actor_label(session),
        )
        .to_dict()
    )


@router.delete("/zones/{zone}/records/{record_id}", response_model=AuthDnsRemovedModel)
def delete_record(zone: str, record_id: int, session: Session) -> AuthDnsRemovedModel:
    """
    Remove a record.

    Args:
        zone: The zone.
        record_id: The record's number.
        session: The authenticated session.

    Returns:
        Whether there was one.
    """
    return AuthDnsRemovedModel(
        removed=ZoneEditor().delete_record(zone, record_id, actor=actor_label(session))
    )


# -- DNSSEC and the delegation -------------------------------------------------------------


@router.get("/zones/{zone}/dnssec", response_model=AuthDnsDnssecModel)
def get_dnssec(zone: str, session: Session) -> dict[str, Any]:
    """
    Whether a zone is signed and, when it is, the DS records the registrar is given.

    Args:
        zone: The zone.
        session: The authenticated session.

    Returns:
        The state of the signing.
    """
    return ZoneEditor().dnssec(zone).to_dict()


@router.put("/zones/{zone}/dnssec", response_model=AuthDnsDnssecModel)
def set_dnssec(zone: str, body: AuthDnsDnssecRequest, session: Elevated) -> dict[str, Any]:
    """
    Sign a zone, or stop signing it.

    Needs sudo mode. Unsigning is refused unless ``ds_removed`` says the registrar no longer
    holds the DS: while it does, every validating resolver refuses the domain.

    Args:
        zone: The zone.
        body: Whether it is signed.
        session: The elevated session.

    Returns:
        The state of the signing; when it was just turned on, with the DS records.

    Raises:
        ValidationError: Unsigning without ``ds_removed`` (400).
    """
    return (
        ZoneEditor()
        .set_dnssec(zone, body.enabled, ds_removed=body.ds_removed, actor=actor_label(session))
        .to_dict()
    )


@router.post("/zones/{zone}/dnssec/ds-published", response_model=AuthDnsDnssecModel)
def ds_published(zone: str, session: Session) -> dict[str, Any]:
    """
    Tell the server the registrar now has the DS record.

    Args:
        zone: The zone.
        session: The authenticated session.

    Returns:
        The state of the signing.

    Raises:
        ValidationError: The zone is not signed (400).
    """
    return ZoneEditor().confirm_ds(zone, actor=actor_label(session)).to_dict()


@router.get("/zones/{zone}/delegation", response_model=AuthDnsDelegationPlanModel)
def delegation_plan(zone: str, session: Session) -> dict[str, Any]:
    """
    Exactly what to do at the registrar to delegate a domain to this server, in order.

    Args:
        zone: The zone.
        session: The authenticated session.

    Returns:
        The steps (sentences with a code), the name servers with the addresses the registrar needs
        as glue, the DS records of a signed zone and what would make the delegation fragile.
    """
    return DelegationGuide().plan(zone).to_dict()


@router.get("/zones/{zone}/delegation/check", response_model=AuthDnsDelegationCheckModel)
def delegation_check(zone: str, session: Session) -> dict[str, Any]:
    """
    Look at the world and say whether the domain is delegated to this server.

    It asks each name server directly and the public resolvers; it changes nothing.

    Args:
        zone: The zone.
        session: The authenticated session.

    Returns:
        What each name server answered, what resolvers find and what is missing.
    """
    return DelegationGuide().check(zone).to_dict()


__all__ = ["router"]
