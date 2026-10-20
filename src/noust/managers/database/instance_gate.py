# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Change what an instance's Compose file is rendered from, and restart it behind the readiness gate.

The Compose file of an instance is rendered from its row
(:func:`~noust.managers.database.instance_compose.render_compose`), so changing what the container
is started with (its limits, its probe, its command) is a validated column and a new file, never an
edited one. Docker applies those when it creates the container, so the engine restarts, and it
restarts behind the same readiness gate a creation, an upgrade and a change of TLS pass: when the
engine does not come up on the new row, the previous row and the previous file are put back, the
engine is started on them, and the error carries the container's own log.

This is that sequence, once (rule 3). What differs between a change of limits
(:mod:`~noust.managers.database.instance_tuning`) and a change of command
(:mod:`~noust.managers.database.instance_launch`) is which columns are written, and the audit event
that says so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from noust.core.db_instance_store import READY, UPGRADING, InstanceRecord
from noust.core.exceptions import DatabaseEngineError, NoustError
from noust.managers.database import instance_layout as layout
from noust.managers.database.managed import ManagedInstances

__all__ = ["RestartGate"]


class RestartGate:
    """
    Write a row, restart the engine on it, and put the previous row back when it does not answer.

    Args:
        managed: The manager of instances, whose files, unit and readiness gate this reuses.
        write: Stores the columns this change is about, from a row.
        event: The audit event a failure is recorded as.
    """

    def __init__(
        self, managed: ManagedInstances, write: Callable[[InstanceRecord], None], event: str
    ) -> None:
        self.managed = managed
        self.write = write
        self.event = event

    def run(self, record: InstanceRecord, wanted: InstanceRecord) -> None:
        """
        Go from a row to another, undoing it when the engine does not come up.

        Args:
            record: The instance as it is.
            wanted: The row to go to.

        Raises:
            DatabaseEngineError: The engine did not come up; the previous row is back, or the
                error says it could not be.
        """
        managed = self.managed
        name = record.name
        unit = layout.unit_name(name)
        managed.records.set_state(name, UPGRADING)
        try:
            self.write(wanted)
            managed._write_compose(replace(wanted, state=UPGRADING))
            managed.units().restart(unit)
            managed._wait_ready(wanted)
        except (NoustError, OSError) as exc:
            raise self._undo(record, exc) from exc
        managed.records.set_state(name, READY)
        managed.service.refresh_instances()

    def _undo(self, record: InstanceRecord, cause: Exception) -> DatabaseEngineError:
        """
        Put the previous row back after an engine that did not come up.

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
            self.write(record)
            managed._write_compose(record)
            managed.units().restart(layout.unit_name(record.name))
            managed._wait_ready(record)
        except (NoustError, OSError) as back:
            managed.service.audit(self.event, record.name, "failure")
            return DatabaseEngineError(
                f"The change of {record.name} failed and its previous settings could not be "
                "put back",
                details=(
                    f"{message}\nPutting them back failed too: "
                    f"{getattr(back, 'message', str(back))}. Its row says 'upgrading'; its "
                    f"Compose file is {layout.compose_path(record.name)}: fix the cause, then "
                    f"'noust db instance start {record.name}'."
                ),
                output=output,
            )
        managed.records.set_state(record.name, READY)
        managed.service.refresh_instances()
        managed.service.audit(self.event, record.name, "failure")
        return DatabaseEngineError(
            f"{record.name} did not come up on the new settings, and runs as it did before",
            details=f"{message}\nNothing of its data was changed.",
            output=output,
        )
