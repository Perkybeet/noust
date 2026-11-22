# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What needs an operator, as one list the server joins and orders (item 161).

There used to be three: the bell listed what the Overview collected and what the console added to it,
the Server page listed the machine's own facts and the open findings of Security were a count of their
own. An operator was told 2, 8 and 10 on the same afternoon and could not tell which was true. This
module is the one place that holds how a row is made, which source it comes from and how the sources
are ranked, so the console draws a list and does not join one:

- a **state**: an application that failed, a certificate that is not renewed, a unit that is down, the
  machine's own facts (a disk, a reboot, updates), a request waiting for a decision, a newer Noust;
- an **alert**: a rule that is firing, a probe that is down, what the monitor noted;
- a **security** finding: a check of the hardening that failed or warned.

A row is one subject and carries every reason it has, each saying its own origin. The sources are read
elsewhere (:mod:`noust.managers.overview` for the server's own, the console's route for what is a
person's); what they hand over here is plain data, so every rule below is tested without a machine.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from noust.core.compose_acceptance import PENDING_ATTENTION_DAYS, GateNote
from noust.core.docker.events import LOOP_MINUTES
from noust.core.docker.summary import LOOP_AT
from noust.core.server_text import ServerText, say
from noust.core.timeutil import to_iso_offset

#: Most rows the list returns; the total still counts every one.
ATTENTION_LIMIT = 100

#: How serious a reason is, worst first.
RANK = {"fail": 0, "warn": 1}

#: Where a row comes from, in the order rows of the same severity are grouped.
ORIGINS = ("state", "alert", "security")

#: The kinds of reason that are not a state: a state is what a thing is, the rest are what a rule, a
#: probe, the monitor or a check of the hardening said of it.
_ORIGIN_OF_KIND = {
    "alert": "alert",
    "probe": "alert",
    "monitor": "alert",
    "security": "security",
}

#: The checks of the hardening whose finding the machine's facts already say, with their numbers.
COVERED_BY_FACTS = frozenset(
    {
        "upd.security_pending",
        "upd.pkg_broken",
        "upd.reboot_required",
        "os.eol",
        "time.unsynced",
        "mem.no_swap",
        "disk.full",
        "sys.degraded",
    }
)


def origin_of(kind: str) -> str:
    """
    Args:
        kind: The kind of a reason (``state``, ``deploy``, ``alert``, ``security``...).

    Returns:
        ``alert``, ``security`` or ``state``: a kind nobody ruled on is a state, never a hole.
    """
    return _ORIGIN_OF_KIND.get(kind, "state")


def reason(
    kind: str,
    severity: str,
    code: str,
    *,
    params: Mapping[str, Any] | None = None,
    detail: str | None = None,
    detail_coded: Mapping[str, Any] | None = None,
    when: str | None = None,
    deployment_id: int | None = None,
    actions: Sequence[str] | None = None,
) -> dict[str, Any]:
    """
    Build one reason a row needs attention.

    Args:
        kind: Where it comes from: ``state``, ``deploy``, ``first_deploy``, ``certificate``,
            ``unit``, ``machine``, ``approval``, ``noust``, ``recipe``, ``monitor``, ``alert``,
            ``probe`` or ``security``.
        severity: ``fail`` or ``warn``.
        code: Machine-readable and stable; the console words it.
        params: Values for the console's sentence.
        detail: The system's own words, verbatim.
        detail_coded: The detail as a sentence of Noust's (``noust.core.server_text``), when it is
            one and not another program's words.
        when: When it happened, with its UTC offset.
        deployment_id: A deployment to open for the full story.
        actions: What the operator can do about it, as identifiers the console maps to buttons.

    Returns:
        The reason, with the ``origin`` the kind belongs to.
    """
    return {
        "kind": kind,
        "origin": origin_of(kind),
        "severity": severity,
        "code": code,
        "params": dict(params or {}),
        "detail": detail,
        "detail_coded": dict(detail_coded) if detail_coded is not None else None,
        "when": when,
        "deployment_id": deployment_id,
        "actions": list(actions or []),
    }


class AttentionList:
    """The rows that need an operator, joined by subject, ranked and counted once."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def add(self, key: str, subject: Mapping[str, Any], title: str, why: Mapping[str, Any]) -> None:
        """
        Add a reason to the row of a subject, making the row when there is none.

        Args:
            key: The row's stable identifier (``app:shop.example.com``).
            subject: What it is about (``{"kind": "app", "domain": ...}``).
            title: A domain, a unit, a process, a rule: what the row is called.
            why: The reason, from :func:`reason`.
        """
        row = self._rows.get(key)
        if row is None:
            self._rows[key] = {
                "id": key,
                "subject": dict(subject),
                "title": title,
                "severity": why["severity"],
                "reasons": [dict(why)],
            }
            return
        row["reasons"].append(dict(why))
        if RANK[why["severity"]] < RANK[row["severity"]]:
            row["severity"] = why["severity"]

    def extend(self, items: Iterable[Mapping[str, Any]]) -> None:
        """
        Add whole rows another source built, joining the ones that name a subject already here.

        Args:
            items: Rows as :func:`machine_items` and the others return them.
        """
        for item in items:
            for each in item["reasons"]:
                self.add(item["id"], item["subject"], item["title"], each)

    def result(self, limit: int = ATTENTION_LIMIT) -> tuple[list[dict[str, Any]], int]:
        """
        Rank the rows and say where each comes from.

        The worst first; within a severity the states, then the alerts, then the findings, so rows
        with the same label sit together; then by name.

        Args:
            limit: The most rows to return.

        Returns:
            The rows, at most ``limit``, and how many there were.
        """
        rows = []
        for row in self._rows.values():
            worst = next(each for each in row["reasons"] if each["severity"] == row["severity"])
            rows.append({**row, "origin": worst["origin"]})
        rows.sort(
            key=lambda row: (
                RANK[row["severity"]],
                ORIGINS.index(row["origin"]),
                row["title"].lower(),
            )
        )
        return rows[:limit], len(rows)


def _row(
    key: str, subject: Mapping[str, Any], title: str, why: Mapping[str, Any]
) -> dict[str, Any]:
    """
    Args:
        key: The row's identifier.
        subject: What it is about.
        title: What it is called.
        why: Its one reason.

    Returns:
        A row of one reason, as a source hands it over.
    """
    return {
        "id": key,
        "subject": dict(subject),
        "title": title,
        "severity": why["severity"],
        "reasons": [dict(why)],
        "origin": why["origin"],
    }


def _machine(fact: str, title: str, why: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        fact: Which fact of the machine (``disk``, ``reboot_required``...).
        title: What the row is called, in English: the console words it from ``fact``.
        why: Its reason.

    Returns:
        The row.
    """
    return _row(f"machine:{fact}", {"kind": "machine", "fact": fact}, title, why)


def machine_items(
    summary: Mapping[str, Any] | None, known_units: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """
    Read what is wrong with the machine from the server area's summary.

    The summary's facts are the ones a thing the machine reports with its own numbers: the pending
    updates, the end of support of the system, a disk, a reboot, the clock, the swap and the units
    that failed. A section that could not be read has no value and says nothing here.

    Args:
        summary: The summary the server area builds, or None when this Noust has none.
        known_units: The units the list already names (an application's, Noust's own), as the
            services list calls them, so a failed unit is not said twice.

    Returns:
        One row per fact that is wrong.
    """
    if not summary:
        return []
    items: list[dict[str, Any]] = []
    updates = summary.get("updates") or {}
    if updates.get("broken") is True:
        items.append(
            _machine(
                "packages_broken",
                "Packages",
                reason("machine", "fail", "packages_broken", actions=["open_updates"]),
            )
        )
    security = updates.get("security")
    if isinstance(security, int) and security > 0:
        items.append(
            _machine(
                "security_updates",
                "Security updates",
                # A warning, as the Overview's own figure says it: the server marks no update critical.
                reason(
                    "machine",
                    "warn",
                    "security_updates",
                    params={"count": security},
                    actions=["open_updates"],
                ),
            )
        )
    system = summary.get("os") or {}
    eol = system.get("eol") or {}
    name = str(system.get("name") or "")
    if eol.get("status") == "expired":
        items.append(
            _machine(
                "os_support",
                "Operating system",
                reason(
                    "machine",
                    "fail",
                    "os_unsupported",
                    params={"name": name, "date": eol.get("end_date")},
                    actions=["open_updates"],
                ),
            )
        )
    elif eol.get("status") == "warn" and eol.get("days_left") is not None:
        items.append(
            _machine(
                "os_support",
                "Operating system",
                reason(
                    "machine",
                    "warn",
                    "os_ending",
                    params={"name": name, "days": eol["days_left"], "date": eol.get("end_date")},
                    actions=["open_updates"],
                ),
            )
        )
    disk = summary.get("disk") or {}
    if (
        disk.get("status") in ("critical", "warn")
        and disk.get("worst_mount") is not None
        and disk.get("worst_percent") is not None
    ):
        items.append(
            _machine(
                "disk",
                "Disk",
                reason(
                    "machine",
                    "fail" if disk["status"] == "critical" else "warn",
                    "disk_full",
                    params={
                        "mount": disk["worst_mount"],
                        "percent": disk["worst_percent"],
                        "free": disk.get("free_bytes"),
                    },
                    actions=["free_space"],
                ),
            )
        )
    reboot = summary.get("reboot") or {}
    if reboot.get("required") is True:
        since = reboot.get("since")
        items.append(
            _machine(
                "reboot_required",
                "Reboot",
                reason(
                    "machine",
                    "warn",
                    "reboot_required",
                    params={"packages": list(reboot.get("packages") or []), "since": since},
                    when=to_iso_offset(since) if since else None,
                    actions=["schedule_reboot"],
                ),
            )
        )
    clock = summary.get("time") or {}
    if clock.get("synchronized") is False:
        items.append(
            _machine(
                "clock",
                "Clock",
                reason(
                    "machine",
                    "warn",
                    "clock_unsynced",
                    params={"timezone": clock.get("timezone")},
                    actions=["fix_clock"],
                ),
            )
        )
    swap = summary.get("swap") or {}
    if swap.get("recommended") is True and not swap.get("total_bytes"):
        items.append(
            _machine("swap", "Swap", reason("machine", "warn", "no_swap", actions=["add_swap"]))
        )
    known = {name.removesuffix(".service") for name in known_units}
    failed = [
        unit
        for unit in ((summary.get("system") or {}).get("failed_units") or [])
        if str(unit).removesuffix(".service") not in known
    ]
    if failed:
        items.append(
            _machine(
                "failed_units",
                "Failed services",
                reason(
                    "machine",
                    "warn",
                    "failed_units",
                    params={"units": failed, "count": len(failed)},
                    actions=["open_services"],
                ),
            )
        )
    return items


def security_items(checks: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """
    Read the open findings of the hardening checks.

    Args:
        checks: The last results (``GET /api/server/security``'s ``attention``): each with its
            ``id``, ``group``, ``title``, ``severity``, ``status`` and ``reason``.

    Returns:
        One row per check that failed (critical) or warned (warning); what passed, what was
        accepted, what could not be known and what is only information are not findings, and the
        checks the machine's facts already say with their numbers are left to them.
    """
    items: list[dict[str, Any]] = []
    for check in checks or ():
        severity = {"critical": "fail", "warning": "warn"}.get(str(check.get("severity")))
        if (
            check.get("status") not in ("fail", "warn")
            or severity is None
            or check.get("id") in COVERED_BY_FACTS
        ):
            continue
        identifier = str(check["id"])
        group = str(check.get("group") or "")
        items.append(
            _row(
                f"security:{identifier}",
                {"kind": "finding", "check": identifier, "group": group},
                str(check.get("title") or identifier),
                reason(
                    "security",
                    severity,
                    "security_finding",
                    params={"check": identifier, "group": group, "severity": check.get("severity")},
                    detail=str(check["reason"]) if check.get("reason") else None,
                    detail_coded=check.get("reason_coded"),
                    actions=["review_finding"],
                ),
            )
        )
    return items


def _container_story(failure: Mapping[str, Any]) -> ServerText | None:
    """
    Args:
        failure: One thing the Docker zone asks for, as ``Attention.to_dict`` says it.

    Returns:
        What happened to the container, as a sentence of the catalog; None for what is not a container
        of an application falling (a disk, a new version, a container that failed and stays stopped).
    """
    facts = failure.get("facts") or {}
    container = str(failure.get("subject") or "")
    kind = failure.get("kind")
    if kind == "stopped":
        return (
            say("attention.container_oom", container=container)
            if facts.get("kind") == "oom"
            else None
        )
    if kind != "loop":
        return None
    restarts = int(facts.get("restarts_recent") or 0)
    if restarts < LOOP_AT:
        # Docker is bringing it back right now, and it has not yet done it often enough for a count to say it.
        return say("attention.container_restarting", container=container)
    minutes = int(facts.get("window_minutes") or LOOP_MINUTES)
    if not facts.get("oom"):
        return say(
            "attention.container_loop", container=container, restarts=restarts, minutes=minutes
        )
    limit = facts.get("memory_limit_bytes")
    if isinstance(limit, int) and limit > 0:
        return say(
            "attention.container_loop_oom_limit",
            container=container,
            restarts=restarts,
            minutes=minutes,
            limit_mb=max(1, round(limit / 2**20)),
        )
    return say(
        "attention.container_loop_oom", container=container, restarts=restarts, minutes=minutes
    )


def container_items(failures: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """
    Read what the Docker zone knows of the containers of applications: the ones that keep falling.

    The application said it ran while its container was restarted six times in ten minutes, because
    that fact lived only in the zone. It is a failure of the application and joins the row its own state
    makes, with the story of the container as the detail and the same two ways in as any failed
    service: its log and its diagnosis.

    Args:
        failures: What :meth:`noust.core.docker.gate.DockerGate.app_failures` found, as plain data.

    Returns:
        One row per container of an application that is broken (rows of one application are joined by
        :class:`AttentionList`); what is not an application's, or is not a container falling, is none.
    """
    items: list[dict[str, Any]] = []
    for failure in failures or ():
        owner = failure.get("owner") or {}
        domain = str(owner.get("subject") or "")
        story = _container_story(failure)
        if owner.get("kind") != "app" or not domain or story is None:
            continue
        items.append(
            _row(
                f"app:{domain}",
                {"kind": "app", "domain": domain},
                domain,
                reason(
                    "state",
                    "fail",
                    "service_failed",
                    params={"container": str(failure.get("subject") or "")},
                    detail=story.text,
                    detail_coded=story.to_dict(),
                    actions=["view_log", "diagnose"],
                ),
            )
        )
    return items


def _moment(value: float | str | None) -> str | None:
    """
    Args:
        value: Epoch seconds, an ISO string or None.

    Returns:
        The moment as ISO 8601 with its offset, or None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")
    return to_iso_offset(value)


def request_items(requests: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """
    Make the rows of the requests waiting for a decision.

    Args:
        requests: The requests this person may decide: each with its ``id``, the ``requester``
            (a mapping with a ``name``) and ``created_at``.

    Returns:
        One warning row per request.
    """
    return [
        _row(
            f"approval:{request['id']}",
            {"kind": "approval", "id": request["id"]},
            f"#{request['id']}",
            reason(
                "approval",
                "warn",
                "approval_waiting",
                params={"id": request["id"], "by": (request.get("requester") or {}).get("name")},
                when=_moment(request.get("created_at")),
                actions=["review_approval"],
            ),
        )
        for request in requests
    ]


def noust_item(info: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """
    Make the row of a newer Noust.

    Args:
        info: What the update check says: ``has_update``, ``latest_version``, ``current_version``
            and ``security_update``; None when the check is off or has not answered.

    Returns:
        A warning row when there is a newer release; nothing when the check is off or the server
        is current.
    """
    if not info or info.get("has_update") is not True:
        return []
    security = info.get("security_update") is True
    return [
        _row(
            "noust:update",
            {"kind": "noust"},
            "Noust",
            reason(
                "noust",
                "warn",
                "noust_security_update" if security else "noust_update",
                params={
                    "version": info.get("latest_version") or "",
                    "current": info.get("current_version"),
                },
                actions=["open_about"],
            ),
        )
    ]


def recipe_items(statuses: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """
    Make the rows of the applications installed from a recipe that has a newer version.

    Args:
        statuses: Each installed recipe's status: ``domain``, ``recipe``, ``update_available``,
            ``installed_version`` and ``latest_version``.

    Returns:
        A warning row for each that is behind.
    """
    return [
        _row(
            f"recipe:{status['domain']}",
            {"kind": "recipe", "domain": status["domain"]},
            str(status["domain"]),
            reason(
                "recipe",
                "warn",
                "recipe_update",
                params={
                    "recipe": status["recipe"],
                    "version": status.get("latest_version") or "",
                    "current": status.get("installed_version"),
                },
                actions=["open_recipes"],
            ),
        )
        for status in statuses
        if status.get("update_available")
    ]


def waiting_stack_items(
    notes: Iterable[GateNote], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """
    Make the rows of the Compose stacks Noust has not been able to bring under the check of their start.

    A stack that cannot be resolved when the upgrade reaches it is left as it was and tried again every ten
    minutes; ``noust health`` says so. A week of that is not a thing that will mend by itself, and it is a
    stack whose start nothing holds to what was judged, so it joins what needs an operator.

    Args:
        notes: What the gate said of each stack it said something of
            (:func:`noust.core.compose_acceptance.notes`).
        now: The moment to count the days to; the clock by default.

    Returns:
        A warning row for each stack that has been ``pending`` for :data:`PENDING_ATTENTION_DAYS` days or more,
        on the row of its application. A stack whose start was held back is not here: it has its own words,
        and a note whose date cannot be read says nothing.
    """
    items = []
    for note in notes:
        days = note.days(now)
        if note.state != "pending" or days < PENDING_ATTENTION_DAYS:
            continue
        items.append(
            _row(
                f"app:{note.domain}",
                {"kind": "app", "domain": note.domain},
                note.domain,
                reason(
                    "state",
                    "warn",
                    "compose_check_waiting",
                    params={"days": days},
                    # What Compose or Docker said, as it said it.
                    detail=note.detail,
                    when=_moment(note.noted_at),
                ),
            )
        )
    return items
