# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whose a Docker object is, decided from what Noust registered and never from what the object says.

The notices of 2026 against the tools that manage Docker (Dokploy 130, Coolify 61, Portainer 19)
repeat one pattern: the id of a container is not tied to its owner before the permission is checked.
This module is where that is done, once (rule 4), for every kind of object, and the rules are these.

- **The store is the authority.** An application owns what runs from its directory: the container's
  ``com.docker.compose.project.working_dir`` has to be the directory the store records for the
  application (or inside it), and when the store pinned a Compose project for it (an adopted stack)
  the container's project has to be that one. A database engine owns the container the store's row
  names, by name. Noust owns the container it runs in and what it started for a copy.
- **A claim can only take access away.** A label that says "I am Noust's" makes an object more
  restricted, never less; a label that says "I belong to this application" is checked against the
  directory and the project the store has, and an object that merely wears it is nobody's.
- **What nobody made is the instance's.** An object Noust did not make is :data:`NOBODY`: only who
  administers the server sees it or changes it.

Matching is by path components, after ``..`` is resolved, so ``/var/www/apps/shop-example-com-evil``
is not inside ``/var/www/apps/shop-example-com`` and ``/var/www/apps/shop/../blog`` is the blog's.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from noust.core.docker import names
from noust.core.docker.models import (
    NOBODY,
    Container,
    Image,
    Network,
    Owner,
    OwnerKind,
    Volume,
)

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = [
    "AppRecord",
    "OwnerIndex",
    "Registry",
    "own_container_ids",
    "registry_from_store",
]

_CONTAINER_ID_IN_MOUNTS = re.compile(r"/docker/containers/([0-9a-f]{64})/")
_SHORT_ID = re.compile(r"[0-9a-f]{12}")


@dataclass(frozen=True)
class AppRecord:
    """
    What the store knows of an application that Docker's objects are matched with.

    Attributes:
        domain: The application's domain.
        path: The directory it runs from, as the store records it.
        project: The Compose project the store pinned for it (an adopted stack); empty when it
            runs under the name Compose derives from its directory.
    """

    domain: str
    path: str
    project: str = ""

    @property
    def app_name(self) -> str:
        """The name of the application: its domain with the dots as dashes."""
        return self.domain.replace(".", "-")


@dataclass(frozen=True)
class Registry:
    """
    What Noust registered, which is what decides whose an object is.

    Attributes:
        apps: Every application.
        engines: The name of every database instance Noust runs in a container.
        own_ids: The ids (full or the first twelve digits) of the container Noust runs in.
    """

    apps: tuple[AppRecord, ...] = ()
    engines: tuple[str, ...] = ()
    own_ids: frozenset[str] = field(default_factory=frozenset)


def own_container_ids(mountinfo: Path = Path("/proc/self/mountinfo")) -> frozenset[str]:
    """
    Find the container this process runs in, so that Noust never stops itself.

    Docker mounts the container's ``hostname`` and ``resolv.conf`` from a folder named after its id,
    which ``mountinfo`` shows, and its hostname is the first twelve digits of that id unless it was
    set.

    Args:
        mountinfo: The file that lists this process's mounts.

    Returns:
        The ids found; empty on a machine that is not a container.
    """
    found: set[str] = set()
    try:
        text = mountinfo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    found.update(_CONTAINER_ID_IN_MOUNTS.findall(text))
    hostname = os.environ.get("HOSTNAME", "")
    if _SHORT_ID.fullmatch(hostname):
        found.add(hostname)
    return frozenset(found)


def registry_from_store(store: NoustStore, own_ids: Iterable[str] | None = None) -> Registry:
    """
    Read what Noust registered out of its store.

    Args:
        store: The store; the instance's, not a team's: the owner of an object is a fact, and who
            may see it is decided by :mod:`noust.core.docker.judge`.
        own_ids: The ids of the container Noust runs in; read from this process when None.

    Returns:
        The registry.
    """
    from noust.core.appdir.layout import app_root
    from noust.core.db_instance_store import DbInstanceStore

    apps = tuple(
        AppRecord(app.domain, str(app_root(app)), app.compose_project or "")
        for app in store.list_apps()
    )
    engines = tuple(record.name for record in DbInstanceStore(store).all())
    return Registry(
        apps=apps,
        engines=engines,
        own_ids=frozenset(own_ids) if own_ids is not None else own_container_ids(),
    )


def _inside(path: str, root: str) -> bool:
    """
    Tell whether a path is a directory or something below it, by components.

    Args:
        path: The path to judge.
        root: The directory.

    Returns:
        True when ``path`` is ``root`` or under it, once ``..`` is resolved. A path that is empty or
        relative is inside nothing.
    """
    if not path.startswith("/") or not root.startswith("/"):
        return False
    where = posixpath.normpath(path)
    top = posixpath.normpath(root)
    return where == top or where.startswith(top.rstrip("/") + "/")


def _common(owners: Iterable[Owner]) -> Owner:
    """
    Args:
        owners: The owners of a set of objects.

    Returns:
        The one they all have, or nobody when there are none or they differ.
    """
    distinct = {(owner.kind, owner.subject) for owner in owners}
    if len(distinct) != 1:
        return NOBODY
    kind, subject = next(iter(distinct))
    if kind is OwnerKind.NOBODY:
        return NOBODY
    for owner in owners:
        return Owner(kind, subject, owner.project)
    return NOBODY  # pragma: no cover


class OwnerIndex:
    """
    Whose each object is, for one look at the machine.

    Built from what Noust registered and the containers Docker lists, and asked once per object.
    Building it reads no disk and asks no process: the containers are given.

    Args:
        registry: What Noust registered.
        containers: Every container on the machine.
    """

    def __init__(self, registry: Registry, containers: Sequence[Container] = ()) -> None:
        self.registry = registry
        self._apps = sorted(registry.apps, key=lambda app: len(app.path), reverse=True)
        self._containers = list(containers)
        self._owner_of_container: dict[str, Owner] = {}
        for found in self._containers:
            self._owner_of_container[found.id] = self.of_container(found)
        # The projects an application runs under, as its containers show them: a stack that names
        # its own project is the only way to know it before a volume or a network says it.
        self._projects: dict[str, set[str]] = {}
        for found in self._containers:
            owner = self._owner_of_container[found.id]
            if owner.kind is OwnerKind.APP and found.project:
                self._projects.setdefault(owner.subject, set()).add(found.project)

    # -- containers ------------------------------------------------------------------------

    def of_container(self, container: Container) -> Owner:
        """
        Args:
            container: A container.

        Returns:
            Noust if it is the one Noust runs in or one it started for a copy, the engine whose
            row names it, the application whose directory it runs from, else nobody.
        """
        if any(
            container.id.startswith(own) or own.startswith(container.id)
            for own in self.registry.own_ids
            if own
        ):
            return Owner(OwnerKind.NOUST, "central")
        for engine in self.registry.engines:
            if container.name == names.instance_container(engine):
                return Owner(OwnerKind.DATABASE, engine, names.instance_project(engine))
        if container.helper:
            return Owner(OwnerKind.NOUST, "helper")
        if container.project:
            for app in self._apps:
                if not _inside(container.working_dir, app.path):
                    continue
                if app.project and container.project != app.project:
                    continue
                return Owner(OwnerKind.APP, app.domain, container.project)
        return NOBODY

    # -- what is named after a project ----------------------------------------------------------

    def _app_of_project(self, project: str) -> AppRecord | None:
        """
        Args:
            project: A ``com.docker.compose.project`` label.

        Returns:
            The application that runs under it: the store's pinned project, a project its
            containers show, or the name Compose derives from its directory. The first with a
            claim, the most specific directory first.
        """
        if not project:
            return None
        for app in self._apps:
            if app.project and app.project == project:
                return app
        for app in self._apps:
            if project in self._projects.get(app.domain, ()):
                return app
        for app in self._apps:
            if not app.project and project == names.project_of_directory(
                PurePosixPath(app.path).name
            ):
                return app
        return None

    def _owner_of_project_label(
        self, project: str, name: str, *, mounted_by: Iterable[Container]
    ) -> Owner:
        """
        Decide whose a volume or a network is that carries a project label.

        Args:
            project: Its ``com.docker.compose.project`` label.
            name: Its name.
            mounted_by: The containers that use it.

        Returns:
            Its project's owner when the name is the one Compose gives (``<project>_<name>``) or a
            container of that owner uses it; else nobody: a label anyone can write is a claim.
        """
        for engine in self.registry.engines:
            if project == names.instance_project(engine):
                return Owner(OwnerKind.DATABASE, engine, project)
        app = self._app_of_project(project)
        if app is None:
            return NOBODY
        if name.startswith(f"{project}_"):
            return Owner(OwnerKind.APP, app.domain, project)
        for user in mounted_by:
            owner = self._owner_of_container.get(user.id, NOBODY)
            if owner.kind is OwnerKind.APP and owner.subject == app.domain:
                return Owner(OwnerKind.APP, app.domain, project)
        return NOBODY

    # -- volumes and networks ------------------------------------------------------------------

    def of_volume(self, volume: Volume) -> Owner:
        """
        Args:
            volume: A named volume.

        Returns:
            The engine whose data or configuration volume it is, the application whose project
            made it (the name Compose gives, or mounted by that application's container), else
            nobody.
        """
        for engine in self.registry.engines:
            if volume.name in {
                names.instance_data_volume(engine),
                names.instance_config_volume(engine),
            }:
                return Owner(OwnerKind.DATABASE, engine, names.instance_project(engine))
        return self._owner_of_project_label(
            volume.project,
            volume.name,
            mounted_by=[found for found in self._containers if volume.name in found.mounts],
        )

    def of_network(self, network: Network) -> Owner:
        """
        Args:
            network: A network.

        Returns:
            The owner of its project when the name is the one Compose gives or a container of that
            owner is on it, else nobody (and the ones Docker makes are nobody's).
        """
        return self._owner_of_project_label(
            network.project,
            network.name,
            mounted_by=[found for found in self._containers if network.name in found.networks],
        )

    # -- images and projects ---------------------------------------------------------------

    def is_way_back(self, image: Image) -> bool:
        """
        Args:
            image: An image.

        Returns:
            Whether an application keeps it to go back to, by any of its names.
        """
        return any(names.is_way_back(tag) for tag in image.tags)

    def of_image(self, image: Image) -> Owner:
        """
        Args:
            image: An image, with the names of the containers made from it.

        Returns:
            The application whose release it is by its name; else the one owner every container
            made from it has; else nobody (an image two owners use is not either's to remove).
        """
        for tag in image.tags:
            repository, _, tail = tag.rpartition(":")
            for app in self.registry.apps:
                if names.release_of_tag(app.app_name, tag) is not None:
                    return Owner(OwnerKind.APP, app.domain)
                if (tail.startswith(names.TAG_PREFIX) or tail in names.PREVIOUS_TAGS) and any(
                    repository.startswith(f"{project}-") for project in self._projects_of(app)
                ):
                    return Owner(OwnerKind.APP, app.domain)
        users = [
            self._owner_of_container[found.id]
            for found in self._containers
            if found.name in image.used_by
        ]
        common = _common(users)
        # An image is not part of a project: whose it is, and nothing more.
        return Owner(common.kind, common.subject) if users else NOBODY

    def _projects_of(self, app: AppRecord) -> set[str]:
        """
        Args:
            app: An application.

        Returns:
            Every project name it can run under.
        """
        projects = set(self._projects.get(app.domain, ()))
        if app.project:
            projects.add(app.project)
        else:
            projects.add(names.project_of_directory(PurePosixPath(app.path).name))
        return {project for project in projects if project}

    def of_project(self, name: str, working_dir: str = "") -> Owner:
        """
        Args:
            name: A Compose project.
            working_dir: The directory it runs from.

        Returns:
            The owner all its containers have; for a project with none, the application whose
            directory it runs from; else nobody.
        """
        members = [
            self._owner_of_container[found.id]
            for found in self._containers
            if found.project == name
        ]
        if members:
            return _common(members)
        for app in self._apps:
            if _inside(working_dir, app.path) and (not app.project or app.project == name):
                return Owner(OwnerKind.APP, app.domain, name)
        return NOBODY
