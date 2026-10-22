#!/usr/bin/env python3
"""
Real-machine integration harness for Noust.

Builds the wheel from the working tree, builds a systemd-under-Docker image,
starts a privileged container, installs Noust into it from the wheel exactly
as an operator would (``noust`` on the PATH), and runs a set of scenarios over the real CLI against real nginx,
real systemd and a real SQLite store.

This file is intentionally excluded from pytest's default collection: it
does not match ``test_*.py`` (see ``[tool.pytest.ini_options]`` in
``pyproject.toml``), and it drives Docker rather than being a unit test.

Usage:
    .venv/bin/python tests/integration/run.py
    .venv/bin/python tests/integration/run.py --keep
    .venv/bin/python tests/integration/run.py --scenario node_app_update
    .venv/bin/python tests/integration/run.py --scenario compose
    .venv/bin/python tests/integration/run.py --scenario container
    .venv/bin/python tests/integration/run.py --scenario railpack   (optional, needs the network)
    .venv/bin/python tests/integration/run.py --scenario proggest   (optional, slow)

Requires Docker (tested against Docker 29) with a working systemd-in-Docker
setup: cgroup v2, ``--privileged``, ``--cgroupns=host`` and the host cgroup
filesystem bind-mounted. run.py wires all of that itself.

The container runs its own Docker daemon and Compose plugin (for the Docker Compose
scenarios), on a dedicated named volume: see ``DOCKER_DATA_VOLUME``. A scenario that cannot
run here, because the machine lacks Docker or a registry, or because the build under test
does not have a command it drives yet, reports SKIP with the reason, never PASS.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = INTEGRATION_DIR / "fixtures"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
DOCKERFILE = INTEGRATION_DIR / "Dockerfile.systemd"

#: Every Docker object the harness creates on the machine's own Docker (the systemd image, the
#: container, the volume under its inner Docker) carries this label, so whoever shares the machine
#: can find and remove exactly what a run made and nothing else. Never a prune: the host's Docker
#: holds other people's containers.
RESOURCE_LABEL = "noust.test=1"

#: Names are overridable so that two people (or two work streams) who share one Docker do not
#: retag each other's image or share one inner-Docker volume: NOUST_IT_IMAGE, NOUST_IT_DOCKER_VOLUME
#: and NOUST_IT_PREFIX (what every container is named after: a machine that shares its Docker asks for
#: ``noust-test-<who>`` so what a run made can be told from what it did not).
IMAGE_TAG = os.environ.get("NOUST_IT_IMAGE", "noust-integration:latest")
RESOURCE_PREFIX = os.environ.get("NOUST_IT_PREFIX", "noust-it")

#: A memory limit for the harness container (``12g``), so that a heavy scenario (Proggest builds two
#: Node images) that runs out is killed inside it and not among the other containers of a shared
#: Docker. Unset, the container is not limited.
CONTAINER_MEMORY = os.environ.get("NOUST_IT_MEMORY", "")

#: The harness container runs its own Docker daemon (the Compose scenarios deploy real stacks),
#: and that daemon keeps its data in this dedicated named volume mounted on /var/lib/docker.
#: Overlay-on-overlay does not work on the container's own root filesystem, so the volume is what
#: lets overlay2 run at all; it also keeps the pulled base images between runs. Never the host's
#: docker.sock and never a host container or volume: the host's Docker holds real data
#: (proggest_*) that no scenario may see, name or touch.
DOCKER_DATA_VOLUME = os.environ.get("NOUST_IT_DOCKER_VOLUME", "noust-integration-docker")

#: What the harness container's own Docker is cleared of when a run starts and ends: every
#: container, volume and network (the images stay, they are the cache). It runs inside the
#: container, where the only daemon it can reach is the container's own.
INNER_DOCKER_CLEAR = (
    "docker ps -aq | xargs -r docker rm -f >/dev/null 2>&1; "
    "docker volume prune -af >/dev/null 2>&1; docker network prune -f >/dev/null 2>&1"
)

#: How long the container's own Docker daemon gets to answer after systemd is up.
INNER_DOCKER_READY_TIMEOUT = 60

#: How long the whole container gets to reach "running" or "degraded".
SYSTEMD_READY_TIMEOUT = 90

#: How long a `pip install '<wheel>[all]'` may take with a cold cache.
PIP_INSTALL_TIMEOUT = 600

#: How long a deploy (fetch + install + build + certbot-less site + start)
#: may take. Node dependency installs are the slow part.
DEPLOY_TIMEOUT = 300

#: The repository the release scenarios deploy from, and its git:// URL.
RELEASE_REPO = "/root/fixtures/node-rel"
RELEASE_URL = "git://127.0.0.1/node-rel"

#: The release scenarios' application.
RELEASE_DOMAIN = "rel.test"
RELEASE_ROOT = "/var/www/apps/rel-test"

#: The store, wherever this install put it. `core/store.py:_resolve_db_path`
#: only prefers the system directory (/var/lib/noust) when it already exists
#: and is writable; nothing in a plain `pip install` creates it, so a fresh
#: container still gets the per-user path.
NOUST_DB = "$(ls /var/lib/noust/noust.db /root/.local/share/noust/noust.db 2>/dev/null | head -1)"

#: The schema the working tree's store migrates to, read from its source rather than imported:
#: the harness runs on the host and never imports the package it tests.
SCHEMA_VERSION = int(
    re.search(
        r"^SCHEMA_VERSION = (\d+)$",
        (Path(__file__).resolve().parents[2] / "src/noust/core/store.py").read_text(),
        re.MULTILINE,
    ).group(1)  # type: ignore[union-attr]
)


class HarnessError(RuntimeError):
    """Raised for failures in the setup phases (build, install), not scenarios."""


class ScenarioSkipped(Exception):
    """Raised by a scenario that cannot run here, with the reason.

    A scenario that cannot run reports SKIP and says why: it is never a PASS (nothing was
    checked) and never a FAIL (nothing broke). Two things raise it: the machine (no network,
    no Docker) and the build under test, which does not have a command or an option the
    scenario drives yet (:func:`require_cli`, :func:`require_module`).
    """


# ---------------------------------------------------------------------------
# Host-side process helpers
# ---------------------------------------------------------------------------


def sh(
    argv: list[str], *, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command on the host (not in the container) and capture output."""
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise HarnessError(
            f"command failed (exit {proc.returncode}): {' '.join(argv)}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def docker_exec(
    container: str, script: str, *, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a shell script inside the container through `docker exec`."""
    proc = subprocess.run(
        ["docker", "exec", container, "bash", "-lc", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise HarnessError(
            f"command failed in container (exit {proc.returncode}): {script}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


# ---------------------------------------------------------------------------
# Setup phases
# ---------------------------------------------------------------------------


def build_wheel(workdir: Path) -> Path:
    """Build the wheel with the repo's own venv, installing `build` if needed."""
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    print(f"[setup] building wheel with {python}")

    probe = subprocess.run([str(python), "-c", "import build"], capture_output=True, text=True)
    if probe.returncode != 0:
        print("[setup] installing the `build` package into the repo venv")
        sh([str(python), "-m", "pip", "install", "build"], timeout=180)

    outdir = workdir / "dist"
    outdir.mkdir(parents=True, exist_ok=True)
    sh(
        [str(python), "-m", "build", "--wheel", "--outdir", str(outdir), str(REPO_ROOT)],
        timeout=180,
    )
    # Named for the current distribution only: `outdir` accumulates wheels
    # across runs, and a bare `*.whl` glob picked up a stale pre-rename wheel here.
    wheels = sorted(outdir.glob("noust-*.whl"))
    if not wheels:
        raise HarnessError(f"`python -m build` produced no noust-*.whl wheel in {outdir}")
    wheel = wheels[-1]
    print(f"[setup] built {wheel.name}")
    return wheel


def build_image() -> None:
    """Build the systemd-under-Docker image scenarios run against."""
    print(f"[setup] building image {IMAGE_TAG}")
    sh(
        [
            "docker",
            "build",
            "--label",
            RESOURCE_LABEL,
            "-t",
            IMAGE_TAG,
            "-f",
            str(DOCKERFILE),
            str(INTEGRATION_DIR),
        ],
        timeout=600,
    )


def docker_data_volume(name: str) -> str:
    """Choose the named volume the container's own Docker daemon keeps its data in.

    One daemon per data directory: two daemons on the same /var/lib/docker corrupt it, so a
    second harness running while the first (or a ``--keep`` container) still holds
    :data:`DOCKER_DATA_VOLUME` gets a volume of its own, named after its container and removed
    with it by :func:`remove_container`. It starts cold (the base images are pulled again), which
    is slower and correct.

    Args:
        name: The container the volume is for.

    Returns:
        The volume's name.
    """
    holders = sh(
        ["docker", "ps", "-q", "--filter", f"volume={DOCKER_DATA_VOLUME}"], timeout=30, check=False
    )
    if holders.stdout.strip():
        volume = f"{DOCKER_DATA_VOLUME}-{name}"
        print(f"[setup] {DOCKER_DATA_VOLUME} is in use by another container: using {volume}")
        return volume
    return DOCKER_DATA_VOLUME


def start_container(name: str) -> None:
    """Start the privileged, systemd-as-PID-1 container, with its own Docker data volume."""
    print(f"[setup] starting container {name}")
    volume = docker_data_volume(name)
    # Created here, not by `docker run -v`, so that it carries the label: an implicit volume
    # has none and could only be found again by its name.
    sh(["docker", "volume", "create", "--label", RESOURCE_LABEL, volume], timeout=30)
    sh(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            RESOURCE_LABEL,
            *(["--memory", CONTAINER_MEMORY] if CONTAINER_MEMORY else []),
            "--privileged",
            "--cgroupns=host",
            "-v",
            "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "-v",
            f"{volume}:/var/lib/docker",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/run/lock",
            IMAGE_TAG,
        ],
        timeout=60,
    )


def wait_for_systemd(name: str, timeout: int = SYSTEMD_READY_TIMEOUT) -> str:
    """Poll `systemctl is-system-running` until it settles or the deadline passes."""
    print("[setup] waiting for systemd to report running/degraded")
    deadline = time.time() + timeout
    last = "<never answered>"
    while time.time() < deadline:
        result = docker_exec(name, "systemctl is-system-running || true", timeout=15, check=False)
        last = result.stdout.strip() or last
        if last in ("running", "degraded"):
            print(f"[setup] systemd is {last}")
            return last
        time.sleep(2)
    journal = docker_exec(name, "journalctl -xb --no-pager | tail -100", timeout=15, check=False)
    raise HarnessError(
        f"systemd never reached running/degraded in {timeout}s (last state: {last!r})\n"
        f"--- journalctl -xb (tail) ---\n{journal.stdout}"
    )


def install_noust(name: str, wheel: Path) -> None:
    """Install Noust into a dedicated venv, exactly as documented for operators.

    Symlinks ``noust`` onto ``/usr/local/bin`` from the venv.

    Args:
        name: Container name.
        wheel: Path to the wheel built from the working tree, on the host.
    """
    print(f"[setup] copying {wheel.name} into the container")
    sh(["docker", "cp", str(wheel), f"{name}:/tmp/{wheel.name}"], timeout=60)

    print("[setup] creating /opt/noust venv and installing the wheel")
    docker_exec(name, "python3 -m venv /opt/noust", timeout=60)
    docker_exec(name, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=120)
    docker_exec(
        name,
        f"/opt/noust/bin/pip install --quiet '/tmp/{wheel.name}[all]'",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    docker_exec(name, "ln -sf /opt/noust/bin/noust /usr/local/bin/noust", timeout=15)
    result = docker_exec(name, "noust --help", timeout=30)
    print("[setup] noust --help:")
    print(result.stdout)


def install_fixtures(name: str) -> None:
    """Copy the fixture apps into the container and turn each into a git repo."""
    print("[setup] copying fixtures into the container")
    sh(["docker", "cp", str(FIXTURES_DIR), f"{name}:/root/fixtures"], timeout=60)

    # `docker cp` preserves the numeric uid of the files on the host, which
    # is almost never root's. git then refuses to touch the tree ("detected
    # dubious ownership") and so would Noust's own fetch step were these ever
    # used as a source through anything but a straight directory copy.
    docker_exec(name, "chown -R root:root /root/fixtures", timeout=15)
    docker_exec(name, "git config --global --add safe.directory '*'", timeout=15)

    # The nodejs deployer always installs with `npm ci`
    # (helpers/package_manager.py:187), which refuses to run without a
    # committed lockfile. Real Node projects ship one; this fixture needs one
    # too, generated fresh so it matches whatever npm version the image has.
    docker_exec(
        name,
        "cd /root/fixtures/node-app && npm install --package-lock-only",
        timeout=60,
    )

    for app in ("static-site", "node-app"):
        script = (
            f"cd /root/fixtures/{app} && "
            "git init -q && "
            "git config user.email noust-it@example.com && "
            "git config user.name 'Noust Integration' && "
            "git add -A && "
            "git commit -q -m 'initial fixture'"
        )
        docker_exec(name, script, timeout=30)
    print("[setup] fixtures are local git repositories under /root/fixtures")

    # The release scenarios deploy from a git remote, so the repository cache,
    # the fetch and the export run for real. A copy of the node fixture keeps
    # their commits out of the in-place scenarios' repository, and git daemon
    # serves it over git:// on loopback: Noust refuses file:// on purpose.
    docker_exec(name, f"cp -a /root/fixtures/node-app {RELEASE_REPO}", timeout=30)
    # A dependency, so there is a node_modules for an update to reuse. A local
    # one: npm links it from the tree and never touches the network.
    docker_exec(
        name,
        f"cd {RELEASE_REPO} && mkdir local-dep && "
        'echo \'{"name": "local-dep", "version": "1.0.0"}\' > local-dep/package.json && '
        "echo 'module.exports = 1;' > local-dep/index.js && "
        "npm pkg set dependencies.local-dep=file:./local-dep && "
        "npm install --package-lock-only && "
        "git add -A && git commit -q -m 'add a local dependency'",
        timeout=60,
    )
    docker_exec(
        name,
        "git daemon --reuseaddr --export-all --base-path=/root/fixtures "
        "--listen=127.0.0.1 --detach /root/fixtures",
        timeout=30,
    )
    docker_exec(name, f"git ls-remote --exit-code {RELEASE_URL} HEAD", timeout=30)
    print(f"[setup] {RELEASE_REPO} is served at {RELEASE_URL}")


def wheel_version(wheel: Path) -> str:
    """The version a wheel carries, from its file name (``noust-3.1.0-py3-none-any.whl``).

    The upgrade steps pin ``noust`` to the working tree's version, so that
    ``--no-index`` finds the wheel it was given.
    """
    return wheel.name.split("-")[1]


def remove_container(name: str) -> None:
    """Best-effort container teardown; never raises.

    The container's own Docker is cleared and stopped first, so its data volume is left
    consistent (images only) for the next run instead of holding containers a killed daemon
    never stopped; the per-container volume :func:`docker_data_volume` hands out when the shared
    one is busy goes with it.
    """
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                name,
                "bash",
                "-lc",
                f"{INNER_DOCKER_CLEAR}; systemctl stop docker.socket docker.service",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"[teardown] the Docker inside {name} did not stop within 120s; removing it anyway")
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=60)
    subprocess.run(
        ["docker", "volume", "rm", "-f", f"{DOCKER_DATA_VOLUME}-{name}"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def prepare_inner_docker(name: str) -> bool:
    """Wait for the container's own Docker daemon and clear what a killed run left in it.

    Not fatal when it never answers: only the Docker scenarios need it, and each of them
    reports SKIP with the daemon's journal (:func:`require_docker`) rather than the whole run
    failing for scenarios that have nothing to do with Docker.

    Args:
        name: Container name.

    Returns:
        Whether the daemon answered.
    """
    print("[setup] waiting for the container's own Docker daemon")
    deadline = time.time() + INNER_DOCKER_READY_TIMEOUT
    while time.time() < deadline:
        probe = docker_exec(name, "docker info >/dev/null 2>&1", timeout=30, check=False)
        if probe.returncode == 0:
            break
        time.sleep(2)
    else:
        print(
            f"[setup] WARNING: the Docker inside {name} did not answer in "
            f"{INNER_DOCKER_READY_TIMEOUT}s; the Docker scenarios will be skipped"
        )
        return False
    docker_exec(name, INNER_DOCKER_CLEAR, timeout=120, check=False)
    version = docker_exec(
        name,
        "docker version --format '{{.Server.Version}}' && docker compose version --short",
        timeout=30,
        check=False,
    )
    print(f"[setup] Docker inside the container: {' / compose '.join(version.stdout.split())}")
    return True


# ---------------------------------------------------------------------------
# Scenario framework
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """Collaborator scenarios use to run commands and assert on them.

    Every command run through :meth:`run` is kept, in order, as evidence -
    printed verbatim after the scenario's PASS/FAIL line.
    """

    container: str
    evidence: list[str] = field(default_factory=list)

    def run(
        self, script: str, *, timeout: int = 60, check: bool = True, label: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        proc = docker_exec(self.container, script, timeout=timeout, check=False)
        block = [f"$ {label or script}"]
        if proc.stdout:
            block.append(proc.stdout.rstrip("\n"))
        if proc.stderr.strip():
            block.append("--- stderr ---")
            block.append(proc.stderr.rstrip("\n"))
        block.append(f"(exit={proc.returncode})")
        self.evidence.append("\n".join(block))
        if check and proc.returncode != 0:
            raise AssertionError(f"command failed (exit {proc.returncode}): {label or script}")
        return proc

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)


ScenarioFn = Callable[[Scenario], None]
SCENARIOS: list[tuple[str, ScenarioFn]] = []


def scenario(name: str) -> Callable[[ScenarioFn], ScenarioFn]:
    def decorator(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS.append((name, fn))
        return fn

    return decorator


#: Set by ``--ignore-requirements``: run a scenario even when the build under test lacks the
#: command or module it needs, to see how far it gets while the feature is half-built.
IGNORE_REQUIREMENTS = False


def require_cli(sc: Scenario, command: str, *, option: str | None = None, what: str) -> None:
    """Skip the scenario, saying why, until the installed CLI has a command or an option.

    Asks the real CLI (``noust <command> --help``), so it is the build under test that answers:
    a scenario for a feature another flow is still building goes green by itself the day the
    command lands, with no list of versions to keep up to date.

    Args:
        sc: The scenario.
        command: The command words, such as ``"app hooks"`` or ``"update"``.
        option: An option its help must list, such as ``"--tag"``; None for the command alone.
        what: The feature the scenario tests, for the reason.

    Raises:
        ScenarioSkipped: The command does not exist, or its help does not list the option.
    """
    if IGNORE_REQUIREMENTS:
        return
    probe = docker_exec(sc.container, f"noust {command} --help", timeout=60, check=False)
    if probe.returncode != 0:
        raise ScenarioSkipped(f"`noust {command}` does not exist in this build yet ({what})")
    if option is not None and option not in probe.stdout:
        raise ScenarioSkipped(f"`noust {command}` has no {option} in this build yet ({what})")


def require_module(sc: Scenario, module: str, *, what: str) -> None:
    """Skip the scenario, saying why, until the installed Noust has a Python module.

    For a feature with no command of its own to ask about: the zero-downtime relay is switched
    on by a command that already exists and means something else until the engine does.

    Args:
        sc: The scenario.
        module: Dotted module path, such as ``"noust.deployers.compose_relay"``.
        what: The feature the scenario tests, for the reason.

    Raises:
        ScenarioSkipped: The module cannot be imported.
    """
    if IGNORE_REQUIREMENTS:
        return
    probe = docker_exec(sc.container, f"/opt/noust/bin/python -c 'import {module}'", check=False)
    if probe.returncode != 0:
        raise ScenarioSkipped(f"{module} does not exist in this build yet ({what})")


def require_docker(sc: Scenario) -> None:
    """Skip the scenario, saying why, when the container's own Docker cannot run a stack.

    Args:
        sc: The scenario.

    Raises:
        ScenarioSkipped: ``docker info`` or ``docker compose version`` fails, with the daemon's
            own journal.
    """
    probe = docker_exec(
        sc.container,
        "docker info >/dev/null 2>&1 && docker compose version --short",
        timeout=60,
        check=False,
    )
    if probe.returncode != 0:
        journal = docker_exec(
            sc.container, "journalctl --no-pager -n 15 -u docker -u containerd", check=False
        )
        raise ScenarioSkipped(
            "Docker is not usable inside the harness container "
            f"(docker info / docker compose version failed):\n{journal.stdout.strip()}"
        )


def pull_images(sc: Scenario, *images: str) -> None:
    """Make sure the container's own Docker has base images, pulling the ones it lacks.

    The images stay in the data volume, so only the first run on a machine pulls them. A scenario
    that cannot pull has no network to the registry, which is the machine's and not the
    build's: it is skipped, never failed.

    Args:
        sc: The scenario.
        *images: Image references, such as ``"python:3.12-alpine"``.

    Raises:
        ScenarioSkipped: An image is missing and cannot be pulled.
    """
    for image in images:
        have = docker_exec(sc.container, f"docker image inspect {image}", check=False)
        if have.returncode == 0:
            continue
        pulled = sc.run(
            f"docker pull -q {image}", timeout=300, check=False, label=f"docker pull {image}"
        )
        if pulled.returncode != 0:
            raise ScenarioSkipped(
                f"{image} is not in the container's Docker and cannot be pulled: "
                f"{(pulled.stderr or pulled.stdout).strip()}"
            )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@scenario("static_site_create_and_serve")
def scenario_static_site(sc: Scenario) -> None:
    """A static site deploys with --no-ssl and nginx serves it and its image."""
    sc.run(
        "noust create -d static.test -s /root/fixtures/static-site -t static --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label="noust create -d static.test -s /root/fixtures/static-site -t static --no-ssl",
    )

    page = sc.run(
        "curl -sS -H 'Host: static.test' http://127.0.0.1/",
        timeout=30,
        label="curl -H 'Host: static.test' http://127.0.0.1/",
    )
    sc.check(
        "Noust Integration Static Fixture" in page.stdout,
        f"index page missing the fixture marker text: {page.stdout!r}",
    )

    image = sc.run(
        "curl -sS -o /dev/null -w '%{http_code} %{size_download}' "
        "-H 'Host: static.test' http://127.0.0.1/logo.png",
        timeout=30,
        label="curl -H 'Host: static.test' http://127.0.0.1/logo.png",
    )
    code, _, size = image.stdout.strip().partition(" ")
    sc.check(
        code == "200" and int(size or "0") > 0,
        f"image fetch failed or was empty: {image.stdout!r}",
    )

    # Planted in the served tree so this proves nginx itself refuses them, not just
    # that nothing happens to be there. Written here rather than shipped in the fixture
    # because git cannot track a file inside a directory named .git.
    sc.run(
        "root=$(readlink -e /var/www/apps/static-test/current || echo /var/www/apps/static-test) && "
        'echo SECRET=integration > "$root/.env" && '
        'mkdir -p "$root/.git" && echo \'[core]\' > "$root/.git/config"',
        timeout=30,
        label="plant .env and .git/config in the served tree",
    )
    for path in ("/.env", "/.git/config"):
        hidden = sc.run(
            f"curl -sS -o /dev/null -w '%{{http_code}}' -H 'Host: static.test' "
            f"http://127.0.0.1{path}",
            timeout=30,
            label=f"curl -H 'Host: static.test' http://127.0.0.1{path}",
        )
        sc.check(
            hidden.stdout.strip() in ("403", "404"),
            f"{path} must answer 403 or 404, got {hidden.stdout.strip()!r}",
        )

    challenge = sc.run(
        "mkdir -p /var/www/html/.well-known/acme-challenge && "
        "echo noust-integration-token > /var/www/html/.well-known/acme-challenge/probe && "
        "curl -sS -H 'Host: static.test' http://127.0.0.1/.well-known/acme-challenge/probe",
        timeout=30,
        label="curl -H 'Host: static.test' http://127.0.0.1/.well-known/acme-challenge/probe",
    )
    sc.check(
        "noust-integration-token" in challenge.stdout,
        f"ACME's own well-known path must stay reachable: {challenge.stdout!r}",
    )


@scenario("node_app_create_update_and_data_survival")
def scenario_node_app_update(sc: Scenario) -> None:
    """A Node app deploys, is updated to a new commit, and keeps its own data.

    This is the v1.6.3 data-loss regression check: an update must not erase
    ``.env`` or anything the running application wrote into its own tree
    (uploads among them). It deploys with the 1.x in-place layout, which is
    what every application deployed before 2.0 is on, and checks that an
    update leaves it there.
    """
    sc.run(
        "noust create -d node.test -s /root/fixtures/node-app -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label="noust create -d node.test -s /root/fixtures/node-app -t nodejs --no-ssl "
        "--layout inplace",
    )

    page = sc.run(
        "curl -sS -H 'Host: node.test' http://127.0.0.1/",
        timeout=30,
        label="curl -H 'Host: node.test' http://127.0.0.1/",
    )
    sc.check(page.stdout.strip() == "ok 1", f"expected 'ok 1', got {page.stdout!r}")

    env_before = sc.run(
        "test -f /var/www/apps/node-test/.env && echo present",
        timeout=15,
        check=False,
        label="test -f /var/www/apps/node-test/.env",
    )
    sc.check(
        env_before.stdout.strip() == "present",
        ".env was not created from .env.example on deploy",
    )

    upload = sc.run(
        "curl -sS -H 'Host: node.test' --data-binary 'integration-harness-upload' "
        "http://127.0.0.1/upload",
        timeout=30,
        label="curl -H 'Host: node.test' --data-binary ... http://127.0.0.1/upload",
    )
    sc.check("uploaded" in upload.stdout, f"upload did not succeed: {upload.stdout!r}")

    sc.run(
        "cd /root/fixtures/node-app && echo 2 > VERSION && "
        "git add VERSION && git commit -q -m 'bump version to 2'",
        timeout=30,
        label="(fixture repo) echo 2 > VERSION; git commit",
    )

    sc.run("noust update node.test", timeout=DEPLOY_TIMEOUT, label="noust update node.test")

    page2 = sc.run(
        "curl -sS -H 'Host: node.test' http://127.0.0.1/",
        timeout=30,
        label="curl -H 'Host: node.test' http://127.0.0.1/ (after update)",
    )
    sc.check(
        page2.stdout.strip() == "ok 2",
        f"expected 'ok 2' after update, got {page2.stdout!r}",
    )

    upload_after = sc.run(
        "cat /var/www/apps/node-test/uploads/upload.txt",
        timeout=15,
        check=False,
        label="cat /var/www/apps/node-test/uploads/upload.txt (after update)",
    )
    sc.check(
        upload_after.returncode == 0 and "integration-harness-upload" in upload_after.stdout,
        "the uploaded file did not survive `noust update` - this is the v1.6.3 "
        "data-loss regression `noust update` is supposed to have fixed",
    )

    env_after = sc.run(
        "test -f /var/www/apps/node-test/.env && echo present",
        timeout=15,
        check=False,
        label="test -f /var/www/apps/node-test/.env (after update)",
    )
    sc.check(env_after.stdout.strip() == "present", ".env did not survive `noust update`")

    listing = sc.run(
        "ls -A /var/www/apps/node-test",
        timeout=15,
        label="ls -A /var/www/apps/node-test (after update)",
    )
    layout = sc.run(
        store_query("SELECT layout FROM apps WHERE domain = 'node.test'"),
        timeout=15,
        label="SELECT layout FROM apps WHERE domain = 'node.test' (after update)",
    )
    sc.check(
        not {"releases", "current", "shared", "repo"} & set(listing.stdout.split())
        and layout.stdout.strip() == "inplace",
        f"the in-place app was moved onto releases by an update: {listing.stdout!r} "
        f"{layout.stdout!r}",
    )


@scenario("update_hands_tree_back_to_www_data")
def scenario_ownership(sc: Scenario) -> None:
    """After an update, nothing under the app tree is still owned by root.

    This is the v1.6.2 regression check (EACCES at runtime because the tree
    was handed back to the service user only on deploy, not on update).
    node_modules is excluded per the task brief, though the current
    implementation (helpers/permissions.py:hand_over_tree) chowns the whole
    tree including it.
    """
    result = sc.run(
        "find /var/www/apps/node-test -not -path '*/node_modules/*' "
        "-not -path '*/node_modules' -user root",
        timeout=30,
        check=False,
        label="find /var/www/apps/node-test -not -path '*/node_modules/*' -user root",
    )
    sc.check(result.returncode == 0, f"find failed: {result.stderr}")
    sc.check(
        result.stdout.strip() == "",
        f"root-owned files remain under the app tree after update:\n{result.stdout}",
    )


def store_query(query: str) -> str:
    """Return the shell command that runs a read-only query against the store."""
    return store_query_at(NOUST_DB, query)


def store_query_at(db: str, query: str) -> str:
    """Return the shell command that runs a read-only query against a given store file."""
    return f'sqlite3 {db} "{query}"'


def active_release(sc: Scenario, label: str) -> str:
    """Return what ``current`` points at, as ``releases/<id>``."""
    link = sc.run(f"readlink {RELEASE_ROOT}/current", timeout=15, check=False, label=label)
    return link.stdout.strip()


def commit_fixture(sc: Scenario, script: str, message: str) -> None:
    """Change the release fixture repository and commit it."""
    sc.run(
        f"cd {RELEASE_REPO} && {script} && git add -A && git commit -q -m '{message}'",
        timeout=30,
        label=f"(fixture repo) {script}; git commit -m '{message}'",
    )


@scenario("release_app_create_and_serve")
def scenario_release_create(sc: Scenario) -> None:
    """A Node app created on the release layout builds releases/<id> behind current.

    Deployed from a git remote, so the repository cache, the fetch and the
    export into the release all run for real; ``uploads`` is a persistent
    path that lives in shared/.
    """
    create = (
        f"noust create -d {RELEASE_DOMAIN} -s {RELEASE_URL} -t nodejs --no-ssl "
        "--layout releases --persist uploads"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)

    tree = sc.run(
        f"ls -A {RELEASE_ROOT}; ls {RELEASE_ROOT}/releases; "
        f"test -d {RELEASE_ROOT}/repo/.git && echo repo-is-a-clone; "
        f"test -e {RELEASE_ROOT}/current/.git || echo release-has-no-git",
        timeout=15,
        label=f"ls -A {RELEASE_ROOT}; ls releases; repo/.git; current/.git",
    )
    for expected in ("releases", "current", "shared", "repo", "repo-is-a-clone"):
        sc.check(expected in tree.stdout.split(), f"{expected} missing: {tree.stdout!r}")
    sc.check("release-has-no-git" in tree.stdout, "the release was exported with its .git")

    current = active_release(sc, f"readlink {RELEASE_ROOT}/current")
    sc.check(current.startswith("releases/"), f"current is not a release link: {current!r}")

    links = sc.run(
        f"readlink {RELEASE_ROOT}/current/.env {RELEASE_ROOT}/current/uploads; "
        f"stat -c '%a %U' {RELEASE_ROOT}/shared/.env",
        timeout=15,
        label="readlink current/.env current/uploads; stat shared/.env",
    )
    sc.check(
        links.stdout.split()[:2] == ["../../shared/.env", "../../shared/uploads"],
        f".env and uploads are not linked into shared/: {links.stdout!r}",
    )
    # New applications run as an account of their own (3.1), not as www-data.
    sc.check(
        links.stdout.split()[2:] == ["600", f"noust-app-{RELEASE_ROOT.rsplit('/', 1)[1]}"],
        f"shared/.env: {links.stdout!r}",
    )

    if limits_unit(sc, "rel-test").endswith("@blue"):
        # Updates without downtime (the default on releases): the application is the instances
        # of one template, which run from a link per color, and the serving color is the release
        # `current` names.
        unit = sc.run(
            "systemctl cat rel-test@.service | grep -E '^(WorkingDirectory|ExecStart)='",
            timeout=15,
            label="systemctl cat rel-test@.service | grep WorkingDirectory/ExecStart",
        )
        sc.check(
            f"WorkingDirectory={RELEASE_ROOT}/colors/%i" in unit.stdout,
            f"the template does not run from the color's link: {unit.stdout!r}",
        )
        colors = sc.run(
            f"readlink {RELEASE_ROOT}/colors/blue {RELEASE_ROOT}/colors/green",
            timeout=15,
            label="readlink colors/blue colors/green",
        )
        sc.check(
            f"../{current}" in colors.stdout.split(),
            f"neither color is the release current names ({current}): {colors.stdout!r}",
        )
    else:
        unit = sc.run(
            "systemctl cat rel-test | grep -E '^(WorkingDirectory|ExecStart)='",
            timeout=15,
            label="systemctl cat rel-test | grep WorkingDirectory/ExecStart",
        )
        sc.check(
            f"WorkingDirectory={RELEASE_ROOT}/current" in unit.stdout,
            f"the unit does not run from current: {unit.stdout!r}",
        )

    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
    )
    sc.check(page.stdout.strip() == "ok 1", f"expected 'ok 1', got {page.stdout!r}")

    rows = sc.run(
        store_query(
            "SELECT a.layout, r.status FROM releases r "
            "JOIN apps a ON a.id = r.app_id WHERE a.domain = 'rel.test'"
        ),
        timeout=15,
        label="SELECT layout, release status FROM the store",
    )
    sc.check(rows.stdout.split() == ["releases|active"], f"store rows: {rows.stdout!r}")


@scenario("release_app_update_keeps_uploads")
def scenario_release_update(sc: Scenario) -> None:
    """An update builds a new release; the upload the old one took is still served.

    The lockfile does not change between the two commits, so the new release
    takes its node_modules from the active one instead of installing.
    """
    before = active_release(sc, f"readlink {RELEASE_ROOT}/current (before update)")

    upload = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' --data-binary 'release-harness-upload' "
        "http://127.0.0.1/upload",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' --data-binary ... http://127.0.0.1/upload",
    )
    sc.check("uploaded" in upload.stdout, f"upload did not succeed: {upload.stdout!r}")
    sc.run(
        f"cat {RELEASE_ROOT}/shared/uploads/upload.txt",
        timeout=15,
        label="cat shared/uploads/upload.txt (the upload landed in shared/)",
    )

    commit_fixture(sc, "echo 2 > VERSION", "bump version to 2")
    sc.run(
        f"noust update {RELEASE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust update {RELEASE_DOMAIN}",
    )

    # The console shows substeps only with -v; the captured deploy log has all.
    log_path = sc.run(
        store_query(
            "SELECT log_path FROM deployments WHERE domain = 'rel.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the deploy log of the update",
    ).stdout.strip()
    log = sc.run(
        f"grep -E 'Dependencies reused|npm ci|Activated release' {log_path}",
        timeout=15,
        check=False,
        label=f"grep 'Dependencies reused|npm ci|Activated release' {log_path}",
    )
    sc.check(
        f"Dependencies reused from {before.split('/')[-1]}" in log.stdout
        and "npm ci" not in log.stdout,
        "the update installed again although the lockfile did not change",
    )

    after = active_release(sc, f"readlink {RELEASE_ROOT}/current (after update)")
    sc.check(after != before, f"current still points at {before}")
    count = sc.run(f"ls {RELEASE_ROOT}/releases | wc -l", timeout=15, label="ls releases | wc -l")
    sc.check(count.stdout.strip() == "2", f"expected 2 releases, found {count.stdout.strip()}")

    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (after update)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"expected 'ok 2' after update, got {page.stdout!r}")

    kept = sc.run(
        f"cat {RELEASE_ROOT}/current/uploads/upload.txt",
        timeout=15,
        check=False,
        label="cat current/uploads/upload.txt (through the new release)",
    )
    sc.check(
        kept.returncode == 0 and "release-harness-upload" in kept.stdout,
        "the upload is not reachable from the new release",
    )

    owners = sc.run(
        f"find {RELEASE_ROOT}/{after} {RELEASE_ROOT}/shared -user root "
        "-not -path '*/node_modules/*' -not -path '*/node_modules'",
        timeout=30,
        check=False,
        label=f"find {after} shared -user root (not node_modules)",
    )
    sc.check(owners.stdout.strip() == "", f"root-owned files remain:\n{owners.stdout}")


@scenario("release_app_broken_commit_rolls_back")
def scenario_release_rollback(sc: Scenario) -> None:
    """A release whose server throws on start fails the health gate and is rolled back.

    The previous release serves again without the operator doing anything,
    the update fails with the process's own words, and the store says which
    release failed.
    """
    before = active_release(sc, f"readlink {RELEASE_ROOT}/current (before the broken commit)")

    commit_fixture(
        sc,
        "echo 3 > VERSION && sed -i '1i throw new Error(\"broken on purpose\");' server.js",
        "break the server",
    )
    result = sc.run(
        f"noust update {RELEASE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust update {RELEASE_DOMAIN} (broken commit)",
    )
    output = result.stdout + result.stderr
    sc.check(result.returncode != 0, "noust update reported success for a release that never ran")
    sc.check("did not pass its health check" in output, "the failure does not say what failed")
    # With updates without downtime (the default on releases) the new release starts beside the
    # serving one and is never switched to, so the failure says what kept serving; without, it
    # says what was put back. Either way it names the release that serves.
    sc.check(
        f"{before.split('/')[-1]} is active again" in output or "kept serving" in output,
        "the failure does not say the previous release is back, or that it kept serving",
    )

    after = active_release(sc, f"readlink {RELEASE_ROOT}/current (after the failed update)")
    sc.check(after == before, f"current moved from {before} to {after}")

    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (after the rollback)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"the previous release is not serving: {page.stdout!r}")

    history = sc.run(
        store_query(
            "SELECT status, error FROM deployments "
            "WHERE domain = 'rel.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the last deployment of rel.test",
    )
    sc.check(
        history.stdout.startswith("failed|") and "broken on purpose" in history.stdout,
        "the deployment record does not carry the process's own error",
    )
    statuses = sc.run(
        store_query(
            "SELECT r.status FROM releases r JOIN apps a ON a.id = r.app_id "
            "WHERE a.domain = 'rel.test' ORDER BY r.created_at"
        ),
        timeout=15,
        label="SELECT every release status of rel.test",
    )
    sc.check(
        statuses.stdout.split() == ["superseded", "active", "failed"],
        f"release statuses: {statuses.stdout.split()!r}",
    )

    # Leave the fixture deployable for anything that runs after this.
    sc.run(
        f"cd {RELEASE_REPO} && git revert --no-edit HEAD",
        timeout=30,
        label="(fixture repo) git revert --no-edit HEAD",
    )


@scenario("status_and_list")
def scenario_status_and_list(sc: Scenario) -> None:
    """`noust status` and `noust list` succeed and mention the deployed apps.

    Also under ``--json``, where nothing but the JSON itself may be on stdout.
    """
    status = sc.run("noust status node.test", timeout=30, label="noust status node.test")
    sc.check(status.returncode == 0, "noust status node.test failed")

    listing = sc.run("noust list", timeout=30, label="noust list")
    sc.check(listing.returncode == 0, "noust list failed")
    sc.check(
        "node.test" in listing.stdout and "static.test" in listing.stdout,
        f"noust list did not mention both deployed apps: {listing.stdout!r}",
    )

    noust_json = sc.run("noust list --json", timeout=30, label="noust list --json").stdout
    json.loads(noust_json)  # raises if anything besides the JSON was printed


#: The strict policy the console is served under (plan, Global Constraints).
#: Trusted Types may follow it; nothing may relax it.
CONSOLE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self' https://github.com; object-src 'none'"
)

PANEL_URL = "http://127.0.0.1:8080"


def http_head_and_body(sc: Scenario, path: str) -> tuple[int, dict[str, str], str]:
    """Fetch a panel path inside the container; return status, headers, body."""
    proc = sc.run(
        f"curl -sS -D - {PANEL_URL}{path}",
        timeout=15,
        check=False,
        label=f"curl -D - {PANEL_URL}{path} (body truncated in evidence)",
    )
    # Keep the evidence readable: the console bundle is not something to print.
    if len(sc.evidence[-1]) > 4000:
        sc.evidence[-1] = sc.evidence[-1][:4000] + "\n... (truncated)"
    head, _, body = proc.stdout.replace("\r\n", "\n").partition("\n\n")
    lines = head.splitlines()
    sc.check(bool(lines), f"no response for {path}: {proc.stderr!r}")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers, body


@scenario("web_panel_serves_the_console")
def scenario_web_panel(sc: Scenario) -> None:
    """`noust web start` answers /health and serves the console from the wheel.

    The console's Vite build ships inside the package (web/static), so this
    is also the packaging check: a build that was not committed, or a glob
    that stopped matching, is a blank page here and nowhere else before an
    operator opens it.
    """
    sc.run("noust web start --daemon", timeout=60, label="noust web start --daemon")

    health: subprocess.CompletedProcess[str] | None = None
    deadline = time.time() + 30
    while time.time() < deadline:
        health = sc.run(
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
            label=f"curl -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
        )
        if health.stdout.strip() == "200":
            break
        time.sleep(1)

    sc.check(
        health is not None and health.stdout.strip() == "200",
        f"panel /health did not return 200 within 30s: {health.stdout if health else None!r}",
    )

    body = sc.run(
        f"curl -sS {PANEL_URL}/health",
        timeout=15,
        check=False,
        label=f"curl {PANEL_URL}/health",
    )
    sc.check('"status"' in body.stdout, f"unexpected /health body: {body.stdout!r}")

    try:
        # The console, at the root and at a deep link a reload lands on.
        for path in ("/", "/apps/example.com/deployments"):
            status, headers, html = http_head_and_body(sc, path)
            sc.check(status == 200, f"GET {path} answered {status}")
            sc.check('<div id="root">' in html, f"GET {path} is not the console: {html[:200]!r}")
            sc.check(
                headers.get("content-type", "").startswith("text/html"),
                f"GET {path} content-type {headers.get('content-type')!r}",
            )
            sc.check(
                headers.get("cache-control") == "no-store",
                f"GET {path} cache-control {headers.get('cache-control')!r}",
            )
            csp = headers.get("content-security-policy", "")
            sc.check(
                csp.startswith(CONSOLE_CSP) and "unsafe-inline" not in csp,
                f"GET {path} is not served under the strict policy: {csp!r}",
            )

        # Every asset the console names loads, and is cached for good.
        _, _, html = http_head_and_body(sc, "/")
        assets = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)))
        sc.check(bool(assets), "the console names no /assets/ file")
        status_lines = sc.run(
            "for p in "
            + " ".join(assets)
            + f'; do curl -sS -o /dev/null -w "%{{http_code}} $p\\n" {PANEL_URL}$p; done',
            timeout=60,
            check=False,
            label=f"curl every one of the {len(assets)} assets index.html names",
        ).stdout.splitlines()
        failed = [line for line in status_lines if not line.startswith("200 ")]
        sc.check(
            len(status_lines) == len(assets) and not failed,
            f"assets that did not load: {failed or status_lines}",
        )
        status, headers, _ = http_head_and_body(sc, assets[0])
        sc.check(
            "immutable" in headers.get("cache-control", ""),
            f"{assets[0]} cache-control {headers.get('cache-control')!r}",
        )
        status, _, _ = http_head_and_body(sc, "/favicon.svg")
        sc.check(status == 200, f"/favicon.svg answered {status}")

        # A machine path is never shadowed by the console.
        status, headers, _ = http_head_and_body(sc, "/api/does-not-exist")
        sc.check(
            status in (401, 404) and headers.get("content-type", "").startswith("application/json"),
            f"/api/does-not-exist answered {status} {headers.get('content-type')!r}",
        )
    finally:
        sc.run("noust web stop", timeout=30, check=False, label="noust web stop")


# ---------------------------------------------------------------------------
# Instant rollback, migration and resource limits
# ---------------------------------------------------------------------------

#: The in-place application the migration scenario moves onto releases.
INPLACE_DOMAIN = "node.test"
INPLACE_ROOT = "/var/www/apps/node-test"


def tree_census(sc: Scenario, root: str, label: str) -> str:
    """Count the regular files under a directory and their bytes, following nothing."""
    return sc.run(
        f"find {root} -type f -printf '%s\\n' | awk '{{n++; s+=$1}} END {{print n, s}}'",
        timeout=30,
        label=label,
    ).stdout.strip()


@scenario("release_app_instant_rollback")
def scenario_instant_rollback(sc: Scenario) -> None:
    """`noust releases rollback` serves the previous release at once, with nothing rebuilt.

    Runs after the release scenarios, which leave two releases on disk: the
    first serving "ok 1" and the second, active, serving "ok 2".
    """
    listing = sc.run(
        f"noust releases list {RELEASE_DOMAIN} --json",
        timeout=30,
        label=f"noust releases list {RELEASE_DOMAIN} --json",
    )
    items = json.loads(listing.stdout)["items"]
    on_disk = [item for item in items if item["on_disk"]]
    sc.check(len(on_disk) == 2, f"expected two releases on disk: {items!r}")
    newer, older = on_disk[0]["id"], on_disk[1]["id"]
    sc.check(on_disk[0]["active"], f"the newest release is not the active one: {items!r}")

    timed = sc.run(
        f"start=$(date +%s%N); noust releases rollback {RELEASE_DOMAIN}; "
        'echo "elapsed_ms=$(( ($(date +%s%N) - start) / 1000000 ))"',
        timeout=120,
        label=f"noust releases rollback {RELEASE_DOMAIN} (timed)",
    )
    elapsed = int(timed.stdout.rsplit("elapsed_ms=", 1)[1].strip())
    sc.check(elapsed < 30_000, f"the rollback took {elapsed} ms")
    sc.check(
        active_release(sc, "readlink current (after the rollback)") == f"releases/{older}",
        "current does not point at the previous release",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (after the rollback)",
    )
    sc.check(page.stdout.strip() == "ok 1", f"expected 'ok 1', got {page.stdout!r}")

    row = sc.run(
        store_query(
            "SELECT status, triggered_by, log_path FROM deployments "
            "WHERE domain = 'rel.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the deployment row of the rollback",
    ).stdout.strip()
    status, trigger, log_path = row.split("|")
    sc.check((status, trigger) == ("success", "cli"), f"deployment row: {row!r}")
    rebuilt = sc.run(
        f"grep -cE 'npm (ci|install)|Installing' {log_path} || true",
        timeout=15,
        label="grep the rollback log for an install",
    )
    sc.check(rebuilt.stdout.strip() == "0", "the rollback installed something")
    statuses = dict(
        line.split("=", 1)
        for line in sc.run(
            store_query(
                "SELECT r.id || '=' || r.status FROM releases r JOIN apps a ON a.id = r.app_id "
                "WHERE a.domain = 'rel.test'"
            ),
            timeout=15,
            label="SELECT the status of every release of rel.test",
        ).stdout.split()
    )
    sc.check(
        (statuses.get(older), statuses.get(newer)) == ("active", "rolled_back"),
        f"release statuses: {statuses!r}",
    )

    # And forward again, by id.
    sc.run(
        f"noust releases rollback {RELEASE_DOMAIN} {newer}",
        timeout=120,
        label=f"noust releases rollback {RELEASE_DOMAIN} {newer}",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (forward again)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"expected 'ok 2', got {page.stdout!r}")


@scenario("inplace_app_migrate_keeps_uploads")
def scenario_migrate(sc: Scenario) -> None:
    """An in-place app with an upload moves onto releases, keeps serving and keeps the upload.

    node.test was deployed in place and took an upload into its own tree
    (uploads/, gitignored) in the first Node scenario. A rehearsal changes
    nothing; the migration moves every file, none copied or lost; the upload
    ends in shared/ and is still reachable; an update afterwards builds a
    release that sees it too.
    """
    before_listing = sc.run(f"ls -A {INPLACE_ROOT}", timeout=15, label=f"ls -A {INPLACE_ROOT}")
    before = tree_census(sc, INPLACE_ROOT, "count files and bytes before the migration")

    sc.run(
        f"noust --dry-run app migrate {INPLACE_DOMAIN}",
        timeout=60,
        label=f"noust --dry-run app migrate {INPLACE_DOMAIN}",
    )
    sc.check(
        sc.run(f"ls -A {INPLACE_ROOT}", timeout=15, label="ls -A (after the rehearsal)").stdout
        == before_listing.stdout,
        "the rehearsal changed the application directory",
    )

    sc.run(
        f"noust app migrate {INPLACE_DOMAIN} --yes",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust app migrate {INPLACE_DOMAIN} --yes",
    )
    after = tree_census(sc, INPLACE_ROOT, "count files and bytes after the migration")
    sc.check(after == before, f"files and bytes before {before!r}, after {after!r}")

    layout = sc.run(
        store_query(
            "SELECT layout || ' ' || persistent_paths FROM apps WHERE domain = 'node.test'"
        ),
        timeout=15,
        label="SELECT layout, persistent_paths FROM apps WHERE domain = 'node.test'",
    )
    sc.check(
        layout.stdout.strip().startswith("releases") and "uploads" in layout.stdout,
        f"store row: {layout.stdout!r}",
    )
    tree = sc.run(
        f"ls -A {INPLACE_ROOT}; readlink {INPLACE_ROOT}/current {INPLACE_ROOT}/current/uploads "
        f"{INPLACE_ROOT}/current/.env; cat {INPLACE_ROOT}/shared/uploads/upload.txt",
        timeout=15,
        label="the migrated tree: entries, links and the upload in shared/",
    )
    for expected in ("current", "releases", "shared", "../../shared/uploads", "../../shared/.env"):
        sc.check(expected in tree.stdout.split(), f"{expected} missing: {tree.stdout!r}")
    sc.check("integration-harness-upload" in tree.stdout, "the upload is not in shared/uploads")
    unit = sc.run(
        "systemctl cat node-test | grep -E '^WorkingDirectory='",
        timeout=15,
        label="systemctl cat node-test | grep WorkingDirectory",
    )
    sc.check(
        f"WorkingDirectory={INPLACE_ROOT}/current" in unit.stdout,
        f"the unit does not run from current: {unit.stdout!r}",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/ (after the migration)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"expected 'ok 2', got {page.stdout!r}")

    # A new upload lands in shared/, through the release.
    sc.run(
        f"curl -sS -H 'Host: {INPLACE_DOMAIN}' --data-binary 'after-migration' "
        "http://127.0.0.1/upload",
        timeout=30,
        label=f"curl -H 'Host: {INPLACE_DOMAIN}' --data-binary ... /upload (after the migration)",
    )
    sc.check(
        "after-migration"
        in sc.run(
            f"cat {INPLACE_ROOT}/shared/uploads/upload.txt",
            timeout=15,
            label="cat shared/uploads/upload.txt",
        ).stdout,
        "an upload after the migration did not land in shared/",
    )

    # The migrated application keeps updating, now as releases.
    sc.run(
        "cd /root/fixtures/node-app && echo 3 > VERSION && "
        "git add VERSION && git commit -q -m 'bump version to 3'",
        timeout=30,
        label="(fixture repo) echo 3 > VERSION; git commit",
    )
    sc.run(
        f"noust update {INPLACE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust update {INPLACE_DOMAIN} (after the migration)",
    )
    count = sc.run(f"ls {INPLACE_ROOT}/releases | wc -l", timeout=15, label="ls releases | wc -l")
    sc.check(count.stdout.strip() == "2", f"expected 2 releases, found {count.stdout.strip()}")
    page = sc.run(
        f"curl -sS -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/ (after the update)",
    )
    sc.check(page.stdout.strip() == "ok 3", f"expected 'ok 3', got {page.stdout!r}")
    kept = sc.run(
        f"cat {INPLACE_ROOT}/current/uploads/upload.txt",
        timeout=15,
        check=False,
        label="cat current/uploads/upload.txt (through the new release)",
    )
    sc.check("after-migration" in kept.stdout, "the new release does not see the upload")


def limits_unit(sc: Scenario, unit: str) -> str:
    """The unit that carries an application's limits: its template's instance, when it has one."""
    probe = docker_exec(sc.container, f"systemctl cat {unit}@.service", check=False)
    return f"{unit}@blue" if probe.returncode == 0 else unit


@scenario("resource_limits_in_systemd")
def scenario_limits(sc: Scenario) -> None:
    """`noust app limits` puts the limits in the unit, and systemd reports them.

    ``--restart`` passes the same health gate as a deploy, so the application
    answers again the moment the command returns.
    """
    serving = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (before the limits)",
    ).stdout.strip()
    sc.check(serving.startswith("ok "), f"{RELEASE_DOMAIN} is not serving: {serving!r}")
    sc.run(
        f"noust app limits {RELEASE_DOMAIN} --memory 512M --cpu 50% --tasks 256 --restart",
        timeout=60,
        label=f"noust app limits {RELEASE_DOMAIN} --memory 512M --cpu 50% --tasks 256 --restart",
    )
    # An application that updates without downtime (the default on releases) runs as the
    # instances of one template, and the limits are the template's: the plain unit is not what
    # serves, and says nothing about them.
    serving_unit = limits_unit(sc, "rel-test")
    shown = sc.run(
        f"systemctl show {serving_unit} -p MemoryMax,CPUQuotaPerSecUSec,TasksMax",
        timeout=15,
        label=f"systemctl show {serving_unit} -p MemoryMax,CPUQuotaPerSecUSec,TasksMax",
    )
    properties = dict(line.split("=", 1) for line in shown.stdout.split())
    sc.check(
        properties
        == {"MemoryMax": str(512 * 1024 * 1024), "CPUQuotaPerSecUSec": "500ms", "TasksMax": "256"},
        f"systemd reports {properties!r}",
    )
    states = sc.run(
        "systemctl is-active rel-test rel-test@blue rel-test@green; true",
        timeout=15,
        check=False,
        label="is-active of the unit and of both instances",
    ).stdout.split()
    sc.check("active" in states, f"the application did not come back under its limits: {states}")
    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (under limits)",
    )
    sc.check(page.stdout.strip() == serving, f"expected {serving!r}, got {page.stdout!r}")
    row = sc.run(
        store_query(
            "SELECT memory_max_mb || ' ' || cpu_quota_percent || ' ' || tasks_max "
            "FROM apps WHERE domain = 'rel.test'"
        ),
        timeout=15,
        label="SELECT the limits of rel.test",
    )
    sc.check(row.stdout.strip() == "512 50 256", f"store row: {row.stdout!r}")

    # Removing one removes its directive.
    sc.run(
        f"noust app limits {RELEASE_DOMAIN} --memory none",
        timeout=60,
        label=f"noust app limits {RELEASE_DOMAIN} --memory none",
    )
    template = "rel-test@.service" if serving_unit.endswith("@blue") else "rel-test"
    unit = sc.run(
        f"systemctl cat {template} | grep -E '^(MemoryMax|CPUQuota|TasksMax)=' || true",
        timeout=15,
        label=f"systemctl cat {template} | grep the limit directives",
    )
    sc.check(
        unit.stdout.split() == ["CPUQuota=50%", "TasksMax=256"],
        f"directives after removing the memory limit: {unit.stdout!r}",
    )


def curl_host(sc: Scenario, host: str, path: str = "/", *, head: bool = False) -> str:
    """Ask nginx on loopback for ``path`` as ``host``; return the body, or the headers."""
    flags = "-sS -I" if head else "-sS"
    return sc.run(
        f"curl {flags} -H 'Host: {host}' 'http://127.0.0.1{path}'",
        timeout=30,
        check=False,
        label=f"curl {flags} -H 'Host: {host}' http://127.0.0.1{path}",
    ).stdout


def check_alias_and_redirect(sc: Scenario, primary: str, alias: str, redirect: str) -> None:
    """Give an app an alias and a redirect, and see nginx serve and redirect them."""
    sc.run(
        f"noust domain add {primary} {alias} --kind alias",
        timeout=60,
        label=f"noust domain add {primary} {alias} --kind alias",
    )
    sc.run(
        f"noust domain add {primary} {redirect} --kind redirect",
        timeout=60,
        label=f"noust domain add {primary} {redirect} --kind redirect",
    )
    listed = sc.run(
        f"noust domain list {primary} --json",
        timeout=30,
        label=f"noust domain list {primary} --json",
    )
    kinds = {item["domain"]: item["kind"] for item in json.loads(listed.stdout)["items"]}
    sc.check(
        kinds == {primary: "primary", alias: "alias", redirect: "redirect"},
        f"noust domain list says {kinds!r}",
    )

    served = curl_host(sc, primary).strip()
    sc.check(served.startswith("ok "), f"{primary} is not serving: {served!r}")
    through_alias = curl_host(sc, alias).strip()
    sc.check(through_alias == served, f"{alias} served {through_alias!r}, not {served!r}")

    headers = curl_host(sc, redirect, "/some/path?q=1", head=True)
    status_line = headers.splitlines()[0] if headers else ""
    location = re.search(r"^location:\s*(\S+)", headers, re.IGNORECASE | re.MULTILINE)
    sc.check(" 301" in status_line, f"{redirect} answered {status_line!r}, not a 301")
    sc.check(
        location is not None and location.group(1) == f"http://{primary}/some/path?q=1",
        f"{redirect} redirected to {location.group(1) if location else None!r}",
    )
    sc.run("nginx -t", timeout=15, label="nginx -t (after the domain changes)")


@scenario("domains_alias_redirect_and_removal")
def scenario_domains(sc: Scenario) -> None:
    """An app answers on an alias and redirects another name; removing the alias stops it.

    Once in place and once on releases: the site of a release app is written
    against ``current``, and a domain change must render it that way too.
    The primary cannot be removed, and a removal takes effect within seconds.
    """
    sc.run(
        "noust create -d dom.test -s /root/fixtures/node-app -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label="noust create -d dom.test -s /root/fixtures/node-app -t nodejs --no-ssl "
        "--layout inplace",
    )
    check_alias_and_redirect(sc, "dom.test", "alias.dom.test", "old-dom.test")

    sc.run(
        "noust domain remove dom.test alias.dom.test",
        timeout=60,
        label="noust domain remove dom.test alias.dom.test",
    )
    # nginx -s reload signals the master and returns before the new workers
    # take connections, so an old worker may answer for a moment: the removal
    # must take effect within seconds, not before the command returns.
    deadline = time.monotonic() + 5
    after = curl_host(sc, "alias.dom.test").strip()
    while after.startswith("ok ") and time.monotonic() < deadline:
        time.sleep(0.25)
        after = curl_host(sc, "alias.dom.test").strip()
    sc.check(
        not after.startswith("ok "),
        f"alias.dom.test is still served by dom.test 5 s after its removal: {after!r}",
    )
    sc.check(
        curl_host(sc, "dom.test").strip().startswith("ok "),
        "dom.test stopped serving when its alias was removed",
    )
    config = sc.run(
        "cat /etc/nginx/sites-available/dom.test",
        timeout=15,
        label="cat /etc/nginx/sites-available/dom.test (after the removal)",
    )
    sc.check("alias.dom.test" not in config.stdout, "the removed alias is still in the site")

    refused = sc.run(
        "noust domain remove dom.test dom.test",
        timeout=30,
        check=False,
        label="noust domain remove dom.test dom.test (the primary)",
    )
    sc.check(refused.returncode == 1, "removing the primary domain was not refused")

    sc.run(
        f"noust create -d reldom.test -s {RELEASE_URL} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d reldom.test -s {RELEASE_URL} -t nodejs --no-ssl --layout releases",
    )
    check_alias_and_redirect(sc, "reldom.test", "alias.reldom.test", "old-reldom.test")


# ---------------------------------------------------------------------------
# Upgrade rehearsal: 1.6.5 -> 2.0 (--upgrade mode; not part of the default suite)
# ---------------------------------------------------------------------------
#
# This is the most important pre-release scenario for 2.0: a real 1.6.5
# server, with real applications, upgraded in place. It needs a different
# setup sequence to every scenario above (install the released package
# first, upgrade to the working tree's wheel second), so it runs in its own
# container through `run.py --upgrade` instead of being one more entry in
# SCENARIOS: a plain `run.py` invocation, and CI, keep running exactly the
# scenarios they always have.


#: The stack the Compose health gate scenario deploys, served by git daemon.
COMPOSE_GATE_REPO = "/root/fixtures/compose-gate"
COMPOSE_GATE_URL = "git://127.0.0.1/compose-gate"
COMPOSE_GATE_DOMAIN = "compose-gate.test"
COMPOSE_GATE_ROOT = "/var/www/apps/compose-gate-test"
COMPOSE_GATE_PROJECT = "compose-gate-test"


def compose_gate_web(sc: Scenario, script: str, label: str) -> subprocess.CompletedProcess[str]:
    """Run a shell command inside the stack's web container."""
    container = (
        "$(docker ps -q "
        f"-f label=com.docker.compose.project={COMPOSE_GATE_PROJECT} "
        "-f label=com.docker.compose.service=web)"
    )
    return sc.run(f"docker exec {container} sh -c '{script}'", timeout=30, label=label)


@scenario("compose_update_rolls_back")
def scenario_compose_rollback(sc: Scenario) -> None:
    """A stack whose update does not answer goes back to the images and commit that served.

    The web service is built from the repository, so going back needs the
    image the old container ran, not a rebuild. A file written into a named
    volume before the update must still be there after the way back.
    Skipped, saying why, when Docker is not usable inside the container: a scenario that
    checked nothing is not a PASS. The stack is removed whatever happens: a project left
    running here is one every later scenario that counts projects finds.
    """
    try:
        _compose_rollback(sc)
    finally:
        compose_clean(sc, COMPOSE_GATE_DOMAIN)


def _compose_rollback(sc: Scenario) -> None:
    """The body of :func:`scenario_compose_rollback`."""
    require_docker(sc)
    pull_images(sc, "nginx:alpine")

    sc.run(
        f"mkdir -p {COMPOSE_GATE_REPO}/html && cd {COMPOSE_GATE_REPO} && "
        "printf 'FROM nginx:alpine\\nCOPY html /usr/share/nginx/html\\n' > Dockerfile && "
        "printf 'gate v1' > html/index.html && "
        'printf \'services:\\n  web:\\n    build: .\\n    ports:\\n      - "18091:80"\\n'
        "    volumes:\\n      - data:/data\\nvolumes:\\n  data: {}\\n' > docker-compose.yml && "
        "git init -q && git config user.email noust-it@example.com && "
        "git config user.name 'Noust Integration' && git add -A && git commit -q -m 'gate v1'",
        timeout=30,
        label="(fixture repo) a stack whose web image is built from the repository",
    )
    first = sc.run(
        f"git -C {COMPOSE_GATE_REPO} rev-parse HEAD", timeout=15, label="the first commit"
    ).stdout.strip()

    # --port: this scenario is about the way back, not about where the site goes. Without it a
    # stack got a free port that its site proxied to, whatever the stack published (the
    # published-ports scenario checks that Noust finds the port by itself).
    compose_create(sc, COMPOSE_GATE_DOMAIN, COMPOSE_GATE_URL, port=18091)
    wait_served(sc, COMPOSE_GATE_DOMAIN, "gate v1")
    compose_gate_web(sc, "echo kept > /data/marker", "write a marker into the named volume")

    # nginx refuses to start on a broken configuration: the container exits.
    sc.run(
        f"cd {COMPOSE_GATE_REPO} && printf 'gate v2' > html/index.html && "
        "printf 'FROM nginx:alpine\\nCOPY html /usr/share/nginx/html\\n"
        "RUN echo broken > /etc/nginx/conf.d/broken.conf\\n' > Dockerfile && "
        "git add -A && git commit -q -m 'gate v2: nginx cannot start'",
        timeout=30,
        label="(fixture repo) commit a version whose container cannot start",
    )
    update = sc.run(
        f"noust update {COMPOSE_GATE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust update {COMPOSE_GATE_DOMAIN}",
    )
    output = update.stdout + update.stderr
    sc.check(update.returncode != 0, "an update that does not answer must fail")
    sc.check(
        "did not pass its health check" in output and "running again" in output,
        f"the update did not say it went back: {output!r}",
    )

    page = sc.run(
        f"curl -sS -H 'Host: {COMPOSE_GATE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {COMPOSE_GATE_DOMAIN}' http://127.0.0.1/",
    )
    sc.check("gate v1" in page.stdout, f"v1 is not serving again: {page.stdout!r}")
    marker = compose_gate_web(sc, "cat /data/marker", "read the marker back from the volume")
    sc.check(marker.stdout.strip() == "kept", f"the named volume lost its data: {marker.stdout!r}")
    head = sc.run(
        f"git -C {COMPOSE_GATE_ROOT} rev-parse HEAD", timeout=15, label="the tree's commit"
    )
    sc.check(head.stdout.strip() == first, f"the tree is not back on v1: {head.stdout!r}")
    kept = sc.run(
        f"docker image ls --format '{{{{.Repository}}}}:{{{{.Tag}}}}' {COMPOSE_GATE_PROJECT}-web",
        timeout=15,
        label=f"docker image ls {COMPOSE_GATE_PROJECT}-web",
    )
    sc.check(
        f"{COMPOSE_GATE_PROJECT}-web:noust_previous" in kept.stdout,
        f"the image that served was not kept: {kept.stdout!r}",
    )
    row = sc.run(
        store_query(
            "SELECT status FROM deployments WHERE domain = 'compose-gate.test' "
            "ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the update's history row",
    )
    sc.check(row.stdout.strip() == "failed", f"the update's row: {row.stdout!r}")


# ---------------------------------------------------------------------------
# 2.2: blue/green, remote backups, pull request previews, deploy notifications
# ---------------------------------------------------------------------------

#: Scripts the 2.2 scenarios run inside the container: a load generator and a
#: notification recorder. Copied in by :func:`install_tools`.
TOOLS_DIR = INTEGRATION_DIR / "tools"
CONTAINER_TOOLS = "/root/it-tools"

#: The notification recorder: a POST listener the ``webhook`` channel points at.
RECORDER_UNIT = "noust-it-recorder"
NOTIFY_PORT = 9199
NOTIFY_URL = f"http://127.0.0.1:{NOTIFY_PORT}/noust"
NOTIFY_LOG = "/root/it-notifications.jsonl"

#: The load generator: one request through nginx every LOAD_INTERVAL seconds.
LOAD_UNIT = "noust-it-load"
LOAD_LOG = "/root/it-load.log"
LOAD_INTERVAL = 0.05

#: What every repository the 2.2 scenarios create commits as.
GIT_IDENTITY = (
    "git config user.email noust-it@example.com && git config user.name 'Noust Integration'"
)


def install_tools(sc: Scenario) -> None:
    """Copy tests/integration/tools into the container; idempotent."""
    sh(["docker", "exec", sc.container, "mkdir", "-p", CONTAINER_TOOLS], timeout=30)
    sh(["docker", "cp", f"{TOOLS_DIR}/.", f"{sc.container}:{CONTAINER_TOOLS}"], timeout=30)


def make_node_repo(sc: Scenario, name: str) -> tuple[str, str]:
    """
    Create a repository of the Node fixture at VERSION 1 on ``main``, served by git daemon.

    Its own repository, so the commits a scenario makes never reach another
    scenario's application. git daemon exports everything under
    /root/fixtures, so the repository is reachable the moment it exists.

    Args:
        sc: The scenario.
        name: Path under /root/fixtures, such as ``bg-app`` or ``acme/prev-app``.

    Returns:
        The repository's path in the container and its git:// URL.
    """
    repo = f"/root/fixtures/{name}"
    sc.run(
        f"rm -rf {repo} && mkdir -p {repo} && "
        f"git -C /root/fixtures/node-app archive HEAD | tar -x -C {repo} && "
        f"cd {repo} && echo 1 > VERSION && git init -q -b main && {GIT_IDENTITY} && "
        "git add -A && git commit -q -m 'version 1'",
        timeout=30,
        label=f"(fixture repo) {repo}: the Node fixture at VERSION 1, served at "
        f"git://127.0.0.1/{name}",
    )
    return repo, f"git://127.0.0.1/{name}"


def commit_to(sc: Scenario, repo: str, script: str, message: str) -> str:
    """Change a fixture repository, commit it, and return the new commit."""
    return sc.run(
        f"cd {repo} && {script} && git add -A && git commit -q -m '{message}' && "
        "git rev-parse HEAD",
        timeout=30,
        label=f"(fixture repo {repo}) {script}; git commit -m '{message}'",
    ).stdout.strip()


def json_of(proc: subprocess.CompletedProcess[str], what: str) -> Any:
    """Parse a command's JSON output, whatever it printed around it."""
    text = proc.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise AssertionError(f"{what} printed no JSON: {text!r}") from None
        return json.loads(text[start:])


def journal_tail(sc: Scenario, units: str, label: str) -> None:
    """Put the end of some units' journal into the evidence, for a failure."""
    sc.run(
        f"journalctl --no-pager -n 60 {units} || true",
        timeout=30,
        check=False,
        label=label,
    )


# -- Notifications -----------------------------------------------------------


def start_notifications(sc: Scenario, *, started: bool) -> None:
    """
    Start the recorder and point Noust's ``webhook`` channel at it.

    Loopback is inside the SSRF guard's forbidden networks, so the recorder's
    host is listed under ``notifications.allow_private_hosts``, which is
    exactly what an operator with an internal endpoint does.

    Args:
        sc: The scenario.
        started: Whether ``deploy_started`` is switched on.
    """
    install_tools(sc)
    sc.run(
        f"systemctl stop {RECORDER_UNIT} 2>/dev/null; systemctl reset-failed {RECORDER_UNIT} "
        f"2>/dev/null; rm -f {NOTIFY_LOG}; systemd-run --unit {RECORDER_UNIT} --collect "
        f"/usr/bin/python3 {CONTAINER_TOOLS}/recorder.py {NOTIFY_PORT} {NOTIFY_LOG}",
        timeout=30,
        label=f"start the notification recorder (POST listener on 127.0.0.1:{NOTIFY_PORT})",
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' -X POST -d '{{}}' {NOTIFY_URL}",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "204":
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the notification recorder never answered")
    sc.run(f": > {NOTIFY_LOG}", timeout=15, label="empty the recorder's log")
    for setting in (
        f"notifications.channels.webhook.webhook_url {NOTIFY_URL}",
        "notifications.allow_private_hosts 127.0.0.1 --list",
        f"notifications.events.deploy_started {'true' if started else 'false'}",
        "notifications.enabled true",
    ):
        sc.run(f"noust config set {setting}", timeout=30, label=f"noust config set {setting}")


def stop_notifications(sc: Scenario) -> None:
    """Turn notifications off and stop the recorder; never raises."""
    sc.run(
        "noust config set notifications.enabled false; "
        "noust config set notifications.events.deploy_started false; "
        f"systemctl stop {RECORDER_UNIT} 2>/dev/null; true",
        timeout=60,
        check=False,
        label="turn notifications off and stop the recorder",
    )


def recorded_notifications(sc: Scenario, label: str) -> list[dict[str, Any]]:
    """Every notification the recorder received, in order."""
    lines = sc.run(f"cat {NOTIFY_LOG}", timeout=15, label=label).stdout.splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# -- Load --------------------------------------------------------------------


def start_load(
    sc: Scenario,
    host: str,
    *,
    interval: float = LOAD_INTERVAL,
    path: str = "/",
    accept: str = "2",
) -> int:
    """
    Start one request through nginx every ``interval`` seconds as ``host``, and wait for answers.

    Args:
        sc: The scenario.
        host: The ``Host`` header, which picks the site.
        interval: Seconds between requests.
        path: What to ask for.
        accept: The leading digits of the statuses that count as served: ``"2"`` for 2xx,
            ``"23"`` when the application answers with a redirect.

    Returns:
        The log's line count once it is running: the first phase's start.
    """
    install_tools(sc)
    sc.run(
        f"systemctl stop {LOAD_UNIT} 2>/dev/null; systemctl reset-failed {LOAD_UNIT} 2>/dev/null; "
        f"rm -f {LOAD_LOG}; systemd-run --unit {LOAD_UNIT} --collect /usr/bin/python3 "
        f"{CONTAINER_TOOLS}/load.py {host} {LOAD_LOG} {interval} {path} {accept}",
        timeout=30,
        label=f"start the load: GET {path} as {host} through nginx every {int(interval * 1000)} ms",
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        count = load_mark(sc)
        if count >= 10:
            return count
        time.sleep(0.5)
    raise AssertionError("the load generator is not writing its log")


def load_mark(sc: Scenario) -> int:
    """The number of complete lines in the load log."""
    proc = docker_exec(sc.container, f"wc -l < {LOAD_LOG} 2>/dev/null || echo 0", timeout=15)
    return int(proc.stdout.strip() or 0)


@dataclass
class LoadReport:
    """What the load saw during one phase."""

    requests: int
    failures: list[str]
    mark: int


def load_report(sc: Scenario, phase: str, since: int) -> LoadReport:
    """
    Count what the load saw since a mark and put it in the evidence; judge nothing.

    Args:
        sc: The scenario.
        phase: What was happening, for the evidence.
        since: The mark the phase started at.

    Returns:
        The requests made, the failed ones, and the mark the next phase starts at.
    """
    # Requests in flight when the command returned land within the timeout.
    time.sleep(1)
    proc = docker_exec(sc.container, f"tail -n +{since + 1} {LOAD_LOG}", timeout=30)
    text = proc.stdout
    if not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
    lines = text.splitlines()
    failures = [line for line in lines if not line.startswith("OK ")]
    answers: dict[str, int] = {}
    order: list[str] = []
    for line in lines:
        if line.startswith("OK "):
            body = line.split(" ", 3)[3] if line.count(" ") >= 3 else ""
            if body not in answers:
                order.append(body)
            answers[body] = answers.get(body, 0) + 1
    seconds = 0.0
    if lines:
        seconds = float(lines[-1].split(" ", 2)[1]) - float(lines[0].split(" ", 2)[1])
    summary = ", ".join(f"{body!r} x{answers[body]}" for body in order) or "none"
    sc.evidence.append(
        f"[load] {phase}: {len(lines)} requests through nginx over {seconds:.1f}s, "
        f"{len(failures)} failed; answers in order of appearance: {summary}"
        + ("\n" + "\n".join(f"  {line}" for line in failures[:30]) if failures else "")
    )
    return LoadReport(requests=len(lines), failures=failures, mark=since + len(lines))


def load_phase(sc: Scenario, phase: str, since: int, *, minimum: int = 20) -> int:
    """
    Count what the load saw since a mark, put it in the evidence, and fail on any error.

    Args:
        sc: The scenario.
        phase: What was happening, for the evidence.
        since: The mark the phase started at.
        minimum: The fewest requests the phase must have seen for its silence to mean something.

    Returns:
        The mark the next phase starts at.
    """
    report = load_report(sc, phase, since)
    running = docker_exec(sc.container, f"systemctl is-active {LOAD_UNIT}", check=False)
    sc.check(running.stdout.strip() == "active", f"the load generator died during {phase}")
    sc.check(report.requests >= minimum, f"{phase}: only {report.requests} requests were made")
    sc.check(
        not report.failures,
        f"{phase}: {len(report.failures)} of {report.requests} requests failed: "
        f"{report.failures[:5]}",
    )
    return report.mark


# -- Blue/green ----------------------------------------------------------------

BG_DOMAIN = "bg.test"
BG_APP = "bg-test"
BG_PORT = 3710
BG_UPSTREAM = f"/etc/nginx/noust-upstreams/{BG_APP}.conf"
BG_TEMPLATE = f"/etc/systemd/system/{BG_APP}@.service"
BG_UNIT_FILE = f"/etc/systemd/system/{BG_APP}.service"


def zero_downtime_status(sc: Scenario, label: str) -> dict[str, Any]:
    """``noust app zero-downtime DOMAIN --json``."""
    status: dict[str, Any] = json_of(
        sc.run(f"noust app zero-downtime {BG_DOMAIN} --json", timeout=30, label=label), label
    )
    return status


def unit_states(sc: Scenario, label: str) -> dict[str, str]:
    """systemd's ActiveState of the application's own unit and of both instances."""
    units = [BG_APP, f"{BG_APP}@blue", f"{BG_APP}@green"]
    proc = sc.run(f"systemctl is-active {' '.join(units)}", timeout=15, check=False, label=label)
    return dict(zip(units, proc.stdout.split(), strict=False))


def listening(sc: Scenario, port: int) -> bool:
    """Whether something listens on a loopback TCP port."""
    proc = docker_exec(sc.container, f"ss -ltnH 'sport = :{port}'", timeout=15, check=False)
    return bool(proc.stdout.strip())


@scenario("blue_green_zero_downtime")
def scenario_blue_green(sc: Scenario) -> None:
    """
    Blue/green: an update, a rollback, a broken update and switching off serve every request.

    A Node app on releases is switched to two instances with a 3 s drain.
    While a request goes through nginx every 50 ms, it is updated (the other
    colour serves the new release), rolled back (the first colour serves the
    old one), updated to a commit whose server throws at start (the update
    fails, the colour that served keeps serving and a rolled-back
    notification goes out), and switched off (its own unit serves on its own
    port again). Not one request may fail in any of them.
    """
    repo, url = make_node_repo(sc, "bg-app")
    create = f"noust create -d {BG_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases --port {BG_PORT}"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        _blue_green(sc, repo)
    except AssertionError:
        journal_tail(sc, f"-u '{BG_APP}*'", f"journalctl -u '{BG_APP}*' (on failure)")
        raise
    finally:
        sc.run(f"systemctl stop {LOAD_UNIT}; true", timeout=30, check=False, label="stop the load")
        stop_notifications(sc)
        sc.run(f"noust delete {BG_DOMAIN} -f", timeout=180, check=False, label="cleanup")


def _blue_green(sc: Scenario, repo: str) -> None:
    """The body of :func:`scenario_blue_green`."""
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' before the switch, got {served!r}")

    sc.run(
        f"noust app zero-downtime {BG_DOMAIN} on --drain 3",
        timeout=180,
        label=f"noust app zero-downtime {BG_DOMAIN} on --drain 3",
    )
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after on)")
    sc.check(
        status["enabled"] and status["active_color"] == "green" and status["drain_seconds"] == 3,
        f"the mode after on: {status!r}",
    )
    sc.check(status["upstream_port"] == BG_PORT + 1, f"upstream port: {status!r}")
    states = unit_states(sc, "systemctl is-active (after on)")
    sc.check(states.get(f"{BG_APP}@green") == "active", f"green is not active: {states!r}")
    sc.check(states.get(BG_APP) != "active", f"the old single unit still runs: {states!r}")
    files = sc.run(
        f"cat {BG_UPSTREAM}; test -e {BG_UNIT_FILE} && echo single-unit-file-present; "
        f"test -e {BG_TEMPLATE} && echo template-present; "
        f"grep -c 'noust-upstreams/{BG_APP}.conf' /etc/nginx/sites-available/{BG_DOMAIN}",
        timeout=15,
        check=False,
        label="the upstream, the unit files and the site (after on)",
    )
    sc.check(
        f"server 127.0.0.1:{BG_PORT + 1};" in files.stdout,
        f"the upstream does not name green's port: {files.stdout!r}",
    )
    sc.check("single-unit-file-present" not in files.stdout, f"{BG_UNIT_FILE} is still there")
    sc.check("template-present" in files.stdout, f"{BG_TEMPLATE} was not written")
    sc.check(files.stdout.strip().endswith("1"), "the site does not include the upstream file")
    sc.check(
        listening(sc, BG_PORT + 1) and not listening(sc, BG_PORT),
        "green should listen on port+1 and nothing on the app's own port",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' through the upstream, got {served!r}")

    mark = start_load(sc, BG_DOMAIN)

    # (a) An update starts the new release on blue and switches to it.
    commit_to(sc, repo, "echo 2 > VERSION", "version 2")
    sc.run(f"noust update {BG_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {BG_DOMAIN}")
    mark = load_phase(sc, "(a) noust update (green -> blue)", mark)
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after the update)")
    sc.check(status["active_color"] == "blue", f"the update did not switch colour: {status!r}")
    sc.check(status["upstream_port"] == BG_PORT, f"upstream after the update: {status!r}")
    states = unit_states(sc, "systemctl is-active (after the update)")
    sc.check(
        states.get(f"{BG_APP}@blue") == "active" and states.get(f"{BG_APP}@green") != "active",
        f"green was not stopped after the drain: {states!r}",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 2", f"expected 'ok 2' after the update, got {served!r}")

    # (b) A rollback puts the previous release back on green.
    sc.run(
        f"noust releases rollback {BG_DOMAIN}",
        timeout=180,
        label=f"noust releases rollback {BG_DOMAIN}",
    )
    mark = load_phase(sc, "(b) noust releases rollback (blue -> green)", mark)
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after the rollback)")
    sc.check(status["active_color"] == "green", f"the rollback did not switch colour: {status!r}")
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' after the rollback, got {served!r}")

    # (c) A release that throws at start never takes the traffic.
    start_notifications(sc, started=False)
    commit_to(
        sc,
        repo,
        "echo 3 > VERSION && sed -i '1i throw new Error(\"broken on purpose\");' server.js",
        "break the server",
    )
    broken = sc.run(
        f"noust update {BG_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust update {BG_DOMAIN} (a commit whose server throws at start)",
    )
    mark = load_phase(sc, "(c) noust update to a broken commit (green keeps serving)", mark)
    sc.check(broken.returncode != 0, "the update of a release that never ran reported success")
    sc.check(
        "green instance kept serving" in broken.stdout + broken.stderr,
        "the failure does not say the serving instance kept serving",
    )
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after the broken update)")
    sc.check(status["active_color"] == "green", f"the colour moved: {status!r}")
    sc.check(status["upstream_port"] == BG_PORT + 1, f"the upstream moved: {status!r}")
    states = unit_states(sc, "systemctl is-active (after the broken update)")
    sc.check(states.get(f"{BG_APP}@blue") != "active", f"blue was left running: {states!r}")
    row = sc.run(
        store_query(
            "SELECT status || '|' || COALESCE(error, '') FROM deployments "
            "WHERE domain = 'bg.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the last deployment of bg.test",
    ).stdout.strip()
    sc.check(
        row.split("|", 1)[0] in ("failed", "rolled_back") and "broken on purpose" in row,
        f"the history does not show the failed deployment with its cause: {row!r}",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' after the failed update, got {served!r}")
    events = [
        event
        for event in recorded_notifications(sc, "the notifications the recorder received")
        if event.get("domain") == BG_DOMAIN
    ]
    kinds = [event.get("event") for event in events]
    sc.check(
        kinds == ["deploy_rolled_back"],
        f"expected exactly one deploy_rolled_back notification for {BG_DOMAIN}, got {kinds!r}",
    )
    sc.check(
        "broken on purpose" in str(events[0].get("body")),
        f"the rolled-back notification does not carry the health gate's cause: {events[0]!r}",
    )
    stop_notifications(sc)
    sc.run(f"cd {repo} && git revert --no-edit HEAD", timeout=30, label="(fixture repo) revert")

    # (d) Switching off hands the traffic back to the app's own unit.
    sc.run(
        f"noust app zero-downtime {BG_DOMAIN} off",
        timeout=180,
        label=f"noust app zero-downtime {BG_DOMAIN} off",
    )
    mark = load_phase(sc, "(d) noust app zero-downtime off (green -> single unit)", mark)
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after off)")
    sc.check(not status["enabled"], f"the mode is still on: {status!r}")
    states = unit_states(sc, "systemctl is-active (after off)")
    sc.check(
        states.get(BG_APP) == "active"
        and states.get(f"{BG_APP}@blue") != "active"
        and states.get(f"{BG_APP}@green") != "active",
        f"units after off: {states!r}",
    )
    files = sc.run(
        f"test -e {BG_TEMPLATE} && echo template-present; "
        f"test -e {BG_UPSTREAM} && echo upstream-present; "
        f"test -e {BG_UNIT_FILE} && echo single-unit-file-present; "
        f"grep -c 'noust-upstreams' /etc/nginx/sites-available/{BG_DOMAIN}; "
        f"ls -A /var/www/apps/{BG_APP}",
        timeout=15,
        check=False,
        label="the template, the upstream, the unit file, the site and the app tree (after off)",
    )
    sc.check("template-present" not in files.stdout, f"{BG_TEMPLATE} is still there")
    sc.check("upstream-present" not in files.stdout, f"{BG_UPSTREAM} is still there")
    sc.check("single-unit-file-present" in files.stdout, f"{BG_UNIT_FILE} was not written")
    sc.check("\n0\n" in f"\n{files.stdout}", "the site still includes the upstream file after off")
    sc.check("colors" not in files.stdout.split(), "the instance links are still there")
    sc.check(
        listening(sc, BG_PORT) and not listening(sc, BG_PORT + 1),
        "the app's own unit should listen on its port and nothing on port+1",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' after off, got {served!r}")


# -- Remote backups ------------------------------------------------------------

BK_DOMAIN = "bk.test"
BK_APP = "bk-test"
BK_REMOTE_DIR = "/srv/noust-it-remote"
SFTP_IMAGE = "atmoz/sftp:alpine"
SFTP_USER = "noustit"


def local_backup_ids(sc: Scenario, label: str) -> list[str]:
    """The ids of the application's local archives, oldest first."""
    proc = sc.run(
        f"find /var/backups/noust -name '{BK_APP}_*.tar.gz' -printf '%f\\n' | sort",
        timeout=15,
        label=label,
    )
    return [name.removesuffix(".tar.gz") for name in proc.stdout.split()]


def remote_ids(listing: dict[str, Any]) -> list[str]:
    """The backup ids of a ``noust backup remote-list --app`` listing, sorted."""
    return sorted(entry["backup_id"] for entry in listing.get("backups", []))


@scenario("remote_backups_rclone")
def scenario_remote_backups(sc: Scenario) -> None:
    """
    Schedules push to rclone destinations with their own retention, and restore from them.

    A local directory destination takes three scheduled runs and keeps two;
    the local copies keep one; a backup is listed and restored from the
    remote. An SFTP server, a container of the harness container's own Docker, takes a push; with it
    stopped, a scheduled run exits non-zero, keeps its local backup and
    sends a backup_failed notification.
    """
    probe = sc.run("rclone version | head -1", timeout=30, check=False, label="rclone version")
    sc.check(probe.returncode == 0, "rclone is not installed in the image (Dockerfile.systemd)")
    create = f"noust create -d {BK_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    findings: list[str] = []
    sftp = "noust-it-sftp"
    try:
        _remote_backups_local(sc, findings)
        _remote_backups_sftp(sc, sftp, findings)
    finally:
        # The SFTP server is a container of the harness container's own Docker, never of the
        # machine's: the machine's Docker is shared with whoever else works on it.
        docker_exec(sc.container, f"docker rm -f {sftp}", timeout=60, check=False)
        stop_notifications(sc)
        sc.run(
            f"noust backup schedule delete {BK_DOMAIN}; "
            "noust backup destination remove itlocal -f; "
            "noust backup destination remove itsftp -f; "
            f"noust delete {BK_DOMAIN} -f; rm -rf {BK_REMOTE_DIR}",
            timeout=180,
            check=False,
            label="cleanup",
        )
    sc.check(not findings, "product findings:\n- " + "\n- ".join(findings))


def _remote_backups_local(sc: Scenario, findings: list[str]) -> None:
    """Part (a): a ``local`` destination, retention on both sides, remote-list, restore --from."""
    # A name with a dash is one validate_destination_name accepts, and the
    # one the console's own placeholder suggests. Checked on its own, so the
    # rest of the scenario runs with dashless names whatever it finds.
    dashed = sc.run(
        "noust backup destination add it-dash --type local --field path=/tmp && "
        "noust backup destination test it-dash",
        timeout=60,
        check=False,
        label="noust backup destination add it-dash --type local --field path=/tmp; ... test it-dash",
    )
    if dashed.returncode != 0:
        findings.append(
            "a destination whose name has a dash cannot be reached: rclone reads the remote "
            "`it-dash` from RCLONE_CONFIG_IT-DASH_*, but managers/backup_destinations.py "
            "_env_prefix() turns the dash into an underscore (RCLONE_CONFIG_IT_DASH_*), so "
            "rclone answers `didn't find section in config file` (rclone 1.60.1 from Ubuntu "
            "24.04 and upstream 1.75.1 alike)"
        )
    sc.run(
        "noust backup destination remove it-dash -f",
        timeout=60,
        check=False,
        label="noust backup destination remove it-dash -f",
    )

    sc.run(f"rm -rf {BK_REMOTE_DIR}", timeout=15, label=f"rm -rf {BK_REMOTE_DIR}")
    add = f"noust backup destination add itlocal --type local --field path={BK_REMOTE_DIR}"
    sc.run(add, timeout=60, label=add)
    fresh = sc.run(
        "noust backup destination test itlocal",
        timeout=60,
        check=False,
        label="noust backup destination test itlocal (its folder does not exist yet)",
    )
    if fresh.returncode != 0:
        findings.append(
            "`noust backup destination test` fails on a destination whose folder does not exist "
            "yet, although the path field's help says it is 'created if it does not exist' "
            "(managers/backup_destinations.py test(): `rclone lsf` of a missing directory)"
        )
        sc.run(f"mkdir -p {BK_REMOTE_DIR}", timeout=15, label=f"mkdir -p {BK_REMOTE_DIR}")
        sc.run(
            "noust backup destination test itlocal",
            timeout=60,
            label="noust backup destination test itlocal (folder created)",
        )

    schedule = (
        f"noust backup schedule create {BK_DOMAIN} --schedule daily --retention-count 1 "
        "--destination itlocal:2"
    )
    sc.run(schedule, timeout=60, label=schedule)
    for run in range(1, 4):
        sc.run(
            f"noust backup run-schedule {BK_DOMAIN}",
            timeout=300,
            label=f"noust backup run-schedule {BK_DOMAIN} (run {run} of 3)",
        )

    remote = sc.run(
        f"ls -1 {BK_REMOTE_DIR}/{BK_APP}",
        timeout=15,
        check=False,
        label=f"ls -1 {BK_REMOTE_DIR}/{BK_APP}",
    ).stdout.split()
    archives = sorted(name.removesuffix(".tar.gz") for name in remote if name.endswith(".tar.gz"))
    sidecars = sorted(name.removesuffix(".json") for name in remote if name.endswith(".json"))
    sc.check(
        len(archives) == 2 and archives == sidecars and len(remote) == 4,
        f"the destination should hold 2 archives and their sidecars: {remote!r}",
    )
    local = local_backup_ids(sc, "the local archives of bk-test")
    sc.check(
        len(local) == 1 and local[0] == archives[-1],
        f"local retention 1 should keep only the newest ({archives[-1]}): {local!r}",
    )

    listing = json_of(
        sc.run(
            f"noust backup remote-list itlocal --app {BK_APP} --json",
            timeout=60,
            label=f"noust backup remote-list itlocal --app {BK_APP} --json",
        ),
        "remote-list",
    )
    sc.check(remote_ids(listing) == archives, f"remote-list: {listing!r}")
    apps = json_of(
        sc.run(
            "noust backup remote-list itlocal --json",
            timeout=60,
            label="noust backup remote-list itlocal --json",
        ),
        "remote-list",
    )
    sc.check(BK_APP in apps.get("apps", []), f"remote-list without --app: {apps!r}")

    # The same listing from another working directory: an absolute path must
    # not depend on where the operator happens to stand.
    elsewhere = sc.run(
        f"cd /root && noust backup remote-list itlocal --app {BK_APP} --json",
        timeout=60,
        check=False,
        label=f"cd /root && noust backup remote-list itlocal --app {BK_APP} --json",
    )
    try:
        elsewhere_ids = remote_ids(json_of(elsewhere, "remote-list from /root"))
    except (AssertionError, json.JSONDecodeError):
        elsewhere_ids = []
    if elsewhere.returncode != 0 or elsewhere_ids != archives:
        findings.append(
            f"a `local` destination with path={BK_REMOTE_DIR} resolves relative to the working "
            "directory: run from /root, remote-list sees "
            f"{elsewhere_ids!r} instead of {archives!r} (managers/backup_destinations.py "
            'target(): `.strip("/")` drops the leading slash, so the rclone path becomes '
            f"itlocal:{BK_REMOTE_DIR.lstrip('/')}; a push from /root writes under "
            f"/root{BK_REMOTE_DIR})"
        )

    root = sc.run(
        f"readlink -e /var/www/apps/{BK_APP}/current || echo /var/www/apps/{BK_APP}",
        timeout=15,
        label="the served tree",
    ).stdout.strip()
    sc.run(
        f"echo tampered > {root}/index.html",
        timeout=15,
        label=f"echo tampered > {root}/index.html",
    )
    sc.check(curl_host(sc, BK_DOMAIN).strip() == "tampered", "the tampered page is not served")
    restore = f"noust backup restore {archives[-1]} --from itlocal -f"
    sc.run(restore, timeout=300, label=restore)
    page = curl_host(sc, BK_DOMAIN)
    sc.check(
        "Noust Integration Static Fixture" in page,
        f"restoring from the destination did not bring the page back: {page[:200]!r}",
    )


def _remote_backups_sftp(sc: Scenario, sftp: str, findings: list[str]) -> None:
    """Part (b): an SFTP destination; a push; the server stopped; the failure is reported."""
    have = docker_exec(sc.container, f"docker image inspect {SFTP_IMAGE}", check=False)
    if have.returncode != 0:
        pulled = docker_exec(sc.container, f"docker pull -q {SFTP_IMAGE}", timeout=300, check=False)
        if pulled.returncode != 0:
            sc.evidence.append(
                f"NOTE: {SFTP_IMAGE} is not cached and could not be pulled, so the SFTP part "
                f"of this scenario is SKIPPED:\n{pulled.stderr.strip()}"
            )
            return
    password = secrets.token_urlsafe(16)
    docker_exec(sc.container, f"docker rm -f {sftp}", timeout=60, check=False)
    sc.run(
        f"docker run -d --name {sftp} {SFTP_IMAGE} {SFTP_USER}:{password}:::upload",
        timeout=60,
        label=f"docker run -d --name {sftp} {SFTP_IMAGE} {SFTP_USER}:<password>:::upload",
    )
    address = sc.run(
        f"docker inspect -f '{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}} {{{{end}}}}' "
        f"{sftp}",
        timeout=30,
        label=f"docker inspect {sftp} (its address on the inner Docker's bridge)",
    ).stdout.split()[0]
    sc.run(
        f"for i in $(seq 1 60); do timeout 1 bash -c '</dev/tcp/{address}/22' && exit 0; "
        "sleep 0.5; done; exit 1",
        timeout=60,
        label=f"wait for sshd on {address}:22",
    )

    add_sftp = (
        f"printf '%s' '{password}' | noust backup destination add itsftp --type sftp "
        f"--field host={address} --field user={SFTP_USER} --field path=upload/noust --stdin"
    )
    add_label = add_sftp.replace(password, "<password>")
    # A destination on this machine's own networks is a decision (3.2): without it a destination
    # could reach the server's network from the console. Refused first, then decided.
    refused = sc.run(
        add_sftp,
        timeout=60,
        check=False,
        label=add_label + "  (before security.allow_private_hosts names it)",
    )
    sc.check(
        refused.returncode != 0 and "allow_private_hosts" in refused.stdout + refused.stderr,
        f"a destination inside a private network was not refused with the way to allow it: "
        f"{refused.stdout!r} {refused.stderr!r}",
    )
    sc.run(
        f"noust config set security.allow_private_hosts {address} --list",
        timeout=30,
        label=f"noust config set security.allow_private_hosts {address} --list",
    )
    sc.run(add_sftp, timeout=60, label=add_label)
    stored = sc.run(
        f"grep -rl '{password}' /var/lib/noust /root/.local/share/noust /etc/noust 2>/dev/null; "
        f"ps -eo args | grep -c '{password}' || true",
        timeout=30,
        check=False,
        label="where the password is stored in clear (the secret store only)",
    )
    sc.check(
        all("secrets" in line or line.strip().isdigit() for line in stored.stdout.splitlines()),
        f"the SFTP password is stored in clear outside the secret store: {stored.stdout!r}",
    )
    fresh = sc.run(
        "noust backup destination test itsftp",
        timeout=120,
        check=False,
        label="noust backup destination test itsftp (upload/noust does not exist yet)",
    )
    if fresh.returncode != 0:
        sc.run(
            f"docker exec {sftp} sh -c 'mkdir -p /home/{SFTP_USER}/upload/noust && "
            f"chown -R {SFTP_USER} /home/{SFTP_USER}/upload/noust'",
            timeout=30,
            label=f"docker exec {sftp} mkdir -p /home/{SFTP_USER}/upload/noust",
        )
        sc.run(
            "noust backup destination test itsftp",
            timeout=120,
            label="noust backup destination test itsftp (folder created)",
        )

    update = (
        f"noust backup schedule update {BK_DOMAIN} --schedule daily --retention-count 10 "
        "--destination itsftp:5"
    )
    sc.run(update, timeout=60, label=update)
    pushed = sc.run(
        f"noust backup run-schedule {BK_DOMAIN}",
        timeout=300,
        label=f"noust backup run-schedule {BK_DOMAIN} (to SFTP)",
    )
    match = re.search(rf"({BK_APP}_\d{{8}}_\d{{6}})", pushed.stdout + pushed.stderr)
    sc.check(match is not None, "run-schedule did not name the backup it took")
    backup_id = match.group(1) if match else ""
    # A destination is encrypted unless told otherwise (3.4): what lands on the server has
    # neither the name nor the body of the backup, and the listing below is how it is found.
    arrived = sc.run(
        f"docker exec {sftp} find /home/{SFTP_USER}/upload/noust -type f",
        timeout=30,
        check=False,
        label=f"docker exec {sftp} find /home/{SFTP_USER}/upload/noust -type f",
    )
    sc.check(
        len(arrived.stdout.split()) >= 2 and BK_APP not in arrived.stdout,
        f"{backup_id} did not arrive on the SFTP server, or arrived with its name in clear: "
        f"{arrived.stdout!r}",
    )
    listing = json_of(
        sc.run(
            f"noust backup remote-list itsftp --app {BK_APP} --json",
            timeout=120,
            label=f"noust backup remote-list itsftp --app {BK_APP} --json",
        ),
        "remote-list",
    )
    sc.check(backup_id in remote_ids(listing), f"remote-list itsftp: {listing!r}")

    # The server goes away: the run fails, keeps its local backup, and says so.
    sc.run(f"docker stop -t 1 {sftp}", timeout=60, label=f"docker stop {sftp}")
    start_notifications(sc, started=False)
    before = local_backup_ids(sc, "the local archives (before the run with SFTP down)")
    started = time.monotonic()
    failed = sc.run(
        f"noust backup run-schedule {BK_DOMAIN}",
        timeout=600,
        check=False,
        label=f"noust backup run-schedule {BK_DOMAIN} (SFTP server stopped)",
    )
    elapsed = time.monotonic() - started
    sc.evidence.append(f"[timing] the failing run took {elapsed:.1f}s")
    sc.check(failed.returncode != 0, "run-schedule succeeded with the destination down")
    if "dial tcp" not in failed.stdout + failed.stderr:
        findings.append(
            "`noust backup run-schedule` (what the timer runs, so also its journal) does not say "
            "why a destination failed: run_schedule() raises a summary BackupError once any "
            "destination fails (managers/backup_scheduler.py:745-749, 'see the notification for "
            "rclone's own error'), so the CLI's per-destination 'Failed to send to' lines "
            "(cli/commands/backup.py _run_schedule) never run; without a notification channel "
            "rclone's error is not shown anywhere"
        )
    after = local_backup_ids(sc, "the local archives (after the run with SFTP down)")
    sc.check(
        len(after) == len(before) + 1 and set(before) < set(after),
        f"the local backup was not kept: before {before!r}, after {after!r}",
    )
    events = [
        event
        for event in recorded_notifications(sc, "the notifications the recorder received")
        if event.get("event") == "backup_failed" and event.get("domain") == BK_DOMAIN
    ]
    sc.check(
        # 3.1: the title names the application; the destination is one of the facts.
        len(events) == 1
        and any(fact.get("value") == "itsftp" for fact in events[0].get("facts") or []),
        f"expected one backup_failed notification naming itsftp: {events!r}",
    )
    if elapsed > 120:
        findings.append(
            f"a scheduled run with its SFTP destination unreachable took {elapsed:.0f}s to fail"
        )


# -- Pull request previews ---------------------------------------------------------

PREV_DOMAIN = "prev.test"
PREV_APP = "prev-test"
PREV_REPOSITORY = "acme/prev-app"
PREV_BASE = "previews.test"
PREVIEW_DOMAIN = f"pr-1-{PREV_APP}.{PREV_BASE}"
PREVIEW_APP = f"pr-1-{PREV_APP}-previews-test"


def wait_for_console(sc: Scenario) -> None:
    """Start the console and wait for /health."""
    sc.run("noust web start --daemon", timeout=60, label="noust web start --daemon")
    deadline = time.time() + 30
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "200":
            return
        time.sleep(1)
    raise AssertionError("the console did not answer /health within 30s")


def api(sc: Scenario, token: str, method: str, path: str, label: str) -> tuple[int, Any]:
    """Call the console's API with a Bearer token; the token never reaches the evidence."""
    proc = docker_exec(
        sc.container,
        f"curl -sS -X {method} -H 'Authorization: Bearer {token}' "
        f"-w '\\n%{{http_code}}' {PANEL_URL}{path}",
        timeout=30,
        check=False,
    )
    body, _, code = proc.stdout.rpartition("\n")
    try:
        payload: Any = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        payload = body
    sc.evidence.append(f"$ {label}\n{code} {str(payload)[:4000]}")
    return int(code or 0), payload


def deliver(sc: Scenario, secret: str, event: str, payload: dict[str, Any]) -> tuple[int, Any]:
    """POST a GitHub-signed delivery to the parent's webhook."""
    body = json.dumps(payload, separators=(",", ":"))
    sc.check("'" not in body, "a delivery body cannot carry a single quote through printf")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    proc = sc.run(
        f"printf '%s' '{body}' > /root/it-delivery.json && "
        "curl -sS -o /root/it-delivery.out -w '%{http_code}' -X POST "
        f"-H 'Content-Type: application/json' -H 'X-GitHub-Event: {event}' "
        f"-H 'X-GitHub-Delivery: {secrets.token_hex(8)}' "
        f"-H 'X-Hub-Signature-256: sha256={signature}' "
        f"--data-binary @/root/it-delivery.json {PANEL_URL}/hooks/deploy/{PREV_DOMAIN}; "
        "echo; cat /root/it-delivery.out",
        timeout=60,
        label=f"POST /hooks/deploy/{PREV_DOMAIN} (X-GitHub-Event: {event}, signed): {body}",
    )
    code, _, text = proc.stdout.partition("\n")
    try:
        return int(code), json.loads(text)
    except json.JSONDecodeError:
        return int(code), text


def pull_request(
    action: str,
    number: int,
    branch: str,
    sha: str,
    *,
    author: str = "it-owner",
    author_type: str = "User",
    association: str = "OWNER",
) -> dict[str, Any]:
    """
    A GitHub ``pull_request`` delivery for a branch of the preview repository.

    The author fields are GitHub's: previews build only for the repository's
    owners, members and collaborators, and never for a bot unless allowed.
    """
    repository = {
        "full_name": PREV_REPOSITORY,
        "clone_url": f"git://127.0.0.1/{PREV_REPOSITORY}",
    }
    person = {"login": author, "type": author_type}
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "title": f"Integration pull request {number}",
            "user": person,
            "author_association": association,
            "head": {"ref": branch, "sha": sha, "repo": repository},
            "base": {"ref": "main", "repo": repository},
        },
        "repository": repository,
        "sender": person,
    }


def wait_for_jobs(sc: Scenario, token: str, job_ids: list[str], what: str) -> None:
    """Wait for console jobs to finish; fail with their log when one did not complete."""
    for job_id in job_ids:
        deadline = time.time() + DEPLOY_TIMEOUT
        job: Any = None
        while time.time() < deadline:
            proc = docker_exec(
                sc.container,
                f"curl -sS -H 'Authorization: Bearer {token}' {PANEL_URL}/api/jobs/{job_id}",
                timeout=30,
                check=False,
            )
            try:
                job = json.loads(proc.stdout)
            except json.JSONDecodeError:
                job = {"status": "unreadable", "raw": proc.stdout}
            if job.get("status") in ("completed", "failed", "cancelled"):
                break
            time.sleep(2)
        logs = "\n".join(
            f"  [{entry.get('level')}] {entry.get('message')}" for entry in job.get("logs", [])
        )
        sc.evidence.append(
            f"$ GET /api/jobs/{job_id} ({what})\nstatus={job.get('status')} "
            f"error={job.get('error')!r}\n{logs}"
        )
        if job.get("status") != "completed":
            api(sc, token, "GET", f"/api/jobs/{job_id}/log", f"GET /api/jobs/{job_id}/log")
        sc.check(job.get("status") == "completed", f"{what}: job {job_id} ended {job!r:.300}")


def parent_state(sc: Scenario, label: str) -> tuple[str, str]:
    """The parent's history row count and active release."""
    count = sc.run(
        store_query("SELECT COUNT(*) FROM deployments WHERE domain = 'prev.test'"),
        timeout=15,
        label=label + ": count the deployments of prev.test",
    ).stdout.strip()
    current = sc.run(
        f"readlink /var/www/apps/{PREV_APP}/current",
        timeout=15,
        label=f"{label}: readlink current of {PREV_DOMAIN}",
    ).stdout.strip()
    return count, current


@scenario("pr_preview_webhook")
def scenario_pr_preview(sc: Scenario) -> None:
    """
    A signed pull_request delivery builds a preview; synchronize updates it; closed removes it.

    The parent is on releases, deployed from git daemon; previews are turned
    on under previews.test. Deliveries are signed with the parent's webhook
    secret, minted through the API with an admin token. The preview answers
    through nginx by Host header. Certificates cannot be issued here (no
    certbot, no public DNS), so the scenario also records what a preview does
    when its certificate fails. A ping and the pull request deliveries never
    update the parent.
    """
    repo, url = make_node_repo(sc, PREV_REPOSITORY)
    create = f"noust create -d {PREV_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    findings: list[str] = []
    try:
        _pr_preview(sc, repo, findings)
    except AssertionError:
        journal_tail(sc, "-u 'noust*' -u 'pr-*'", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust web stop; noust preview disable {PREV_DOMAIN} -y; "
            f"noust delete {PREVIEW_DOMAIN} -f 2>/dev/null; noust delete {PREV_DOMAIN} -f",
            timeout=300,
            check=False,
            label="cleanup",
        )
    sc.check(not findings, "product findings:\n- " + "\n- ".join(findings))


def _pr_preview(sc: Scenario, repo: str, findings: list[str]) -> None:
    """The body of :func:`scenario_pr_preview`."""
    enable = f"noust preview enable {PREV_DOMAIN} --domain {PREV_BASE} --max 2 --ttl 1h"
    sc.run(enable, timeout=60, label=enable)

    issued = docker_exec(
        sc.container,
        f"noust token create it-previews-{secrets.token_hex(4)} --scope admin",
        timeout=30,
    )
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "noust token create printed no token")
    token = token_match.group(1) if token_match else ""
    sc.evidence.append("$ noust token create it-previews-... --scope admin\n(token issued)")

    wait_for_console(sc)
    code, minted = api(
        sc, token, "POST", f"/api/apps/{PREV_DOMAIN}/webhook-secret", "POST webhook-secret"
    )
    sc.check(code == 200 and isinstance(minted, dict), f"minting the secret answered {code}")
    secret = str(minted["secret"])
    sc.evidence[-1] = sc.evidence[-1].replace(secret, "<secret>")

    parent_before = parent_state(sc, "before the deliveries")

    code, answer = deliver(sc, secret, "ping", {"zen": "Keep it logically awesome.", "hook_id": 1})
    sc.check(code == 200 and answer.get("event") == "ping", f"ping answered {code} {answer!r}")
    wrong = deliver(sc, "not-the-secret", "ping", {"zen": "forged"})
    sc.check(wrong[0] == 401, f"a delivery signed with the wrong secret answered {wrong!r}")

    sha = commit_to(
        sc,
        repo,
        "git checkout -q -b feature-1 && echo pr1 > VERSION",
        "pull request 1",
    )
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label="(fixture repo) back to main")

    # A bot's pull request (Dependabot, say) builds nothing unless the settings allow bots:
    # its code would run as root with the application's secrets.
    code, answer = deliver(
        sc,
        secret,
        "pull_request",
        pull_request(
            "opened",
            9,
            "feature-1",
            sha,
            author="dependabot[bot]",
            author_type="Bot",
            association="NONE",
        ),
    )
    sc.check(
        code == 200 and isinstance(answer, dict) and answer.get("status") == "ignored",
        f"a bot's pull request was refused: {code} {answer}",
    )
    code, answer = deliver(sc, secret, "pull_request", pull_request("opened", 1, "feature-1", sha))
    sc.check(code == 202 and answer.get("job_ids"), f"opened answered {code} {answer!r}")
    wait_for_jobs(sc, token, answer["job_ids"], "the preview's first deploy")

    listed = json_of(
        sc.run(
            f"noust preview list {PREV_DOMAIN} --json",
            timeout=30,
            label=f"noust preview list {PREV_DOMAIN} --json",
        ),
        "preview list",
    )
    items = listed.get("items", [])
    sc.check(
        len(items) == 1
        and items[0]["domain"] == PREVIEW_DOMAIN
        and items[0]["status"] == "ready"
        and items[0]["number"] == 1,
        f"preview list: {items!r}",
    )
    row = sc.run(
        store_query(
            "SELECT layout || '|' || ssl_enabled || '|' || COALESCE(preview_parent, '') "
            "FROM apps WHERE domain = 'pr-1-prev-test.previews.test'"
        ),
        timeout=15,
        label="SELECT layout, ssl_enabled, preview_parent of the preview",
    ).stdout.strip()
    sc.check(
        row.startswith("releases|") and row.endswith(f"|{PREV_DOMAIN}"),
        f"the preview's row: {row!r}",
    )
    served = curl_host(sc, PREVIEW_DOMAIN).strip()
    sc.check(served == "ok pr1", f"the preview should serve 'ok pr1', got {served!r}")
    log_path = sc.run(
        store_query(
            "SELECT log_path FROM deployments "
            "WHERE domain = 'pr-1-prev-test.previews.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the preview's deploy log",
    ).stdout.strip()
    sc.run(
        f"grep -iE 'ssl|certif' {log_path}",
        timeout=15,
        check=False,
        label="what the preview's deploy log says about its certificate",
    )
    tls = sc.run(
        f"grep -cE 'listen 443|ssl_certificate' /etc/nginx/sites-available/{PREVIEW_DOMAIN}",
        timeout=15,
        check=False,
        label="TLS lines in the preview's site",
    )
    sc.evidence.append(
        "NOTE (certificates): a preview is always created with ssl=True "
        "(managers/previews.py _deploy). Here certbot is absent and nothing is publicly "
        f"resolvable; the store says ssl_enabled={row.split('|')[1]!r} and the log lines above "
        "show what the deploy did about it."
    )
    sc.check(
        tls.stdout.strip().endswith("0"),
        "the preview's site has TLS server blocks without a certificate",
    )
    advertised = str(items[0].get("url"))
    if row.split("|")[1] == "0" and advertised.startswith("https://"):
        findings.append(
            f"the preview is served over HTTP only (its certificate failed, ssl_enabled=0) but "
            f"`noust preview list`, the API and the pull request comment advertise {advertised} "
            "(managers/previews.py preview_url() always answers https://)"
        )

    sc.check(
        parent_state(sc, "after ping and opened") == parent_before,
        "a ping or pull_request delivery updated the parent",
    )

    sha2 = commit_to(
        sc,
        repo,
        "git checkout -q feature-1 && echo pr1b > VERSION",
        "pull request 1, second push",
    )
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label="(fixture repo) back to main")
    code, answer = deliver(
        sc, secret, "pull_request", pull_request("synchronize", 1, "feature-1", sha2)
    )
    sc.check(code == 202 and answer.get("job_ids"), f"synchronize answered {code} {answer!r}")
    wait_for_jobs(sc, token, answer["job_ids"], "the preview's update")
    served = curl_host(sc, PREVIEW_DOMAIN).strip()
    sc.check(served == "ok pr1b", f"the updated preview should serve 'ok pr1b', got {served!r}")
    releases = sc.run(
        f"ls /var/www/apps/{PREVIEW_APP}/releases | wc -l",
        timeout=15,
        label="ls releases of the preview | wc -l",
    ).stdout.strip()
    sc.check(releases == "2", f"the update did not build a second release: {releases}")

    code, answer = deliver(sc, secret, "pull_request", pull_request("closed", 1, "feature-1", sha2))
    sc.check(code == 202 and answer.get("job_ids"), f"closed answered {code} {answer!r}")
    wait_for_jobs(sc, token, answer["job_ids"], "the preview's removal")
    rows = sc.run(
        store_query("SELECT COUNT(*) FROM apps WHERE domain = 'pr-1-prev-test.previews.test'"),
        timeout=15,
        label="the preview's row (after closed)",
    ).stdout.strip()
    gone = sc.run(
        f"echo {rows}; test -e /etc/systemd/system/{PREVIEW_APP}.service && echo unit-present; "
        f"test -e /etc/nginx/sites-available/{PREVIEW_DOMAIN} && echo site-present; "
        f"test -e /etc/nginx/sites-enabled/{PREVIEW_DOMAIN} && echo site-enabled; "
        f"test -e /var/www/apps/{PREVIEW_APP} && echo tree-present; "
        f"systemctl is-active {PREVIEW_APP} || true",
        timeout=15,
        check=False,
        label="the preview's row, unit, site and tree (after closed)",
    )
    sc.check(
        gone.stdout.split() in (["0", "inactive"], ["0", "unknown"]),
        f"the preview was not removed completely: {gone.stdout!r}",
    )
    listed = json_of(
        sc.run(
            f"noust preview list {PREV_DOMAIN} --json",
            timeout=30,
            label=f"noust preview list {PREV_DOMAIN} --json (after closed)",
        ),
        "preview list",
    )
    sc.check(listed.get("items") == [], f"the preview record is still listed: {listed!r}")

    sc.check(
        parent_state(sc, "after every delivery") == parent_before,
        "a pull_request delivery updated the parent",
    )
    served = curl_host(sc, PREV_DOMAIN).strip()
    sc.check(served == "ok 1", f"the parent should still serve 'ok 1', got {served!r}")


# -- Deploy notifications --------------------------------------------------------

NTF_DOMAIN = "ntf.test"


@scenario("deploy_notifications")
def scenario_deploy_notifications(sc: Scenario) -> None:
    """
    A deploy from the CLI notifies: started and success, then rolled back with the gate's cause.

    The webhook channel points at a recorder inside the container. With
    deploy_started on, ``noust update`` sends started then success for the
    application; an update to a commit whose server throws sends started then
    rolled back, the process's own error in the body.
    """
    repo, url = make_node_repo(sc, "ntf-app")
    create = f"noust create -d {NTF_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        start_notifications(sc, started=True)

        commit = commit_to(sc, repo, "echo 2 > VERSION", "version 2")
        sc.run(
            f"noust update {NTF_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {NTF_DOMAIN}"
        )
        events = [
            event
            for event in recorded_notifications(sc, "the notifications the recorder received")
            if event.get("domain") == NTF_DOMAIN
        ]
        kinds = [event.get("event") for event in events]
        sc.check(
            kinds == ["deploy_started", "deploy_success"],
            f"expected started then success for {NTF_DOMAIN}, got {kinds!r}",
        )
        sc.check(
            commit[:7] in str(events[1].get("title")) + str(events[1].get("body")),
            f"the success notification does not name the commit {commit[:7]}: {events[1]!r}",
        )

        sc.run(f": > {NOTIFY_LOG}", timeout=15, label="empty the recorder's log")
        commit_to(
            sc,
            repo,
            "echo 3 > VERSION && sed -i '1i throw new Error(\"broken on purpose\");' server.js",
            "break the server",
        )
        broken = sc.run(
            f"noust update {NTF_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {NTF_DOMAIN} (a commit whose server throws at start)",
        )
        sc.check(broken.returncode != 0, "the broken update reported success")
        events = [
            event
            for event in recorded_notifications(sc, "the notifications the recorder received")
            if event.get("domain") == NTF_DOMAIN
        ]
        kinds = [event.get("event") for event in events]
        sc.check(
            kinds == ["deploy_started", "deploy_rolled_back"],
            f"expected started then rolled back for {NTF_DOMAIN}, got {kinds!r}",
        )
        sc.check(
            "broken on purpose" in str(events[1].get("body")),
            f"the rolled-back notification does not carry the gate's cause: {events[1]!r}",
        )
    finally:
        stop_notifications(sc)
        sc.run(f"noust delete {NTF_DOMAIN} -f", timeout=180, check=False, label="cleanup")


# -- 2.3: recipes, export and import -------------------------------------------------

WP_DOMAIN = "wp.test"
WP_ROOT = "/var/www/apps/wp-test"
WP_TITLE = "Noust Integration Blog"

#: A recipe builds from what it downloads; a whole npm install and a Vite build
#: for Uptime Kuma take minutes on a cold cache.
RECIPE_TIMEOUT = 1500

KUMA_DOMAIN = "kuma.test"
KUMA_ROOT = "/var/www/apps/kuma-test"

N8N_DOMAIN = "n8n.test"
N8N_ROOT = "/var/www/apps/n8n-test"

UMAMI_DOMAIN = "umami.test"
UMAMI_ROOT = "/var/www/apps/umami-test"

EXPORT_DOMAIN = "exp.test"
EXPORT_SECRET = "it-export-secret-value-0123456789"


def http_status(sc: Scenario, host: str, path: str, label: str) -> tuple[str, str]:
    """Return the status code and the Location of one request through nginx."""
    out = sc.run(
        f"curl -sS -o /dev/null -w '%{{http_code}} %{{redirect_url}}' -H 'Host: {host}' "
        f"'http://127.0.0.1{path}'",
        timeout=60,
        check=False,
        label=label,
    ).stdout.strip()
    code, _, location = out.partition(" ")
    return code, location


def require_network(sc: Scenario, url: str, what: str) -> None:
    """Skip the scenario, saying why, when the container cannot reach ``url``."""
    probe = sc.run(
        f"curl -sSI --max-time 20 '{url}'",
        timeout=40,
        check=False,
        label=f"curl -sI {url} (is the network there?)",
    )
    if probe.returncode != 0:
        raise ScenarioSkipped(
            f"{url} is unreachable from the container, so {what} cannot be downloaded: "
            f"{(probe.stderr or probe.stdout).strip()}"
        )


def fpm_unit(sc: Scenario) -> str:
    """Name the PHP-FPM unit of the image (php8.3-fpm on Ubuntu 24.04)."""
    return sc.run(
        "basename /usr/lib/systemd/system/php*-fpm.service .service",
        timeout=15,
        label="the PHP-FPM unit",
    ).stdout.strip()


@scenario("recipe_wordpress")
def scenario_recipe_wordpress(sc: Scenario) -> None:
    """
    WordPress from its recipe: PHP-FPM pool, MariaDB, the installer, updates and removal.

    Real php-fpm and MariaDB behind real nginx. The pool carries the
    database settings as env[] lines and is readable by root only; the
    installer runs through nginx; what nginx must refuse is refused, a PHP
    file planted among the uploads included; a plugin installed into shared/
    survives an update to a new release and a rollback; deleting the site
    removes its pool and leaves FPM running for everyone else.
    """
    require_network(sc, "https://wordpress.org/latest.tar.gz.sha1", "WordPress")
    unit = fpm_unit(sc)
    create = f"noust create --recipe wordpress -d {WP_DOMAIN} --no-ssl"
    sc.run(create, timeout=RECIPE_TIMEOUT, label=create)
    try:
        _recipe_wordpress(sc, unit)
    finally:
        journal_tail(sc, f"-u {unit}", f"journalctl -u {unit} (tail)")
        sc.run(f"noust delete {WP_DOMAIN} --force", timeout=180, check=False, label="cleanup")


def _recipe_wordpress(sc: Scenario, unit: str) -> None:
    version = unit.removeprefix("php").removesuffix("-fpm")
    pool = f"/etc/php/{version}/fpm/pool.d/noust-wp-test.conf"
    stat = sc.run(f"stat -c '%a %U' {pool}", timeout=15, label=f"stat {pool}")
    sc.check(stat.stdout.split() == ["640", "root"], f"pool file mode/owner: {stat.stdout!r}")
    env_line = sc.run(
        f"grep -c '^env\\[WORDPRESS_DB_NAME\\]' {pool}",
        timeout=15,
        check=False,
        label=f"grep env[WORDPRESS_DB_NAME] {pool}",
    )
    sc.check(env_line.stdout.strip() == "1", "the pool does not carry env[WORDPRESS_DB_NAME]")

    code, location = http_status(sc, WP_DOMAIN, "/", f"curl -H 'Host: {WP_DOMAIN}' /")
    sc.check(
        code == "302" and location.endswith("/wp-admin/install.php"),
        f"/ before the installation answered {code} {location!r}, not a 302 to install.php",
    )
    code, _ = http_status(
        sc, WP_DOMAIN, "/wp-admin/install.php", "curl -H 'Host: wp.test' /wp-admin/install.php"
    )
    sc.check(code == "200", f"/wp-admin/install.php answered {code}")

    install = sc.run(
        "curl -sS -o /dev/null -w '%{http_code}' -H 'Host: wp.test' "
        f"--data-urlencode 'weblog_title={WP_TITLE}' --data-urlencode 'user_name=itadmin' "
        "--data-urlencode 'admin_password=it-Admin-Passw0rd' "
        "--data-urlencode 'admin_password2=it-Admin-Passw0rd' --data-urlencode 'pw_weak=1' "
        "--data-urlencode 'admin_email=it@example.com' --data-urlencode 'blog_public=0' "
        "'http://127.0.0.1/wp-admin/install.php?step=2'",
        timeout=120,
        label="POST the installation form to /wp-admin/install.php?step=2",
    )
    sc.check(install.stdout.strip() == "200", f"the installation answered {install.stdout!r}")
    home = sc.run(
        "curl -sS -w '\\n%{http_code}' -H 'Host: wp.test' http://127.0.0.1/",
        timeout=60,
        label="curl -H 'Host: wp.test' / (installed)",
    ).stdout
    sc.check(
        home.rstrip().endswith("200") and WP_TITLE in home,
        f"the installed site does not answer 200 with its title: {home[-300:]!r}",
    )

    sc.run(
        f"mkdir -p {WP_ROOT}/shared/wp-content/uploads && "
        f"echo '<?php echo \"planted\";' > {WP_ROOT}/shared/wp-content/uploads/x.php && "
        f"chown www-data: {WP_ROOT}/shared/wp-content/uploads/x.php",
        timeout=15,
        label="plant wp-content/uploads/x.php",
    )
    for path in ("/wp-config.php", "/.env", "/readme.html", "/wp-content/uploads/x.php"):
        code, _ = http_status(sc, WP_DOMAIN, path, f"curl -H 'Host: wp.test' {path}")
        sc.check(code == "403", f"{path} answered {code}, not 403")

    linked = sc.run(
        store_query(
            "SELECT d.engine || ':' || d.name FROM databases d JOIN apps a ON a.id = d.app_id "
            "WHERE a.domain = 'wp.test'"
        ),
        timeout=15,
        label="the databases the store links to wp.test",
    )
    sc.check(linked.stdout.strip().startswith("mysql:"), f"no linked database: {linked.stdout!r}")

    plugin = f"{WP_ROOT}/shared/wp-content/plugins/it-plugin/it-plugin.php"
    sc.run(
        f"mkdir -p $(dirname {plugin}) && "
        f"printf '<?php\\n/* Plugin Name: IT plugin */\\n' > {plugin} && "
        f"chown -R www-data: $(dirname {plugin})",
        timeout=15,
        label="plant a plugin in shared/wp-content/plugins",
    )
    before = sc.run(f"readlink {WP_ROOT}/current", timeout=15, label="readlink current").stdout
    sc.run(f"noust update {WP_DOMAIN}", timeout=RECIPE_TIMEOUT, label=f"noust update {WP_DOMAIN}")
    after = sc.run(
        f"readlink {WP_ROOT}/current", timeout=15, label="readlink current (after update)"
    ).stdout
    sc.check(
        after.strip() != before.strip(), f"the update did not activate a new release: {after!r}"
    )
    survived = sc.run(
        f"test -f {WP_ROOT}/current/wp-content/plugins/it-plugin/it-plugin.php && echo present",
        timeout=15,
        check=False,
        label="the plugin, through the new release",
    )
    sc.check(survived.stdout.strip() == "present", "the plugin did not survive the update")
    code, _ = http_status(sc, WP_DOMAIN, "/", "curl -H 'Host: wp.test' / (after update)")
    sc.check(code == "200", f"/ answered {code} after the update")

    sc.run(
        f"noust releases rollback {WP_DOMAIN}",
        timeout=300,
        label=f"noust releases rollback {WP_DOMAIN}",
    )
    rolled = sc.run(
        f"readlink {WP_ROOT}/current", timeout=15, label="readlink current (after rollback)"
    ).stdout
    sc.check(rolled.strip() == before.strip(), f"rollback activated {rolled!r}, not {before!r}")
    code, _ = http_status(sc, WP_DOMAIN, "/", "curl -H 'Host: wp.test' / (after rollback)")
    sc.check(code == "200", f"/ answered {code} after the rollback")

    sc.run(f"noust delete {WP_DOMAIN} --force", timeout=180, label=f"noust delete {WP_DOMAIN}")
    gone = sc.run(f"test -e {pool} || echo gone", timeout=15, check=False, label=f"test -e {pool}")
    sc.check(gone.stdout.strip() == "gone", f"{pool} is still there after the delete")
    active = sc.run(
        f"systemctl is-active {unit}", timeout=15, check=False, label=f"systemctl is-active {unit}"
    )
    sc.check(active.stdout.strip() == "active", f"{unit} is {active.stdout.strip()!r}")


@scenario("recipe_uptime_kuma")
def scenario_recipe_uptime_kuma(sc: Scenario) -> None:
    """
    Uptime Kuma from its recipe: built from GitHub, on loopback only, its data in shared/.

    Its Socket.IO endpoint answers through nginx, it listens on 127.0.0.1
    and nothing else, its SQLite database is in shared/data, and an update to
    a new release keeps that database.
    """
    require_network(sc, "https://github.com/louislam/uptime-kuma.git", "Uptime Kuma")
    create = f"noust create --recipe uptime-kuma -d {KUMA_DOMAIN} --no-ssl"
    sc.run(create, timeout=RECIPE_TIMEOUT, label=create)
    try:
        code, location = http_status(sc, KUMA_DOMAIN, "/", f"curl -H 'Host: {KUMA_DOMAIN}' /")
        sc.check(code in ("301", "302"), f"/ answered {code} {location!r}, not a redirect")
        polling = curl_host(sc, KUMA_DOMAIN, "/socket.io/?EIO=4&transport=polling")
        sc.check(polling.startswith("0{"), f"Socket.IO polling answered {polling[:200]!r}")

        # Whatever it listens on, on loopback only. The port the store records is the first
        # instance's; an application that updates without downtime (the default on releases)
        # is served by the other one, so the listening sockets are read from the processes of
        # the application's own units.
        listeners = listening_addresses(sc, "kuma-test")
        sc.check(
            bool(listeners) and all(address.startswith("127.0.0.1:") for address in listeners),
            f"the application listens on {listeners!r}, not on loopback only",
        )

        db = f"{KUMA_ROOT}/shared/data/kuma.db"
        sc.run(f"test -f {db} && ls -l {db}", timeout=15, label=f"test -f {db}")
        sum_before = sc.run(f"stat -c %i {db}", timeout=15, label=f"inode of {db}").stdout
        sc.run(
            f"noust update {KUMA_DOMAIN}",
            timeout=RECIPE_TIMEOUT,
            label=f"noust update {KUMA_DOMAIN}",
        )
        sum_after = sc.run(
            f"stat -c %i {db}", timeout=15, label=f"inode of {db} (after update)"
        ).stdout
        sc.check(
            sum_before.strip() == sum_after.strip(),
            f"kuma.db was replaced by the update: {sum_before!r} -> {sum_after!r}",
        )
        code, _ = http_status(sc, KUMA_DOMAIN, "/", f"curl -H 'Host: {KUMA_DOMAIN}' / (after)")
        sc.check(code in ("301", "302"), f"/ answered {code} after the update")
    finally:
        journal_tail(sc, "-u kuma-test", "journalctl -u kuma-test (tail)")
        sc.run(f"noust delete {KUMA_DOMAIN} --force", timeout=180, check=False, label="cleanup")


def listening_addresses(sc: Scenario, unit: str) -> list[str]:
    """The addresses the processes of an application's units listen on (the unit and its instances)."""
    script = (
        f"for u in {unit} {unit}@blue {unit}@green; do "
        "cg=$(systemctl show -p ControlGroup --value $u 2>/dev/null); "
        '[ -n "$cg" ] && cat /sys/fs/cgroup$cg/cgroup.procs 2>/dev/null; done | sort -u '
        "| sed 's/^/pid=/;s/$/,/' > /tmp/it-unit-pids; "
        "ss -ltnpH | grep -F -f /tmp/it-unit-pids | awk '{print $4}' | sort -u"
    )
    return sc.run(
        script, timeout=30, check=False, label=f"the sockets {unit}'s processes listen on"
    ).stdout.split()


def env_value(sc: Scenario, domain: str, name: str, label: str) -> str:
    """Read one variable of an application, secrets in clear, from ``noust env show``."""
    out = sc.run(
        f"noust env show {domain} --unmask --json", timeout=30, check=False, label=label
    ).stdout
    try:
        return str(json.loads(out)["variables"].get(name, ""))
    except (ValueError, KeyError):
        return ""


@scenario("recipe_n8n")
def scenario_recipe_n8n(sc: Scenario) -> None:
    """
    n8n from its recipe: a pinned npm package, on loopback only, its data in shared/.

    The health check answers through nginx, the process listens on 127.0.0.1
    and nothing else, the encryption key it generated is in the application's
    environment and survives an update, and so does the folder n8n keeps its
    SQLite database in.
    """
    require_network(sc, "https://registry.npmjs.org/n8n", "n8n")
    create = f"noust recipe install n8n -d {N8N_DOMAIN} --no-ssl --no-backups"
    sc.run(create, timeout=RECIPE_TIMEOUT, label=create)
    try:
        code, _ = http_status(sc, N8N_DOMAIN, "/healthz", f"curl -H 'Host: {N8N_DOMAIN}' /healthz")
        sc.check(code == "200", f"/healthz answered {code}")
        port = sc.run(
            store_query("SELECT port FROM apps WHERE domain = 'n8n.test'"),
            timeout=15,
            label=f"the port the store records for {N8N_DOMAIN}",
        ).stdout.strip()
        listeners = sc.run(
            f"ss -ltnH '( sport = :{port} )' | awk '{{print $4}}'",
            timeout=15,
            label=f"ss -ltn sport = :{port}",
        ).stdout.split()
        sc.check(listeners == [f"127.0.0.1:{port}"], f"port {port} is bound on {listeners!r}")

        key = env_value(sc, N8N_DOMAIN, "N8N_ENCRYPTION_KEY", "the encryption key n8n was given")
        sc.check(len(key) >= 32, "n8n was not given an encryption key of its own")
        sc.run(
            f"test -d {N8N_ROOT}/shared/data", timeout=15, label=f"test -d {N8N_ROOT}/shared/data"
        )
        sc.run(
            f"noust update {N8N_DOMAIN}",
            timeout=RECIPE_TIMEOUT,
            label=f"noust update {N8N_DOMAIN}",
        )
        after = env_value(
            sc, N8N_DOMAIN, "N8N_ENCRYPTION_KEY", "the encryption key after the update"
        )
        sc.check(after == key, "the update changed n8n's encryption key")
        code, _ = http_status(
            sc, N8N_DOMAIN, "/healthz", f"curl -H 'Host: {N8N_DOMAIN}' /healthz (after)"
        )
        sc.check(code == "200", f"/healthz answered {code} after the update")
    finally:
        journal_tail(sc, "-u n8n-test", "journalctl -u n8n-test (tail)")
        sc.run(f"noust delete {N8N_DOMAIN} --force", timeout=180, check=False, label="cleanup")


@scenario("recipe_umami")
def scenario_recipe_umami(sc: Scenario) -> None:
    """
    Umami from its recipe: built at a release tag with a PostgreSQL database Noust provisions.

    The engine is installed by the recipe's own install (``--install-requirements``),
    the build applies Umami's migrations to the database it was given, the
    heartbeat answers through nginx, the database has a daily verified backup
    policy from the first minute, and an update keeps the application's secret.
    """
    require_network(sc, "https://github.com/umami-software/umami.git", "Umami")
    create = f"noust recipe install umami -d {UMAMI_DOMAIN} --no-ssl --install-requirements"
    sc.run(create, timeout=RECIPE_TIMEOUT * 2, label=create)
    try:
        code, _ = http_status(
            sc, UMAMI_DOMAIN, "/api/heartbeat", f"curl -H 'Host: {UMAMI_DOMAIN}' /api/heartbeat"
        )
        sc.check(code == "200", f"/api/heartbeat answered {code}")
        databases = sc.run(
            store_query(
                "SELECT d.engine || '/' || d.name FROM databases d JOIN apps a "
                "ON a.id = d.app_id WHERE a.domain = 'umami.test'"
            ),
            timeout=15,
            label="the database recorded for umami.test",
        ).stdout.split()
        sc.check(
            len(databases) == 1 and databases[0].startswith("postgresql/"),
            f"umami.test has {databases!r}, not one PostgreSQL database",
        )
        policy = sc.run(
            f"noust db backup-schedule show {databases[0].split('/', 1)[1]} -e postgresql --json",
            timeout=60,
            check=False,
            label="the backup policy Umami's database started with",
        )
        sc.check(
            policy.returncode == 0 and '"verify_restore": true' in policy.stdout,
            "Umami's database was not given a daily verified backup policy",
        )
        secret = env_value(sc, UMAMI_DOMAIN, "APP_SECRET", "Umami's APP_SECRET")
        sc.run(
            f"noust update {UMAMI_DOMAIN}",
            timeout=RECIPE_TIMEOUT,
            label=f"noust update {UMAMI_DOMAIN}",
        )
        after = env_value(sc, UMAMI_DOMAIN, "APP_SECRET", "Umami's APP_SECRET after the update")
        sc.check(secret != "" and after == secret, "the update changed Umami's APP_SECRET")
        code, _ = http_status(
            sc, UMAMI_DOMAIN, "/api/heartbeat", f"curl -H 'Host: {UMAMI_DOMAIN}' (after)"
        )
        sc.check(code == "200", f"/api/heartbeat answered {code} after the update")
    finally:
        journal_tail(sc, "-u umami-test", "journalctl -u umami-test (tail)")
        sc.run(f"noust delete {UMAMI_DOMAIN} --force", timeout=180, check=False, label="cleanup")


@scenario("app_export_import")
def scenario_app_export_import(sc: Scenario) -> None:
    """
    An application exported and imported under another domain comes back whole.

    The export without secrets leaves the secret's value out, and the import
    needs it given; aliases and cron job names follow the new domain; the
    health check and the retention are applied; what could not be applied
    (the port, which the original still holds) is listed. An export with
    secrets imports with nothing else given.
    """
    _, url = make_node_repo(sc, "exp-app")
    create = (
        f"noust create -d {EXPORT_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases "
        f"--env APP_SECRET={EXPORT_SECRET} --env GREETING=hello"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        _export_import(sc)
    finally:
        for domain in (EXPORT_DOMAIN, "copy.test", "copy2.test"):
            sc.run(f"noust delete {domain} --force", timeout=180, check=False, label="cleanup")
        for job in ("exp-test-nightly", "copy-test-nightly", "copy2-test-nightly"):
            sc.run(f"noust cron delete {job} --force", timeout=60, check=False, label="cleanup")


def _export_import(sc: Scenario) -> None:
    for script in (
        f"noust domain add {EXPORT_DOMAIN} alias.{EXPORT_DOMAIN} --kind alias",
        f"noust cron create exp-test-nightly '/bin/true' --schedule daily --app {EXPORT_DOMAIN}",
        f"noust app health {EXPORT_DOMAIN} --path / --expect 200-399 --timeout 20",
        f"noust releases keep {EXPORT_DOMAIN} 3",
    ):
        sc.run(script, timeout=120, label=script)

    sc.run(
        f"noust app export {EXPORT_DOMAIN} -o /root/x.json",
        timeout=60,
        label=f"noust app export {EXPORT_DOMAIN} -o /root/x.json",
    )
    document = json.loads(sc.run("cat /root/x.json", timeout=15, label="cat /root/x.json").stdout)
    env = document["env"]
    sc.check(
        env.get("APP_SECRET") == {"secret": True, "value": None},
        f"the export without secrets carries APP_SECRET as {env.get('APP_SECRET')!r}",
    )
    sc.check(env.get("GREETING", {}).get("value") == "hello", f"GREETING: {env.get('GREETING')!r}")
    sc.check(EXPORT_SECRET not in json.dumps(document), "the secret's value is in the export")

    refused = sc.run(
        "noust app import /root/x.json --domain copy.test --yes",
        timeout=120,
        check=False,
        label="noust app import /root/x.json --domain copy.test --yes (no value for the secret)",
    )
    sc.check(
        refused.returncode != 0 and "APP_SECRET" in refused.stdout + refused.stderr,
        "the import without the secret's value was not refused naming APP_SECRET",
    )

    # The document creates a cron job, a command it chose: the import asks first.
    unconfirmed = sc.run(
        f"noust app import /root/x.json --domain copy.test --env APP_SECRET={EXPORT_SECRET}",
        timeout=120,
        check=False,
        label="noust app import /root/x.json --domain copy.test --env APP_SECRET=... (no --yes)",
    )
    sc.check(
        unconfirmed.returncode != 0 and "--yes" in unconfirmed.stdout + unconfirmed.stderr,
        "an import that creates cron jobs ran without being confirmed",
    )
    created = sc.run(
        store_query("SELECT COUNT(*) FROM apps WHERE domain = 'copy.test'"),
        timeout=15,
        label="is copy.test in the store (after the unconfirmed import)",
    )
    sc.check(created.stdout.strip() == "0", "the unconfirmed import created copy.test")

    imported = sc.run(
        f"noust app import /root/x.json --domain copy.test --env APP_SECRET={EXPORT_SECRET} --yes",
        timeout=DEPLOY_TIMEOUT,
        label="noust app import /root/x.json --domain copy.test --env APP_SECRET=... --yes",
    )
    printed = imported.stdout + imported.stderr
    sc.check(
        "not applied" in printed and "port" in printed,
        "the import did not print the parts it did not apply (the port the original holds)",
    )
    served = curl_host(sc, "copy.test").strip()
    sc.check(served == "ok 1", f"copy.test served {served!r}")
    sc.check(curl_host(sc, "alias.copy.test").strip() == "ok 1", "alias.copy.test does not serve")

    domains = json.loads(
        sc.run("noust domain list copy.test --json", timeout=30, label="noust domain list").stdout
    )
    kinds = {item["domain"]: item["kind"] for item in domains["items"]}
    sc.check(kinds == {"copy.test": "primary", "alias.copy.test": "alias"}, f"domains: {kinds!r}")
    cron = sc.run(
        "systemctl cat noust-cron-copy-test-nightly.timer",
        timeout=15,
        check=False,
        label="systemctl cat noust-cron-copy-test-nightly.timer",
    )
    sc.check(cron.returncode == 0, "the cron job was not carried over as copy-test-nightly")
    row = sc.run(
        store_query(
            "SELECT health_path, health_expect, health_timeout, keep_releases FROM apps "
            "WHERE domain = 'copy.test'"
        ),
        timeout=15,
        label="health and retention of copy.test in the store",
    ).stdout.strip()
    sc.check(row == "/|200-399|20|3", f"health and retention of copy.test: {row!r}")
    env_file = sc.run(
        "cat /var/www/apps/copy-test/shared/.env",
        timeout=15,
        label="cat /var/www/apps/copy-test/shared/.env",
    ).stdout
    sc.check(f"APP_SECRET={EXPORT_SECRET}" in env_file, "the secret given with --env is missing")

    sc.run(
        f"noust app export {EXPORT_DOMAIN} --with-secrets -o /root/xs.json",
        timeout=60,
        label=f"noust app export {EXPORT_DOMAIN} --with-secrets -o /root/xs.json",
    )
    mode = sc.run("stat -c %a /root/xs.json", timeout=15, label="stat /root/xs.json").stdout
    sc.check(mode.strip() == "600", f"the export with secrets is mode {mode.strip()}")
    sc.run(
        "noust app import /root/xs.json --domain copy2.test --yes",
        timeout=DEPLOY_TIMEOUT,
        label="noust app import /root/xs.json --domain copy2.test --yes",
    )
    sc.check(curl_host(sc, "copy2.test").strip() == "ok 1", "copy2.test does not serve")
    env_file = sc.run(
        "cat /var/www/apps/copy2-test/shared/.env",
        timeout=15,
        label="cat /var/www/apps/copy2-test/shared/.env",
    ).stdout
    sc.check(f"APP_SECRET={EXPORT_SECRET}" in env_file, "the exported secret did not come back")


#: The build sandbox scenario: its repository (tests/integration/fixtures/
#: evil-postinstall, whose postinstall attacks the server), its two
#: applications, and what the postinstall tries to leave on the host.
SANDBOX_REPO = "/root/fixtures/evil-postinstall"
SANDBOX_URL = "git://127.0.0.1/evil-postinstall"
SANDBOX_DOMAIN = "evil.test"
SANDBOX_ROOT = "/var/www/apps/evil-test"
SANDBOX_CONTROL_DOMAIN = "evil-root.test"
SANDBOX_CONTROL_ROOT = "/var/www/apps/evil-root-test"
SANDBOX_TRACES = ("/root/pwned", "/etc/cron.d/noust-evil", "/usr/local/bin/noust-evil")

#: What the postinstall must not manage in the sandbox, and does manage without it.
SANDBOX_FORBIDDEN = (
    "write_root",
    "read_config",
    "read_decoy",
    "read_store",
    "read_other_env",
    "write_cron",
    "write_usr_local",
)


def sandbox_report(sc: Scenario, root: str, label: str) -> dict[str, Any]:
    """Read what the hostile postinstall of an application managed."""
    proc = sc.run(f"cat {root}/current/sandbox-report.json", timeout=15, label=label)
    report = json_of(proc, label)
    if not isinstance(report, dict):
        raise AssertionError(f"{label}: not a report: {report!r}")
    return report


def host_traces(sc: Scenario, label: str) -> list[str]:
    """List what the postinstall left on the host, outside any application."""
    proc = sc.run(
        "; ".join(f"test -e {path} && echo {path}" for path in SANDBOX_TRACES) + "; true",
        timeout=15,
        label=label,
    )
    return proc.stdout.split()


@scenario("build_sandbox_blocks_a_hostile_postinstall")
def scenario_build_sandbox(sc: Scenario) -> None:
    """A postinstall that attacks the server fails in the sandbox, and succeeds without it.

    The repository's postinstall tries to write /root/pwned, /etc/cron.d and
    /usr/local/bin, and to read /etc/noust (config.yaml, and a decoy there that
    is world-readable, so only the sandbox can refuse it), the store and
    another application's .env (world-readable too); it leaves a daemon
    behind. A new application builds in the sandbox: every attempt must fail,
    the build itself must succeed, and nothing may survive the build. Then the
    negative control, without which the first half proves nothing: the same
    postinstall, in an application whose sandbox an operator turned off, must
    manage exactly what the sandbox stopped.
    """
    sc.run(
        f"rm -f {' '.join(SANDBOX_TRACES)} && cd {SANDBOX_REPO} && rm -rf .git && "
        f"git init -q -b main && {GIT_IDENTITY} && "
        "npm install --package-lock-only --ignore-scripts && "
        "git add -A && git commit -q -m 'a hostile postinstall'",
        timeout=60,
        label=f"(fixture repo) {SANDBOX_REPO}, served at {SANDBOX_URL}",
    )
    # Readable by everyone: only the sandbox stands between a build and them.
    sc.run(
        "mkdir -p /etc/noust /var/www/apps/decoy-test/shared && "
        "echo decoy > /etc/noust/sandbox-decoy && chmod 644 /etc/noust/sandbox-decoy && "
        "echo OTHER_APP_SECRET=1 > /var/www/apps/decoy-test/shared/.env && "
        "chmod 644 /var/www/apps/decoy-test/shared/.env && "
        "(test -f /etc/noust/config.yaml || noust config show >/dev/null 2>&1 || true)",
        timeout=30,
        label="(decoys) /etc/noust/sandbox-decoy and another application's .env, mode 644",
    )

    selftest = json_of(
        sc.run(
            "noust app sandbox self-test --json",
            timeout=120,
            check=False,
            label="noust app sandbox self-test --json",
        ),
        "self-test",
    )
    sc.check(selftest.get("passed") is True, f"the sandbox does not hold here: {selftest}")

    create = (
        f"NOUST_SECRET_SENTINEL=leaked noust create -d {SANDBOX_DOMAIN} -s {SANDBOX_URL} "
        "-t nodejs --no-ssl --layout releases"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)

    status = json_of(
        sc.run(
            f"noust app sandbox status {SANDBOX_DOMAIN} --json",
            timeout=30,
            label=f"noust app sandbox status {SANDBOX_DOMAIN} --json",
        ),
        "sandbox status",
    )
    sc.check(status.get("mode") == "on", f"a new application is not sandboxed: {status}")

    report = sandbox_report(sc, SANDBOX_ROOT, "what the postinstall managed in the sandbox")
    for action in SANDBOX_FORBIDDEN:
        sc.check(
            report[action]["ok"] is False,
            f"{action} succeeded in the sandbox: {report[action]}",
        )
    sc.check(
        report["write_release"]["ok"] is True, f"the build could not write its release: {report}"
    )
    sc.check(
        report["interfaces"]["ok"] is True,
        f"os.networkInterfaces() failed (AF_NETLINK): {report['interfaces']}",
    )
    sc.check(report["sentinel"] is None, "the build inherited the CLI's environment")
    build_uid = sc.run("id -u noust-build", timeout=15, label="id -u noust-build").stdout.strip()
    sc.check(str(report["uid"]) == build_uid, f"the build ran as uid {report['uid']}")
    sc.check(
        host_traces(sc, "what the sandboxed postinstall left on the host") == [],
        "the sandboxed postinstall left traces on the host",
    )
    survivors = sc.run(
        "pgrep -u noust-build -a || echo none",
        timeout=15,
        label="pgrep -u noust-build (after the build)",
    )
    sc.check(survivors.stdout.strip() == "none", f"a build process survived: {survivors.stdout!r}")
    owner = sc.run(
        f"stat -c %U {SANDBOX_ROOT}/current/built.txt",
        timeout=15,
        label="stat -c %U current/built.txt",
    )
    # The release belongs to the account the application runs as: its own, since 3.1.
    account = f"noust-app-{SANDBOX_ROOT.rsplit('/', 1)[1]}"
    sc.check(owner.stdout.strip() == account, f"the release is {owner.stdout.strip()}'s")
    page = sc.run(
        f"curl -sS -H 'Host: {SANDBOX_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {SANDBOX_DOMAIN}' http://127.0.0.1/",
    )
    sc.check('"write_release"' in page.stdout, f"the application does not serve: {page.stdout!r}")

    # The negative control: the same postinstall, built as root on purpose.
    control = (
        f"noust create -d {SANDBOX_CONTROL_DOMAIN} -s {SANDBOX_URL} -t nodejs --no-ssl "
        "--layout releases"
    )
    sc.run(control, timeout=DEPLOY_TIMEOUT, label=control)
    sc.check(
        host_traces(sc, "traces after the control's first, sandboxed, build") == [],
        "the control's first build was not sandboxed",
    )
    sc.run(
        f"noust app sandbox disable {SANDBOX_CONTROL_DOMAIN} --reason 'negative control' --yes",
        timeout=30,
        label=f"noust app sandbox disable {SANDBOX_CONTROL_DOMAIN} --reason ... --yes",
    )
    # A new lockfile, so the update installs afresh and the postinstall runs.
    commit_to(
        sc,
        SANDBOX_REPO,
        "npm pkg set version=1.0.1 && npm install --package-lock-only --ignore-scripts",
        "a new lockfile",
    )
    update = f"noust update {SANDBOX_CONTROL_DOMAIN}"
    sc.run(update, timeout=DEPLOY_TIMEOUT, label=update)
    as_root = sandbox_report(sc, SANDBOX_CONTROL_ROOT, "what the postinstall managed as root")
    try:
        sc.check(as_root["uid"] == 0, f"the control did not build as root: {as_root}")
        for action in ("write_root", "read_decoy", "read_other_env", "write_cron"):
            sc.check(
                as_root[action]["ok"] is True,
                f"{action} failed without the sandbox too, so the scenario proves nothing: "
                f"{as_root[action]}",
            )
        sc.check(
            "/root/pwned" in host_traces(sc, "what the control left on the host"),
            "/root/pwned was not written without the sandbox",
        )
    finally:
        sc.run(
            f"rm -f {' '.join(SANDBOX_TRACES)}; pkill -f '^sleep 777$' || true",
            timeout=15,
            check=False,
            label="(cleanup) remove the control's traces and its daemon",
        )


#: The in-place application from before 3.1 moved into the sandbox (3.1.1).
LEGACY_REPO_NAME = "legacy-inplace"
LEGACY_DOMAIN = "legacy.test"
LEGACY_ROOT = "/var/www/apps/legacy-test"

#: Its build needs a devDependency and a tracked file changed on the server.
LEGACY_BUILD_JS = r"""
const fs = require("node:fs");
const dev = require("legacy-dev");
const config = JSON.parse(fs.readFileSync("config.json", "utf8"));
const version = fs.readFileSync("VERSION", "utf8").trim();
fs.mkdirSync("dist", { recursive: true });
fs.writeFileSync("dist/built.txt", `${dev.tag} ${config.flavour} ${version}\n`);
"""

#: Serves the version and what the build wrote.
LEGACY_SERVER_JS = r"""
const http = require("node:http");
const fs = require("node:fs");
const read = (name) => { try { return fs.readFileSync(name, "utf8").trim(); } catch { return "-"; } };
http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/plain" });
  res.end(`ok ${read("VERSION")} ${read("dist/built.txt")}\n`);
}).listen(process.env.PORT || 3000);
"""


def _legacy_owners(sc: Scenario, label: str) -> dict[str, list[str]]:
    """
    List what under the legacy application is owned by root and by noust-build.

    ``.git`` is left out: it is the metadata of Noust's own git, which runs as
    root (a ``git status`` after the tree is handed back rewrites its index),
    and the service never writes there.
    """
    found: dict[str, list[str]] = {}
    for account in ("root", "noust-build"):
        proc = sc.run(
            f"find {LEGACY_ROOT} -path {LEGACY_ROOT}/.git -prune -o -user {account} -print "
            "| head -20",
            timeout=30,
            label=f"{label}: owned by {account}",
        )
        found[account] = proc.stdout.split()
    return found


@scenario("inplace_legacy_app_sandbox_test_enable_update")
def scenario_legacy_inplace_sandbox(sc: Scenario) -> None:
    """
    An in-place application built as root before 3.1 moves into the sandbox and updates.

    Production evidence from 3.1.0: the trial built a clean export of the
    commit while the real update builds the tree (with changes made on the
    server); a .env holding NODE_ENV="production" made the sandboxed install
    drop devDependencies; and trees left partly root-owned by earlier root
    builds are what the service account's sandboxed build must write. So:
    a Node application whose build needs a devDependency and a tracked file
    changed on the server, deployed in place, with no build regime (as a
    pre-3.1 row has), updated once as root, its node_modules and build output
    then handed to root as older versions left them; then test, enable,
    update, and the new commit is served, built in the sandbox, with nothing
    left owned by root or noust-build.
    """
    repo, url = make_node_repo(sc, LEGACY_REPO_NAME)
    sc.run(
        f"cd {repo} && mkdir -p legacy-dev && "
        """echo '{"name": "legacy-dev", "version": "1.0.0"}' > legacy-dev/package.json && """
        "echo 'module.exports = { tag: \"dev\" };' > legacy-dev/index.js && "
        f"cat > build.js <<'JS'\n{LEGACY_BUILD_JS}\nJS\n"
        f"cat > server.js <<'JS'\n{LEGACY_SERVER_JS}\nJS\n"
        """echo '{"flavour": "committed"}' > config.json && """
        "printf 'node_modules/\\ndist/\\n.env\\nuploads/\\n' > .gitignore && "
        "npm pkg set scripts.build='node build.js' && "
        "npm pkg set devDependencies.legacy-dev=file:./legacy-dev && "
        "npm install --package-lock-only && "
        "git add -A && git commit -q -m 'a build that needs a devDependency'",
        timeout=60,
        label=f"(fixture repo) {repo}: build.js needs devDependency legacy-dev and config.json",
    )
    sc.run(
        f"noust create -d {LEGACY_DOMAIN} -s {url} -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {LEGACY_DOMAIN} -s {url} -t nodejs --no-ssl --layout inplace",
    )
    # What a server upgraded from before 3.1 has: no build regime at all.
    sc.run(
        store_query(
            "DELETE FROM build_sandbox WHERE app_id = "
            "(SELECT id FROM apps WHERE domain = 'legacy.test')"
        ),
        timeout=15,
        label="DELETE FROM build_sandbox for legacy.test (a pre-3.1 app has no row)",
    )
    status = json_of(
        sc.run(f"noust app sandbox status {LEGACY_DOMAIN} --json", timeout=30),
        "sandbox status",
    )
    sc.check(status.get("mode") == "legacy", f"not a pre-3.1 regime: {status}")
    # The server's own NODE_ENV, and a tracked file changed on the server.
    sc.run(
        f"echo 'NODE_ENV=\"production\"' >> {LEGACY_ROOT}/.env && "
        f"""echo '{{"flavour": "local"}}' > {LEGACY_ROOT}/config.json""",
        timeout=15,
        label=f"NODE_ENV=production in {LEGACY_ROOT}/.env; config.json changed on the server",
    )
    sc.run(
        f"noust update {LEGACY_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust update {LEGACY_DOMAIN} (as root, as before 3.1)",
    )
    served = curl_host(sc, LEGACY_DOMAIN)
    sc.check(served.strip() == "ok 1 dev local 1", f"the root build serves {served!r}")
    sc.run(
        f"chown -R root:root {LEGACY_ROOT}/node_modules {LEGACY_ROOT}/dist",
        timeout=30,
        label="node_modules and dist left owned by root, as earlier root builds left them",
    )

    trial = json_of(
        sc.run(
            f"noust app sandbox test {LEGACY_DOMAIN} --json",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust app sandbox test {LEGACY_DOMAIN} --json",
        ),
        "sandbox test",
    )
    sc.check(trial.get("passed") is True, f"the trial failed: {trial.get('detail')}")
    sc.check(
        trial.get("source") == "working tree" and trial.get("uncommitted") == ["config.json"],
        f"the trial did not build the tree with its local change: {trial}",
    )
    sc.run(
        f"noust app sandbox enable {LEGACY_DOMAIN} --json",
        timeout=60,
        label=f"noust app sandbox enable {LEGACY_DOMAIN} --json",
    )
    commit_to(sc, repo, "echo 2 > VERSION", "version 2")
    update = sc.run(
        f"noust update {LEGACY_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust update {LEGACY_DOMAIN} (in the sandbox)",
    )
    if update.returncode != 0:
        journal_tail(sc, f"-u {LEGACY_DOMAIN.replace('.', '-')}", "the application's journal")
    sc.check(update.returncode == 0, "the sandboxed in-place update failed")
    status = json_of(
        sc.run(f"noust app sandbox status {LEGACY_DOMAIN} --json", timeout=30),
        "sandbox status",
    )
    sc.check(status.get("mode") == "on", f"the update did not build in the sandbox: {status}")
    served = curl_host(sc, LEGACY_DOMAIN)
    sc.check(served.strip() == "ok 2 dev local 2", f"after the update it serves {served!r}")
    owners = _legacy_owners(sc, "after the sandboxed update")
    sc.check(
        owners == {"root": [], "noust-build": []},
        f"the tree is not the service account's: {owners}",
    )


#: Run inside the container by the installed Noust: the runner's sandbox
#: against the real systemd, reporting how each command ended, as JSON.
SANDBOX_RUNNER_PROBE = r"""
import json, subprocess
from noust.core.runner import SandboxSpec, SubprocessRunner

runner = SubprocessRunner()
spec = SandboxSpec(user="noust-build", name="it-runner")
out = {}
failed = runner.run(["sh", "-c", "echo no >&2; exit 3"], sandbox=spec, timeout=60)
out["exit"] = [failed.exit_code, failed.sandbox_result, failed.stderr.strip()]
echoed = runner.run(["echo", "$HOME", "${X}", "%h"], sandbox=spec, timeout=60)
out["echo"] = echoed.stdout.strip()
late = runner.stream(["sleep", "120"], on_line=lambda _l: None, sandbox=spec, timeout=3)
state = subprocess.run(
    ["systemctl", "is-active", f"{late.sandbox_unit}.service"], capture_output=True, text=True
)
out["deadline"] = [late.timed_out, late.stderr, state.stdout.strip()]
swap = subprocess.run(["swapon", "--show", "--noheadings"], capture_output=True, text=True)
if swap.stdout.strip():
    out["memory"] = "skipped: swap is on, so a memory limit swaps instead of killing"
else:
    hungry = runner.run(
        ["python3", "-c", "b = bytearray(512 * 1024 * 1024); print(len(b))"],
        sandbox=SandboxSpec(user="noust-build", name="it-oom", memory_max_mb=64),
        timeout=120,
    )
    out["memory"] = [hungry.exit_code, hungry.out_of_memory, hungry.stderr.strip()]
print(json.dumps(out))
"""


@scenario("build_sandbox_says_how_a_command_ended")
def scenario_sandbox_runner(sc: Scenario) -> None:
    """The runner's sandbox, against systemd as PID 1: exit codes, $ and the limits.

    systemd-run exits 1 both for a deadline and for the memory limit; the
    runner learns which from the result marker its unit's ExecStopPost leaves,
    and a deadline stops the unit, which killing systemd-run alone does not.
    """
    probe = sc.run(
        f"/opt/noust/bin/python - <<'PY'\n{SANDBOX_RUNNER_PROBE}\nPY",
        timeout=300,
        label="the runner's sandbox against systemd (exit 3, $HOME, a 3 s deadline, 64M)",
    )
    ended = json_of(probe, "sandbox runner probe")
    sc.check(ended["exit"][:2] == [3, "exit-code"], f"exit 3 came back as {ended['exit']}")
    sc.check(ended["exit"][2] == "no", f"stderr came back as {ended['exit'][2]!r}")
    sc.check(ended["echo"] == "$HOME ${X} %h", f"$ or % were interpreted: {ended['echo']!r}")
    timed_out, why, unit_state = ended["deadline"]
    sc.check(timed_out is True, f"the deadline was not reported: {ended['deadline']}")
    sc.check("deadline" in why, f"the deadline is not named: {why!r}")
    sc.check(unit_state != "active", f"the unit outlived its deadline: {unit_state!r}")
    if isinstance(ended["memory"], list):
        code, oom, words = ended["memory"]
        sc.check(code == 137 and oom is True, f"the memory kill came back as {ended['memory']}")
        sc.check("MemoryMax=64M" in words, f"the memory kill is not named: {words!r}")


# ---------------------------------------------------------------------------
# Upgrade rehearsal: Noust 3.0 -> 3.1 (--upgrade-from-3.0; not in the default suite)
# ---------------------------------------------------------------------------

#: The release the 3.1 rehearsal starts from, installed from PyPI.
UPGRADE_30_VERSION = "3.0.0"

#: Its applications: in place (static and Node, the Node one later moved to
#: another branch with `noust update --branch`) and one on releases.
U30_STATIC_DOMAIN = "u30-static.test"
U30_NODE_DOMAIN = "u30-node.test"
U30_NODE_ROOT = "/var/www/apps/u30-node-test"
U30_NODE_REPO_NAME = "u30-node"
U30_REL_DOMAIN = "u30-rel.test"
U30_REL_REPO_NAME = "u30-rel"
U30_DOMAINS = (U30_STATIC_DOMAIN, U30_NODE_DOMAIN, U30_REL_DOMAIN)
U30_BRANCH = "feature"
U30_TOKEN_NAME = "u30-admin-token"

#: The obsolete setting (the 1.x AI monitor's key) and the operator comment
#: the config.yaml carries into the upgrade.
U30_OBSOLETE_KEY_VALUE = "sk-noust-it-obsolete-openai-key"
U30_COMMENT = "# Operator note (noust-it): keep this comment across the 3.1 upgrade"
U30_CONFIG = "/etc/noust/config.yaml"

#: The password of the first account; the harness never prints it.
U30_ADMIN = "u30admin"


def install_noust_release(name: str, version: str) -> None:
    """Install a released Noust from PyPI into /opt/noust, the way install_noust installs a wheel.

    Args:
        name: Container name.
        version: The exact ``noust`` version, e.g. "3.0.0".
    """
    print(f"[setup] creating /opt/noust venv and installing noust=={version} from PyPI")
    docker_exec(name, "python3 -m venv /opt/noust", timeout=60)
    docker_exec(name, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=120)
    docker_exec(
        name,
        f"/opt/noust/bin/pip install --quiet 'noust[all]=={version}'",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    docker_exec(name, "ln -sf /opt/noust/bin/noust /usr/local/bin/noust", timeout=15)
    result = docker_exec(name, "noust --version", timeout=30)
    print(f"[setup] installed {result.stdout.strip()}")


def upgrade_noust_venv(sc: Scenario, wheel: Path) -> None:
    """Install the working tree's wheel over a released Noust in /opt/noust.

    ``pip install '<wheel>[all]'`` first, for the dependencies 3.1 added
    (cryptography): pip leaves an installed wheel of the same version alone
    ("already installed with the same version as the provided wheel"), and
    this tree may still carry the version it upgrades from. Then the wheel
    itself, forced and without dependencies, so the code really is replaced.
    Checked by importing the package, never by running the CLI: the first
    command is the one that migrates the store, and that belongs to the
    rehearsal.
    """
    sh(["docker", "cp", str(wheel), f"{sc.container}:/tmp/{wheel.name}"], timeout=60)
    sc.run(
        f"/opt/noust/bin/pip install --quiet '/tmp/{wheel.name}[all]'",
        timeout=PIP_INSTALL_TIMEOUT,
        label=f"pip install '/tmp/{wheel.name}[all]' (dependencies 3.1 added)",
    )
    sc.run(
        f"/opt/noust/bin/pip install --quiet --force-reinstall --no-deps '/tmp/{wheel.name}'",
        timeout=PIP_INSTALL_TIMEOUT,
        label=f"pip install --force-reinstall --no-deps /tmp/{wheel.name}",
    )
    installed = sc.run(
        "/opt/noust/bin/python -c 'import noust.core.store as s; print(s.SCHEMA_VERSION)'",
        timeout=30,
        label="the installed package's SCHEMA_VERSION (read without running the CLI)",
    ).stdout.strip()
    sc.check(
        installed == str(SCHEMA_VERSION),
        f"the venv still runs the old code: SCHEMA_VERSION {installed!r}, "
        f"the tree has {SCHEMA_VERSION}",
    )


def api_secret(
    sc: Scenario,
    method: str,
    path: str,
    label: str,
    *,
    bearer: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Call the console with a secret that never reaches an argv (curl reads its config on stdin).

    Args:
        sc: The scenario.
        method: HTTP method.
        path: Path under the console.
        label: What the evidence calls it.
        bearer: A token for the Authorization header.
        body: A JSON body.

    Returns:
        The status code and the parsed body.
    """

    def quoted(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        f"url = {quoted(PANEL_URL + path)}",
        f"request = {quoted(method)}",
        "silent",
        "show-error",
        'write-out = "\\n%{http_code}"',
    ]
    if bearer is not None:
        lines.append(f"header = {quoted('Authorization: Bearer ' + bearer)}")
    if body is not None:
        lines.append(f"header = {quoted('Content-Type: application/json')}")
        lines.append(f"data = {quoted(json.dumps(body))}")
    proc = subprocess.run(
        ["docker", "exec", "-i", sc.container, "curl", "-K", "-"],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    text, _, code = proc.stdout.rpartition("\n")
    try:
        payload: Any = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        payload = text
    shown = str(payload)
    for secret in (bearer, (body or {}).get("token"), (body or {}).get("password")):
        if secret:
            shown = shown.replace(str(secret), "<secret>")
    sc.evidence.append(f"$ {label}\n{code} {shown[:3000]}")
    return int(code or 0), payload


def _u30_curl(sc: Scenario, domain: str, label: str) -> str:
    return sc.run(
        f"curl -sS -H 'Host: {domain}' http://127.0.0.1/",
        timeout=30,
        check=False,
        label=f"{label} curl -H 'Host: {domain}' http://127.0.0.1/",
    ).stdout.strip()


def _u30_serving(sc: Scenario, label: str, node_version: str) -> None:
    """Every application serves what it should."""
    static = _u30_curl(sc, U30_STATIC_DOMAIN, label)
    sc.check("Noust Integration Static Fixture" in static, f"{label} static: {static!r}")
    node = _u30_curl(sc, U30_NODE_DOMAIN, label)
    sc.check(node == f"ok {node_version}", f"{label} node: expected ok {node_version}: {node!r}")
    rel = _u30_curl(sc, U30_REL_DOMAIN, label)
    sc.check(rel == "ok 1", f"{label} releases app: {rel!r}")


def _u30_unit(sc: Scenario, unit: str, label: str) -> tuple[str, str, bool]:
    """(is-enabled, is-active, unit file present) of a unit, one line each, never failing."""
    out = (
        sc.run(
            f"echo $(systemctl is-enabled {unit} 2>/dev/null); "
            f"echo $(systemctl is-active {unit} 2>/dev/null); "
            f"test -e /etc/systemd/system/{unit} && echo present || echo absent",
            timeout=15,
            check=False,
            label=label,
        )
        .stdout.strip()
        .splitlines()
    )
    out += [""] * (3 - len(out))
    return out[0].strip(), out[1].strip(), out[2].strip() == "present"


def run_upgrade_30_rehearsal(sc: Scenario, wheel: Path, *, monitor_installed: bool) -> None:
    """Set a server up with the released Noust 3.0.0, upgrade it to this tree, check 3.1's promises.

    What docs/UPGRADING-3.1.md says holds: the store at v12 with a
    ``noust.db.v11-*.bak`` copy beside it, the applications serving and
    listed, the API token and the console's access token still
    authenticating, the first admin account adopting the tokens, ``config
    clean`` removing the obsolete key with the comment kept and a 0600 copy,
    the monitor installed and enabled only where it was installed (and left
    off where 3.0 uninstalled it, with no marker 3.0 ever wrote), a plain
    update of an application moved with ``update --branch`` staying on that
    branch, the audit chain verifying, and ``ens check --json`` answering.

    The package's own configure steps (obs/debian.postinst) are run by hand
    after the pip upgrade, in its order, since that is what reaches most
    servers and pip runs none of them.

    Args:
        sc: The scenario.
        wheel: The working tree's wheel, on the host.
        monitor_installed: Whether 3.0 leaves the monitor installed (True)
            or enabled and then uninstalled (False).
    """
    v30 = "[noust 3.0]"
    v31 = "[noust 3.1]"
    #: Defects that do not stop the rehearsal; any of them fails it at the end.
    findings: list[str] = []

    # --- The monitor first, as run_upgrade_rehearsal does: its StateDirectory
    # --- gives /var/lib/noust a real directory before the store is created.
    sc.run("noust monitor enable", timeout=90, label=f"{v30} noust monitor enable")
    if not monitor_installed:
        # Let it run, as a real 3.0 monitor did, then remove it: 3.0 wrote no
        # marker, so only the journal remembers it.
        time.sleep(3)
        sc.run(
            "journalctl --unit noust-monitor.service --lines 3 --no-pager --output cat",
            timeout=15,
            label=f"{v30} the monitor's journal before it is uninstalled",
        )
        sc.run("noust monitor uninstall -y", timeout=60, label=f"{v30} noust monitor uninstall -y")
        gone = _u30_unit(
            sc, "noust-monitor.service", f"{v30} noust-monitor.service after uninstall"
        )
        sc.check(not gone[2], f"3.0's uninstall left the unit file: {gone!r}")

    # --- config.yaml: the defaults, then an operator comment and the obsolete
    # --- monitor.openai.api_key inside the existing monitor: section.
    sc.run("noust config upgrade", timeout=30, label=f"{v30} noust config upgrade")
    sc.run(
        "python3 - <<'EOF'\n"
        "import pathlib\n"
        f"p = pathlib.Path('{U30_CONFIG}')\n"
        "lines = p.read_text().splitlines() if p.exists() else []\n"
        f"block = ['  openai:', '    api_key: {U30_OBSOLETE_KEY_VALUE}', '    model: gpt-4']\n"
        "out, done = [], False\n"
        "for line in lines:\n"
        "    out.append(line)\n"
        "    if line.rstrip() == 'monitor:' and not done:\n"
        "        out.extend(block)\n"
        "        done = True\n"
        "if not done:\n"
        "    out += ['monitor:'] + block\n"
        f"p.write_text('{U30_COMMENT}\\n' + '\\n'.join(out) + '\\n')\n"
        "EOF",
        timeout=15,
        label=f"{v30} add '{U30_COMMENT}' and monitor.openai.api_key to {U30_CONFIG}",
    )
    sc.run(
        f"grep -n -B1 -A3 'openai' {U30_CONFIG}; head -3 {U30_CONFIG}",
        timeout=15,
        label=f"{v30} {U30_CONFIG} around the obsolete key",
    )
    sc.run("noust config show >/dev/null", timeout=30, label=f"{v30} noust config show (loads)")

    # --- Applications ------------------------------------------------------
    repo, url = make_node_repo(sc, U30_NODE_REPO_NAME)
    commit_to(
        sc, repo, f"git checkout -q -b {U30_BRANCH} && echo {U30_BRANCH}-1 > VERSION", "feature 1"
    )
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label=f"(fixture repo) {repo}: main")
    rel_repo, rel_url = make_node_repo(sc, U30_REL_REPO_NAME)

    sc.run(
        f"noust create -d {U30_STATIC_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl "
        "--layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v30} noust create -d {U30_STATIC_DOMAIN} (static, in place)",
    )
    sc.run(
        f"noust create -d {U30_NODE_DOMAIN} -s {url} -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v30} noust create -d {U30_NODE_DOMAIN} -s {url} (node, in place)",
    )
    sc.run(
        f"noust create -d {U30_REL_DOMAIN} -s {rel_url} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v30} noust create -d {U30_REL_DOMAIN} -s {rel_url} (node, releases)",
    )
    switched = sc.run(
        f"noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"{v30} noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}",
    )
    if switched.returncode != 0:
        # An in-place application is a `git clone --depth 1 --branch main`, which
        # is single-branch: `git fetch origin feature` then only fills
        # FETCH_HEAD, never refs/remotes/origin/feature, so the retried
        # `git checkout feature` still finds nothing (source_manager.pull). The
        # finding is kept and the rest is rehearsed on the state an operator
        # reaches by widening the clone's refspec by hand.
        findings.append(
            f"3.0: `noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}` failed on an in-place "
            f"git app (exit {switched.returncode}): {switched.stdout.strip().splitlines()[-1:]!r}"
        )
        sc.run(
            f"git -C {U30_NODE_ROOT} remote set-branches --add origin {U30_BRANCH}",
            timeout=15,
            label=f"{v30} (workaround) git remote set-branches --add origin {U30_BRANCH}",
        )
        sc.run(
            f"noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}",
            timeout=DEPLOY_TIMEOUT,
            label=f"{v30} noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH} (after the workaround)",
        )
    branch_30 = sc.run(
        f"git -C {U30_NODE_ROOT} rev-parse --abbrev-ref HEAD",
        timeout=15,
        label=f"{v30} the in-place checkout's branch",
    ).stdout.strip()
    sc.check(branch_30 == U30_BRANCH, f"3.0 did not switch the checkout: {branch_30!r}")
    _u30_serving(sc, v30, f"{U30_BRANCH}-1")

    # --- Console and tokens ------------------------------------------------
    enable = docker_exec(sc.container, "noust web enable", timeout=60, check=False)
    sc.evidence.append(f"$ {v30} noust web enable\n(output withheld: it holds the access token)")
    master_match = re.search(r"Access Token:\s*(\S+)", enable.stdout)
    sc.check(
        enable.returncode == 0 and master_match is not None,
        f"noust web enable printed no access token (exit {enable.returncode}): {enable.stderr}",
    )
    master = master_match.group(1) if master_match else ""
    deadline = time.time() + 30
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "200":
            break
        time.sleep(1)
    issued = docker_exec(
        sc.container, f"noust token create {U30_TOKEN_NAME} --scope admin", timeout=30
    )
    sc.evidence.append(f"$ {v30} noust token create {U30_TOKEN_NAME} --scope admin\n(withheld)")
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "noust token create printed no token")
    token = token_match.group(1) if token_match else ""

    code, session = api_secret(
        sc, "GET", "/api/auth/session", f"{v30} GET /api/auth/session (API token)", bearer=token
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the API token does not authenticate on 3.0: {code} {session!r}",
    )
    code, login = api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{v30} POST /api/auth/login (access token)",
        body={"token": master},
    )
    sc.check(code == 200, f"the access token does not sign in on 3.0: {code} {login!r}")

    # --- Before ------------------------------------------------------------
    db = sc.run("noust store path", timeout=30, label=f"{v30} noust store path").stdout.strip()
    db = db.splitlines()[-1].strip() if db else ""
    sc.check(db.endswith("noust.db"), f"unexpected store path: {db!r}")
    schema_30 = sc.run(
        store_query_at(db, "SELECT MAX(version) FROM schema_version"),
        timeout=15,
        label=f"{v30} store schema",
    ).stdout.strip()
    list_30 = sc.run("noust list", timeout=30, label=f"{v30} noust list").stdout
    monitor_30 = _u30_unit(sc, "noust-monitor.service", f"{v30} noust-monitor.service")
    sc.check(
        monitor_30[2] == monitor_installed,
        f"the monitor's unit file is not as this variant set it: {monitor_30!r}",
    )
    sc.run(f"ls -l {db}* 2>&1", timeout=15, check=False, label=f"{v30} ls -l the store")

    # --- Upgrade: the wheel over the same venv, then the package's steps ---
    upgrade_noust_venv(sc, wheel)
    for step in (
        'noust config upgrade --reason "package upgrade"',
        'noust config clean --reason "package upgrade"',
    ):
        sc.run(step, timeout=60, label=f"{v31} (postinst) {step}")
    if _u30_unit(sc, "noust-monitor.service", f"{v31} (postinst) is the monitor enabled?")[0] == (
        "enabled"
    ):
        sc.run(
            'noust monitor install --reason "package upgrade" && systemctl daemon-reload && '
            "systemctl try-restart noust-monitor.service",
            timeout=90,
            label=f"{v31} (postinst) noust monitor install; try-restart noust-monitor",
        )
    auto = sc.run(
        'noust monitor autoenable --reason "package upgrade"',
        timeout=90,
        label=f"{v31} (postinst) noust monitor autoenable",
    )
    sc.run(
        "systemctl is-active --quiet noust-web.service && systemctl restart noust-web.service",
        timeout=60,
        label=f"{v31} (postinst) systemctl restart noust-web.service",
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "200":
            break
        time.sleep(1)

    # --- The store: v12, with the v11 copy beside it -----------------------
    schema_31 = sc.run(
        store_query_at(db, "SELECT MAX(version) FROM schema_version"),
        timeout=15,
        label=f"{v31} store schema",
    ).stdout.strip()
    sc.check(
        schema_31 == str(SCHEMA_VERSION),
        f"the store is at v{schema_31} (was v{schema_30}), expected v{SCHEMA_VERSION}",
    )
    backups = (
        sc.run(
            f"stat -c '%a %s %n' {db}.v11-*.bak",
            timeout=15,
            check=False,
            label=f"{v31} stat {db}.v11-*.bak",
        )
        .stdout.strip()
        .splitlines()
    )
    sc.check(len(backups) == 1, f"expected one v11 copy of the store: {backups!r}")
    if backups:
        mode, _, path = backups[0].partition(" ")
        bak = path.partition(" ")[2]
        sc.check(mode == "600", f"the v11 copy is mode {mode}, expected 600")
        bak_schema = sc.run(
            " ; ".join(
                (
                    store_query_at(bak, "SELECT MAX(version) FROM schema_version"),
                    store_query_at(bak, "SELECT COUNT(*) FROM apps"),
                )
            ),
            timeout=15,
            label=f"{v31} the copy's schema and app count",
        ).stdout.split()
        sc.check(
            bak_schema == [schema_30, "3"],
            f"the copy is not the 3.0 store: {bak_schema!r} (3.0 was v{schema_30})",
        )

    # --- Applications ------------------------------------------------------
    _u30_serving(sc, v31, f"{U30_BRANCH}-1")
    list_31 = sc.run("noust list", timeout=30, label=f"{v31} noust list").stdout
    for domain in U30_DOMAINS:
        sc.check(domain in list_30, f"3.0's noust list did not show {domain}")
        sc.check(domain in list_31, f"noust list after the upgrade does not show {domain}")

    # --- Tokens, before any account ------------------------------------------
    code, session = api_secret(
        sc, "GET", "/api/auth/session", f"{v31} GET /api/auth/session (API token)", bearer=token
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the 3.0 API token no longer authenticates: {code} {session!r}",
    )
    code, login = api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{v31} POST /api/auth/login (access token)",
        body={"token": master},
    )
    sc.check(code == 200, f"the 3.0 access token no longer signs in: {code} {login!r}")
    web_token = sc.run("noust web token", timeout=30, label=f"{v31} noust web token").stdout
    sc.check("issued" in web_token, f"noust web token does not report the token: {web_token!r}")
    listed = json_of(
        sc.run("noust token list --json", timeout=30, label=f"{v31} noust token list --json"),
        "noust token list --json",
    )
    ours = [t for t in listed.get("tokens", []) if t.get("name") == U30_TOKEN_NAME]
    sc.check(
        len(ours) == 1 and not ours[0].get("revoked_at") and ours[0].get("scope") == "admin",
        f"the 3.0 token is not listed live with its scope: {ours!r}",
    )

    # --- The first account adopts the tokens ------------------------------
    password = "Upgrade-" + secrets.token_hex(8)
    created = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            sc.container,
            "noust",
            "user",
            "create",
            U30_ADMIN,
            "--role",
            "admin",
            "--stdin",
        ],
        input=password + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    sc.evidence.append(
        f"$ {v31} noust user create {U30_ADMIN} --role admin --stdin\n{created.stdout.strip()}\n"
        f"{created.stderr.strip()}\n(exit={created.returncode})"
    )
    sc.check(created.returncode == 0, f"noust user create failed: {created.stderr!r}")
    sc.check(
        "adopted 1 API token" in created.stdout.replace("\n", " "),
        f"the first admin did not report adopting the 3.0 token: {created.stdout!r}",
    )
    listed = json_of(
        sc.run("noust token list --json", timeout=30, label=f"{v31} noust token list --json"),
        "noust token list --json",
    )
    ours = [t for t in listed.get("tokens", []) if t.get("name") == U30_TOKEN_NAME]
    sc.check(
        len(ours) == 1 and ours[0].get("owner_account_id") is not None,
        f"the 3.0 token has no owner after the first admin: {ours!r}",
    )
    code, session = api_secret(
        sc,
        "GET",
        "/api/auth/session",
        f"{v31} GET /api/auth/session (adopted API token)",
        bearer=token,
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the adopted API token no longer authenticates: {code} {session!r}",
    )
    code, apps_payload = api_secret(
        sc, "GET", "/api/apps", f"{v31} GET /api/apps (adopted API token)", bearer=token
    )
    sc.check(code == 200, f"the adopted admin token cannot list apps: {code}")
    code, login = api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{v31} POST /api/auth/login (access token, now break-glass)",
        body={"token": master},
    )
    sc.check(code == 200, f"the access token no longer signs in as break-glass: {code} {login!r}")
    sc.run("noust user list", timeout=30, label=f"{v31} noust user list")

    # --- config clean -------------------------------------------------------
    config_after = sc.run(f"cat {U30_CONFIG}", timeout=15, label=f"{v31} cat {U30_CONFIG}").stdout
    sc.check("openai" not in config_after, "config clean left monitor.openai in config.yaml")
    sc.check(U30_OBSOLETE_KEY_VALUE not in config_after, "the OpenAI key is still in config.yaml")
    sc.check(U30_COMMENT in config_after, "config clean dropped the operator's comment")
    copies = (
        sc.run(
            f"stat -c '%a %n' {U30_CONFIG}.bak-*",
            timeout=15,
            check=False,
            label=f"{v31} stat {U30_CONFIG}.bak-*",
        )
        .stdout.strip()
        .splitlines()
    )
    sc.check(len(copies) >= 1, "config clean wrote no dated copy")
    for line in copies:
        mode, _, path = line.partition(" ")
        sc.check(mode == "600", f"{path} is mode {mode}, expected 600")
        leaked = sc.run(
            f"grep -c '{U30_OBSOLETE_KEY_VALUE}' {path} || true",
            timeout=15,
            check=False,
            label=f"{v31} the OpenAI key in {path}?",
        ).stdout.strip()
        sc.check(leaked == "0", f"the OpenAI key was kept in the copy {path}")
    shown = sc.run("noust config show", timeout=30, label=f"{v31} noust config show").stdout
    sc.check("openai" not in shown.lower(), "config show still lists monitor.openai")

    # --- The monitor --------------------------------------------------------
    monitor_31 = _u30_unit(sc, "noust-monitor.service", f"{v31} noust-monitor.service")
    marker = sc.run(
        "ls /var/lib/noust/monitor-declined 2>/dev/null || true",
        timeout=15,
        check=False,
        label=f"{v31} ls /var/lib/noust/monitor-declined",
    ).stdout.strip()
    sc.run("noust monitor status", timeout=30, check=False, label=f"{v31} noust monitor status")
    if monitor_installed:
        sc.check(
            monitor_31[:2] == ("enabled", "active") and monitor_31[2],
            f"the installed monitor is not enabled and active after the upgrade: {monitor_31!r}",
        )
        sc.check(not marker, f"a declined marker was written for an installed monitor: {marker}")
    else:
        sc.check(
            not monitor_31[2] and monitor_31[1] != "active",
            f"the monitor 3.0 uninstalled came back after the upgrade: {monitor_31!r} "
            f"(autoenable said: {auto.stdout.strip()!r})",
        )
        sc.check(bool(marker), "the removed monitor was not remembered as declined")

    # --- A plain update stays on the branch 3.0 switched to ---------------
    commit_to(sc, repo, f"git checkout -q {U30_BRANCH} && echo {U30_BRANCH}-2 > VERSION", "f2")
    commit_to(sc, repo, "git checkout -q main && echo main-2 > VERSION", "main 2")
    pin = json_of(
        sc.run(
            f"noust app branch {U30_NODE_DOMAIN} --json",
            timeout=30,
            label=f"{v31} noust app branch {U30_NODE_DOMAIN} --json",
        ),
        "noust app branch --json",
    )
    sc.check(not pin.get("pinned"), f"the upgrade pinned a branch by itself: {pin!r}")
    sc.run(
        f"noust update {U30_NODE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v31} noust update {U30_NODE_DOMAIN} (no --branch)",
    )
    branch_31 = sc.run(
        f"git -C {U30_NODE_ROOT} rev-parse --abbrev-ref HEAD",
        timeout=15,
        label=f"{v31} the in-place checkout's branch after a plain update",
    ).stdout.strip()
    sc.check(branch_31 == U30_BRANCH, f"a plain update moved the checkout to {branch_31!r}")
    node = _u30_curl(sc, U30_NODE_DOMAIN, v31)
    sc.check(
        node == f"ok {U30_BRANCH}-2", f"the plain update did not deploy {U30_BRANCH}: {node!r}"
    )

    # --- 3.1 on a branch the in-place clone has never seen ----------------
    commit_to(sc, repo, "git checkout -q -b feature2 && echo feature2-1 > VERSION", "feature2")
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label=f"(fixture repo) {repo}: main")
    fresh = sc.run(
        f"noust update {U30_NODE_DOMAIN} --branch feature2",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"{v31} noust update {U30_NODE_DOMAIN} --branch feature2 (not in the clone's refspec)",
    )
    if fresh.returncode != 0:
        findings.append(
            f"3.1: `noust update {U30_NODE_DOMAIN} --branch feature2` still fails on an in-place "
            f"git app (exit {fresh.returncode}): {fresh.stdout.strip().splitlines()[-1:]!r}"
        )
        still = _u30_curl(sc, U30_NODE_DOMAIN, v31)
        sc.check(still == f"ok {U30_BRANCH}-2", f"the failed switch broke the app: {still!r}")
    else:
        now = _u30_curl(sc, U30_NODE_DOMAIN, v31)
        sc.check(now == "ok feature2-1", f"update --branch feature2 serves {now!r}")

    # --- Audit and ENS ------------------------------------------------------
    sc.run("noust audit status", timeout=30, check=False, label=f"{v31} noust audit status")
    sc.run("noust audit verify", timeout=60, label=f"{v31} noust audit verify")
    ens = sc.run(
        "noust ens check --json", timeout=180, check=False, label=f"{v31} noust ens check --json"
    )
    sc.check(ens.returncode in (0, 1, 2), f"ens check crashed: exit {ens.returncode}")
    sc.check("Traceback" not in ens.stderr, "ens check raised")
    report = json_of(ens, "noust ens check --json")
    sc.check(
        isinstance(report, dict) and bool(report.get("findings")),
        f"ens check --json has no findings: {str(report)[:500]}",
    )
    sc.check(
        not report.get("errors"),
        f"ens check could not read part of the server: {report.get('errors')!r}",
    )
    sc.run("noust --version", timeout=30, label=f"{v31} noust --version")
    sc.check(not findings, "defects found:\n  - " + "\n  - ".join(findings))


def run_upgrade_30_mode(wheel: Path, *, variants: list[bool], keep: bool) -> int:
    """Run :func:`run_upgrade_30_rehearsal` once per monitor variant, each in its own container.

    Args:
        wheel: The working tree's wheel, on the host.
        variants: ``monitor_installed`` values to run.
        keep: Do not remove the containers when done.

    Returns:
        0 when every variant passed, 1 otherwise.
    """
    started_at = time.monotonic()
    failures = 0
    for monitor_installed in variants:
        label = (
            "upgrade_noust_3_0_to_3_1_monitor_installed"
            if monitor_installed
            else "upgrade_noust_3_0_to_3_1_monitor_uninstalled"
        )
        name = random_container_name()
        sc = Scenario(container=name)
        print(f"\n=== {label} ({name}) ===")
        try:
            try:
                start_container(name)
                wait_for_systemd(name)
                install_fixtures(name)
                install_noust_release(name, UPGRADE_30_VERSION)
                run_upgrade_30_rehearsal(sc, wheel, monitor_installed=monitor_installed)
            except (AssertionError, HarnessError) as exc:
                failures += 1
                print(f"FAIL: {label}: {exc}")
            else:
                print(f"PASS: {label}")
        finally:
            if sc.evidence:
                print("--- evidence ---")
                print("\n\n".join(sc.evidence))
            if keep:
                print(f"\n[teardown] --keep given, leaving container {name} running")
            else:
                print(f"\n[teardown] removing container {name}")
                remove_container(name)
    elapsed = time.monotonic() - started_at
    print(
        f"\n[summary] {len(variants)} scenario(s) run, {failures} failure(s), {elapsed:.1f}s total"
    )
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Noust 3.2: Docker Compose stacks, deploy hooks, relay, adoption, copies, tags
# ---------------------------------------------------------------------------
#
# Scenarios over small generic fixtures (tests/integration/fixtures/compose and the node-hooks
# and node-prisma overlays): a web service with a health endpoint, a worker with no port, a
# PostgreSQL next to a web service, a version that does not start. Every scenario drives the real
# CLI. The ones for a feature that is still being built check, first, that the build under test
# has the command or module they need (require_cli, require_module) and SKIP with the reason
# until it does: they turn on by themselves when the feature lands, and run today with
# --ignore-requirements to see how far they get.

COMPOSE_FIXTURES = "/root/fixtures/compose"
COMPOSE_BASE_IMAGE = "python:3.12-alpine"
COMPOSE_DB_IMAGE = "postgres:16-alpine"

#: Seconds between the requests of the probe the relay scenarios run: the spec's 0.2 s.
PROBE_INTERVAL = 0.2

#: How long a stack gets to come up and answer through nginx after the CLI returns.
STACK_READY_TIMEOUT = 60


def app_name_of(domain: str) -> str:
    """The application name Noust derives from a domain: dots as dashes."""
    return domain.replace(".", "-")


def compose_project(domain: str) -> str:
    """The Compose project of a stack Noust deployed on ``domain``: named after its application."""
    return app_name_of(domain)


def make_compose_repo(
    sc: Scenario, name: str, *fixtures: str, port: int | None = None, port2: int | None = None
) -> tuple[str, str]:
    """
    Create a git repository from Compose fixtures, served by git daemon, at its first version.

    Fixtures are directories under ``fixtures/compose``, copied in order: a later one adds to or
    replaces files of an earlier one (``database`` and ``two-webs`` hold only the compose file
    that replaces the web fixture's). ``@PORT@`` and ``@PORT2@`` in them become the host ports,
    because scenarios share the container's ports and each one takes its own.

    Args:
        sc: The scenario.
        name: Path under /root/fixtures, such as ``hooks-stack``.
        *fixtures: The fixture directories to copy, such as ``"web"`` or ``"web", "database"``.
        port: The host port for ``@PORT@``.
        port2: The host port for ``@PORT2@``.

    Returns:
        The repository's path in the container and its git:// URL.
    """
    repo = f"/root/fixtures/{name}"
    copies = " && ".join(f"cp -a {COMPOSE_FIXTURES}/{fixture}/. {repo}/" for fixture in fixtures)
    substitutions = "".join(
        f"grep -rl '{token}' {repo} --exclude-dir=.git | xargs -r sed -i 's/{token}/{value}/g' && "
        for token, value in (("@PORT@", port), ("@PORT2@", port2))
        if value is not None
    )
    sc.run(
        f"rm -rf {repo} && mkdir -p {repo} && {copies} && {substitutions}"
        f"cd {repo} && git init -q -b main && {GIT_IDENTITY} && "
        "git add -A && git commit -q -m 'v1'",
        timeout=30,
        label=f"(fixture repo) {repo}: {' + '.join(fixtures)}, served at git://127.0.0.1/{name}",
    )
    return repo, f"git://127.0.0.1/{name}"


def compose_create(
    sc: Scenario, domain: str, url: str, *, port: int | None, ssl: bool = False
) -> subprocess.CompletedProcess[str]:
    """
    Deploy a Compose stack the way an operator does: ``noust create -t docker-compose``.

    ``--port`` is passed unless ``port`` is None: before 3.2 a stack without it got a free port
    that its site proxied to, whatever ports the stack published, so only the scenario that is
    about that (``compose_site_follows_the_published_ports``) leaves it out.

    Args:
        sc: The scenario.
        domain: The domain to serve.
        url: The repository.
        port: The stack's published port, or None to leave Noust to find it.
        ssl: Ask for a certificate instead of ``--no-ssl``.

    Returns:
        The finished command.
    """
    command = f"noust create -d {domain} -s {url} -t docker-compose"
    if port is not None:
        command += f" --port {port}"
    if not ssl:
        command += " --no-ssl"
    return sc.run(command, timeout=DEPLOY_TIMEOUT, label=command)


def compose_ids(sc: Scenario, domain: str, service: str) -> list[str]:
    """The full ids of the running containers of one service of a stack (not one-off ones)."""
    proc = docker_exec(
        sc.container,
        "docker ps -q --no-trunc "
        f"-f label=com.docker.compose.project={compose_project(domain)} "
        f"-f label=com.docker.compose.service={service} "
        "-f label=com.docker.compose.oneoff=False",
        timeout=30,
        check=False,
    )
    return proc.stdout.split()


def compose_exec(
    sc: Scenario, domain: str, service: str, script: str, label: str
) -> subprocess.CompletedProcess[str]:
    """Run a shell script inside a stack's service container; ``script`` holds no single quote."""
    return sc.run(
        "docker exec "
        f"$(docker ps -q -f label=com.docker.compose.project={compose_project(domain)} "
        f"-f label=com.docker.compose.service={service} "
        "-f label=com.docker.compose.oneoff=False | head -1) "
        f"sh -c '{script}'",
        timeout=60,
        check=False,
        label=label,
    )


def compose_logs(sc: Scenario, domain: str, service: str) -> str:
    """The last lines a stack's service printed."""
    return docker_exec(
        sc.container,
        f"docker logs --tail 30 $(docker ps -aq -f label=com.docker.compose.project="
        f"{compose_project(domain)} -f label=com.docker.compose.service={service} | head -1) 2>&1",
        timeout=30,
        check=False,
    ).stdout


def site_files(sc: Scenario, domain: str) -> str:
    """The nginx site files of a domain that exist, one per line: empty when it has none."""
    return docker_exec(
        sc.container,
        f"ls /etc/nginx/sites-enabled/{domain} /etc/nginx/sites-available/{domain} 2>/dev/null",
        timeout=15,
        check=False,
    ).stdout.strip()


def compose_clean(sc: Scenario, domain: str) -> None:
    """Remove an application, then anything of its stack that is left, whatever the scenario did."""
    project = compose_project(domain)
    sc.run(
        f"noust delete {domain} -f",
        timeout=180,
        check=False,
        label=f"cleanup: noust delete {domain}",
    )
    sc.run(
        f"docker ps -aq -f label=com.docker.compose.project={project} | xargs -r docker rm -f; "
        f"docker volume ls -q -f label=com.docker.compose.project={project} "
        "| xargs -r docker volume rm -f; true",
        timeout=120,
        check=False,
        label=f"cleanup: what is left of the {project} stack",
    )


def stop_load(sc: Scenario) -> None:
    """Stop the load generator, whether or not it is running."""
    sc.run(f"systemctl stop {LOAD_UNIT}; true", timeout=30, check=False, label="stop the load")


def wait_served(sc: Scenario, host: str, expected: str, *, path: str = "/") -> str:
    """
    Wait until the site answers with ``expected`` in its body, and fail when it never does.

    A stack is up when the CLI returns, but its container may still be starting: the first
    answers through nginx can be a 502 for a moment.

    Args:
        sc: The scenario.
        host: The ``Host`` header, which picks the site.
        expected: What the body must contain.
        path: What to ask for.

    Returns:
        The body that contained it.
    """
    deadline = time.monotonic() + STACK_READY_TIMEOUT
    body = ""
    while time.monotonic() < deadline:
        body = docker_exec(
            sc.container,
            f"curl -sS -m 5 -H 'Host: {host}' 'http://127.0.0.1{path}'",
            timeout=30,
            check=False,
        ).stdout.strip()
        if expected in body:
            break
        time.sleep(1)
    sc.evidence.append(
        f"$ (until it answers) curl -H 'Host: {host}' http://127.0.0.1{path}\n{body}"
    )
    sc.check(expected in body, f"{host}{path} answered {body!r}, not {expected!r}")
    return body


def store_rows(sc: Scenario, sql: str, *, domain: str, label: str) -> list[dict[str, Any]]:
    """
    Run a query against the store and return its rows, with the domain as a bound parameter.

    The query is a literal that names ``@domain``, never a string built from the domain:
    sqlite3's ``.parameter`` binds it, which is what keeps the harness's queries literals.

    Args:
        sc: The scenario.
        sql: The query, such as ``"SELECT port FROM apps WHERE domain = @domain"``.
        domain: What ``@domain`` is bound to.
        label: What the query is for, in the evidence.

    Returns:
        One dict per row.
    """
    proc = sc.run(
        f'sqlite3 -json -cmd ".parameter set @domain \'{domain}\'" {NOUST_DB} "{sql}"',
        timeout=15,
        label=label,
    )
    rows: list[dict[str, Any]] = json.loads(proc.stdout or "[]")
    return rows


def recorded_port(sc: Scenario, domain: str) -> int | None:
    """The port the store has for an application: None when it has none."""
    rows = store_rows(
        sc,
        "SELECT port FROM apps WHERE domain = @domain",
        domain=domain,
        label=f"the port recorded for {domain}",
    )
    sc.check(bool(rows), f"{domain} is not in the store")
    port: int | None = rows[0]["port"]
    return port


def last_deployment(sc: Scenario, domain: str) -> dict[str, Any]:
    """The newest row of an application's deployment history, as the store holds it."""
    rows = store_rows(
        sc,
        "SELECT id, status, schema_changed, hooks, warnings, error, git_commit "
        "FROM deployments WHERE domain = @domain ORDER BY id DESC LIMIT 1",
        domain=domain,
        label=f"the newest deployment of {domain} in the history",
    )
    sc.check(bool(rows), f"{domain} has no deployment in the history")
    return rows[0]


def command_output(proc: subprocess.CompletedProcess[str]) -> str:
    """Everything a finished command printed."""
    return proc.stdout + proc.stderr


# -- The generic stack scenarios that run on the build as it is --------------------------------

GAP_DOMAIN = "gap.test"
GAP_PORT = 18101


@scenario("compose_update_probe_sees_the_gap")
def scenario_compose_gap(sc: Scenario) -> None:
    """
    A plain Compose update leaves the site without an answer while the new container starts.

    The control for the relay scenario, and a real update of a real stack under load: the new
    version takes four seconds to listen, so recreating the container leaves its port empty for
    that long and nginx answers 502. A probe of one request every 0.2 s through nginx must see
    it, because if it could not, the relay scenario's "zero failures" would prove nothing.
    """
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    repo, url = make_compose_repo(sc, "gap-stack", "web", port=GAP_PORT)
    compose_create(sc, GAP_DOMAIN, url, port=GAP_PORT)
    try:
        wait_served(sc, GAP_DOMAIN, "web v1")
        mark = start_load(sc, GAP_DOMAIN, interval=PROBE_INTERVAL, accept="23")
        commit_to(
            sc,
            repo,
            "printf 'v2\\n' > VERSION && printf '4\\n' > DELAY",
            "v2: four seconds to start",
        )
        sc.run(
            f"noust update {GAP_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {GAP_DOMAIN}"
        )
        report = load_report(sc, "a plain update of a stack that takes 4 s to start", mark)
        sc.check(report.requests >= 20, f"only {report.requests} requests were made")
        sc.check(
            len(report.failures) >= 1,
            "the probe saw no failed request while the container was recreated with an empty "
            "port for 4 s: it cannot show that the relay closes the gap",
        )
        wait_served(sc, GAP_DOMAIN, "web v2")
    finally:
        stop_load(sc)
        compose_clean(sc, GAP_DOMAIN)


WORKER_DOMAIN = "worker.test"


@scenario("compose_worker_deploys_without_a_site")
def scenario_compose_worker(sc: Scenario) -> None:
    """
    A stack that publishes no port is deployed and updated with no site and no web probe.

    A worker (the shape of a notices worker in production): running, healthy, nothing for a browser.
    No nginx site is written for it, its container runs, and an update recreates it with the
    new version, still without a site.
    """
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    repo, url = make_compose_repo(sc, "worker-stack", "worker")
    compose_create(sc, WORKER_DOMAIN, url, port=None)
    try:
        ids = compose_ids(sc, WORKER_DOMAIN, "worker")
        sc.check(len(ids) == 1, f"expected one worker container, found {ids!r}")
        state = sc.run(
            f"docker inspect -f '{{{{.State.Status}}}} {{{{.RestartCount}}}}' {ids[0]}",
            timeout=15,
            label="the worker container's state and restart count",
        )
        sc.check(state.stdout.strip() == "running 0", f"the worker is not steady: {state.stdout!r}")
        written = site_files(sc, WORKER_DOMAIN)
        sc.evidence.append(f"$ ls the nginx site files of {WORKER_DOMAIN}\n{written or '(none)'}")
        sc.check(not written, f"a site was written for a stack that publishes no port: {written!r}")
        logs = compose_logs(sc, WORKER_DOMAIN, "worker")
        sc.check("worker v1 heartbeat" in logs, f"the worker is not beating: {logs!r}")

        commit_to(sc, repo, "printf 'v2\\n' > VERSION", "v2")
        sc.run(
            f"noust update {WORKER_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {WORKER_DOMAIN}",
        )
        deadline = time.monotonic() + 30
        logs = ""
        while "worker v2 heartbeat" not in logs and time.monotonic() < deadline:
            time.sleep(1)
            logs = compose_logs(sc, WORKER_DOMAIN, "worker")
        sc.check("worker v2 heartbeat" in logs, f"the update did not recreate the worker: {logs!r}")
        again = site_files(sc, WORKER_DOMAIN)
        sc.check(not again, f"the update wrote a site for a stack with no port: {again!r}")
    finally:
        compose_clean(sc, WORKER_DOMAIN)


# -- Port reader, headless, hooks, relay and the rest: features other flows are building --------

PORTS_DOMAIN = "ports.test"
PORTS_FRONT = 18108
PORTS_BACK = 18109


@scenario("compose_site_follows_the_published_ports")
def scenario_compose_ports(sc: Scenario) -> None:
    """
    Without --port, the site goes to the port the stack publishes, and `/` to the web in front.

    A frontend that depends on a backend, the backend published on loopback only
    (``127.0.0.1:P:C``). No ``--port`` and no ``noust.nginx.yaml``: Noust reads the ports from
    the compose file, sends ``/`` to the service that depends on the other web service, and does
    not invent a ``/backend`` route (before 3.2 the backend got ``/`` and the frontend
    ``/frontend``, and the site proxied to a free port that nothing listened on).
    """
    require_module(sc, "noust.deployers.helpers.compose_ports", what="the Compose port reader")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    _, url = make_compose_repo(
        sc, "ports-stack", "web", "two-webs", port=PORTS_FRONT, port2=PORTS_BACK
    )
    compose_create(sc, PORTS_DOMAIN, url, port=None)
    try:
        wait_served(sc, PORTS_DOMAIN, "frontend v1")
        root = curl_host(sc, PORTS_DOMAIN).strip()
        sc.check(root == "frontend v1", f"/ went to {root!r}, not to the frontend")
        invented = curl_host(sc, PORTS_DOMAIN, "/backend/").strip()
        sc.check(
            invented != "backend v1",
            "a /backend route was invented: only what noust.nginx.yaml declares is routed",
        )
        port = recorded_port(sc, PORTS_DOMAIN)
        sc.check(port == PORTS_FRONT, f"the store recorded port {port!r}, not the frontend's")
        sc.run("nginx -t", timeout=15, label="nginx -t")
    finally:
        compose_clean(sc, PORTS_DOMAIN)


@scenario("compose_worker_is_not_a_down_web")
def scenario_compose_worker_not_down(sc: Scenario) -> None:
    """
    A stack with no port is judged by its containers, and a mis-registered one is fixed on request.

    Item 57 (notices): a worker deployed by WASM 1.x kept port 3000 and an nginx site, and
    `noust diagnose`, the monitor and the health report called it down because nothing answered
    on a port it never had. Now: a healthy worker diagnoses healthy; a worker that crashes in a
    loop diagnoses down with its container state and last log lines (not an HTTP probe);
    `noust health` points a worker still registered with a port at `noust app headless`, which
    clears the port and removes the site Noust wrote, and never does it by itself.
    """
    require_cli(sc, "app headless", what="a Compose stack with no web (item 57)")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    domain = "workerok.test"
    _, url = make_compose_repo(sc, "worker-ok-stack", "worker")
    compose_create(sc, domain, url, port=None)
    try:
        port = recorded_port(sc, domain)
        sc.check(port is None, f"a stack that publishes nothing was given port {port!r}")
        diagnosis = json_of(
            sc.run(f"noust diagnose {domain} --json", timeout=60, check=False, label="diagnose"),
            "noust diagnose",
        )
        sc.check(
            diagnosis["verdict"] == "healthy",
            f"a running worker with no port was diagnosed {diagnosis['verdict']!r}: "
            f"{diagnosis.get('probable_cause')!r}",
        )
        health = sc.run("noust health", timeout=60, check=False, label="noust health")
        sc.check(
            f"noust app headless {domain}" not in command_output(health),
            "noust health sends a correct worker to `noust app headless`",
        )

        # A stack that crashes in a loop: the version file the process reads at start says so.
        compose_exec(
            sc, domain, "worker", "echo crash > /app/VERSION", "make the worker crash on start"
        )
        sc.run(
            f"docker restart $(docker ps -aq -f label=com.docker.compose.project="
            f"{compose_project(domain)} -f label=com.docker.compose.service=worker)",
            timeout=60,
            label="restart the worker into its crash loop",
        )
        time.sleep(5)
        down = json_of(
            sc.run(f"noust diagnose {domain} --json", timeout=60, check=False, label="diagnose"),
            "noust diagnose",
        )
        sc.check(
            down["verdict"] == "down", f"a worker in a crash loop was diagnosed {down['verdict']!r}"
        )
        sc.check(
            "this version crashes" in json.dumps(down),
            "the diagnosis does not show the stack's own last log lines",
        )

        compose_exec(
            sc, domain, "worker", "echo v1 > /app/VERSION", "give the worker its version back"
        )
        sc.run(
            f"docker restart $(docker ps -aq -f label=com.docker.compose.project="
            f"{compose_project(domain)} -f label=com.docker.compose.service=worker)",
            timeout=60,
            label="restart the worker, steady again",
        )
        # Docker counts the restarts it made, so a worker that has just been fixed still looks
        # like one in a loop until its current run is past the ten seconds after which Docker
        # starts its back-off over.
        time.sleep(12)

        # Registered the way WASM 1.x left it: a port, and a site Noust wrote for it.
        store_rows(
            sc,
            "UPDATE apps SET port = 3000 WHERE domain = @domain",
            domain=domain,
            label="register the worker with a port, as WASM 1.x did",
        )
        sc.run(
            f"noust site create -d {domain} --port 3000 --no-ssl",
            timeout=60,
            label="the site WASM 1.x wrote for it",
        )
        health = sc.run("noust health", timeout=60, check=False, label="noust health")
        sc.check(
            f"noust app headless {domain}" in command_output(health),
            "noust health does not say how to fix a worker registered with a port",
        )
        sc.check(bool(site_files(sc, domain)), "the site was removed before anyone asked")
        sc.run(
            f"yes | noust app headless {domain}",
            timeout=60,
            label=f"noust app headless {domain} (answering yes)",
        )
        port = recorded_port(sc, domain)
        sc.check(port is None, f"noust app headless left port {port!r} recorded")
        left = site_files(sc, domain)
        sc.check(not left, f"noust app headless left the site Noust had written: {left!r}")
        sc.run("nginx -t", timeout=15, label="nginx -t (after removing the site)")
    finally:
        compose_clean(sc, domain)


HOOKS_DOMAIN = "hooks.test"
HOOKS_PORT = 18102


@scenario("compose_hooks_pass_and_fail")
def scenario_compose_hooks(sc: Scenario) -> None:
    """
    Deploy hooks in noust.yaml: they run in the new image, and a failing one moves nothing.

    v2 adds a pre_deploy migration (``migrates: true``) and a post_deploy: both run inside the
    new image against the service's own volume, and the deployment is recorded as one that
    changed the schema. v3 makes the migration fail: the update stops with the hook's own output,
    the container that served is the same one, still on v2. v4 makes only the post_deploy fail:
    the new version stays deployed, with a warning in the history.
    """
    require_cli(sc, "app hooks", what="deploy hooks (noust.yaml)")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    repo, url = make_compose_repo(sc, "hooks-stack", "web", port=HOOKS_PORT)
    compose_create(sc, HOOKS_DOMAIN, url, port=HOOKS_PORT)
    try:
        wait_served(sc, HOOKS_DOMAIN, "web v1")

        commit_to(
            sc,
            repo,
            f"printf 'v2\\n' > VERSION && cp {COMPOSE_FIXTURES}/yaml/hooks.yaml noust.yaml",
            "v2: a migration and a cache purge",
        )
        sc.run(
            f"noust update {HOOKS_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {HOOKS_DOMAIN}",
        )
        wait_served(sc, HOOKS_DOMAIN, "web v2")
        migrated = compose_exec(
            sc,
            HOOKS_DOMAIN,
            "web",
            "cat /data/migrations.log /data/purge.log",
            "what the hooks wrote",
        )
        sc.check(
            "migrated by v2" in migrated.stdout, f"pre_deploy did not run: {migrated.stdout!r}"
        )
        sc.check("purged by v2" in migrated.stdout, f"post_deploy did not run: {migrated.stdout!r}")
        row = last_deployment(sc, HOOKS_DOMAIN)
        sc.check(row["status"] == "success", f"the update's row: {row!r}")
        sc.check(row["schema_changed"] == 1, f"a migrates hook did not mark the schema: {row!r}")
        sc.check(
            "migrate.sh" in (row["hooks"] or ""), f"the history lacks the hooks that ran: {row!r}"
        )

        served_by = compose_ids(sc, HOOKS_DOMAIN, "web")
        commit_to(
            sc,
            repo,
            "printf 'v3\\n' > VERSION && touch FAIL_HOOK",
            "v3: the migration cannot apply",
        )
        failed = sc.run(
            f"noust update {HOOKS_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {HOOKS_DOMAIN} (the migration fails)",
        )
        sc.check(failed.returncode != 0, "an update whose pre_deploy hook failed succeeded")
        sc.check(
            "migration exploded: table users is locked" in command_output(failed),
            "the error does not carry the hook's own output",
        )
        sc.check(
            compose_ids(sc, HOOKS_DOMAIN, "web") == served_by,
            "the container that served was recreated by a deploy that never got past its hook",
        )
        sc.check(curl_host(sc, HOOKS_DOMAIN).strip() == "web v2", "what served is not serving v2")
        row = last_deployment(sc, HOOKS_DOMAIN)
        sc.check(row["status"] == "failed", f"the failed update's row: {row!r}")
        sc.check(row["schema_changed"] == 0, f"a migration that failed marked the schema: {row!r}")

        commit_to(
            sc,
            repo,
            "printf 'v4\\n' > VERSION && rm FAIL_HOOK && touch FAIL_POST",
            "v4: the cache purge fails",
        )
        sc.run(
            f"noust update {HOOKS_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {HOOKS_DOMAIN} (the post_deploy hook fails)",
        )
        wait_served(sc, HOOKS_DOMAIN, "web v4")
        row = last_deployment(sc, HOOKS_DOMAIN)
        sc.check(
            row["status"] in ("success", "deployed_with_warnings")
            and "cache purge failed" in json.dumps(row),
            f"a post_deploy that failed was not recorded as a warning: {row!r}",
        )
    finally:
        compose_clean(sc, HOOKS_DOMAIN)


NODE_HOOKS_PORT = 3722


def node_overlay_repo(sc: Scenario, repo: str, fixture: str, message: str) -> None:
    """Copy a fixture directory over a Node repository and commit it."""
    commit_to(sc, repo, f"cp -a /root/fixtures/{fixture}/. .", message)


@scenario("node_prisma_failure_aborts_the_update")
def scenario_prisma_failure(sc: Scenario) -> None:
    """
    A Prisma migration that fails stops the update: the code that serves is not replaced.

    Until 3.2 the failure was a warning and the new code served against the old schema. The
    ``prisma`` here is a stand-in with the real one's output (tests/integration/fixtures/
    node-prisma/fake-prisma), driven by files in the commit. A code-only update applies
    nothing and does not mark the schema; one with a new migration does; a failing migrate
    aborts, the release that served keeps serving and the error carries the tool's output.
    """
    require_cli(sc, "app hooks", what="Prisma failures aborting the update (H1)")
    domain = "prisma.test"
    repo, url = make_node_repo(sc, "prisma-app")
    node_overlay_repo(sc, repo, "node-prisma", "a Prisma project")
    sc.run(
        f"cd {repo} && npm pkg set devDependencies.prisma=file:./fake-prisma && "
        "npm install --package-lock-only && git add -A && git commit -q -m 'depend on prisma'",
        timeout=120,
        label="(fixture repo) the Node fixture depends on the stand-in prisma",
    )
    create = f"noust create -d {domain} -s {url} -t nodejs --no-ssl --layout releases --port 3721"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        sc.check(curl_host(sc, domain).strip() == "ok 1", "the app is not serving version 1")

        commit_to(sc, repo, "echo 2 > VERSION", "version 2: code only")
        sc.run(f"noust update {domain}", timeout=DEPLOY_TIMEOUT, label=f"noust update {domain}")
        sc.check(curl_host(sc, domain).strip() == "ok 2", "version 2 is not serving")
        row = last_deployment(sc, domain)
        sc.check(row["schema_changed"] == 0, f"nothing was migrated, yet: {row!r}")

        commit_to(
            sc,
            repo,
            "echo 3 > VERSION && mkdir prisma/migrations/20260201000000_add_orders && "
            "echo 'CREATE TABLE orders (id int);' > "
            "prisma/migrations/20260201000000_add_orders/migration.sql",
            "version 3: a new migration",
        )
        sc.run(f"noust update {domain}", timeout=DEPLOY_TIMEOUT, label=f"noust update {domain}")
        sc.check(curl_host(sc, domain).strip() == "ok 3", "version 3 is not serving")
        row = last_deployment(sc, domain)
        sc.check(
            row["schema_changed"] == 1, f"an applied migration did not mark the schema: {row!r}"
        )

        release = active_release_of(sc, domain)
        commit_to(
            sc, repo, "echo 4 > VERSION && touch prisma/FAIL", "version 4: the migration fails"
        )
        failed = sc.run(
            f"noust update {domain}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {domain} (prisma migrate deploy fails)",
        )
        sc.check(failed.returncode != 0, "an update whose Prisma migration failed succeeded")
        sc.check("P3009" in command_output(failed), "the error does not carry prisma's own output")
        sc.check(curl_host(sc, domain).strip() == "ok 3", "what served was replaced anyway")
        sc.check(active_release_of(sc, domain) == release, "the active release changed")
        row = last_deployment(sc, domain)
        sc.check(row["status"] == "failed", f"the failed update's row: {row!r}")
    finally:
        sc.run(f"noust delete {domain} -f", timeout=180, check=False, label="cleanup")


def active_release_of(sc: Scenario, domain: str) -> str:
    """What the ``current`` link of a release application points at."""
    link = sc.run(
        f"readlink /var/www/apps/{app_name_of(domain)}/current",
        timeout=15,
        check=False,
        label=f"the active release of {domain}",
    )
    return link.stdout.strip()


@scenario("release_rollback_over_a_schema_change")
def scenario_schema_rollback(sc: Scenario) -> None:
    """
    Going back by hand over a migration needs an explicit yes; going back over none does not.

    A Node app on releases with a ``migrates: true`` pre_deploy hook. Rolling back past the
    update that migrated is refused with the flag that confirms it, and nothing moves; with the
    flag it goes. An update that declares no migration can be rolled back without asking.
    """
    require_cli(
        sc,
        "releases rollback",
        option="--schema-changed-ok",
        what="manual rollbacks over a schema change",
    )
    require_cli(sc, "app hooks", what="deploy hooks (noust.yaml)")
    domain = "schema.test"
    repo, url = make_node_repo(sc, "schema-app")
    node_overlay_repo(sc, repo, "node-hooks", "a migrating pre_deploy hook")
    create = (
        f"noust create -d {domain} -s {url} -t nodejs --no-ssl --layout releases "
        f"--port {NODE_HOOKS_PORT}"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        commit_to(sc, repo, "echo 2 > VERSION", "version 2: its hook migrates")
        sc.run(f"noust update {domain}", timeout=DEPLOY_TIMEOUT, label=f"noust update {domain}")
        sc.check(curl_host(sc, domain).strip() == "ok 2", "version 2 is not serving")
        row = last_deployment(sc, domain)
        sc.check(row["schema_changed"] == 1, f"the migrating hook did not mark the schema: {row!r}")
        release = active_release_of(sc, domain)

        refused = sc.run(
            f"noust releases rollback {domain}",
            timeout=120,
            check=False,
            label=f"noust releases rollback {domain} (over a migration)",
        )
        sc.check(refused.returncode != 0, "a rollback over a schema change went ahead unasked")
        sc.check(
            "--schema-changed-ok" in command_output(refused),
            "the refusal does not name the flag that confirms it",
        )
        sc.check(active_release_of(sc, domain) == release, "the refused rollback moved the link")
        sc.check(
            curl_host(sc, domain).strip() == "ok 2", "the refused rollback changed what serves"
        )

        sc.run(
            f"noust releases rollback {domain} --schema-changed-ok",
            timeout=120,
            label=f"noust releases rollback {domain} --schema-changed-ok",
        )
        sc.check(
            curl_host(sc, domain).strip() == "ok 1", "the confirmed rollback is not serving v1"
        )

        commit_to(
            sc,
            repo,
            "echo 3 > VERSION && sed -i 's/migrates: true/migrates: false/' noust.yaml",
            "version 3: no migration",
        )
        sc.run(f"noust update {domain}", timeout=DEPLOY_TIMEOUT, label=f"noust update {domain}")
        sc.check(curl_host(sc, domain).strip() == "ok 3", "version 3 is not serving")
        row = last_deployment(sc, domain)
        sc.check(
            row["schema_changed"] == 0, f"a hook that does not migrate marked the schema: {row!r}"
        )
        sc.run(
            f"noust releases rollback {domain}",
            timeout=120,
            label=f"noust releases rollback {domain} (over an update that changed nothing)",
        )
        sc.check(curl_host(sc, domain).strip() == "ok 2", "the rollback did not go back to v2")
    finally:
        sc.run(f"noust delete {domain} -f", timeout=180, check=False, label="cleanup")


RELAY_DOMAIN = "relay.test"
RELAY_PORT = 18103


@scenario("compose_relay_without_cuts")
def scenario_compose_relay(sc: Scenario) -> None:
    """
    With the relay on, a Compose update and a broken one cost the visitor nothing.

    The same stack as the gap scenario (the new version takes four seconds to listen), with
    `noust app zero-downtime on`: the site reads its upstream from a servers file of Noust's, a
    relay container with the new image takes the traffic while the container is recreated, and
    a probe of one request every 0.2 s through nginx sees no failed answer. Then a version
    that cannot start: the update fails and the container that served is the same one, still
    serving, with no relay left behind.
    """
    require_module(sc, "noust.deployers.compose_relay", what="the Compose relay")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    repo, url = make_compose_repo(sc, "relay-stack", "web", port=RELAY_PORT)
    compose_create(sc, RELAY_DOMAIN, url, port=RELAY_PORT)
    app = compose_project(RELAY_DOMAIN)
    servers = f"/etc/nginx/noust-upstreams/{app}/web.servers"
    try:
        wait_served(sc, RELAY_DOMAIN, "web v1")
        sc.run(
            f"noust app zero-downtime {RELAY_DOMAIN} on --drain 2",
            timeout=120,
            label=f"noust app zero-downtime {RELAY_DOMAIN} on --drain 2",
        )
        listed = sc.run(f"cat {servers}", timeout=15, label="the servers file the site includes")
        sc.check(
            listed.stdout.strip() == f"server 127.0.0.1:{RELAY_PORT};",
            f"the servers file does not point at the stack's port: {listed.stdout!r}",
        )
        included = sc.run(
            f"grep -F '{servers}' /etc/nginx/sites-available/{RELAY_DOMAIN}",
            timeout=15,
            check=False,
            label="the site includes the servers file",
        )
        sc.check(included.returncode == 0, "the site does not include the servers file")

        mark = start_load(sc, RELAY_DOMAIN, interval=PROBE_INTERVAL, accept="23")
        commit_to(
            sc,
            repo,
            "printf 'v2\\n' > VERSION && printf '4\\n' > DELAY",
            "v2: four seconds to start",
        )
        sc.run(
            f"noust update {RELAY_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {RELAY_DOMAIN}",
        )
        mark = load_phase(sc, "a relayed update of a stack that takes 4 s to start", mark)
        wait_served(sc, RELAY_DOMAIN, "web v2")
        left = sc.run(
            "docker ps -a --format '{{.Names}}' | grep -- '-relay' || true",
            timeout=30,
            label="no relay container is left",
        )
        sc.check(not left.stdout.strip(), f"a relay container was left behind: {left.stdout!r}")
        listed = sc.run(f"cat {servers}", timeout=15, label="the servers file after the update")
        sc.check(
            listed.stdout.strip() == f"server 127.0.0.1:{RELAY_PORT};",
            f"the servers file was not given back to the stack's own port: {listed.stdout!r}",
        )

        serving = compose_ids(sc, RELAY_DOMAIN, "web")
        commit_to(sc, repo, "printf 'broken\\n' > VERSION", "v3: the image cannot start")
        failed = sc.run(
            f"noust update {RELAY_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {RELAY_DOMAIN} (an image that cannot start)",
        )
        sc.check(failed.returncode != 0, "an update to an image that cannot start succeeded")
        mark = load_phase(sc, "a relayed update to an image that cannot start", mark, minimum=5)
        sc.check(
            compose_ids(sc, RELAY_DOMAIN, "web") == serving,
            "the container that served was touched by an update whose image never started",
        )
        sc.check(curl_host(sc, RELAY_DOMAIN).strip() == "web v2", "what served is not serving v2")
        left = sc.run(
            "docker ps -a --format '{{.Names}}' | grep -- '-relay' || true",
            timeout=30,
            label="no relay container is left after the failure",
        )
        sc.check(not left.stdout.strip(), f"a relay container was left behind: {left.stdout!r}")

        sc.run(
            f"noust app zero-downtime {RELAY_DOMAIN} off",
            timeout=120,
            label=f"noust app zero-downtime {RELAY_DOMAIN} off",
        )
        sc.check(
            curl_host(sc, RELAY_DOMAIN).strip() == "web v2", "switching the relay off cut the site"
        )
    finally:
        stop_load(sc)
        compose_clean(sc, RELAY_DOMAIN)


ADOPT_DOMAIN = "adopt.test"
ADOPT_PORT = 18104
ADOPT_DIR = "/srv/adopt-stack"
ADOPT_PROJECT = "adoptstack"
ADOPT_SITE = "shop-front"

#: The site an operator wrote long before Noust: its file is not named after the domain.
ADOPT_SITE_TEXT = """# Written by hand, long before Noust.
server {
    listen 80;
    server_name @DOMAIN@;
    add_header X-Operator "hand-written" always;
    location / {
        proxy_pass http://127.0.0.1:@PORT@;
        proxy_set_header Host $host;
    }
}
"""


@scenario("compose_adopt_a_running_stack")
def scenario_compose_adopt(sc: Scenario) -> None:
    """
    A stack brought up by hand is adopted without a single container being touched.

    ``docker compose -p adoptstack up`` from a clone in /srv, and a site in sites-available
    whose name is not the domain, both as an operator leaves them. `noust app adopt` registers
    the stack: the same containers (same ids, same start time), the project name it already
    runs as, the site file found by the names it serves and left byte for byte as it was, a unit
    enabled but not started. The next update then works inside that project and that directory.
    """
    require_cli(sc, "app adopt", what="adopting a running stack")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    repo, url = make_compose_repo(sc, "adopt-stack", "web", port=ADOPT_PORT)
    site = ADOPT_SITE_TEXT.replace("@DOMAIN@", ADOPT_DOMAIN).replace("@PORT@", str(ADOPT_PORT))
    site_path = f"/etc/nginx/sites-available/{ADOPT_SITE}"
    try:
        sc.run(
            f"rm -rf {ADOPT_DIR} && git clone -q {url} {ADOPT_DIR} && cd {ADOPT_DIR} && "
            f"docker compose -p {ADOPT_PROJECT} up -d --build",
            timeout=DEPLOY_TIMEOUT,
            label=f"the operator's own `docker compose -p {ADOPT_PROJECT} up -d --build` in {ADOPT_DIR}",
        )
        sc.run(
            f"cat > {site_path} <<'EOF'\n{site}EOF\n"
            f"ln -sf {site_path} /etc/nginx/sites-enabled/{ADOPT_SITE} && nginx -t && nginx -s reload",
            timeout=30,
            label=f"the operator's own site, {site_path}",
        )
        wait_served(sc, ADOPT_DOMAIN, "web v1")
        before = sc.run(
            f"docker inspect -f '{{{{.Id}}}} {{{{.State.StartedAt}}}}' "
            f"$(docker ps -q -f label=com.docker.compose.project={ADOPT_PROJECT})",
            timeout=30,
            label="the stack's containers before",
        ).stdout
        site_sum = sc.run(f"sha256sum {site_path}", timeout=15, label="the site's checksum").stdout

        sc.run(
            f"yes | noust app adopt {ADOPT_DOMAIN} --path {ADOPT_DIR} --source {url}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust app adopt {ADOPT_DOMAIN} --path {ADOPT_DIR} --source {url}",
        )

        after = sc.run(
            f"docker inspect -f '{{{{.Id}}}} {{{{.State.StartedAt}}}}' "
            f"$(docker ps -q -f label=com.docker.compose.project={ADOPT_PROJECT})",
            timeout=30,
            label="the stack's containers after",
        ).stdout
        sc.check(before == after, f"adopting touched the containers:\n{before}\n{after}")
        sc.check(
            sc.run(f"sha256sum {site_path}", timeout=15, label="the site's checksum after").stdout
            == site_sum,
            "adopting rewrote the operator's site",
        )
        row = store_rows(
            sc,
            "SELECT * FROM apps WHERE domain = @domain",
            domain=ADOPT_DOMAIN,
            label="the adopted application's row",
        )[0]
        sc.check(row["layout"] == "inplace", f"adopted with layout {row['layout']!r}")
        sc.check(
            row["compose_project"] == ADOPT_PROJECT,
            f"the project it already ran as was not recorded: {row['compose_project']!r}",
        )
        sc.check(
            row["site_name"] == ADOPT_SITE,
            f"the site's file name was not recorded: {row['site_name']!r}",
        )
        unit = f"{compose_project(ADOPT_DOMAIN)}"
        enabled = sc.run(
            f"systemctl is-enabled {unit}", timeout=15, check=False, label="is the unit enabled?"
        )
        active = sc.run(
            f"systemctl is-active {unit}", timeout=15, check=False, label="is the unit active?"
        )
        sc.check(enabled.stdout.strip() == "enabled", f"the unit is {enabled.stdout.strip()!r}")
        sc.check(active.stdout.strip() != "active", "adopting started the unit")
        history = last_deployment(sc, ADOPT_DOMAIN)
        sc.check(history["status"] == "success", f"the adoption's history row: {history!r}")

        commit_to(sc, repo, "printf 'v2\\n' > VERSION", "v2")
        sc.run(
            f"noust update {ADOPT_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {ADOPT_DOMAIN}",
        )
        wait_served(sc, ADOPT_DOMAIN, "web v2")
        projects = sc.run("docker compose ls -a -q", timeout=30, label="the Compose projects")
        sc.check(
            projects.stdout.split() == [ADOPT_PROJECT],
            f"the update made another project beside the adopted one: {projects.stdout!r}",
        )
        sc.check(
            sc.run(
                f"curl -sS http://127.0.0.1:{ADOPT_PORT}/", timeout=30, label="the stack directly"
            ).stdout.strip()
            == "web v2",
            "the adopted stack's own port does not serve v2",
        )
    finally:
        compose_clean(sc, ADOPT_DOMAIN)
        sc.run(
            f"cd {ADOPT_DIR} && docker compose -p {ADOPT_PROJECT} down -v; "
            f"rm -rf {ADOPT_DIR} /etc/nginx/sites-enabled/{ADOPT_SITE} {site_path}; "
            "nginx -s reload; true",
            timeout=120,
            check=False,
            label="cleanup: the operator's stack and site",
        )


OPSITE_DOMAIN = "opsite.test"
OPSITE_PORT = 18105

OPSITE_TEXT = """# My own site, written by hand: Noust did not generate it and must never rewrite it.
server {
    listen 80;
    server_name @DOMAIN@;
    add_header X-Operator "hand-written" always;
    location / {
        proxy_pass http://127.0.0.1:@PORT@;
        proxy_set_header Host $host;
    }
}
"""


@scenario("compose_operator_site_survives_update")
def scenario_compose_operator_site(sc: Scenario) -> None:
    """
    A site the operator wrote is left alone by an update, and by deleting the application.

    The site Noust wrote for a stack is replaced by one written by hand (no Noust marker). An
    update must leave the file byte for byte as it was, and the site keeps serving the new
    version with the operator's own header. Deleting the application leaves the file, and says so.
    """
    require_module(sc, "noust.deployers.helpers.site", what="the operator's site on Compose")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    repo, url = make_compose_repo(sc, "opsite-stack", "web", port=OPSITE_PORT)
    compose_create(sc, OPSITE_DOMAIN, url, port=OPSITE_PORT)
    site_path = f"/etc/nginx/sites-available/{OPSITE_DOMAIN}"
    text = OPSITE_TEXT.replace("@DOMAIN@", OPSITE_DOMAIN).replace("@PORT@", str(OPSITE_PORT))
    try:
        wait_served(sc, OPSITE_DOMAIN, "web v1")
        sc.run(
            f"cat > {site_path} <<'EOF'\n{text}EOF\nnginx -t && nginx -s reload",
            timeout=30,
            label=f"replace {site_path} with a site written by hand",
        )
        before = sc.run(f"sha256sum {site_path}", timeout=15, label="the site's checksum").stdout

        commit_to(sc, repo, "printf 'v2\\n' > VERSION", "v2")
        sc.run(
            f"noust update {OPSITE_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {OPSITE_DOMAIN}",
        )
        wait_served(sc, OPSITE_DOMAIN, "web v2")
        after = sc.run(
            f"sha256sum {site_path}", timeout=15, label="the site's checksum after"
        ).stdout
        sc.check(before == after, "the update rewrote a site Noust did not write")
        headers = curl_host(sc, OPSITE_DOMAIN, head=True)
        sc.check(
            "X-Operator: hand-written" in headers, f"the operator's header is gone: {headers!r}"
        )

        deleted = sc.run(
            f"noust delete {OPSITE_DOMAIN} -f",
            timeout=180,
            label=f"noust delete {OPSITE_DOMAIN} -f",
        )
        sc.check(
            sc.run(
                f"sha256sum {site_path}", timeout=15, check=False, label="the site after the delete"
            ).stdout
            == before,
            "deleting the application removed or changed the operator's site",
        )
        said = command_output(deleted).lower()
        sc.check(
            any(word in said for word in ("kept", "left", "operator")),
            "deleting the application did not say that it left the site",
        )
    finally:
        compose_clean(sc, OPSITE_DOMAIN)
        sc.run(
            f"rm -f /etc/nginx/sites-enabled/{OPSITE_DOMAIN} {site_path}; nginx -s reload; true",
            timeout=30,
            check=False,
            label="cleanup: the operator's site",
        )


TLS_DOMAIN = "tls.test"
TLS_PORT = 18106


def install_fake_certbot(sc: Scenario) -> None:
    """Put the self-signing certbot stand-in on PATH for this scenario (see :func:`remove_fake_certbot`)."""
    install_tools(sc)
    sc.run(
        f"ln -sf {CONTAINER_TOOLS}/fake_certbot.py /usr/local/bin/certbot && "
        f"chmod +x {CONTAINER_TOOLS}/fake_certbot.py",
        timeout=15,
        label="install the certbot stand-in that issues self-signed certificates",
    )


def remove_fake_certbot(sc: Scenario) -> None:
    """Take the stand-in and its certificates away: every other scenario runs without certbot."""
    sc.run(
        "rm -f /usr/local/bin/certbot; rm -rf /etc/letsencrypt; true",
        timeout=30,
        check=False,
        label="remove the certbot stand-in and its certificates",
    )


def tls_san(sc: Scenario, name: str) -> str:
    """The subjectAltName entries of the certificate nginx presents for ``name`` on :443."""
    return sc.run(
        f"openssl s_client -connect 127.0.0.1:443 -servername {name} </dev/null 2>/dev/null "
        "| openssl x509 -noout -ext subjectAltName",
        timeout=30,
        check=False,
        label=f"the certificate presented for {name}",
    ).stdout


def curl_tls(sc: Scenario, host: str, *, flags: str = "") -> str:
    """Ask nginx over TLS on loopback for ``/`` as ``host``, trusting the self-signed certificate."""
    return sc.run(
        f"curl -sSk {flags} --resolve {host}:443:127.0.0.1 https://{host}/",
        timeout=30,
        check=False,
        label=f"curl -k https://{host}/",
    ).stdout


@scenario("compose_alias_with_selfsigned_certificate")
def scenario_compose_alias_tls(sc: Scenario) -> None:
    """
    A Compose stack answers on an alias and redirects another name, over TLS, through an update.

    A certbot stand-in issues self-signed certificates (tools/fake_certbot.py), so Noust's whole
    certificate path runs without an ACME server: the stack is created with a certificate, an
    alias extends it to cover the new name, a redirect sends the third name to the primary over
    HTTPS, and an update keeps the aliases, the redirect and the certificate.
    """
    require_module(sc, "noust.deployers.helpers.site", what="aliases and certificates on Compose")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE)
    alias = "www.tls.test"
    redirect = "old-tls.test"
    repo, url = make_compose_repo(sc, "tls-stack", "web", port=TLS_PORT)
    install_fake_certbot(sc)
    try:
        compose_create(sc, TLS_DOMAIN, url, port=TLS_PORT, ssl=True)
        sc.check(
            curl_tls(sc, TLS_DOMAIN).strip() == "web v1",
            "the stack does not serve over TLS on its primary domain",
        )
        sc.run(
            f"noust domain add {TLS_DOMAIN} {alias} --kind alias",
            timeout=120,
            label=f"noust domain add {TLS_DOMAIN} {alias} --kind alias",
        )
        sc.run(
            f"noust domain add {TLS_DOMAIN} {redirect} --kind redirect",
            timeout=120,
            label=f"noust domain add {TLS_DOMAIN} {redirect} --kind redirect",
        )
        sc.check(
            f"DNS:{alias}" in tls_san(sc, alias),
            "the certificate was not extended to cover the alias",
        )
        sc.check(curl_tls(sc, alias).strip() == "web v1", f"{alias} does not serve the stack")
        moved = curl_tls(sc, redirect, flags="-o /dev/null -w '%{http_code} %{redirect_url}'")
        sc.check(
            moved.strip().startswith("301 ") and TLS_DOMAIN in moved,
            f"{redirect} did not redirect to {TLS_DOMAIN}: {moved!r}",
        )

        commit_to(sc, repo, "printf 'v2\\n' > VERSION", "v2")
        sc.run(
            f"noust update {TLS_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {TLS_DOMAIN}"
        )
        deadline = time.monotonic() + STACK_READY_TIMEOUT
        body = curl_tls(sc, alias).strip()
        while body != "web v2" and time.monotonic() < deadline:
            time.sleep(1)
            body = curl_tls(sc, alias).strip()
        sc.check(body == "web v2", f"the alias does not serve the update: {body!r}")
        sc.check(
            f"DNS:{alias}" in tls_san(sc, alias),
            "the update lost the alias from the certificate",
        )
        kinds = json_of(
            sc.run(f"noust domain list {TLS_DOMAIN} --json", timeout=30, label="noust domain list"),
            "noust domain list",
        )
        names = {item["domain"]: item["kind"] for item in kinds["items"]}
        sc.check(
            names == {TLS_DOMAIN: "primary", alias: "alias", redirect: "redirect"},
            f"the update changed the domains: {names!r}",
        )
        sc.run("nginx -t", timeout=15, label="nginx -t")
    finally:
        compose_clean(sc, TLS_DOMAIN)
        remove_fake_certbot(sc)


DB_DOMAIN = "dbstack.test"
DB_PORT = 18107

#: What the database scenarios put in the stack's PostgreSQL before an update.
DB_SEED_SQL = (
    "CREATE TABLE orders (id int PRIMARY KEY, quantity int); "
    "INSERT INTO orders VALUES (1, 10), (2, 20);"
)


def stack_psql(sc: Scenario, domain: str, sql: str, label: str) -> subprocess.CompletedProcess[str]:
    """Run SQL against the stack's PostgreSQL, as its own user, inside its container."""
    return compose_exec(sc, domain, "db", f'psql -U shop -d shop -tA -c "{sql}"', label)


@scenario("compose_database_copied_before_update_and_restored")
def scenario_compose_database_backup(sc: Scenario) -> None:
    """
    The stack's PostgreSQL is dumped before an update, and the dump brings the data back.

    A web service and an official `postgres:16-alpine` whose credentials sit in its environment,
    which is what Noust detects. Two rows go in; an update takes the pre-update backup with a
    dump of that database in it; the table is then dropped, the way a bad migration would;
    `noust backup restore` of that backup gives the rows back. The password never being on a
    command line is a property of how the dump is made, covered by tests/test_stack_databases.py.
    """
    require_module(sc, "noust.managers.stack_databases", what="the stack's database copies")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE, COMPOSE_DB_IMAGE)
    repo, url = make_compose_repo(sc, "db-stack", "web", "database", port=DB_PORT)
    compose_create(sc, DB_DOMAIN, url, port=DB_PORT)
    try:
        wait_served(sc, DB_DOMAIN, "web v1")
        stack_psql(sc, DB_DOMAIN, DB_SEED_SQL, "put two rows in the stack's database")
        count = stack_psql(sc, DB_DOMAIN, "SELECT count(*) FROM orders", "count them")
        sc.check(count.stdout.strip() == "2", f"the seed did not land: {count.stdout!r}")

        commit_to(sc, repo, "printf 'v2\\n' > VERSION", "v2")
        sc.run(
            f"noust update {DB_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {DB_DOMAIN}"
        )
        wait_served(sc, DB_DOMAIN, "web v2")

        listing = json_of(
            sc.run(f"noust backup list {DB_DOMAIN} --json", timeout=30, label="the backups"),
            "noust backup list",
        )
        backups = (
            listing
            if isinstance(listing, list)
            else listing.get("backups") or listing.get("items") or []
        )
        sc.check(bool(backups), "the update took no backup")
        backup_id = backups[0]["id"]
        info = json_of(
            sc.run(f"noust backup info {backup_id} --json", timeout=30, label="what it holds"),
            "noust backup info",
        )
        metadata = info.get("backup", info)
        sc.check(
            bool(metadata.get("database_backups")),
            f"the pre-update backup holds no database dump: {info!r}",
        )

        stack_psql(sc, DB_DOMAIN, "DROP TABLE orders", "drop the table, as a bad migration would")
        gone = stack_psql(sc, DB_DOMAIN, "SELECT count(*) FROM orders", "it is gone")
        sc.check(gone.returncode != 0, "the table is still there after the drop")

        sc.run(
            f"noust backup restore {backup_id} -f",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust backup restore {backup_id} -f",
        )
        deadline = time.monotonic() + 60
        rows = ""
        while rows != "2" and time.monotonic() < deadline:
            rows = stack_psql(
                sc, DB_DOMAIN, "SELECT count(*) FROM orders", "the rows"
            ).stdout.strip()
            if rows != "2":
                time.sleep(2)
        sc.check(rows == "2", f"the restore did not bring the rows back: {rows!r}")
    finally:
        compose_clean(sc, DB_DOMAIN)


DBFAIL_DOMAIN = "dbfail.test"
DBFAIL_PORT = 18110


@scenario("compose_database_copy_failure_stops_the_update")
def scenario_compose_database_copy_failure(sc: Scenario) -> None:
    """
    If the stack's database cannot be copied, the update stops before touching anything.

    noust.yaml declares a database that is not in the stack. The update fails with the copy's own
    error and says how to switch the copy off; the container that served is the same one, still on
    v1. `noust app backup-before-update off` is that switch, and with it the update goes through.
    """
    require_module(sc, "noust.deployers.helpers.project_file", what="noust.yaml")
    require_module(sc, "noust.managers.stack_databases", what="the stack's database copies")
    require_cli(sc, "app backup-before-update", what="turning the copy off")
    require_docker(sc)
    pull_images(sc, COMPOSE_BASE_IMAGE, COMPOSE_DB_IMAGE)
    repo, url = make_compose_repo(sc, "dbfail-stack", "web", "database", port=DBFAIL_PORT)
    commit_to(
        sc,
        repo,
        f"cp {COMPOSE_FIXTURES}/yaml/backup-missing-database.yaml noust.yaml",
        "declare a database that is not there",
    )
    compose_create(sc, DBFAIL_DOMAIN, url, port=DBFAIL_PORT)
    try:
        wait_served(sc, DBFAIL_DOMAIN, "web v1")
        serving = compose_ids(sc, DBFAIL_DOMAIN, "web")

        commit_to(sc, repo, "printf 'v2\\n' > VERSION", "v2")
        failed = sc.run(
            f"noust update {DBFAIL_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {DBFAIL_DOMAIN} (the copy fails)",
        )
        sc.check(failed.returncode != 0, "an update went ahead without the copy it needs")
        sc.check(
            "missing_database" in command_output(failed),
            "the error does not carry the database tool's own message",
        )
        sc.check(
            "backup-before-update" in command_output(failed),
            "the error does not say how to turn the copy off",
        )
        sc.check(
            compose_ids(sc, DBFAIL_DOMAIN, "web") == serving, "a container was recreated anyway"
        )
        sc.check(curl_host(sc, DBFAIL_DOMAIN).strip() == "web v1", "what served is not serving v1")

        sc.run(
            f"noust app backup-before-update {DBFAIL_DOMAIN} off",
            timeout=30,
            label=f"noust app backup-before-update {DBFAIL_DOMAIN} off",
        )
        sc.run(
            f"noust update {DBFAIL_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {DBFAIL_DOMAIN} (the copy is off)",
        )
        wait_served(sc, DBFAIL_DOMAIN, "web v2")
    finally:
        compose_clean(sc, DBFAIL_DOMAIN)


TAG_DOMAIN = "tag.test"
TAG_PORT = 3723


@scenario("deploy_by_tag")
def scenario_deploy_by_tag(sc: Scenario) -> None:
    """
    `noust update --tag` deploys exactly the commit a tag names, not the head of the branch.

    A Node app on releases, created at version 1 (tagged v1.0.0). The repository then gets
    version 2 (tagged v1.1.0) and version 3 (untagged, the head of main). Updating to
    v1.1.0 serves version 2 and records its commit; a tag that does not exist fails and changes
    nothing; the next plain update follows the branch again, to version 3.
    """
    require_cli(sc, "update", option="--tag", what="deploying a tag")
    repo, url = make_node_repo(sc, "tag-app")
    sc.run(
        f"cd {repo} && git tag -a v1.0.0 -m 'v1.0.0'", timeout=15, label="tag version 1 as v1.0.0"
    )
    create = f"noust create -d {TAG_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases --port {TAG_PORT}"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        sc.check(curl_host(sc, TAG_DOMAIN).strip() == "ok 1", "version 1 is not serving")
        tagged = commit_to(sc, repo, "echo 2 > VERSION", "version 2")
        sc.run(
            f"cd {repo} && git tag -a v1.1.0 -m 'v1.1.0'",
            timeout=15,
            label="tag version 2 as v1.1.0",
        )
        commit_to(sc, repo, "echo 3 > VERSION", "version 3: the head of main, untagged")

        sc.run(
            f"noust update {TAG_DOMAIN} --tag v1.1.0",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {TAG_DOMAIN} --tag v1.1.0",
        )
        sc.check(
            curl_host(sc, TAG_DOMAIN).strip() == "ok 2",
            "--tag v1.1.0 did not deploy the tagged commit (it deployed the head of the branch?)",
        )
        row = last_deployment(sc, TAG_DOMAIN)
        sc.check(
            (row["git_commit"] or "").startswith(tagged[:7]),
            f"the history does not name the tagged commit {tagged[:7]}: {row!r}",
        )

        release = active_release_of(sc, TAG_DOMAIN)
        missing = sc.run(
            f"noust update {TAG_DOMAIN} --tag v9.9.9",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {TAG_DOMAIN} --tag v9.9.9 (there is no such tag)",
        )
        sc.check(missing.returncode != 0, "a tag that does not exist was deployed")
        sc.check(
            active_release_of(sc, TAG_DOMAIN) == release, "the failed update changed the release"
        )
        sc.check(
            curl_host(sc, TAG_DOMAIN).strip() == "ok 2", "the failed update changed what serves"
        )

        sc.run(
            f"noust update {TAG_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {TAG_DOMAIN}"
        )
        sc.check(
            curl_host(sc, TAG_DOMAIN).strip() == "ok 3",
            "after a tag, the next update did not follow the branch again",
        )
    finally:
        sc.run(f"noust delete {TAG_DOMAIN} -f", timeout=180, check=False, label="cleanup")


# ---------------------------------------------------------------------------
# Proggest: a real production stack, locally (optional: --scenario proggest)
# ---------------------------------------------------------------------------

#: Proggest's repository on the host; NOUST_IT_PROGGEST names another checkout. Only its
#: committed files are used, through a clone the harness patches: never its .env, never its
#: backups, never its working tree, never its Docker objects (they live in the host's daemon,
#: which this harness never talks to).
PROGGEST_SOURCE = Path(os.environ.get("NOUST_IT_PROGGEST") or REPO_ROOT.parent / "proggest")

#: The files the harness adds to its clone, and copies into the container with the fixtures.
PROGGEST_PATCH = FIXTURES_DIR / "proggest-patch"
PROGGEST_PATCH_IN_CONTAINER = "/root/fixtures/proggest-patch"

PROGGEST_DOMAIN = "proggest.test"
PROGGEST_APP = app_name_of(PROGGEST_DOMAIN)
#: Where deploy.sh runs from on the server (its nginx site aliases /assets/ there).
PROGGEST_DIR = "/var/www/proggest"
PROGGEST_PROJECT = "proggest"
#: The operator's site file, named as on the server: not after the domain.
PROGGEST_SITE = "proggest"
PROGGEST_SITE_PATH = f"/etc/nginx/sites-available/{PROGGEST_SITE}"
PROGGEST_LIMITS = "/etc/nginx/conf.d/proggest-limits.conf"
PROGGEST_COMPOSE_FILE = "docker-compose.prod.yml"
PROGGEST_COMPOSE = f"docker compose -p {PROGGEST_PROJECT} -f {PROGGEST_COMPOSE_FILE}"
PROGGEST_REPO = "/root/fixtures/proggest"
PROGGEST_URL = "git://127.0.0.1/proggest"
PROGGEST_IMAGES = ("node:22-alpine", "postgres:16-alpine", "redis:7-alpine")
#: deploy.sh's own path to the CLI: `npx prisma` does not find it in the runtime image.
PROGGEST_PRISMA = "/app/node_modules/.pnpm/node_modules/.bin/prisma"
PROGGEST_MIGRATION = "20261002120000_noust_it_probe"
#: The pnpm monorepo builds two images with a 4 GB Node heap each; cold, with every package
#: downloaded, it takes a good part of an hour. The inner Docker's layer cache, on the data
#: volume, makes the retries minutes.
PROGGEST_BUILD_TIMEOUT = 5400
#: How long the stack gets to answer through nginx: the backend's healthcheck has a 40 s
#: start period in the compose file.
PROGGEST_READY_TIMEOUT = 300
#: deploy.sh's HEALTH_TIMEOUT, given to Noust's gate the same way an operator would.
PROGGEST_HEALTH_TIMEOUT = 120
PROGGEST_DRAIN = 5
#: Lines of a long command's log kept in the evidence; the whole log stays in the container.
PROGGEST_LOG_TAIL = 60

#: The two probes the relay runs under, each one request every PROBE_INTERVAL through nginx:
#: (name, path, the load generator's accepted leading digits, the exact status that counts).
PROGGEST_PROBES: tuple[tuple[str, str, str, int], ...] = (
    ("health", "/api/v1/health", "2", 200),
    ("root", "/", "3", 307),
)

#: Every https URL of proggest.es or one of its subdomains, in the compose file.
_PROGGEST_HTTPS = re.compile(r"https://((?:[a-z0-9-]+\.)*)proggest\.es")


@dataclass
class Ledger:
    """How long each step took, and the numbers worth reporting, for the scenario's summary."""

    laps: list[tuple[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @contextlib.contextmanager
    def lap(self, name: str) -> Iterator[None]:
        """Time a step, whether it passes or fails."""
        started = time.monotonic()
        try:
            yield
        finally:
            self.laps.append((name, time.monotonic() - started))

    def render(self) -> str:
        """The timings and the notes, one per line."""
        lines = ["[proggest] timings:"]
        lines += [f"  {seconds:8.1f}s  {name}" for name, seconds in self.laps]
        lines.append(f"  {sum(s for _, s in self.laps):8.1f}s  total")
        if self.notes:
            lines.append("[proggest] numbers:")
            lines += [f"  {note}" for note in self.notes]
        return "\n".join(lines)


def _replace_once(text: str, old: str, new: str, what: str) -> str:
    """Replace a fragment that must be there, so a change upstream fails here, loudly."""
    if old not in text:
        raise HarnessError(
            f"Proggest's {what} no longer contains {old!r}: adapt the harness's patch "
            "(tests/integration/run.py, proggest_compose_stack) to the new file"
        )
    return text.replace(old, new)


def proggest_compose(text: str) -> str:
    """
    Point Proggest's production compose file at the harness's local names.

    Every https URL of proggest.es and its subdomains becomes http on proggest.test (the build
    args NEXT_PUBLIC_API_URL, CORS_ORIGIN, PASSKEY_ORIGINS, FRONTEND_URL, NEXTAUTH_URL and
    PUBLIC_BASE_URL's default), the passkey RP ID and the cookie domain follow, and storage is
    local instead of S3. The Docker socket mount and its group_add stay: the stack runs as it
    does in production, and Noust is expected to warn about it and run it under the recorded
    compose exception.

    Args:
        text: docker-compose.prod.yml as committed.

    Returns:
        The patched file.

    Raises:
        HarnessError: A fragment the patch rewrites is gone, or a value outside a comment
            still names proggest.es.
    """
    text = _replace_once(
        text, "STORAGE_TYPE: ${STORAGE_TYPE:-s3}", "STORAGE_TYPE: local", "compose"
    )
    text = _replace_once(
        text, "PASSKEY_RP_ID: proggest.es", f"PASSKEY_RP_ID: {PROGGEST_DOMAIN}", "compose"
    )
    text = _replace_once(
        text,
        "AUTH_COOKIE_DOMAIN: .proggest.es",
        f"AUTH_COOKIE_DOMAIN: .{PROGGEST_DOMAIN}",
        "compose",
    )
    text, count = _PROGGEST_HTTPS.subn(r"http://\1proggest.test", text)
    if count < 5:
        raise HarnessError(f"Proggest's compose file names https://proggest.es {count} times")
    left = [
        line
        for line in text.splitlines()
        if "proggest.es" in line and not line.lstrip().startswith("#")
    ]
    if left:
        raise HarnessError("Proggest's compose file still names proggest.es:\n" + "\n".join(left))
    return text


def proggest_site(text: str, *, relay: bool) -> str:
    """
    Adapt Proggest's nginx site (infra/nginx/proggest.es) to the harness: plain HTTP on .test.

    The HTTP server that only redirects to HTTPS goes, and the HTTPS server listens on port 80
    without its certificate: there is no TLS here. Every location, upstream, limit and header
    stays as written. With ``relay``, each upstream includes Noust's servers file instead of
    naming its port, which is the change the relay asks of an operator's site.

    Args:
        text: The committed site.
        relay: Write the include lines.

    Returns:
        The adapted site.

    Raises:
        HarnessError: A fragment the adaptation rewrites is gone.
    """
    start = text.find("# HTTP server (redirect to HTTPS)")
    end = text.find("# HTTPS server")
    if start < 0 or end < start:
        raise HarnessError("Proggest's site no longer has its HTTP and HTTPS servers in order")
    text = text[:start] + text[end:]
    text = _replace_once(
        text,
        "# HTTPS server",
        "# Served on port 80: the harness has no certificate, TLS is not validated locally",
        "site",
    )
    text = _replace_once(
        text,
        "    listen 443 ssl;\n    http2 on;\n    listen [::]:443 ssl;\n",
        "    listen 80;\n    listen [::]:80;\n",
        "site",
    )
    text, removed = re.subn(r"(?m)^    ssl_[^\n]*\n", "", text)
    if removed < 5:
        raise HarnessError(f"Proggest's site has {removed} ssl_ lines, expected its TLS block")
    text = _replace_once(text, "proggest.es", PROGGEST_DOMAIN, "site")
    for upstream, port, service in (
        ("nextjs_upstream", 3001, "frontend"),
        ("nestjs_upstream", 3000, "backend"),
    ):
        pinned = f"server 127.0.0.1:{port};"
        own = f"include /etc/nginx/noust-upstreams/{PROGGEST_APP}/{service}.servers;"
        # Proggest's site includes Noust's servers file since it was deployed with the relay
        # (2026-10-03); before, it named the port. The harness needs both: the one with the port is
        # the operator's site before the relay (the relay refuses it and gives the line to add),
        # the one with the include is what they then install.
        committed = f"include /etc/nginx/noust-upstreams/proggest-es/{service}.servers;"
        if committed in text:
            text = _replace_once(text, committed, own if relay else pinned, "site")
        else:
            text = _replace_once(
                text,
                f"upstream {upstream} {{\n    {pinned}",
                f"upstream {upstream} {{\n    {own if relay else pinned}",
                "site",
            )
    header = (
        "# Proggest's infra/nginx/proggest.es, adapted by Noust's integration harness: "
        f"{PROGGEST_DOMAIN}, plain HTTP.\n"
    )
    return header + text


def prepare_proggest_clone(sc: Scenario) -> str:
    """
    Clone Proggest, patch only the clone, commit there, and serve it from the harness's git.

    ``git clone --local`` of the committed tree into a temporary directory on the host; the
    compose file and a ``.test`` copy of the nginx site patched, ``noust.yaml`` added, one
    commit; then a one-commit clone of that is what goes into the container and git daemon
    serves. The source repository is only read.

    Args:
        sc: The scenario.

    Returns:
        The adapted site as it is before the relay (upstreams naming their ports), which is
        how the operator's server has it.
    """
    with tempfile.TemporaryDirectory(prefix="noust-it-proggest-") as tmp:
        clone = Path(tmp) / "clone"
        sh(["git", "clone", "--local", "-q", str(PROGGEST_SOURCE), str(clone)], timeout=300)
        compose = clone / PROGGEST_COMPOSE_FILE
        compose.write_text(proggest_compose(compose.read_text(encoding="utf-8")), encoding="utf-8")
        original = (clone / "infra" / "nginx" / "proggest.es").read_text(encoding="utf-8")
        (clone / "infra" / "nginx" / PROGGEST_DOMAIN).write_text(
            proggest_site(original, relay=True), encoding="utf-8"
        )
        shutil.copyfile(PROGGEST_PATCH / "noust.yaml", clone / "noust.yaml")
        git = ["git", "-C", str(clone), "-c", "core.hooksPath=/dev/null"]
        sh([*git, "add", "-A"], timeout=60)
        sh(
            [
                *git,
                "-c",
                "user.email=noust-it@example.com",
                "-c",
                "user.name=Noust Integration",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                f"noust-it: local values ({PROGGEST_DOMAIN}, plain HTTP, local storage), "
                "noust.yaml, the site with Noust's servers files",
            ],
            timeout=60,
        )
        served = Path(tmp) / "proggest"
        sh(["git", "clone", "-q", "--depth", "1", f"file://{clone}", str(served)], timeout=300)
        sh(["git", "-C", str(served), "remote", "remove", "origin"], timeout=30)
        sc.run(f"rm -rf {PROGGEST_REPO}", timeout=60, label=f"rm -rf {PROGGEST_REPO}")
        sh(["docker", "cp", str(served), f"{sc.container}:{PROGGEST_REPO}"], timeout=600)
    sc.run(
        f"chown -R root:root {PROGGEST_REPO} && cd {PROGGEST_REPO} && {GIT_IDENTITY} && "
        f"git log --oneline -1 && git show --stat --format= HEAD | tail -1 && "
        f"git ls-remote --exit-code {PROGGEST_URL} HEAD",
        timeout=60,
        label=f"(fixture repo) {PROGGEST_REPO}: a clean clone of {PROGGEST_SOURCE}, patched and "
        f"committed, served at {PROGGEST_URL}",
    )
    return proggest_site(original, relay=False)


def proggest_env(docker_gid: str) -> str:
    """
    The stack's test ``.env``: generated values, never the repository's own.

    The 64-hex keys are what ``openssl rand -hex 32`` gives, the passwords are hex so they go
    into DATABASE_URL as they are. NEXT_SERVER_ACTIONS_ENCRYPTION_KEY is a build argument of
    the frontend: it is derived from a fixed string, so the Docker layer cache holds across
    runs instead of the frontend being rebuilt from scratch every time.

    Args:
        docker_gid: The gid of the docker group, for the backend's group_add.

    Returns:
        The file's text.
    """
    server_actions = base64.b64encode(
        hashlib.sha256(b"noust-it proggest server actions").digest()
    ).decode()
    values = {
        "DB_PASSWORD": secrets.token_hex(16),
        "REDIS_PASSWORD": secrets.token_hex(16),
        "JWT_SECRET": secrets.token_hex(32),
        "AUTH_SECRET": secrets.token_hex(32),
        "INTERNAL_API_KEY": secrets.token_hex(32),
        "NEXT_SERVER_ACTIONS_ENCRYPTION_KEY": server_actions,
        "BULL_BOARD_USER": "admin",
        "BULL_BOARD_PASSWORD": secrets.token_hex(16),
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": "2525",
        "SMTP_USER": "",
        "SMTP_PASS": "",
        "MAIL_FROM": f"noreply@{PROGGEST_DOMAIN}",
        "TWO_FACTOR_ENCRYPTION_KEY": secrets.token_hex(32),
        "INTEGRATION_ENCRYPTION_KEY": secrets.token_hex(32),
        "PERSONNEL_ENCRYPTION_KEY": secrets.token_hex(32),
        "STORAGE_TYPE": "local",
        "PUBLIC_BASE_URL": f"http://{PROGGEST_DOMAIN}",
        "DOCKER_GID": docker_gid,
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


def logged(
    sc: Scenario, command: str, *, log: str, label: str, timeout: int, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """
    Run a long command with its output in a file in the container, keeping its end as evidence.

    A pnpm build prints tens of thousands of lines; the whole log stays at ``log`` for whoever
    keeps the container (``--keep``), and the evidence gets its last lines.

    Args:
        sc: The scenario.
        command: The command.
        log: Where its output goes, in the container.
        label: What it is, for the evidence.
        timeout: Seconds it may take.
        check: Fail the scenario when it fails.

    Returns:
        The finished command, its stdout being the end of the log.
    """
    return sc.run(
        f"( {command} ) > {log} 2>&1; rc=$?; tail -n {PROGGEST_LOG_TAIL} {log}; exit $rc",
        timeout=timeout,
        check=check,
        label=f"{label}  [full output: {log}]",
    )


def proggest_status(sc: Scenario, path: str) -> str:
    """The status nginx answers for a path of the stack's site: ``000`` when nothing answers."""
    return docker_exec(
        sc.container,
        f"curl -s -o /dev/null -m 10 -w '%{{http_code}}' -H 'Host: {PROGGEST_DOMAIN}' "
        f"'http://127.0.0.1{path}'",
        timeout=30,
        check=False,
    ).stdout.strip()


def wait_proggest(sc: Scenario, path: str, expected: int) -> None:
    """Wait until the site answers a path with a status, and fail when it never does."""
    deadline = time.monotonic() + PROGGEST_READY_TIMEOUT
    status = ""
    while time.monotonic() < deadline:
        status = proggest_status(sc, path)
        if status == str(expected):
            break
        time.sleep(3)
    sc.evidence.append(f"$ (until it answers) GET {path} as {PROGGEST_DOMAIN}\n{status}")
    sc.check(status == str(expected), f"{path} answers {status}, not {expected}")


def proggest_psql(sc: Scenario, sql: str, label: str) -> str:
    """Run SQL against the stack's PostgreSQL as deploy.sh does, and return what it printed.

    The SQL goes in on stdin, so its quotes never meet the shell's.
    """
    return sc.run(
        "docker exec -i proggest-postgres sh -c "
        "'PGPASSWORD=$POSTGRES_PASSWORD psql -v ON_ERROR_STOP=1 -U $POSTGRES_USER "
        f"-d $POSTGRES_DB -tA' <<'EOF'\n{sql};\nEOF",
        timeout=60,
        label=f"{label}: {sql}",
    ).stdout.strip()


def proggest_containers(sc: Scenario, label: str) -> dict[str, str]:
    """Each container of the project by name, with its id and start time."""
    proc = sc.run(
        "docker inspect -f '{{.Name}} {{.Id}} {{.State.StartedAt}}' "
        f"$(docker ps -q -f label=com.docker.compose.project={PROGGEST_PROJECT})",
        timeout=30,
        label=label,
    )
    found: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        name, _, rest = line.strip().lstrip("/").partition(" ")
        if name:
            found[name] = rest
    return found


def proggest_servers(sc: Scenario, label: str) -> dict[str, str]:
    """What each of the stack's servers files names."""
    found: dict[str, str] = {}
    for service in ("backend", "frontend"):
        found[service] = sc.run(
            f"cat /etc/nginx/noust-upstreams/{PROGGEST_APP}/{service}.servers 2>/dev/null",
            timeout=15,
            check=False,
            label=f"{label}: {service}.servers",
        ).stdout.strip()
    return found


def proggest_backups(sc: Scenario, label: str) -> list[dict[str, Any]]:
    """The stack's backups, as ``noust backup list --json`` gives them."""
    listing = json_of(
        sc.run(f"noust backup list {PROGGEST_DOMAIN} --json", timeout=60, label=label),
        "noust backup list",
    )
    backups: list[dict[str, Any]] = (
        listing if isinstance(listing, list) else listing.get("backups") or listing.get("items")
    ) or []
    return backups


def proggest_pre_update_backup(sc: Scenario, before: set[str], update: str, ledger: Ledger) -> str:
    """
    Find the backup an update took, and check it holds a dump of the stack's PostgreSQL.

    Args:
        sc: The scenario.
        before: The backup ids there were before the update.
        update: Which update, for the messages.
        ledger: Where its size goes.

    Returns:
        Its id.
    """
    new = [b for b in proggest_backups(sc, f"the backups after {update}") if b["id"] not in before]
    sc.check(len(new) == 1, f"{update} took {len(new)} backups, not one: {new!r}")
    backup_id = str(new[0]["id"])
    info = json_of(
        sc.run(
            f"noust backup info {backup_id} --json", timeout=60, label=f"what {backup_id} holds"
        ),
        "noust backup info",
    )
    metadata = info.get("backup", info)
    dumps = metadata.get("database_backups") or []
    sc.check(bool(dumps), f"the backup before {update} holds no database dump: {info!r}")
    ledger.notes.append(
        f"pre-update backup before {update}: {backup_id}, "
        f"{metadata.get('size_bytes', '?')} bytes, dumps: " + json.dumps(dumps)[:300]
    )
    return backup_id


def start_proggest_probes(sc: Scenario) -> dict[str, int]:
    """
    Start one probe per path, one request through nginx every PROBE_INTERVAL seconds.

    Returns:
        Each probe's log line count once it runs: the first phase's start.
    """
    install_tools(sc)
    for name, path, accept, _ in PROGGEST_PROBES:
        unit, log = f"noust-it-pg-{name}", f"/root/pg-probe-{name}.log"
        sc.run(
            f"systemctl stop {unit} 2>/dev/null; systemctl reset-failed {unit} 2>/dev/null; "
            f"rm -f {log}; systemd-run --unit {unit} --collect /usr/bin/python3 "
            f"{CONTAINER_TOOLS}/load.py {PROGGEST_DOMAIN} {log} {PROBE_INTERVAL} {path} {accept}",
            timeout=30,
            label=f"start the probe: GET {path} as {PROGGEST_DOMAIN} through nginx every "
            f"{int(PROBE_INTERVAL * 1000)} ms",
        )
    deadline = time.time() + 30
    marks: dict[str, int] = {}
    while time.time() < deadline:
        marks = {
            name: int(
                docker_exec(
                    sc.container, f"wc -l < /root/pg-probe-{name}.log 2>/dev/null || echo 0"
                ).stdout.strip()
                or 0
            )
            for name, *_ in PROGGEST_PROBES
        }
        if all(count >= 10 for count in marks.values()):
            return marks
        time.sleep(0.5)
    raise AssertionError(f"the probes are not writing their logs: {marks!r}")


def stop_proggest_probes(sc: Scenario) -> None:
    """Stop the probes, whether or not they run."""
    units = " ".join(f"noust-it-pg-{name}" for name, *_ in PROGGEST_PROBES)
    sc.run(f"systemctl stop {units}; true", timeout=30, check=False, label="stop the probes")


def proggest_probe_phase(
    sc: Scenario, phase: str, marks: dict[str, int], ledger: Ledger, *, minimum: int = 20
) -> dict[str, int]:
    """
    Judge what each probe saw since its mark: every answer must be its exact expected status.

    Args:
        sc: The scenario.
        phase: What was happening.
        marks: Where each probe's phase starts.
        ledger: Where the counts go.
        minimum: The fewest requests per probe for the phase's silence to mean something.

    Returns:
        Where the next phase starts.
    """
    time.sleep(1)
    after: dict[str, int] = {}
    problems: list[str] = []
    for name, path, _, expected in PROGGEST_PROBES:
        text = docker_exec(
            sc.container, f"tail -n +{marks[name] + 1} /root/pg-probe-{name}.log", timeout=30
        ).stdout
        lines = text[: text.rfind("\n") + 1].splitlines()
        statuses: dict[str, int] = {}
        failed = []
        for line in lines:
            parts = line.split(" ", 3)
            status = parts[2] if len(parts) > 2 else "?"
            statuses[status] = statuses.get(status, 0) + 1
            if not line.startswith("OK ") or status != str(expected):
                failed.append(line)
        seconds = (
            float(lines[-1].split(" ", 2)[1]) - float(lines[0].split(" ", 2)[1]) if lines else 0.0
        )
        summary = (
            f"{phase}: GET {path} {len(lines)} requests over {seconds:.1f}s, "
            f"{len(failed)} not {expected}; statuses {statuses}"
        )
        ledger.notes.append(summary)
        sc.evidence.append(f"[probe] {summary}" + "".join(f"\n  {line}" for line in failed[:30]))
        running = docker_exec(sc.container, f"systemctl is-active noust-it-pg-{name}", check=False)
        if running.stdout.strip() != "active":
            problems.append(f"the {path} probe died during {phase}")
        if len(lines) < minimum:
            problems.append(f"{phase}: only {len(lines)} requests to {path}")
        if failed:
            problems.append(f"{phase}: {len(failed)} of {len(lines)} requests to {path} failed")
        after[name] = marks[name] + len(lines)
    sc.check(not problems, "; ".join(problems))
    return after


def proggest_update(
    sc: Scenario, ledger: Ledger, number: int, what: str, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``noust -v update`` on the stack, timed, its output in a log of its own.

    Verbose, because the relay's steps (which container starts where, when nginx moves) are
    substeps, and they are the evidence that each service was relayed.
    """
    with ledger.lap(f"noust update #{number}: {what}"):
        return logged(
            sc,
            f"noust -v update {PROGGEST_DOMAIN}",
            log=f"/root/pg-update-{number}.log",
            label=f"noust -v update {PROGGEST_DOMAIN}  ({what})",
            timeout=PROGGEST_BUILD_TIMEOUT,
            check=check,
        )


def in_log(sc: Scenario, log: str, text: str, label: str) -> bool:
    """Whether a log in the container holds a fixed string."""
    return (
        sc.run(f"grep -F -m 3 -- '{text}' {log}", timeout=30, check=False, label=label).returncode
        == 0
    )


def no_relay_left(sc: Scenario, label: str) -> None:
    """Fail when a relay container exists."""
    left = sc.run(
        "docker ps -a --format '{{.Names}}' | grep -- '-relay$' || true", timeout=30, label=label
    ).stdout.strip()
    sc.check(not left, f"a relay container was left behind: {left!r}")


@scenario("proggest_compose_stack")
def scenario_proggest_compose_stack(sc: Scenario) -> None:
    """
    Proggest's production stack, brought up the way deploy.sh does, adopted and updated by Noust.

    A clean clone of Proggest (its committed files only), patched in the clone to local values
    and served by the harness's git: postgres, redis, a NestJS backend and a Next.js frontend
    built from a pnpm monorepo, and its own hand-written nginx site with its own upstream names
    and rate limits. Then: adopted with a clean `--dry-run`; the relay switched on (first
    refused with the exact include line its site lacks, then accepted once the site has it);
    two updates (a trivial one, then one with an additive Prisma migration) under two probes
    every 0.2 s through nginx (/api/v1/health must answer 200 and / 307, every time), each
    with a pre-update PostgreSQL dump and the migrations run by the noust.yaml hook; a backend
    that cannot start refused at its relay with the old one serving; a rollback past the
    migration refused until confirmed; the site's structure and the route of
    /api/v1/auth/login from the console's API; and `noust delete` keeping the directory.
    """
    require_cli(sc, "app adopt", what="adopting a running stack")
    require_cli(sc, "app hooks", what="deploy hooks (noust.yaml)")
    require_module(sc, "noust.deployers.compose_relay", what="the Compose relay")
    require_module(sc, "noust.managers.stack_databases", what="the stack's database copies")
    require_docker(sc)
    if not (PROGGEST_SOURCE / ".git").exists():
        raise ScenarioSkipped(
            f"no Proggest checkout at {PROGGEST_SOURCE} (NOUST_IT_PROGGEST names another)"
        )
    ledger = Ledger()
    try:
        with ledger.lap("pull the base images"):
            pull_images(sc, *PROGGEST_IMAGES)
        with ledger.lap("clone Proggest, patch and commit the clone, serve it"):
            plain_site = prepare_proggest_clone(sc)
        _proggest_by_hand(sc, ledger, plain_site)
        _proggest_adopt(sc, ledger)
        _proggest_relay_on(sc, ledger)
        marks = start_proggest_probes(sc)
        marks, before_migration = _proggest_updates(sc, ledger, marks)
        marks = _proggest_broken(sc, ledger, marks)
        marks = _proggest_rollback_refused(sc, ledger, marks, before_migration)
        stop_proggest_probes(sc)
        _proggest_site_api(sc, ledger)
        _proggest_sizes(sc, ledger)
        _proggest_delete(sc, ledger)
    except AssertionError:
        journal_tail(sc, f"-u {PROGGEST_APP} -u 'noust*' -u nginx", "journalctl (on failure)")
        sc.run(
            f"cd {PROGGEST_DIR} && {PROGGEST_COMPOSE} ps -a; "
            f"{PROGGEST_COMPOSE} logs --tail 40 --no-color backend frontend",
            timeout=60,
            check=False,
            label="the stack's containers and their last lines (on failure)",
        )
        raise
    finally:
        stop_proggest_probes(sc)
        sc.run(
            f"noust web stop; noust delete {PROGGEST_DOMAIN} -f 2>/dev/null; "
            f"cd {PROGGEST_DIR} 2>/dev/null && {PROGGEST_COMPOSE} down -v --remove-orphans; "
            "docker ps -aq -f name=-relay | xargs -r docker rm -f; "
            f"rm -rf {PROGGEST_DIR} {PROGGEST_SITE_PATH} /etc/nginx/sites-enabled/{PROGGEST_SITE} "
            f"{PROGGEST_LIMITS} /etc/nginx/noust-upstreams/{PROGGEST_APP}; nginx -s reload; true",
            timeout=300,
            check=False,
            label="cleanup: the application, the stack and its volumes (its images stay: they "
            "are the next run's cache), the site",
        )
        report = ledger.render()
        sc.evidence.append(report)
        print(report)


def _proggest_by_hand(sc: Scenario, ledger: Ledger, plain_site: str) -> None:
    """Step 1: the stack and its site, the way deploy.sh and the operator left them."""
    gid = docker_exec(
        sc.container, "getent group docker | cut -d: -f3", timeout=15, check=False
    ).stdout.strip()
    sc.run(
        f"rm -rf {PROGGEST_DIR} && git clone -q {PROGGEST_URL} {PROGGEST_DIR} && "
        f"git -C {PROGGEST_DIR} log --oneline -1",
        timeout=300,
        label=f"the operator's checkout: git clone {PROGGEST_URL} {PROGGEST_DIR}",
    )
    sc.run(
        f"umask 077 && cat > {PROGGEST_DIR}/.env <<'EOF'\n{proggest_env(gid or '999')}EOF\n"
        f"git -C {PROGGEST_DIR} status --short",
        timeout=30,
        label=f"write {PROGGEST_DIR}/.env with generated test values (withheld); git status",
    )
    sc.run(
        f"cp {PROGGEST_PATCH_IN_CONTAINER}/limits.conf {PROGGEST_LIMITS}",
        timeout=15,
        label=f"the rate-limit zones the site uses, in {PROGGEST_LIMITS}",
    )
    with ledger.lap("docker compose build (backend + frontend, as deploy.sh)"):
        logged(
            sc,
            f"cd {PROGGEST_DIR} && {PROGGEST_COMPOSE} build",
            log="/root/pg-build.log",
            label=f"cd {PROGGEST_DIR} && {PROGGEST_COMPOSE} build",
            timeout=PROGGEST_BUILD_TIMEOUT,
        )
    with ledger.lap("start postgres and redis"):
        sc.run(
            f"cd {PROGGEST_DIR} && {PROGGEST_COMPOSE} up -d --wait postgres redis",
            timeout=300,
            label=f"{PROGGEST_COMPOSE} up -d --wait postgres redis",
        )
    # Proggest's history does not replay cleanly on an empty database, and its schema cannot be
    # pushed either: a column the migrations make as a generated one is a `dbgenerated` default in
    # schema.prisma that names another column, which PostgreSQL refuses in a DEFAULT. So the
    # committed SQL is replayed statement by statement with the errors counted, not stopped on
    # (one migration drops an index only production ever had, another checks counts of rows that
    # only production holds), and every migration is then marked applied the way Prisma
    # documents a baseline, so deploy.sh's own step runs on a database that is up to date.
    postgres_exec = (
        f"docker compose -p {PROGGEST_PROJECT} -f {PROGGEST_DIR}/{PROGGEST_COMPOSE_FILE} "
        "exec -T postgres sh -c "
        '\'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=0\''
    )
    replay = (
        f"cd {PROGGEST_DIR}/packages/database/prisma/migrations && errors=0 && "
        "for m in $(ls | grep '^[0-9]'); do "
        f"n=$({postgres_exec} < $m/migration.sql 2>&1 >/dev/null | grep -c ERROR || true); "
        '[ "$n" = 0 ] || { echo "$m: $n error(s)"; errors=$((errors+n)); }; '
        'done; echo "migrations replayed, $errors statement(s) refused"'
    )
    with ledger.lap("replay the committed migrations on the empty database"):
        logged(
            sc,
            replay,
            log="/root/pg-replay.log",
            label="replay every committed migration's SQL on the empty database, counting the "
            "statements PostgreSQL refuses",
            timeout=900,
        )
    baseline = (
        f"B={PROGGEST_PRISMA} && for m in $(ls prisma/migrations | grep ^[0-9]); "
        "do $B migrate resolve --applied $m >/dev/null || exit 1; done"
    )
    with ledger.lap("mark every migration applied (migrate resolve --applied)"):
        logged(
            sc,
            f"cd {PROGGEST_DIR} && {PROGGEST_COMPOSE} run --rm --no-deps --entrypoint sh "
            f"backend -c 'cd /app && {baseline}'",
            log="/root/pg-baseline.log",
            label="mark every committed migration applied: prisma migrate resolve --applied",
            timeout=900,
        )
    with ledger.lap("migrations, as deploy.sh runs them"):
        logged(
            sc,
            f"cd {PROGGEST_DIR} && {PROGGEST_COMPOSE} run --rm --no-deps --entrypoint sh "
            f"backend -c 'cd /app && {PROGGEST_PRISMA} migrate deploy'",
            log="/root/pg-migrate.log",
            label=f"{PROGGEST_COMPOSE} run --rm --no-deps --entrypoint sh backend -c "
            f"'cd /app && {PROGGEST_PRISMA} migrate deploy'",
            timeout=900,
        )
    with ledger.lap("docker compose up -d"):
        sc.run(
            f"cd {PROGGEST_DIR} && {PROGGEST_COMPOSE} up -d",
            timeout=600,
            label=f"{PROGGEST_COMPOSE} up -d",
        )
    sc.run(
        f"cat > {PROGGEST_SITE_PATH} <<'EOF'\n{plain_site}EOF\n"
        f"ln -sf {PROGGEST_SITE_PATH} /etc/nginx/sites-enabled/{PROGGEST_SITE} && "
        "nginx -t && nginx -s reload",
        timeout=30,
        label=f"the operator's site, {PROGGEST_SITE_PATH} (upstreams naming 3000 and 3001)",
    )
    with ledger.lap("until /api/v1/health is 200 and / is 307 through nginx"):
        wait_proggest(sc, "/api/v1/health", 200)
        wait_proggest(sc, "/", 307)


def _proggest_adopt(sc: Scenario, ledger: Ledger) -> None:
    """Step 1, continued: `noust app adopt`, rehearsed first; nothing it finds is touched."""
    before = proggest_containers(sc, "the stack's containers before adopting")
    sc.check(len(before) == 4, f"the stack does not run four containers: {before!r}")
    site_sum = sc.run(f"sha256sum {PROGGEST_SITE_PATH}", timeout=15, label="the site's checksum")
    adopt = (
        f"app adopt {PROGGEST_DOMAIN} --path {PROGGEST_DIR} --compose-file {PROGGEST_COMPOSE_FILE}"
    )
    # A --dry-run never creates the store, and refuses without one: on a server where Noust
    # has never run a command, the rehearsal would fail for that alone.
    sc.run("noust list", timeout=60, label="noust list (the store exists, as on any server)")
    with ledger.lap("noust --dry-run app adopt"):
        rehearsal = sc.run(
            f"noust --dry-run {adopt} --json", timeout=300, label=f"noust --dry-run {adopt} --json"
        )
    plan = json_of(rehearsal, "noust --dry-run app adopt")
    sc.check(plan.get("adopted") is False, f"the rehearsal adopted: {plan!r}")
    sc.check(plan.get("changes") == [], f"the rehearsal is not clean: {plan.get('changes')!r}")
    sc.check(plan.get("project") == PROGGEST_PROJECT, f"project {plan.get('project')!r}")
    sc.check(plan.get("site") == PROGGEST_SITE, f"site {plan.get('site')!r}")
    sc.check(plan.get("port") == 3001, f"the port in front is {plan.get('port')!r}, not 3001")
    ledger.notes.append(
        f"adopt --dry-run: changes {plan.get('changes')}, warnings {plan.get('warnings')}"
    )
    sc.check(
        not store_rows(
            sc,
            "SELECT id FROM apps WHERE domain = @domain",
            domain=PROGGEST_DOMAIN,
            label="the rehearsal registered nothing",
        ),
        "the rehearsal registered the application",
    )
    with ledger.lap("noust app adopt"):
        sc.run(f"noust {adopt} -y", timeout=300, label=f"noust {adopt} -y")
    sc.check(
        proggest_containers(sc, "the stack's containers after adopting") == before,
        "adopting touched the containers",
    )
    sc.check(
        sc.run(f"sha256sum {PROGGEST_SITE_PATH}", timeout=15, label="its checksum after").stdout
        == site_sum.stdout,
        "adopting rewrote the operator's site",
    )
    row = store_rows(
        sc,
        "SELECT layout, compose_project, site_name, port, app_path FROM apps "
        "WHERE domain = @domain",
        domain=PROGGEST_DOMAIN,
        label="the adopted application's row",
    )[0]
    sc.check(
        row
        == {
            "layout": "inplace",
            "compose_project": PROGGEST_PROJECT,
            "site_name": PROGGEST_SITE,
            "port": 3001,
            "app_path": PROGGEST_DIR,
        },
        f"the adopted row: {row!r}",
    )


def _proggest_relay_on(sc: Scenario, ledger: Ledger) -> None:
    """Step 2: `noust app zero-downtime on`, refused with the line the site lacks, then on."""
    sc.run(
        f"noust app sandbox compose-exception {PROGGEST_DOMAIN} -y --reason "
        "'the backend reads container state through the Docker socket (super-admin system page)'",
        timeout=60,
        label=f"noust app sandbox compose-exception {PROGGEST_DOMAIN} --reason '...'",
    )
    sc.run(
        f"noust app health {PROGGEST_DOMAIN} --timeout {PROGGEST_HEALTH_TIMEOUT}",
        timeout=60,
        label=f"noust app health {PROGGEST_DOMAIN} --timeout {PROGGEST_HEALTH_TIMEOUT} "
        "(deploy.sh's HEALTH_TIMEOUT)",
    )
    command = f"noust app zero-downtime {PROGGEST_DOMAIN} on --drain {PROGGEST_DRAIN}"
    refused = sc.run(command, timeout=120, check=False, label=f"{command}  (site without include)")
    line = f"include /etc/nginx/noust-upstreams/{PROGGEST_APP}/backend.servers;"
    sc.check(refused.returncode != 0, "the relay was turned on for a site that cannot use it")
    sc.check(line in command_output(refused), f"the refusal does not give the line {line!r}")
    with ledger.lap("the operator installs the site with the include lines"):
        sc.run(
            f"cp {PROGGEST_DIR}/infra/nginx/{PROGGEST_DOMAIN} {PROGGEST_SITE_PATH} && "
            "nginx -t && nginx -s reload",
            timeout=30,
            label=f"cp {PROGGEST_DIR}/infra/nginx/{PROGGEST_DOMAIN} {PROGGEST_SITE_PATH}; reload",
        )
    with ledger.lap("noust app zero-downtime on"):
        sc.run(command, timeout=120, label=command)
    servers = proggest_servers(sc, "the servers files")
    sc.check(
        servers == {"backend": "server 127.0.0.1:3000;", "frontend": "server 127.0.0.1:3001;"},
        f"the servers files: {servers!r}",
    )
    sc.check(
        sc.run(
            f"cmp {PROGGEST_DIR}/infra/nginx/{PROGGEST_DOMAIN} {PROGGEST_SITE_PATH}",
            timeout=15,
            check=False,
            label="the operator's site is unchanged by switching the relay on",
        ).returncode
        == 0,
        "switching the relay on rewrote the operator's site",
    )
    wait_proggest(sc, "/api/v1/health", 200)


def _proggest_check_update(sc: Scenario, number: int, revision: int) -> dict[str, Any]:
    """What every good update leaves: success, the hook, the relays gone, the new images."""
    log = f"/root/pg-update-{number}.log"
    row = last_deployment(sc, PROGGEST_DOMAIN)
    sc.check(row["status"] == "success", f"update #{number}'s row: {row!r}")
    sc.check("migrate deploy" in (row["hooks"] or ""), f"the hook did not run: {row!r}")
    for service in ("backend", "frontend"):
        sc.check(
            in_log(sc, log, f"proggest-{service}-relay", f"update #{number} relayed {service}"),
            f"update #{number} did not relay {service}",
        )
        value = sc.run(
            "docker inspect -f '{{index .Config.Labels \"noust-it.revision\"}}' "
            f"proggest-{service}",
            timeout=30,
            check=False,
            label=f"the revision of the image proggest-{service} runs",
        ).stdout.strip()
        sc.check(value == str(revision), f"proggest-{service} runs revision {value!r}")
    sc.check(
        in_log(sc, log, f"allowed for {PROGGEST_DOMAIN} by", "the compose exception is named"),
        "the Docker socket mount was not reported as allowed by the recorded exception",
    )
    no_relay_left(sc, f"no relay container is left after update #{number}")
    servers = proggest_servers(sc, f"the servers files after update #{number}")
    sc.check(
        servers == {"backend": "server 127.0.0.1:3000;", "frontend": "server 127.0.0.1:3001;"},
        f"the servers files were not given back to the stack's ports: {servers!r}",
    )
    return row


def _proggest_updates(
    sc: Scenario, ledger: Ledger, marks: dict[str, int]
) -> tuple[dict[str, int], str]:
    """Step 3: a trivial update, then one with an additive migration, under the probes."""
    marks = proggest_probe_phase(sc, "idle, before the first update", marks, ledger, minimum=1)
    # A label on both images: new images, every layer from the cache. Anything in the build
    # context would rebuild both from `COPY . .` on (apps/*/Dockerfile included: Proggest's
    # .dockerignore leaves out only the root Dockerfile).
    commit_to(
        sc,
        PROGGEST_REPO,
        "sed -i 's#^      dockerfile: apps/\\(erp-backend\\|web-gateway\\)/Dockerfile$#&\\n"
        '      labels:\\n        noust-it.revision: "2"#\' '
        f"{PROGGEST_COMPOSE_FILE} && "
        f"test $(grep -c 'noust-it.revision: \"2\"' {PROGGEST_COMPOSE_FILE}) = 2",
        "noust-it: revision 2 (a label on both images)",
    )
    before = {b["id"] for b in proggest_backups(sc, "the backups before update #1")}
    proggest_update(sc, ledger, 1, "a trivial change to both images")
    marks = proggest_probe_phase(sc, "update #1 (trivial)", marks, ledger)
    row = _proggest_check_update(sc, 1, 2)
    ledger.notes.append(f"update #1 schema_changed={row['schema_changed']} (the hook migrates)")
    proggest_pre_update_backup(sc, before, "update #1", ledger)

    migration_dir = f"packages/database/prisma/migrations/{PROGGEST_MIGRATION}"
    commit_to(
        sc,
        PROGGEST_REPO,
        f"mkdir -p {migration_dir} && "
        f"cp {PROGGEST_PATCH_IN_CONTAINER}/migration.sql {migration_dir}/migration.sql && "
        'sed -i \'s/noust-it.revision: "2"/noust-it.revision: "3"/\' '
        f"{PROGGEST_COMPOSE_FILE}",
        "noust-it: revision 3, an additive migration",
    )
    before = {b["id"] for b in proggest_backups(sc, "the backups before update #2")}
    proggest_update(
        sc, ledger, 2, "an additive Prisma migration (rebuilds from COPY . . when not cached)"
    )
    marks = proggest_probe_phase(sc, "update #2 (migration)", marks, ledger)
    row = _proggest_check_update(sc, 2, 3)
    sc.check(row["schema_changed"] == 1, f"the migration did not mark the schema: {row!r}")
    sc.check(PROGGEST_MIGRATION in (row["hooks"] or ""), f"the hook's output: {row!r}")
    applied = proggest_psql(
        sc,
        "SELECT count(*) FROM _prisma_migrations WHERE migration_name LIKE '%_noust_it_probe' "
        "AND finished_at IS NOT NULL",
        "the migration is recorded as applied",
    )
    sc.check(applied == "1", f"_prisma_migrations: {applied!r}")
    table = proggest_psql(
        sc, "SELECT to_regclass('public.noust_it_probe')", "the table the migration adds"
    )
    sc.check(table == "noust_it_probe", f"the migration's table: {table!r}")
    before_migration = proggest_pre_update_backup(sc, before, "update #2", ledger)
    return marks, before_migration


def _proggest_broken(sc: Scenario, ledger: Ledger, marks: dict[str, int]) -> dict[str, int]:
    """Step 4: a backend that cannot start is refused at its relay; the old one serves."""
    serving = proggest_containers(sc, "the containers before the broken update")
    head = sc.run(
        f"git -C {PROGGEST_DIR} rev-parse HEAD", timeout=15, label="the commit that serves"
    ).stdout.strip()
    commit_to(
        sc,
        PROGGEST_REPO,
        "sed -i 's#^    container_name: proggest-backend$#&\\n"
        '    command: ["node", "dist/noust-it-missing.js"]#\' '
        f"{PROGGEST_COMPOSE_FILE} && grep -q noust-it-missing {PROGGEST_COMPOSE_FILE}",
        "noust-it: a backend that cannot start (its command names a file the image lacks)",
    )
    failed = proggest_update(sc, ledger, 3, "a backend that cannot start", check=False)
    marks = proggest_probe_phase(sc, "update #3 (broken backend)", marks, ledger, minimum=5)
    output = command_output(failed)
    sc.check(failed.returncode != 0, "an update whose backend cannot start succeeded")
    sc.check(
        in_log(
            sc,
            "/root/pg-update-3.log",
            "did not answer in its relay",
            "the error says the relay never answered",
        ),
        f"the error does not say the relay never answered: {output[-2000:]!r}",
    )
    sc.check(
        proggest_containers(sc, "the containers after the broken update") == serving,
        "a container that served was touched by an update whose backend never started",
    )
    no_relay_left(sc, "no relay container is left after the broken update")
    sc.check(
        sc.run(
            f"git -C {PROGGEST_DIR} rev-parse HEAD", timeout=15, label="the checkout after it"
        ).stdout.strip()
        == head,
        "the checkout was not put back on the commit that serves",
    )
    sc.check(proggest_status(sc, "/api/v1/health") == "200", "the backend no longer answers")
    row = last_deployment(sc, PROGGEST_DOMAIN)
    ledger.notes.append(
        f"update #3 (broken): exit {failed.returncode}, row status {row['status']}, "
        f"schema_changed={row['schema_changed']}"
    )
    return marks


def _proggest_rollback_refused(
    sc: Scenario, ledger: Ledger, marks: dict[str, int], backup_id: str
) -> dict[str, int]:
    """Step 5: going back past the migration asks for --schema-changed-ok and moves nothing."""
    serving = proggest_containers(sc, "the containers before the rollback")
    head = sc.run(
        f"git -C {PROGGEST_DIR} rev-parse HEAD", timeout=15, label="the commit that serves"
    ).stdout.strip()
    command = f"noust rollback {PROGGEST_DOMAIN} {backup_id}"
    with ledger.lap("noust rollback past the migration (refused)"):
        refused = sc.run(
            command, timeout=600, check=False, label=f"{command}  (the backup before the migration)"
        )
    sc.check(refused.returncode != 0, "a rollback past a migration went ahead unasked")
    sc.check(
        "--schema-changed-ok" in command_output(refused),
        "the refusal does not name the flag that confirms it",
    )
    sc.check(
        proggest_containers(sc, "the containers after the refused rollback") == serving,
        "the refused rollback touched the containers",
    )
    sc.check(
        sc.run(
            f"git -C {PROGGEST_DIR} rev-parse HEAD", timeout=15, label="the checkout after it"
        ).stdout.strip()
        == head,
        "the refused rollback moved the checkout",
    )
    return proggest_probe_phase(sc, "the refused rollback", marks, ledger, minimum=5)


def api_json(
    sc: Scenario, token: str, method: str, path: str, body: dict[str, Any], label: str
) -> tuple[int, Any]:
    """Call the console's API with a JSON body and a Bearer token kept out of the evidence."""
    payload = json.dumps(body)
    sc.check("'" not in payload, "a JSON body cannot carry a single quote through the heredoc")
    proc = docker_exec(
        sc.container,
        f"curl -sS -X {method} -H 'Authorization: Bearer {token}' "
        "-H 'Content-Type: application/json' --data-binary @- "
        f"-w '\\n%{{http_code}}' {PANEL_URL}{path} <<'EOF'\n{payload}\nEOF",
        timeout=30,
        check=False,
    )
    text, _, code = proc.stdout.rpartition("\n")
    try:
        answer: Any = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        answer = text
    sc.evidence.append(f"$ {label}\n{payload}\n{code} {str(answer)[:4000]}")
    return int(code or 0), answer


def _locations(node: Any) -> Iterator[dict[str, Any]]:
    """Every location in a site's structure, nested ones included."""
    if isinstance(node, dict):
        if "modifier" in node and "path" in node and "id" in node:
            yield node
        for value in node.values():
            yield from _locations(value)
    elif isinstance(node, list):
        for item in node:
            yield from _locations(item)


def _proggest_site_api(sc: Scenario, ledger: Ledger) -> None:
    """Step 6: the console's API reads the operator's site and routes a URL to its location."""
    issued = docker_exec(
        sc.container, f"noust token create it-proggest-{secrets.token_hex(4)} --scope admin"
    )
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "noust token create printed no token")
    token = token_match.group(1) if token_match else ""
    sc.evidence.append("$ noust token create it-proggest-... --scope admin\n(token issued)")
    wait_for_console(sc)
    with ledger.lap("GET structure + POST route x3"):
        code, structure = api(
            sc, token, "GET", f"/api/sites/{PROGGEST_DOMAIN}/structure", "GET structure"
        )
        sc.check(code == 200, f"GET /api/sites/{PROGGEST_DOMAIN}/structure answered {code}")
        by_path = {loc["path"]: loc["id"] for loc in _locations(structure)}
        ledger.notes.append(f"structure: {len(by_path)} locations")
        for path, expected, upstream in (
            ("/api/v1/auth/login", "/api/v1/auth/login", "nestjs_upstream"),
            ("/api/v1/health", "/api/", "nestjs_upstream"),
            ("/", "/", "nextjs_upstream"),
        ):
            sc.check(expected in by_path, f"the structure has no location {expected}: {by_path}")
            code, routed = api_json(
                sc,
                token,
                "POST",
                f"/api/sites/{PROGGEST_DOMAIN}/route",
                {"host": PROGGEST_DOMAIN, "path": path, "scheme": "http"},
                f"POST route {path}",
            )
            sc.check(code == 200, f"POST route {path} answered {code}")
            sc.check(
                isinstance(routed, dict) and routed.get("location_id") == by_path[expected],
                f"{path} was routed to {routed!r}, not to location {expected} "
                f"({by_path[expected]})",
            )
            sc.check(
                f"u:{upstream}" in (routed.get("highlight") or []),
                f"{path} does not reach {upstream}: {routed.get('highlight')!r}",
            )
            ledger.notes.append(f"route {path}: {routed.get('location_id')} -> {upstream}")


def _proggest_sizes(sc: Scenario, ledger: Ledger) -> None:
    """The images' sizes and the inner Docker's disk use, for the report."""
    images = sc.run(
        "docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -i proggest",
        timeout=60,
        check=False,
        label="the stack's images",
    ).stdout.split("\n")
    ledger.notes.extend(f"image {line}" for line in images if line.strip())
    usage = sc.run(
        "docker system df --format '{{.Type}} {{.Size}}'; du -sh /var/backups/noust 2>/dev/null",
        timeout=120,
        check=False,
        label="the inner Docker's disk use, and the backups",
    ).stdout.split("\n")
    ledger.notes.extend(f"disk {line}" for line in usage if line.strip())


def _proggest_delete(sc: Scenario, ledger: Ledger) -> None:
    """Step 7: `noust delete` without --remove-adopted-directory keeps the stack's directory."""
    with ledger.lap("noust delete"):
        deleted = sc.run(
            f"noust delete {PROGGEST_DOMAIN} -f",
            timeout=600,
            check=False,
            label=f"noust delete {PROGGEST_DOMAIN} -f",
        )
    # The operator's site and the servers files it includes are kept on purpose, and the
    # command reports them as warnings, the same channel a unit it failed to remove would use:
    # it exits 1. Accepted here only with every warning being one of those two.
    said = command_output(deleted)
    sc.check(deleted.returncode in (0, 1), f"noust delete exited {deleted.returncode}")
    if deleted.returncode == 1:
        sc.check(
            "Noust did not write; it is left in place" in said
            and "was not removed" not in said
            and "failed" not in said.lower(),
            "noust delete failed at something other than keeping the operator's site",
        )
    ledger.notes.append(f"noust delete of the adopted stack: exit {deleted.returncode}")
    containers = sc.run(
        f"docker ps -aq -f label=com.docker.compose.project={PROGGEST_PROJECT}",
        timeout=30,
        label="the stack's containers after the delete",
    ).stdout.split()
    sc.check(not containers, f"the stack still has containers: {containers}")
    kept = sc.run(
        f"test -d {PROGGEST_DIR}/.git && test -f {PROGGEST_DIR}/.env && "
        f"test -f {PROGGEST_DIR}/{PROGGEST_COMPOSE_FILE} && echo kept",
        timeout=15,
        check=False,
        label=f"{PROGGEST_DIR}, its checkout and its .env",
    )
    sc.check(kept.stdout.strip() == "kept", f"noust delete removed {PROGGEST_DIR}")
    sc.check(
        sc.run(
            f"test -f {PROGGEST_SITE_PATH}", timeout=15, check=False, label="the operator's site"
        ).returncode
        == 0,
        "noust delete removed the operator's site",
    )
    volumes = sc.run(
        f"docker volume ls -q -f label=com.docker.compose.project={PROGGEST_PROJECT}",
        timeout=30,
        label="the stack's volumes",
    ).stdout.split()
    sc.check(f"{PROGGEST_PROJECT}_pgdata" in volumes, f"the database's volume is gone: {volumes}")
    sc.check(
        not store_rows(
            sc,
            "SELECT id FROM apps WHERE domain = @domain",
            domain=PROGGEST_DOMAIN,
            label="the application is gone from the store",
        ),
        "the application is still registered",
    )


INSTALLER = REPO_ROOT / "packaging" / "install.sh"


@scenario("installer_platform_and_dry_run")
def scenario_installer_platform_and_dry_run(sc: Scenario) -> None:
    """The one-line installer detects this system, plans without touching it, and refuses what it must."""
    sh(["docker", "cp", str(INSTALLER), f"{sc.container}:/tmp/install.sh"], timeout=30)
    platform = sc.run("sh /tmp/install.sh --print-platform", label="install.sh --print-platform")
    sc.check(
        "status=supported" in platform.stdout,
        f"Ubuntu 24.04 is declared supported: {platform.stdout}",
    )
    sc.check("repo=xUbuntu_24.04" in platform.stdout, "it picks the Ubuntu 24.04 repository")

    plan = sc.run("sh /tmp/install.sh --dry-run --version 3.3.1", label="install.sh --dry-run")
    sc.check(
        "[dry-run]" in plan.stdout and "Dry run: nothing was changed." in plan.stdout, "it plans"
    )
    sc.check(
        "noust=3.3.1-*" in plan.stdout
        and "D9FA6122D7F154D75A588A501945D169A62CFF65" in plan.stdout,
        "the plan names the pinned version and the key fingerprint it will check",
    )
    sc.check(
        sc.run(
            "test ! -e /etc/apt/sources.list.d/noust.list", check=False, label="nothing written"
        ).returncode
        == 0,
        "a dry run wrote nothing",
    )

    for name, os_release, arch, expect in (
        ("Arch Linux", 'ID=arch\nPRETTY_NAME="Arch Linux"\n', "x86_64", 3),
        ("Debian 11", 'ID=debian\nVERSION_ID="11"\nPRETTY_NAME="Debian 11"\n', "x86_64", 3),
        (
            "32-bit Raspberry Pi OS",
            'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian 12"\n',
            "armv7l",
            3,
        ),
    ):
        sc.run(f"printf '{os_release}' > /tmp/fake-os-release", label=f"a {name} os-release")
        refused = sc.run(
            f"NOUST_OS_RELEASE=/tmp/fake-os-release NOUST_ARCH={arch} sh /tmp/install.sh --yes",
            check=False,
            label=f"install.sh on {name}",
        )
        sc.check(
            refused.returncode == expect,
            f"{name} is refused with status {expect}: {refused.returncode}",
        )

    wrong = sc.run(
        "NOUST_REPO_KEY_FPR=0123456789ABCDEF0123456789ABCDEF01234567 "
        "sh /tmp/install.sh --yes --skip-setup --no-console",
        timeout=180,
        check=False,
        label="install.sh with a key fingerprint that is not the repository's",
    )
    sc.check(wrong.returncode != 0, "a key with another fingerprint is refused")
    sc.check("does not have the expected fingerprint" in wrong.stderr, "and the refusal says why")
    sc.check(
        sc.run(
            "test ! -e /etc/apt/sources.list.d/noust.list",
            check=False,
            label="still nothing written",
        ).returncode
        == 0,
        "nothing was written before the key was verified",
    )


#: Stands in for certbot in the harness, which has no route to an ACME server: it "issues" a
#: self-signed certificate where certbot would put one, and removes it on `delete`. Everything
#: else of the exposure (nginx, systemd, the console, the store) is the real thing.
FAKE_CERTBOT = """#!/bin/sh
case "$1" in
    delete)
        while [ "$#" -gt 0 ]; do [ "$1" = --cert-name ] && name="$2"; shift; done
        rm -rf "/etc/letsencrypt/live/$name" "/etc/letsencrypt/archive/$name"
        exit 0 ;;
    certonly)
        while [ "$#" -gt 0 ]; do [ "$1" = --cert-name ] && name="$2"; shift; done
        live="/etc/letsencrypt/live/$name"
        mkdir -p "$live"
        openssl req -x509 -newkey rsa:2048 -nodes -days 90 -subj "/CN=$name" \\
            -keyout "$live/privkey.pem" -out "$live/fullchain.pem" 2>/dev/null
        cp "$live/fullchain.pem" "$live/cert.pem"; cp "$live/fullchain.pem" "$live/chain.pem"
        exit 0 ;;
esac
exit 0
"""

CONSOLE_NAME = "console.test"
CONSOLE_SITE = "/etc/nginx/sites-available/console.test"


@scenario("console_exposed_through_nginx")
def scenario_console_exposed_through_nginx(sc: Scenario) -> None:
    """`noust console expose` publishes the console over TLS through the real nginx, and undoes it.

    The console is a real systemd service; nginx, the unit, the store and the configuration are
    real; only certbot is a stand-in that issues a self-signed certificate. What is pinned:
    the refusals before anything is written (no second factor), the nginx allow-list enforced
    before the console sees a request, the console's unit trusting nginx and still answering,
    and `unexpose` leaving the machine as it found it.
    """
    require_cli(sc, "console expose", what="the console on a public name")
    sc.run("command -v openssl nginx", timeout=15, label="openssl and nginx are there")
    docker_exec(
        sc.container,
        f"cat > /usr/local/bin/certbot <<'SHIM'\n{FAKE_CERTBOT}SHIM\nchmod 0755 /usr/local/bin/certbot",
        timeout=30,
    )
    sc.evidence.append("$ install a certbot stand-in (self-signed certificate, no ACME)")
    secret = ""
    try:
        enable = docker_exec(sc.container, "noust web enable", timeout=90, check=False)
        sc.evidence.append("$ noust web enable\n(output withheld: it holds the access token)")
        sc.check(enable.returncode == 0, f"noust web enable failed: {enable.stderr}")
        wait_until_health(sc, PANEL_URL)

        # Refused first: no account and the access token has no second factor.
        refused = sc.run(
            f"noust console expose --domain {CONSOLE_NAME} --skip-dns-check --yes",
            check=False,
            label="expose without a second factor",
        )
        sc.check(refused.returncode != 0, "the console was published without a second factor")
        sc.check(
            "second factor" in refused.stderr + refused.stdout,
            f"the refusal does not say why: {refused.stderr}{refused.stdout}",
        )
        sc.check(
            sc.run(f"test ! -e {CONSOLE_SITE}", check=False, label="nothing written").returncode
            == 0,
            "a refused exposure wrote the site",
        )

        # An account that signs in with a password alone stops it too.
        sc.run(
            "printf '%s\n' 'a-long-passphrase-for-the-admin-7' | noust user create alice --role admin --stdin",
            label="noust user create alice",
        )
        refused = sc.run(
            f"noust console expose --domain {CONSOLE_NAME} --skip-dns-check --yes",
            check=False,
            label="expose with an account lacking a second factor",
        )
        sc.check(refused.returncode != 0, "published while alice has no second factor")
        sc.check(
            "alice" in refused.stderr + refused.stdout, "the refusal does not name the account"
        )
        sc.run("noust user disable alice", label="noust user disable alice")

        # The access token's own second factor, enrolled the way an operator does.
        enroll = docker_exec(sc.container, "noust 2fa enroll", timeout=30, check=False)
        match = re.search(r"Secret\W+([A-Z2-7]{16,})", enroll.stdout)
        sc.check(match is not None, "noust 2fa enroll printed no secret")
        secret = match.group(1) if match else ""
        code = docker_exec(
            sc.container,
            f"/opt/noust/bin/python -c 'from noust.core.totp import totp_now; print(totp_now(\"{secret}\"))'",
            timeout=30,
        ).stdout.strip()
        confirmed = docker_exec(sc.container, f"noust 2fa confirm {code}", timeout=30, check=False)
        sc.evidence.append("$ noust 2fa enroll / confirm\n(output withheld: it holds secrets)")
        sc.check(confirmed.returncode == 0, f"2fa confirm failed: {confirmed.stderr}")

        # The real thing, with an allow-list that does not include the address the test comes from.
        sc.run(
            f"noust console expose --domain {CONSOLE_NAME} --allow-ip 203.0.113.0/24 "
            "--skip-dns-check --yes",
            timeout=180,
            label="noust console expose",
        )
        site = sc.run(f"cat {CONSOLE_SITE}", label="the nginx site").stdout
        sc.check(site.startswith("# Noust console site for"), "the site does not say it is Noust's")
        sc.check(
            "allow 203.0.113.0/24;" in site and "deny all;" in site, "the allow-list is not there"
        )
        sc.check(
            sc.run("nginx -t", check=False, label="nginx -t").returncode == 0,
            "nginx rejects the site",
        )
        unit = sc.run("systemctl cat noust-web.service", label="the console's unit").stdout
        sc.check("--trusted-proxy 127.0.0.1" in unit, "the console does not trust nginx")
        wait_until_health(sc, PANEL_URL)
        status = sc.run("noust console status --json", label="noust console status").stdout
        sc.check('"exposed": true' in status, f"status does not say it is exposed: {status}")

        via_nginx = (
            f"curl -sk -o /dev/null -w '%{{http_code}}' --resolve {CONSOLE_NAME}:443:127.0.0.1 "
            f"https://{CONSOLE_NAME}/health"
        )
        blocked = sc.run(via_nginx, check=False, label="through nginx, from outside the allow-list")
        sc.check(blocked.stdout.strip() == "403", f"nginx let a stranger through: {blocked.stdout}")
        redirect = sc.run(
            f"curl -s -o /dev/null -w '%{{http_code}} %{{redirect_url}}' -H 'Host: {CONSOLE_NAME}' "
            "http://127.0.0.1/",
            check=False,
            label="plain HTTP",
        )
        sc.check(
            redirect.stdout.startswith(f"301 https://{CONSOLE_NAME}"),
            f"plain HTTP is not redirected to HTTPS: {redirect.stdout}",
        )

        # Open it to this machine, and the console answers through nginx over TLS.
        sc.run(
            f"noust console expose --domain {CONSOLE_NAME} --allow-ip 127.0.0.1 --skip-dns-check --yes",
            timeout=180,
            label="noust console expose again, allowing 127.0.0.1",
        )
        wait_until_health(sc, PANEL_URL)
        answered = sc.run(via_nginx, check=False, label="through nginx, from inside the allow-list")
        sc.check(
            answered.stdout.strip() == "200",
            f"the console does not answer through nginx: {answered.stdout}",
        )

        # Take it off: exactly what it made, and the console keeps running on loopback.
        sc.run("noust console unexpose --yes", timeout=180, label="noust console unexpose")
        sc.check(
            sc.run(
                f"test ! -e {CONSOLE_SITE} && test ! -e /etc/letsencrypt/live/{CONSOLE_NAME}",
                check=False,
                label="the site and the certificate are gone",
            ).returncode
            == 0,
            "unexpose left the site or the certificate",
        )
        unit = sc.run("systemctl cat noust-web.service", label="the console's unit again").stdout
        sc.check("--trusted-proxy" not in unit, "the unit still trusts a proxy that is gone")
        sc.check(
            sc.run("nginx -t", check=False, label="nginx -t").returncode == 0,
            "nginx rejects what is left",
        )
        wait_until_health(sc, PANEL_URL)
        again = sc.run("noust console status --json", label="noust console status").stdout
        sc.check('"exposed": false' in again, f"status still says exposed: {again}")
    finally:
        sc.run("noust console unexpose --yes", timeout=180, check=False, label="cleanup: unexpose")
        if secret:
            # The next 30-second step: the one confirm used is spent and replays are refused.
            code = docker_exec(
                sc.container,
                "/opt/noust/bin/python -c 'import time; from noust.core.totp import totp_now; "
                f'print(totp_now("{secret}", t=time.time() + 30))\'',
                timeout=30,
                check=False,
            ).stdout.strip()
            docker_exec(sc.container, f"noust 2fa disable {code}", timeout=30, check=False)
        sc.run("noust user remove alice --force", check=False, label="cleanup: alice")
        sc.run("noust web disable", timeout=60, check=False, label="cleanup: web disable")
        sc.run("rm -f /usr/local/bin/certbot", check=False, label="cleanup: certbot stand-in")


def wait_until_health(sc: Scenario, url: str) -> None:
    """Wait until the console answers ``/health``."""
    deadline = time.time() + 45
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' {url}/health",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "200":
            return
        time.sleep(1)
    raise AssertionError(f"the console did not answer {url}/health within 45s")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def random_container_name() -> str:
    return f"{RESOURCE_PREFIX}-{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------
# 3.4: container applications (a Dockerfile, an image from a registry, Railpack)
# ---------------------------------------------------------------------------

CONTAINER_DOMAIN = "ctr.test"
CONTAINER_PORT = 18210
CONTAINER_REPO = "/root/fixtures/ctr-app"
CONTAINER_URL = "git://127.0.0.1/ctr-app"
CONTAINER_IMAGES = "noust-ctr-test"
CONTAINER_BASE = "nginx:alpine"

CONTAINER_REGISTRY_DOMAIN = "ctr-image.test"
CONTAINER_REGISTRY_PORT = 18211
CONTAINER_REGISTRY_IMAGES = "noust-ctr-image-test"

CONTAINER_RAILPACK_DOMAIN = "ctr-rp.test"
CONTAINER_RAILPACK_PORT = 18212
CONTAINER_RAILPACK_REPO = "/root/fixtures/ctr-railpack"
CONTAINER_RAILPACK_URL = "git://127.0.0.1/ctr-railpack"

#: Seconds a way back may take: it recreates one container from an image already on the host.
CONTAINER_ROLLBACK_SECONDS = 45


def container_dockerfile(extra: str = "") -> str:
    """A printf-ready Dockerfile of the nginx fixture, with a line of ``extra`` before EXPOSE."""
    return f"FROM {CONTAINER_BASE}\\nCOPY html /usr/share/nginx/html\\n{extra}EXPOSE 80\\n"


def container_images(sc: Scenario, repository: str) -> list[str]:
    """The tags of an application's images, as the container's own Docker lists them."""
    listed = sc.run(
        f"docker image ls --format '{{{{.Repository}}}}:{{{{.Tag}}}}' {repository}",
        timeout=30,
        check=False,
        label=f"docker image ls {repository}",
    )
    return sorted(line for line in listed.stdout.split() if line)


def container_clean(sc: Scenario, domain: str, repository: str) -> None:
    """Remove an application, then anything of it that is left, whatever the scenario did."""
    compose_clean(sc, domain)
    sc.run(
        f"docker image ls -q {repository} | xargs -r docker image rm -f; true",
        timeout=60,
        check=False,
        label=f"cleanup: the images of {repository}",
    )


@scenario("container_app_dockerfile_update_rollback_and_failed_update")
def scenario_container_dockerfile(sc: Scenario) -> None:
    """
    A Dockerfile in a repository is a container application, with a release per image.

    Deployed with ``--type container``: the site answers through nginx and the container is
    published on loopback only. An update builds a second image and serves it; going back is
    instant (no build: the image of the first release is still there); an update whose container
    cannot start puts the one that served back and says so. Deleting the application takes every
    image with it.
    """
    require_cli(sc, "app container", what="container applications")
    require_docker(sc)
    pull_images(sc, CONTAINER_BASE)
    sc.run(
        f"rm -rf {CONTAINER_REPO} && mkdir -p {CONTAINER_REPO}/html && cd {CONTAINER_REPO} && "
        f"printf 'ctr v1' > html/index.html && printf '{container_dockerfile()}' > Dockerfile && "
        f"git init -q -b main && {GIT_IDENTITY} && git add -A && git commit -q -m 'v1'",
        timeout=30,
        label="(fixture repo) a Dockerfile that serves one page with nginx",
    )
    sc.run(
        f"noust create -d {CONTAINER_DOMAIN} -s {CONTAINER_URL} -t container "
        f"-p {CONTAINER_PORT} --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {CONTAINER_DOMAIN} -s {CONTAINER_URL} -t container",
    )
    try:
        wait_served(sc, CONTAINER_DOMAIN, "ctr v1")

        # The container is reached through nginx, never from the network.
        published = sc.run(
            f"docker port $(docker ps -q -f label=io.noust.app={CONTAINER_DOMAIN} | head -1)",
            timeout=15,
            label="docker port <the container>",
        ).stdout
        sc.check(
            f"127.0.0.1:{CONTAINER_PORT}" in published and "0.0.0.0" not in published,  # noqa: S104 - the check that it is not bound
            f"the container is published beyond loopback: {published!r}",
        )

        shown = json_of(
            sc.run(
                f"noust app container show {CONTAINER_DOMAIN} --json",
                timeout=30,
                label=f"noust app container show {CONTAINER_DOMAIN} --json",
            ),
            "app container show",
        )
        sc.check(shown["method"] == "dockerfile", f"the method: {shown['method']!r}")
        sc.check(shown["container_port"] == 80, f"the port the Dockerfile exposes: {shown!r}")

        commit_to(sc, CONTAINER_REPO, "printf 'ctr v2' > html/index.html", "v2")
        sc.run(
            f"noust update {CONTAINER_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {CONTAINER_DOMAIN}",
        )
        wait_served(sc, CONTAINER_DOMAIN, "ctr v2")
        releases = json_of(
            sc.run(
                f"noust releases list {CONTAINER_DOMAIN} --json",
                timeout=30,
                label=f"noust releases list {CONTAINER_DOMAIN} --json",
            ),
            "releases list",
        )["items"]
        sc.check(
            len(releases) == 2 and [r["active"] for r in releases] == [True, False],
            f"two releases, the newest serving: {releases!r}",
        )
        sc.check(
            len(container_images(sc, CONTAINER_IMAGES)) == 2,
            "each release must have its own image on the host",
        )

        # Instant: nothing is built, the first release's image is what runs again.
        started = time.monotonic()
        sc.run(
            f"noust releases rollback {CONTAINER_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust releases rollback {CONTAINER_DOMAIN}",
        )
        took = time.monotonic() - started
        wait_served(sc, CONTAINER_DOMAIN, "ctr v1")
        sc.check(
            took < CONTAINER_ROLLBACK_SECONDS,
            f"going back took {took:.0f}s: it recreates one container from an image on the host",
        )

        # nginx refuses to start on a broken configuration: the container exits.
        broken = container_dockerfile("RUN echo broken > /etc/nginx/conf.d/broken.conf\\n")
        commit_to(
            sc,
            CONTAINER_REPO,
            f"printf 'ctr v3' > html/index.html && printf '{broken}' > Dockerfile",
            "v3: nginx cannot start",
        )
        update = sc.run(
            f"noust update {CONTAINER_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {CONTAINER_DOMAIN}",
        )
        output = update.stdout + update.stderr
        sc.check(update.returncode != 0, "an update whose container does not start must fail")
        sc.check(
            "did not pass its health check" in output and "running again" in output,
            f"the update did not say it went back: {output!r}",
        )
        body = curl_host(sc, CONTAINER_DOMAIN)
        sc.check("ctr v1" in body, f"what served before is not serving again: {body!r}")
        after = json_of(
            sc.run(
                f"noust releases list {CONTAINER_DOMAIN} --json",
                timeout=30,
                label=f"noust releases list {CONTAINER_DOMAIN} --json",
            ),
            "releases list",
        )["items"]
        statuses = [release["status"] for release in after]
        sc.check(
            statuses.count("failed") == 1,
            f"the release that did not answer must be recorded as failed: {statuses!r}",
        )

        sc.run(
            f"noust delete {CONTAINER_DOMAIN} -f",
            timeout=180,
            label=f"noust delete {CONTAINER_DOMAIN} -f",
        )
        left = container_images(sc, CONTAINER_IMAGES)
        sc.check(not left, f"images of a deleted application were left on the host: {left!r}")
    finally:
        container_clean(sc, CONTAINER_DOMAIN, CONTAINER_IMAGES)


@scenario("container_app_from_a_registry_image")
def scenario_container_registry(sc: Scenario) -> None:
    """
    An image from a registry is deployed with no source, and an update pulls it again.

    ``noust create --image``: no repository, no Dockerfile. The image is named for the release
    on the host, so going back never needs the registry. The restart policy set afterwards
    reaches the container with the next update. Skipped, saying why, when the registry cannot be
    reached from the harness container.
    """
    require_cli(sc, "app container", what="container applications")
    require_docker(sc)
    pull_images(sc, CONTAINER_BASE)
    sc.run(
        f"noust create -d {CONTAINER_REGISTRY_DOMAIN} --image {CONTAINER_BASE} "
        f"-p {CONTAINER_REGISTRY_PORT} --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {CONTAINER_REGISTRY_DOMAIN} --image {CONTAINER_BASE}",
    )
    try:
        wait_served(sc, CONTAINER_REGISTRY_DOMAIN, "Welcome to nginx!")
        shown = json_of(
            sc.run(
                f"noust app container show {CONTAINER_REGISTRY_DOMAIN} --json",
                timeout=30,
                label=f"noust app container show {CONTAINER_REGISTRY_DOMAIN} --json",
            ),
            "app container show",
        )
        sc.check(shown["method"] == "image" and shown["image"] == CONTAINER_BASE, f"{shown!r}")
        sc.check(
            str(shown.get("digest") or "").startswith("sha256:"),
            f"the digest the pull resolved was not recorded: {shown!r}",
        )
        sc.check(shown["container_port"] == 80, f"the port the image exposes: {shown!r}")

        sc.run(
            f"noust app container set {CONTAINER_REGISTRY_DOMAIN} --restart always",
            timeout=30,
            label=f"noust app container set {CONTAINER_REGISTRY_DOMAIN} --restart always",
        )
        update = sc.run(
            f"noust update {CONTAINER_REGISTRY_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {CONTAINER_REGISTRY_DOMAIN}",
        )
        sc.check("Pulling the image again" in update.stdout + update.stderr, "no pull on update")
        wait_served(sc, CONTAINER_REGISTRY_DOMAIN, "Welcome to nginx!")
        policy = sc.run(
            "docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "
            f"$(docker ps -q -f label=io.noust.app={CONTAINER_REGISTRY_DOMAIN} | head -1)",
            timeout=15,
            label="the container's restart policy",
        ).stdout.strip()
        sc.check(policy == "always", f"the restart policy did not reach the container: {policy!r}")
        sc.check(
            len(container_images(sc, CONTAINER_REGISTRY_IMAGES)) == 2,
            "each pull must leave its release's image on the host",
        )
    finally:
        container_clean(sc, CONTAINER_REGISTRY_DOMAIN, CONTAINER_REGISTRY_IMAGES)


@scenario("container_app_built_by_railpack")
def scenario_container_railpack(sc: Scenario) -> None:
    """
    A repository with no Dockerfile is built by Railpack, installed by ``noust setup railpack``.

    The install is the pinned release, checked against its published SHA-256. The build pulls
    Railpack's BuildKit frontend and its base images, so it needs the network of the machine:
    when the release or an image cannot be fetched the scenario is skipped, never passed.
    """
    require_cli(sc, "setup railpack", what="Railpack builds")
    require_docker(sc)
    installed = sc.run(
        "noust setup railpack",
        timeout=300,
        check=False,
        label="noust setup railpack",
    )
    if installed.returncode != 0:
        raise ScenarioSkipped(
            "Railpack could not be installed here (no network to its release?): "
            f"{(installed.stdout + installed.stderr).strip()[-300:]}"
        )
    sc.run(
        "railpack --version",
        timeout=30,
        label="railpack --version",
    )
    sc.run(
        f"rm -rf {CONTAINER_RAILPACK_REPO} && mkdir -p {CONTAINER_RAILPACK_REPO} && "
        f"cd {CONTAINER_RAILPACK_REPO} && "
        'printf \'{"name":"rp","version":"1.0.0","scripts":{"start":"node server.js"}}\' '
        "> package.json && "
        "printf \"require('http').createServer((q, r) => r.end('railpack v1'))"
        '.listen(process.env.PORT || 3000);\\n" > server.js && '
        f"git init -q -b main && {GIT_IDENTITY} && git add -A && git commit -q -m 'v1'",
        timeout=30,
        label="(fixture repo) a Node server and no Dockerfile",
    )
    created = sc.run(
        f"noust create -d {CONTAINER_RAILPACK_DOMAIN} -s {CONTAINER_RAILPACK_URL} -t container "
        f"--builder railpack -p {CONTAINER_RAILPACK_PORT} --no-ssl",
        timeout=1800,
        check=False,
        label=f"noust create -d {CONTAINER_RAILPACK_DOMAIN} --builder railpack",
    )
    output = created.stdout + created.stderr
    try:
        if created.returncode != 0 and "containerd image store" in output:
            # Railpack's frontend merges layers, which only Docker's containerd image store allows;
            # this harness runs the classic overlay2 store on a volume (the containerd one would
            # be overlay on overlay, on the container's own root). What can be checked is that
            # the failure says what to change.
            sc.check(
                "containerd-snapshotter" in output and "/etc/docker/daemon.json" in output,
                f"a build refused for the image store does not say how to turn it on: "
                f"{output[-600:]!r}",
            )
            raise ScenarioSkipped(
                "this harness's Docker keeps the classic image store, which Railpack builds "
                "refuse (the error names the containerd one and how to turn it on: checked)"
            )
        if created.returncode != 0 and any(
            hint in output for hint in ("railpack-frontend", "ghcr.io", "mise", "no such host")
        ):
            raise ScenarioSkipped(
                "Railpack's frontend or base images could not be pulled here: "
                f"{output.strip()[-300:]}"
            )
        sc.check(created.returncode == 0, f"the Railpack build failed: {output[-600:]!r}")
        wait_served(sc, CONTAINER_RAILPACK_DOMAIN, "railpack v1")
        shown = json_of(
            sc.run(
                f"noust app container show {CONTAINER_RAILPACK_DOMAIN} --json",
                timeout=30,
                label=f"noust app container show {CONTAINER_RAILPACK_DOMAIN} --json",
            ),
            "app container show",
        )
        sc.check(shown["method"] == "railpack", f"the method: {shown['method']!r}")
    finally:
        container_clean(sc, CONTAINER_RAILPACK_DOMAIN, "noust-ctr-rp-test")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", action="store_true", help="Do not remove the container when done."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=None,
        metavar="NAME",
        help="Run only scenarios whose name contains this substring. May repeat.",
    )
    parser.add_argument(
        "--reuse",
        metavar="CONTAINER",
        default=None,
        help=(
            "Run the scenarios in a harness container a previous --keep run left, instead of "
            "starting one: this tree's wheel is installed over what it runs and the fixtures are "
            "copied over its own, so a failing scenario is run again in seconds. The container is "
            "left running. Only a container this harness named (NOUST_IT_PREFIX) is accepted."
        ),
    )
    parser.add_argument(
        "--skip-wheel-build",
        action="store_true",
        help="Reuse the newest wheel already in the working directory instead of rebuilding.",
    )
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Install this wheel instead of building one from the working tree: a wheel of a "
            "committed tree, say, while other work is half-done in this one."
        ),
    )
    parser.add_argument(
        "--skip-image-build",
        action="store_true",
        help=f"Reuse the existing {IMAGE_TAG} image instead of rebuilding it.",
    )
    parser.add_argument(
        "--ignore-requirements",
        action="store_true",
        help=(
            "Run the scenarios of features still being built (hooks, relay, adoption, ...) "
            "even though the build under test lacks the command or module they need, instead "
            "of skipping them: to see how far one gets, and what it reports, before it lands."
        ),
    )
    parser.add_argument(
        "--upgrade-from-3.0",
        dest="upgrade_from_30",
        action="store_true",
        help=(
            f"Run only the Noust {UPGRADE_30_VERSION} -> working tree upgrade rehearsal, one "
            "container per monitor variant. --scenario is ignored when this is given."
        ),
    )
    parser.add_argument(
        "--upgrade-from-3.3",
        dest="upgrade_from_33",
        action="store_true",
        help=(
            "Run only the Noust 3.3.1 -> working tree upgrade, on a server seeded with every "
            "legacy name (a wasm- unit, a wasm_bg_ upstream and an operator's site that names it, "
            "a wasm_ro_ role, a backup in wasm-backups, an API token): see scenarios_upgrade.py. "
            "--scenario is ignored when this is given."
        ),
    )
    parser.add_argument(
        "--downgrade-to-3.3",
        dest="downgrade_to_33",
        action="store_true",
        help=(
            "Run only the going-back rehearsal: a Noust 3.3.1 server upgraded to this tree, then "
            "returned to 3.3.1 the way docs/downgrading.md says (the store's copy put back). "
            "--scenario is ignored when this is given."
        ),
    )
    parser.add_argument(
        "--mixed-fleet",
        action="store_true",
        help=(
            "Run only the mixed fleets (tests/integration/scenarios_fleet_mixed.py): a central of "
            "this tree with a node of 3.3.1, and a node of this tree under a central of 3.3.1, "
            "enrolled over a real SSH tunnel. --scenario is ignored when this is given."
        ),
    )
    parser.add_argument(
        "--install-matrix",
        action="store_true",
        help=(
            "Run only the installer against the platform matrix (tests/integration/"
            "install_matrix.py): one throwaway container per distribution, installing the "
            "published package. They run in the harness container's own Docker, so nothing but "
            "that one container is added to the machine's."
        ),
    )
    parser.add_argument(
        "--image",
        action="append",
        default=None,
        metavar="IMAGE",
        help="With --install-matrix: only this image (debian:12); may repeat.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        metavar="PLATFORM",
        help="With --install-matrix: one platform (linux/arm64) instead of each case's own.",
    )
    parser.add_argument(
        "--monitor-variant",
        choices=("installed", "uninstalled", "both"),
        default="both",
        help="With --upgrade-from-3.0: which state 3.0 leaves the monitor in (default: both).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# 3.4: sites that were already running, and the disk an application takes
# ---------------------------------------------------------------------------

NATIVE_ROOT = "/srv/native"
NATIVE_PORT = 18711
NATIVE_SITES = ("native-legacy.test", "native-blog.test", "native-docs.test")

NATIVE_UNIT = """[Unit]
Description=Legacy API, written by hand
After=network.target

[Service]
WorkingDirectory=@ROOT@/legacy
ExecStart=@NODE@ server.js
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

NATIVE_SERVER_JS = """const http = require('http');
http.createServer((request, response) => { response.end('legacy v1\\n'); }).listen(@PORT@, '127.0.0.1', () => {
  console.log('legacy api listening on @PORT@');
});
"""

NATIVE_PROXY_SITE = """server {
    listen 80;
    server_name native-legacy.test;
    location / { proxy_pass http://127.0.0.1:@PORT@; }
}
"""

NATIVE_PHP_SITE = """server {
    listen 80;
    server_name native-blog.test;
    root @ROOT@/blog;
    index index.php;
    location / { try_files $uri $uri/ /index.php?$args; }
    location ~ \\.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:@SOCKET@;
    }
}
"""

NATIVE_STATIC_SITE = """server {
    listen 80;
    server_name native-docs.test;
    root @ROOT@/docs;
    location / { try_files $uri $uri/ =404; }
}
"""


def native_state(sc: Scenario, label: str) -> str:
    """Checksums of everything the operator wrote, and the unit's own start, to compare."""
    return sc.run(
        f"find {NATIVE_ROOT} /etc/nginx/sites-available/native-* "
        "/etc/systemd/system/native-legacy.service -type f -exec sha256sum {} + | sort; "
        "systemctl show native-legacy -p ActiveEnterTimestampMonotonic -p MainPID",
        timeout=30,
        label=label,
    ).stdout


@scenario("native_sites_adopted_without_touching_them")
def scenario_native_adoption(sc: Scenario) -> None:
    """
    A unit written by hand behind nginx, a PHP site and a folder of files are adopted as they are.

    The operator's own setup, none of it deployed by Noust: a Node server run by a unit they wrote
    and proxied by an nginx site, a PHP site served through the real PHP-FPM socket with the
    distribution's own `fastcgi-php.conf` include, and a static folder. `noust app discover` finds
    the unit that listens by asking the real `ss` and the real control group of the process.
    Adopting writes the store and nothing else: every file keeps its checksum and the unit keeps
    running with the same process. Noust then restarts the unit and reads its journal, refuses to
    build or write its environment, and deleting the application only forgets it.
    """
    require_cli(sc, "app adopt-site", what="adopting sites that already run")
    socket = sc.run(
        "ls /run/php/php*-fpm.sock | head -1", timeout=15, label="the PHP-FPM socket"
    ).stdout.strip()
    sc.check(socket.endswith(".sock"), f"no PHP-FPM socket: {socket!r}")
    node = sc.run("command -v node", timeout=15, label="node").stdout.strip()
    fill = {"@ROOT@": NATIVE_ROOT, "@PORT@": str(NATIVE_PORT), "@SOCKET@": socket, "@NODE@": node}

    def render(text: str) -> str:
        for key, value in fill.items():
            text = text.replace(key, value)
        return text

    def write(path: str, text: str) -> None:
        sc.run(f"cat > {path} <<'EOF'\n{render(text)}EOF", timeout=15, label=f"write {path}")

    try:
        sc.run(
            f"mkdir -p {NATIVE_ROOT}/legacy {NATIVE_ROOT}/blog {NATIVE_ROOT}/docs && "
            f"echo '<?php echo \"blog php ok\\n\";' > {NATIVE_ROOT}/blog/index.php && "
            f"echo 'docs static ok' > {NATIVE_ROOT}/docs/index.html && "
            f"chmod -R a+rX {NATIVE_ROOT}",
            timeout=15,
            label="the operator's folders",
        )
        write(f"{NATIVE_ROOT}/legacy/server.js", NATIVE_SERVER_JS)
        write("/etc/systemd/system/native-legacy.service", NATIVE_UNIT)
        write("/etc/nginx/sites-available/native-legacy.test", NATIVE_PROXY_SITE)
        write("/etc/nginx/sites-available/native-blog.test", NATIVE_PHP_SITE)
        write("/etc/nginx/sites-available/native-docs.test", NATIVE_STATIC_SITE)
        sc.run(
            "for site in native-legacy.test native-blog.test native-docs.test; do "
            "ln -sf /etc/nginx/sites-available/$site /etc/nginx/sites-enabled/$site; done && "
            "systemctl daemon-reload && systemctl start native-legacy && nginx -t && nginx -s reload",
            timeout=60,
            label="the operator starts the unit and enables the sites",
        )
        wait_served(sc, "native-legacy.test", "legacy v1")
        wait_served(sc, "native-blog.test", "blog php ok")
        wait_served(sc, "native-docs.test", "docs static ok")
        before = native_state(sc, "everything the operator wrote, before")

        found = json.loads(
            sc.run("noust --json app discover", timeout=120, label="noust --json app discover")
            .stdout.strip()
            .splitlines()[-1]
        )["sites"]
        by_site = {site["site"]: site for site in found}
        for name in NATIVE_SITES:
            sc.check(name in by_site, f"discover did not list {name}: {sorted(by_site)}")
        legacy, blog, docs = (by_site[name] for name in NATIVE_SITES)
        sc.check(
            (legacy["kind"], legacy["unit"], legacy["port"])
            == ("proxy", "native-legacy", NATIVE_PORT),
            f"the unit that listens was not found from the process: {legacy}",
        )
        sc.check(legacy["unit_active"] is True, f"the unit is running: {legacy}")
        sc.check(blog["kind"] == "php" and blog["root"] == f"{NATIVE_ROOT}/blog", f"php: {blog}")
        sc.check(
            docs["kind"] == "static" and docs["root"] == f"{NATIVE_ROOT}/docs", f"static: {docs}"
        )
        sc.check(all(by_site[n]["adoptable"] for n in NATIVE_SITES), "a site was not adoptable")

        for name in NATIVE_SITES:
            sc.run(
                f"noust app adopt-site {name} --yes",
                timeout=120,
                label=f"noust app adopt-site {name} --yes",
            )
        sc.check(
            native_state(sc, "everything the operator wrote, after adopting") == before,
            "adopting changed a file, or restarted the unit",
        )
        for name, expected in (
            ("native-legacy.test", "legacy v1"),
            ("native-blog.test", "blog php ok"),
            ("native-docs.test", "docs static ok"),
        ):
            wait_served(sc, name, expected)
        listing = sc.run("noust --json list", timeout=60, label="noust --json list").stdout
        apps = {row["domain"]: row for row in json.loads(listing.strip().splitlines()[-1])["items"]}
        sc.check(
            all(apps[name]["type"] == "adopted" for name in NATIVE_SITES),
            f"the applications are not listed as adopted: {sorted(apps)}",
        )

        pid_before = sc.run(
            "systemctl show native-legacy -p MainPID --value",
            timeout=15,
            label="the unit's process",
        ).stdout.strip()
        sc.run(
            "noust restart native-legacy.test",
            timeout=120,
            label="noust restart native-legacy.test",
        )
        pid_after = sc.run(
            "systemctl show native-legacy -p MainPID --value",
            timeout=15,
            label="the unit's process now",
        ).stdout.strip()
        sc.check(pid_before != pid_after, "noust restart did not restart the operator's unit")
        wait_served(sc, "native-legacy.test", "legacy v1")
        logs = sc.run(
            "noust logs native-legacy.test -n 20", timeout=60, label="noust logs native-legacy.test"
        )
        sc.check("legacy api listening on" in logs.stdout, f"the unit's journal: {logs.stdout!r}")
        status = sc.run(
            "noust status native-legacy.test", timeout=60, check=False, label="noust status"
        )
        sc.check(status.returncode == 0, f"noust status failed: {status.stdout}{status.stderr}")

        for command, words in (
            ("noust update native-legacy.test", "adopted site"),
            ("noust env set native-legacy.test A=1", "adopted site"),
            ("noust app hardening set native-legacy.test default", "your"),
            ("noust app tuning set native-legacy.test --cpu-weight 50", "your"),
        ):
            refused = sc.run(command, timeout=120, check=False, label=f"{command} (refused)")
            sc.check(refused.returncode != 0, f"{command} was not refused")
            sc.check(
                words in refused.stdout + refused.stderr,
                f"{command} did not say why: {refused.stdout}{refused.stderr}",
            )
        unit_text = sc.run(
            "cat /etc/systemd/system/native-legacy.service", timeout=15, label="the unit file"
        ).stdout
        sc.check("Generated by Noust" not in unit_text, "the operator's unit got Noust's marker")

        sc.run(
            "noust delete native-legacy.test --force",
            timeout=120,
            label="noust delete native-legacy.test",
        )
        sc.check(
            sc.run(
                "systemctl is-active native-legacy", timeout=15, label="is the unit still running?"
            ).stdout.strip()
            == "active",
            "deleting the application stopped the operator's unit",
        )
        wait_served(sc, "native-legacy.test", "legacy v1")
        sc.check(
            sc.run(
                f"test -f {NATIVE_ROOT}/legacy/server.js && test -f /etc/nginx/sites-available/native-legacy.test "
                "&& test -f /etc/systemd/system/native-legacy.service && echo kept",
                timeout=15,
                label="are the files still there?",
            ).stdout.strip()
            == "kept",
            "deleting the application removed something of the operator's",
        )
        gone = store_rows(
            sc,
            "SELECT * FROM apps WHERE domain = @domain",
            domain="native-legacy.test",
            label="the row",
        )
        sc.check(gone == [], f"the application is still registered: {gone}")
        for name in NATIVE_SITES[1:]:
            sc.run(f"noust delete {name} --force", timeout=120, label=f"noust delete {name}")
        wait_served(sc, "native-blog.test", "blog php ok")
        wait_served(sc, "native-docs.test", "docs static ok")
    finally:
        sc.run(
            "systemctl stop native-legacy; rm -f /etc/systemd/system/native-legacy.service "
            "/etc/nginx/sites-enabled/native-* /etc/nginx/sites-available/native-*; "
            f"rm -rf {NATIVE_ROOT}; systemctl daemon-reload; nginx -s reload; "
            "for name in native-legacy.test native-blog.test native-docs.test; do "
            "noust delete $name --force >/dev/null 2>&1; done; true",
            timeout=120,
            check=False,
            label="cleanup: the operator's setup",
        )


DISK_DOMAIN = "disk.test"
DISK_ROOT = "/var/www/apps/disk-test"


@scenario("app_disk_measured_and_held_to_a_limit")
def scenario_app_disk(sc: Scenario) -> None:
    """
    What an application takes of the disk is measured by place, and a limit holds new releases.

    A Node app on releases with two releases and 80 MB in shared/. The measurement is the real
    `du`, `findmnt` reports the container's real filesystem, and a limit under what it takes makes
    the next update refuse before building, with the figures, while going back to the earlier
    release works. Raising the limit lets the update through.
    """
    require_cli(sc, "app disk", what="the disk of an application")
    head = sc.run(
        f"git -C {RELEASE_REPO} rev-parse HEAD", timeout=15, label="(fixture repo) where it is"
    ).stdout.strip()
    sc.run(
        f"noust create -d {DISK_DOMAIN} -s {RELEASE_URL} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {DISK_DOMAIN} (releases)",
    )
    try:
        wait_served(sc, DISK_DOMAIN, "ok")
        commit_fixture(sc, "echo disk-2 > VERSION", "disk scenario: second release")
        sc.run(
            f"noust update {DISK_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {DISK_DOMAIN}",
        )
        sc.run(
            f"fallocate -l 80M {DISK_ROOT}/shared/blob.bin && chown www-data {DISK_ROOT}/shared/blob.bin",
            timeout=30,
            label="80 MB of uploads in shared/",
        )
        measured = json.loads(
            sc.run(
                f"noust --json app disk {DISK_DOMAIN} --measure",
                timeout=300,
                label=f"noust --json app disk {DISK_DOMAIN} --measure",
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        parts = {part["name"]: part for part in measured["usage"]["parts"]}
        sc.check({"current", "releases", "shared", "repo"} <= set(parts), f"parts: {sorted(parts)}")
        sc.check(parts["shared"]["bytes"] >= 80 * 1024 * 1024, f"shared/ is {parts['shared']}")
        sc.check(
            measured["usage"]["total_bytes"] == sum(part["bytes"] for part in parts.values()),
            "the total is not the sum of the places",
        )
        sc.check(measured["usage"]["errors"] == [], f"errors: {measured['usage']['errors']}")
        sc.check(
            measured["filesystem"]["fstype"]
            and measured["filesystem"]["project_quota"] in ("enabled", "available", "unsupported"),
            f"the filesystem was not read: {measured['filesystem']}",
        )

        sc.run(
            f"noust app disk {DISK_DOMAIN} --limit 64",
            timeout=60,
            label=f"noust app disk {DISK_DOMAIN} --limit 64",
        )
        commit_fixture(sc, "echo disk-3 > VERSION", "disk scenario: third release")
        before = active_release_of(sc, DISK_DOMAIN)
        refused = sc.run(
            f"noust update {DISK_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {DISK_DOMAIN} (over its limit)",
        )
        text = refused.stdout + refused.stderr
        sc.check(refused.returncode != 0, "the update was not held")
        sc.check(
            "over its disk limit" in text and "noust app disk" in text, f"the refusal: {text!r}"
        )
        sc.check(active_release_of(sc, DISK_DOMAIN) == before, "the held update moved current")

        sc.run(
            f"noust releases rollback {DISK_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust releases rollback {DISK_DOMAIN} (never held up by the limit)",
        )
        sc.check(active_release_of(sc, DISK_DOMAIN) != before, "going back did not go back")

        sc.run(
            f"noust app disk {DISK_DOMAIN} --limit 4096",
            timeout=60,
            label="noust app disk --limit 4096",
        )
        sc.run(
            f"noust update {DISK_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {DISK_DOMAIN} (under the limit)",
        )
        sc.run(
            f"noust app disk {DISK_DOMAIN} --no-limit",
            timeout=60,
            label="noust app disk --no-limit",
        )
    finally:
        sc.run(
            f"noust delete {DISK_DOMAIN} --force; true",
            timeout=120,
            check=False,
            label="cleanup: disk.test",
        )
        sc.run(
            f"cd {RELEASE_REPO} && git reset -q --hard {head}; true",
            timeout=30,
            check=False,
            label="(fixture repo) back to before this scenario",
        )


KERNEL_DOMAIN = "kq.test"
KERNEL_ROOT = "/var/www/apps/kq-test"
KERNEL_TREE = "/root/kq-tree"
#: Detach the loop devices this scenario made, by the images that back them and nothing else.
KERNEL_LET_GO = (
    "for image in /root/kq-xfs.img /root/kq-ext4.img; do "
    "for dev in $(losetup -j $image 2>/dev/null | cut -d: -f1); do losetup -d $dev; done; done"
)


def kernel_quota_disk(sc: Scenario) -> dict[str, Any]:
    """What `noust --json app disk` says about the kernel's quota on the scenario's application."""
    shown = json.loads(
        sc.run(
            f"noust --json app disk {KERNEL_DOMAIN}",
            timeout=120,
            label=f"noust --json app disk {KERNEL_DOMAIN}",
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    return dict(shown)


def kernel_quota_write(sc: Scenario, who: str, megabytes: int) -> tuple[int, str, int]:
    """
    Write a file of that many megabytes into the application's tree, as an account.

    Returns:
        The exit code of the write, what it said, and the size of the file left behind.
    """
    target = f"{KERNEL_ROOT}/shared/fill/blob"
    as_who = "" if who == "root" else f"setpriv --reuid={who} --regid={who} --clear-groups "
    written = sc.run(
        f"rm -f {target}; {as_who}dd if=/dev/zero of={target} bs=1M count={megabytes} conv=fsync",
        timeout=180,
        check=False,
        label=f"write {megabytes} MB into the tree as {who}",
    )
    size = int(
        sc.run(f"stat -c %s {target} 2>/dev/null || echo 0", timeout=15, label="size left")
        .stdout.strip()
        .splitlines()[-1]
    )
    return written.returncode, written.stdout + written.stderr, size


def kernel_quota_move(sc: Scenario, fstype: str) -> None:
    """
    Put the application's tree on a fresh loop filesystem mounted with project quotas.

    The loop filesystem is mounted over the apps directory, which is where Noust looks for an
    application's tree, so the quota is set by the same code path a real XFS volume would take.
    Nothing here is Noust's doing: mounting and tuning a filesystem is the operator's.
    """
    image = f"/root/kq-{fstype}.img"
    sc.run(f"noust stop {KERNEL_DOMAIN}", timeout=120, label="stop the app while its tree moves")
    sc.run(
        f"rm -rf {KERNEL_TREE} && cp -a {KERNEL_ROOT} {KERNEL_TREE}",
        timeout=120,
        label="keep the tree aside",
    )
    sc.run(
        f"if mountpoint -q /var/www/apps; then umount /var/www/apps; fi; {KERNEL_LET_GO}; true",
        timeout=60,
        label="let go of the filesystem before",
    )
    if fstype == "xfs":
        prepare = f"mkfs.xfs -q -f {image}"
    else:
        prepare = f"mkfs.ext4 -q -F {image} && tune2fs -O project -Q prjquota {image}"
    sc.run(
        f"rm -f {image}; truncate -s 400M {image} && {prepare} && "
        f"mount -o prjquota $(losetup -f --show {image}) /var/www/apps",
        timeout=120,
        label=f"{fstype} on a loop device, mounted over /var/www/apps with prjquota",
    )
    sc.run(
        f"cp -a {KERNEL_TREE} {KERNEL_ROOT} && noust start {KERNEL_DOMAIN}",
        timeout=120,
        label="the tree back, on the new filesystem",
    )


@scenario("kernel_project_quota_stops_the_writes")
def scenario_kernel_quota(sc: Scenario) -> None:
    """
    An application's limit given to the kernel stops the writes themselves, on XFS and on ext4.

    A Node app on releases, deployed on the container's filesystem, where `--kernel` is refused
    with the reason. Its tree then moves onto an XFS and onto an ext4 loop filesystem mounted
    with prjquota (the operator's part), and on each: `--kernel` sets the project and the limit,
    the kernel reports it, the app's own account is refused past the limit with the kernel's
    refusal, a changed limit follows into the kernel, `--no-kernel` lets it write
    again, and deleting the app takes the quota off. Noust never wrote /etc/projects.
    """
    require_cli(sc, "app disk", option="--kernel", what="the kernel quota of an application")
    sc.run(
        "command -v mkfs.xfs >/dev/null && command -v xfs_quota >/dev/null && "
        "command -v setquota >/dev/null && command -v repquota >/dev/null || "
        "(apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
        "xfsprogs quota >/dev/null)",
        timeout=300,
        check=False,
        label="the filesystem and quota tools (the operator's)",
    )
    tools = sc.run(
        "command -v mkfs.xfs && command -v xfs_quota && command -v setquota && "
        "command -v repquota && command -v tune2fs && losetup -f",
        timeout=30,
        check=False,
        label="the tools and a loop device are there",
    )
    if tools.returncode != 0:
        raise ScenarioSkipped("no xfsprogs, quota tools or loop device in the harness container")
    head = sc.run(
        f"git -C {RELEASE_REPO} rev-parse HEAD", timeout=15, label="(fixture repo) where it is"
    ).stdout.strip()
    sc.run(
        f"noust create -d {KERNEL_DOMAIN} -s {RELEASE_URL} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {KERNEL_DOMAIN} (releases)",
    )
    try:
        wait_served(sc, KERNEL_DOMAIN, "ok")
        sc.run(
            f"noust app disk {KERNEL_DOMAIN} --limit 128",
            timeout=60,
            label=f"noust app disk {KERNEL_DOMAIN} --limit 128",
        )

        refused = sc.run(
            f"noust app disk {KERNEL_DOMAIN} --kernel",
            timeout=120,
            check=False,
            label="noust app disk --kernel (on a filesystem without project quotas)",
        )
        said = refused.stdout + refused.stderr
        sc.check(refused.returncode != 0, "a kernel quota was accepted on the container's overlay")
        sc.check("not mounted with project quotas" in said, f"the refusal: {said!r}")
        sc.check(not kernel_quota_disk(sc)["kernel"]["applied"], "the refused quota was recorded")

        for fstype in ("xfs", "ext4"):
            kernel_quota_move(sc, fstype)
            sc.run(
                f"mkdir -p {KERNEL_ROOT}/shared/fill && chown www-data:www-data {KERNEL_ROOT}/shared/fill",
                timeout=30,
                label="a place the app can write",
            )
            sc.run(
                f"noust app disk {KERNEL_DOMAIN} --limit 128",
                timeout=60,
                label=f"noust app disk {KERNEL_DOMAIN} --limit 128 ({fstype})",
            )
            sc.run(
                f"noust app disk {KERNEL_DOMAIN} --kernel",
                timeout=300,
                label=f"noust app disk {KERNEL_DOMAIN} --kernel ({fstype})",
            )
            kernel = kernel_quota_disk(sc)["kernel"]
            sc.check(kernel["applied"] is True, f"{fstype}: not applied: {kernel}")
            sc.check(kernel["hard_mb"] == 128, f"{fstype}: the kernel reports {kernel}")
            sc.check(kernel["mount"] == "/var/www/apps", f"{fstype}: mount {kernel}")
            sc.check(
                kernel["project"] is not None and kernel["project"] >= 3_000_000_000,
                f"{fstype}: project {kernel}",
            )
            sc.run(
                "test ! -e /etc/projects && test ! -e /etc/projid",
                timeout=15,
                label="Noust wrote neither /etc/projects nor /etc/projid",
            )
            if fstype == "xfs":
                report = sc.run(
                    "xfs_quota -x -c 'report -p -N' /var/www/apps",
                    timeout=30,
                    label="what the kernel holds for the project",
                ).stdout
                sc.check(
                    f"#{kernel['project']}" in report and "131072" in report,
                    f"xfs_quota report: {report!r}",
                )
            else:
                report = sc.run(
                    "repquota -P -n -O csv /var/www/apps",
                    timeout=30,
                    label="what the kernel holds for the project",
                ).stdout
                sc.check(
                    f"#{kernel['project']}" in report and "131072" in report,
                    f"repquota: {report!r}",
                )

            code, said, size = kernel_quota_write(sc, "www-data", 300)
            sc.check(code != 0, f"{fstype}: the app wrote 300 MB past a 128 MB limit")
            # XFS answers a project over its limit with ENOSPC ("No space left on device"); ext4
            # with EDQUOT ("Disk quota exceeded"). Either is the kernel refusing the write.
            refused_words = ("quota exceeded", "no space left")
            sc.check(
                any(words in said.lower() for words in refused_words),
                f"{fstype}: the write said {said!r}",
            )
            sc.check(16 * 1024 * 1024 <= size <= 128 * 1024 * 1024, f"{fstype}: left {size} bytes")
            if fstype == "xfs":
                code, said, size = kernel_quota_write(sc, "root", 300)
                sc.check(
                    code != 0 and size <= 128 * 1024 * 1024,
                    f"{fstype}: root wrote {size} bytes past the limit: {said!r}",
                )

            sc.run(
                f"noust app disk {KERNEL_DOMAIN} --limit 96",
                timeout=120,
                label="noust app disk --limit 96 (follows into the kernel)",
            )
            kernel = kernel_quota_disk(sc)["kernel"]
            sc.check(kernel["hard_mb"] == 96, f"{fstype}: the kernel still holds {kernel}")
            code, said, size = kernel_quota_write(sc, "www-data", 300)
            sc.check(
                code != 0 and size <= 96 * 1024 * 1024,
                f"{fstype}: wrote {size} bytes past 96 MB: {said!r}",
            )

            sc.run(
                f"noust app disk {KERNEL_DOMAIN} --no-kernel",
                timeout=300,
                label=f"noust app disk {KERNEL_DOMAIN} --no-kernel ({fstype})",
            )
            sc.check(not kernel_quota_disk(sc)["kernel"]["applied"], f"{fstype}: still applied")
            code, said, size = kernel_quota_write(sc, "www-data", 160)
            sc.check(
                code == 0 and size == 160 * 1024 * 1024,
                f"{fstype}: could not write once taken off: {said!r}",
            )
            sc.run(f"rm -f {KERNEL_ROOT}/shared/fill/blob", timeout=15, label="make room again")

        sc.run(
            f"noust app disk {KERNEL_DOMAIN} --limit 128 && noust app disk {KERNEL_DOMAIN} --kernel",
            timeout=300,
            label="the quota again, to see deleting the app take it off",
        )
        sc.run(
            f"noust delete {KERNEL_DOMAIN} --force",
            timeout=120,
            label=f"noust delete {KERNEL_DOMAIN}",
        )
        left = sc.run(
            "repquota -P -n -O csv /var/www/apps | grep -c '^#3' || true",
            timeout=30,
            label="projects with a limit left on the filesystem",
        ).stdout.strip()
        limits = sc.run(
            "repquota -P -n -O csv /var/www/apps | awk -F, '$1 ~ /^#3/ && $6 != 0 {print}'",
            timeout=30,
            label="a limit still set",
        ).stdout.strip()
        sc.check(limits == "", f"the deleted app left a limit behind (projects {left}): {limits}")
    finally:
        sc.run(
            f"noust delete {KERNEL_DOMAIN} --force >/dev/null 2>&1; "
            "if mountpoint -q /var/www/apps; then umount /var/www/apps; fi; "
            f"{KERNEL_LET_GO}; "
            f"rm -f /root/kq-xfs.img /root/kq-ext4.img; rm -rf {KERNEL_TREE} {KERNEL_ROOT}; true",
            timeout=180,
            check=False,
            label="cleanup: the loop filesystems and the application",
        )
        sc.run(
            f"cd {RELEASE_REPO} && git reset -q --hard {head}; true",
            timeout=30,
            check=False,
            label="(fixture repo) back to before this scenario",
        )


#: Scenarios that run only when a --scenario pattern holds their key, never in the default
#: suite nor matched by a broader pattern such as "compose": they are slow and need something
#: outside this repository (Proggest's checkout, the network that has Railpack's images).
OPTIONAL_SCENARIOS = {
    "proggest_compose_stack": "proggest",
    # Pulls Railpack's BuildKit frontend and its base images: minutes and the network.
    "container_app_built_by_railpack": "railpack",
}


def selected_scenarios(names: list[str] | None) -> list[tuple[str, ScenarioFn]]:
    def wanted(name: str) -> bool:
        key = OPTIONAL_SCENARIOS.get(name)
        return key is None or any(key in pattern for pattern in names or [])

    if not names:
        return [(name, fn) for name, fn in SCENARIOS if wanted(name)]
    selected = [
        (name, fn)
        for name, fn in SCENARIOS
        if any(pattern in name for pattern in names) and wanted(name)
    ]
    if not selected:
        raise HarnessError(f"no scenario matches {names!r}; available: {[n for n, _ in SCENARIOS]}")
    return selected


def run_install_matrix(images: list[str] | None, platform: str | None) -> int:
    """Run the installer against the platform matrix inside the harness container.

    The matrix starts one container per distribution and pulls their images. Here the Docker
    that does so is the harness container's own, so those images and containers live in its data
    volume and go with it, and the machine's Docker (which other people's containers share) gets
    one labelled container and nothing else.

    Args:
        images: Only these images; the whole matrix when None.
        platform: One platform for every case instead of each case's own.

    Returns:
        The matrix's exit status.
    """
    name = random_container_name()
    try:
        start_container(name)
        wait_for_systemd(name)
        if not prepare_inner_docker(name):
            raise HarnessError("the Docker inside the harness container does not answer")
        docker_exec(name, "mkdir -p /root/matrix/tests/integration /root/matrix/packaging")
        sh(
            [
                "docker",
                "cp",
                str(INTEGRATION_DIR / "install_matrix.py"),
                f"{name}:/root/matrix/tests/integration/install_matrix.py",
            ],
            timeout=60,
        )
        sh(
            [
                "docker",
                "cp",
                str(REPO_ROOT / "packaging" / "install.sh"),
                f"{name}:/root/matrix/packaging/install.sh",
            ],
            timeout=60,
        )
        argv = [
            "docker",
            "exec",
            name,
            "python3",
            "-u",
            "/root/matrix/tests/integration/install_matrix.py",
        ]
        for image in images or []:
            argv += ["--image", image]
        if platform:
            argv += ["--platform", platform]
        return subprocess.run(argv, check=False).returncode
    finally:
        print(f"\n[teardown] removing container {name}")
        remove_container(name)


def reuse_container(name: str, wheel: Path) -> None:
    """Make a container a previous ``--keep`` run left ready for this tree's scenarios.

    The wheel goes over the installed one and the fixtures over the container's own (the
    repositories the scenarios made under /root/fixtures stay); the inner Docker is cleared as at
    the start of a run.

    Args:
        name: The container, one this harness named.
        wheel: This tree's wheel, on the host.

    Raises:
        HarnessError: The name is not one of the harness's, or the container does not run.
    """
    if not name.startswith(f"{RESOURCE_PREFIX}-"):
        raise HarnessError(
            f"--reuse takes a container this harness named ({RESOURCE_PREFIX}-...), not {name!r}"
        )
    state = sh(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    if state.stdout.strip() != "true":
        raise HarnessError(f"{name} is not a running container")
    print(f"[setup] reusing container {name}")
    prepare_inner_docker(name)
    sh(["docker", "cp", str(wheel), f"{name}:/tmp/{wheel.name}"], timeout=60)
    docker_exec(
        name,
        f"/opt/noust/bin/pip install --quiet '/tmp/{wheel.name}[all]' && "
        f"/opt/noust/bin/pip install --quiet --force-reinstall --no-deps '/tmp/{wheel.name}'",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    sh(["docker", "cp", f"{FIXTURES_DIR}/.", f"{name}:/root/fixtures/"], timeout=60)
    docker_exec(name, "chown -R root:root /root/fixtures", timeout=30)
    print(f"[setup] {docker_exec(name, 'noust --version', timeout=30).stdout.strip()} in {name}")


def main() -> int:
    global IGNORE_REQUIREMENTS
    args = parse_args()
    if args.install_matrix:
        # No wheel: the matrix installs the published package.
        return run_install_matrix(args.image, args.platform)
    IGNORE_REQUIREMENTS = args.ignore_requirements
    started_at = time.monotonic()

    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)

    if args.wheel is not None:
        wheel = args.wheel.resolve()
        if not wheel.is_file():
            raise HarnessError(f"--wheel {args.wheel} is not a file")
        print(f"[setup] using the given wheel {wheel.name}")
    elif args.skip_wheel_build:
        wheels = sorted((workdir / "dist").glob("noust-*.whl"))
        if not wheels:
            raise HarnessError("--skip-wheel-build given but no noust-*.whl wheel exists yet")
        wheel = wheels[-1]
        print(f"[setup] reusing existing wheel {wheel.name}")
    else:
        wheel = build_wheel(workdir)

    if not args.skip_image_build:
        build_image()
    else:
        print(f"[setup] reusing existing image {IMAGE_TAG}")

    if args.upgrade_from_30:
        variants = {"installed": [True], "uninstalled": [False], "both": [True, False]}
        return run_upgrade_30_mode(wheel, variants=variants[args.monitor_variant], keep=args.keep)

    if args.upgrade_from_33:
        import scenarios_upgrade

        return scenarios_upgrade.run_upgrade_33_mode(wheel, keep=args.keep)

    if args.downgrade_to_33:
        import scenarios_upgrade

        return scenarios_upgrade.run_downgrade_mode(wheel, keep=args.keep)

    if args.mixed_fleet:
        import scenarios_fleet_mixed

        return scenarios_fleet_mixed.run_mixed_fleet_mode(wheel, keep=args.keep)

    name = args.reuse or random_container_name()
    failures = 0
    attempted = 0
    skipped: list[tuple[str, str]] = []
    setup_error: str | None = None

    try:
        try:
            if args.reuse:
                reuse_container(name, wheel)
            else:
                start_container(name)
                wait_for_systemd(name)
                prepare_inner_docker(name)
                install_noust(name, wheel)
                install_fixtures(name)
            to_run = selected_scenarios(args.scenario)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            print(f"\n[scenarios] running {len(to_run)} scenario(s): {[n for n, _ in to_run]}")

            for sc_name, fn in to_run:
                print(f"\n=== {sc_name} ===")
                attempted += 1
                sc = Scenario(container=name)
                try:
                    fn(sc)
                except ScenarioSkipped as exc:
                    skipped.append((sc_name, str(exc)))
                    print(f"SKIP: {sc_name}: {exc}")
                except AssertionError as exc:
                    failures += 1
                    print(f"FAIL: {sc_name}: {exc}")
                except HarnessError as exc:
                    failures += 1
                    print(f"FAIL: {sc_name} (command error): {exc}")
                except Exception:
                    # The harness's error boundary: one scenario's own bug (a missing key, a bad
                    # parse) is a FAIL with its traceback, and the scenarios after it still run.
                    failures += 1
                    print(f"FAIL: {sc_name} (the scenario itself raised)")
                    traceback.print_exc(file=sys.stdout)
                else:
                    print(f"PASS: {sc_name}")
                finally:
                    if sc.evidence:
                        print("--- evidence ---")
                        print("\n\n".join(sc.evidence))
    finally:
        if args.keep or args.reuse:
            print(
                f"\n[teardown] leaving container {name} running ({'--reuse' if args.reuse else '--keep'})"
            )
        else:
            print(f"\n[teardown] removing container {name}")
            remove_container(name)

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        print(f"\n[summary] 0/0 scenario(s) run, setup failed, {elapsed:.1f}s total")
        return 1

    # Said again at the end, with the reasons: a SKIP in the middle of a long run is easy to
    # miss, and a scenario that never ran is exactly what a green summary must not hide.
    for sc_name, reason in skipped:
        print(f"[skipped] {sc_name}: {reason}")
    print(
        f"\n[summary] {attempted} scenario(s) run, {failures} failure(s), {len(skipped)} skipped, "
        f"{elapsed:.1f}s total"
    )
    return 1 if failures else 0


def load_extension_scenarios() -> None:
    """Import the modules that add scenarios to :data:`SCENARIOS`.

    ``run.py`` is the ``__main__`` module when it is run, and an extension module does ``import
    run`` to reach the helpers and the ``scenario`` decorator: without this alias that import would
    load a second copy of this file, and the scenarios would register in the copy.
    """
    sys.modules.setdefault("run", sys.modules[__name__])
    for module in EXTENSION_MODULES:
        importlib.import_module(module)


#: Modules beside this one whose scenarios join the suite (see :func:`load_extension_scenarios`).
EXTENSION_MODULES = ("scenarios_34", "scenarios_w6a", "scenarios_real")

load_extension_scenarios()


if __name__ == "__main__":
    sys.exit(main())
