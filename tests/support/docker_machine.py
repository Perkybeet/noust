# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A machine with Docker on it, for the tests of the Docker zone.

:class:`DockerMachine` is a runner that answers what the gate asks Docker the way Docker answers it
(a container listing, ``inspect`` of the ids it is given, one reading of ``stats``, the last lines of
a log, the events of a window, the disk table, an image, a volume or a network that is removed and is
gone from the next listing), so a test builds the machine it wants and asserts on what the code said,
not on which argument list it happened to build. What a test scripts itself (``script(["docker", "rm"],
exit_code=1, ...)``) wins over the machine, which is how a refusal of Docker's is made.

Nothing here runs Docker.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.runner import CommandResult, FakeRunner

SHOP = "/var/www/apps/shop-example-com"
BLOG = "/var/www/apps/blog-example-com"
RELEASE = "20261112-101500-abc1234"


def full_id(name: str) -> str:
    """Args: name: A container's name. Returns: A 64-digit id that is always the same for it."""
    return (name.encode().hex() * 16)[:64]


@dataclass
class ImageFacts:
    """What ``docker inspect --type image`` says of a local image."""

    repo_digests: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    created: str = "2026-10-01T10:00:00Z"
    os: str = "linux"
    architecture: str = "amd64"


@dataclass
class RemoteTag:
    """What a registry has behind a reference: its digest and the image that digest names."""

    digest: str
    created: str = "2026-11-09T10:00:00Z"
    env: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    # More than one platform makes the config a map keyed by platform, as buildx prints it.
    platforms: list[str] = field(default_factory=list)
    # A registry that serves the manifest and not the config (an old buildx cannot print it).
    config_readable: bool = True


@dataclass
class Box:
    """One container of the machine, with everything the commands read of it."""

    name: str
    image: str = "nginx:1.27"
    state: str = "running"
    status: str = "Up 2 hours"
    project: str = ""
    service: str = ""
    working_dir: str = ""
    config_files: str = ""
    mounts: str = ""
    networks: str = "default"
    ports: str = ""
    oneoff: bool = False
    instance: str = ""
    helper: str = ""
    # What ``inspect`` adds.
    exit_code: int = 0
    oom: bool = False
    restart_count: int = 0
    policy: str = "no"
    max_retries: int = 0
    memory: int = 0
    nano_cpus: int = 0
    health: str = ""
    health_output: str = ""
    started_at: str = "2026-11-12T08:00:00.000000000Z"
    finished_at: str = "0001-01-01T00:00:00Z"
    error: str = ""
    privileged: bool = False
    mount_list: list[dict[str, Any]] = field(default_factory=list)
    # What ``stats`` and ``logs`` answer.
    stats: dict[str, str] | None = None
    log: str = ""
    # How many times Docker restarted it in the window of ``events``.
    restarts_recent: int = 0

    @property
    def id(self) -> str:
        return full_id(self.name)


class DockerMachine(FakeRunner):
    """
    A runner that is a machine with Docker.

    Attributes:
        boxes: The containers, by name.
        images: ``(id digit, repository, tag, size text)``.
        volumes: Volume rows as ``docker volume ls`` prints them.
        networks: Network rows as ``docker network ls`` prints them.
        df: ``docker system df`` rows.
        volume_sizes: ``name -> size text`` ``docker system df -v`` reports.
        engine: The pieces ``docker version`` and ``docker info`` answer.
        installed: Whether there is a ``docker`` program at all.
        down: The daemon does not answer.
    """

    def __init__(self) -> None:
        super().__init__()
        self.boxes: dict[str, Box] = {}
        self.images: list[dict[str, str]] = []
        self.volumes: list[dict[str, str]] = []
        self.networks: list[dict[str, str]] = []
        self.df: list[dict[str, str]] = [
            {"Type": "Images", "TotalCount": "0", "Size": "0B", "Reclaimable": "0B (0%)"},
            {"Type": "Build Cache", "TotalCount": "0", "Size": "0B", "Reclaimable": "0B"},
        ]
        self.volume_sizes: dict[str, str] = {}
        self.engine: dict[str, str] = {
            "version": "29.1.3",
            "api": "1.52",
            "min_api": "1.44",
            "compose": "2.40.3",
            "root_dir": "/var/lib/docker",
        }
        self.installed = True
        self.down = False
        self.removed: list[str] = []
        self.listings = 0
        # The local images by id (what ``inspect`` says of them), and the registry: what each
        # reference is behind, by its canonical name (``docker.io/library/redis:7``).
        self.image_facts: dict[str, ImageFacts] = {}
        self.remote: dict[str, RemoteTag] = {}
        self.buildx = True
        self.denied: set[str] = set()
        self.pulled: dict[str, RemoteTag] = {}
        # Compose reading a document on its standard input: what ``config`` says it resolves to when a
        # test wants it to differ from the file (an ``include``), what it fails with, and what
        # ``up --dry-run`` would do.
        self.resolved: dict[str, Any] | None = None
        self.resolved_for: str | None = None
        self.compose_error = ""
        self.dry_run_output = ""
        self.dry_run_failure = ""

    # -- building the machine ---------------------------------------------------------------

    def box(self, name: str, **fields: Any) -> Box:
        """
        Add a container.

        Args:
            name: Its name.
            **fields: Anything :class:`Box` has.

        Returns:
            The container, to change further.
        """
        made = Box(name, **fields)
        self.boxes[name] = made
        return made

    def image(self, digit: str, repository: str, tag: str = "latest", size: str = "100MB") -> str:
        """
        Add an image.

        Args:
            digit: One hexadecimal digit its id is made of.
            repository: Its repository (``<none>`` for no name).
            tag: Its tag.
            size: How big Docker says it is.

        Returns:
            Its id.
        """
        image_id = "sha256:" + digit * 64
        self.images.append(
            {
                "ID": image_id,
                "Repository": repository,
                "Tag": tag,
                "CreatedAt": "2026-11-01 10:00:00 +0000 UTC",
                "Size": size,
            }
        )
        return image_id

    def facts(self, image_id: str, **fields: Any) -> ImageFacts:
        """
        Say what ``inspect`` answers for a local image.

        Args:
            image_id: Its id.
            **fields: Anything :class:`ImageFacts` has.

        Returns:
            The facts, to change further.
        """
        made = ImageFacts(**fields)
        self.image_facts[image_id] = made
        return made

    def volume(self, name: str, *, size: str | None = None, **labels: str) -> None:
        """
        Add a named volume.

        Args:
            name: Its name.
            size: What ``system df -v`` says it takes.
            **labels: ``project``, ``volume`` (its key in the compose file) and ``instance``.
        """
        self.volumes.append({"name": name, **labels})
        if size is not None:
            self.volume_sizes[name] = size

    def network(self, name: str, *, driver: str = "bridge", **labels: str) -> str:
        """
        Add a network.

        Args:
            name: Its name.
            driver: Its driver.
            **labels: ``project``.

        Returns:
            Its id.
        """
        ident = hashlib.sha256(name.encode()).hexdigest()
        self.networks.append({"id": ident, "name": name, "driver": driver, **labels})
        return ident

    # -- what the machine answers -----------------------------------------------------------

    def exists(self, program: str) -> bool:
        if program == "docker":
            return self.installed
        if program == "docker-compose":
            # Compose 1 is a program of its own, which a machine of this age does not have.
            return False
        return super().exists(program)

    def _lookup(
        self, argv: Sequence[str], user: str | None = None, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        result = super()._lookup(argv, user, env)
        recorded = tuple(argv)
        if recorded[:1] != ("docker",) or self._scripted_for(recorded):
            return result
        answered = self._docker(recorded)
        return CommandResult(
            argv=recorded,
            exit_code=answered[0],
            stdout=answered[1],
            stderr=answered[2],
        )

    def _docker(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        """
        Answer one ``docker`` command.

        Args:
            argv: The command.

        Returns:
            ``(exit code, stdout, stderr)``.
        """
        if self.down and argv[1:2] != ("version",):
            return 1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
        handlers: dict[tuple[str, ...], Callable[[tuple[str, ...]], tuple[int, str, str]]] = {
            ("version",): self._version,
            ("info",): self._info,
            ("compose", "version"): self._compose,
            ("compose",): self._compose_verb,
            ("context", "inspect"): self._context,
            ("ps",): self._ps,
            ("buildx", "imagetools"): self._imagetools,
            ("pull",): self._pull,
            ("inspect",): self._inspect,
            ("stats",): self._stats,
            ("logs",): self._logs,
            ("events",): self._events,
            ("image", "ls"): self._image_ls,
            ("volume", "ls"): self._volume_ls,
            ("network", "ls"): self._network_ls,
            ("system", "df"): self._system_df,
            ("start",): self._start,
            ("stop",): self._stop,
            ("restart",): self._restart,
            ("rm",): self._rm,
            ("image", "rm"): self._image_rm,
            ("volume", "rm"): self._volume_rm,
            ("network", "rm"): self._network_rm,
            ("builder", "prune"): self._builder_prune,
            ("image", "prune"): self._image_prune,
        }
        for size in (2, 1):
            handler = handlers.get(argv[1 : 1 + size])
            if handler is not None:
                return handler(argv)
        return 0, "", ""

    def _version(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        if self.down:
            body = {"Client": {"Version": "29.1.3", "ApiVersion": "1.52"}}
            return 1, json.dumps(body), "Cannot connect to the Docker daemon"
        body = {
            "Client": {"Version": "29.1.3", "ApiVersion": "1.52"},
            "Server": {
                "Version": self.engine["version"],
                "ApiVersion": self.engine["api"],
                "MinAPIVersion": self.engine["min_api"],
            },
        }
        return 0, json.dumps(body), ""

    def _info(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        body = {
            "Driver": "overlay2",
            "DockerRootDir": self.engine["root_dir"],
            "OperatingSystem": "Ubuntu 24.04",
            "SecurityOptions": ["name=seccomp,profile=builtin"],
            "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
        }
        return 0, json.dumps(body), ""

    def _compose(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        if not self.engine["compose"]:
            return 1, "", "docker: 'compose' is not a docker command."
        return 0, f"Docker Compose version v{self.engine['compose']}", ""

    def _compose_document(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        """
        ``docker compose -p NAME -f - config|up``: Compose reading a document from its standard input.

        ``config`` answers the document as a model (or ``resolved`` when a test says what an ``include`` or
        an ``extends`` would make of it), and fails with Compose's own words on what is not YAML.
        ``up --dry-run`` answers ``dry_run_output``.
        """
        import yaml

        text = (self.inputs[-1] if self.inputs else None) or ""
        if "config" in argv:
            if self.compose_error:
                return 1, "", self.compose_error
            try:
                model = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                line = mark.line + 1 if mark is not None else 1
                return (
                    1,
                    "",
                    f"validating <stdin>: yaml: line {line}: did not find expected ',' or ']'",
                )
            if not isinstance(model, dict):
                return 1, "", "validating <stdin>: services must be a mapping"
            says = self.resolved is not None and self.resolved_for in (None, text)
            return 0, json.dumps(self.resolved if says else model), ""
        if "--dry-run" in argv:
            if self.dry_run_failure:
                return 1, "", self.dry_run_failure
            return 0, "", self.dry_run_output
        return 0, "", ""

    def _compose_verb(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        """``docker compose -p NAME start|stop|restart``: what Compose does to a project's containers."""
        if "-f" in argv and argv[argv.index("-f") + 1 : argv.index("-f") + 2] == ("-",):
            return self._compose_document(argv)
        if "-p" not in argv:
            return 0, "", ""
        project = argv[argv.index("-p") + 1]
        rest = argv[argv.index("-p") + 2 :]
        verb = next((word for word in rest if word in ("start", "stop", "restart")), "")
        members = [box for box in self.boxes.values() if box.project == project]
        if verb and not members:
            return 1, "", f"no configuration file provided: not found ({project})"
        for box in members:
            if verb in ("start", "restart"):
                box.state, box.status = "running", "Up Less than a second"
            elif verb == "stop":
                box.state, box.status = "exited", "Exited (143) Less than a second ago"
        return 0, "", ""

    def _context(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        body = [{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
        return 0, json.dumps(body), ""

    def _ps(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        self.listings += 1
        rows = [
            {
                "id": box.id,
                "name": box.name,
                "image": box.image,
                "state": box.state,
                "status": box.status,
                "created": "2026-11-12 08:00:00 +0000 UTC",
                "ports": box.ports,
                "mounts": box.mounts,
                "networks": box.networks,
                "project": box.project,
                "service": box.service,
                "working_dir": box.working_dir,
                "config_files": box.config_files,
                "oneoff": "True" if box.oneoff else "False",
                "instance": box.instance,
                "helper": box.helper,
            }
            for box in self.boxes.values()
        ]
        return 0, "\n".join(json.dumps(row) for row in rows), ""

    def _inspect_row(self, box: Box) -> dict[str, Any]:
        mounts = box.mount_list or [
            {"Type": "volume", "Name": name, "Source": f"/var/lib/docker/volumes/{name}/_data"}
            | {"Destination": "/data", "RW": True}
            for name in (part for part in box.mounts.split(",") if part)
        ]
        state: dict[str, Any] = {
            "Status": box.state,
            "Running": box.state in ("running", "restarting", "paused"),
            "Paused": box.state == "paused",
            "Restarting": box.state == "restarting",
            "OOMKilled": box.oom,
            "ExitCode": box.exit_code,
            "Error": box.error,
            "StartedAt": box.started_at,
            "FinishedAt": box.finished_at,
        }
        if box.health:
            state["Health"] = {
                "Status": box.health,
                "FailingStreak": 3 if box.health == "unhealthy" else 0,
                "Log": [{"ExitCode": 1, "Output": box.health_output}] if box.health_output else [],
            }
        return {
            "id": box.id,
            "name": "/" + box.name,
            "image": box.image,
            "image_id": "sha256:" + "a" * 64,
            "state": state,
            "restart_count": box.restart_count,
            "host": {
                "RestartPolicy": {"Name": box.policy, "MaximumRetryCount": box.max_retries},
                "Memory": box.memory,
                "NanoCpus": box.nano_cpus,
                "Privileged": box.privileged,
            },
            "mounts": mounts,
            "networks": {name: {} for name in box.networks.split(",") if name},
            "ports": {},
        }

    def _inspect_image(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        wanted = list(argv[argv.index("--format") + 2 :])
        rows = []
        missing: list[str] = []
        for ident in wanted:
            facts = self.image_facts.get(ident)
            tag = self.pulled.get(ident)
            if facts is None and tag is not None:
                facts = ImageFacts(
                    repo_digests=[
                        f"{ident.rsplit(':', 1)[0].removeprefix('docker.io/library/')}@{tag.digest}"
                    ],
                    env=tag.env,
                    labels=tag.labels,
                    created=tag.created,
                )
            if facts is None:
                # Docker prints the ones it found and complains of the one it did not.
                missing.append(ident)
                continue
            rows.append(
                {
                    "id": ident,
                    "digests": facts.repo_digests,
                    "env": facts.env,
                    "labels": facts.labels,
                    "created": facts.created,
                    "os": facts.os,
                    "arch": facts.architecture,
                }
            )
        out = "\n".join(json.dumps(row) for row in rows)
        if missing:
            return 1, out, f"Error: No such image: {missing[0]}"
        return 0, out, ""

    def _imagetools(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        if not self.buildx:
            return 1, "", "docker: 'buildx' is not a docker command."
        reference = argv[-1]
        if reference in self.denied:
            return 1, "", f"ERROR: {reference}: unauthorized: authentication required"
        found = self.remote.get(reference)
        if found is None:
            return 1, "", f"ERROR: {reference}: not found"
        if "--format" not in argv:
            return (
                0,
                f"Name:      {reference}\nMediaType: application/vnd.oci.image.index.v1+json\nDigest:    {found.digest}\n",
                "",
            )
        if not found.config_readable:
            return 1, "", "ERROR: unknown flag: --format"
        config = {"created": found.created, "config": {"Env": found.env, "Labels": found.labels}}
        body = dict.fromkeys(found.platforms, config) if found.platforms else config
        return 0, json.dumps(body), ""

    def _pull(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        reference = argv[-1]
        if reference in self.denied:
            return (
                1,
                "",
                f"Error response from daemon: pull access denied for {reference}, unauthorized",
            )
        found = self.remote.get(reference)
        if found is None:
            return 1, "", f"Error response from daemon: manifest for {reference} not found"
        self.pulled[reference] = found
        return 0, f"Digest: {found.digest}\nStatus: Downloaded newer image for {reference}", ""

    def _inspect(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        if "--type" in argv and argv[argv.index("--type") + 1 : argv.index("--type") + 2] == (
            "image",
        ):
            return self._inspect_image(argv)
        wanted = [part for part in argv[2:] if not part.startswith("-")]
        # ``--format`` takes the next argument as its value, which is not an id.
        if "--format" in argv:
            wanted = list(argv[argv.index("--format") + 2 :])
        boxes = [box for box in self.boxes.values() if box.id in wanted or box.name in wanted]
        missing = [part for part in wanted if part not in {b.id for b in boxes} | set(self.boxes)]
        out = "\n".join(json.dumps(self._inspect_row(box)) for box in boxes)
        if missing and not boxes:
            return 1, "", f"Error: No such object: {missing[0]}"
        return 0, out, ""

    def _stats(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        ident = argv[-1]
        box = next((b for b in self.boxes.values() if b.id == ident), None)
        if box is None:
            return 1, "", f"Error response from daemon: No such container: {ident}"
        reading = box.stats or {
            "CPUPerc": "14.50%",
            "MemUsage": "212MiB / 512MiB",
            "MemPerc": "41.41%",
            "NetIO": "38kB / 91kB",
            "BlockIO": "1.2MB / 0B",
            "PIDs": "12",
        }
        return 0, json.dumps({"ID": ident[:12], "Name": box.name, **reading}), ""

    def _logs(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        ident = argv[-1]
        box = next((b for b in self.boxes.values() if b.id == ident), None)
        if box is None:
            return 1, "", f"Error response from daemon: No such container: {ident}"
        return 0, box.log, ""

    def _events(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        lines = []
        for box in self.boxes.values():
            for _ in range(box.restarts_recent):
                event = {
                    "Type": "container",
                    "Action": "restart",
                    "Actor": {"ID": box.id, "Attributes": {"name": box.name}},
                    "time": 1_700_000_000,
                }
                lines.append(json.dumps(event))
        return 0, "\n".join(lines), ""

    def _image_ls(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        return 0, "\n".join(json.dumps(row) for row in self.images), ""

    def _volume_ls(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        return 0, "\n".join(json.dumps(row) for row in self.volumes), ""

    def _network_ls(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        return 0, "\n".join(json.dumps(row) for row in self.networks), ""

    def _system_df(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        if "-v" in argv:
            body = {
                "Volumes": [
                    {"Name": name, "Size": size} for name, size in self.volume_sizes.items()
                ]
            }
            return 0, json.dumps(body), ""
        return 0, "\n".join(json.dumps(row) for row in self.df), ""

    def _find(self, ident: str) -> Box | None:
        return next((b for b in self.boxes.values() if b.id == ident), None)

    def _start(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        box = self._find(argv[-1])
        if box is None:
            return 1, "", f"Error response from daemon: No such container: {argv[-1]}"
        box.state, box.status = "running", "Up Less than a second"
        return 0, argv[-1], ""

    def _stop(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        box = self._find(argv[-1])
        if box is None:
            return 1, "", f"Error response from daemon: No such container: {argv[-1]}"
        box.state, box.status = "exited", "Exited (143) Less than a second ago"
        return 0, argv[-1], ""

    def _restart(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        box = self._find(argv[-1])
        if box is None:
            return 1, "", f"Error response from daemon: No such container: {argv[-1]}"
        box.state, box.status = "running", "Up Less than a second"
        return 0, argv[-1], ""

    def _rm(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        box = self._find(argv[-1])
        if box is None:
            return 1, "", f"Error response from daemon: No such container: {argv[-1]}"
        if box.state == "running" and "--force" not in argv:
            return 1, "", "Error response from daemon: cannot remove a running container"
        del self.boxes[box.name]
        self.removed.append(f"container:{box.name}")
        return 0, argv[-1], ""

    def _image_rm(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        before = len(self.images)
        self.images = [row for row in self.images if row["ID"] != argv[-1]]
        if len(self.images) == before:
            return 1, "", f"Error response from daemon: No such image: {argv[-1]}"
        self.removed.append(f"image:{argv[-1]}")
        return 0, f"Deleted: {argv[-1]}", ""

    def _volume_rm(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        before = len(self.volumes)
        self.volumes = [row for row in self.volumes if row["name"] != argv[-1]]
        if len(self.volumes) == before:
            return 1, "", f"Error response from daemon: get {argv[-1]}: no such volume"
        self.removed.append(f"volume:{argv[-1]}")
        return 0, argv[-1], ""

    def _network_rm(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        before = len(self.networks)
        self.networks = [row for row in self.networks if row["id"] != argv[-1]]
        if len(self.networks) == before:
            return 1, "", f"Error response from daemon: network {argv[-1]} not found"
        self.removed.append(f"network:{argv[-1]}")
        return 0, argv[-1], ""

    def _builder_prune(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        self.removed.append("cache")
        return 0, "Total reclaimed space: 1.2GB", ""

    def _image_prune(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        self.removed.append("dangling")
        return 0, "Total reclaimed space: 640MB", ""

    # -- asserting --------------------------------------------------------------------------

    def changes(self) -> list[tuple[str, ...]]:
        """
        Returns:
            The commands that changed something, in order: what is not a look.
        """
        from noust.core.runner import is_read_only

        return [call for call in self.calls if not is_read_only(call)]

    def commands_for_docker(self) -> list[tuple[str, ...]]:
        """Returns: Every ``docker`` command that ran, in order."""
        return [call for call in self.calls if call[:1] == ("docker",)]


__all__ = [
    "BLOG",
    "RELEASE",
    "SHOP",
    "Box",
    "DockerMachine",
    "ImageFacts",
    "RemoteTag",
    "full_id",
]
