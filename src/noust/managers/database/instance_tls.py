# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TLS for the engines Noust runs: a certificate of its own authority, a mode, and a way back.

An instance serves TLS when its row says so (``tls_mode``): ``optional`` has the
engine accept a client that asks for TLS and one that does not, ``required``
refuses a connection from outside the container that is not encrypted. The
certificate is signed by Noust's authority (:mod:`noust.managers.database.tls_authority`)
for the names the instance is reached by, kept in the instance's ``tls/``
directory and mounted read-only, so ``verify-full`` from an application checks
the name, and ``verify-ca`` checks the authority an application is handed.

**Changing the mode is an engine restart, behind the same kind of gate as every
other activation.** The Compose file is written from the new row, the unit
restarted, and the engine must answer and accept the superuser; when it does not,
the previous file is put back and the engine restarted on it, and the error carries
what the container said. Applications linked to the instance lose their connections
for the length of the restart, which the plan says before anything is touched.

Redis and Valkey cannot serve TLS yet (Noust's own client inside their container
does not speak it); MongoDB serves it as ``optional`` only. The refusal says why,
before anything changes.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field, replace
from typing import Any

from noust.core.db_instance_store import (
    READY,
    TLS_MODES,
    TLS_OFF,
    TLS_REQUIRED,
    UPGRADING,
    InstanceRecord,
)
from noust.core.exceptions import (
    DatabaseEngineError,
    NoustError,
    ValidationError,
)
from noust.managers.database import instance_layout as layout
from noust.managers.database.instance_compose import (
    ENGINE_GID,
    render_pg_hba,
    tls_supported,
)
from noust.managers.database.instance_records import key_of
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.tls_authority import (
    IssuedCertificate,
    TlsAuthority,
    ca_certificate_path,
)

__all__ = ["InstanceTls", "TlsPlan"]


@dataclass
class TlsPlan:
    """
    What changing an instance's TLS would do.

    Attributes:
        name: The instance.
        mode: The mode asked for.
        current: The mode it has now.
        restarts: Whether the engine restarts (it does whenever the mode changes).
        apps: The applications linked to its databases, which lose their
            connections for the length of the restart.
        names: The names the certificate will carry.
        blockers: Why it cannot be done.
        warnings: What to know first.
    """

    name: str
    mode: str
    current: str
    restarts: bool = True
    apps: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "mode": self.mode,
            "current": self.current,
            "restarts": self.restarts,
            "apps": list(self.apps),
            "names": list(self.names),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


class InstanceTls:
    """
    Turn TLS on, off or over for the instances Noust runs.

    Args:
        managed: The manager of instances, whose files, unit and readiness gate
            this reuses.
        authority: Signs the certificates; the server's own by default.
    """

    def __init__(self, managed: ManagedInstances, authority: TlsAuthority | None = None) -> None:
        self.managed = managed
        self._authority = authority

    @property
    def authority(self) -> TlsAuthority:
        """The authority that signs the certificates."""
        if self._authority is None:
            self._authority = TlsAuthority(runner=self.managed._runner, fs=self.managed._fs)
        return self._authority

    @property
    def logger(self) -> Any:
        """Where progress is reported."""
        return self.managed.logger

    # --------------------------------------------------------------- questions

    def names_for(self, record: InstanceRecord) -> list[str]:
        """
        List the names an instance is reached by, which its certificate carries.

        Args:
            record: The instance's row.

        Returns:
            The loopback names, the instance's own and its container's, and the
            server's host name.
        """
        names = ["localhost", "127.0.0.1", "::1", record.name, layout.container_name(record.name)]
        host = socket.gethostname()
        if host and host not in names:
            names.append(host)
        return names

    def status(self, name: str) -> dict[str, Any]:
        """
        Say how an instance serves TLS.

        Args:
            name: The instance.

        Returns:
            ``mode``, ``certificate`` (its subject, names, expiry and fingerprint, or
            None), ``ca`` (the authority's public certificate: its path and
            fingerprint) and ``supported`` (each mode with the reason it cannot be
            had, or None).

        Raises:
            DatabaseNotFoundError: No such instance.
            DatabaseEngineError: The certificate cannot be read.
        """
        record = self.managed.get(name)
        certificate: dict[str, Any] | None = None
        authority: dict[str, Any] | None = None
        if record.tls_mode != TLS_OFF:
            certificate = self.authority.inspect(
                layout.tls_directory(name) / "server.crt"
            ).to_dict()
            authority = self.authority.inspect(ca_certificate_path()).to_dict()
            authority["path"] = str(ca_certificate_path())
        return {
            "name": name,
            "key": key_of(record),
            "mode": record.tls_mode,
            "certificate": certificate,
            "ca": authority,
            "supported": {
                mode: tls_supported(
                    record.flavour, record.version, mode, custom_command=bool(record.command)
                )
                for mode in TLS_MODES
            },
        }

    def plan(self, name: str, mode: str) -> TlsPlan:
        """
        Decide what a mode would do, changing nothing.

        Args:
            name: The instance.
            mode: ``off``, ``optional`` or ``required``.

        Returns:
            The plan; ``blockers`` says whether it can go ahead.

        Raises:
            ValidationError: ``mode`` is not one.
            DatabaseNotFoundError: No such instance.
        """
        if mode not in TLS_MODES:
            raise ValidationError(
                f"{mode!r} is not a TLS mode",
                details=f"The modes are: {', '.join(TLS_MODES)}.",
                field="mode",
            )
        record = self.managed.get(name)
        plan = TlsPlan(name=name, mode=mode, current=record.tls_mode)
        if record.state != READY:
            plan.blockers.append(f"{name} is not ready: it is {record.state}")
        reason = tls_supported(
            record.flavour, record.version, mode, custom_command=bool(record.command)
        )
        if reason is not None:
            plan.blockers.append(reason)
        if mode == record.tls_mode:
            plan.restarts = False
            plan.warnings.append(f"{name} already serves TLS as {mode}: nothing changes")
        if mode != TLS_OFF:
            plan.names = self.names_for(record)
        key = key_of(record)
        service = self.managed.service
        links = service.records.links(engine=key)
        domains = set()
        for link in links:
            app = service.store.get_app_by_id(link.app_id)
            if app is not None:
                domains.add(app.domain)
        plan.apps = sorted(domains)
        if plan.restarts:
            plan.warnings.append(
                f"{name} restarts: its connections drop for a few seconds"
                + (f", {', '.join(plan.apps)} among them" if plan.apps else "")
            )
        if mode == TLS_REQUIRED:
            plan.warnings.append(
                "A client outside the container that does not use TLS is refused. Link each "
                "application with --tls verify-full first, or it stops connecting."
            )
        return plan

    # ----------------------------------------------------------------- change

    def set_mode(self, name: str, mode: str) -> dict[str, Any]:
        """
        Make an instance serve TLS in a mode, or stop.

        Args:
            name: The instance.
            mode: ``off``, ``optional`` or ``required``.

        Returns:
            The instance's TLS, as :meth:`status` says.

        Raises:
            ValidationError: The mode is not one, or the engine cannot serve it.
            DatabaseEngineError: The instance is being changed; or the engine did
                not come up with the new mode, in which case the previous
                configuration is back and the error carries the container's log.
        """
        plan = self.plan(name, mode)
        if plan.blockers:
            raise ValidationError(
                f"{name} cannot serve TLS as {mode}",
                details="\n".join(f"- {blocker}" for blocker in plan.blockers),
                field="mode",
            )
        record = self.managed.get(name)
        if mode == record.tls_mode:
            return self.status(name)
        issued: IssuedCertificate | None = None
        if mode != TLS_OFF:
            issued = self.authority.issue(name, plan.names)
        self._apply(record, mode, issued)
        self.managed.service.audit("db.instance.tls", name, mode=mode, previous=record.tls_mode)
        return self.status(name)

    def renew(self, name: str) -> dict[str, Any]:
        """
        Sign the instance's certificate again and restart it on the new one.

        Args:
            name: The instance.

        Returns:
            The instance's TLS, as :meth:`status` says.

        Raises:
            DatabaseEngineError: The instance does not serve TLS, or did not come
                up on the new certificate (the previous one is back).
        """
        record = self.managed.get(name)
        self.managed._require_ready(record, "renew")
        if record.tls_mode == TLS_OFF:
            raise DatabaseEngineError(
                f"{name} does not serve TLS, so it has no certificate to renew",
                details=f"Turn it on first: noust db instance tls {name} --mode optional",
            )
        issued = self.authority.issue(name, self.names_for(record))
        self._apply(record, record.tls_mode, issued)
        self.managed.service.audit("db.instance.tls", name, mode=record.tls_mode, renewed=True)
        return self.status(name)

    def _write_files(self, record: InstanceRecord, mode: str, issued: IssuedCertificate) -> None:
        """
        Write the certificate, the key and the authority where the container reads them.

        Args:
            record: The instance.
            mode: The mode they are for.
            issued: What the authority signed.
        """
        fs = self.managed.fs
        directory = layout.tls_directory(record.name)
        fs.make_dir(directory, mode=0o755)
        # The engine's account is not root: the key is root's, readable by its group and no
        # other, which is what PostgreSQL accepts of a key it does not own.
        owner = (0, ENGINE_GID)
        fs.write_text(directory / "server.key", issued.key, mode=0o640, owner=owner)
        fs.write_text(directory / "server.crt", issued.certificate, mode=0o644)
        fs.write_text(directory / "ca.crt", issued.ca, mode=0o644)
        if record.flavour == "mongodb":
            fs.write_text(
                directory / "server.pem", issued.key + issued.certificate, mode=0o640, owner=owner
            )
        if record.flavour == "postgresql" and mode == TLS_REQUIRED:
            fs.write_text(directory / "pg_hba.conf", render_pg_hba(mode), mode=0o644)

    def _apply(self, record: InstanceRecord, mode: str, issued: IssuedCertificate | None) -> None:
        """
        Write the new configuration, restart the engine on it, and undo it when it does not answer.

        Args:
            record: The instance as it is.
            mode: The mode to go to.
            issued: The certificate, for any mode but ``off``.

        Raises:
            DatabaseEngineError: The engine did not come up; the previous
                configuration is back, or the error says it could not be.
        """
        managed = self.managed
        name = record.name
        unit = layout.unit_name(name)
        candidate = replace(record, tls_mode=mode, state=UPGRADING)
        managed.records.set_state(name, UPGRADING)
        try:
            if issued is not None:
                self._write_files(record, mode, issued)
            managed._write_compose(candidate)
            managed.units().restart(unit)
            managed._wait_ready(candidate)
        except (NoustError, OSError) as exc:
            raise self._undo(record, exc) from exc
        managed.records.set_tls_mode(name, mode)
        managed.records.set_state(name, READY)
        if mode == TLS_OFF:
            managed.fs.remove_tree(layout.tls_directory(name))
        managed.service.refresh_instances()

    def _undo(self, record: InstanceRecord, cause: Exception) -> DatabaseEngineError:
        """
        Put the previous configuration back after an engine that did not come up.

        Args:
            record: The instance as it was.
            cause: What failed.

        Returns:
            The error to raise.
        """
        managed = self.managed
        message = getattr(cause, "message", str(cause))
        output = getattr(cause, "output", None)
        try:
            managed._write_compose(record)
            managed.units().restart(layout.unit_name(record.name))
            managed._wait_ready(record)
        except (NoustError, OSError) as back:
            managed.service.audit("db.instance.tls", record.name, "failure")
            return DatabaseEngineError(
                f"The TLS change of {record.name} failed and its previous configuration could "
                "not be put back",
                details=(
                    f"{message}\nPutting it back failed too: {getattr(back, 'message', str(back))}. "
                    f"Its row says 'upgrading'; its Compose file is "
                    f"{layout.compose_path(record.name)}: fix the cause, then "
                    f"'noust db instance start {record.name}'."
                ),
                output=output,
            )
        managed.records.set_state(record.name, READY)
        if record.tls_mode == TLS_OFF:
            # It never served TLS: the files this attempt wrote are not a half-done state to keep.
            managed.fs.remove_tree(layout.tls_directory(record.name))
        managed.service.refresh_instances()
        managed.service.audit("db.instance.tls", record.name, "failure")
        return DatabaseEngineError(
            f"{record.name} did not come up with the new TLS mode, and runs as it did before",
            details=f"{message}\nNothing of its data was changed.",
            output=output,
        )
