# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The things Docker has, as Noust sees them, and whose each one is.

Every object carries its :class:`Owner`: nothing in the gate hands out a container, an image, a
volume or a network that has not been resolved to an owner first, which is what makes the owner a
property of the object and not a check a caller remembers to make (rule 4). The shapes are plain
frozen dataclasses with a ``to_dict`` for the API and the command line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

__all__ = [
    "NOBODY",
    "Action",
    "ComposeProject",
    "Container",
    "Image",
    "Network",
    "ObjectKind",
    "Owner",
    "OwnerKind",
    "Page",
    "Removal",
    "Volume",
]


class ObjectKind(str, Enum):
    """What kind of thing a Docker object is."""

    CONTAINER = "container"
    IMAGE = "image"
    VOLUME = "volume"
    NETWORK = "network"
    PROJECT = "project"


class Action(str, Enum):
    """What can be done to an object. A project is changed through its application, not here."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    REMOVE = "remove"


class OwnerKind(str, Enum):
    """Whose an object is."""

    #: An application of Noust: a stack, or a container application. Through it, a project and a team.
    APP = "app"
    #: A database engine Noust runs in a container of its own (``noust db instance``).
    DATABASE = "database"
    #: Noust itself: the container Noust runs in, a helper it started for a copy.
    NOUST = "noust"
    #: Nobody's: Noust did not make it. It is the instance's, and only who administers the server
    #: sees it or changes it.
    NOBODY = "nobody"


@dataclass(frozen=True)
class Owner:
    """
    Whose an object is, and which one.

    Attributes:
        kind: See :class:`OwnerKind`.
        subject: The application's domain, the instance's name, ``central`` or ``helper`` for
            Noust's own; empty for nobody.
        project: The Compose project it is part of, when it is part of one.
    """

    kind: OwnerKind
    subject: str = ""
    project: str = ""

    @property
    def unowned(self) -> bool:
        """Whether Noust did not make it."""
        return self.kind is OwnerKind.NOBODY

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            ``kind``, ``subject`` and ``project``.
        """
        return {"kind": self.kind.value, "subject": self.subject, "project": self.project}


#: The owner of what Noust did not make.
NOBODY = Owner(OwnerKind.NOBODY)


def _plain(value: Any) -> Any:
    """
    Make a dataclass value something JSON can carry.

    Args:
        value: One field.

    Returns:
        Enums as their value, owners as their dictionary, tuples as lists.
    """
    if isinstance(value, Owner):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class Container:
    """
    A container, running or not.

    Attributes:
        id: The full id (64 hexadecimal digits).
        name: Its name, without the slash.
        image: The image it was made from, as it was asked for.
        state: ``running``, ``exited``, ``created``, ``paused``, ``restarting`` or ``dead``.
        status: Docker's sentence for it (``Up 2 hours (healthy)``).
        health: ``healthy``, ``unhealthy``, ``starting`` or empty when the image has no check.
        created: When it was made, as Docker prints it.
        ports: What it publishes, as Docker prints it.
        mounts: The volumes (by name) and the host paths it has mounted.
        networks: The networks it is on.
        project: Its ``com.docker.compose.project`` label.
        service: Its ``com.docker.compose.service`` label.
        working_dir: Its ``com.docker.compose.project.working_dir`` label: the directory the
            project runs from, which is what ties it to an application.
        config_files: The compose files Compose recorded for it.
        oneoff: Whether Compose made it for ``run`` (a hook, a relay's twin) and not ``up``.
        instance: Its ``io.noust.db-instance`` label. A claim, not a proof: the store decides.
        helper: Its ``io.noust.helper`` label, which Noust puts on what it starts for a copy.
        owner: Whose it is.
    """

    id: str
    name: str
    image: str = ""
    state: str = ""
    status: str = ""
    health: str = ""
    created: str = ""
    ports: str = ""
    mounts: tuple[str, ...] = ()
    networks: tuple[str, ...] = ()
    project: str = ""
    service: str = ""
    working_dir: str = ""
    config_files: tuple[str, ...] = ()
    oneoff: bool = False
    instance: str = ""
    helper: str = ""
    owner: Owner = NOBODY

    @property
    def running(self) -> bool:
        """Whether it is running now."""
        return self.state in {"running", "restarting", "paused"}

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, by name.
        """
        return {key: _plain(value) for key, value in asdict(self).items() if key != "owner"} | {
            "owner": self.owner.to_dict()
        }


@dataclass(frozen=True)
class Image:
    """
    An image, once, with every name it has.

    Attributes:
        id: Its id (``sha256:...``).
        tags: ``repository:tag`` for each name it has; none for an image with no name.
        created: When it was made, as Docker prints it.
        size_bytes: Its size, when Docker said it.
        used_by: The names of the containers made from it, running or not.
        way_back: Whether an application keeps it to go back to (see
            :func:`noust.core.docker.names.is_way_back`): never offered as unused.
        owner: Whose it is.
    """

    id: str
    tags: tuple[str, ...] = ()
    created: str = ""
    size_bytes: int | None = None
    used_by: tuple[str, ...] = ()
    way_back: bool = False
    owner: Owner = NOBODY

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, by name.
        """
        return {key: _plain(value) for key, value in asdict(self).items() if key != "owner"} | {
            "owner": self.owner.to_dict()
        }


@dataclass(frozen=True)
class Volume:
    """
    A named volume.

    Attributes:
        name: Its name.
        driver: Its driver, ``local`` for the ones Docker keeps itself.
        scope: ``local`` or ``global``.
        mountpoint: Where Docker keeps its data on the host.
        project: Its ``com.docker.compose.project`` label.
        key: Its ``com.docker.compose.volume`` label: the name the compose file gave it.
        instance: Its ``io.noust.db-instance`` label.
        size_bytes: Its size, when it has been measured; measuring costs a walk of the volume, so
            a listing leaves it empty (:meth:`DockerGate.volume_sizes`).
        used_by: The names of the containers that mount it.
        owner: Whose it is.
    """

    name: str
    driver: str = "local"
    scope: str = "local"
    mountpoint: str = ""
    project: str = ""
    key: str = ""
    instance: str = ""
    size_bytes: int | None = None
    used_by: tuple[str, ...] = ()
    owner: Owner = NOBODY

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, by name.
        """
        return {key: _plain(value) for key, value in asdict(self).items() if key != "owner"} | {
            "owner": self.owner.to_dict()
        }


@dataclass(frozen=True)
class Network:
    """
    A network.

    Attributes:
        id: Its id.
        name: Its name.
        driver: ``bridge``, ``host``, ``overlay``...
        scope: ``local`` or ``swarm``.
        internal: Whether it has no way out.
        project: Its ``com.docker.compose.project`` label.
        created: When it was made, as Docker prints it.
        containers: The names of the containers on it.
        owner: Whose it is.
    """

    id: str
    name: str
    driver: str = ""
    scope: str = ""
    internal: bool = False
    project: str = ""
    created: str = ""
    containers: tuple[str, ...] = ()
    owner: Owner = NOBODY

    @property
    def builtin(self) -> bool:
        """Whether Docker made it itself (``bridge``, ``host`` and ``none``)."""
        return self.name in {"bridge", "host", "none"} and self.driver in {"bridge", "host", "null"}

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, by name, and whether it is Docker's own.
        """
        return {key: _plain(value) for key, value in asdict(self).items() if key != "owner"} | {
            "builtin": self.builtin,
            "owner": self.owner.to_dict(),
        }


@dataclass(frozen=True)
class ComposeProject:
    """
    A Compose project, as its containers show it.

    Attributes:
        name: The project's name.
        working_dir: The directory it runs from.
        config_files: Its compose files.
        containers: The names of its containers.
        running: How many of them run.
        owner: Whose it is.
    """

    name: str
    working_dir: str = ""
    config_files: tuple[str, ...] = ()
    containers: tuple[str, ...] = ()
    running: int = 0
    owner: Owner = NOBODY

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, by name, and how many containers there are.
        """
        return {key: _plain(value) for key, value in asdict(self).items() if key != "owner"} | {
            "total": len(self.containers),
            "owner": self.owner.to_dict(),
        }


T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """
    One page of a listing.

    Attributes:
        items: What is on the page.
        total: How many there are in all, after the filters and before the page.
        offset: Where the page starts.
        limit: How many it holds at most; None for all of them.
    """

    items: Sequence[T] = field(default_factory=tuple)
    total: int = 0
    offset: int = 0
    limit: int | None = None


@dataclass(frozen=True)
class Removal:
    """
    What an action that removes something removed.

    Attributes:
        kind: Of what kind of object.
        name: Its name, as Docker has it.
        size_bytes: What it held, when it was measured (a volume's data is gone with it).
    """

    kind: ObjectKind
    name: str
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, by name.
        """
        return {"kind": self.kind.value, "name": self.name, "size_bytes": self.size_bytes}
