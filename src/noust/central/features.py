# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Noust can do, in one table.

Before this table a function was refused in one place (the 146 calls of ``require_server_role``, each
with a label of its own) and offered in another (sixteen files of the console that each decided
what to show a hub), and the two agreed only by luck. Now a function is a **key** of
:data:`FEATURES`, and the table says what it needs:

- the **server role** (:attr:`Need.ROLE`): this Noust deploys here (``central.role = server``); a
  hub refuses it, and
- a **fact of the machine**: systemd, a web server, Docker, root, the journal
  (:class:`noust.central.machine.MachineFacts`).

The guard (:func:`noust.central.require_server_role`) takes its decision and its words from here, and
the session publishes what is available and why not (:func:`published`), so the console offers what
works and nothing else.

**The keys are by what a function needs, not by "hub or not".** A container application needs Docker
and a native one needs systemd and a web server; a machine that has only Docker (the container of a
NAS) can run the first and not the second, and the table says it. Today every function a hub
refuses also lists the server role: a hub deploys nothing itself, whatever the machine has. A
function that needs only Docker is the one the NAS lane lets a hub with Docker do, by taking the
role off its line: nothing else changes.

**What the guard refuses is the role; what is published is the role and the machine.** The guard has
only ever answered "is this a hub", and a server that lacks nginx or Docker keeps getting the error
of the manager that needed it, in its own words and with the way to fix it. What is published is the
fuller answer: whether the function would work here now, and the sentence for why not. The code of
that sentence tells the two apart (``features.hub_role`` against ``features.needs_*`` and Docker's
own), so the console can offer to install what a server lacks and offer nothing on a hub.

**The table is closed.** A call of the guard with a key that is not here is an error on every
machine, and a test fails on a key nobody guards (:attr:`Feature.guarded`) and on a call whose key
is not in the table (``tests/test_central_features.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from noust.core.server_text import ServerText, say

if TYPE_CHECKING:
    from noust.central.machine import MachineFacts

__all__ = [
    "FEATURES",
    "SERVER_ROLE_COMMAND",
    "Availability",
    "Feature",
    "Need",
    "availability",
    "feature",
    "hub_refusal",
    "published",
]

#: The command a hub's refusal gives to make a machine a server.
SERVER_ROLE_COMMAND = "noust config set central.role server"


class Need(str, Enum):
    """
    What a function can need. The value of a fact is the name of its field in ``MachineFacts``.

    Attributes:
        ROLE: This Noust deploys here: ``central.role`` is ``server``.
        SYSTEMD: The machine was booted with systemd.
        WEB_SERVER: nginx or Apache is installed.
        DOCKER: Noust can use the machine's Docker.
        ROOT: Noust runs as root.
        JOURNAL: ``journalctl`` is there.
    """

    ROLE = "role"
    SYSTEMD = "systemd"
    WEB_SERVER = "web_server"
    DOCKER = "docker"
    ROOT = "root"
    JOURNAL = "journal"


#: The sentence for each need of the machine that is missing. Docker is said by the engine's doctor.
_MISSING: dict[Need, str] = {
    Need.SYSTEMD: "features.needs_systemd",
    Need.WEB_SERVER: "features.needs_web_server",
    Need.ROOT: "features.needs_root",
    Need.JOURNAL: "features.needs_journal",
}


@dataclass(frozen=True)
class Feature:
    """
    One function of Noust and what it needs.

    Attributes:
        key: Its name, which the guard, the session and the console all use. A lowercase word with
            underscores, because the console reads it as a property of the session.
        label: What a refusal calls it, as a noun phrase ("Applications"). Several keys may be called
            the same when a person would not tell them apart.
        needs: What must be so for it to work, in the order a reason is looked for.
        guarded: Some manager refuses it with ``require_server_role``. False for what only the
            console decides by (the machine's clock, its logs, its terminal): a hub's own machine
            does those and no manager refuses them, but a container without systemd or a journal
            cannot, and the console should not offer them.
    """

    key: str
    label: str
    needs: tuple[Need, ...]
    guarded: bool = True


@dataclass(frozen=True)
class Availability:
    """
    Whether a function would work here, and why not when it would not.

    Attributes:
        available: It would.
        reason: The sentence for the first thing in the way; None when ``available``.
    """

    available: bool
    reason: ServerText | None = None


_SERVER = Need.ROLE

#: What a function that writes a site for a web server needs of the machine.
_WEB = (Need.WEB_SERVER, Need.ROOT)

#: What a function that keeps a service or a timer needs of the machine.
_UNIT = (Need.SYSTEMD, Need.ROOT)


def _table(*features: Feature) -> dict[str, Feature]:
    """
    Args:
        *features: The functions.

    Returns:
        The table by key.

    Raises:
        ValueError: When a key is there twice, which would make one of them unreachable.
    """
    table: dict[str, Feature] = {}
    for item in features:
        if item.key in table:
            raise ValueError(f"{item.key} is in the table of features twice")
        table[item.key] = item
    return table


#: Every function, by key. Grouped by what a person calls it; each line is what it needs.
FEATURES: dict[str, Feature] = _table(
    # -- Applications. One key for what is done to an application whatever it runs as, and one for
    # each way of running one, because they need different things of the machine.
    Feature("applications", "Applications", (_SERVER,)),
    Feature("native_deploy", "Applications", (_SERVER, Need.SYSTEMD, Need.WEB_SERVER, Need.ROOT)),
    # A container application is run by a unit that starts its Compose file, today. The NAS lane
    # takes the unit and the role off this line when Docker's own restart policy keeps it alive.
    Feature("container_deploy", "Applications", (_SERVER, Need.SYSTEMD, Need.DOCKER, Need.ROOT)),
    Feature("releases", "Releases", (_SERVER,)),
    Feature("rollbacks", "Rollbacks", (_SERVER,)),
    Feature(
        "preview_environments",
        "Preview environments",
        (_SERVER, Need.SYSTEMD, Need.WEB_SERVER, Need.ROOT),
    ),
    Feature("importing_applications", "Importing applications", (_SERVER,)),
    Feature("recipes", "Recipes", (_SERVER,)),
    Feature("application_environments", "Application environments", (_SERVER,)),
    Feature("application_diagnosis", "Application diagnosis", (_SERVER,)),
    # -- What watches and sets up the server Noust deploys to.
    Feature("process_monitor", "The process monitor", (_SERVER, Need.SYSTEMD)),
    Feature("server_health", "The server health report", (_SERVER,)),
    Feature("server_setup", "Server setup (nginx, runtimes, certbot)", (_SERVER, Need.ROOT)),
    # -- Names, sites and certificates: what is written for the web server.
    Feature("domains", "Domains", (_SERVER, *_WEB)),
    Feature("sites", "Sites", (_SERVER, *_WEB)),
    Feature("certificates", "Certificates", (_SERVER, *_WEB)),
    Feature("console_exposure", "Console exposure", (_SERVER, *_WEB)),
    Feature("status_page", "The status page", (_SERVER, *_WEB)),
    Feature("published_ports", "Published ports", (_SERVER, *_WEB)),
    Feature("tunnels", "Tunnels", (_SERVER, *_UNIT)),
    Feature("noust_dns", "Noust DNS", (_SERVER, *_UNIT)),
    # Records and accounts at a DNS provider are calls to its API: the machine has nothing to give.
    Feature("dns_records", "DNS records", (_SERVER,)),
    Feature("dns_accounts", "DNS accounts", (_SERVER,)),
    # -- Databases and what is copied of them.
    Feature("databases", "Databases", (_SERVER, *_UNIT)),
    Feature("database_instances", "Database instances", (_SERVER, Need.DOCKER)),
    Feature("public_database_access", "Public access to a database", (_SERVER, *_UNIT)),
    Feature("database_backups", "Database backups", (_SERVER, *_UNIT)),
    Feature("point_in_time_recovery", "Point-in-time recovery", (_SERVER, *_UNIT)),
    Feature("application_backups", "Application backups", (_SERVER, Need.ROOT)),
    Feature("volume_backups", "Volume backups", (_SERVER, Need.DOCKER)),
    Feature("restoring_a_server", "Restoring a server", (_SERVER, *_UNIT)),
    Feature("moving_a_server", "Moving a server", (_SERVER, Need.ROOT)),
    Feature("checking_a_moved_server", "Checking a moved server", (_SERVER,)),
    # -- What keeps running on the machine.
    Feature("scheduled_jobs", "Scheduled jobs", (_SERVER, *_UNIT)),
    Feature("services", "Services", (_SERVER, *_UNIT)),
    Feature("software", "Software", (_SERVER, Need.ROOT)),
    Feature("log_destinations", "Log destinations", (_SERVER, Need.JOURNAL)),
    # -- The machine itself. No manager refuses these to a hub, whose own machine does them, and
    # no role is needed; they are here because a machine without what they ask for cannot, and the
    # console should not offer them (a container has no systemd, no journal and no root).
    Feature("system_clock", "The system clock", (Need.SYSTEMD,), guarded=False),
    Feature("power", "Reboot and shutdown", (Need.SYSTEMD, Need.ROOT), guarded=False),
    Feature("system_logs", "The system logs", (Need.JOURNAL,), guarded=False),
    Feature("root_shell", "A root shell", (Need.ROOT,), guarded=False),
    Feature("server_security", "Server security", (Need.SYSTEMD, Need.ROOT), guarded=False),
    Feature("firewall", "The firewall", (Need.SYSTEMD, Need.ROOT), guarded=False),
)


def feature(key: str) -> Feature:
    """
    Find a function of the table.

    Args:
        key: Its key.

    Returns:
        The function.

    Raises:
        KeyError: When the table has no such key. Said on every machine and not only on a hub, so
            a mistyped key is found by the first test that calls it.
    """
    try:
        return FEATURES[key]
    except KeyError:
        raise KeyError(
            f"{key!r} is not a function of noust.central.features: add it to the table "
            "with what it needs"
        ) from None


def hub_refusal(item: Feature) -> ServerText:
    """
    Say why a hub does not do a function.

    Args:
        item: The function.

    Returns:
        The sentence the guard raises and the session publishes: one wording for both.
    """
    return say("features.hub_role", feature=item.label, command=SERVER_ROLE_COMMAND)


def _missing(need: Need, facts: MachineFacts) -> ServerText:
    """
    Args:
        need: A fact the machine lacks.
        facts: What the machine has.

    Returns:
        Why it matters: the doctor's sentence for Docker, and the one named in :data:`_MISSING`
        for the rest.
    """
    if need is Need.DOCKER:
        return facts.docker_problem or say("docker.engine.missing", command="noust setup init")
    return say(_MISSING[need])


def availability(key: str, *, role: str, facts: MachineFacts) -> Availability:
    """
    Say whether a function would work here now.

    Args:
        key: The function's key.
        role: This Noust's role, ``server`` or ``hub``.
        facts: What the machine has.

    Returns:
        Yes, or the sentence for the first need that is not met: the role first, then the machine in
        the order the table lists it.

    Raises:
        KeyError: When the table has no such key.
    """
    item = feature(key)
    for need in item.needs:
        if need is Need.ROLE:
            if role != "server":
                return Availability(False, hub_refusal(item))
        elif not getattr(facts, need.value):
            return Availability(False, _missing(need, facts))
    return Availability(True)


def published(*, role: str, facts: MachineFacts) -> dict[str, dict[str, Any]]:
    """
    Say what is available, for everything in the table, as the session carries it.

    Args:
        role: This Noust's role.
        facts: What the machine has.

    Returns:
        For each key ``{"available": bool, "reason": ...}``; the reason is None when available and
        otherwise the sentence as ``{code, params, text, hint}``, the English beside the code the
        console translates it by.
    """
    said: dict[str, dict[str, Any]] = {}
    for key in FEATURES:
        answer = availability(key, role=role, facts=facts)
        reason = answer.reason
        said[key] = {
            "available": answer.available,
            "reason": None
            if reason is None
            else {**reason.to_dict(), "text": reason.text, "hint": reason.hint},
        }
    return said
