# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The database servers of a Noust server: one list for every place an engine can run (4.1, item 103).

The owner got lost in Databases in 4.0, among engines, instances, containers and databases. The
model the console draws is two things: the **databases** an application uses, and the **database
servers** they run on. A database server is an engine in a version, running somewhere, on a Noust
server:

- **in the system**: the host's own PostgreSQL, MySQL or MariaDB, Redis or Valkey, MongoDB;
- **in a container Noust runs**: another version beside the system's, or an engine the system
  lacks (:mod:`noust.managers.database.managed`), whose stopped ones Docker no longer shows;
- **in a container an application's stack runs**: not Noust's to start, remove or choose as a
  default, but a place databases are, so it is listed.

This module is the **join, not a second implementation** (rule 3). What is done to a server stays
with the code that did it: the engines' routes drive the system's, and
:class:`~noust.managers.database.managed.ManagedInstances` makes, starts, upgrades and removes the
containers. What is new is what no one of them says: one list; the **default** of each engine, as a
hosting panel keeps (the operator's choice, else the system's, else the only one, else the
oldest); when each one's support ends, next to its version; and how many databases it holds.

A database Noust finds on a server and does not manage is not a kind of server: it is a database
with the state "not added", and the action is "add it to Noust"
(:func:`~noust.managers.database.adoption.adopt_database`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from noust.core.db_instance_store import CREATING, READY, REMOVING
from noust.core.db_server_defaults import DbServerDefaultStore
from noust.core.exceptions import DatabaseError, DatabaseNotFoundError, ValidationError
from noust.core.server_text import say
from noust.managers.database.flavours import FLAVOURS
from noust.managers.database.instance_catalog import ALIASES, SPECS
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.service import DatabaseService, DatabaseView, ListingProblem
from noust.validators.db_instance import check_server_key

__all__ = ["DatabaseServers", "ServerListing"]

#: Where a server runs.
RUNS_IN_SYSTEM = "system"
RUNS_IN_NOUST = "noust_container"
RUNS_IN_APP = "app_container"

#: Why a server is the default of its engine.
SOURCE_CHOSEN = "chosen"
SOURCE_SYSTEM = "system"
SOURCE_ONLY = "only"
SOURCE_OLDEST = "oldest"

#: The states of a container Noust runs in which it is being made or removed: it is listed, and
#: nothing can be done to it until it is finished.
UNFINISHED = frozenset({CREATING, REMOVING})

#: What the listing of the command line prints for the command the errors suggest.
LIST_COMMAND = "noust db servers"
CREATE_COMMAND = "noust db instance create"


@dataclass
class ServerListing:
    """
    Every database server, and what could not be read.

    Attributes:
        servers: One entry per server, in the order the console draws them: the system's engines,
            then the containers by key.
        problems: One entry per place that could not be asked (Docker not answering, an engine
            that refused Noust), so a short list is never taken for a server with nothing in it.
    """

    servers: list[dict[str, Any]]
    problems: list[ListingProblem] = field(default_factory=list)


def _docker_problem(exc: DatabaseError) -> ListingProblem:
    """
    Args:
        exc: What Docker answered, or did not.

    Returns:
        The problem to report: the containers could not be listed.
    """
    return ListingProblem(
        engine="docker",
        display_name="Docker",
        message=exc.message,
        hint=exc.details,
        output=exc.output or "",
        access=False,
        kind="docker",
    )


class DatabaseServers:
    """
    List the database servers, choose the default of an engine and take the choice back.

    Args:
        service: The database service, which resolves engines and finds containers.
        managed: The manager of the containers Noust runs; built over ``service`` when omitted.
    """

    def __init__(self, service: DatabaseService, *, managed: ManagedInstances | None = None):
        self.service = service
        self.managed = managed or ManagedInstances(service)
        self.defaults = DbServerDefaultStore(service.store)

    # ---------------------------------------------------------------- the list

    def all(
        self,
        *,
        engine: str | None = None,
        visible: Callable[[DatabaseView], bool] | None = None,
    ) -> ServerListing:
        """
        List the database servers of this Noust server.

        Args:
            engine: Only this engine (a flavour, an alias or the engine of a family: ``mysql``
                names MySQL and MariaDB, ``mariadb`` only MariaDB).
            visible: Which databases count towards what a server holds: a team member counts
                what its teams own. Every database counts when omitted.

        Returns:
            The servers, each with ``default`` and ``default_source`` decided, and the problems.
        """
        problems: list[ListingProblem] = []
        servers = [*self._system(), *self._containers(problems)]
        counts = self._counts(servers, problems, visible)
        for server in servers:
            server["databases"] = counts.get(server["key"])
        self._decide_defaults(servers)
        for server in servers:
            server["actions"] = self._actions(server)
        if engine:
            wanted = ALIASES.get(engine.strip().lower(), engine.strip().lower())
            servers = [s for s in servers if wanted in (s["flavour"], s["engine"])]
        return ServerListing(servers, problems)

    def get(
        self, key: str, *, visible: Callable[[DatabaseView], bool] | None = None
    ) -> dict[str, Any]:
        """
        Describe one server.

        Args:
            key: Its key (``mysql``, ``mysql@legacy``).
            visible: Which databases count towards what it holds; see :meth:`all`.

        Returns:
            The server, as :meth:`all` describes it.

        Raises:
            ValidationError: The key cannot be a server's (``field`` is ``server``).
            DatabaseNotFoundError: No server has that key; the error says how to list them.
        """
        wanted = self._key(key)
        listing = self.all(visible=visible)
        found = next((s for s in listing.servers if s["key"] == wanted), None)
        if found is None:
            raise DatabaseNotFoundError.from_text(
                say("database_servers.unknown", key=key, command=LIST_COMMAND)
            )
        return found

    def _key(self, key: str) -> str:
        """
        Args:
            key: As typed. A key of a Compose stack's container has a dot in it, which is not
                a name Noust makes servers of, and is looked up as typed.

        Returns:
            The key, judged.

        Raises:
            ValidationError: It is no key at all (not text, spaces, a shell's characters).
        """
        if isinstance(key, str) and "@" in key and "." in key.partition("@")[2]:
            # A container of an application: a key to look up, never one to choose as a default.
            return key
        return check_server_key(key)

    def _system(self) -> list[dict[str, Any]]:
        """
        Returns:
            The engines installed on the system, in the registry's order.
        """
        found: list[dict[str, Any]] = []
        for manager in self.service.host_managers():
            if not manager.is_installed():
                continue
            status = manager.get_status()
            running = bool(status.get("running"))
            flavour = manager.installed_flavour() or manager.ENGINE_NAME
            support = status.get("support") or {}
            found.append(
                {
                    "key": manager.ENGINE_NAME,
                    "engine": manager.engine_type,
                    "flavour": flavour,
                    "display_name": FLAVOURS[flavour].display_name
                    if flavour in FLAVOURS
                    else manager.DISPLAY_NAME,
                    "version": status.get("version"),
                    "runs_in": RUNS_IN_SYSTEM,
                    "name": None,
                    "state": "running" if running else "stopped",
                    "running": running,
                    "port": manager.server_port() if running else status.get("port"),
                    "memory_limit_mb": None,
                    "image": None,
                    "digest": None,
                    "custom_image": False,
                    "app": None,
                    "support": support.get("status"),
                    "end_of_life": support.get("end_of_life"),
                    "created_at": None,
                }
            )
        return found

    def _containers(self, problems: list[ListingProblem]) -> list[dict[str, Any]]:
        """
        Args:
            problems: Where Docker's failure to answer is recorded.

        Returns:
            The containers Noust runs (whatever state they are in) and those an application's
            stack runs; none, and a problem, when Docker does not answer.
        """
        try:
            held = self.service.instances()
            described = self.managed.all()
        except DatabaseError as exc:
            problems.append(_docker_problem(exc))
            return []
        found: list[dict[str, Any]] = []
        for entry in described:
            flavour = entry["flavour"]
            state = entry["state"] if entry["state"] != READY else entry["status"]
            found.append(
                {
                    "key": entry["key"],
                    "engine": entry["engine"],
                    "flavour": flavour,
                    "display_name": FLAVOURS[flavour].display_name
                    if flavour in FLAVOURS
                    else entry["display_name"],
                    "version": entry["version"],
                    "runs_in": RUNS_IN_NOUST,
                    "name": entry["name"],
                    "state": state,
                    "running": bool(entry["running"]),
                    "port": entry["host_port"],
                    "memory_limit_mb": entry["memory_limit_mb"],
                    "image": entry["image"],
                    "digest": entry["digest"],
                    "custom_image": bool(entry["custom_image"]),
                    "app": None,
                    "support": entry["support"],
                    "end_of_life": entry["end_of_life"],
                    "created_at": entry["created_at"],
                }
            )
        for item in held:
            if item.managed is not None:
                continue
            # The image's own engine (MariaDB, Valkey) when Noust knows it; else the engine.
            flavour = item.flavour if item.flavour in FLAVOURS else item.engine
            found.append(
                {
                    "key": item.key,
                    "engine": item.engine,
                    "flavour": flavour,
                    "display_name": FLAVOURS[flavour].display_name
                    if flavour in FLAVOURS
                    else item.engine,
                    "version": None,
                    "runs_in": RUNS_IN_APP,
                    "name": None,
                    "state": "running" if item.running else item.state,
                    "running": item.running,
                    "port": item.published[0].host_port if item.published else None,
                    "memory_limit_mb": None,
                    "image": item.image,
                    "digest": None,
                    "custom_image": False,
                    "app": item.app,
                    "support": None,
                    "end_of_life": None,
                    "created_at": None,
                }
            )
        return sorted(found, key=lambda server: server["key"])

    def _counts(
        self,
        servers: list[dict[str, Any]],
        problems: list[ListingProblem],
        visible: Callable[[DatabaseView], bool] | None,
    ) -> dict[str, int | None]:
        """
        Count the databases each running server holds.

        Args:
            servers: The servers.
            problems: Where an engine that could not be read is recorded.
            visible: Which databases count; all of them when None.

        Returns:
            The count by key; a server that is not running, or could not be read, has none.
        """
        listing = self.service.listing()
        # Docker not answering is already a problem of the list; the listing says it again.
        problems.extend(p for p in listing.problems if p.kind != "docker")
        unreadable = {problem.engine for problem in listing.problems}
        counts: dict[str, int | None] = {
            server["key"]: 0
            for server in servers
            if server["running"] and server["key"] not in unreadable
        }
        for view in listing.databases:
            if view.engine in counts and not view.missing and (visible is None or visible(view)):
                counts[view.engine] = (counts[view.engine] or 0) + 1
        return counts

    # ----------------------------------------------------------------- defaults

    def _candidates(self, servers: list[dict[str, Any]], flavour: str) -> list[dict[str, Any]]:
        """
        Args:
            servers: Every server.
            flavour: The engine.

        Returns:
            The servers a new database of the engine may be made on: the system's, and the
            containers Noust runs that are finished.
        """
        return [
            s
            for s in servers
            if s["flavour"] == flavour
            and (
                s["runs_in"] == RUNS_IN_SYSTEM
                or (s["runs_in"] == RUNS_IN_NOUST and s["state"] not in UNFINISHED)
            )
        ]

    def _decide_defaults(self, servers: list[dict[str, Any]]) -> None:
        """
        Mark exactly one server per engine as its default, and say why.

        Args:
            servers: Every server; each gets ``default`` and ``default_source``.
        """
        chosen = self.defaults.all()
        for server in servers:
            server["default"] = False
            server["default_source"] = None
        for flavour in sorted({s["flavour"] for s in servers}):
            candidates = self._candidates(servers, flavour)
            if not candidates:
                continue
            row = chosen.get(flavour)
            picked = next((s for s in candidates if row and s["key"] == row.server_key), None)
            if picked is not None:
                source = SOURCE_CHOSEN
            else:
                picked = next((s for s in candidates if s["runs_in"] == RUNS_IN_SYSTEM), None)
                source = SOURCE_SYSTEM
            if picked is None and len(candidates) == 1:
                picked, source = candidates[0], SOURCE_ONLY
            if picked is None:
                picked = min(candidates, key=lambda s: (s["created_at"] or "", s["key"]))
                source = SOURCE_OLDEST
            picked["default"] = True
            picked["default_source"] = source

    def _actions(self, server: dict[str, Any]) -> list[str]:
        """
        Args:
            server: A server, with its default decided.

        Returns:
            What can be done to it now, as codes the console draws buttons from.
        """
        if server["runs_in"] == RUNS_IN_APP:
            return []
        if server["state"] in UNFINISHED:
            return []
        actions = ["stop" if server["running"] else "start"]
        if not server["default"]:
            actions.append("set_default")
        elif server["default_source"] == SOURCE_CHOSEN:
            actions.append("clear_default")
        if server["flavour"] in SPECS:
            actions.append("other_version")
        if server["runs_in"] == RUNS_IN_NOUST:
            actions += ["upgrade", "remove"]
        return actions

    def set_default(self, key: str) -> dict[str, Any]:
        """
        Choose the server a new database of an engine is made on.

        Args:
            key: The server's key.

        Returns:
            The server, now the default of its engine.

        Raises:
            ValidationError: The key is not one, or the server is an application's container or
                is still being made (``field`` is ``server``).
            DatabaseNotFoundError: No server has that key.
        """
        server = self.get(key)
        if server["runs_in"] == RUNS_IN_APP:
            raise ValidationError.from_text(
                say("database_servers.default_app", key=server["key"], command=CREATE_COMMAND),
                field="server",
            )
        if server["state"] in UNFINISHED:
            raise ValidationError.from_text(
                say("database_servers.default_not_ready", key=server["key"], state=server["state"]),
                field="server",
            )
        previous = self.defaults.get(server["flavour"])
        actor = self.service.actor
        self.defaults.set(server["flavour"], server["key"], set_by=actor.label if actor else None)
        self.service.audit(
            "db.server.default",
            server["key"],
            flavour=server["flavour"],
            chosen=True,
            previous=previous.server_key if previous else None,
        )
        return self.get(server["key"])

    def clear_default(self, key: str) -> dict[str, Any]:
        """
        Take back the choice of this server as the default, if it was the one chosen.

        The engine goes back to the default Noust works out. Asking for a server that was not
        chosen changes nothing.

        Args:
            key: The server's key.

        Returns:
            The server as it is now.

        Raises:
            ValidationError: The key is not one (``field`` is ``server``).
            DatabaseNotFoundError: No server has that key.
        """
        server = self.get(key)
        row = self.defaults.get(server["flavour"])
        if row is not None and row.server_key == server["key"]:
            self.defaults.clear(server["flavour"])
            self.service.audit(
                "db.server.default",
                server["key"],
                flavour=server["flavour"],
                chosen=False,
                previous=server["key"],
            )
        return self.get(server["key"])
