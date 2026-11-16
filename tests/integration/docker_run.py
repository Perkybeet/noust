#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Everything Noust does with Docker, against real Docker engines of three ages.

The unit tests of ``noust.core.docker`` and of the Docker zone drive the code against doubles whose output was
written from Docker's documentation, which can only agree with the code. This harness holds them to the real
thing, on the three Docker engines a Noust meets:

- ``e20``: Docker 20.10 (API 1.41), the oldest Noust works with: the Docker of Debian 12, with Compose 2.17;
- ``e24``: Docker 24.0.2 (API 1.43), the one on a Synology, kept in ``/volume1/@docker`` as DSM keeps it,
  with Compose 2.19;
- ``e29``: Docker 29.x (the engine of a UGREEN), with Compose 5.

Each engine is the official Docker-in-Docker image of its age in a container of its own: the engine's
container is the machine. ``docker`` and ``docker compose`` there are the engine's own, as on a server that has
Docker and nothing else, and Noust's code runs in that machine (``docker_inside.py``) or as the central's image
built from ``packaging/container/`` and run there the way a NAS runs it (``docker_central.py``): the hub with the
socket mounted and ``NOUST_DEPLOY_HERE=containers``. Compose 2.40.3, which the central's image carried before the
one it carries now, and Compose 2.0.0, the oldest Noust admits, are put beside the one each engine brings (an
engine that refuses a Compose says so, and that is a result); the doctor also runs once with Compose hidden, which
is what a server that has Docker and no Compose is.

A local registry runs in each engine (``localhost:5000``, plain HTTP, which Docker and buildx accept only
for ``localhost``) with a few small images of two versions each, so what is asked of a registry is asked of a real
one, and nothing that is pulled weighs more than a few megabytes.

The scenarios, each one a named check on every engine (the numbers are those of the lane's brief):

==  ==========================  ========================================================================
 1  ``doctor``                  what Docker there is, and what does not fit in it, in sentences
 2  ``reading``                 containers, images, volumes and networks as Docker prints them
 3  ``owners``                  whose each thing is: an application, a stack, an engine, nobody
 4  ``container_inside``        why a container ended, what it uses, its restarts, its log, a console
 5  ``cleanup``                 actions judged by owner; a cleanup removes what its plan said and no more
 6  ``privileges``              the guard of privileges over what Compose resolves
 7  ``versions``                a registry asked for a new version with and without buildx
 8  ``update_gate``             an image updated behind the health gate; one that does not start is undone
 9  ``compose_check``           a Compose read on the standard input, and rehearsed
10  ``compose_edit``            editing a stack's Compose end to end
11  ``bring``                   a stack that already ran, brought to Noust without a restart
12  ``client_negotiates``       the central's own Docker client against each engine
13  ``precheck``                the pre-flight of a central that deploys here
14  ``recipe``                  a recipe: deploy, stop, start, a failed update, back, delete
15  ``reach``                   the probe reaches the service from an unprivileged central
16  ``busy_port``               a port the machine already has
17  ``volume_copy``             a scheduled copy of a volume, and one missed while the central was off
18  ``how_it_runs``             choosing how an application runs on a machine that has only Docker
==  ==========================  ========================================================================

Nothing here touches the machine's own containers. Everything it creates on the machine's Docker is named
``noust-test-w3rd-*`` and labelled ``noust.test=1``, is removed by exact name when the run ends (``--keep``
leaves it), and nothing is ever pruned. A scenario that cannot run on an engine reports why, never a pass. Not
part of pytest (it does not match ``test_*.py``).

Usage (the machine's Docker is shared: one run at a time, through ``noust-docker``):
    noust-docker 2400 .venv/bin/python tests/integration/docker_run.py
    noust-docker 2400 .venv/bin/python tests/integration/docker_run.py --engine e24 --scenario doctor --keep
    noust-docker 2400 .venv/bin/python tests/integration/docker_run.py --engine e24 --reuse --scenario reading

The results are written to ``tests/integration/results/docker-engines.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))
# The central's scenarios are modules beside this file that import each other by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

#: What every container, volume, network and image the harness makes is called, and the label they carry.
PREFIX = os.environ.get("NOUST_IT_PREFIX", "noust-test-w3rd")
LABEL = "noust.test=1"

#: Where the harness keeps what it builds and downloads, outside the repository.
CACHE = Path(
    os.environ.get("NOUST_IT_CACHE", str(Path.home() / ".cache" / "noust-runs" / "4.3" / "w3rd"))
)

RESULTS = REPO_ROOT / "tests" / "integration" / "results" / "docker-engines.json"

#: Free room the machine's disk needs before a run starts, in gigabytes.
MIN_FREE_GB = float(os.environ.get("NOUST_IT_MIN_FREE_GB", "30"))

#: The machine inside the engine's container: where the scenarios keep their files and the NAS-like folders.
MACHINE_WORK = "/srv/w3rd"
NAS_DATA = "/volume1/docker/noust"
NAS_STACKS = "/volume1/docker/stacks"

LOCAL_REGISTRY = "localhost:5000"

#: The engines' own network, and the addresses each engine gives what it runs. An engine's container on the
#: machine's default bridge (172.17.0.0/16) and the engine's own ``docker0`` (also 172.17.0.0/16) are one address range
#: twice: the engine reaches nothing it starts and its DNS answers never come back. The engines are on a network of
#: their own, and what runs in them gets addresses from ranges nothing outside uses.
NETWORK = f"{PREFIX}-net"
INNER_BRIDGE = "10.213.0.1/24"
INNER_POOL = "base=10.214.0.0/16,size=24"


def _load(name: str) -> ModuleType:
    """
    Load a sibling harness as a module, without touching ``sys.path``.

    Args:
        name: The file, without ``.py``.

    Returns:
        The module.
    """
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_noust_it_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------- the engines


@dataclass(frozen=True)
class Engine:
    """
    One Docker engine, in a container of its own.

    Attributes:
        key: ``e20``, ``e24`` or ``e29``.
        tag: The tag of the official ``docker`` image that carries it.
        server: The start of the version it must say it is.
        api: The API it must speak, when the tag fixes it (the 29 line moves).
        compose: The start of the version of the Compose it brings.
        data_root: Where it keeps its data inside its container.
        note: Which machine it stands for.
        variants: The Compose versions put beside the bundled one that this engine can use.
    """

    key: str
    tag: str
    server: str
    api: str
    compose: str
    data_root: str
    note: str
    variants: tuple[str, ...] = ("v2.40.3", "v2.0.0")

    @property
    def container(self) -> str:
        """The container that is the machine."""
        return f"{PREFIX}-{self.key}"

    @property
    def image(self) -> str:
        """The image the machine is made from: the engine, with Python for Noust's own code."""
        return f"{PREFIX}-{self.key}-image:latest"

    @property
    def volume(self) -> str:
        """The volume the engine keeps its data in."""
        return f"{PREFIX}-{self.key}-lib"


ENGINES: dict[str, Engine] = {
    "e20": Engine(
        "e20",
        "20.10-dind",
        "20.10",
        "1.41",
        "2.17",
        "/var/lib/docker",
        "the minimum Noust works with (Debian 12)",
    ),
    "e24": Engine(
        "e24",
        "24.0.2-dind",
        "24.0.2",
        "1.43",
        "2.19",
        "/volume1/@docker",
        "a Synology of today (DSM keeps Docker in /volume1/@docker)",
    ),
    "e29": Engine(
        "e29",
        "29-dind",
        "29.",
        "",
        "5.",
        "/var/lib/docker",
        "a UGREEN of today (the current engine)",
        variants=("v2.40.3", "v2.0.0"),
    ),
}

#: The variant of a scenario that runs with Compose taken out of the engine's machine for the time it takes.
NO_COMPOSE = "no-compose"

#: Where the engine's machine keeps Compose: the plugin of ``docker compose`` and the program beside it.
COMPOSE_FILES = (
    "/usr/local/libexec/docker/cli-plugins/docker-compose",
    "/usr/local/bin/docker-compose",
)

#: Compose releases put beside the bundled one: ``(version, file, the sha256 its release publishes)``.
COMPOSE_RELEASES = {
    "v2.40.3": "https://github.com/docker/compose/releases/download/v2.40.3/docker-compose-linux-x86_64",
    "v2.0.0": "https://github.com/docker/compose/releases/download/v2.0.0/docker-compose-linux-amd64",
}

ENGINE_DOCKERFILE = """\
ARG DIND
FROM ${DIND}
RUN apk add --no-cache python3 py3-pip git \\
 && python3 -m venv /opt/venv \\
 && /opt/venv/bin/pip install --no-cache-dir click Jinja2 PyYAML questionary rich psutil
ENV PATH=/opt/venv/bin:$PATH
"""


def _fetch(url: str) -> bytes:
    """
    Args:
        url: An https address.

    Returns:
        What it serves.
    """
    with urllib.request.urlopen(url, timeout=300) as response:
        data: bytes = response.read()
    return data


# ------------------------------------------------------------------------------- the machine's Docker


def docker(
    *args: str, input_text: str | None = None, check: bool = False, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    """
    Run the machine's own Docker client, with a clean configuration.

    Args:
        *args: Its arguments.
        input_text: Standard input.
        check: Raise on a non-zero exit.
        timeout: Seconds.

    Returns:
        The completed process.
    """
    config = Path(tempfile.gettempdir()) / "noust-test-docker-config"
    config.mkdir(exist_ok=True)
    if not (config / "config.json").exists():
        (config / "config.json").write_text("{}", encoding="utf-8")
    try:
        return subprocess.run(
            ["docker", *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
            env=dict(os.environ, DOCKER_CONFIG=str(config)),
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            ["docker", *args], 124, str(exc.stdout or ""), f"timed out after {timeout}s"
        )


def inside(
    engine: Engine,
    *args: str,
    input_text: str | None = None,
    timeout: int = 300,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run a command in an engine's machine.

    Args:
        engine: The machine.
        *args: The command.
        input_text: Standard input.
        timeout: Seconds.
        env: Variables for it.

    Returns:
        The completed process.
    """
    options = [item for key, value in (env or {}).items() for item in ("-e", f"{key}={value}")]
    return docker(
        "exec",
        *(["-i"] if input_text is not None else []),
        *options,
        engine.container,
        *args,
        input_text=input_text,
        timeout=timeout,
    )


def free_gb() -> float | None:
    """
    Returns:
        The free room of the machine's disk in gigabytes: C: asked of Windows from WSL, the home
        directory's disk elsewhere. None when neither can be read.
    """
    powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if powershell.exists():
        done = subprocess.run(
            ["/init", str(powershell), "-NoProfile", "-Command", "(Get-PSDrive C).Free/1GB"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        try:
            return float(done.stdout.strip().replace(",", "."))
        except ValueError:
            pass
    try:
        return shutil.disk_usage(Path.home()).free / 1024**3
    except OSError:
        return None


# ------------------------------------------------------------------------------- results


@dataclass
class Outcome:
    """
    What one scenario found on one engine.

    Attributes:
        status: ``pass``, ``fail``, ``error``, ``na`` (it does not apply here) or ``skip``.
        checks: Every check, ``(name, ok, detail)``.
        notes: What the real engine said, for the report.
        skips: The checks that could not be made, with why.
        reason: Why a scenario did not apply, or what broke.
        seconds: How long it took.
    """

    status: str = "pass"
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    skips: list[tuple[str, str]] = field(default_factory=list)
    reason: str = ""
    seconds: float = 0.0

    def finish(self) -> Outcome:
        """
        Returns:
            This outcome, with its status decided by its checks.
        """
        if self.status in ("na", "skip", "error"):
            return self
        self.status = "fail" if any(not ok for _, ok, _ in self.checks) else "pass"
        return self

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as the results file keeps it: a passing check by its name only.
        """
        return {
            "status": self.status,
            "seconds": round(self.seconds, 1),
            "reason": self.reason,
            "passed": [name for name, ok, _ in self.checks if ok],
            "failed": [
                {"check": name, "detail": detail} for name, ok, detail in self.checks if not ok
            ],
            "skipped": [{"check": name, "why": why} for name, why in self.skips],
            "notes": self.notes,
        }


def parse_inside(output: str) -> Outcome:
    """
    Read what ``docker_inside.py`` printed.

    Args:
        output: Its standard output.

    Returns:
        The outcome: one line a check, a note or a skip.
    """
    outcome = Outcome()
    for line in output.splitlines():
        if not line.startswith("@@"):
            continue
        try:
            row = json.loads(line[2:])
        except ValueError:
            continue
        if "check" in row:
            outcome.checks.append((row["check"], bool(row["ok"]), row.get("detail", "")))
        elif "note" in row:
            outcome.notes[row["note"]] = row["value"]
        elif "skip" in row:
            outcome.skips.append((row["skip"], row["why"]))
        elif "na" in row:
            outcome.status, outcome.reason = "na", row["na"]
        elif "error" in row:
            outcome.status, outcome.reason = "error", row["error"]
    return outcome


# ------------------------------------------------------------------------------- the scenarios


@dataclass(frozen=True)
class Spec:
    """
    A scenario of the table.

    Attributes:
        number: The brief's number.
        name: What ``--scenario`` matches.
        title: One sentence.
        where: ``inside`` (Noust's code in the engine's machine) or ``central`` (the central's image).
        compose_variants: Whether it also runs with each Compose put beside the engine's own.
        slow: Seconds it is given.
    """

    number: int
    name: str
    title: str
    where: str = "inside"
    compose_variants: bool = False
    slow: int = 600


SPECS = (
    Spec(1, "doctor", "the engine's doctor", compose_variants=True),
    Spec(2, "reading", "the readings of containers, images, volumes and networks"),
    Spec(3, "owners", "whose each object is"),
    Spec(4, "container_inside", "a container from the inside"),
    Spec(5, "cleanup", "actions by owner and the cleanup plan"),
    Spec(
        6, "privileges", "the guard of privileges over what Compose resolves", compose_variants=True
    ),
    Spec(7, "versions", "new versions of images from a registry"),
    Spec(8, "update_gate", "updating an image behind the health gate", "central"),
    Spec(
        9,
        "compose_check",
        "a Compose read on the standard input and rehearsed",
        compose_variants=True,
    ),
    Spec(10, "compose_edit", "editing a stack's Compose end to end", "central"),
    Spec(11, "bring", "a stack that already ran, brought to Noust", "central"),
    Spec(12, "client_negotiates", "the central's Docker client against the engine", "central"),
    Spec(13, "precheck", "the pre-flight of a central that deploys here", "central"),
    Spec(14, "recipe", "a recipe from deploy to delete", "central"),
    Spec(15, "reach", "the probe reaches the service from an unprivileged central", "central"),
    Spec(16, "busy_port", "a port the machine already has", "central"),
    Spec(17, "volume_copy", "a scheduled copy of a volume", "central"),
    Spec(18, "how_it_runs", "choosing how an application runs here", "central"),
)


# ------------------------------------------------------------------------------- the run


class Harness:
    """
    One run: the engines it starts, the central it builds, what each scenario found.

    Args:
        options: The command line.
    """

    def __init__(self, options: argparse.Namespace) -> None:
        self.options = options
        self.engines = [ENGINES[key] for key in options.engine]
        self.results: dict[str, dict[str, Outcome]] = {engine.key: {} for engine in self.engines}
        self.versions: dict[str, dict[str, str]] = {}
        self.started = time.monotonic()
        self.central: list[ModuleType] = []
        pair = options.client_pair
        self.client_versions = self.pinned_client_versions(options.client_pair)
        self.central_image_name = (
            f"{PREFIX}-central-{pair.replace(':', '-c').replace('.', '')}:latest"
            if pair
            else f"{PREFIX}-central:latest"
        )
        self.registry = LOCAL_REGISTRY

    # -- what this run creates ----------------------------------------------------------------

    def say(self, text: str) -> None:
        """
        Args:
            text: A line of progress.
        """
        print(f"[{time.monotonic() - self.started:6.0f}s] {text}", flush=True)

    def preflight(self) -> None:
        """
        Refuse to start when Docker is not there or the disk has no room.

        Raises:
            SystemExit: Docker does not answer, or the free room is under the floor.
        """
        if (
            shutil.which("docker") is None
            or docker("version", "--format", "{{.Server.Version}}").returncode != 0
        ):
            raise SystemExit("Docker does not answer: nothing was started")
        free = free_gb()
        if free is not None and free < MIN_FREE_GB:
            raise SystemExit(
                f"only {free:.0f} GB are free and the floor is {MIN_FREE_GB:.0f} GB: nothing was started"
            )
        self.say(
            f"Docker {docker('version', '--format', '{{.Server.Version}}').stdout.strip()}, "
            f"{'?' if free is None else f'{free:.0f}'} GB free"
        )

    def ensure_engine_images(self) -> None:
        """Build the image of each engine: the official one with Python for Noust's own code."""
        context = CACHE / "engine-ctx"
        context.mkdir(parents=True, exist_ok=True)
        (context / "Dockerfile").write_text(ENGINE_DOCKERFILE, encoding="utf-8")
        for engine in self.engines:
            have = docker("image", "inspect", engine.image).returncode == 0
            if have and not self.options.rebuild:
                continue
            self.say(f"building {engine.image} from docker:{engine.tag}")
            built = docker(
                "build",
                "-q",
                "--label",
                LABEL,
                "--build-arg",
                f"DIND=docker:{engine.tag}",
                "-t",
                engine.image,
                str(context),
                timeout=900,
            )
            if built.returncode != 0:
                raise SystemExit(f"the image of {engine.key} did not build: {built.stderr[-600:]}")

    @staticmethod
    def pinned_client_versions(pair: str | None) -> tuple[str, str, str]:
        """
        Args:
            pair: ``--client-pair``, or None.

        Returns:
            The versions of Docker's command line, Compose's plugin and buildx's that the central's image carries:
            the ones asked for, and for what is not asked the ones ``install_docker_client.py`` pins.
        """
        text = (REPO_ROOT / "packaging" / "container" / "install_docker_client.py").read_text(
            encoding="utf-8"
        )
        pinned = tuple(
            re.search(rf'{name}_VERSION = "([^"]*)"', text).group(1)  # type: ignore[union-attr]
            for name in ("DOCKER", "COMPOSE", "BUILDX")
        )
        given = pair.split(":") if pair else []
        return (
            given[0] if len(given) > 0 else pinned[0],
            given[1] if len(given) > 1 else pinned[1],
            given[2] if len(given) > 2 else pinned[2],
        )

    @staticmethod
    def source_hash() -> str:
        """
        Returns:
            A fingerprint of what the central's image is built from (Noust's source and the packaging of the
            image), so that an image made from other code is made again and not trusted for its name.
        """
        digest = hashlib.sha256()
        files = sorted((REPO_ROOT / "src" / "noust").rglob("*.py")) + sorted(
            (REPO_ROOT / "packaging" / "container").glob("*")
        )
        for path in files:
            if path.is_file():
                digest.update(str(path.relative_to(REPO_ROOT)).encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    def central_image(self) -> str:
        """
        Build the central's image from ``packaging/container/`` with the wheel of this tree.

        Returns:
            Its name.

        Raises:
            SystemExit: The wheel or the image did not build.
        """
        name = self.central_image_name
        source = self.source_hash()
        built = docker(
            "image", "inspect", "--format", '{{index .Config.Labels "w3rd.source"}}', name
        )
        if built.returncode == 0 and built.stdout.strip() == source and not self.options.rebuild:
            return name
        context = CACHE / "central-ctx"
        shutil.rmtree(context, ignore_errors=True)
        (context / "dist").mkdir(parents=True)
        self.say("building the wheel of this tree")
        wheel = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(context / "dist"),
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if wheel.returncode != 0:
            raise SystemExit(f"the wheel did not build: {wheel.stderr[-600:]}")
        shutil.copy(REPO_ROOT / "packaging" / "install.sh", context / "dist" / "install.sh")
        for item in ("Dockerfile", "install_docker_client.py"):
            shutil.copy(REPO_ROOT / "packaging" / "container" / item, context / item)
        if self.options.client_pair:
            self.pin_client_pair(context / "install_docker_client.py", self.options.client_pair)
        version = next(path.name.split("-")[1] for path in (context / "dist").glob("noust-*.whl"))
        self.say(f"building {name} (Noust {version})")
        built = docker(
            "build",
            "-q",
            "--label",
            LABEL,
            "--label",
            f"w3rd.source={source}",
            "--build-arg",
            f"NOUST_VERSION={version}",
            "-t",
            name,
            str(context),
            timeout=1500,
        )
        if built.returncode != 0:
            raise SystemExit(f"the central's image did not build: {built.stderr[-800:]}")
        return name

    def pin_client_pair(self, installer: Path, pair: str) -> None:
        """
        Make the copy of the image's installer fetch other Docker programs, each fixed by a sum read from what
        its own release publishes (the ``checksums.txt`` of Compose and of buildx) or from the file as downloaded
        (Docker publishes none for its static packages), for both processors the image is built for.

        Args:
            installer: The copy of ``install_docker_client.py`` in the build context.
            pair: ``<docker>:<compose>`` or ``<docker>:<compose>:<buildx>``, as ``29.9.3:5.7.1:0.39.0``; the
                buildx the installer already names when it is left out.

        Raises:
            SystemExit: A file is not the one its release publishes.
        """
        text = installer.read_text(encoding="utf-8")
        docker_version, compose_version, *rest = pair.split(":")
        buildx_version = (
            rest[0] if rest else re.search(r'BUILDX_VERSION = "([^"]*)"', text).group(1)
        )  # type: ignore[union-attr]
        self.say(
            f"pinning Docker {docker_version}, Compose {compose_version} and buildx {buildx_version} "
            "for the central's image"
        )

        def published(release: str, name: str) -> bytes:
            """The file of a GitHub release, checked against that release's checksums.txt."""
            sums = _fetch(f"{release}/checksums.txt").decode()
            wanted = next(
                (row.split()[0] for row in sums.splitlines() if row.rstrip().endswith(name)), ""
            )
            data = _fetch(f"{release}/{name}")
            if hashlib.sha256(data).hexdigest() != wanted:
                raise SystemExit(f"{name} of {release} is not what its checksums.txt says")
            return data

        rows = []
        for machine, buildx_arch in (("x86_64", "amd64"), ("aarch64", "arm64")):
            package = _fetch(
                f"https://download.docker.com/linux/static/stable/{machine}/docker-{docker_version}.tgz"
            )
            compose = published(
                f"https://github.com/docker/compose/releases/download/v{compose_version}",
                f"docker-compose-linux-{machine}",
            )
            buildx = published(
                f"https://github.com/docker/buildx/releases/download/v{buildx_version}",
                f"buildx-v{buildx_version}.linux-{buildx_arch}",
            )
            rows.append(
                f'    "{machine}": (\n        "{machine}",\n'
                f'        "{hashlib.sha256(package).hexdigest()}",\n'
                f'        "{hashlib.sha256(compose).hexdigest()}",\n'
                f'        "{hashlib.sha256(buildx).hexdigest()}",\n    ),\n'
            )
        start, end = text.index("PINS: dict"), text.index("#: What buildx's releases call")
        text = (
            text[:start]
            + "PINS: dict[str, tuple[str, str, str, str]] = {\n"
            + "".join(rows)
            + "}\n\n"
            + text[end:]
        )
        text = re.sub(r'DOCKER_VERSION = "[^"]*"', f'DOCKER_VERSION = "{docker_version}"', text)
        text = re.sub(r'COMPOSE_VERSION = "[^"]*"', f'COMPOSE_VERSION = "{compose_version}"', text)
        text = re.sub(r'BUILDX_VERSION = "[^"]*"', f'BUILDX_VERSION = "{buildx_version}"', text)
        installer.write_text(text, encoding="utf-8")
        (CACHE / f"pins-{docker_version}-{compose_version}-{buildx_version}.txt").write_text(
            "".join(rows), encoding="utf-8"
        )

    def compose_binaries(self) -> Path:
        """
        Fetch the Compose releases that are put beside each engine's own, once, each checked against the sum
        its own release publishes (a file that is not the one published is not used).

        Returns:
            The folder, holding ``<version>/cli-plugins/docker-compose`` for each.

        Raises:
            SystemExit: A file is not the one its release publishes, or the release publishes no sum.
        """
        root = CACHE / "compose"
        for version, url in COMPOSE_RELEASES.items():
            target = root / version / "cli-plugins" / "docker-compose"
            if target.exists():
                continue
            self.say(f"fetching Compose {version}")
            target.parent.mkdir(parents=True, exist_ok=True)
            data = _fetch(url)
            published = ""
            for candidate in (url + ".sha256", url.rsplit("/", 1)[0] + "/checksums.txt"):
                try:
                    published = _fetch(candidate).decode()
                    break
                except OSError:
                    continue
            digest = hashlib.sha256(data).hexdigest()
            if digest not in published:
                raise SystemExit(
                    f"Compose {version}: {digest} is not in the sums its release publishes"
                )
            target.write_bytes(data)
            target.chmod(0o755)
        return root

    # -- an engine ----------------------------------------------------------------------------

    def start_engine(self, engine: Engine, compose_root: Path) -> None:
        """
        Start an engine's container and wait until its Docker answers.

        Args:
            engine: Which.
            compose_root: Where the extra Compose releases are.

        Raises:
            SystemExit: It did not come up.
        """
        reused = docker("inspect", "--type", "container", engine.container).returncode == 0
        if reused and self.options.reuse:
            self.say(f"{engine.key}: reusing {engine.container}")
        else:
            docker("rm", "-f", "-v", engine.container)
            # An engine starts from nothing: what is left of an earlier one holds its old networks.
            docker("volume", "rm", "-f", engine.volume)
            if docker("network", "inspect", NETWORK).returncode != 0:
                docker("network", "create", "--label", LABEL, NETWORK)
            docker("volume", "create", "--label", LABEL, engine.volume)
            started = docker(
                "run",
                "-d",
                "--name",
                engine.container,
                "--label",
                LABEL,
                "--privileged",
                "--network",
                NETWORK,
                "-e",
                "DOCKER_TLS_CERTDIR=/certs",
                "-v",
                f"{engine.volume}:{engine.data_root}",
                "-v",
                f"{REPO_ROOT / 'src'}:/noust/src:ro",
                "-v",
                f"{REPO_ROOT / 'tests' / 'integration'}:/harness:ro",
                "-v",
                f"{compose_root}:/opt/compose-releases:ro",
                engine.image,
                f"--data-root={engine.data_root}",
                f"--bip={INNER_BRIDGE}",
                f"--default-address-pool={INNER_POOL}",
            )
            if started.returncode != 0:
                raise SystemExit(f"{engine.key} did not start: {started.stderr[-500:]}")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if inside(engine, "docker", "info", timeout=30).returncode == 0:
                break
            time.sleep(1)
        else:
            raise SystemExit(
                f"{engine.key}: Docker did not answer in two minutes: {docker('logs', '--tail', '20', engine.container).stdout[-600:]}"
            )
        said = inside(
            engine,
            "docker",
            "version",
            "--format",
            "{{.Server.Version}} {{.Server.APIVersion}} {{.Server.MinAPIVersion}}",
        ).stdout.split()
        compose = inside(engine, "docker", "compose", "version", "--short").stdout.strip()
        self.versions[engine.key] = {
            "server": said[0] if said else "",
            "api": said[1] if len(said) > 1 else "",
            "min_api": said[2] if len(said) > 2 else "",
            "compose": compose,
            "machine": engine.note,
            "data_root": engine.data_root,
        }
        self.say(
            f"{engine.key}: Docker {self.versions[engine.key]['server']} (API {self.versions[engine.key]['api']}), "
            f"Compose {compose}"
        )
        if not self.versions[engine.key]["server"].startswith(engine.server):
            raise SystemExit(
                f"{engine.key} says {self.versions[engine.key]['server']}, not {engine.server}"
            )

    def prepare_machine(self, engine: Engine, central_image: str | None) -> None:
        """
        Make the engine's machine what a NAS is: its shared folders, its local registry, the central's image.

        Args:
            engine: Which.
            central_image: The central's image, when a scenario needs it.
        """
        inside(
            engine,
            "sh",
            "-c",
            f"mkdir -p {MACHINE_WORK} {NAS_DATA}/apps {NAS_STACKS} && chown -R 10001:10001 {NAS_DATA}"
            # Docker's configuration folders must be writable (Compose and buildx keep state in them).
            " && rm -rf /opt/compose && cp -r /opt/compose-releases /opt/compose",
        )
        if (
            inside(engine, "docker", "inspect", "--type", "container", "w3rd-registry").returncode
            != 0
        ):
            self.say(f"{engine.key}: starting the local registry")
            pulled = inside(engine, "docker", "pull", "-q", "registry:2", timeout=300)
            if pulled.returncode != 0:
                raise SystemExit(
                    f"{engine.key}: registry:2 cannot be pulled: {pulled.stderr[-300:]}"
                )
            inside(
                engine,
                "docker",
                "run",
                "-d",
                "--name",
                "w3rd-registry",
                "--label",
                LABEL,
                "--restart",
                "unless-stopped",
                "-p",
                "127.0.0.1:5000:5000",
                "registry:2",
            )
        seeded = self.run_inside(engine, "_seed", variant="bundled", timeout=900)
        if seeded.status != "pass":
            raise SystemExit(
                f"{engine.key}: the registry could not be seeded: {seeded.reason or seeded.checks}"
            )
        if (
            central_image is not None
            and inside(engine, "docker", "image", "inspect", central_image).returncode != 0
        ):
            self.say(f"{engine.key}: loading the central's image")
            saved = subprocess.Popen(["docker", "save", central_image], stdout=subprocess.PIPE)
            loaded = subprocess.run(
                ["docker", "exec", "-i", engine.container, "docker", "load"],
                stdin=saved.stdout,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            saved.wait()
            if loaded.returncode != 0:
                raise SystemExit(
                    f"{engine.key}: the central's image did not load: {loaded.stderr[-500:]}"
                )

    def run_inside(
        self,
        engine: Engine,
        name: str,
        *,
        variant: str = "bundled",
        timeout: int = 600,
        extra_env: dict[str, str] | None = None,
    ) -> Outcome:
        """
        Run one scenario of ``docker_inside.py`` in an engine's machine.

        Args:
            engine: Which.
            name: The scenario.
            variant: ``bundled`` or the Compose release put in front of the engine's own.
            timeout: Seconds.
            extra_env: More variables for the scenario (the arguments of a helper one).

        Returns:
            What it found.
        """
        env = {
            "W3RD_ENGINE": engine.key,
            "W3RD_VARIANT": variant,
            "W3RD_REGISTRY": LOCAL_REGISTRY,
            "W3RD_WORK": MACHINE_WORK,
            "HOME": "/tmp/w3rd-home",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if variant not in ("bundled", NO_COMPOSE):
            env["DOCKER_CONFIG"] = f"/opt/compose/{variant}"
        env.update(extra_env or {})
        began = time.monotonic()
        hidden = variant == NO_COMPOSE
        if hidden:
            self._hide_compose(engine, True)
        try:
            done = inside(
                engine, "python3", "/harness/docker_inside.py", name, timeout=timeout, env=env
            )
        finally:
            if hidden:
                self._hide_compose(engine, False)
        outcome = parse_inside(done.stdout)
        outcome.seconds = time.monotonic() - began
        if outcome.status == "pass" and done.returncode != 0:
            outcome.status, outcome.reason = "error", (done.stderr or done.stdout)[-1500:]
        if outcome.status == "error" and not outcome.reason:
            outcome.reason = (done.stderr or done.stdout)[-1500:]
        return outcome.finish()

    def _hide_compose(self, engine: Engine, hide: bool) -> None:
        """
        Take Compose out of an engine's machine, or put it back.

        The files are renamed and never removed, and the rename is undone in the same call that did it
        (``run_inside``), so an interrupted run leaves a ``.hidden`` beside each and nothing lost.

        Args:
            engine: Which.
            hide: ``True`` to hide it, ``False`` to put it back.
        """
        script = " ; ".join(
            (
                f'[ -e "{path}" ] && mv "{path}" "{path}.hidden"'
                if hide
                else f'[ -e "{path}.hidden" ] && mv "{path}.hidden" "{path}"'
            )
            for path in COMPOSE_FILES
        )
        inside(engine, "sh", "-c", script + " ; true", timeout=60)

    def inside(
        self,
        engine: Engine,
        *args: str,
        input_text: str | None = None,
        timeout: int = 300,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run a command in an engine's machine (what the central's scenarios call it by).

        Args:
            engine: Which.
            *args: The command.
            input_text: Standard input.
            timeout: Seconds.
            env: Variables for it.

        Returns:
            The completed process.
        """
        return inside(engine, *args, input_text=input_text, timeout=timeout, env=env)

    # -- the table ----------------------------------------------------------------------------

    def wanted(self) -> list[Spec]:
        """
        Returns:
            The scenarios this run was asked for.
        """
        words = self.options.scenario
        return [
            spec
            for spec in SPECS
            if not words or any(word in spec.name or word == str(spec.number) for word in words)
        ]

    def run_scenarios(self, engine: Engine) -> None:
        """
        Run every wanted scenario on one engine.

        Args:
            engine: Which.
        """
        for spec in self.wanted():
            variants = ["bundled"]
            if spec.compose_variants:
                variants += list(engine.variants)
            if spec.name == "doctor":
                variants.append(NO_COMPOSE)
            for variant in variants:
                key = f"{spec.number:02d} {spec.name}" + (
                    ""
                    if variant == "bundled"
                    else " [no Compose]"
                    if variant == NO_COMPOSE
                    else f" [Compose {variant}]"
                )
                self.say(f"{engine.key}: {key}")
                if spec.where == "central":
                    runner = next(
                        (
                            getattr(module, f"scenario_{spec.name}")
                            for module in self.central
                            if hasattr(module, f"scenario_{spec.name}")
                        ),
                        None,
                    )
                    if runner is None:
                        outcome = Outcome(
                            status="skip", reason="the central's scenarios are not written yet"
                        )
                    else:
                        began = time.monotonic()
                        try:
                            found = runner(self, engine)
                            outcome = Outcome(
                                status=found.status,
                                checks=found.checks,
                                notes=found.notes,
                                skips=found.skips,
                                reason=found.reason,
                            )
                        except Exception as exc:
                            outcome = Outcome(
                                status="error", reason=f"{type(exc).__name__}: {exc}"[:1500]
                            )
                        outcome.seconds = time.monotonic() - began
                        outcome.finish()
                else:
                    outcome = self.run_inside(engine, spec.name, variant=variant, timeout=spec.slow)
                self.results[engine.key][key] = outcome
                failed = [name for name, ok, _ in outcome.checks if not ok]
                self.say(
                    f"{engine.key}: {key}: {outcome.status} ({len(outcome.checks)} checks"
                    f"{', failed: ' + '; '.join(failed[:3]) if failed else ''}{', ' + outcome.reason[:200] if outcome.reason else ''})"
                )

    def cleanup(self) -> None:
        """Remove what this run made, by exact name and by label; never anything else, never a prune."""
        if self.options.keep:
            self.say("kept: " + ", ".join(engine.container for engine in self.engines))
            return
        for engine in ENGINES.values():
            docker("rm", "-f", "-v", engine.container)
            docker("volume", "rm", "-f", engine.volume)
        docker("network", "rm", NETWORK)
        if self.options.purge_images:
            for engine in ENGINES.values():
                docker("rmi", "-f", engine.image)
            docker("rmi", "-f", f"{PREFIX}-central:latest")
        left = docker(
            "ps",
            "-a",
            "--filter",
            f"label={LABEL}",
            "--filter",
            f"name={PREFIX}",
            "--format",
            "{{.Names}}",
        ).stdout.split()
        if left:
            self.say(f"left behind: {', '.join(left)}")

    def report(self, elapsed: float) -> int:
        """
        Print the table, write the results file and say how it went.

        Args:
            elapsed: Seconds the run took.

        Returns:
            The exit code: 0 when nothing failed.
        """
        keys = sorted({key for rows in self.results.values() for key in rows})
        width = max((len(key) for key in keys), default=10)
        names = {"pass": "pass", "fail": "FAIL", "error": "ERROR", "na": "n/a", "skip": "skip"}
        header = "  ".join(f"{engine.key:>6}" for engine in self.engines)
        print(f"\n{'scenario':<{width}}  {header}")
        for key in keys:
            cells = "  ".join(
                f"{names.get(self.results[engine.key][key].status, '?') if key in self.results[engine.key] else '-':>6}"
                for engine in self.engines
            )
            print(f"{key:<{width}}  {cells}")
        bad = [
            (engine, key, outcome)
            for engine in self.engines
            for key, outcome in self.results[engine.key].items()
            if outcome.status in ("fail", "error")
        ]
        for engine, key, outcome in bad:
            print(f"\n{engine.key} / {key}: {outcome.status} {outcome.reason[:600]}")
            for note, value in outcome.notes.items():
                print(f"    note {note}: {str(value)[:700]}")
            for name, ok, detail in outcome.checks:
                if not ok:
                    print(f"    FAIL {name}: {detail[:500]}")
        if not self.options.no_results:
            results = Path(self.options.results) if self.options.results else RESULTS
            results.parent.mkdir(parents=True, exist_ok=True)
            # One run is one engine, or a few scenarios: what earlier runs found of the rest is kept, and the
            # time of each engine is what its runs took together.
            try:
                payload = json.loads(results.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
            payload["generated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            engines = payload.setdefault("engines", {})
            for engine in self.engines:
                kept = engines.setdefault(engine.key, {})
                kept.update(self.versions.get(engine.key, {}))
                kept.setdefault("scenarios", {}).update(
                    {key: outcome.to_dict() for key, outcome in self.results[engine.key].items()}
                )
                kept["seconds"] = round(
                    sum(found.get("seconds", 0) for found in kept["scenarios"].values())
                )
            payload["seconds"] = round(sum(item.get("seconds", 0) for item in engines.values()))
            results.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"\nresults: {results}")
        print(f"{elapsed / 60:.1f} minutes")
        return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    """
    Run the harness.

    Args:
        argv: The command line; the process's by default.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(
        description="Docker, whole, against real engines of three ages"
    )
    parser.add_argument(
        "--engine", action="append", choices=sorted(ENGINES), help="Only this engine (repeatable)."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="Only scenarios whose name contains this, or this number.",
    )
    parser.add_argument(
        "--keep", action="store_true", help="Leave the engines' containers for another run."
    )
    parser.add_argument(
        "--reuse", action="store_true", help="Use the engines' containers that are already there."
    )
    parser.add_argument("--rebuild", action="store_true", help="Build the images again.")
    parser.add_argument(
        "--client-pair",
        metavar="DOCKER:COMPOSE",
        help="Build the central's image with this pair of Docker programs instead of the pinned one "
        "(for judging a candidate pair, as 29.9.3:5.7.1); the sums are read from what the releases publish.",
    )
    parser.add_argument(
        "--purge-images", action="store_true", help="Also remove the images the harness built."
    )
    parser.add_argument("--no-results", action="store_true", help="Do not write the results file.")
    parser.add_argument(
        "--results",
        metavar="FILE",
        help="Keep the results in this file instead of the one of the repository.",
    )
    parser.add_argument("--list", action="store_true", help="List the scenarios and stop.")
    options = parser.parse_args(argv)
    options.engine = options.engine or list(ENGINES)
    if options.list:
        for spec in SPECS:
            print(f"{spec.number:2d}  {spec.name:<20} {spec.where:<8} {spec.title}")
        return 0
    harness = Harness(options)
    harness.preflight()
    began = time.monotonic()
    code = 1
    try:
        harness.ensure_engine_images()
        needs_central = any(spec.where == "central" for spec in harness.wanted())
        image = harness.central_image() if needs_central else None
        compose_root = harness.compose_binaries()
        if needs_central:
            import docker_central
            import docker_central_changes

            harness.central = [docker_central, docker_central_changes]
        for engine in harness.engines:
            harness.start_engine(engine, compose_root)
            harness.prepare_machine(engine, image)
            harness.run_scenarios(engine)
    finally:
        elapsed = time.monotonic() - began
        harness.cleanup()
        code = harness.report(elapsed)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
