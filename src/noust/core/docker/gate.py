# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The gate to Docker: what the console, the command line and the jobs ask Docker through.

:class:`DockerGate` reads every kind of object Docker has (containers, images, volumes, networks and
Compose projects) with its owner already resolved, and does the few things that change them (start,
stop, restart and remove a container, remove an image, a volume or a network), each judged as
(owner, action, object) by :func:`noust.core.docker.judge.judge` before a process is started. It is
the only module that decides to run one of those commands (rule 4); the commands themselves go
through the runner as argument lists with a deadline (rule 1).

Three properties are the reason it exists.

- **Docker is only ever given what Docker listed.** A person names an object by its name or its id;
  the gate looks that up among the objects Docker itself listed and gives the command the full id or
  the exact name from the listing. What was typed never reaches an argument list: an option, a path,
  another container's name in a field that was meant for one's own cannot become part of a command.
- **An object is resolved to its owner before a permission is asked.** The id of a container is the
  same container as its name, whoever's it is, and its owner is what the store registered
  (:mod:`noust.core.docker.owners`), so asking by id what cannot be asked by name has no answer.
- **What a person may not see is not there.** A principal that sees only some teams gets the answer
  for an object that does not exist, for the ones that are not its teams'.

The gate is a library for the layers above it. It reads the principal's scope from the ambient one
(the request's inside a request, the instance's in the command line and in a job), so the same code
serves each without showing a team another team's containers.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeVar, cast

from noust.core.docker import cleanup as cleanup_module
from noust.core.docker import engine as engine_module
from noust.core.docker import events, image_sources, inspect, reading, registry, states, stats
from noust.core.docker import restart_loss as restart_loss_module
from noust.core.docker import summary as summary_module
from noust.core.docker.errors import (
    DockerPlanChangedError,
    DockerRefusedError,
    DockerUnavailableError,
)
from noust.core.docker.judge import Can, can, judge, not_found, visible
from noust.core.docker.models import (
    Action,
    ComposeProject,
    Container,
    Image,
    Network,
    ObjectKind,
    Owner,
    OwnerKind,
    Page,
    Removal,
    Volume,
)
from noust.core.docker.owners import OwnerIndex, Registry, registry_from_store
from noust.core.docker.versions import with_default_tag
from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
from noust.core.exceptions import ConfirmationRequired, DockerError, ValidationError
from noust.core.redact import Scrubber, scrubber_for
from noust.core.runner import CommandRunner, get_runner
from noust.core.teams.scope import Scope, current_scope

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["ContainerDetail", "DockerGate"]

T = TypeVar("T")

#: Seconds a container is given to stop before Docker kills it.
STOP_GRACE = 30

#: Seconds an action gets: the grace, then the daemon's own work.
ACTION_TIMEOUT = 90

#: Seconds an action on a whole stack gets: Compose stops what depends on a service before it, a layer at
#: a time, and each layer is given the grace.
STACK_TIMEOUT = 300

#: The most a page may hold: a console asks for a few dozen.
MAX_PAGE = 1000

#: How old the build cache has to be to be pruned: a week.
BUILD_CACHE_HOURS = 168

#: Lines of a container's log that say why it stopped, and the most a page of logs holds.
WHY_LINES = 20
MAX_LOG_LINES = 5000

#: The most characters of a log a call returns: a container that writes a megabyte a line is not a
#: log to put in a response.
MAX_LOG_CHARS = 2_000_000

#: Seconds a read of a container's log gets.
LOGS_TIMEOUT = 60

#: Seconds pruning the build cache gets: Docker walks every layer.
CLEANUP_TIMEOUT = 900

#: What a container, a volume or a network is called, and what an image is called.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}")
_IMAGE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,254}")
_HEX_ID = re.compile(r"(?:sha256:)?[0-9a-f]{12,64}")


def _human(size: int | None) -> str:
    """
    Args:
        size: Bytes, or None when they were not measured.

    Returns:
        The size in Docker's own powers of ten (``1.5 GB``), or ``an unknown amount of data``.
    """
    if size is None:
        return "an unknown amount of data"
    value = float(size)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1000
    return f"{size} B"  # pragma: no cover


def _cut(
    items: Sequence[T],
    owner: OwnerKind | None,
    search: str | None,
    words: Callable[[T], Sequence[str]],
) -> list[T]:
    """
    Cut a listing by who owns each thing and by a few words.

    Args:
        items: Objects that have an ``owner``.
        owner: Keep only those of this kind of owner.
        search: Keep only those that have this text in one of their words, any case.
        words: The words of an object a search looks in.

    Returns:
        What is left, in order.
    """
    found = list(items)
    if owner is not None:
        found = [item for item in found if item.owner.kind is owner]  # type: ignore[attr-defined]
    if search:
        needle = search.lower()
        found = [item for item in found if any(needle in word.lower() for word in words(item))]
    return found


def _page(items: Sequence[T], offset: int, limit: int | None) -> Page[T]:
    """
    Cut a window out of a listing.

    Args:
        items: Everything, in order.
        offset: Where the window starts.
        limit: How many it holds at most; None for all.

    Returns:
        The page.

    Raises:
        ValidationError: The window makes no sense.
    """
    if offset < 0:
        raise ValidationError("The offset of a page cannot be negative", field="offset")
    if limit is not None and not 1 <= limit <= MAX_PAGE:
        raise ValidationError(f"A page holds between 1 and {MAX_PAGE} items", field="limit")
    end = None if limit is None else offset + limit
    return Page(tuple(items[offset:end]), len(items), offset, limit)


@dataclass(frozen=True)
class ContainerDetail:
    """
    One container seen from the inside.

    Attributes:
        container: The container as the listing has it, with its owner.
        state: Its state as one word, with how its last run ended taken from ``inspect``.
        inspected: What ``inspect`` said (its policy, limits, mounts, health and times); None when
            the container went away between the listing and the look.
        usage: What it uses now, or why that is not known.
        last_lines: What it wrote last, as it wrote it and with its credentials masked; empty for
            a container that runs well, whose log is read live.
        restarts_recent: How many times Docker restarted it in the last ten minutes.
    """

    container: Container
    state: states.StateInfo
    inspected: inspect.Inspected | None
    usage: stats.Usage
    last_lines: str
    restarts_recent: int

    @property
    def looping(self) -> bool:
        """Whether it keeps falling: restarting now, or restarted often in the window."""
        return self.state.kind == "loop" or self.restarts_recent >= summary_module.LOOP_AT

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The container, how it is, why, where it comes from and what it touches, its policy and
            its consumption. Nothing of its environment is in it.
        """
        seen = self.inspected
        policy = seen.restart_policy if seen else inspect.RestartPolicy()
        return {
            "container": self.container.to_dict(),
            "state": self.state.to_dict(),
            "owner": self.container.owner.to_dict(),
            "job": states.is_job(kind=self.state.kind, policy=policy.name),
            "why": {
                "kind": self.state.kind,
                "exit_code": self.state.exit_code,
                "oom_killed": bool(seen and seen.oom_killed),
                "error": seen.error if seen else "",
                "restart_count": seen.restart_count if seen else 0,
                "restarts_recent": self.restarts_recent,
                "window_minutes": events.LOOP_MINUTES,
                "looping": self.looping,
                "started_at": seen.started_at.isoformat() if seen and seen.started_at else None,
                "finished_at": seen.finished_at.isoformat() if seen and seen.finished_at else None,
                "memory_limit_bytes": seen.memory_limit_bytes if seen else None,
                "health": seen.health.to_dict() if seen and seen.health else None,
                "last_lines": self.last_lines,
            },
            "origin": {
                "image": self.container.image,
                "image_id": seen.image_id if seen else "",
                "project": self.container.project,
                "service": self.container.service,
                "working_dir": self.container.working_dir,
                "config_files": list(self.container.config_files),
            },
            "touches": {
                "mounts": [mount.to_dict() for mount in seen.mounts] if seen else [],
                "networks": list(seen.networks) if seen else list(self.container.networks),
                "ports": self.container.ports,
                "privileged": bool(seen and seen.privileged),
            },
            "restart_policy": policy.to_dict(),
            "cpu_limit": seen.cpu_limit if seen else None,
            "usage": self.usage.to_dict(),
        }


def _sum_sizes(usage: Mapping[str, Mapping[str, Any]]) -> int | None:
    """
    Args:
        usage: ``docker system df`` by kind.

    Returns:
        What Docker holds in all, or None when it said nothing.
    """
    sizes = [row["size"] for row in usage.values() if row.get("size") is not None]
    return sum(sizes) if sizes else None


def _sum_reclaimable(usage: Mapping[str, Mapping[str, Any]]) -> int | None:
    """
    Args:
        usage: ``docker system df`` by kind.

    Returns:
        What a cleanup could give back without touching data, or None when Docker said nothing.
    """
    sizes = [row["reclaimable"] for row in usage.values() if row.get("reclaimable") is not None]
    return sum(sizes) if sizes else None


def _free_space(path: str) -> tuple[int, int, int] | None:
    """
    Args:
        path: Where Docker keeps its data.

    Returns:
        ``(total, used, free)`` of the disk it is on, or None when this process cannot see it (a
        Noust that runs in a container does not see the host's disks).
    """
    if not path:
        return None
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.total, usage.used, usage.free


class DockerGate:
    """
    Ask Docker what it has and change it, as whoever is asking.

    Args:
        runner: Runs every command; the process-wide one when None.
        store: The store Noust registered its applications and database instances in; the
            process-wide one when None. It decides whose an object is.
        registry: What Noust registered, as a function (a test gives one); read from ``store`` on
            each look when None.
        scope: Which teams the principal sees; the ambient one when None.
        sees_app: Whether the principal sees an application; its scope's when None.
        disk: How much room the disk Docker lives on has, as ``(total, used, free)``; looked at
            with :func:`shutil.disk_usage` when None. A test gives its own.
        clock: The time, in epoch seconds; the machine's when None.
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        store: NoustStore | None = None,
        registry: Callable[[], Registry] | None = None,
        scope: Scope | None = None,
        sees_app: Callable[[str], bool] | None = None,
        disk: Callable[[str], tuple[int, int, int] | None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._store = store
        self._registry = registry
        self._scope = scope
        self._sees_app = sees_app
        self._disk = disk or _free_space
        self._clock = clock or time.time

    # -- who is asking and what Noust registered -----------------------------------------------

    @property
    def runner(self) -> CommandRunner:
        """The runner every command goes through."""
        return self._runner or get_runner()

    @property
    def scope(self) -> Scope:
        """The principal's scope: the one given, or the ambient one."""
        return self._scope or current_scope()

    def _sees(self, domain: str) -> bool:
        """
        Args:
            domain: An application's domain.

        Returns:
            Whether the principal sees it (a team's own, or all of them).
        """
        if self._sees_app is not None:
            return self._sees_app(domain)
        from noust.core.store import get_store
        from noust.core.teams.scoped import ScopedStore

        scoped = ScopedStore(self.scope, self._store or get_store())
        return scoped.get_app(domain) is not None

    def _registered(self) -> Registry:
        """
        Returns:
            What Noust registered, read now.
        """
        if self._registry is not None:
            return self._registry()
        from noust.core.store import get_store

        return registry_from_store(self._store or get_store())

    def _visible(self, owner: Owner) -> bool:
        """
        Args:
            owner: Whose an object is.

        Returns:
            Whether the principal may know it exists.
        """
        return visible(owner, restricted=self.scope.restricted, sees_app=self._sees)

    def _judge(
        self, owner: Owner, action: Action, kind: ObjectKind, name: str, *, builtin: bool = False
    ) -> None:
        """
        Judge an action as the principal; see :func:`noust.core.docker.judge.judge`.

        Args:
            owner: Whose the object is.
            action: What is asked.
            kind: Of what.
            name: How it is called.
            builtin: Docker makes it itself.

        Raises:
            DockerNotFoundError: The principal may not see it.
            DockerOwnedError: It is changed through its owner.
        """
        judge(
            owner,
            action,
            kind,
            name,
            restricted=self.scope.restricted,
            sees_app=self._sees,
            builtin=builtin,
        )

    # -- the engine ------------------------------------------------------------------------

    def engine(self) -> engine_module.EngineInfo:
        """
        Returns:
            Which Docker there is: versions, API, Compose, image store, whose the socket is.
        """
        return engine_module.read_engine(self.runner)

    def doctor(self) -> engine_module.EngineReport:
        """
        Returns:
            The engine in sentences: what is, and what does not fit in it.
        """
        return engine_module.diagnose(self.engine())

    # -- one look at the machine -----------------------------------------------------------

    def _look(self) -> tuple[list[Container], OwnerIndex]:
        """
        Ask for every container and resolve their owners, once.

        Returns:
            The containers, each with its owner, and the index that answers for the other objects.

        Raises:
            DockerUnavailableError: Docker cannot be asked.
        """
        listed = reading.list_containers(self.runner)
        index = OwnerIndex(self._registered(), listed)
        return [replace(c, owner=index.of_container(c)) for c in listed], index

    def owner_of(self, thing: Container | Image | Volume | Network | ComposeProject) -> Owner:
        """
        Whose an object is, by what Noust registered.

        Args:
            thing: Any object of this package's models.

        Returns:
            Its owner. One look at the containers is asked for, whatever the kind of object.

        Raises:
            DockerUnavailableError: Docker cannot be asked.
        """
        _containers, index = self._look()
        if isinstance(thing, Container):
            return index.of_container(thing)
        if isinstance(thing, Image):
            return index.of_image(thing)
        if isinstance(thing, Volume):
            return index.of_volume(thing)
        if isinstance(thing, Network):
            return index.of_network(thing)
        return index.of_project(thing.name, thing.working_dir)

    # -- listing ---------------------------------------------------------------------------

    def containers(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        owner: OwnerKind | None = None,
        app: str | None = None,
        state: str | None = None,
        project: str | None = None,
        search: str | None = None,
    ) -> Page[Container]:
        """
        List the containers the principal sees, by name.

        Args:
            offset: Where the page starts.
            limit: How many at most (1 to 1000); all when None.
            owner: Only those of this kind of owner.
            app: Only those of the application with this domain.
            state: Only those in this state (``running``, ``exited``...).
            project: Only those of this Compose project.
            search: Only those whose name or image contains this text, any case.

        Returns:
            The page, with the total after the filters.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, _index = self._look()
        found = [c for c in owned if self._visible(c.owner)]
        if owner is not None:
            found = [c for c in found if c.owner.kind is owner]
        if app is not None:
            found = [c for c in found if c.owner.kind is OwnerKind.APP and c.owner.subject == app]
        if state is not None:
            found = [c for c in found if c.state == state]
        if project is not None:
            found = [c for c in found if c.project == project]
        if search:
            needle = search.lower()
            found = [c for c in found if needle in c.name.lower() or needle in c.image.lower()]
        return _page(sorted(found, key=lambda c: c.name), offset, limit)

    def images(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        owner: OwnerKind | None = None,
        search: str | None = None,
    ) -> Page[Image]:
        """
        List the images the principal sees, with who runs each and whether it is a way back.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.
            owner: Only those of this kind of owner.
            search: Only those whose name, id or the name of a container that uses it contains this
                text, any case.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = _cut(
            self._images_of(owned, index),
            owner,
            search,
            lambda i: [*i.tags, i.id, *i.used_by],
        )
        return _page(found, offset, limit)

    def _images_of(self, owned: Sequence[Container], index: OwnerIndex) -> list[Image]:
        """
        Args:
            owned: Every container, with its owner.
            index: Whose each object is.

        Returns:
            The images the principal sees, by name, each with who runs it.
        """
        found = []
        for image in reading.list_images(self.runner):
            users = tuple(sorted(c.name for c in owned if self._runs(c.image, image)))
            image = replace(image, used_by=users)
            image = replace(image, owner=index.of_image(image), way_back=index.is_way_back(image))
            if self._visible(image.owner):
                found.append(image)
        return sorted(found, key=lambda i: (i.tags or (i.id,))[0])

    @staticmethod
    def _runs(reference: str, image: Image) -> bool:
        """
        Args:
            reference: The image a container was made from, as it was asked for.
            image: An image.

        Returns:
            Whether the container runs it: by a name it has or by its id.
        """
        if with_default_tag(reference) in image.tags:
            return True
        bare = image.id.removeprefix("sha256:")
        asked = reference.removeprefix("sha256:")
        return len(asked) >= 12 and bare.startswith(asked)

    def volumes(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        owner: OwnerKind | None = None,
        search: str | None = None,
    ) -> Page[Volume]:
        """
        List the volumes the principal sees, with who mounts each. Sizes are not measured here.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.
            owner: Only those of this kind of owner.
            search: Only those whose name or the name of a container that mounts it contains this
                text, any case.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = _cut(self._volumes_of(owned, index), owner, search, lambda v: [v.name, *v.used_by])
        return _page(found, offset, limit)

    def _volumes_of(self, owned: Sequence[Container], index: OwnerIndex) -> list[Volume]:
        """
        Args:
            owned: Every container, with its owner.
            index: Whose each object is.

        Returns:
            The volumes the principal sees, by name, each with who mounts it.
        """
        found = []
        for volume in reading.list_volumes(self.runner):
            users = tuple(sorted(c.name for c in owned if volume.name in c.mounts))
            volume = replace(volume, used_by=users)
            volume = replace(volume, owner=index.of_volume(volume))
            if self._visible(volume.owner):
                found.append(volume)
        return sorted(found, key=lambda v: v.name)

    def volume_sizes(self) -> dict[str, int]:
        """
        Measure the volumes the principal sees: a call of its own, because Docker walks each one.

        Returns:
            The size in bytes of each volume it could measure.

        Raises:
            DockerUnavailableError: Docker cannot be asked.
        """
        seen = {volume.name for volume in self.volumes().items}
        return {
            name: size for name, size in reading.volume_sizes(self.runner).items() if name in seen
        }

    def networks(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        owner: OwnerKind | None = None,
        search: str | None = None,
    ) -> Page[Network]:
        """
        List the networks the principal sees, with the containers on each.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.
            owner: Only those of this kind of owner.
            search: Only those whose name or the name of a container on it contains this text, any
                case.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = _cut(
            self._networks_of(owned, index), owner, search, lambda n: [n.name, *n.containers]
        )
        return _page(found, offset, limit)

    def _networks_of(self, owned: Sequence[Container], index: OwnerIndex) -> list[Network]:
        """
        Args:
            owned: Every container, with its owner.
            index: Whose each object is.

        Returns:
            The networks the principal sees, by name, each with the containers on it.
        """
        found = []
        for network in reading.list_networks(self.runner):
            on_it = tuple(sorted(c.name for c in owned if network.name in c.networks))
            network = replace(network, containers=on_it)
            network = replace(network, owner=index.of_network(network))
            if self._visible(network.owner):
                found.append(network)
        return sorted(found, key=lambda n: n.name)

    def projects(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        owner: OwnerKind | None = None,
        search: str | None = None,
    ) -> Page[ComposeProject]:
        """
        List the Compose projects the principal sees, as their containers show them.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.
            owner: Only those of this kind of owner.
            search: Only those whose name, directory or the name of a container of it contains this
                text, any case.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = _cut(
            [project for project, _members in self._projects_of(owned, index)],
            owner,
            search,
            lambda p: [p.name, p.working_dir, *p.containers],
        )
        return _page(found, offset, limit)

    def _projects_of(
        self,
        owned: Sequence[Container],
        index: OwnerIndex,
        seen: Mapping[str, inspect.Inspected] | None = None,
    ) -> list[tuple[ComposeProject, list[Container]]]:
        """
        Group the containers into the Compose projects the principal sees, each with its state.

        What ended is inspected (once, whatever the number) to know whether it was a job that did
        its work or a service that stopped: the listing cannot tell, and a stack that holds a setup
        step that finished is not failing.

        Args:
            owned: Every container, with its owner.
            index: Whose each object is.
            seen: What ``inspect`` already said of the containers, when a caller has asked.

        Returns:
            Each project with its containers, by name.
        """
        grouped: dict[str, list[Container]] = {}
        for container in owned:
            if container.project:
                grouped.setdefault(container.project, []).append(container)
        if seen is None:
            ended = [c.id for members in grouped.values() for c in members if not c.running]
            seen = inspect.read_inspect(self.runner, ended)
        found = []
        for name, members in grouped.items():
            where = next((c.working_dir for c in members if c.working_dir), "")
            kinds = []
            for member in members:
                info = seen.get(member.id)
                state = states.classify_container(
                    member,
                    exit_code=info.exit_code if info else None,
                    oom=bool(info and info.oom_killed),
                )
                kinds.append((state, info.restart_policy.name if info else "no"))
            verdict, running, workers = states.stack_state(kinds)
            project = ComposeProject(
                name=name,
                working_dir=where,
                config_files=next((c.config_files for c in members if c.config_files), ()),
                containers=tuple(sorted(c.name for c in members)),
                running=running,
                workers=workers,
                state=verdict,
                owner=index.of_project(name, where),
            )
            if self._visible(project.owner):
                found.append((project, sorted(members, key=lambda c: c.name)))
        return sorted(found, key=lambda pair: pair[0].name)

    def project(self, name: str) -> tuple[ComposeProject, tuple[Container, ...]]:
        """
        Find one Compose project by its name, with its containers.

        Args:
            name: The project's name.

        Returns:
            The project and its containers, by name.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = [
            (project, members)
            for project, members in self._projects_of(owned, index)
            if project.name == name and _NAME.fullmatch(name)
        ]
        if len(found) != 1:
            raise not_found(ObjectKind.PROJECT, name)
        return found[0][0], tuple(found[0][1])

    def can(
        self,
        thing: Container | Image | Volume | Network | ComposeProject,
        *,
        manage: bool = True,
        console: bool = True,
    ) -> Can:
        """
        What the principal may do to an object, for a row of the console to offer.

        It is the judgement an action goes through, asked once for each action, so what is offered
        and what is let through cannot differ.

        Args:
            thing: A container, an image, a volume or a network, with its owner.
            manage: Whether the principal may change Docker at all (the permission to). A principal
                that may not is offered nothing and told nothing of who owns what.
            console: Whether the principal may open a shell in this container (what it needs
                depends on whose it is, which the caller knows from ``thing.owner``).

        Returns:
            What is allowed, and where the rest is done.
        """
        if not manage:
            return Can(blocked="permission")
        users: Sequence[str] = ()
        if isinstance(thing, Container):
            kind, name = ObjectKind.CONTAINER, thing.name
        elif isinstance(thing, Image):
            kind, name = ObjectKind.IMAGE, thing.tags[0] if thing.tags else thing.id
            users = thing.used_by
        elif isinstance(thing, Volume):
            kind, name = ObjectKind.VOLUME, thing.name
            users = thing.used_by
        elif isinstance(thing, ComposeProject):
            kind, name = ObjectKind.PROJECT, thing.name
        else:
            kind, name = ObjectKind.NETWORK, thing.name
            users = thing.containers
        verdict = can(
            thing.owner,
            kind,
            name,
            restricted=self.scope.restricted,
            sees_app=self._sees,
            builtin=isinstance(thing, Network) and thing.builtin,
        )
        if not console:
            verdict = replace(verdict, console=False)
        if users and verdict.remove:
            # Docker refuses what something still uses, and a button that ends there is a button
            # that should not have been drawn.
            return replace(verdict, remove=False, blocked="in_use")
        return verdict

    # -- finding one -----------------------------------------------------------------------

    def container(self, ref: str) -> Container:
        """
        Find a container by its name or by its id (the twelve digits Docker shows, or all).

        Args:
            ref: How the person named it.

        Returns:
            The container, as Docker listed it, with its owner.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it (the same answer).
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, _index = self._look()
        return cast(Container, self._pick(ObjectKind.CONTAINER, ref, owned, self._is_container))

    def image(self, ref: str) -> Image:
        """
        Find an image by one of its names or by its id.

        Args:
            ref: How the person named it.

        Returns:
            The image, with who runs it.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        return cast(Image, self._pick(ObjectKind.IMAGE, ref, self.images().items, self._is_image))

    def volume(self, name: str) -> Volume:
        """
        Find a volume by its name.

        Args:
            name: The volume's name.

        Returns:
            The volume, with who mounts it.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        return cast(
            Volume, self._pick(ObjectKind.VOLUME, name, self.volumes().items, self._is_volume)
        )

    def network(self, ref: str) -> Network:
        """
        Find a network by its name or its id.

        Args:
            ref: How the person named it.

        Returns:
            The network, with the containers on it.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        return cast(
            Network, self._pick(ObjectKind.NETWORK, ref, self.networks().items, self._is_network)
        )

    @staticmethod
    def _is_container(ref: str, c: Container) -> bool:
        return bool(_NAME.fullmatch(ref)) and (
            c.name == ref or (_HEX_ID.fullmatch(ref) is not None and c.id.startswith(ref))
        )

    @staticmethod
    def _is_image(ref: str, image: Image) -> bool:
        if not _IMAGE_REF.fullmatch(ref):
            return False
        if ref in image.tags:
            return True
        return _HEX_ID.fullmatch(ref) is not None and image.id.removeprefix("sha256:").startswith(
            ref.removeprefix("sha256:")
        )

    @staticmethod
    def _is_volume(ref: str, volume: Volume) -> bool:
        return bool(_NAME.fullmatch(ref)) and volume.name == ref

    @staticmethod
    def _is_network(ref: str, network: Network) -> bool:
        return bool(_NAME.fullmatch(ref)) and (
            network.name == ref
            or (_HEX_ID.fullmatch(ref) is not None and network.id.startswith(ref))
        )

    def _pick(
        self,
        kind: ObjectKind,
        ref: str,
        candidates: Sequence[Any],
        matches: Callable[[str, Any], bool],
    ) -> Any:
        """
        Pick the object a person named out of what Docker listed.

        Args:
            kind: Of what kind it is, for the words of the answer.
            ref: How it was named.
            candidates: What Docker listed, owners resolved and invisible ones already left out.
            matches: Whether a reference names an object.

        Returns:
            The object.

        Raises:
            DockerNotFoundError: Nothing was named, or what was named is not there for this principal.
        """
        found = [item for item in candidates if matches(ref, item) and self._visible(item.owner)]
        if len(found) != 1:
            raise not_found(kind, ref)
        return found[0]

    # -- a container from the inside ---------------------------------------------------------

    def container_for_console(self, ref: str) -> Container:
        """
        Find a container a shell may be opened in.

        The container is resolved to its owner and judged (:attr:`Action.CONSOLE`) before anything
        else is asked of it, so the id of a container cannot reach what its name could not.

        Args:
            ref: Its name or id.

        Returns:
            The container, with its owner.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It is part of Noust, which is never opened.
            DockerUnavailableError: Docker cannot be asked.
        """
        target = self.container(ref)
        self._judge(target.owner, Action.CONSOLE, ObjectKind.CONTAINER, target.name)
        return target

    def container_detail(self, ref: str) -> ContainerDetail:
        """
        Look at one container: why it is the way it is, where it comes from and what it uses.

        Args:
            ref: Its name or id.

        Returns:
            The container, its state, what ``inspect`` says, its consumption and, for one that does
            not run well, its last lines.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        container = self.container(ref)
        seen = inspect.read_inspect(self.runner, [container.id]).get(container.id)
        if seen is not None:
            seen = self._masked(container, seen)
        state = states.classify_container(
            container,
            exit_code=seen.exit_code if seen else None,
            oom=bool(seen and seen.oom_killed),
        )
        recent = 0
        if seen is None or seen.restart_count > 0:
            recent = events.recent_restarts(self.runner, now=self._clock()).get(container.id, 0)
        uptime = None
        if seen and seen.started_at and container.state == "running":
            uptime = self._clock() - seen.started_at.timestamp()
        usage = stats.read_usage(
            self.runner,
            container.id,
            state=container.state,
            uptime_seconds=uptime,
            memory_limited=(seen.memory_limit_bytes is not None) if seen else None,
        )
        last = ""
        if state.problem or not container.running:
            last = self._lines(container, WHY_LINES, timestamps=False)
        return ContainerDetail(container, state, seen, usage, last, recent)

    def _masked(self, container: Container, seen: inspect.Inspected) -> inspect.Inspected:
        """
        Mask the credentials in the words an inspection carries that a program wrote.

        What a health check printed and what Docker said of a start that failed are text that a
        program produced, like a log, and can hold what the program was given.

        Args:
            container: The container the words are about.
            seen: What ``inspect`` said of it.

        Returns:
            The same, with those words masked.
        """
        scrubber = self._scrubber_for(container)
        health = seen.health
        if health is not None:
            health = replace(health, last_output=scrubber.scrub(health.last_output))
        return replace(seen, health=health, error=scrubber.scrub(seen.error))

    def _lines(self, container: Container, tail: int, *, timestamps: bool) -> str:
        """
        Read the last lines of a container's log.

        Args:
            container: A container this principal sees.
            tail: How many lines.
            timestamps: Ask Docker for the time of each line.

        Returns:
            The lines, both streams together, with the credentials in them masked: those of its
            application when it is one, Noust's own, and whatever looks like one. Nothing else is
            changed.
        """
        collected: list[str] = []
        self.runner.stream(
            self.logs_argv(container.id, tail=tail, timestamps=timestamps),
            on_line=collected.append,
            timeout=LOGS_TIMEOUT,
        )
        return self._scrubber_for(container).scrub("\n".join(collected)[-MAX_LOG_CHARS:])

    @staticmethod
    def _scrubber_for(container: Container) -> Scrubber:
        """
        Args:
            container: The container a text came from.

        Returns:
            What masks the credentials in it.
        """
        return scrubber_for(
            container.owner.subject if container.owner.kind is OwnerKind.APP else None
        )

    @staticmethod
    def logs_argv(
        container_id: str, *, tail: int, timestamps: bool = True, follow: bool = False
    ) -> list[str]:
        """
        Args:
            container_id: The id of a container, as Docker listed it.
            tail: How many lines from the end.
            timestamps: Ask for the time of each line.
            follow: Keep going as lines are written.

        Returns:
            The command that reads its log.
        """
        return [
            "docker",
            "logs",
            *(["--follow"] if follow else []),
            "--tail",
            str(tail),
            *(["--timestamps"] if timestamps else []),
            container_id,
        ]

    @staticmethod
    def _checked_tail(tail: int) -> int:
        """
        Args:
            tail: How many lines a person asked for.

        Returns:
            The same.

        Raises:
            ValidationError: It is not between 1 and :data:`MAX_LOG_LINES`.
        """
        if not 1 <= tail <= MAX_LOG_LINES:
            raise ValidationError(
                f"A log is read from 1 to {MAX_LOG_LINES} lines at a time", field="tail"
            )
        return tail

    def container_logs(self, ref: str, *, tail: int = 200) -> str:
        """
        Read the end of a container's log, as it wrote it with the time of each line.

        Args:
            ref: Its name or id.
            tail: How many lines from the end (1 to 5000).

        Returns:
            The lines, masked and nothing else changed.

        Raises:
            ValidationError: The tail makes no sense.
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        self._checked_tail(tail)
        return self._lines(self.container(ref), tail, timestamps=True)

    def follow_container_logs(
        self, ref: str, on_line: Callable[[str], None], *, tail: int = 100, seconds: int = 3600
    ) -> Container:
        """
        Follow a container's log, line by line, until the container ends, the time is up or the
        enclosing cancellation (:func:`noust.core.runner.cancellable`) is set.

        The container is resolved to its owner first: a principal that may not see it gets the
        answer for one that does not exist, and nothing is started.

        Args:
            ref: Its name or id.
            on_line: Called with each line, masked.
            tail: How many lines from the end to start with (1 to 5000).
            seconds: The longest it follows.

        Returns:
            The container that was followed.

        Raises:
            ValidationError: The tail makes no sense.
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        self._checked_tail(tail)
        container = self.container(ref)
        scrubber = self._scrubber_for(container)
        self.runner.stream(
            self.logs_argv(container.id, tail=tail, follow=True),
            on_line=lambda line: on_line(scrubber.scrub(line)),
            timeout=seconds,
        )
        return container

    @staticmethod
    def exec_argv(container_id: str, shell: str) -> list[str]:
        """
        Args:
            container_id: The id of a container, as Docker listed it.
            shell: A shell by name (``sh``, ``bash``...): never a path and never a command line.

        Returns:
            The command that opens that shell in it, on a terminal.
        """
        return ["docker", "exec", "-it", "-e", "TERM", "-e", "COLORTERM", container_id, shell]

    # -- the machine in short ------------------------------------------------------------------

    def require_usable(
        self, report: engine_module.EngineReport | None = None
    ) -> engine_module.EngineReport:
        """
        Make sure containers can be looked at and controlled with this Docker.

        Args:
            report: The engine's report, when a caller has just asked for it.

        Returns:
            The report.

        Raises:
            DockerUnavailableError: Docker is not there, or does not answer, or is below what Noust
                speaks; the doctor's own sentence says which.
        """
        report = report or self.doctor()
        if not report.usable:
            raise self._unusable(report)
        return report

    def summary(self, report: engine_module.EngineReport | None = None) -> summary_module.Summary:
        """
        Say what asks for the operator, worst first, and how much there is of each thing.

        One look at the containers, one ``inspect`` of all of them and one question about restarts:
        a handful of processes however big the machine is.

        Args:
            report: The engine's report, when a caller has just asked for it.

        Returns:
            The summary.

        Raises:
            DockerUnavailableError: Docker is not there, or does not answer, or is below what Noust
                speaks.
        """
        report = self.require_usable(report)
        owned, index = self._look()
        shown = [c for c in owned if self._visible(c.owner)]
        seen = inspect.read_inspect(self.runner, [c.id for c in shown])
        restarts = events.recent_restarts(self.runner, now=self._clock())
        projects = self._projects_of(owned, index, seen)
        usage = reading.disk_usage(self.runner) or {}
        room = self._disk(report.info.root_dir) if report.info.root_dir else None
        disk = summary_module.DiskInfo(
            docker_bytes=_sum_sizes(usage),
            reclaimable_bytes=_sum_reclaimable(usage),
            free_bytes=room[2] if room else None,
            total_bytes=room[0] if room else None,
            path=report.info.root_dir,
        )
        images = self._images_of(owned, index)
        found = self._records_for(shown, images)
        return summary_module.Summary(
            engine=report,
            counts=summary_module.counts(
                shown,
                seen,
                stacks=len(projects),
                unowned_stacks=sum(1 for project, _ in projects if project.owner.unowned),
                images=len(images),
                volumes=len(self._volumes_of(owned, index)),
                networks=len(self._networks_of(owned, index)),
            ),
            attention=summary_module.attention(
                shown,
                seen,
                restarts,
                disk,
                versions=summary_module.version_attention(found.values(), set(found)),
            ),
            disk=disk,
        )

    def version_records(self) -> dict[str, VersionRecord]:
        """
        Say what the last look at the registries found of the images the principal's containers run.

        Nothing is asked of a registry. What a look found is read as of now: a version it called new that
        already runs is current.

        Returns:
            The records by image reference, only for images a container the principal sees runs.

        Raises:
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        shown = [c for c in owned if self._visible(c.owner)]
        return self._records_for(shown, self._images_of(owned, index))

    def _records_for(
        self, shown: Sequence[Container], images: Sequence[Image]
    ) -> dict[str, VersionRecord]:
        """
        Args:
            shown: The containers the principal sees.
            images: The images, with who runs each.

        Returns:
            The records of the images those containers run, settled against what runs now.
        """
        from noust.core.store import get_store

        references = image_sources.registry_references(shown, self._store or get_store())
        in_use = set(references.values())
        records = [
            item
            for item in ImageVersionStore(self._store or get_store()).all()
            if item.reference in in_use
        ]
        image_of = {name: image.id for image in images for name in image.used_by}
        ids = {references[name]: image_of[name] for name in references if name in image_of}
        return {item.reference: item for item in registry.settled(self.runner, records, ids)}

    @staticmethod
    def _unusable(report: engine_module.EngineReport) -> DockerUnavailableError:
        """
        Args:
            report: The engine's report, whose Docker cannot be used.

        Returns:
            The error that says so in the doctor's own words.
        """
        limit = report.limits[0].text if report.limits else "Docker cannot be used here"
        return DockerUnavailableError("Docker cannot be used on this server", details=limit)

    def restart_loss(self) -> tuple[restart_loss_module.Lost, ...]:
        """
        Find what a restart of the machine would leave down.

        Returns:
            The containers that would not come back, with why and how each is fixed.

        Raises:
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, _index = self._look()
        shown = [c for c in owned if self._visible(c.owner)]
        seen = inspect.read_inspect(self.runner, [c.id for c in shown])
        return restart_loss_module.find_lost(shown, seen)

    # -- what is not an object -------------------------------------------------------------

    @staticmethod
    def remove_image_argv(image_id: str) -> list[str]:
        """
        Args:
            image_id: The id of an image, as Docker listed it.

        Returns:
            The command that removes it, without ``--force``: Docker refuses what a container uses.
        """
        return ["docker", "image", "rm", image_id]

    @staticmethod
    def prune_images_argv() -> list[str]:
        """
        Returns:
            The command that removes the images with no name and no container: the dangling ones
            only (never ``-a``, which would take the way back of every application).
        """
        return ["docker", "image", "prune", "-f"]

    @staticmethod
    def prune_build_cache_argv(hours: int = BUILD_CACHE_HOURS) -> list[str]:
        """
        Args:
            hours: Only the cache older than this is removed.

        Returns:
            The command that prunes the build cache by age (never ``system prune``, which takes
            volumes with it).
        """
        return ["docker", "builder", "prune", "-f", "--filter", f"until={hours}h"]

    # -- changing --------------------------------------------------------------------------

    def start_container(self, ref: str) -> Container:
        """
        Start a container nobody of Noust owns.

        Args:
            ref: Its name or id.

        Returns:
            The container, as it was before.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust: use its door.
            DockerError: Docker refused; its own words are in ``output``.
        """
        target = self.container(ref)
        self._judge(target.owner, Action.START, ObjectKind.CONTAINER, target.name)
        self._run(["docker", "start", target.id], f"start {target.name}")
        return target

    def stop_container(self, ref: str) -> Container:
        """
        Stop a container nobody of Noust owns, giving it :data:`STOP_GRACE` seconds.

        Args:
            ref: Its name or id.

        Returns:
            The container, as it was before.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust: use its door.
            DockerError: Docker refused; its own words are in ``output``.
        """
        target = self.container(ref)
        self._judge(target.owner, Action.STOP, ObjectKind.CONTAINER, target.name)
        self._run(["docker", "stop", "--time", str(STOP_GRACE), target.id], f"stop {target.name}")
        return target

    def restart_container(self, ref: str) -> Container:
        """
        Restart a container nobody of Noust owns.

        Args:
            ref: Its name or id.

        Returns:
            The container, as it was before.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust: use its door.
            DockerError: Docker refused; its own words are in ``output``.
        """
        target = self.container(ref)
        self._judge(target.owner, Action.RESTART, ObjectKind.CONTAINER, target.name)
        self._run(
            ["docker", "restart", "--time", str(STOP_GRACE), target.id], f"restart {target.name}"
        )
        return target

    def start_stack(self, name: str) -> ComposeProject:
        """
        Start the containers of a stack nobody of Noust owns, in the order Compose gives.

        Args:
            name: The Compose project's name.

        Returns:
            The stack, as it was before.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust: use its door.
            DockerError: Docker refused; its own words are in ``output``.
        """
        return self._act_on_stack(name, Action.START, ["start"])

    def stop_stack(self, name: str) -> ComposeProject:
        """
        Stop the containers of a stack nobody of Noust owns, giving each :data:`STOP_GRACE` seconds.

        Args:
            name: The Compose project's name.

        Returns:
            The stack, as it was before.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust: use its door.
            DockerError: Docker refused; its own words are in ``output``.
        """
        return self._act_on_stack(name, Action.STOP, ["stop", "--timeout", str(STOP_GRACE)])

    def restart_stack(self, name: str) -> ComposeProject:
        """
        Restart the containers of a stack nobody of Noust owns.

        Args:
            name: The Compose project's name.

        Returns:
            The stack, as it was before.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust: use its door.
            DockerError: Docker refused; its own words are in ``output``.
        """
        return self._act_on_stack(name, Action.RESTART, ["restart", "--timeout", str(STOP_GRACE)])

    def _act_on_stack(self, name: str, action: Action, verb: Sequence[str]) -> ComposeProject:
        """
        Judge an action on a stack and have Compose do it.

        The stack is found among the projects Docker's own containers name, and Compose is given that
        name: what was typed never reaches the command. Compose does it, and not a start of each
        container, because it knows what a service waits for.

        Args:
            name: The Compose project's name, as the person typed it.
            action: What is asked.
            verb: Compose's subcommand and its options.

        Returns:
            The stack, as it was before.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust.
            DockerError: Docker refused.
        """
        project, _members = self.project(name)
        self._judge(project.owner, action, ObjectKind.PROJECT, project.name)
        result = self.runner.run(
            ["docker", "compose", "-p", project.name, *verb], timeout=STACK_TIMEOUT
        )
        if not result.success:
            raise DockerRefusedError(
                f"Docker could not {action.value} {project.name}",
                details="Docker's own message is below.",
                output=(result.stderr or result.stdout).strip() or f"exit code {result.exit_code}",
            )
        return project

    def remove_container(self, ref: str, *, force: bool = False) -> Removal:
        """
        Remove a container nobody of Noust owns. Its anonymous volumes are left where they are.

        Args:
            ref: Its name or id.
            force: Remove it even if it is running.

        Returns:
            What was removed.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application, an engine or Noust: use its door.
            DockerError: Docker refused; its own words are in ``output``.
        """
        target = self.container(ref)
        self._judge(target.owner, Action.REMOVE, ObjectKind.CONTAINER, target.name)
        argv = ["docker", "rm", *(["--force"] if force else []), target.id]
        self._run(argv, f"remove {target.name}")
        return Removal(ObjectKind.CONTAINER, target.name)

    def remove_image(self, ref: str) -> Removal:
        """
        Remove an image nobody keeps. An image a container uses, running or not, stays.

        Args:
            ref: One of its names, or its id.

        Returns:
            What was removed.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: An application keeps it (its way back), or it is an engine's: use
                the door the error names.
            ValidationError: A container still uses it.
            DockerError: Docker refused; its own words are in ``output``.
        """
        target = self.image(ref)
        label = target.tags[0] if target.tags else target.id
        self._judge(target.owner, Action.REMOVE, ObjectKind.IMAGE, label)
        if target.used_by:
            raise ValidationError(
                f"The image {label} is used by {', '.join(target.used_by)}",
                details="Remove the containers that use it first.",
            )
        self._run(self.remove_image_argv(target.id), f"remove {label}")
        return Removal(ObjectKind.IMAGE, label, target.size_bytes)

    def remove_volume(
        self, name: str, *, confirm_name: str | None = None, known_size: int | None = None
    ) -> Removal:
        """
        Remove a volume: it must be named a second time, and what it holds is said first.

        Args:
            name: The volume's name.
            confirm_name: The name again, typed by the person. Without it, or when it is another
                name, nothing is removed and the error says what is at stake.
            known_size: How much it holds, when the caller has just measured it: measuring walks
                every volume of the machine, and a cleanup of several should not do it for each.

        Returns:
            What was removed, with the size it held when it could be measured.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: It belongs to an application or an engine: use its door.
            ValidationError: A container mounts it.
            ConfirmationRequired: It was not named a second time.
            DockerError: Docker refused; its own words are in ``output``.
        """
        target = self.volume(name)
        self._judge(target.owner, Action.REMOVE, ObjectKind.VOLUME, target.name)
        if target.used_by:
            raise ValidationError(
                f"The volume {target.name} is mounted by {', '.join(target.used_by)}",
                details="Remove those containers first, or leave the volume where it is.",
            )
        size = (
            known_size
            if known_size is not None
            else reading.volume_sizes(self.runner).get(target.name)
        )
        if confirm_name != target.name:
            raise ConfirmationRequired(
                [
                    f"Removing the volume {target.name} deletes its data ({_human(size)}), and "
                    "it cannot be undone. Type its name to confirm."
                ],
                field="confirm_name",
            )
        self._run(["docker", "volume", "rm", target.name], f"remove {target.name}")
        return Removal(ObjectKind.VOLUME, target.name, size)

    def remove_network(self, ref: str) -> Removal:
        """
        Remove a network nobody of Noust owns and Docker did not make.

        Args:
            ref: Its name or id.

        Returns:
            What was removed.

        Raises:
            DockerNotFoundError: There is none, or the principal may not see it.
            DockerOwnedError: Docker made it, or it belongs to an application or an engine.
            DockerError: Docker refused (a container is on it); its own words are in ``output``.
        """
        target = self.network(ref)
        self._judge(
            target.owner, Action.REMOVE, ObjectKind.NETWORK, target.name, builtin=target.builtin
        )
        self._run(["docker", "network", "rm", target.id], f"remove {target.name}")
        return Removal(ObjectKind.NETWORK, target.name)

    # -- cleaning --------------------------------------------------------------------------

    def cleanup_plan(self) -> cleanup_module.CleanupPlan:
        """
        Work out what a cleanup would do, without doing it (see :mod:`noust.core.docker.cleanup`).

        Returns:
            The plan: what would go, whose it is, how much it holds and why, what stays, and the
            token it is applied with. Empty for a principal that sees only what teams own: nothing
            that goes into a plan is a team's.

        Raises:
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        shown = [c for c in owned if self._visible(c.owner)]
        images = self._images_of(owned, index)
        volumes = self._volumes_of(owned, index)
        networks = self._networks_of(owned, index)
        sizes: dict[str, int] = {}
        if any(v.owner.kind is OwnerKind.NOBODY and not v.used_by for v in volumes):
            sizes = reading.volume_sizes(self.runner)
        cache = None
        if not self.scope.restricted:
            cache = (
                (reading.disk_usage(self.runner) or {}).get("Build Cache", {}).get("reclaimable")
            )
        return cleanup_module.build_plan(
            images=images,
            containers=shown,
            volumes=volumes,
            networks=networks,
            volume_sizes=sizes,
            cache_bytes=cache,
        )

    def prepare_cleanup(
        self,
        token: str,
        select: Sequence[str],
        *,
        confirm_names: Mapping[str, str] | None = None,
    ) -> list[cleanup_module.PlanItem]:
        """
        Check that what a person chose is what the plan of now holds, before anything is touched.

        The plan is worked out again and must be the one the token names; what is chosen must be in
        it, and each volume chosen must have been named by the person.

        Args:
            token: The token of the plan that was shown.
            select: The ids of what to remove: ``PlanItem.id`` of items of the plan.
            confirm_names: For each volume chosen, its name as the person typed it.

        Returns:
            The items chosen, in the order they go.

        Raises:
            DockerPlanChangedError: The plan is not the one that was shown; nothing was changed.
            ValidationError: Something chosen is not in the plan, or nothing was chosen.
            ConfirmationRequired: A volume was chosen and not named; nothing was changed.
            DockerUnavailableError: Docker cannot be asked.
        """
        plan = self.cleanup_plan()
        if plan.token != token:
            raise DockerPlanChangedError(
                "The cleanup plan is not the one that was shown",
                details="What is in it changed since you looked. Look at the plan again.",
                plan=plan,
            )
        by_id = {item.id: item for item in plan.items}
        chosen = []
        for ident in dict.fromkeys(select):
            if ident not in by_id:
                raise ValidationError(
                    f"{ident!r} is not in the cleanup plan",
                    details="Choose from what the plan shows.",
                    field="select",
                )
            chosen.append(by_id[ident])
        if not chosen:
            raise ValidationError(
                "Nothing was chosen to clean up", details="Tick what to remove.", field="select"
            )
        typed = confirm_names or {}
        unnamed = [item for item in chosen if item.type_name and typed.get(item.name) != item.name]
        if unnamed:
            raise ConfirmationRequired(
                [
                    f"Removing the volume {item.name} deletes its data ({_human(item.size_bytes)}), "
                    "and it cannot be undone. Type its name to confirm."
                    for item in unnamed
                ],
                field="confirm_names",
            )
        return chosen

    def apply_cleanup(
        self,
        token: str,
        select: Sequence[str],
        *,
        confirm_names: Mapping[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> cleanup_module.CleanupResult:
        """
        Apply a cleanup plan as it was shown.

        What is chosen is checked first (:meth:`prepare_cleanup`). Then each thing is removed through
        the gate, which judges its owner once more, and one that Docker refuses is told and does not
        stop the rest.

        Args:
            token: The token of the plan that was shown.
            select: The ids of what to remove: ``PlanItem.id`` of items of the plan.
            confirm_names: For each volume chosen, its name as the person typed it.
            on_line: Told each thing that was removed, as a sentence.

        Returns:
            What was removed, what Docker refused and what was freed.

        Raises:
            DockerPlanChangedError: The plan is not the one that was shown; nothing was changed.
            ValidationError: Something chosen is not in the plan, or nothing was chosen.
            ConfirmationRequired: A volume was chosen and not named; nothing was changed.
            DockerUnavailableError: Docker cannot be asked.
        """
        say = on_line or (lambda line: None)
        chosen = self.prepare_cleanup(token, select, confirm_names=confirm_names)
        result = cleanup_module.CleanupResult()
        for item in chosen:
            try:
                freed = self._remove_planned(item, say)
            except (DockerError, ValidationError) as exc:
                result.failed.append(
                    {
                        "kind": item.kind,
                        "name": item.name,
                        "error": (getattr(exc, "output", "") or exc.message).strip(),
                    }
                )
                continue
            result.removed.append(
                {"kind": item.kind, "name": item.name, "size_bytes": item.size_bytes}
            )
            result.freed_bytes += freed if freed is not None else (item.size_bytes or 0)
        return result

    def _remove_planned(
        self, item: cleanup_module.PlanItem, say: Callable[[str], None]
    ) -> int | None:
        """
        Remove one thing of a plan.

        Args:
            item: What to remove.
            say: Told that it was removed.

        Returns:
            What the removal said it freed, when it said; None otherwise.

        Raises:
            DockerError: Docker refused; its own words are in ``output``.
            ValidationError: Something still uses it.
        """
        if item.kind == "container":
            self.remove_container(item.id)
        elif item.kind == "image":
            self.remove_image(item.id)
        elif item.kind == "network":
            self.remove_network(item.id)
        elif item.kind == "volume":
            self.remove_volume(item.name, confirm_name=item.name, known_size=item.size_bytes)
        else:
            collected: list[str] = []
            outcome = self.runner.stream(
                self.prune_build_cache_argv(), on_line=collected.append, timeout=CLEANUP_TIMEOUT
            )
            if not outcome.success:
                raise DockerRefusedError(
                    "Docker could not prune the build cache",
                    details="Docker's own message is below.",
                    output="\n".join(collected[-40:]) or outcome.stderr,
                )
            say("Pruned the build cache")
            return cleanup_module.parse_reclaimed("\n".join(collected))
        say(f"Removed {item.kind} {item.name}")
        return None

    def _run(self, argv: list[str], what: str) -> None:
        """
        Run an action Docker was already judged to allow.

        Args:
            argv: The command, made only of what Docker listed.
            what: What it does, for the error.

        Raises:
            DockerError: Docker refused; its own words are in ``output``.
        """
        result = self.runner.run(argv, timeout=ACTION_TIMEOUT)
        if not result.success:
            raise DockerRefusedError(
                f"Docker could not {what}",
                details="Docker's own message is below.",
                output=(result.stderr or result.stdout).strip() or f"exit code {result.exit_code}",
            )
