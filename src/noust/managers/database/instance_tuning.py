# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an instance may use, and how it is asked whether it is well (4.0, C-30 and C-31).

An instance Noust runs is held to a memory limit from the day it is made. This module
changes that limit, adds the other two a container can be held to (the CPUs it may use
and the swap it may touch), and makes the health probe the container is judged by
configurable: how often it is asked, how long it may take to answer, and how many
failures in a row make it unhealthy.

**A limit is a row and a restart.** The Compose file is rendered from the instance's
row (:func:`~noust.managers.database.instance_compose.render_compose`), so a change is a
validated column and a new file, never an edited one. Docker applies a container's limits
when it is created, so the engine restarts, and it restarts behind the same readiness
gate a creation, an upgrade and a change of TLS pass: when the engine does not come up
on the new numbers (a memory limit below what its caches need, a probe that cannot
succeed in the time it is given) the previous row and the previous file are put back, the
engine is started on them, and the error carries the container's own log.

**Nothing is applied that the server could not honour.** The memory is checked against
what the other instances leave of the share instances may hold, the CPUs against the
server's, and a probe whose timeout is longer than its interval is refused: Docker would
accept it and report an engine that is well as unhealthy.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from noust.core.db_instance_store import (
    READY,
    InstanceRecord,
    check_cpus,
    check_probe,
    check_swap,
)
from noust.core.exceptions import ValidationError
from noust.managers.database import instance_layout as layout
from noust.managers.database.instance_catalog import spec_for
from noust.managers.database.instance_compose import DEFAULT_PROBE
from noust.managers.database.instance_gate import RestartGate
from noust.managers.database.instance_records import key_of
from noust.managers.database.managed import ManagedInstances
from noust.validators.db_instance import check_memory, parse_memory

__all__ = ["CLEARABLE", "InstanceTuning", "TuningPlan"]

#: What can be put back to its default.
CLEARABLE: tuple[str, ...] = ("cpus", "swap", "interval", "timeout", "retries")


@dataclass
class TuningPlan:
    """
    What a change of limits or of the probe would do.

    Attributes:
        name: The instance.
        changes: One entry per setting that changes: ``setting``, ``from`` and ``to``.
        values: What the instance would have afterwards, as :meth:`InstanceTuning.status` names it.
        restarts: Whether the engine restarts (it does whenever anything changes).
        apps: The applications that use its databases, which lose their connections
            for the length of the restart.
        blockers: Why it cannot be done.
        warnings: What to know first.
    """

    name: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)
    restarts: bool = False
    apps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "changes": [dict(change) for change in self.changes],
            "values": dict(self.values),
            "restarts": self.restarts,
            "apps": list(self.apps),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def _values(record: InstanceRecord) -> dict[str, Any]:
    """
    Name what an instance is held to and how it is probed.

    Args:
        record: The instance's row.

    Returns:
        ``memory_mb``, ``cpus``, ``swap_mb`` (None: Docker's default), and the probe's
        ``interval_s``, ``timeout_s`` and ``retries`` as they will be applied (the
        defaults where the row says nothing).
    """
    return {
        "memory_mb": record.memory_limit_mb,
        "cpus": record.cpu_limit,
        "swap_mb": record.swap_limit_mb,
        "interval_s": record.probe_interval_s or DEFAULT_PROBE["interval_s"],
        "timeout_s": record.probe_timeout_s or DEFAULT_PROBE["timeout_s"],
        "retries": record.probe_retries or DEFAULT_PROBE["retries"],
    }


class InstanceTuning:
    """
    Change what an instance may use and how it is probed.

    Args:
        managed: The manager of instances, whose files, unit and readiness gate
            this reuses.
    """

    def __init__(self, managed: ManagedInstances) -> None:
        self.managed = managed

    # --------------------------------------------------------------- questions

    def status(self, name: str) -> dict[str, Any]:
        """
        Say what an instance is held to, how it is probed, and how it is doing.

        Args:
            name: The instance.

        Returns:
            ``name``, ``key``, ``limits`` (``memory_mb``, ``cpus``, ``swap_mb``),
            ``probe`` (``interval_s``, ``timeout_s``, ``retries``, each as applied) and
            ``defaults`` (what the probe is when the row says nothing), ``server`` (the
            memory and CPUs it may be given) and ``health`` (``status``, the failures in
            a row and the last probe's output, or ``absent`` for a container that is not
            running).

        Raises:
            DatabaseNotFoundError: No such instance.
        """
        record = self.managed.get(name)
        values = _values(record)
        budget = self.managed.budget()
        return {
            "name": name,
            "key": key_of(record),
            "limits": {
                "memory_mb": values["memory_mb"],
                "cpus": values["cpus"],
                "swap_mb": values["swap_mb"],
            },
            "probe": {
                "interval_s": values["interval_s"],
                "timeout_s": values["timeout_s"],
                "retries": values["retries"],
            },
            "defaults": dict(DEFAULT_PROBE),
            "server": {
                "memory_mb": budget["server_mb"],
                "available_mb": budget["available_mb"] + record.memory_limit_mb
                if budget["server_mb"]
                else 0,
                "cpus": self.managed._resources().cpus,
            },
            "health": self.health(record),
        }

    def health(self, record: InstanceRecord) -> dict[str, Any]:
        """
        Read what Docker's health probe says of the container now.

        Args:
            record: The instance's row.

        Returns:
            ``status`` (``healthy``, ``starting``, ``unhealthy`` or ``absent``),
            ``failing_streak`` and ``last`` (the last probe's exit code and its output,
            which an engine's own command printed and is not a secret).
        """
        result = self.managed._docker(
            "inspect",
            "--format",
            "{{json .State.Health}}",
            layout.container_name(record.name),
            timeout=30,
        )
        absent: dict[str, Any] = {"status": "absent", "failing_streak": 0, "last": None}
        if not result.success or not result.stdout.strip() or result.stdout.strip() == "null":
            return absent
        try:
            found = json.loads(result.stdout)
        except ValueError:
            return absent
        if not isinstance(found, dict):
            return absent
        log = found.get("Log")
        last = log[-1] if isinstance(log, list) and log and isinstance(log[-1], dict) else None
        return {
            "status": str(found.get("Status") or "absent"),
            "failing_streak": int(found.get("FailingStreak") or 0),
            "last": None
            if last is None
            else {
                "exit_code": last.get("ExitCode"),
                "output": str(last.get("Output") or "").strip()[:500],
            },
        }

    def plan(
        self,
        name: str,
        *,
        memory: object = None,
        cpus: object = None,
        swap: object = None,
        interval: object = None,
        timeout: object = None,
        retries: object = None,
        clear: Iterable[str] = (),
    ) -> TuningPlan:
        """
        Decide what a change would do, changing nothing.

        Args:
            name: The instance.
            memory: The new memory limit (megabytes, or ``512m``, ``2g``); None leaves it.
            cpus: The new CPU limit (``0.5``, ``2``); None leaves it.
            swap: The swap it may touch besides its memory (megabytes, ``512m``, or ``0``
                for none); None leaves it.
            interval: Seconds between probes; None leaves it.
            timeout: Seconds a probe may take; None leaves it.
            retries: Failures in a row before it is unhealthy; None leaves it.
            clear: Settings of :data:`CLEARABLE` to put back to their default.

        Returns:
            The plan; ``blockers`` says whether it can go ahead.

        Raises:
            ValidationError: A value cannot be read, is out of range, or the server
                cannot give it; ``field`` says which. Nothing was changed.
            DatabaseNotFoundError: No such instance.
        """
        record = self.managed.get(name)
        wanted = self._candidate(
            record,
            memory=memory,
            cpus=cpus,
            swap=swap,
            interval=interval,
            timeout=timeout,
            retries=retries,
            clear=tuple(clear),
        )
        before, after = _values(record), _values(wanted)
        plan = TuningPlan(name=name, values=after)
        for setting, label in (
            ("memory_mb", "memory"),
            ("cpus", "cpus"),
            ("swap_mb", "swap"),
            ("interval_s", "interval"),
            ("timeout_s", "timeout"),
            ("retries", "retries"),
        ):
            if before[setting] != after[setting]:
                plan.changes.append(
                    {"setting": label, "from": before[setting], "to": after[setting]}
                )
        plan.restarts = bool(plan.changes)
        if not plan.changes:
            plan.warnings.append(f"{name} already has these settings: nothing changes")
        if record.state != READY:
            plan.blockers.append(f"{name} is not ready: it is {record.state}")
        key = key_of(record)
        service = self.managed.service
        domains = set()
        for link in service.records.links(engine=key):
            app = service.store.get_app_by_id(link.app_id)
            if app is not None:
                domains.add(app.domain)
        plan.apps = sorted(domains)
        if plan.restarts:
            plan.warnings.append(
                f"{name} restarts: its connections drop for a few seconds"
                + (f", {', '.join(plan.apps)} among them" if plan.apps else "")
            )
        if after["memory_mb"] < before["memory_mb"]:
            plan.warnings.append(
                f"Less memory ({after['memory_mb']} MB, from {before['memory_mb']} MB): the "
                "engine's caches are sized to it again, and a database larger than they hold "
                "gets slower before it gets faster"
            )
        return plan

    def _candidate(
        self,
        record: InstanceRecord,
        *,
        memory: object,
        cpus: object,
        swap: object,
        interval: object,
        timeout: object,
        retries: object,
        clear: tuple[str, ...],
    ) -> InstanceRecord:
        """
        Build the row an instance would have after a change, validating every value.

        Args:
            record: The instance as it is.
            memory: See :meth:`plan`.
            cpus: See :meth:`plan`.
            swap: See :meth:`plan`.
            interval: See :meth:`plan`.
            timeout: See :meth:`plan`.
            retries: See :meth:`plan`.
            clear: See :meth:`plan`.

        Returns:
            The row, changed.

        Raises:
            ValidationError: A value is not acceptable.
        """
        unknown = [item for item in clear if item not in CLEARABLE]
        if unknown:
            raise ValidationError(
                f"{', '.join(unknown)} cannot be put back to a default",
                details=f"These can: {', '.join(CLEARABLE)}.",
                field="clear",
            )
        given = {
            "cpus": cpus,
            "swap": swap,
            "interval": interval,
            "timeout": timeout,
            "retries": retries,
        }
        both = [item for item in clear if given.get(item) is not None]
        if both:
            raise ValidationError(
                f"{', '.join(both)} cannot be both set and put back to its default",
                details="Give a value or ask for the default, not both.",
                field="clear",
            )
        spec = spec_for(record.flavour)
        changes: dict[str, Any] = {}
        if memory is not None:
            ceiling = self.managed._memory_ceiling() + record.memory_limit_mb
            changes["memory_limit_mb"] = check_memory(
                memory, minimum=spec.min_memory_mb, maximum=ceiling
            )
        if cpus is not None:
            number = check_cpus(cpus)
            here = self.managed._resources().cpus
            if here and number > here:
                # Docker refuses it too, with a message about its own range.
                raise ValidationError(
                    f"{number:g} CPUs is more than this server has ({here})",
                    details=f"Use a number up to {here}, or clear the limit to use them all.",
                    field="cpus",
                )
            changes["cpu_limit"] = number
        if "cpus" in clear:
            changes["cpu_limit"] = None
        if swap is not None:
            text = str(swap).strip().lower()
            changes["swap_limit_mb"] = (
                0 if text in ("0", "0m", "0g", "none") else check_swap(parse_memory(swap))
            )
        if "swap" in clear:
            changes["swap_limit_mb"] = None
        probe = {
            "probe_interval_s": record.probe_interval_s,
            "probe_timeout_s": record.probe_timeout_s,
            "probe_retries": record.probe_retries,
        }
        for key, value, label in (
            ("probe_interval_s", interval, "interval"),
            ("probe_timeout_s", timeout, "timeout"),
            ("probe_retries", retries, "retries"),
        ):
            if value is not None:
                probe[key] = self._whole(value, label)
            if label in clear:
                probe[key] = None
        # The row's own checks, so a plan refuses what an apply would.
        check_probe(
            interval_s=probe["probe_interval_s"],
            timeout_s=probe["probe_timeout_s"],
            retries=probe["probe_retries"],
        )
        effective_interval = probe["probe_interval_s"] or DEFAULT_PROBE["interval_s"]
        effective_timeout = probe["probe_timeout_s"] or DEFAULT_PROBE["timeout_s"]
        if effective_timeout > effective_interval:
            raise ValidationError(
                "A probe cannot take longer than the time between probes",
                details=(
                    f"The timeout would be {effective_timeout} s and the interval "
                    f"{effective_interval} s: Docker would call a healthy engine unhealthy."
                ),
                field="timeout",
            )
        return replace(
            record,
            **changes,
            probe_interval_s=probe["probe_interval_s"],
            probe_timeout_s=probe["probe_timeout_s"],
            probe_retries=probe["probe_retries"],
        )

    @staticmethod
    def _whole(value: object, label: str) -> int:
        """
        Read a whole number of seconds or tries.

        Args:
            value: What was given.
            label: The field the error names.

        Returns:
            The number.

        Raises:
            ValidationError: Not a whole number.
        """
        try:
            if isinstance(value, bool):
                raise ValueError
            return int(str(value).strip())
        except ValueError:
            raise ValidationError(
                f"{value!r} is not a whole number",
                details="Give a whole number, such as 15.",
                field=label,
            ) from None

    # ----------------------------------------------------------------- change

    def apply(
        self,
        name: str,
        *,
        memory: object = None,
        cpus: object = None,
        swap: object = None,
        interval: object = None,
        timeout: object = None,
        retries: object = None,
        clear: Iterable[str] = (),
    ) -> dict[str, Any]:
        """
        Change what an instance may use or how it is probed, restarting it on the new numbers.

        Args:
            name: The instance.
            memory: See :meth:`plan`.
            cpus: See :meth:`plan`.
            swap: See :meth:`plan`.
            interval: See :meth:`plan`.
            timeout: See :meth:`plan`.
            retries: See :meth:`plan`.
            clear: See :meth:`plan`.

        Returns:
            The instance's settings afterwards, as :meth:`status` says.

        Raises:
            ValidationError: A value is not acceptable, or the instance is not ready.
            DatabaseEngineError: The engine did not come up on the new numbers; the
                previous ones are back, or the error says they could not be.
        """
        plan = self.plan(
            name,
            memory=memory,
            cpus=cpus,
            swap=swap,
            interval=interval,
            timeout=timeout,
            retries=retries,
            clear=clear,
        )
        if plan.blockers:
            raise ValidationError(
                f"{name} cannot be changed now",
                details="\n".join(f"- {blocker}" for blocker in plan.blockers),
                field="state",
            )
        if not plan.changes:
            return self.status(name)
        record = self.managed.get(name)
        wanted = self._candidate(
            record,
            memory=memory,
            cpus=cpus,
            swap=swap,
            interval=interval,
            timeout=timeout,
            retries=retries,
            clear=tuple(clear),
        )
        self._restart_on(record, wanted)
        self.managed.service.audit(
            "db.instance.tune",
            name,
            changed=",".join(change["setting"] for change in plan.changes),
        )
        return self.status(name)

    def _write(self, candidate: InstanceRecord) -> None:
        """
        Store the limits and the probe of a row.

        Args:
            candidate: The row to keep.
        """
        records = self.managed.records
        name = candidate.name
        records.set_memory_limit(name, candidate.memory_limit_mb)
        records.set_cpu_limit(name, candidate.cpu_limit)
        records.set_swap_limit(name, candidate.swap_limit_mb)
        records.set_probe(
            name,
            interval_s=candidate.probe_interval_s,
            timeout_s=candidate.probe_timeout_s,
            retries=candidate.probe_retries,
        )

    def _restart_on(self, record: InstanceRecord, wanted: InstanceRecord) -> None:
        """
        Write the new Compose file, restart the engine on it, and undo it when it does not answer.

        Args:
            record: The instance as it is.
            wanted: The row to go to.

        Raises:
            DatabaseEngineError: The engine did not come up; the previous settings are
                back, or the error says they could not be.
        """
        RestartGate(self.managed, self._write, "db.instance.tune").run(record, wanted)
