# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an instance's container is started with, changed after it was made (4.0).

An instance is started with the command Noust works out for its engine: the program, the memory
share it sizes the engine to, the TLS arguments when it serves them
(:func:`~noust.managers.database.instance_compose.engine_command`). An operator may add arguments
after those (``-c max_connections=200``), or write the whole command instead. Both are columns of
the instance's row, so the Compose file is rendered from them like from everything else, and
changing them restarts the engine behind the readiness gate every change of an instance passes
(:class:`~noust.managers.database.instance_gate.RestartGate`): when it does not come up on the new
words, the previous ones are put back and the error carries the container's own log.

**What an operator accepts.** Words Noust did not choose are not words it tested. Setting them asks
for that to be accepted, with the price said
(:func:`~noust.managers.database.managed.untested_warnings`); taking them back asks for nothing.
A command is the whole command, so it replaces arguments and arguments replace it, and it is
refused for an instance that serves TLS, whose arguments Noust cannot add to a command it does not
write. Neither can carry a password: a command line is visible to every local user.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from noust.core.db_instance_store import READY, InstanceRecord, check_launch
from noust.core.exceptions import ConfirmationRequired, ValidationError
from noust.managers.database.instance_compose import engine_command, tls_supported
from noust.managers.database.instance_gate import RestartGate
from noust.managers.database.instance_records import key_of
from noust.managers.database.managed import ManagedInstances, untested_warnings

__all__ = ["InstanceLaunch", "LaunchPlan"]


@dataclass
class LaunchPlan:
    """
    What a change of the container's command would do.

    Attributes:
        name: The instance.
        changes: One entry per setting that changes: ``setting`` (``command`` or ``args``),
            ``from`` and ``to``.
        effective: The command the container would be started with afterwards, or empty when the
            image's own is left (an engine whose command Noust does not build).
        restarts: Whether the engine restarts (it does whenever anything changes).
        requires_confirmation: The new words are the operator's, and Noust did not test them.
        untested: What the operator accepts by going ahead with them (the warnings that say so).
        apps: The applications that use its databases, which lose their connections for the
            length of the restart.
        blockers: Why it cannot be done.
        warnings: What to know first.
    """

    name: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    effective: list[str] = field(default_factory=list)
    restarts: bool = False
    requires_confirmation: bool = False
    untested: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def require_accepted(self, accepted: bool) -> None:
        """
        Refuse a change the operator has not accepted the price of.

        The one place the question is asked, for the change and for the API's check before it
        queues one.

        Args:
            accepted: The operator accepted that Noust did not test their words.

        Raises:
            ConfirmationRequired: The words are theirs and they did not accept; it carries what
                they accept.
        """
        if self.requires_confirmation and not accepted:
            raise ConfirmationRequired(self.untested, field="accept_untested")

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "changes": [dict(change) for change in self.changes],
            "effective": list(self.effective),
            "restarts": self.restarts,
            "requires_confirmation": self.requires_confirmation,
            "apps": list(self.apps),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def effective_command(record: InstanceRecord) -> list[str]:
    """
    Say what the container of a row is started with.

    Args:
        record: The instance's row.

    Returns:
        The operator's command if they wrote one; otherwise the engine's, with the operator's
        arguments last; empty when the image's own command is left alone.
    """
    if record.command:
        return list(record.command)
    return (
        engine_command(
            record.flavour, record.memory_limit_mb, tls=record.tls_mode, extra=record.args
        )
        or []
    )


class InstanceLaunch:
    """
    Change what an instance's container is started with.

    Args:
        managed: The manager of instances, whose files, unit and readiness gate this reuses.
    """

    EVENT = "db.instance.launch"

    def __init__(self, managed: ManagedInstances) -> None:
        self.managed = managed

    # --------------------------------------------------------------- questions

    def status(self, name: str) -> dict[str, Any]:
        """
        Say what an instance is started with.

        Args:
            name: The instance.

        Returns:
            ``name``, ``key``, ``command`` and ``args`` (the operator's), ``customised`` and
            ``effective`` (what the container runs).

        Raises:
            DatabaseNotFoundError: No such instance.
        """
        record = self.managed.get(name)
        return {
            "name": name,
            "key": key_of(record),
            "command": list(record.command),
            "args": list(record.args),
            "customised": record.customised,
            "custom_image": record.custom_image,
            "effective": effective_command(record),
        }

    # ---------------------------------------------------------------- planning

    @staticmethod
    def _wanted(
        record: InstanceRecord,
        command: object,
        args: object,
        reset: bool,
    ) -> InstanceRecord:
        """
        Build the row a change would leave.

        Args:
            record: The instance as it is.
            command: The whole command; None to leave it.
            args: The arguments; None to leave them.
            reset: Take both back to what Noust passes.

        Returns:
            The row.

        Raises:
            ValidationError: Nothing is asked, more than one thing is, or a word is not valid.
        """
        asked = [command is not None, args is not None, reset]
        if sum(asked) != 1:
            raise ValidationError(
                "Say one thing: a command, arguments, or to take them back",
                details=(
                    "--command writes the container's whole command, --args adds arguments after "
                    "the ones Noust passes the engine, --reset takes both back."
                ),
                field="args",
            )
        if reset:
            return replace(record, command=(), args=())
        words, extra = check_launch(command, args)
        return replace(record, command=words, args=extra)

    def plan(
        self,
        name: str,
        *,
        command: object = None,
        args: object = None,
        reset: bool = False,
    ) -> LaunchPlan:
        """
        Decide what a change would do, changing nothing.

        Args:
            name: The instance.
            command: The whole command the container runs; None to leave it.
            args: Arguments added after the ones Noust passes; None to leave them.
            reset: Take the operator's command and arguments back.

        Returns:
            The plan; ``blockers`` says whether it can go ahead.

        Raises:
            ValidationError: Nothing or more than one thing is asked, or a word is not valid.
            DatabaseNotFoundError: No such instance.
        """
        record = self.managed.get(name)
        wanted = self._wanted(record, command, args, reset)
        plan = LaunchPlan(name=name, effective=effective_command(wanted))
        for setting, before, after in (
            ("command", record.command, wanted.command),
            ("args", record.args, wanted.args),
        ):
            if before != after:
                plan.changes.append({"setting": setting, "from": list(before), "to": list(after)})
        plan.restarts = bool(plan.changes)
        plan.requires_confirmation = bool(wanted.command or wanted.args) and bool(plan.changes)
        if not plan.changes:
            plan.warnings.append(f"{name} is already started with these: nothing changes")
        if record.state != READY:
            plan.blockers.append(f"{name} is not ready: it is {record.state}")
        if wanted.command:
            reason = tls_supported(
                record.flavour, record.version, record.tls_mode, custom_command=True
            )
            if reason is not None:
                plan.blockers.append(reason)
        if plan.requires_confirmation:
            plan.untested = untested_warnings(
                image=False, command=bool(wanted.command), args=bool(wanted.args)
            )
            plan.warnings += plan.untested
        service = self.managed.service
        domains = set()
        for link in service.records.links(engine=key_of(record)):
            app = service.store.get_app_by_id(link.app_id)
            if app is not None:
                domains.add(app.domain)
        plan.apps = sorted(domains)
        if plan.restarts:
            plan.warnings.append(
                f"{name} restarts: its connections drop for a few seconds"
                + (f", {', '.join(plan.apps)} among them" if plan.apps else "")
            )
        return plan

    # ---------------------------------------------------------------- applying

    def apply(
        self,
        name: str,
        *,
        command: object = None,
        args: object = None,
        reset: bool = False,
        accept_untested: bool = False,
    ) -> dict[str, Any]:
        """
        Change what an instance is started with, restarting it on the new words.

        Args:
            name: The instance.
            command: See :meth:`plan`.
            args: See :meth:`plan`.
            reset: See :meth:`plan`.
            accept_untested: The operator accepts that Noust did not test words it did not
                choose; not needed to take them back.

        Returns:
            The instance's command afterwards, as :meth:`status` says.

        Raises:
            ValidationError: A word is not valid, or the instance cannot be changed now.
            ConfirmationRequired: Words of the operator's, without ``accept_untested``.
            DatabaseEngineError: The engine did not come up on them; the previous ones are
                back, or the error says they could not be.
        """
        plan = self.plan(name, command=command, args=args, reset=reset)
        if plan.blockers:
            raise ValidationError(
                f"{name} cannot be changed now",
                details="\n".join(f"- {blocker}" for blocker in plan.blockers),
                field="state",
            )
        if not plan.changes:
            return self.status(name)
        plan.require_accepted(accept_untested)
        record = self.managed.get(name)
        wanted = self._wanted(record, command, args, reset)
        RestartGate(self.managed, self._write, self.EVENT).run(record, wanted)
        self.managed.service.audit(
            self.EVENT,
            name,
            changed=",".join(change["setting"] for change in plan.changes),
            reset=reset,
        )
        return self.status(name)

    def _write(self, candidate: InstanceRecord) -> None:
        """
        Store the command and the arguments of a row.

        Args:
            candidate: The row to keep.
        """
        self.managed.records.set_launch(
            candidate.name, command=candidate.command, args=candidate.args
        )
