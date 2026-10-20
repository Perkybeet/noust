# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a server fits together, generated from what it records: names, applications, databases,
scheduled work and the data an application keeps (checklist M-69).

A canvas an operator arranges by hand is a drawing that goes stale the day after it is made, and
every layout that depends on stored coordinates moves under the reader when something is added.
This is the opposite: the map is *read* from the server each time (the store, the applications'
units, the cron timers), in fixed layers from left to right, and carries no coordinates at all.
The same server is always the same picture (nodes are ordered, never positioned), and the console
draws it with the layered layout the site diagram uses, with the same connections as a table
beside it.

========  ===============================================  ===================================
layer     what                                             connects to
========  ===============================================  ===================================
names     every name an application answers on             the application, ``serves``
apps      the applications, with their state               databases, scheduled work, data
databases the databases the store records, by engine       (linked to the application ``uses``)
scheduled the cron jobs that belong to an application      (``runs``)
data      persistent paths and mounts of an application    (``keeps``)
========  ===============================================  ===================================

**Whose it is.** Applications and databases are read through :class:`ScopedStore`, so a team's map
shows its own and nothing else. The cron timers are the server's (``/api/cron`` is the instance's):
one that belongs to a visible application is drawn, one that belongs to none only for a principal who
sees every team. No process is run and nothing is probed except the applications' unit states, in
the one round the applications list also uses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from noust.core.teams.scoped import ScopedStore

if TYPE_CHECKING:
    from noust.core.store import App, Database

#: Most nodes a map holds; past it the rest is left out and the map says so.
MAX_NODES = 400

LAYER_NAMES = "names"
LAYER_APPS = "applications"
LAYER_DATABASES = "databases"
LAYER_SCHEDULED = "scheduled"
LAYER_DATA = "data"

#: The layers, left to right, each with the kind of node it holds.
LAYERS: tuple[tuple[str, str], ...] = (
    (LAYER_NAMES, "domain"),
    (LAYER_APPS, "application"),
    (LAYER_DATABASES, "database"),
    (LAYER_SCHEDULED, "scheduled"),
    (LAYER_DATA, "storage"),
)

#: What an application's state word means for a node: it responds, it does not, or it is not known.
_STATE_OF = {
    "running": "ok",
    "static": "ok",
    "restarting": "fail",
    "failed": "fail",
    "no_answer": "fail",
    "degraded": "fail",
    "stopped": "unknown",
    "unknown": "unknown",
}


@dataclass(frozen=True)
class MapNode:
    """
    One thing on the map.

    Attributes:
        id: Stable across reads: ``app:shop.example.com``.
        kind: ``domain``, ``application``, ``database``, ``scheduled`` or ``storage``.
        label: What it is called, as a system value.
        detail: A second line: an application's type, a database's engine, a job's schedule.
        state: ``ok``, ``fail`` or ``unknown`` for what can be checked; None for what cannot.
        state_word: The server's word for the state (``running``, ``failed``), for the console to say.
        group: The project a node belongs to, when there is more than one to tell apart.
        domain: The application it is about or belongs to, to open it.
        engine: A database's engine, to open it.
    """

    id: str
    kind: str
    label: str
    detail: str | None = None
    state: str | None = None
    state_word: str | None = None
    group: str | None = None
    domain: str | None = None
    engine: str | None = None


@dataclass(frozen=True)
class MapEdge:
    """
    A relation between two things.

    Attributes:
        source: The id it starts at.
        target: The id it leads to.
        relation: ``serves``, ``uses``, ``runs`` or ``keeps``.
    """

    source: str
    target: str
    relation: str


@dataclass
class MachineMap:
    """
    The map: layers of nodes, and what connects them.

    Attributes:
        layers: Each layer's id and its nodes, left to right.
        edges: Every relation.
        truncated: Nodes were left out because the map reached :data:`MAX_NODES`.
        projects: How many projects the nodes belong to; the console draws them apart when more than one.
    """

    layers: list[dict[str, Any]] = field(default_factory=list)
    edges: list[MapEdge] = field(default_factory=list)
    truncated: bool = False
    projects: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Returns: The map as JSON-ready data."""
        return {
            "layers": self.layers,
            "edges": [asdict(edge) for edge in self.edges],
            "truncated": self.truncated,
            "projects": self.projects,
        }


class MachineMapBuilder:
    """
    Reads the server into a :class:`MachineMap`.

    Args:
        scoped: The store as the principal sees it; the ambient one when omitted.
        cron_jobs: Lists the cron jobs (``CronManager().list_jobs``); read lazily, once.
        states: Resolves the applications' states in one round (``resolve_states_with_status``).
        mounts: Lists an application's mounts' targets.
        project_names: Maps a project id to the name drawn for it.
    """

    def __init__(
        self,
        *,
        scoped: ScopedStore | None = None,
        cron_jobs: Callable[[], list[dict[str, Any]]] | None = None,
        states: Callable[[list[App]], dict[str, str]] | None = None,
        mounts: Callable[[str], list[str]] | None = None,
        project_names: Callable[[int | None], str | None] | None = None,
    ) -> None:
        self._scoped = scoped
        self._cron_jobs = cron_jobs or (lambda: [])
        self._states = states or (lambda apps: {})
        self._mounts = mounts or (lambda domain: [])
        self._project_names = project_names or (lambda project_id: None)

    @property
    def scoped(self) -> ScopedStore:
        """The store as the principal sees it."""
        if self._scoped is None:
            self._scoped = ScopedStore()
        return self._scoped

    def build(
        self, *, project: str | None = None, environment: str | None = None, team: str | None = None
    ) -> MachineMap:
        """
        Read the map.

        Args:
            project: Only the projects with this slug.
            environment: Only the environments with this slug.
            team: Only this team's.

        Returns:
            The map of what the principal sees.
        """
        scoped = self.scoped
        apps = scoped.list_apps(project=project, environment=environment, team=team)
        apps_by_id = {app.id: app for app in apps if app.id is not None}
        databases = scoped.list_databases(project=project, environment=environment, team=team)
        states = self._states(apps)
        groups = {app.domain: self._project_names(app.project_id) for app in apps}
        distinct = {name for name in groups.values() if name is not None}
        draw_groups = len(distinct) > 1

        nodes: dict[str, list[MapNode]] = {layer: [] for layer, _ in LAYERS}
        edges: list[MapEdge] = []
        count = 0
        truncated = False

        def add(layer: str, node: MapNode) -> bool:
            nonlocal count, truncated
            if count >= MAX_NODES:
                truncated = True
                return False
            nodes[layer].append(node)
            count += 1
            return True

        for app in sorted(apps, key=lambda a: a.domain):
            group = groups[app.domain] if draw_groups else None
            word = states.get(app.domain)
            if not add(
                LAYER_APPS,
                MapNode(
                    id=f"app:{app.domain}",
                    kind="application",
                    label=app.domain,
                    detail=app.app_type or None,
                    state=_STATE_OF.get(word) if word is not None else None,
                    state_word=word,
                    group=group,
                    domain=app.domain,
                ),
            ):
                continue
            # The application's own name first, then its aliases, each once.
            for name in dict.fromkeys(
                [app.domain, *[d.domain for d in scoped.list_domains(app.domain)]]
            ):
                if add(
                    LAYER_NAMES,
                    MapNode(
                        id=f"domain:{name}",
                        kind="domain",
                        label=name,
                        group=group,
                        domain=app.domain,
                    ),
                ):
                    edges.append(MapEdge(f"domain:{name}", f"app:{app.domain}", "serves"))
            for target in [*app.persistent_paths, *self._mounts(app.domain)]:
                node_id = f"path:{app.domain}:{target}"
                if any(existing.id == node_id for existing in nodes[LAYER_DATA]):
                    continue
                if add(
                    LAYER_DATA,
                    MapNode(
                        id=node_id, kind="storage", label=target, group=group, domain=app.domain
                    ),
                ):
                    edges.append(MapEdge(f"app:{app.domain}", node_id, "keeps"))

        for database in sorted(databases, key=lambda d: (d.engine, d.name)):
            owner = apps_by_id.get(database.app_id) if database.app_id is not None else None
            group = self._database_group(database, groups, owner) if draw_groups else None
            node_id = f"db:{database.engine}:{database.name}"
            if (
                add(
                    LAYER_DATABASES,
                    MapNode(
                        id=node_id,
                        kind="database",
                        label=database.name,
                        detail=database.engine,
                        group=group,
                        engine=database.engine,
                        domain=owner.domain if owner is not None else None,
                    ),
                )
                and owner is not None
            ):
                edges.append(MapEdge(f"app:{owner.domain}", node_id, "uses"))

        visible = {app.domain for app in apps}
        see_server_wide = scoped.scope.sees_all
        for job in sorted(self._cron_jobs(), key=lambda j: str(j.get("name", ""))):
            domain = job.get("app_domain")
            if domain is not None and domain not in visible:
                continue
            if domain is None and not see_server_wide:
                continue
            name = str(job.get("name", ""))
            node_id = f"cron:{name}"
            if (
                add(
                    LAYER_SCHEDULED,
                    MapNode(
                        id=node_id,
                        kind="scheduled",
                        label=name,
                        detail=str(job.get("on_calendar") or "") or None,
                        state=None if job.get("enabled", True) else "unknown",
                        state_word=None if job.get("enabled", True) else "disabled",
                        group=groups.get(domain) if draw_groups and domain is not None else None,
                        domain=domain,
                    ),
                )
                and domain is not None
            ):
                edges.append(MapEdge(f"app:{domain}", node_id, "runs"))

        layers: list[dict[str, Any]] = [
            {"id": layer, "kind": kind, "nodes": [asdict(node) for node in nodes[layer]]}
            for layer, kind in LAYERS
        ]
        # A layer with nothing in it is not a column of the picture.
        layers = [layer for layer in layers if layer["nodes"]]
        present = {node["id"] for layer in layers for node in layer["nodes"]}
        return MachineMap(
            layers=layers,
            edges=[edge for edge in edges if edge.source in present and edge.target in present],
            truncated=truncated,
            projects=len(distinct),
        )

    @staticmethod
    def _database_group(
        database: Database, groups: dict[str, str | None], owner: App | None
    ) -> str | None:
        """Returns: The project a database is drawn under: its application's."""
        return groups.get(owner.domain) if owner is not None else None


def default_builder() -> MachineMapBuilder:
    """
    The builder over this server: the principal's store, the cron timers, the units' states.

    Returns:
        A builder whose reads are the ones the applications list and the cron list already make.
    """
    from noust.core import app_mount_store, app_state
    from noust.core.teams import scoped_store
    from noust.core.teams.repository import TeamStore
    from noust.managers.cron_manager import CronManager
    from noust.managers.service_manager import ServiceManager

    scoped = scoped_store()
    store = scoped.store
    services = ServiceManager(verbose=False)
    labels = TeamStore(store).owner_labels()

    def states(apps: list[App]) -> dict[str, str]:
        # The units' own word, in the API's vocabulary: no port is probed for a picture of the server.
        resolved = app_state.resolve_states_with_status(apps, services, probe=False)
        return {
            domain: app_state.PUBLIC_STATUS.get(state.label, state.label.lower())
            for domain, (state, _status) in resolved.items()
        }

    def mounts(domain: str) -> list[str]:
        # A file mount is configuration; a path is data the application keeps outside its release.
        return [
            record.target
            for record in app_mount_store.list_mounts(domain, store=store)
            if record.kind == app_mount_store.KIND_PATH
        ]

    return MachineMapBuilder(
        scoped=scoped,
        cron_jobs=CronManager(verbose=False).list_jobs,
        states=states,
        mounts=mounts,
        project_names=lambda project_id: labels.of(project_id, None)[1],
    )
