# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Create a server at a cloud provider and bring it into the fleet.

``ServerProvisioner.provision`` is one job, in the order that keeps the trust model of
``noust fleet enroll`` and spends nothing it cannot account for:

1. **Everything that can be checked without a connection is checked first**: the
   name, the account, the library entries, the size of the document, what the
   provider's create call can do, whether this central may register a server at all.
2. **Two keys are generated and kept only in memory**: the server's SSH host key,
   which goes into the cloud-init document, and a login key of one use.
3. **The login key's public half is given to the provider** as an SSH key of the
   account, so the provider authorizes it for root itself (and does not e-mail a
   root password), and the machine is created. It is on record from then on.
4. **The central waits for it to run, then for its sshd to present the host key it
   chose.** A different key is never accepted: it is waited out and, at the
   deadline, refused. Nothing is sent to an address whose key is not the chosen one.
5. **The guided enrollment does the rest** (:class:`~noust.fleet.enroll.NodeEnroller`),
   with the login key as the credential used once: Noust is installed with this
   central's own copy of the installer, the central is authorized, the node joins,
   and the login key is taken out of root's ``authorized_keys``.
6. **Whatever happens, the provider's copy of the login key is deleted.**

A machine that was created but did not join stays on record (and at the provider)
with the reason, unless ``delete_on_failure`` was asked: deleting a machine is what
stops the charge, but it is also what destroys the evidence of what went wrong.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets as token_source
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from noust.core import audit
from noust.core.exceptions import CloudProviderError, NodeError, NoustError, ValidationError
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import NodeRecord
from noust.fleet.cloud.accounts import CloudAccounts
from noust.fleet.cloud.init import CloudInitLibrary, LibraryEntry, render_user_data
from noust.fleet.cloud.models import CreateSpec, RemoteServer
from noust.fleet.cloud.providers import CloudProvider
from noust.fleet.cloud.servers import (
    BOOTING,
    DELETED,
    ENROLLING,
    FAILED,
    READY,
    CloudServer,
    CloudServers,
)
from noust.fleet.enroll import Credential, EnrollRequest, NodeEnroller
from noust.fleet.keys import generate_ephemeral_pair
from noust.fleet.models import validate_node_name
from noust.fleet.policy import validate_access_level

logger = logging.getLogger(__name__)

#: Seconds the provider may take to report the machine running with an address.
BOOT_TIMEOUT = 600.0

#: Seconds its sshd may take to present the chosen host key once it runs.
SSH_TIMEOUT = 900.0

#: Seconds between looks.
POLL_SECONDS = 5.0

#: A machine the provider does not know yet is waited for this long before it is an error.
VISIBILITY_GRACE = 60.0

#: ``log(message, level)``: the job's log, or a terminal.
Log = Callable[[str, str], None]


def _quiet(_message: str, _level: str = "info") -> None:
    """Say nothing."""


@dataclass
class ProvisionRequest:
    """
    What the operator asked for.

    Attributes:
        name: The name the server gets on this central, and at the provider.
        account: The cloud account to create it with.
        location: A location id from the provider's catalog.
        plan: A plan id from the catalog.
        image: An image id from the catalog.
        access: The ceiling the server sets for this central: ``read``, ``deploy`` or ``admin``.
        host_access: Whether the central may also reach the host itself there.
        ssh_keys: Provider ids of the account's own SSH keys to authorize for root too,
            for the operator's access.
        firewalls: Provider ids of firewalls to attach.
        networks: Provider ids of private networks to attach.
        ipv4: Give it a public IPv4 address.
        ipv6: Give it a public IPv6 address.
        backups: Turn the provider's backups on (they cost extra).
        scripts: Names of library entries to boot with, in order.
        delete_on_failure: Delete the machine when it does not join the fleet.
    """

    name: str
    account: str
    location: str
    plan: str
    image: str
    access: str = "admin"
    host_access: bool = False
    ssh_keys: list[str] = field(default_factory=list)
    firewalls: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    ipv4: bool = True
    ipv6: bool = True
    backups: bool = False
    scripts: list[str] = field(default_factory=list)
    delete_on_failure: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The request, JSON-ready: nothing in it is secret.
        """
        return dict(self.__dict__)


class ServerProvisioner:
    """
    Create servers at a provider and enroll them.

    Args:
        accounts: The cloud accounts; the process-wide ones by default.
        library: The cloud-init library.
        enroller: The guided enrollment the machine is joined with.
        servers: The record of created machines.
        runner: The runner ssh-keygen goes through.
        fs: The filesystem seam.
        sleep: Pause between looks.
        clock: Monotonic clock for the deadlines.
        boot_timeout: Seconds for the machine to run.
        ssh_timeout: Seconds for its sshd to present the chosen host key.
        poll: Seconds between looks.
    """

    def __init__(
        self,
        accounts: CloudAccounts | None = None,
        library: CloudInitLibrary | None = None,
        enroller: NodeEnroller | None = None,
        servers: CloudServers | None = None,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        boot_timeout: float = BOOT_TIMEOUT,
        ssh_timeout: float = SSH_TIMEOUT,
        poll: float = POLL_SECONDS,
    ) -> None:
        self.accounts = accounts or CloudAccounts()
        self.library = library or CloudInitLibrary()
        self.enroller = enroller or NodeEnroller()
        self.servers = servers or CloudServers()
        self._runner = runner
        self._fs = fs
        self._sleep = sleep
        self._clock = clock
        self.boot_timeout = boot_timeout
        self.ssh_timeout = ssh_timeout
        self.poll = poll

    @property
    def runner(self) -> CommandRunner:
        """The runner."""
        return self._runner or get_runner()

    # -- Checking -----------------------------------------------------------

    def validate(self, request: ProvisionRequest) -> tuple[CloudProvider, list[LibraryEntry]]:
        """
        Check everything about a request that can be checked without calling the provider.

        Args:
            request: The request.

        Returns:
            The provider's adapter and the library entries the machine boots with.

        Raises:
            ValidationError: When a field is malformed or the provider's create call
                cannot do what is asked.
            NodeError: When the account or a script is unknown, the name is taken or
                this central may not register servers yet.
        """
        validate_node_name(request.name)
        validate_access_level(request.access)
        for name, value in (
            ("location", request.location),
            ("plan", request.plan),
            ("image", request.image),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"Choose a {name}", field=name)
        for name, values in (
            ("ssh_keys", request.ssh_keys),
            ("firewalls", request.firewalls),
            ("networks", request.networks),
        ):
            if not all(isinstance(item, str) and item.strip() for item in values):
                raise ValidationError(f"{name} is a list of provider ids", field=name)
        provider = self.accounts.provider(request.account)
        entries = self.library.resolve(request.scripts)
        blockers = self.enroller.manager.registration_blockers()
        if blockers:
            raise NodeError(
                "This central may not register servers yet", details="\n".join(blockers)
            )
        if self.enroller.manager.store.get_node(request.name) is not None:
            raise NodeError(
                f"A node named {request.name} is already registered",
                details=f"Pick another name, or remove it first: noust node remove {request.name}",
                field="name",
            )
        recorded = self.servers.find(request.name)
        if recorded is not None and recorded.state != DELETED:
            raise NodeError(
                f"A machine named {request.name} was already created ({recorded.state})",
                details="Delete it first (noust fleet cloud server delete), or pick another name.",
                field="name",
            )
        # The real host key is longer than any placeholder: size the document with one of
        # the right length, so a script that would not fit is refused before anything is made.
        document = render_user_data(
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            + "A" * 400
            + "\n-----END OPENSSH PRIVATE KEY-----\n",
            "ssh-ed25519 " + "A" * 68,
            entries,
        )
        provider.check_spec(self._spec(request, document, []))
        return provider, entries

    # -- Creating -----------------------------------------------------------

    def provision(
        self,
        request: ProvisionRequest,
        *,
        actor: str | None = None,
        job_id: str | None = None,
        log: Log = _quiet,
    ) -> NodeRecord:
        """
        Create the machine, wait for it and enroll it.

        Args:
            request: What to create.
            actor: Who asked, for the audit trail and the server's own log.
            job_id: The job this runs in, recorded with the machine.
            log: Told each step as ``(message, level)``.

        Returns:
            The registered node, reachable.

        Raises:
            NoustError: At any step, with what to do. When the machine exists by then,
                the message names it, and it stays on record.
        """
        provider, entries = self.validate(request)
        name = request.name
        audit.record(
            "fleet.provision.start",
            target=f"node:{name}",
            details={
                "provider": provider.kind,
                "account": request.account,
                "location": request.location,
                "plan": request.plan,
                "image": request.image,
                "scripts": list(request.scripts),
                "access": request.access,
                "backups": request.backups,
            },
        )
        outcome = "failure"
        details: dict[str, Any] = {"provider": provider.kind}
        key_id: str | None = None
        machine: CloudServer | None = None
        try:
            log(
                f"Generating the SSH host key {name} will present, and a login key of one use",
                "info",
            )
            host_private, host_public = generate_ephemeral_pair(
                self.runner, self._fs, comment=f"noust-host-{name}"
            )
            login_private, login_public = generate_ephemeral_pair(
                self.runner, self._fs, comment=f"noust-provision-{name}"
            )
            document = render_user_data(host_private, host_public.bare, entries)
            key_id = provider.upload_ssh_key(
                f"noust-{name}-{token_source.token_hex(3)}", login_public.line()
            )
            spec = self._spec(request, document, [key_id, *request.ssh_keys])
            log(
                f"Asking {provider.display_name} for {request.plan} in {request.location} "
                f"({request.image})",
                "info",
            )
            remote = provider.create_server(spec)
            machine = self.servers.record(
                node=name,
                account=request.account,
                provider=provider.kind,
                provider_id=remote.id,
                address=remote.address,
                location=request.location,
                plan=request.plan,
                image=request.image,
                job_id=job_id,
                created_by=actor,
            )
            details["provider_id"] = remote.id
            self.accounts.mark_used(request.account)
            log(f"{provider.display_name} created {remote.id}", "success")
            provider.after_create(remote, spec)
            running = self._wait_running(provider, remote, log)
            self.servers.update(name, state=BOOTING, address=running.address)
            address = str(running.address)
            self._wait_host_key(address, host_public.fingerprint, log)
            self.servers.update(name, state=ENROLLING)
            node = self.enroller.enroll(
                EnrollRequest(
                    name=name,
                    host=address,
                    port=22,
                    ssh_user="root",
                    host_key_fingerprint=host_public.fingerprint,
                    credential=Credential(private_key=login_private),
                    access=request.access,
                    host_access=request.host_access,
                    retire_key=login_public.bare,
                ),
                actor=actor,
                log=log,
            )
            self.servers.update(name, state=READY)
            outcome = "ok"
            details["address"] = address
            return node
        except NoustError as exc:
            details["error"] = exc.message
            if machine is not None:
                self._failed(provider, request, machine, exc, log)
                raise self._with_machine(provider, machine, exc) from exc
            raise
        finally:
            if key_id is not None:
                self._forget_login_key(provider, key_id, name, log)
            audit.record(
                "fleet.provision.finish", target=f"node:{name}", outcome=outcome, details=details
            )

    def _spec(self, request: ProvisionRequest, document: str, ssh_keys: list[str]) -> CreateSpec:
        """
        Turn a request into what the provider is asked.

        Args:
            request: The request.
            document: The cloud-init document.
            ssh_keys: Provider ids of the keys to authorize for root.

        Returns:
            The spec.
        """
        return CreateSpec(
            name=request.name,
            location=request.location,
            plan=request.plan,
            image=request.image,
            user_data=document,
            ssh_keys=ssh_keys,
            firewalls=list(request.firewalls),
            networks=list(request.networks),
            ipv4=request.ipv4,
            ipv6=request.ipv6,
            backups=request.backups,
        )

    def _wait_running(
        self, provider: CloudProvider, remote: RemoteServer, log: Log
    ) -> RemoteServer:
        """
        Wait until the provider says the machine runs and has an address.

        Args:
            provider: The adapter.
            remote: The machine as the create call returned it.
            log: Where to say what happens.

        Returns:
            The machine, running, with an address.

        Raises:
            NodeError: When it does not run in time or is being deleted.
            CloudProviderError: When the provider fails for a reason other than not
                knowing the machine yet.
        """
        started = self._clock()
        deadline = started + self.boot_timeout
        log("Waiting for it to run", "info")
        seen = remote
        while True:
            try:
                seen = provider.get_server(remote.id)
            except CloudProviderError as exc:
                if exc.status != 404 or self._clock() - started > VISIBILITY_GRACE:
                    raise
            if seen.state == "running" and seen.address:
                log(f"It runs at {seen.address}", "success")
                return seen
            if seen.state == "deleting":
                raise NodeError(
                    f"{provider.display_name} is deleting {remote.id}",
                    details="Someone deleted the machine while it was being set up.",
                )
            if self._clock() > deadline:
                raise NodeError(
                    f"{remote.id} did not run within {int(self.boot_timeout)} seconds",
                    details=f"{provider.display_name} reports it as {seen.raw_state or seen.state}. "
                    "Look at it in the provider's console.",
                )
            self._sleep(self.poll)

    def _wait_host_key(self, address: str, fingerprint: str, log: Log) -> None:
        """
        Wait until the machine's sshd presents the host key chosen for it.

        A key that is another one is not accepted and nothing is sent to it: it is
        waited out, because a machine may answer with the image's own key for a few
        seconds before cloud-init has set the chosen one, and refused at the deadline.

        Args:
            address: Where it answers.
            fingerprint: The chosen host key's ``SHA256:...``.
            log: Where to say what happens.

        Raises:
            NodeError: When it never presents the chosen key.
        """
        deadline = self._clock() + self.ssh_timeout
        log(f"Waiting for {address} to present the host key chosen for it", "info")
        last = "nothing answered on port 22"
        announced = False
        while True:
            try:
                scan = self.enroller.scan_host_key(address, 22)
            except NodeError as exc:
                last = exc.details or exc.message
            else:
                if scan.fingerprint == fingerprint:
                    log("The host key is the chosen one", "success")
                    return
                last = f"it presents {scan.fingerprint}, not {fingerprint}"
                if not announced:
                    announced = True
                    log(
                        "Another host key answers for now; waiting for cloud-init to set the chosen one",
                        "warning",
                    )
            if self._clock() > deadline:
                raise NodeError(
                    f"{address} never presented the host key chosen for it",
                    details=(
                        f"Last attempt: {last}. Either cloud-init did not run (the image may not "
                        "support it), or something other than the machine answers at that "
                        "address. Nothing was sent to it."
                    ),
                )
            self._sleep(self.poll)

    # -- Failing and cleaning -------------------------------------------------

    def _failed(
        self,
        provider: CloudProvider,
        request: ProvisionRequest,
        machine: CloudServer,
        error: NoustError,
        log: Log,
    ) -> None:
        """
        Record that a machine did not join, and delete it when that was asked.

        Args:
            provider: The adapter.
            request: The request.
            machine: The record of the machine.
            error: What failed.
            log: Where to say what happens.
        """
        self.servers.update(machine.node, state=FAILED, error=error.message)
        if not request.delete_on_failure:
            return
        log(f"Deleting {machine.provider_id} at {provider.display_name}, as asked", "warning")
        try:
            provider.delete_server(machine.provider_id)
        except CloudProviderError as exc:
            log(f"It could not be deleted: {exc.message}", "error")
            return
        self.servers.update(machine.node, state=DELETED)
        audit.record(
            "fleet.provision.delete",
            target=f"node:{machine.node}",
            details={
                "provider": provider.kind,
                "provider_id": machine.provider_id,
                "reason": "failed",
            },
        )

    def _with_machine(
        self, provider: CloudProvider, machine: CloudServer, error: NoustError
    ) -> NodeError:
        """
        Say, with the error, which machine is left and how to remove it.

        Args:
            provider: The adapter.
            machine: The machine's record.
            error: What failed.

        Returns:
            The error to raise instead.
        """
        recorded = self.servers.get(machine.node)
        kept = recorded.state != DELETED
        where = f"{provider.display_name} server {machine.provider_id}" + (
            f" at {recorded.address}" if recorded.address else ""
        )
        hint = (
            f"The machine still exists ({where}) and is billed until it is deleted: "
            f"noust fleet cloud server delete {machine.node}"
            if kept
            else f"The machine was deleted ({where})."
        )
        return NodeError(
            f"{machine.node} was created but did not join the fleet: {error.message}",
            details=f"{error.details}\n{hint}".strip(),
        )

    def _forget_login_key(self, provider: CloudProvider, key_id: str, name: str, log: Log) -> None:
        """
        Delete the provider's copy of the one-time login key.

        Args:
            provider: The adapter.
            key_id: What the provider called it.
            name: The server's name, for the message.
            log: Where to say what happens.
        """
        try:
            provider.delete_ssh_key(key_id)
        except CloudProviderError as exc:
            logger.warning("Could not delete the provisioning key %s: %s", key_id, exc.message)
            log(
                f"The one-time SSH key could not be deleted at {provider.display_name} "
                f"({exc.message}). It opens nothing, its private half is gone; delete the key "
                f"named noust-{name}-... in the provider's console.",
                "warning",
            )

    # -- The machines afterwards ---------------------------------------------

    def machine(self, node: str, *, live: bool = False) -> dict[str, Any]:
        """
        Describe a machine this central created.

        Args:
            node: The server's name on this central.
            live: Ask the provider for its state too.

        Returns:
            The record and, when asked, ``remote``: the provider's report (or ``error``).

        Raises:
            NodeError: When this central created no machine of that name.
        """
        record = self.servers.get(node)
        data = record.to_dict()
        if live and record.state != DELETED:
            try:
                data["remote"] = (
                    self.accounts.provider(record.account).get_server(record.provider_id).to_dict()
                )
            except (CloudProviderError, NodeError) as exc:
                data["remote"] = None
                data["remote_error"] = exc.message
        return data

    def link(
        self,
        node: str,
        account: str,
        provider_id: str,
        *,
        force: bool = False,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """
        Link a server that is already in the fleet to its machine at a provider.

        A server enrolled by hand has no record of the machine it runs on, so the fleet cannot
        show its power state, power it on or delete it with the server. The provider is asked
        whether the machine exists and is where the server is; nothing is changed at the provider.

        Args:
            node: The server's name on this central.
            account: The cloud account that machine belongs to.
            provider_id: The provider's id for the machine.
            force: Link even when the machine's public address is not the one the server is
                reached on.
            actor: Who asked.

        Returns:
            The machine, as :meth:`machine` describes it.

        Raises:
            NodeError: The server is not registered, is linked already, the machine belongs to
                another server, or it is not at the server's address (without ``force``).
            CloudProviderError: The provider does not know the machine.
        """
        record = self.enroller.manager.store.get_node(node)
        if record is None:
            raise NodeError(
                f"No node named {node} is registered on this central",
                details="List them with 'noust node list'.",
                field="node",
            )
        existing = self.servers.find(node)
        if existing is not None and existing.state != DELETED:
            raise NodeError(
                f"{node} is already linked to {existing.provider} machine {existing.provider_id}",
                details=f"Unlink it first to link another: noust fleet cloud server unlink {node}",
                field="node",
            )
        held = self.servers.holder_of(account, provider_id)
        if held is not None:
            raise NodeError(
                f"Machine {provider_id} is already linked to {held.node}",
                details="A machine is one server's.",
                field="provider_id",
            )
        provider = self.accounts.provider(account)
        remote = provider.get_server(provider_id)
        addresses = {address for address in (remote.ipv4, remote.ipv6) if address}
        if (
            not force
            and addresses
            and _is_address(record.ssh_host)
            and record.ssh_host not in addresses
        ):
            raise NodeError(
                f"{provider.display_name} machine {provider_id} is at {remote.address}, and "
                f"{node} is reached at {record.ssh_host}",
                details="That is probably another machine. Check its id in the provider's console, "
                "or link it anyway if the server answers on another address (force).",
                field="provider_id",
            )
        self.servers.record(
            node=node,
            account=account,
            provider=self.accounts.get(account).provider,
            provider_id=provider_id,
            location=None,
            plan=None,
            image=None,
            address=remote.address,
            created_by=actor,
        )
        self.servers.update(node, state=READY, address=remote.address)
        self.accounts.mark_used(account)
        audit.record(
            "fleet.provision.link",
            target=f"node:{node}",
            details={"provider_id": provider_id, "account": account, "by": actor},
        )
        return self.machine(node)

    def detach(self, node: str, *, actor: str | None = None) -> None:
        """
        Forget which machine a server runs on. The machine is not touched.

        Args:
            node: The server's name on this central.
            actor: Who asked.

        Raises:
            NodeError: When no machine is on record for it.
        """
        record = self.servers.get(node)
        self.servers.forget(node)
        audit.record(
            "fleet.provision.unlink",
            target=f"node:{node}",
            details={"provider_id": record.provider_id, "by": actor},
        )

    def power_on(self, node: str, *, actor: str | None = None) -> None:
        """
        Start a machine that is off.

        Args:
            node: The server's name on this central.
            actor: Who asked.

        Raises:
            NodeError: When this central created no such machine, or it was deleted.
            CloudProviderError: When the provider refuses.
        """
        record = self.servers.get(node)
        if record.state == DELETED:
            raise NodeError(f"{node} was deleted", field="node")
        self.accounts.provider(record.account).power_on(record.provider_id)
        audit.record(
            "fleet.provision.power",
            target=f"node:{node}",
            details={"provider": record.provider, "provider_id": record.provider_id, "by": actor},
        )

    def delete_machine(
        self, node: str, *, remove_node: bool = False, actor: str | None = None
    ) -> list[str]:
        """
        Delete a machine at its provider, which stops the charge for it.

        Callers ask the operator first: this destroys the machine and everything on it.

        Args:
            node: The server's name on this central.
            remove_node: Also remove it from the fleet (its token cannot be revoked,
                the machine being gone).
            actor: Who asked.

        Returns:
            Warnings for the operator.

        Raises:
            NodeError: When this central created no such machine, or it was already deleted.
            CloudProviderError: When the provider refuses (a machine it no longer knows is
                taken as already gone).
        """
        record = self.servers.get(node)
        if record.state == DELETED:
            raise NodeError(f"{node} was already deleted", field="node")
        provider = self.accounts.provider(record.account)
        warnings: list[str] = []
        try:
            provider.delete_server(record.provider_id)
        except CloudProviderError as exc:
            if exc.status != 404:
                raise
            warnings.append(f"{provider.display_name} no longer knew {record.provider_id}.")
        self.servers.update(node, state=DELETED)
        audit.record(
            "fleet.provision.delete",
            target=f"node:{node}",
            details={"provider": record.provider, "provider_id": record.provider_id, "by": actor},
        )
        manager = self.enroller.manager
        if manager.store.get_node(node) is not None:
            if remove_node:
                warnings += manager.remove(node, revoke=False, actor=actor)
            else:
                warnings.append(
                    f"{node} is still registered in the fleet, and unreachable now: "
                    f"noust node remove {node}"
                )
        return warnings


def _is_address(host: str) -> bool:
    """
    Tell whether a host is an IP address rather than a name.

    Args:
        host: What a server is reached on.

    Returns:
        True for an IPv4 or IPv6 address.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
