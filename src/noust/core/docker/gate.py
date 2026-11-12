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
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, TypeVar, cast

from noust.core.docker import engine as engine_module
from noust.core.docker import reading
from noust.core.docker.judge import judge, not_found, visible
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
from noust.core.exceptions import ConfirmationRequired, DockerError, ValidationError
from noust.core.runner import CommandRunner, get_runner
from noust.core.teams.scope import Scope, current_scope

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["DockerGate"]

T = TypeVar("T")

#: Seconds a container is given to stop before Docker kills it.
STOP_GRACE = 30

#: Seconds an action gets: the grace, then the daemon's own work.
ACTION_TIMEOUT = 90

#: The most a page may hold: a console asks for a few dozen.
MAX_PAGE = 1000

#: How old the build cache has to be to be pruned: a week.
BUILD_CACHE_HOURS = 168

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


def _normal(reference: str) -> str:
    """
    Args:
        reference: An image reference as a container was made from it.

    Returns:
        The reference with the tag Docker gives one that has none.
    """
    last = reference.rsplit("/", 1)[-1]
    if "@" in last or ":" in last or reference.startswith("sha256:"):
        return reference
    return f"{reference}:latest"


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
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        store: NoustStore | None = None,
        registry: Callable[[], Registry] | None = None,
        scope: Scope | None = None,
        sees_app: Callable[[str], bool] | None = None,
    ) -> None:
        self._runner = runner
        self._store = store
        self._registry = registry
        self._scope = scope
        self._sees_app = sees_app

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

    def images(self, *, offset: int = 0, limit: int | None = None) -> Page[Image]:
        """
        List the images the principal sees, with who runs each and whether it is a way back.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = []
        for image in reading.list_images(self.runner):
            users = tuple(sorted(c.name for c in owned if self._runs(c.image, image)))
            image = replace(image, used_by=users)
            image = replace(image, owner=index.of_image(image), way_back=index.is_way_back(image))
            if self._visible(image.owner):
                found.append(image)
        return _page(sorted(found, key=lambda i: (i.tags or (i.id,))[0]), offset, limit)

    @staticmethod
    def _runs(reference: str, image: Image) -> bool:
        """
        Args:
            reference: The image a container was made from, as it was asked for.
            image: An image.

        Returns:
            Whether the container runs it: by a name it has or by its id.
        """
        if _normal(reference) in image.tags:
            return True
        bare = image.id.removeprefix("sha256:")
        asked = reference.removeprefix("sha256:")
        return len(asked) >= 12 and bare.startswith(asked)

    def volumes(self, *, offset: int = 0, limit: int | None = None) -> Page[Volume]:
        """
        List the volumes the principal sees, with who mounts each. Sizes are not measured here.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = []
        for volume in reading.list_volumes(self.runner):
            users = tuple(sorted(c.name for c in owned if volume.name in c.mounts))
            volume = replace(volume, used_by=users)
            volume = replace(volume, owner=index.of_volume(volume))
            if self._visible(volume.owner):
                found.append(volume)
        return _page(sorted(found, key=lambda v: v.name), offset, limit)

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

    def networks(self, *, offset: int = 0, limit: int | None = None) -> Page[Network]:
        """
        List the networks the principal sees, with the containers on each.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        found = []
        for network in reading.list_networks(self.runner):
            on_it = tuple(sorted(c.name for c in owned if network.name in c.networks))
            network = replace(network, containers=on_it)
            network = replace(network, owner=index.of_network(network))
            if self._visible(network.owner):
                found.append(network)
        return _page(sorted(found, key=lambda n: n.name), offset, limit)

    def projects(self, *, offset: int = 0, limit: int | None = None) -> Page[ComposeProject]:
        """
        List the Compose projects the principal sees, as their containers show them.

        Args:
            offset: Where the page starts.
            limit: How many at most; all when None.

        Returns:
            The page, by name.

        Raises:
            ValidationError: The page makes no sense.
            DockerUnavailableError: Docker cannot be asked.
        """
        owned, index = self._look()
        grouped: dict[str, list[Container]] = {}
        for container in owned:
            if container.project:
                grouped.setdefault(container.project, []).append(container)
        found = []
        for name, members in grouped.items():
            where = next((c.working_dir for c in members if c.working_dir), "")
            project = ComposeProject(
                name=name,
                working_dir=where,
                config_files=next((c.config_files for c in members if c.config_files), ()),
                containers=tuple(sorted(c.name for c in members)),
                running=sum(1 for c in members if c.running),
                owner=index.of_project(name, where),
            )
            if self._visible(project.owner):
                found.append(project)
        return _page(sorted(found, key=lambda p: p.name), offset, limit)

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

    def remove_volume(self, name: str, *, confirm_name: str | None = None) -> Removal:
        """
        Remove a volume: it must be named a second time, and what it holds is said first.

        Args:
            name: The volume's name.
            confirm_name: The name again, typed by the person. Without it, or when it is another
                name, nothing is removed and the error says what is at stake.

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
        size = reading.volume_sizes(self.runner).get(target.name)
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
            raise DockerError(
                f"Docker could not {what}",
                details="Docker's own message is below.",
                output=(result.stderr or result.stdout).strip() or f"exit code {result.exit_code}",
            )
