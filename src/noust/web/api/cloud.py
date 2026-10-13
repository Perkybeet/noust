# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers created at a cloud provider, over HTTP: ``/api/fleet/cloud``.

A thin layer over :mod:`noust.fleet.cloud` (rule 3). Four things, all
``fleet.manage`` (an administrator's): the provider accounts a central holds
tokens for, the catalog of what each offers, the cloud-init library, and the
machines themselves.

- **A token is in a request body once and in nothing afterwards.** It goes to the
  secret store; no response, job, log line or approval record carries it
  (:func:`~noust.core.accounts.approvals.one_time_fields` leaves it out of the
  fingerprint of the call).
- **Creating a server is a job** whose log streams what happens (the provider's
  answers, the wait for the host key, the installer's output). Everything that
  needs no connection is checked before it is queued, so a refusal is the
  response and no job exists.
- **Creating and deleting need sudo mode.** One costs money and puts a login on a
  new machine; the other destroys one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    require_elevated,
    require_scope,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

if TYPE_CHECKING:
    from noust.fleet.cloud.accounts import CloudAccounts
    from noust.fleet.cloud.init import CloudInitLibrary
    from noust.fleet.cloud.provision import ServerProvisioner

router = APIRouter(route_class=NoustErrorRoute)


# ------------------------------------------------------------------ bodies


class AccountBody(BaseModel):
    """
    Body of ``POST /api/fleet/cloud/accounts``.

    Attributes:
        name: What to call the account.
        provider: ``hetzner``, ``digitalocean`` or ``vultr``.
        token: The provider's API token. Kept as a secret, never shown again.
        note: A free note, such as which project the token belongs to.
        verify: Ask the provider whether it accepts the token before keeping it.
    """

    name: str = Field(min_length=1, max_length=32)
    provider: str = Field(min_length=1, max_length=32)
    token: str = Field(min_length=1, max_length=1024)
    note: str | None = Field(default=None, max_length=200)
    verify: bool = True

    def __repr__(self) -> str:
        """Describe it without the token."""
        return f"AccountBody(name={self.name!r}, provider={self.provider!r}, token=***)"

    __str__ = __repr__


class TokenBody(BaseModel):
    """
    Body of ``PUT /api/fleet/cloud/accounts/{name}/token``.

    Attributes:
        token: The new API token.
        verify: Ask the provider whether it accepts it before replacing the old one.
    """

    token: str = Field(min_length=1, max_length=1024)
    verify: bool = True

    def __repr__(self) -> str:
        """Describe it without the token."""
        return "TokenBody(token=***)"

    __str__ = __repr__


class ScriptBody(BaseModel):
    """
    Body of ``PUT /api/fleet/cloud/scripts/{name}``.

    Attributes:
        kind: ``cloud-config`` or ``script``.
        content: The document or script.
        description: What it does, in a line.
    """

    kind: Literal["cloud-config", "script"]
    content: str = Field(min_length=1, max_length=65536)
    description: str = Field(default="", max_length=200)


class PreviewBody(BaseModel):
    """
    Body of ``POST /api/fleet/cloud/scripts/preview``.

    Attributes:
        scripts: Library entries to add, in order.
    """

    scripts: list[str] = Field(default_factory=list, max_length=20)


class ProvisionBody(BaseModel):
    """
    Body of ``POST /api/fleet/cloud/servers``.

    Attributes:
        name: The name the server gets on this central and at the provider.
        account: The cloud account to create it with.
        location: A location id from the account's catalog.
        plan: A plan id from the catalog.
        image: An image id from the catalog.
        access: The ceiling the server sets for this central.
        allow_host_access: Whether the central may also reach the host there.
        ssh_keys: Provider ids of the account's SSH keys to authorize for root too.
        firewalls: Provider ids of firewalls to attach.
        networks: Provider ids of private networks to attach.
        ipv4: Give it a public IPv4 address.
        ipv6: Give it a public IPv6 address.
        backups: Turn the provider's paid backups on.
        scripts: Library entries to boot with, in order.
        delete_on_failure: Delete the machine when it does not join the fleet.
    """

    name: str = Field(min_length=1, max_length=32)
    account: str = Field(min_length=1, max_length=32)
    location: str = Field(min_length=1, max_length=128)
    plan: str = Field(min_length=1, max_length=128)
    image: str = Field(min_length=1, max_length=128)
    access: Literal["read", "deploy", "admin"] = "admin"
    allow_host_access: bool = False
    ssh_keys: list[str] = Field(default_factory=list, max_length=20)
    firewalls: list[str] = Field(default_factory=list, max_length=20)
    networks: list[str] = Field(default_factory=list, max_length=20)
    ipv4: bool = True
    ipv6: bool = True
    backups: bool = False
    scripts: list[str] = Field(default_factory=list, max_length=20)
    delete_on_failure: bool = False


class ProviderOut(BaseModel):
    """
    One provider, for the form that adds an account.

    Attributes:
        kind: Its key.
        name: What it is called.
        token_url: Where the operator creates a token.
        token_help: What the token must be allowed to do.
        capabilities: What its create call can do.
        user_data_limit: Largest cloud-init document it accepts, in bytes.
    """

    kind: str
    name: str
    token_url: str
    token_help: str
    capabilities: dict[str, bool]
    user_data_limit: int


class AccountOut(BaseModel):
    """
    A cloud account, without its token.

    Attributes:
        name: Its name.
        provider: The provider's key.
        token_hint: The end of the token, ``...a1b2``.
        note: The note.
        created_at: When it was added.
        created_by: Who added it.
        verified_at: When the provider last accepted the token.
        last_used_at: When a server was last created with it.
    """

    name: str
    provider: str
    token_hint: str
    note: str | None = None
    created_at: str
    created_by: str | None = None
    verified_at: str | None = None
    last_used_at: str | None = None


class ScriptSummary(BaseModel):
    """
    A library entry, without its text.

    Attributes:
        name: Its name.
        kind: ``cloud-config`` or ``script``.
        description: What it does.
        builtin: Ships with Noust.
        bytes: Its size.
        created_at: When it was saved.
        created_by: Who saved it.
        updated_at: When it last changed.
    """

    name: str
    kind: str
    description: str
    builtin: bool
    bytes: int
    created_at: str | None = None
    created_by: str | None = None
    updated_at: str | None = None


class ScriptOut(ScriptSummary):
    """
    A library entry with its text.

    Attributes:
        content: The document or script.
    """

    content: str


class PreviewOut(BaseModel):
    """
    The document a server would boot with.

    Attributes:
        document: The ``#cloud-config``, the host key withheld.
        bytes: Its size once the real host key is in it.
    """

    document: str
    bytes: int


class MachineOut(BaseModel):
    """
    A machine this central created.

    Attributes:
        node: The server's name on this central.
        account: The cloud account.
        provider: The provider's key.
        provider_id: The provider's id.
        address: Where it answers.
        location: Where it is.
        plan: Its size.
        image: What it started from.
        state: ``creating``, ``booting``, ``enrolling``, ``ready``, ``failed`` or ``deleted``.
        error: Why it failed.
        job_id: The job that created it.
        created_at: When the provider answered.
        created_by: Who asked.
        updated_at: When the row last changed.
        remote: The provider's live report, when asked for.
        remote_error: Why the provider could not be asked.
    """

    node: str
    account: str
    provider: str
    provider_id: str
    address: str | None = None
    location: str | None = None
    plan: str | None = None
    image: str | None = None
    state: str
    error: str | None = None
    job_id: str | None = None
    created_at: str
    created_by: str | None = None
    updated_at: str
    remote: dict[str, Any] | None = None
    remote_error: str | None = None


class ChoiceOut(BaseModel):
    """
    One thing a provider offers to pick from.

    Attributes:
        id: What the create call takes.
        name: What the operator reads.
        detail: A short second line (``2 vCPU, 4 GB RAM, 40 GB disk``).
        locations: The locations a plan is sold in; absent when it is sold everywhere.
        arch: The processor a plan is for (``x86``, ``arm``).
        archs: The processors an image exists for.
        monthly: A plan's monthly price, in the account's currency, when the provider says.
        zone: A location's network zone.
        distribution: An image's distribution.
        version: An image's version.
    """

    id: str
    name: str
    detail: str = ""
    locations: list[str] | None = None
    arch: str | None = None
    archs: list[str] | None = None
    monthly: float | None = None
    zone: str | None = None
    distribution: str | None = None
    version: str | None = None


class CatalogOut(BaseModel):
    """
    What an account can create, as its provider lists it now.

    Attributes:
        provider: The provider's key.
        capabilities: What its create call can do.
        locations: Where a machine may be created.
        plans: The sizes on sale.
        images: The operating systems.
        ssh_keys: The account's own SSH keys.
        firewalls: Its firewalls.
        networks: Its private networks.
    """

    provider: str
    capabilities: dict[str, bool]
    locations: list[ChoiceOut]
    plans: list[ChoiceOut]
    images: list[ChoiceOut]
    ssh_keys: list[ChoiceOut]
    firewalls: list[ChoiceOut]
    networks: list[ChoiceOut]


class DeletedOut(BaseModel):
    """
    The outcome of deleting a machine.

    Attributes:
        node: The server's name.
        warnings: What the operator should know.
    """

    node: str
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ factories


def _accounts() -> CloudAccounts:
    """
    Build the accounts manager.

    Returns:
        A :class:`~noust.fleet.cloud.accounts.CloudAccounts`.
    """
    from noust.fleet.cloud.accounts import CloudAccounts

    return CloudAccounts()


def _library() -> CloudInitLibrary:
    """
    Build the cloud-init library.

    Returns:
        A :class:`~noust.fleet.cloud.init.CloudInitLibrary`.
    """
    from noust.fleet.cloud.init import CloudInitLibrary

    return CloudInitLibrary()


def _provisioner() -> ServerProvisioner:
    """
    Build the provisioner.

    Returns:
        A :class:`~noust.fleet.cloud.provision.ServerProvisioner`.
    """
    from noust.fleet.cloud.provision import ServerProvisioner

    return ServerProvisioner()


# ------------------------------------------------------------------ providers


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
) -> list[dict[str, Any]]:
    """
    List the providers a server can be created at.

    Args:
        session: The authenticated session, of ``admin`` scope.

    Returns:
        Each provider, with where its token is made and what its create call can do.
    """
    from noust.fleet.cloud.providers import describe_providers

    return describe_providers()


# ------------------------------------------------------------------ accounts


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
) -> list[dict[str, Any]]:
    """
    List the cloud accounts, tokens never included.

    Args:
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The accounts.
    """
    return [account.to_dict() for account in _accounts().list()]


@router.post("/accounts", response_model=AccountOut, status_code=201)
def add_account(
    body: AccountBody, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> dict[str, Any]:
    """
    Keep a provider token under a name, after the provider accepted it.

    Args:
        body: The name, the provider and the token.
        session: The authenticated session in sudo mode.

    Returns:
        The account. The token is in the secret store and nowhere else.
    """
    account = _accounts().add(
        body.name,
        body.provider,
        body.token,
        note=body.note,
        actor=actor_label(session),
        verify=body.verify,
    )
    return account.to_dict()


@router.put("/accounts/{name}/token", response_model=AccountOut)
def replace_account_token(
    name: str, body: TokenBody, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> dict[str, Any]:
    """
    Replace an account's token, after the provider accepted the new one.

    Args:
        name: The account.
        body: The new token.
        session: The authenticated session in sudo mode.

    Returns:
        The account.
    """
    return (
        _accounts()
        .replace_token(name, body.token, actor=actor_label(session), verify=body.verify)
        .to_dict()
    )


@router.post("/accounts/{name}/verify", response_model=AccountOut)
def verify_account(
    name: str, session: Annotated[dict[str, Any], Depends(require_scope("admin"))]
) -> dict[str, Any]:
    """
    Ask the provider whether it still accepts the account's token.

    Args:
        name: The account.
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The account, with the time it was verified.
    """
    return _accounts().verify(name).to_dict()


@router.delete("/accounts/{name}", status_code=204)
def remove_account(
    name: str,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
    force: bool = Query(default=False),
) -> None:
    """
    Forget an account and its token.

    Args:
        name: The account.
        session: The authenticated session in sudo mode.
        force: Remove it although machines made with it are still recorded.
    """
    _accounts().remove(name, actor=actor_label(session), force=force)


@router.get("/accounts/{name}/catalog", response_model=CatalogOut)
def account_catalog(
    name: str, session: Annotated[dict[str, Any], Depends(require_scope("admin"))]
) -> dict[str, Any]:
    """
    Read what an account can create: locations, plans, images, keys, firewalls, networks.

    Args:
        name: The account.
        session: The authenticated session, of ``admin`` scope.

    Returns:
        ``{"provider", "capabilities", "locations", "plans", "images", "ssh_keys",
        "firewalls", "networks"}``, as the provider lists them now.
    """
    provider = _accounts().provider(name)
    return {
        "provider": provider.kind,
        "capabilities": provider.capabilities.to_dict(),
        **provider.catalog().to_dict(),
    }


# ------------------------------------------------------------------ the library


@router.get("/scripts", response_model=list[ScriptSummary])
def list_scripts(
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
) -> list[dict[str, Any]]:
    """
    List the cloud-init library: what ships with Noust, then the operator's own.

    Args:
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The entries, without their text.
    """
    return [entry.to_dict(content=False) for entry in _library().list()]


@router.post("/scripts/preview", response_model=PreviewOut)
def preview_scripts(
    body: PreviewBody, session: Annotated[dict[str, Any], Depends(require_scope("admin"))]
) -> dict[str, Any]:
    """
    Show the document a new server would boot with, its host key withheld.

    Args:
        body: The library entries to add, in order.
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The document, and its size with a real host key in it.
    """
    from noust.fleet.cloud.init import preview, render_user_data

    entries = _library().resolve(body.scripts)
    sized = render_user_data(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + "A" * 400
        + "\n-----END OPENSSH PRIVATE KEY-----\n",
        "ssh-ed25519 " + "A" * 68,
        entries,
    )
    return {"document": preview(entries), "bytes": len(sized.encode("utf-8"))}


@router.get("/scripts/{name}", response_model=ScriptOut)
def get_script(
    name: str, session: Annotated[dict[str, Any], Depends(require_scope("admin"))]
) -> dict[str, Any]:
    """
    Read one entry of the library.

    Args:
        name: Its name.
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The entry, with its text.
    """
    return _library().get(name).to_dict()


@router.put("/scripts/{name}", response_model=ScriptOut)
def save_script(
    name: str,
    body: ScriptBody,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> dict[str, Any]:
    """
    Add an entry to the library, or replace the operator's own of the same name.

    Args:
        name: Its name.
        body: The kind, the text and a description.
        session: The authenticated session in sudo mode.

    Returns:
        The entry.
    """
    return (
        _library()
        .save(
            name,
            body.kind,
            body.content,
            description=body.description,
            actor=actor_label(session),
        )
        .to_dict()
    )


@router.delete("/scripts/{name}", status_code=204)
def delete_script(name: str, session: Annotated[dict[str, Any], Depends(require_elevated)]) -> None:
    """
    Delete the operator's entry.

    Args:
        name: Its name.
        session: The authenticated session in sudo mode.
    """
    _library().delete(name, actor=actor_label(session))


# ------------------------------------------------------------------ machines


def provision_job(request: dict[str, Any], actor: str, job_context: JobContext) -> dict[str, Any]:
    """
    Create a server and enroll it, as a job whose log streams what happens.

    Args:
        request: A :class:`~noust.fleet.cloud.provision.ProvisionRequest`'s fields.
        actor: Who asked.
        job_context: The job's handle.

    Returns:
        The node and the machine's record. No key or token is in either.
    """
    from noust.fleet.cloud.provision import ProvisionRequest

    provisioner = _provisioner()
    job_context.update("Creating the server", 5)
    node = provisioner.provision(
        ProvisionRequest(**request),
        actor=actor,
        job_id=job_context.job_id,
        log=job_context.log,
    )
    return {"node": node.to_dict(), "machine": provisioner.machine(request["name"])}


@router.get("/servers", response_model=list[MachineOut])
def list_machines(
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
) -> list[dict[str, Any]]:
    """
    List the machines this central created.

    Args:
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The records, the newest first.
    """
    return [machine.to_dict() for machine in _provisioner().servers.list()]


@router.post("/servers", response_model=JobAcceptedResponse, status_code=202)
def create_machine(
    body: ProvisionBody, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Create a server at a provider and enroll it.

    Everything that needs no connection is checked first; the job then asks the
    provider, waits for the machine and for the host key chosen for it, and enrolls it.

    Args:
        body: What to create.
        session: The authenticated session in sudo mode.

    Returns:
        The queued job; its log streams every step.
    """
    from noust.fleet.cloud.provision import ProvisionRequest

    fields: dict[str, Any] = {
        "name": body.name,
        "account": body.account,
        "location": body.location,
        "plan": body.plan,
        "image": body.image,
        "access": body.access,
        "host_access": body.allow_host_access,
        "ssh_keys": body.ssh_keys,
        "firewalls": body.firewalls,
        "networks": body.networks,
        "ipv4": body.ipv4,
        "ipv6": body.ipv6,
        "backups": body.backups,
        "scripts": body.scripts,
        "delete_on_failure": body.delete_on_failure,
    }
    provisioner = _provisioner()
    provider, _entries = provisioner.validate(ProvisionRequest(**fields))
    job = get_job_manager().create_job(
        job_type=JobType.NODE_PROVISION,
        name=f"Create {body.name}",
        description=f"Creating {body.name} at {provider.display_name} and joining it to the fleet",
        func=provision_job,
        kwargs={"request": fields, "actor": actor_label(session)},
        metadata={
            "kind": "node_provision",
            "node": body.name,
            "provider": provider.kind,
            "account": body.account,
        },
        total_steps=100,
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Creating {body.name} queued",
        job=job.to_dict(),
    )


@router.get("/servers/{node}", response_model=MachineOut)
def get_machine(
    node: str,
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
    live: bool = Query(default=False),
) -> dict[str, Any]:
    """
    Describe one machine this central created.

    Args:
        node: The server's name on this central.
        session: The authenticated session, of ``admin`` scope.
        live: Ask the provider for its state too.

    Returns:
        The record and, when asked, the provider's live report.
    """
    return _provisioner().machine(node, live=live)


@router.post("/servers/{node}/power-on", response_model=MachineOut)
def power_on_machine(
    node: str, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> dict[str, Any]:
    """
    Start a machine that is off.

    Args:
        node: The server's name on this central.
        session: The authenticated session in sudo mode.

    Returns:
        The record, with the provider's live report.
    """
    provisioner = _provisioner()
    provisioner.power_on(node, actor=actor_label(session))
    return provisioner.machine(node, live=True)


@router.delete("/servers/{node}", response_model=DeletedOut)
def delete_machine(
    node: str,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
    confirm: str = Query(default="", description="The server's name, typed to confirm."),
    remove_node: bool = Query(default=False),
) -> dict[str, Any]:
    """
    Delete a machine at its provider, which destroys it and stops its charge.

    Args:
        node: The server's name on this central.
        session: The authenticated session in sudo mode.
        confirm: The server's name again: deleting a machine is not undone.
        remove_node: Also remove the server from the fleet.

    Returns:
        The warnings for the operator.
    """
    from noust.core.exceptions import ValidationError

    if confirm != node:
        raise ValidationError(
            f"Type the server's name ({node}) to confirm",
            details="Deleting the machine destroys it and everything on it.",
            field="confirm",
        )
    warnings = _provisioner().delete_machine(
        node, remove_node=remove_node, actor=actor_label(session)
    )
    return {"node": node, "warnings": warnings}
