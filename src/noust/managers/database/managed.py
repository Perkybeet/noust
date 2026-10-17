# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database instances Noust runs itself: another version of an engine, or an engine the host lacks.

The host's PostgreSQL, MySQL, Redis and MongoDB stay the primary engines of the
server (decision D4 of the 3.4 design, owner item 79). **An instance** is an
additional one, run from the engine's official image, pinned by tag and digest
(:mod:`noust.managers.database.instance_catalog`), as a container Noust owns:

- a Compose project in ``<state>/db-instances/<name>/`` (the file is generated,
  :mod:`noust.managers.database.instance_compose`), run by a systemd unit of its
  own (``noust-db-<name>``), so it comes back after a reboot and is started and
  stopped like the rest of what Noust runs;
- published on 127.0.0.1 only, with a memory limit that is mandatory and is also
  what the engine sizes itself to;
- its data in a named Docker volume (``noust-db-<name>-data``);
- a superuser whose password Noust generated, which only the container's
  environment holds.

It is **not a second implementation of anything.** Once the instance is up it is
addressed by a key (``postgresql@pg16``) and the very managers that drive the
host's engine drive it, through ``docker exec``
(:class:`~noust.managers.database.instances.DatabaseInstance`): the explorer,
the console, accounts, dumps and their policies, restore, import and making a
database public all work on it unchanged. What this module adds is only what the
host's packages do for the host's engines: making the instance, starting and
stopping it, moving it to a newer image of the same version, and removing it.

**Every change is written down first and undone from what is written.** The row
of the store (:class:`~noust.core.db_instance_store.DbInstanceStore`) says how far
creating, upgrading or removing got. A creation that fails removes what it made.
An upgrade runs behind the same kind of gate as every other activation: the new
image must answer, and when it does not, the previous one is put back and the
error carries the container's own log.
"""

from __future__ import annotations

import json
import platform
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from noust.central import require_server_role
from noust.core.database_exposures import ExposureStore
from noust.core.db_instance_store import (
    READY,
    REMOVING,
    UPGRADING,
    DbInstanceStore,
    InstanceRecord,
)
from noust.core.exceptions import (
    ConfirmationRequired,
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    NoustError,
    ServiceError,
    ValidationError,
)
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.managers.database import instance_layout as layout
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.eol import support_notice
from noust.managers.database.instance_catalog import (
    EngineSpec,
    ImagePin,
    resolve_pin,
    spec_for,
    with_variant,
)
from noust.managers.database.instance_compose import (
    INIT_FLAVOURS,
    render_compose,
    render_engine_config,
    render_env,
)
from noust.managers.database.instance_records import key_of, stub_instance
from noust.managers.database.instances import instance_key, storage_name
from noust.managers.database.service import DatabaseService
from noust.managers.database.settings import Resources, server_resources
from noust.managers.port_claims import PortClaims
from noust.managers.service_manager import ServiceManager
from noust.validators.db_instance import (
    check_host_port,
    check_init_scripts,
    check_instance_name,
    check_memory,
)

__all__ = [
    "HEALTH_SECONDS",
    "SIGNIN_SECONDS",
    "InstancePlan",
    "ManagedInstances",
    "RemoveOutcome",
    "UpgradeOutcome",
]

#: How long a new container may take to report itself healthy. MongoDB and MySQL
#: initialise a data directory on first start, which on a slow disk takes minutes.
HEALTH_SECONDS = 240

#: How long after the container is healthy the superuser's password is tried.
SIGNIN_SECONDS = 90

#: Seconds between two looks at the container.
POLL_SECONDS = 3

#: What Docker may take to pull an image: a database image is several hundred megabytes.
PULL_TIMEOUT = 1800

#: What a Docker command that only looks or removes may take.
DOCKER_TIMEOUT = 120

#: The share of the server's memory the limits of all instances may add up to, in percent.
MEMORY_SHARE = 90

#: How many ports above the engine's own are tried when none is asked for.
PORT_SCAN = 200

#: How a test replaces the wait between two looks.
_sleep: Callable[[float], None] = time.sleep


def host_architecture() -> str:
    """
    Name this server's processor the way image registries do.

    Returns:
        ``amd64`` or ``arm64``; whatever the system says, lower-cased, for any other.
    """
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        machine, machine
    )


@dataclass(frozen=True)
class InstancePlan:
    """
    What creating an instance would do, decided before anything is touched.

    Attributes:
        name: The instance's name.
        key: The key it is addressed by (``postgresql@pg16``).
        flavour: What it runs.
        engine: The manager that speaks it.
        display_name: The flavour's name for a person.
        version: The tag.
        image: The repository.
        digest: The digest it is pinned to.
        host_port: The port on 127.0.0.1.
        memory_limit_mb: Its memory limit.
        warnings: What the operator should know before going ahead.
        variant: The image variant (``pgvector``), or None for the engine's own image.
        tag: The tag the digest was read from; the version when None.
        init_scripts: The names of the SQL scripts it runs when it first starts.
    """

    name: str
    key: str
    flavour: str
    engine: str
    display_name: str
    version: str
    image: str
    digest: str
    host_port: int
    memory_limit_mb: int
    warnings: list[str] = field(default_factory=list)
    variant: str | None = None
    tag: str | None = None
    init_scripts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "key": self.key,
            "flavour": self.flavour,
            "engine": self.engine,
            "display_name": self.display_name,
            "version": self.version,
            "image": self.image,
            "digest": self.digest,
            "reference": f"{self.image}:{self.tag or self.version}@{self.digest}",
            "host_port": self.host_port,
            "memory_limit_mb": self.memory_limit_mb,
            "warnings": list(self.warnings),
            "variant": self.variant,
            "tag": self.tag or self.version,
            "init_scripts": list(self.init_scripts),
        }


@dataclass(frozen=True)
class UpgradeOutcome:
    """
    An instance moved to the newest image of its version.

    Attributes:
        name: The instance.
        changed: Whether the registry served an image other than the one it ran.
        from_digest: What it ran.
        to_digest: What it runs now (the same when nothing changed).
        dumps: The dumps taken before, one per database.
    """

    name: str
    changed: bool
    from_digest: str
    to_digest: str
    dumps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "changed": self.changed,
            "from_digest": self.from_digest,
            "to_digest": self.to_digest,
            "dumps": list(self.dumps),
        }


@dataclass(frozen=True)
class RemoveOutcome:
    """
    An instance removed.

    Attributes:
        name: The instance.
        removed: What was taken away, in the order it was.
        kept: What stays on purpose (dumps already taken).
    """

    name: str
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {"name": self.name, "removed": list(self.removed), "kept": list(self.kept)}


class ManagedInstances:
    """
    Make, list, start, stop, upgrade and remove the instances Noust runs.

    The one implementation, for the CLI and the API.

    Args:
        service: The database service, which finds containers, resolves engines
            and makes dumps.
        runner: What docker is run through; the process-wide runner by default.
        fs: What files are written through; the process-wide filesystem by default.
        services: Builds the systemd manager; the real one by default.
        resources: Measures the server's memory; the host's by default.
    """

    def __init__(
        self,
        service: DatabaseService,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        services: Callable[[], ServiceManager] | None = None,
        resources: Callable[[], Resources] | None = None,
    ) -> None:
        self.service = service
        self._runner = runner
        self._fs = fs
        self._services = services
        self._resources = resources or server_resources
        self.records = DbInstanceStore(service.store)

    # ------------------------------------------------------------ plumbing

    @property
    def runner(self) -> CommandRunner:
        """The runner docker goes through; a hub refuses it at the chokepoint."""
        require_server_role("Database instances")
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem the instance's files are written through."""
        return self._fs or get_fs()

    @property
    def logger(self) -> Any:
        """Where progress is reported."""
        return self.service.logger

    def units(self) -> ServiceManager:
        """
        Returns:
            The one door to systemd, so ownership is checked the way it is for
            every other unit.
        """
        if self._services is not None:
            return self._services()
        return ServiceManager(runner=self._runner, fs=self._fs)

    def _docker(self, *argv: str, timeout: int = DOCKER_TIMEOUT) -> Any:
        """
        Run docker.

        Args:
            argv: The arguments after ``docker``.
            timeout: Its deadline.

        Returns:
            The command's outcome.
        """
        return self.runner.run(["docker", *argv], timeout=timeout)

    def _actor(self) -> str | None:
        """
        Returns:
            Who acts, as the store records it, or None.
        """
        actor = self.service.actor
        return actor.label if actor is not None else None

    # ----------------------------------------------------------- questions

    def get(self, name: str) -> InstanceRecord:
        """
        Find an instance by name.

        Args:
            name: Its name.

        Returns:
            The row.

        Raises:
            ValidationError: The name cannot be an instance's.
            DatabaseNotFoundError: No instance has that name.
        """
        check_instance_name(name)
        record = self.records.get(name)
        if record is None:
            known = ", ".join(row.name for row in self.records.all()) or "none"
            raise DatabaseNotFoundError(
                f"There is no database instance called {name}",
                details=f"Instances: {known}. List them with: noust db instance list",
            )
        return record

    def _require_ready(self, record: InstanceRecord, action: str) -> None:
        """
        Refuse to act on an instance that is being changed.

        Args:
            record: The instance's row.
            action: What was asked, as a verb.

        Raises:
            DatabaseEngineError: It is being made, upgraded or removed.
        """
        if record.state != READY:
            raise DatabaseEngineError(
                f"{record.name} is not ready: it is {record.state}",
                details=(
                    f"Wait for what is changing it to finish, or finish it: 'noust db instance "
                    f"remove {record.name}' undoes an instance that was never completed. "
                    f"(Asked: {action}.)"
                ),
            )

    def describe(self, record: InstanceRecord) -> dict[str, Any]:
        """
        Describe an instance: its row and what Docker says of its container.

        Args:
            record: The instance's row.

        Returns:
            The row's fields, and ``key``, ``engine``, ``display_name``,
            ``reference``, ``unit``, ``container``, ``volume``, ``directory``,
            ``address``, ``running``, ``status`` (``running``, ``stopped`` when
            Docker has no container for it, or Docker's own word for one that
            exists and is not running), ``end_of_life`` and ``support``.
        """
        spec = with_variant(spec_for(record.flavour), record.variant)
        found = next(
            (item for item in self.service.instances() if item.managed == record.name), None
        )
        # Stopping the unit removes the container, so an instance that is down has
        # none: "stopped" is what that is called, and Docker's own word is kept
        # for a container that exists and is not running (restarting, created).
        status = "stopped"
        if found is not None and found.container_id:
            status = "running" if found.running else (found.state or "unknown")
        notice = support_notice(spec.flavour, record.version)
        return {
            **record.to_dict(),
            "key": key_of(record),
            "engine": spec.engine,
            "display_name": spec.display_name,
            "reference": record.reference,
            "unit": layout.unit_name(record.name),
            "container": layout.container_name(record.name),
            "volume": layout.data_volume(record.name),
            "directory": str(layout.directory(record.name)),
            "address": "127.0.0.1",
            "running": bool(found is not None and found.running),
            "status": status,
            "end_of_life": notice.end_of_life,
            "support": notice.status,
        }

    def all(self) -> list[dict[str, Any]]:
        """
        Describe every instance Noust runs.

        Returns:
            One description per instance, by name.

        Raises:
            DatabaseQueryError: Docker does not answer.
        """
        return [self.describe(record) for record in self.records.all()]

    def budget(self) -> dict[str, int]:
        """
        Say how the server's memory is divided among the instances.

        Returns:
            ``server_mb`` (0 when the system does not say), ``allocatable_mb``
            (the share the limits may add up to), ``allocated_mb`` (what the
            limits add up to) and ``available_mb``.
        """
        server = self._resources().memory_bytes // (1024 * 1024)
        allocatable = server * MEMORY_SHARE // 100
        allocated = sum(record.memory_limit_mb for record in self.records.all())
        return {
            "server_mb": server,
            "allocatable_mb": allocatable,
            "allocated_mb": allocated,
            "available_mb": max(allocatable - allocated, 0) if server else 0,
        }

    # ------------------------------------------------------------ planning

    def plan(
        self,
        name: str,
        engine: str,
        *,
        version: str | None = None,
        memory: object = None,
        port: int | None = None,
        variant: str | None = None,
        init_scripts: Sequence[tuple[str, bytes]] = (),
    ) -> InstancePlan:
        """
        Decide what creating an instance would do, changing nothing.

        Args:
            name: The instance's name.
            engine: What to run: a flavour of the catalog (``postgresql``,
                ``mariadb``, ``valkey``...) or an alias.
            version: The version, a prefix by components; the default when None.
            memory: The memory limit (megabytes, or ``512m``, ``2g``): mandatory.
            port: The port on 127.0.0.1; a free one near the engine's when None.
            variant: An image of the engine with something added (``pgvector``,
                ``postgis``); the engine's own image when None.
            init_scripts: SQL scripts (name and content) the engine runs, in name order, the
                first time it starts: PostgreSQL, MySQL and MariaDB only.

        Returns:
            The plan.

        Raises:
            ValidationError: Something asked for cannot be had, Docker or its
                Compose plugin is missing (``field`` says what); nothing was
                changed.
            DatabaseExistsError: The name, or the key, is taken.
        """
        require_server_role("Database instances")
        check_instance_name(name)
        spec = with_variant(spec_for(engine), variant)
        pin = resolve_pin(spec, version)
        architecture = host_architecture()
        if architecture not in spec.platforms:
            raise ValidationError(
                f"{spec.display_name} is not published for this server's processor ({architecture})",
                details=(
                    f"Its image exists for {', '.join(spec.platforms)} only. Use the engine's own "
                    "image, or another variant, on this server."
                ),
                field="variant",
            )
        self._require_docker()
        key = instance_key(spec.engine, container=name)
        self._require_free(name, key)
        if memory is None:
            raise ValidationError(
                "An instance needs a memory limit",
                details=(
                    f"Give one with --memory (memory_mb in the API), such as "
                    f"{spec.default_memory_mb}m. {spec.display_name} needs at least "
                    f"{spec.min_memory_mb} MB."
                ),
                field="memory_mb",
            )
        megabytes = check_memory(memory, minimum=spec.min_memory_mb, maximum=self._memory_ceiling())
        scripts = check_init_scripts(init_scripts)
        if scripts and spec.flavour not in INIT_FLAVOURS:
            raise ValidationError(
                f"{spec.display_name} does not run initialisation scripts",
                details=(
                    "The images of PostgreSQL, MySQL and MariaDB run .sql files the first time they "
                    "start. Load data into this engine with 'noust db import' once it runs."
                ),
                field="init_scripts",
            )
        host_port = self._choose_port(port, spec)
        return InstancePlan(
            name=name,
            key=key,
            flavour=spec.flavour,
            engine=spec.engine,
            display_name=spec.display_name,
            version=pin.version,
            image=spec.repository,
            digest=pin.digest,
            host_port=host_port,
            memory_limit_mb=megabytes,
            warnings=self._warnings(spec, pin, host_port, variant=variant),
            variant=variant.strip().lower() if variant and variant.strip() else None,
            tag=pin.tag,
            init_scripts=tuple(name for name, _content in scripts),
        )

    def _require_docker(self) -> None:
        """
        Refuse when Docker, or its Compose plugin, is not there.

        Raises:
            ValidationError: With what to install: this server cannot run an
                instance until it is, which is a refusal of the request, not a
                failure of the engine.
        """
        if not self.runner.exists("docker"):
            raise ValidationError(
                "Docker is not installed",
                details=(
                    "A database instance is a container: install Docker (docker.io on Debian "
                    "and Ubuntu, or docker-ce from Docker's repository) with its Compose "
                    "plugin, then try again."
                ),
                field="engine",
            )
        compose = self._docker("compose", "version", timeout=30)
        if not compose.success:
            raise ValidationError(
                "Docker Compose is not installed",
                details=(
                    "A database instance runs as a Compose project: install the Compose plugin "
                    "(docker-compose-plugin, or docker-compose-v2 on Debian and Ubuntu)."
                ),
                field="engine",
            )

    def _require_free(self, name: str, key: str) -> None:
        """
        Refuse a name something already holds.

        Args:
            name: The instance's name.
            key: The key it would be addressed by.

        Raises:
            DatabaseExistsError: A row, a container Docker runs, a volume, a unit
                or a directory of that name exists. A leftover is named, because
                reusing one could hand a new instance somebody's data.
        """
        if self.records.get(name) is not None:
            raise DatabaseExistsError(
                f"A database instance called {name} exists already",
                details="See it with: noust db instance list",
            )
        taken = next((item for item in self.service.instances() if item.key == key), None)
        if taken is not None:
            raise DatabaseExistsError(
                f"{key} is the key of the container {taken.container}",
                details="Choose another name for the instance.",
            )
        leftovers = []
        if layout.directory(name).exists():
            leftovers.append(f"the directory {layout.directory(name)}")
        for kind, what in (
            ("container", layout.container_name(name)),
            ("volume", layout.data_volume(name)),
            ("volume", layout.config_volume(name)),
        ):
            if self._docker("inspect", "--type", kind, what).success:
                leftovers.append(f"the Docker {kind} {what}")
        if self.units().inspect_unit(layout.unit_name(name), serving=False).exists:
            leftovers.append(f"the unit {layout.unit_name(name)}")
        if leftovers:
            raise DatabaseExistsError(
                f"{name} left something on this server: {', '.join(leftovers)}",
                details=(
                    "Noust does not reuse what an instance left behind, because that could be "
                    "somebody's data. Remove it by hand, or choose another name."
                ),
            )

    def _memory_ceiling(self) -> int:
        """
        Returns:
            The most memory one more instance may be given, in megabytes: the
            share of the server's memory the limits may add up to, less what the
            existing ones already hold. Without a measurement of the server there
            is no ceiling to give.
        """
        server = self._resources().memory_bytes // (1024 * 1024)
        if server <= 0:
            return 4 * 1024 * 1024
        return max(server * MEMORY_SHARE // 100 - self.budget()["allocated_mb"], 0)

    def _taken_ports(self) -> set[int]:
        """
        Returns:
            Every port something holds: an instance's, an open socket, the
            public port of a database made reachable.
        """
        return PortClaims(self.runner, self.service.store).taken_ports()

    def _choose_port(self, port: int | None, spec: EngineSpec) -> int:
        """
        Choose and check the port on 127.0.0.1.

        Args:
            port: What was asked for, or None for a free one just above the engine's.
            spec: The engine.

        Returns:
            The port.

        Raises:
            ValidationError: It is not a usable port, is taken, or none is free.
        """
        taken = self._taken_ports()
        if port is None:
            for candidate in range(spec.container_port + 1, spec.container_port + 1 + PORT_SCAN):
                if candidate not in taken:
                    return check_host_port(candidate)
            raise ValidationError(
                f"No port from {spec.container_port + 1} to {spec.container_port + PORT_SCAN} "
                "is free",
                details="Choose one with --port.",
                field="port",
            )
        check_host_port(port)
        if port in taken:
            raise ValidationError(
                f"Port {port} is taken on this server",
                details="Something listens on it already, or another instance or a public "
                "database holds it. Choose another with --port.",
                field="port",
            )
        return port

    def _warnings(
        self, spec: EngineSpec, pin: ImagePin, host_port: int, *, variant: str | None = None
    ) -> list[str]:
        """
        Say what an operator should know before going ahead.

        Args:
            spec: The engine (a variant's, when one was asked for).
            pin: The version.
            host_port: The port chosen.
            variant: The variant, when one was asked for.

        Returns:
            Sentences in English.
        """
        warnings = [
            f"It listens on 127.0.0.1:{host_port} only: reach it from your computer through an "
            "SSH tunnel, or make one of its databases public with 'noust db public'.",
            "It runs in Docker, from the engine's official image pinned by digest; its data is "
            f"in the Docker volume {layout.data_volume('<name>')}. It is not part of the "
            "server's packages: 'noust db instance upgrade' moves it to a newer image of the "
            "same version.",
        ]
        if variant:
            warnings.append(
                f"It runs {spec.repository}, the image of another project than the engine's: "
                "its extensions are the ones that image installed, and its database dumps can "
                "only be loaded into an engine that has them."
            )
        notice = support_notice(spec.flavour, pin.version)
        if notice.status in ("ended", "ending_soon"):
            warnings.append(notice.message)
        return warnings

    # -------------------------------------------------------------- create

    def create(
        self,
        name: str,
        engine: str,
        *,
        version: str | None = None,
        memory: object = None,
        port: int | None = None,
        variant: str | None = None,
        init_scripts: Sequence[tuple[str, bytes]] = (),
    ) -> dict[str, Any]:
        """
        Make an instance, start it, and return once it answers.

        Everything is undone when something fails, and the error says what.

        Args:
            name: The instance's name.
            engine: What to run.
            version: The version; the default when None.
            memory: The memory limit: mandatory.
            port: The port on 127.0.0.1; a free one when None.
            variant: An image of the engine with something added (``pgvector``); the
                engine's own image when None.
            init_scripts: SQL scripts (name and content) the engine runs the first time it
                starts, in name order. They are written beside the Compose file and are not
                run again.

        Returns:
            The instance, as :meth:`describe` says.

        Raises:
            ValidationError: Something asked for cannot be had.
            DatabaseExistsError: The name is taken or left something behind.
            DatabaseEngineError: The image cannot be pulled, the unit fails or the
                engine does not answer.
        """
        decided = self.plan(
            name,
            engine,
            version=version,
            memory=memory,
            port=port,
            variant=variant,
            init_scripts=init_scripts,
        )
        password = BaseDatabaseManager.generate_password(32)
        record = self.records.create(
            name=decided.name,
            flavour=decided.flavour,
            version=decided.version,
            image=decided.image,
            digest=decided.digest,
            host_port=decided.host_port,
            memory_limit_mb=decided.memory_limit_mb,
            created_by=self._actor(),
            variant=decided.variant,
            image_tag=decided.tag if decided.tag != decided.version else None,
            init_scripts=decided.init_scripts,
        )
        self.logger.info(
            f"Creating {decided.display_name} {decided.version} as {decided.name} "
            f"(127.0.0.1:{decided.host_port}, {decided.memory_limit_mb} MB)"
        )
        try:
            self._pull(record)
            self._write_files(record, password, check_init_scripts(init_scripts))
            self._install_unit(record, decided.display_name)
            self.units().start(layout.unit_name(name))
            self._wait_ready(record)
            self.records.set_state(name, READY)
        except (NoustError, OSError) as exc:
            leftovers = self._undo(record)
            self.service.audit("db.instance.create", name, "failure", reason=str(exc)[:200])
            raise self._creation_failed(record, exc, leftovers) from exc
        self.service.refresh_instances()
        self.service.audit(
            "db.instance.create",
            name,
            flavour=record.flavour,
            version=record.version,
            variant=record.variant,
            port=record.host_port,
            memory_mb=record.memory_limit_mb,
        )
        done = self.records.get(name) or replace(record, state=READY)
        return self.describe(done)

    @staticmethod
    def _creation_failed(
        record: InstanceRecord, cause: Exception, leftovers: list[str]
    ) -> DatabaseEngineError:
        """
        Build the error of a creation that was undone.

        Args:
            record: The instance that was being made.
            cause: What failed.
            leftovers: What could not be removed again.

        Returns:
            The error, carrying the cause's own words.
        """
        message = getattr(cause, "message", str(cause))
        details = getattr(cause, "details", "") or ""
        undone = (
            "Everything it made was removed."
            if not leftovers
            else "These could not be removed and have to be taken away by hand: "
            + "; ".join(leftovers)
            + f". Then run: noust db instance remove {record.name} --yes"
        )
        return DatabaseEngineError(
            f"The instance {record.name} could not be made: {message}",
            details="\n".join(part for part in (details, undone) if part),
            output=getattr(cause, "output", None),
        )

    def _pull(self, record: InstanceRecord) -> None:
        """
        Pull the image by its digest, so a restart never has to.

        Args:
            record: The instance's row.

        Raises:
            DatabaseEngineError: Docker could not pull it.
        """
        self.logger.info(f"Pulling {record.image}:{record.tag}")
        result = self._docker("pull", f"{record.image}@{record.digest}", timeout=PULL_TIMEOUT)
        if not result.success:
            raise DatabaseEngineError(
                f"Docker could not pull {record.image}:{record.tag}",
                details="Check that this server reaches Docker Hub (registry-1.docker.io) and "
                "that the Docker daemon runs: systemctl status docker",
                output=(result.stderr or result.stdout).strip() or None,
            )

    def _write_files(
        self,
        record: InstanceRecord,
        password: str,
        init_scripts: Sequence[tuple[str, bytes]] = (),
    ) -> None:
        """
        Write the instance's directory: its environment, its configuration and its Compose file.

        The directory is root's alone. The environment file holds the
        superuser's password and is 0600; Redis's configuration, which holds it
        too, is readable inside the directory only.

        Args:
            record: The instance's row.
            password: The superuser's password.
            init_scripts: The scripts the engine runs the first time it starts. They are
                written 0644 in a directory the container reads, so they must not hold a
                secret.
        """
        directory = layout.directory(record.name)
        self.fs.make_dir(directory, mode=SECRET_DIR_MODE, parents=True)
        self.fs.write_text(
            layout.env_path(record.name), render_env(record.flavour, password), mode=SECRET_MODE
        )
        config = render_engine_config(record, password)
        if config is not None:
            self.fs.write_text(layout.config_path(record.name), config, mode=0o644)
        if init_scripts:
            scripts_dir = layout.init_directory(record.name)
            self.fs.make_dir(scripts_dir, mode=0o755)
            for script, content in init_scripts:
                self.fs.write_text(scripts_dir / script, content.decode("utf-8"), mode=0o644)
        self._write_compose(record)

    def _write_compose(self, record: InstanceRecord) -> None:
        """
        Write the Compose file of a row.

        Args:
            record: The instance's row, with the image it is to run.
        """
        self.fs.write_text(layout.compose_path(record.name), render_compose(record), mode=0o644)

    def _install_unit(self, record: InstanceRecord, display_name: str) -> None:
        """
        Write the unit that runs the project, and enable it.

        Args:
            record: The instance's row.
            display_name: The engine's name, for the unit's description.
        """
        unit = layout.unit_name(record.name)
        services = self.units()
        services.install_unit(
            unit,
            "docker-compose",
            {
                "name": unit,
                "description": (
                    f"Noust database instance {record.name} ({display_name} {record.version})"
                ),
                "working_directory": str(layout.directory(record.name)),
                "environment": {},
                "compose_file": str(layout.compose_path(record.name)),
                "compose_project": layout.project_name(record.name),
            },
        )
        services.enable(unit)

    # --------------------------------------------------------------- ready

    def _bound(self, record: InstanceRecord) -> BaseDatabaseManager:
        """
        Bind an engine's manager to an instance that is being made.

        Discovery does not offer an instance until its row says ready, which is
        what keeps an application from being given a database on one that is not
        finished, so the manager is bound by hand here.

        Args:
            record: The instance's row.

        Returns:
            The manager, driving the container.
        """
        base = self.service.manager(spec_for(record.flavour).engine)
        return base.bind(stub_instance(record, state="running"))

    def _health(self, record: InstanceRecord) -> str:
        """
        Read what Docker says of the container's health check.

        Args:
            record: The instance's row.

        Returns:
            ``healthy``, ``starting``, ``unhealthy`` or ``absent``.
        """
        result = self._docker(
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
            layout.container_name(record.name),
            timeout=30,
        )
        return result.stdout.strip() if result.success and result.stdout.strip() else "absent"

    def _wait_ready(
        self, record: InstanceRecord, *, health: int = HEALTH_SECONDS, signin: int = SIGNIN_SECONDS
    ) -> None:
        """
        Wait until the engine is healthy and accepts the superuser's password.

        The container's own health check says the engine listens; signing in
        proves the password Noust generated is the one the engine holds, which
        is what every later command depends on.

        Args:
            record: The instance's row.
            health: Seconds to wait for the health check.
            signin: Seconds to wait for the sign-in once it is healthy.

        Raises:
            DatabaseEngineError: It is unhealthy, or did not answer in time. The
                error carries the container's log, with its secrets masked.
        """
        if is_rehearsal():
            return
        waited = 0
        while True:
            status = self._health(record)
            if status == "healthy":
                break
            if status == "unhealthy" or waited >= health:
                raise self._not_ready(
                    record,
                    "reports itself unhealthy"
                    if status == "unhealthy"
                    else f"was still {status} after {health} seconds",
                )
            _sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        manager = self._bound(record)
        waited = 0
        while True:
            try:
                manager.list_databases()
                return
            except DatabaseError as exc:
                if waited >= signin:
                    raise self._not_ready(
                        record, f"did not accept its superuser after {signin} seconds", exc
                    ) from exc
            _sleep(POLL_SECONDS)
            waited += POLL_SECONDS

    def _not_ready(
        self, record: InstanceRecord, why: str, cause: DatabaseError | None = None
    ) -> DatabaseEngineError:
        """
        Build the error of an instance that did not come up.

        Args:
            record: The instance's row.
            why: What happened.
            cause: The engine's refusal, when it was one.

        Returns:
            The error, with the container's log (secrets masked) as its output.
        """
        try:
            log = self._bound(record).container_logs(40)
        except DatabaseError as exc:
            log = f"No log could be read: {exc.message}"
        unit = layout.unit_name(record.name)
        output = "\n".join(
            part for part in (cause.output or cause.message if cause else "", log) if part
        )
        return DatabaseEngineError(
            f"{record.name} {why}",
            details=f"Its unit's journal: journalctl -u {unit} -n 50. Its log follows.",
            output=output or None,
        )

    # ---------------------------------------------------------------- undo

    def _undo(self, record: InstanceRecord) -> list[str]:
        """
        Take away everything a creation made, from what the row says was started.

        Args:
            record: The instance's row.

        Returns:
            What could not be removed, each as a sentence; empty when all went.
        """
        leftovers = self._teardown(record, [])
        if not leftovers:
            self.records.delete(record.name)
        return leftovers

    def _teardown(self, record: InstanceRecord, removed: list[str]) -> list[str]:
        """
        Remove an instance's unit, container, volumes and directory, in that order.

        Each step is attempted whatever happened to the one before, and each is
        safe to repeat, so a removal that could not finish is finished by running
        it again.

        Args:
            record: The instance's row.
            removed: Where each thing taken away is noted.

        Returns:
            What could not be removed.
        """
        name = record.name
        unit = layout.unit_name(name)
        failed: list[str] = []
        services = self.units()
        try:
            if services.inspect_unit(unit, serving=False).exists:
                # Stopping runs ``compose down``, which removes the container; the
                # volumes stay for the steps below.
                if not services.stop(unit):
                    failed.append(f"the unit {unit} would not stop")
                services.delete_service(unit)
                removed.append(f"the unit {unit}")
        except (ServiceError, ValidationError) as exc:
            failed.append(f"the unit {unit}: {exc.message}")
        container = layout.container_name(name)
        if self._docker("inspect", "--type", "container", container).success:
            result = self._docker("rm", "-f", container)
            if result.success:
                removed.append(f"the container {container}")
            else:
                failed.append(f"the container {container}: {result.stderr.strip()}")
        for volume in (layout.data_volume(name), layout.config_volume(name)):
            if not self._docker("inspect", "--type", "volume", volume).success:
                continue
            result = self._docker("volume", "rm", volume)
            if result.success:
                removed.append(f"the volume {volume}")
            else:
                failed.append(f"the volume {volume}: {result.stderr.strip()}")
        directory = layout.directory(name)
        if directory.exists():
            try:
                self.fs.remove_tree(directory)
                removed.append(f"the directory {directory}")
            except OSError as exc:
                failed.append(f"the directory {directory}: {exc}")
        return failed

    # ------------------------------------------------------- start and stop

    def start(self, name: str) -> dict[str, Any]:
        """
        Start an instance and wait until it answers.

        Args:
            name: Its name.

        Returns:
            The instance, as :meth:`describe` says.

        Raises:
            DatabaseNotFoundError: No such instance.
            DatabaseEngineError: It is being changed, the unit fails, or the
                engine does not answer.
        """
        record = self.get(name)
        self._require_ready(record, "start")
        unit = layout.unit_name(name)
        if not self.units().inspect_unit(unit, serving=False).exists:
            raise DatabaseEngineError(
                f"{name}'s unit is not on this server",
                details=(
                    f"The store knows {name} but {unit}.service does not exist here: the "
                    "store was restored without the instance's files. Remove it with 'noust "
                    f"db instance remove {name} --yes', make it again, and load its dumps."
                ),
            )
        self.units().start(unit)
        self._wait_ready(record)
        self.service.refresh_instances()
        self.service.audit("db.instance.start", name)
        return self.describe(record)

    def stop(self, name: str) -> dict[str, Any]:
        """
        Stop an instance. Its data stays; applications using it lose their database.

        Args:
            name: Its name.

        Returns:
            The instance, as :meth:`describe` says.

        Raises:
            DatabaseNotFoundError: No such instance.
            DatabaseEngineError: It is being changed, or the unit would not stop.
        """
        record = self.get(name)
        self._require_ready(record, "stop")
        if not self.units().stop(layout.unit_name(name)):
            raise DatabaseEngineError(
                f"The unit {layout.unit_name(name)} would not stop",
                details=f"See why: journalctl -u {layout.unit_name(name)} -n 50",
            )
        self.service.refresh_instances()
        self.service.audit("db.instance.stop", name)
        return self.describe(record)

    # ------------------------------------------------------------- upgrade

    def _newest_digest(self, record: InstanceRecord) -> str:
        """
        Pull the tag of an instance and read the digest the registry serves for it.

        Args:
            record: The instance's row.

        Returns:
            ``sha256:...``: the digest of the image index the tag points at now.

        Raises:
            DatabaseEngineError: Docker could not pull it or says nothing of it.
        """
        reference = f"{record.image}:{record.tag}"
        self.logger.info(f"Looking for a newer {reference}")
        pulled = self._docker("pull", reference, timeout=PULL_TIMEOUT)
        if not pulled.success:
            raise DatabaseEngineError(
                f"Docker could not pull {reference}",
                details="Check that this server reaches Docker Hub and that the daemon runs.",
                output=(pulled.stderr or pulled.stdout).strip() or None,
            )
        shown = self._docker(
            "inspect", "--type", "image", "--format", "{{json .RepoDigests}}", reference, timeout=30
        )
        try:
            listed = json.loads(shown.stdout or "[]") if shown.success else []
        except json.JSONDecodeError:
            listed = []
        prefix = f"{record.image}@"
        for entry in listed if isinstance(listed, list) else []:
            if isinstance(entry, str) and entry.startswith(prefix):
                return entry[len(prefix) :]
        raise DatabaseEngineError(
            f"Docker did not say which digest {reference} is",
            details="Run 'docker image inspect' on it to see what it holds.",
            output=(shown.stderr or shown.stdout).strip() or None,
        )

    def upgrade(self, name: str, *, backup: bool = True) -> UpgradeOutcome:
        """
        Move an instance to the newest image of its version.

        "Minor" because the tag does not change: PostgreSQL 17 stays 17, and what
        moves is the build the registry serves for ``17`` (the release's patches).
        The digest it ran is kept; a new major version is another instance.

        What happens, in this order: the registry is asked for the tag's current
        digest and nothing more is done when it is the one already running; a
        dump of every database is taken (a failed dump stops the upgrade, because
        without it a bad image cannot be undone); the Compose file is written with
        the new image and the unit restarted; the instance must become healthy and
        accept its superuser. When it does not, the previous image is put back and
        the error carries what the container said.

        Args:
            name: Its name.
            backup: Dump every database first. ``False`` only when the operator
                says the data does not matter.

        Returns:
            What changed.

        Raises:
            DatabaseNotFoundError: No such instance.
            DatabaseEngineError: It is being changed, Docker cannot pull, or the
                new image did not come up (the old one runs again).
            DatabaseBackupError: A dump failed, so nothing was touched.
        """
        record = self.get(name)
        self._require_ready(record, "upgrade")
        newest = self._newest_digest(record)
        if newest == record.digest:
            self.logger.info(f"{name} already runs the newest {record.image}:{record.tag}")
            return UpgradeOutcome(name, False, record.digest, record.digest)
        dumps = self._dump_everything(record) if backup else []
        self.records.set_state(name, UPGRADING)
        candidate = replace(record, digest=newest, state=UPGRADING)
        try:
            self._write_compose(candidate)
            self.units().restart(layout.unit_name(name))
            self._wait_ready(candidate)
        except (NoustError, OSError) as exc:
            raise self._upgrade_failed(record, newest, exc) from exc
        self.records.set_digest(name, newest)
        self.records.set_state(name, READY)
        self.service.refresh_instances()
        self.service.audit("db.instance.upgrade", name, from_digest=record.digest, to_digest=newest)
        return UpgradeOutcome(name, True, record.digest, newest, dumps)

    def _upgrade_failed(
        self, record: InstanceRecord, newest: str, cause: Exception
    ) -> DatabaseEngineError:
        """
        Put the previous image back after an upgrade that did not come up.

        Args:
            record: The instance as it was.
            newest: The digest that failed.
            cause: What failed.

        Returns:
            The error to raise. The instance runs the previous image again, or
            the error says it could not be put back and where it stands.
        """
        message = getattr(cause, "message", str(cause))
        output = getattr(cause, "output", None)
        try:
            self._write_compose(record)
            self.units().restart(layout.unit_name(record.name))
            self._wait_ready(record)
        except (NoustError, OSError) as back:
            self.service.audit("db.instance.upgrade", record.name, "failure", to_digest=newest)
            return DatabaseEngineError(
                f"The upgrade of {record.name} failed and the previous image could not be put back",
                details=(
                    f"{message}\nPutting it back failed too: "
                    f"{getattr(back, 'message', str(back))}. Its row says 'upgrading' and its "
                    f"Compose file is {layout.compose_path(record.name)}: fix the cause, then "
                    f"'noust db instance start {record.name}'."
                ),
                output=output,
            )
        self.records.set_state(record.name, READY)
        self.service.refresh_instances()
        self.service.audit("db.instance.upgrade", record.name, "failure", to_digest=newest)
        return DatabaseEngineError(
            f"{record.name} did not come up on the newer image, and runs the previous one again",
            details=f"{message}\nNothing of its data was changed by Noust.",
            output=output,
        )

    def _dump_everything(self, record: InstanceRecord) -> list[str]:
        """
        Dump every database of an instance, before it is changed.

        Args:
            record: The instance's row.

        Returns:
            The dumps' file names.

        Raises:
            DatabaseBackupError: The instance cannot be listed or a dump fails.
        """
        from noust.managers.database.backups import DatabaseBackups

        key = key_of(record)
        listing = self.service.listing(key)
        if listing.problems:
            problem = listing.problems[0]
            raise DatabaseBackupError(
                f"{record.name}'s databases could not be listed to dump them first: "
                f"{problem.message}",
                details="Nothing was changed. Fix that, or upgrade without a dump "
                "(--no-backup) if its data does not matter.",
                output=problem.output or None,
            )
        names = [view.name for view in listing.databases if not view.missing]
        if spec_for(record.flavour).engine == "redis":
            names = names[:1]  # one snapshot covers every slot
        backups = DatabaseBackups(self.service)
        files: list[str] = []
        for database in names:
            self.logger.info(f"Dumping {database}")
            files.append(backups.dump(key, database).name)
        return files

    def snapshot(self, name: str) -> list[str]:
        """
        Dump every database of an instance, as an upgrade does before it changes one.

        Args:
            name: Its name.

        Returns:
            The dumps' file names, one per database.

        Raises:
            DatabaseNotFoundError: No such instance.
            DatabaseBackupError: It cannot be listed or a dump fails.
        """
        record = self.get(name)
        self._require_ready(record, "dump")
        return self._dump_everything(record)

    # -------------------------------------------------------------- remove

    def _in_use(self, record: InstanceRecord) -> list[str]:
        """
        List what stops an instance from being removed.

        Args:
            record: The instance's row.

        Returns:
            Sentences: the applications whose databases live on it and the
            databases made reachable from the network.
        """
        key = key_of(record)
        reasons = []
        users = sorted(
            {
                owner.domain
                if (owner := self.service.store.get_app_by_id(link.app_id))
                else (f"application {link.app_id}")
                for link in self.service.records.links(engine=key)
            }
        )
        if users:
            reasons.append(
                f"{', '.join(users)} use databases on it: unlink them or move them first "
                "('noust db unlink')"
            )
        exposed = [row for row in ExposureStore(self.service.store).all() if row.engine == key]
        if exposed:
            names = ", ".join(row.db_name for row in exposed)
            reasons.append(
                f"{names} is reachable from the network: close it first ('noust db public close')"
            )
        return reasons

    def plan_remove(self, name: str) -> dict[str, Any]:
        """
        Say what removing an instance would delete, and what stops it.

        Args:
            name: Its name.

        Returns:
            ``name``, ``databases`` (what it holds, by name), ``blockers`` and
            ``warnings``.

        Raises:
            DatabaseNotFoundError: No such instance.
        """
        record = self.get(name)
        databases: list[str] = []
        if record.state == READY:
            try:
                listing = self.service.listing(key_of(record))
                databases = [view.name for view in listing.databases if not view.missing]
            except DatabaseError as exc:
                self.logger.warning(f"Could not list {name}'s databases: {exc.message}")
        warnings = [
            f"Removing {name} deletes its data volume {layout.data_volume(name)}"
            + (f" and its databases: {', '.join(databases)}" if databases else "")
            + ". There is no way back.",
            "Dumps already taken stay where they are.",
        ]
        return {
            "name": name,
            "databases": databases,
            "blockers": self._in_use(record),
            "warnings": warnings,
        }

    def remove(self, name: str, *, confirm: bool = False) -> RemoveOutcome:
        """
        Remove an instance, its container and its data.

        Applications that use it and databases made public on it stop the
        removal; policies for its databases are removed with it, and the dumps
        that were taken stay. The unit, the container, the volumes and the
        directory are each removed whatever became of the one before, and
        running it again finishes what could not be.

        Args:
            name: Its name.
            confirm: The operator accepted that its data is deleted. Without it
                nothing is changed and the warnings come back.

        Returns:
            What was taken away.

        Raises:
            DatabaseNotFoundError: No such instance.
            ValidationError: Applications or a public database use it.
            ConfirmationRequired: ``confirm`` is False.
            DatabaseEngineError: Something could not be removed; the row stays
                ``removing`` so a second run finishes it.
        """
        plan = self.plan_remove(name)
        record = self.get(name)
        if plan["blockers"]:
            raise ValidationError(
                f"{name} is in use and is not removed",
                details="; ".join(plan["blockers"]) + ".",
            )
        if not confirm:
            raise ConfirmationRequired(plan["warnings"])
        if record.state == READY:
            self._remove_policies(record)
        self.records.set_state(name, REMOVING)
        self.service.refresh_instances()
        removed: list[str] = []
        failed = self._teardown(record, removed)
        if failed:
            self.service.audit("db.instance.remove", name, "failure", failed=len(failed))
            raise DatabaseEngineError(
                f"{name} could not be removed completely",
                details="Taken away: "
                + (", ".join(removed) or "nothing")
                + ". Left: "
                + "; ".join(failed)
                + f". Fix that and run the same command again: noust db instance remove {name}",
            )
        self._forget(record)
        self.records.delete(name)
        self.service.refresh_instances()
        self.service.audit("db.instance.remove", name, flavour=record.flavour)
        return RemoveOutcome(name, removed, kept=["the dumps already taken"])

    def _remove_policies(self, record: InstanceRecord) -> None:
        """
        Remove the backup policies, and their timers, of an instance's databases.

        Args:
            record: The instance's row.
        """
        from noust.managers.database.backups import DatabaseBackups

        key = key_of(record)
        backups = DatabaseBackups(self.service)
        for policy in backups.list_policies(key):
            backups.remove_policy(key, policy.database)

    def _forget(self, record: InstanceRecord) -> None:
        """
        Forget what the store, and the secret store, kept of an instance's databases.

        Args:
            record: The instance's row.
        """
        key = key_of(record)
        self.service.refresh_instances()
        for row in self.service.store.list_databases(engine=key):
            self.service.forget(key, row.name)
        for account in self.service.records.accounts(engine=key):
            self.service.records.delete_account(key, account.username, account.host)
        self.service.secrets.delete_namespace(f"databases/{storage_name(key)}")
